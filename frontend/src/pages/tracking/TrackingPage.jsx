// F10 Tracking: replay where one car drove in a time range, with its alerts on the route.
// The range is capped at 24 hours; the backend answers 422 above that.
import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { getCarTrack, getFleetMap } from '../../api/endpoints.js';
import { useAsync } from '../../utils/useAsync.js';
import { formatDateTime } from '../../utils/format.js';
import MapCanvas from '../../components/MapCanvas.jsx';
import DataTable from '../../components/DataTable.jsx';
import StatusBadge from '../../components/StatusBadge.jsx';
import SoundType from '../../components/SoundType.jsx';

const MAX_HOURS = 24;
const SPEEDS = [1, 2, 4, 8];
const FRAME_MS = 600;

// <input type="datetime-local"> works in local time; the API wants a real UTC timestamp.
const toInput = (date) =>
  new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
const toIso = (value) => (value ? new Date(value).toISOString() : undefined);

function defaultRange() {
  const now = new Date();
  return { from: toInput(new Date(now.getTime() - 2 * 3600 * 1000)), to: toInput(now) };
}

export default function TrackingPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const cars = useAsync(() => getFleetMap({}), []);
  const [carId, setCarId] = useState(params.get('car_id') ?? '');
  const [range, setRange] = useState(defaultRange);
  const [query, setQuery] = useState(null);         // what Show was last pressed with
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [rangeError, setRangeError] = useState(null);

  const carList = cars.data?.cars ?? [];
  useEffect(() => {
    if (!carId && carList.length > 0) setCarId(String(carList[0].car_id));
  }, [carList, carId]);

  const track = useAsync(
    () => (query ? getCarTrack(query.carId, { from: query.from, to: query.to }) : Promise.resolve(null)),
    [query]);

  const points = track.data?.points ?? [];
  const alerts = track.data?.alerts ?? [];

  useEffect(() => { setIndex(0); setPlaying(false); }, [track.data]);

  useEffect(() => {
    if (!playing || points.length === 0) return undefined;
    if (index >= points.length - 1) { setPlaying(false); return undefined; }
    const timer = setTimeout(() => setIndex(index + 1), FRAME_MS / speed);
    return () => clearTimeout(timer);
  }, [playing, index, speed, points.length]);

  function show(e) {
    e.preventDefault();
    const hours = (Date.parse(range.to) - Date.parse(range.from)) / 3600000;
    if (!carId) { setRangeError('Pick a car first.'); return; }
    if (Number.isNaN(hours) || hours <= 0) { setRangeError('From must be before To.'); return; }
    if (hours > MAX_HOURS) { setRangeError(`The range cannot be longer than ${MAX_HOURS} hours.`); return; }
    setRangeError(null);
    setQuery({ carId: Number(carId), from: toIso(range.from), to: toIso(range.to) });
  }

  const cursor = points[Math.min(index, Math.max(points.length - 1, 0))] ?? null;
  const pins = alerts.map((a, i) => ({
    id: a.id, lat: a.lat, lon: a.lon, label: i + 1,
    title: `${a.sound_type} · ${a.severity ?? ''}`,
    onClick: () => navigate(`/alerts/${a.id}`),
  }));

  const columns = [
    { key: 'n', header: '#', render: (a) => alerts.indexOf(a) + 1, width: '48px' },
    { key: 'timestamp', header: 'Time', render: (a) => formatDateTime(a.timestamp) },
    { key: 'sound_type', header: 'Sound type', render: (a) => <SoundType value={a.sound_type} /> },
    { key: 'severity', header: 'Severity', render: (a) => <StatusBadge value={a.severity} /> },
    { key: 'status', header: 'Status', render: (a) => <StatusBadge value={a.status} /> },
  ];

  return (
    <div className="page">
      <div className="page-head">
        <h1>Tracking</h1>
        <span className="frame-id">F10</span>
      </div>

      <form className="filter-bar" onSubmit={show}>
        <label className="field">
          <span>Car</span>
          <select value={carId} onChange={(e) => setCarId(e.target.value)}>
            {carList.map((c) => <option key={c.car_id} value={c.car_id}>{c.name}</option>)}
          </select>
        </label>
        <label className="field">
          <span>From</span>
          <input type="datetime-local" value={range.from}
                 onChange={(e) => setRange({ ...range, from: e.target.value })} />
        </label>
        <label className="field">
          <span>To</span>
          <input type="datetime-local" value={range.to}
                 onChange={(e) => setRange({ ...range, to: e.target.value })} />
        </label>
        <div className="filter-actions">
          <button type="submit" className="btn btn-primary">Show</button>
          <button type="button" className="btn" onClick={() => setRange(defaultRange())}>Last 2 h</button>
        </div>
      </form>

      {rangeError && <p className="state-error">{rangeError}</p>}
      {track.error && <p className="state-error">Could not load the route: {track.error.message}</p>}

      <section className="panel">
        <div className="section-head">
          <h2>{query ? `Route · ${points.length} points · ${alerts.length} alerts` : 'Pick a car and a range'}</h2>
          {cursor && <span className="muted small">at {formatDateTime(cursor.ts)}</span>}
        </div>

        <MapCanvas route={points} pins={pins} cursor={cursor} height={420}
                   empty={track.loading ? 'Loading route...' : 'No positions in this range'} />

        <div className="playback">
          <button className="btn btn-primary" disabled={points.length < 2}
                  onClick={() => setPlaying(!playing)}>
            {playing ? 'Pause' : 'Play'}
          </button>
          <input type="range" min="0" max={Math.max(points.length - 1, 0)} value={index}
                 disabled={points.length < 2} aria-label="Position in the route"
                 onChange={(e) => { setPlaying(false); setIndex(Number(e.target.value)); }} />
          <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))} aria-label="Playback speed">
            {SPEEDS.map((s) => <option key={s} value={s}>{s}x</option>)}
          </select>
          <span className="muted small">
            {points.length > 0 ? `${index + 1} / ${points.length}` : '-'}
            {cursor?.speed_kmh != null && ` · ${cursor.speed_kmh} km/h`}
          </span>
        </div>
      </section>

      <section className="panel">
        <div className="section-head">
          <h2>Alerts on this route</h2>
        </div>
        <DataTable columns={columns} rows={alerts} loading={track.loading && !track.data}
                   error={track.error} empty="No alerts in this range"
                   onRowClick={(a) => navigate(`/alerts/${a.id}`)} />
      </section>
    </div>
  );
}
