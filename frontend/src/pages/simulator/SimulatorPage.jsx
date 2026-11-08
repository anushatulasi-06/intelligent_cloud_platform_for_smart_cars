import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { getSimulatorState, listDevices, queueDeviceCommand } from '../../api/endpoints.js';
import { useRole } from '../../config/RoleContext.jsx';
import { useAsync } from '../../utils/useAsync.js';
import { usePolling } from '../../utils/usePolling.js';
import { formatDateTime, formatPercent, humanize } from '../../utils/format.js';
import DataTable from '../../components/DataTable.jsx';
import StatusBadge from '../../components/StatusBadge.jsx';
import SoundType from '../../components/SoundType.jsx';
import { useToast } from '../../components/Toast.jsx';

const SCENARIOS = ['emergency', 'in_vehicle', 'human', 'traffic', 'environmental'];

export default function SimulatorPage() {
  const { role } = useRole();
  return <Simulator key={role} role={role} />;
}

function Simulator({ role }) {
  const [params, setParams] = useSearchParams();
  const initialCar = Number(params.get('car_id'));
  const carId = Number.isInteger(initialCar) && initialCar > 0 ? initialCar : 1;
  const [draftCar, setDraftCar] = useState(String(carId));
  const [deviceId, setDeviceId] = useState('');
  const [scenario, setScenario] = useState(SCENARIOS[0]);
  const [busy, setBusy] = useState(false);
  const [frameError, setFrameError] = useState(false);
  const toast = useToast();
  const state = useAsync(() => getSimulatorState(carId), [carId, role]);
  const devices = useAsync(() => listDevices({ car_id: carId, page_size: 100 }), [carId, role]);
  usePolling(state.reload, 2000, !state.loading);
  usePolling(devices.reload, 15000, !devices.loading);
  const rows = (devices.data?.items || []).filter((d) => d.car_id === carId);
  const targetId = rows.some((d) => d.id === Number(deviceId)) ? Number(deviceId) : rows.find((d) => d.device_type === 'microphone')?.id ?? rows[0]?.id;
  const s = state.data?.car_id === carId ? state.data : null;
  const frameUrl = s?.frame_url;
  useEffect(() => { setFrameError(false); }, [frameUrl]);
  useEffect(() => { setDraftCar(String(carId)); }, [carId]);
  async function send(command) {
    if (!targetId) return;
    setBusy(true);
    try { await queueDeviceCommand(targetId, { command, ...(command === 'simulate_event' ? { payload: { scenario } } : {}) }); toast(`${humanize(command)} queued`); state.reload(); }
    catch (e) { toast(e.message, 'error'); }
    finally { setBusy(false); }
  }
  return (
    <div className="page">
      <div className="page-head"><h1>Simulator</h1><span className="frame-id">F11</span></div>
      <form className="filter-bar" onSubmit={(e) => { e.preventDefault(); setParams({ car_id: draftCar }); setFrameError(false); setDeviceId(''); }}>
        <label className="field"><span>Car ID</span><input required type="number" min={1} step={1} value={draftCar} onChange={(e) => setDraftCar(e.target.value)} /></label>
        <button className="btn btn-primary" type="submit">View car</button>
        {s && <StatusBadge value={s.online ? 'online' : 'offline'} />}
        <span className="muted small">Updates every 2 seconds</span>
      </form>
      {state.error && <p className="state-error">{state.error.message}</p>}
      {state.loading && !s && <p className="muted">Loading simulator...</p>}
      <div className="two-col">
        <section className="panel"><h2>Camera · Car {carId}</h2>
          {frameUrl && !frameError ? <img className="simulator-frame" src={frameUrl} alt={`Latest camera frame from car ${carId}`} onError={() => setFrameError(true)} /> : <div className="simulator-empty">{frameError ? 'Camera frame unavailable. Wait for the next edge upload.' : 'Waiting for a camera frame from the edge client.'}</div>}
          {frameError && <button className="btn btn-sm" onClick={() => { setFrameError(false); state.reload(); }}>Reload frame</button>}
        </section>
        <section className="panel"><h2>Position</h2>
          {s?.position ? <dl className="kv">{[
            ['Latitude', s.position.lat], ['Longitude', s.position.lon], ['Speed', `${s.position.speed_kmh ?? 0} km/h`], ['Heading', `${s.position.heading ?? 0}°`], ['Reported', formatDateTime(s.position.ts)],
          ].map(([k, v]) => <div className="kv-row" key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl> : <p className="muted">No position has been reported.</p>}
          {role === 'cloud_staff' && <Link className="btn btn-sm" to={`/map?car_id=${carId}`}>Open map</Link>}
        </section>
      </div>
      {role === 'iot_staff' && <section className="panel stack"><h2>Route and sound scenarios</h2>
        {devices.error && <p className="state-error">{devices.error.message}</p>}
        <div className="action-row">
          <label className="field"><span>Target device</span><select value={targetId ?? ''} onChange={(e) => setDeviceId(e.target.value)} disabled={!rows.length}><option disabled value="">No devices on this car</option>{rows.map((d) => <option value={d.id} key={d.id}>{d.name} · {d.device_type}</option>)}</select></label>
          <button className="btn btn-primary" disabled={busy || !targetId} onClick={() => send('start_route')}>Start route</button>
          <button className="btn" disabled={busy || !targetId} onClick={() => send('stop_route')}>Stop route</button>
        </div>
        <div className="action-row"><label className="field"><span>Sound scenario</span><select value={scenario} onChange={(e) => setScenario(e.target.value)}>{SCENARIOS.map((v) => <option key={v} value={v}>{humanize(v)}</option>)}</select></label><button className="btn btn-primary" disabled={busy || !targetId} onClick={() => send('simulate_event')}>Simulate event</button></div>
        <p className="muted small">The connected edge client executes queued commands and sends audio through the normal upload pipeline.</p>
      </section>}
      <section className="panel"><h2>Latest alert</h2>{s?.last_alert ? <div className="action-row"><SoundType value={s.last_alert.sound_type} /><StatusBadge value={s.last_alert.severity} /><span>{formatPercent(s.last_alert.confidence)} confidence</span>{role === 'cloud_staff' && <Link className="btn btn-sm" to={`/alerts/${s.last_alert.id}`}>Open alert</Link>}</div> : <p className="muted">No alert for this car yet.</p>}</section>
      <section><h2>Recent events</h2><DataTable rows={s?.events} loading={state.loading && !s} empty="No commands for this car yet" rowKey={(r) => `${r.command_id ?? r.ts}-${r.command}`} columns={[
        { key: 'ts', header: 'Time', render: (r) => formatDateTime(r.ts) },
        { key: 'command', header: 'Command', render: (r) => humanize(r.command) },
        { key: 'status', header: 'Status', render: (r) => <StatusBadge value={r.status} /> },
      ]} /></section>
    </div>
  );
}
