// F9 Map View: one marker per car, coloured by DT-MV-1, refreshed every 5 seconds.
// F2 and F7 link here with ?car_id=, which selects that car on arrival.
import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { getFleetMap } from '../../api/endpoints.js';
import { useAsync } from '../../utils/useAsync.js';
import { usePolling } from '../../utils/usePolling.js';
import { formatDateTime, humanize } from '../../utils/format.js';
import { useRole } from '../../config/RoleContext.jsx';
import MapCanvas, { STATUS_COLOURS } from '../../components/MapCanvas.jsx';
import StatusBadge from '../../components/StatusBadge.jsx';
import { SOUND_TYPES } from '../../components/SoundType.jsx';

const REFRESH_MS = 5000;
const STATUSES = ['online', 'alert', 'urgent', 'offline'];
const LEGEND = {
  online: 'online, nothing open',
  alert: 'open alert, low or medium',
  urgent: 'open alert, high or critical',
  offline: 'no position in the last 90 s',
};

export default function MapViewPage() {
  const navigate = useNavigate();
  const { role } = useRole();
  const [params] = useSearchParams();
  const [status, setStatus] = useState('');
  const [soundType, setSoundType] = useState('');
  const [auto, setAuto] = useState(true);
  const [selected, setSelected] = useState(() => {
    const fromUrl = Number(params.get('car_id'));
    return Number.isFinite(fromUrl) && fromUrl > 0 ? fromUrl : null;
  });

  const { data, loading, error, reload } = useAsync(
    () => getFleetMap({ status, sound_type: soundType }), [status, soundType]);
  usePolling(reload, REFRESH_MS, auto);

  const cars = data?.cars ?? [];
  useEffect(() => {
    if (cars.length > 0 && !cars.some((c) => c.car_id === selected)) setSelected(cars[0].car_id);
  }, [cars, selected]);

  const car = cars.find((c) => c.car_id === selected) ?? null;
  const markers = cars.map((c) => ({ id: c.car_id, lat: c.lat, lon: c.lon,
                                     status: c.status, label: `C-${c.car_id}` }));

  return (
    <div className="page">
      <div className="page-head">
        <h1>Map View</h1>
        <span className="frame-id">F9</span>
      </div>

      <div className="filter-bar">
        <label className="field">
          <span>Status</span>
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Any</option>
            {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        <label className="field">
          <span>Alert type</span>
          <select value={soundType} onChange={(e) => setSoundType(e.target.value)}>
            <option value="">Any</option>
            {SOUND_TYPES.map((s) => <option key={s} value={s}>{humanize(s)}</option>)}
          </select>
        </label>
        <label className="check">
          <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
          Auto-refresh 5 s
        </label>
        <div className="filter-actions">
          <button type="button" className="btn" onClick={reload}>Refresh</button>
        </div>
      </div>

      {error && <p className="state-error">Could not load the map: {error.message}</p>}

      <div className="map-layout">
        <section className="panel map-panel">
          <div className="map-legend">
            {STATUSES.map((s) => (
              <span key={s} title={LEGEND[s]}>
                <i style={{ background: STATUS_COLOURS[s] }} /> {s}
              </span>
            ))}
          </div>
          <MapCanvas markers={markers} selectedId={selected} onSelect={setSelected} height={420}
                     empty={loading ? 'Loading positions...' : 'No cars match these filters'} />
        </section>

        <div className="stack">
          <section className="panel">
            <div className="section-head">
              <h2>Cars ({cars.length})</h2>
            </div>
            <ul className="car-list">
              {cars.map((c) => (
                <li key={c.car_id}>
                  <button type="button"
                          className={c.car_id === selected ? 'car-row car-row-active' : 'car-row'}
                          onClick={() => setSelected(c.car_id)}>
                    <i style={{ background: STATUS_COLOURS[c.status] }} />
                    <span className="grow">{c.name}</span>
                    <span className="muted small">{c.status}</span>
                  </button>
                </li>
              ))}
              {cars.length === 0 && !loading && <li className="muted small">No cars match these filters.</li>}
            </ul>
          </section>

          {car && (
            <section className="panel">
              <div className="section-head">
                <h2>Selected: {car.name}</h2>
              </div>
              <dl className="kv">
                <div className="kv-row"><dt>Status</dt><dd><StatusBadge value={car.status} /></dd></div>
                <div className="kv-row">
                  <dt>Speed</dt>
                  <dd>{car.speed_kmh != null ? `${car.speed_kmh} km/h` : '-'}
                    {car.heading != null && <span className="muted small"> · heading {car.heading}</span>}</dd>
                </div>
                <div className="kv-row"><dt>Position at</dt><dd>{formatDateTime(car.position_at)}</dd></div>
                <div className="kv-row">
                  <dt>Open alerts</dt>
                  <dd className="cell-inline">
                    {car.open_alerts}
                    {car.top_severity && <StatusBadge value={car.top_severity} />}
                  </dd>
                </div>
              </dl>
              <div className="action-row">
                <button className="btn btn-primary" onClick={() => navigate(`/tracking?car_id=${car.car_id}`)}>
                  Track car
                </button>
                <button className="btn" onClick={() => navigate(`/alerts?car_id=${car.car_id}`)}>Alerts</button>
                {role === 'cloud_staff' && (
                  <button className="btn" onClick={() => navigate('/simulator')}>Open simulator</button>
                )}
              </div>
            </section>
          )}
        </div>
      </div>

      <p className="muted small">
        Marker colour follows DT-MV-1: teal is online with nothing open, gold has an open low or
        medium alert, coral has a high or critical one, and grey has not reported a position in
        the last 90 seconds.
      </p>
    </div>
  );
}
