// Street map (Leaflet + OpenStreetMap tiles), used by F9 Map View and F10 Tracking.
// The viewer's browser loads the tiles, so it needs internet; the server does not.
//
// Usage:
//   <MapCanvas markers={cars} selectedId={id} onSelect={setId} />            // F9
//   <MapCanvas route={points} pins={alerts} cursor={point} />                // F10
//
// markers: [{ id, lat, lon, status, label }]   status -> colour (DT-MV-1)
// route:   [{ lat, lon }]                      trail, with a start marker
// pins:    [{ id, lat, lon, label, title, onClick }]  numbered markers along a route
// cursor:  { lat, lon }                        current position during playback
//
// The view fits everything when the set of cars or the route changes, but not on every poll,
// so a refresh never undoes the user's zoom or pan.
import { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

const TILES = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
const MAX_ZOOM = 19;
const MAX_FIT_ZOOM = 16;         // so a single point is not zoomed all the way in
const FIT = { padding: [50, 50], maxZoom: MAX_FIT_ZOOM, animate: false };

export const STATUS_COLOURS = {
  online: 'var(--teal)',
  alert: 'var(--warning)',
  urgent: 'var(--danger)',
  offline: 'var(--text-3)',
};

const hasPosition = (p) => p && p.lat !== null && p.lat !== undefined && p.lon !== null && p.lon !== undefined;
const latLng = (p) => [p.lat, p.lon];

function part(className, text, background) {
  const el = document.createElement('span');
  el.className = className;
  if (text !== undefined) el.textContent = text;
  if (background) el.style.background = background;
  return el;
}

// A zero-sized icon whose children are centred on the position by CSS.
function iconOf(...children) {
  const html = document.createElement('div');
  children.forEach((c) => html.appendChild(c));
  return L.divIcon({ className: 'map-icon', html, iconSize: [0, 0] });
}

function carIcon({ status, label }, selected) {
  const dot = part(selected ? 'map-car map-car-selected' : 'map-car', undefined,
                   STATUS_COLOURS[status] ?? STATUS_COLOURS.offline);
  const name = part('map-car-label', label);
  return status === 'urgent'
    ? iconOf(part('map-pulse', undefined, STATUS_COLOURS.urgent), dot, name)
    : iconOf(dot, name);
}

function LeafletMap({ markers, route, pins, cursor, selectedId, onSelect, height }) {
  const element = useRef(null);
  const map = useRef(null);
  const layers = useRef(null);
  const previousSelected = useRef(undefined);
  const latest = useRef({});
  latest.current = { onSelect, pins };

  const first = route[0];
  const last = route[route.length - 1];
  const routeKey = first ? `${route.length}:${first.lat},${first.lon}:${last.lat},${last.lon}` : '';
  const pinsKey = pins.map((p) => `${p.id},${p.lat},${p.lon},${p.label}`).join(';');
  const carsKey = markers.map((m) => `${m.id},${m.lat},${m.lon},${m.status},${m.label}`).join(';');
  const fitKey = [markers.map((m) => m.id).join(','), routeKey, pinsKey].join('|');

  // Create the map once. Leaflet needs a view before anything can be added to it.
  useEffect(() => {
    const start = [...markers, ...route, ...pins][0];
    const instance = L.map(element.current).setView(latLng(start), MAX_FIT_ZOOM);
    L.tileLayer(TILES, { maxZoom: MAX_ZOOM, attribution: ATTRIBUTION }).addTo(instance);
    layers.current = { route: L.layerGroup().addTo(instance), pins: L.layerGroup().addTo(instance),
                       cars: new Map(), cursor: null };
    map.current = instance;

    // The page layout can change the panel size without a window resize.
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => instance.invalidateSize());
    observer?.observe(element.current);
    return () => {
      observer?.disconnect();
      instance.remove();
      map.current = null;
      layers.current = null;
      previousSelected.current = undefined;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const bounds = L.latLngBounds([...markers, ...route, ...pins].map(latLng));
    map.current.fitBounds(bounds, FIT);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitKey]);

  useEffect(() => {
    const { route: group } = layers.current;
    group.clearLayers();
    if (route.length === 0) return;
    if (route.length > 1) L.polyline(route.map(latLng), { className: 'map-route', interactive: false }).addTo(group);
    L.marker(latLng(route[0]), { icon: iconOf(part('map-start', 'S')), interactive: false }).addTo(group);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeKey]);

  useEffect(() => {
    const { pins: group } = layers.current;
    group.clearLayers();
    pins.forEach((p, i) => {
      const marker = L.marker(latLng(p), { icon: iconOf(part('map-pin', p.label ?? i + 1)), title: p.title ?? '' });
      marker.on('click', () => latest.current.pins.find((x) => x.id === p.id)?.onClick?.());
      marker.addTo(group);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pinsKey]);

  // Update cars in place, so a poll does not rebuild a marker someone is hovering or clicking.
  useEffect(() => {
    const { cars } = layers.current;
    const wanted = new Set(markers.map((m) => m.id));
    cars.forEach((entry, id) => {
      if (!wanted.has(id)) { entry.marker.remove(); cars.delete(id); }
    });
    markers.forEach((m) => {
      const selected = m.id === selectedId;
      const iconKey = `${m.status}:${m.label}:${selected}`;
      let entry = cars.get(m.id);
      if (!entry) {
        const marker = L.marker(latLng(m), { icon: carIcon(m, selected), title: `${m.label} (${m.status})` });
        marker.on('click', () => latest.current.onSelect?.(m.id));
        marker.addTo(map.current);
        entry = { marker, iconKey };
        cars.set(m.id, entry);
      } else {
        entry.marker.setLatLng(latLng(m));
        if (entry.iconKey !== iconKey) {
          entry.marker.setIcon(carIcon(m, selected));
          entry.marker.getElement()?.setAttribute('title', `${m.label} (${m.status})`);
          entry.iconKey = iconKey;
        }
      }
      entry.marker.setZIndexOffset(selected ? 500 : 0);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [carsKey, selectedId]);

  // Centre on a car the user picked or arrived with (?car_id=), but leave the fitted view
  // alone when the page selects the first car by itself.
  useEffect(() => {
    const previous = previousSelected.current;
    previousSelected.current = selectedId;
    if (selectedId === null || selectedId === undefined || previous === null) return;
    const car = markers.find((m) => m.id === selectedId);
    if (car) map.current.panTo(latLng(car));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  useEffect(() => {
    const group = layers.current;
    if (!cursor) {
      group.cursor?.remove();
      group.cursor = null;
    } else if (group.cursor) {
      group.cursor.setLatLng(latLng(cursor));
    } else {
      group.cursor = L.marker(latLng(cursor), { icon: iconOf(part('map-cursor')), interactive: false,
                                                zIndexOffset: 1000 }).addTo(map.current);
    }
  }, [cursor?.lat, cursor?.lon]);

  return <div ref={element} className="map-canvas" style={{ height }} role="region" aria-label="Map of car positions" />;
}

export default function MapCanvas({ markers = [], route = [], pins = [], cursor = null,
                                    selectedId = null, onSelect, height = 380, empty = 'No positions to show' }) {
  const placed = markers.filter(hasPosition);
  const line = route.filter(hasPosition);
  const pinned = pins.filter(hasPosition);
  if (placed.length + line.length + pinned.length === 0) {
    return <div className="map-empty" style={{ height }}>{empty}</div>;
  }
  return <LeafletMap markers={placed} route={line} pins={pinned} cursor={hasPosition(cursor) ? cursor : null}
                     selectedId={selectedId} onSelect={onSelect} height={height} />;
}
