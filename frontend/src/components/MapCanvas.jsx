// Map drawn as an SVG from latitude and longitude, used by F9 Map View and F10 Tracking.
// No tile server and no extra dependency, so it also works on an EC2 box without outbound
// internet. Positions are projected flat, which is accurate enough over a city.
//
// Usage:
//   <MapCanvas markers={cars} selectedId={id} onSelect={setId} />            // F9
//   <MapCanvas route={points} pins={alerts} cursor={point} />                // F10
//
// markers: [{ id, lat, lon, status, label }]   status -> colour (DT-MV-1)
// pins:    [{ id, lat, lon, label, onClick }]  numbered markers along a route
// cursor:  { lat, lon }                        current position during playback

const WIDTH = 1000;
const HEIGHT = 560;
const PAD = 60;
const MIN_SPAN = 0.004;          // about 400 m, so a single point is not zoomed to infinity

export const STATUS_COLOURS = {
  online: 'var(--teal)',
  alert: 'var(--warning)',
  urgent: 'var(--danger)',
  offline: 'var(--text-3)',
};

function projector(points) {
  const lats = points.map((p) => p.lat);
  const lons = points.map((p) => p.lon);
  const spanLat = Math.max(Math.max(...lats) - Math.min(...lats), MIN_SPAN);
  const spanLon = Math.max(Math.max(...lons) - Math.min(...lons), MIN_SPAN);
  const left = (Math.min(...lons) + Math.max(...lons)) / 2 - spanLon / 2;
  const bottom = (Math.min(...lats) + Math.max(...lats)) / 2 - spanLat / 2;
  return {
    x: (lon) => PAD + ((lon - left) / spanLon) * (WIDTH - 2 * PAD),
    y: (lat) => HEIGHT - PAD - ((lat - bottom) / spanLat) * (HEIGHT - 2 * PAD),
  };
}

const hasPosition = (p) => p && p.lat !== null && p.lat !== undefined && p.lon !== null && p.lon !== undefined;

export default function MapCanvas({ markers = [], route = [], pins = [], cursor = null,
                                    selectedId = null, onSelect, height = 380, empty = 'No positions to show' }) {
  const placed = markers.filter(hasPosition);
  const line = route.filter(hasPosition);
  const pinned = pins.filter(hasPosition);
  const all = [...placed, ...line, ...pinned, ...(hasPosition(cursor) ? [cursor] : [])];
  if (all.length === 0) return <div className="map-empty" style={{ height }}>{empty}</div>;

  const { x, y } = projector(all);
  const grid = [1, 2, 3, 4].map((i) => (i * WIDTH) / 5);
  const rows = [1, 2, 3].map((i) => (i * HEIGHT) / 4);

  return (
    <svg className="map-canvas" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} style={{ height }}
         role="img" aria-label="Map of car positions">
      <rect x="0" y="0" width={WIDTH} height={HEIGHT} className="map-bg" />
      {grid.map((gx) => <line key={`v${gx}`} x1={gx} y1="0" x2={gx} y2={HEIGHT} className="map-grid" />)}
      {rows.map((gy) => <line key={`h${gy}`} x1="0" y1={gy} x2={WIDTH} y2={gy} className="map-grid" />)}

      {line.length > 1 && (
        <polyline className="map-route" points={line.map((p) => `${x(p.lon)},${y(p.lat)}`).join(' ')} />
      )}

      {line.length > 0 && (
        <g>
          <circle cx={x(line[0].lon)} cy={y(line[0].lat)} r="11" className="map-start" />
          <text x={x(line[0].lon)} y={y(line[0].lat) + 5} className="map-pin-text">S</text>
        </g>
      )}

      {pinned.map((p, i) => (
        <g key={p.id ?? i} className="map-pin" onClick={p.onClick}
           style={p.onClick ? { cursor: 'pointer' } : undefined}>
          <circle cx={x(p.lon)} cy={y(p.lat)} r="14" />
          <text x={x(p.lon)} y={y(p.lat) + 5} className="map-pin-text">{p.label ?? i + 1}</text>
          <title>{p.title ?? ''}</title>
        </g>
      ))}

      {hasPosition(cursor) && (
        <circle cx={x(cursor.lon)} cy={y(cursor.lat)} r="12" className="map-cursor" />
      )}

      {placed.map((m) => {
        const selected = m.id === selectedId;
        return (
          <g key={m.id} onClick={onSelect ? () => onSelect(m.id) : undefined}
             style={onSelect ? { cursor: 'pointer' } : undefined}>
            {m.status === 'urgent' && (
              <circle cx={x(m.lon)} cy={y(m.lat)} r="26" className="map-pulse"
                      fill={STATUS_COLOURS.urgent} />
            )}
            <circle cx={x(m.lon)} cy={y(m.lat)} r={selected ? 17 : 13}
                    fill={STATUS_COLOURS[m.status] ?? STATUS_COLOURS.offline}
                    className={selected ? 'map-marker map-marker-selected' : 'map-marker'} />
            <text x={x(m.lon)} y={y(m.lat) - 24} className="map-marker-label">{m.label}</text>
            <title>{`${m.label} (${m.status})`}</title>
          </g>
        );
      })}
    </svg>
  );
}
