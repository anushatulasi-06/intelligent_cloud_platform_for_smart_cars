import { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { getSimulatorCars, getSimulatorScenarioAudio, getSimulatorState, listDevices, queueDeviceCommand, USE_MOCK } from '../../api/endpoints.js';
import { useRole } from '../../config/RoleContext.jsx';
import { useAsync } from '../../utils/useAsync.js';
import { usePolling } from '../../utils/usePolling.js';
import { formatDateTime, formatPercent, humanize } from '../../utils/format.js';
import DataTable from '../../components/DataTable.jsx';
import StatusBadge from '../../components/StatusBadge.jsx';
import SoundType from '../../components/SoundType.jsx';
import { useToast } from '../../components/Toast.jsx';
import './simulator.css';

const SCENARIOS = [
  ['emergency', 'Siren', 'Emergency vehicle'], ['in_vehicle', 'Glass / crash', 'In-vehicle incident'],
  ['human', 'Scream / cry', 'Human sound'], ['traffic', 'Horn', 'Traffic'], ['environmental', 'Rain / wind', 'Environmental'],
];
export default function SimulatorPage() {
  const { role } = useRole();
  return <Simulator key={role} role={role} />;
}
function Simulator({ role }) {
  const [params, setParams] = useSearchParams();
  const initialCar = Number(params.get('car_id'));
  const carId = Number.isInteger(initialCar) && initialCar > 0 ? initialCar : 1;
  const [deviceId, setDeviceId] = useState('');
  const [busy, setBusy] = useState('');
  const [frameError, setFrameError] = useState(false);
  const [pendingScenario, setPendingScenario] = useState(null);
  const [preview, setPreview] = useState(null);
  const previewAudio = useRef(null);
  const previewRequest = useRef({ id: 0, controller: null, url: null });
  const toast = useToast();
  const state = useAsync(() => getSimulatorState(carId), [carId, role]);
  const cars = useAsync(getSimulatorCars, [role]);
  const devices = useAsync(() => listDevices({ car_id: carId, page_size: 100 }), [carId, role]);
  usePolling(state.reload, 2000, !state.loading);
  usePolling(devices.reload, 15000, !devices.loading);
  const rows = (devices.data?.items || []).filter((d) => d.car_id === carId && d.device_type === 'microphone');
  const targetId = rows.some((d) => d.id === Number(deviceId)) ? Number(deviceId) : rows[0]?.id;
  const target = rows.find((d) => d.id === targetId);
  const targetOnline = !!target?.last_seen && Date.now() - Date.parse(target.last_seen) < 90000;
  const s = state.data?.car_id === carId ? state.data : null;
  const frameUrl = s?.frame_url;
  const frameLive = !!frameUrl && !!s?.frame_at && Date.now() - Date.parse(s.frame_at) < 10000 && !frameError;
  const localDemo = s?.edge_mode === 'local_demo';
  const sourceLabel = USE_MOCK ? 'UI prototype' : localDemo ? 'Local edge demo' : s?.edge_mode === 'carla' ? 'CARLA' : 'Edge client';
  const canSend = !USE_MOCK && role === 'iot_staff' && !busy && targetOnline && !state.error;
  const position = s?.position;
  const carChoices = cars.data || [];
  useEffect(() => { setFrameError(false); }, [frameUrl]);
  useEffect(() => { setPendingScenario(null); setDeviceId(''); }, [carId]);
  useEffect(() => {
    setPreview(null);
    const request = previewRequest.current;
    const audio = previewAudio.current;
    return () => {
      request.id += 1;
      request.controller?.abort();
      audio?.pause();
      audio?.removeAttribute('src');
      if (request.url) URL.revokeObjectURL(request.url);
      request.url = null;
    };
  }, [carId]);
  async function startPreview(category) {
    const request = previewRequest.current;
    const id = ++request.id;
    request.controller?.abort();
    request.controller = new AbortController();
    previewAudio.current?.pause();
    previewAudio.current?.removeAttribute('src');
    if (request.url) URL.revokeObjectURL(request.url);
    request.url = null;
    setPreview({ category, status: 'loading' });
    try {
      const blob = await getSimulatorScenarioAudio(category, { signal: request.controller.signal });
      if (id !== request.id) return;
      request.url = URL.createObjectURL(blob);
      const audio = previewAudio.current;
      audio.src = request.url;
      audio.load();
      setPreview({ category, status: 'ready' });
      try {
        await audio.play();
      } catch {
        if (id === request.id) setPreview({ category, status: 'blocked', message: 'Audio is ready. Press Play preview to hear it.' });
      }
    } catch (error) {
      if (id === request.id && error.name !== 'AbortError') setPreview({ category, status: 'error', message: error.message });
    }
  }
  async function replayPreview() {
    try { await previewAudio.current?.play(); }
    catch { setPreview((current) => current && { ...current, status: 'blocked', message: 'Press the audio player’s Play control to hear this recording.' }); }
  }
  async function send(command, scenario) {
    if (!canSend || !targetId) return;
    // Preview and edge execution are independent: a browser audio problem must
    // never discard the normal queued command or its C3 upload.
    if (scenario) void startPreview(scenario);
    setBusy(scenario || command);
    try {
      const result = await queueDeviceCommand(targetId, { command, ...(scenario ? { payload: { scenario } } : {}) });
      if (scenario) setPendingScenario({ category: scenario, commandId: result.command_id });
      toast(`${scenario ? humanize(scenario) : humanize(command)} queued for ${target.name}`);
      state.reload();
    } catch (e) { toast(e.message, 'error'); }
    finally { setBusy(''); }
  }
  const pendingEvent = s?.events?.find((e) => e.command_id === pendingScenario?.commandId);
  return <div className="page simulator-page">
    <div className="page-head"><div><h1>Simulator</h1><p className="muted small">Car telemetry and sound scenarios · C2</p></div><span className="frame-id">F11</span></div>
    <div className="filter-bar">
      <label className="field"><span>Car</span><select value={carId} onChange={(e) => setParams({ car_id: e.target.value })}>
        {!carChoices.some((c) => c.car_id === carId) && <option value={carId}>Car {carId}</option>}
        {carChoices.map((c) => <option value={c.car_id} key={c.car_id}>C-{String(c.car_id).padStart(3, '0')} · {c.make} {c.model}{c.plate ? ` · ${c.plate}` : ''}</option>)}
      </select></label>
      <StatusBadge value={s?.online ? 'online' : 'offline'} label={s?.online ? 'Client online' : 'Client offline'} />
      <span className="sim-source">{sourceLabel}</span><span className="muted small">Updates every 2 s</span>
      <button className="btn btn-sm" disabled={state.loading} onClick={() => { state.reload(); devices.reload(); cars.reload(); }}>Refresh</button>
    </div>
    {(state.error || cars.error) && <p role="alert" className="state-error">{(state.error || cars.error).message}</p>}
    {USE_MOCK && <p className="sim-notice">UI prototype data. Start the backend and set VITE_USE_MOCK=false to execute commands.</p>}
    {localDemo && <p className="sim-notice">Local edge demo: illustrated road frames and {s?.audio_source === 'recordings' ? 'recorded sound scenarios' : s?.audio_source === 'synthetic' ? 'synthetic test tones' : 'configured audio scenarios'} exercise the real upload and classification pipeline. Scenario labels describe the input; the model determines the result.</p>}
    {!s?.online && !state.loading && !state.error && <div className="sim-notice"><strong>Waiting for an edge client.</strong> Start the local demo or CARLA adapter for this car. A recent microphone heartbeat is required to run scenarios. <Link to={`/devices?car_id=${carId}`}>Check devices</Link></div>}
    <div className="sim-main-grid">
      <section className="panel sim-camera" aria-label="Camera and telemetry">
        <div className="sim-section-head"><h2>Camera · Car {carId}</h2><span className={`sim-live ${frameLive ? 'sim-live-on' : ''}`}>{frameLive ? 'LIVE' : 'NO LIVE FEED'}</span></div>
        <div className="sim-frame-wrap">
          {frameUrl && !frameError ? <img className="simulator-frame" src={frameUrl} alt={`Latest ${sourceLabel} camera frame from car ${carId}`} onError={() => setFrameError(true)} /> : <div className="simulator-empty"><span className="sim-camera-symbol" aria-hidden="true">▣</span><strong>{state.loading && !s ? 'Connecting to simulator…' : frameError ? 'Camera frame unavailable' : 'Waiting for camera frames'}</strong><span className="small">The camera device uploads a JPEG every 2 seconds.</span></div>}
          {frameUrl && !frameLive && <span className="sim-stale">Last received frame · feed stale</span>}
        </div>
        <div className="sim-telemetry" aria-label="Position"><span>{position?.town || sourceLabel}</span><strong>{position?.speed_kmh?.toFixed(1) ?? '—'} km/h</strong><span>Heading {position?.heading?.toFixed(0) ?? '—'}°</span><span>{position ? `${position.lat?.toFixed(5)}, ${position.lon?.toFixed(5)}` : 'No position yet'}</span></div>
        <div className="sim-camera-footer muted small">Position: {formatDateTime(position?.ts)} · Frame: {formatDateTime(s?.frame_at)}</div>
        {frameError && <button className="btn btn-sm" onClick={() => { setFrameError(false); state.reload(); }}>Reload frame</button>}
      </section>
      <section className="panel sim-controls"><h2>Scenario controls</h2>
        {role === 'iot_staff' ? <>
          <label className="field"><span>Microphone device</span><select value={targetId ?? ''} onChange={(e) => setDeviceId(e.target.value)} disabled={!rows.length}><option disabled value="">No microphone on this car</option>{rows.map((d) => <option value={d.id} key={d.id}>{d.name}</option>)}</select></label>
          {devices.error && <p className="state-error">{devices.error.message}</p>}
          <div className="sim-route-actions"><button className="btn btn-primary" disabled={!canSend} onClick={() => send('start_route')}>Start route</button><button className="btn" disabled={!canSend} onClick={() => send('stop_route')}>Stop route</button></div>
          <p className="muted small">Play a sound event in the car:</p>
          <div className="sim-scenarios">{SCENARIOS.map(([category, label, description]) => <button key={category} className={`sim-scenario sim-scenario-${category}`} disabled={!canSend} onClick={() => send('simulate_event', category)}><span className="sound-dot" /><span><strong>{busy === category ? 'Queuing…' : label}</strong><small>{description}</small></span><span aria-hidden="true">↗</span></button>)}</div>
          <div className="sim-audio-preview" hidden={!preview}>
            <div className="sim-preview-heading"><strong>Browser sound preview</strong><span className="small">{SCENARIOS.find(([category]) => category === preview?.category)?.[1]}</span></div>
            <audio ref={previewAudio} controls preload="metadata" aria-label="Scenario preview audio"
              onPlay={() => setPreview((current) => current && { ...current, status: 'playing', message: null })}
              onPause={() => setPreview((current) => current && current.status === 'playing' ? { ...current, status: 'paused' } : current)}
              onEnded={() => setPreview((current) => current && { ...current, status: 'ended' })}
              onError={() => { if (previewRequest.current.url) setPreview((current) => current && { ...current, status: 'error', message: 'The recording could not be played. Retry the preview.' }); }} />
            <p className="small" role="status">{preview?.message || ({ loading: 'Loading the matching recording…', ready: 'Recording ready.', playing: 'Playing the matching recording.', paused: 'Preview paused.', ended: 'Preview finished.' }[preview?.status])}</p>
            {preview?.status === 'blocked' && <button className="btn btn-sm" onClick={replayPreview}>Play preview</button>}
            {preview?.status === 'error' && <button className="btn btn-sm" onClick={() => startPreview(preview.category)}>Retry preview</button>}
            <p className="muted small">This browser plays the bundled recording. An edge client with a custom scenario mapping can upload a different clip.</p>
          </div>
          <details className="sim-recording-credits"><summary>Recording credits · CC BY-NC 3.0</summary>
            <p>Five <a href="https://github.com/karolpiczak/ESC-50" target="_blank" rel="noreferrer">ESC-50</a> recordings, curated by Karol J. Piczak, are used unchanged for this educational demo under <a href="https://creativecommons.org/licenses/by-nc/3.0/" target="_blank" rel="noreferrer">CC BY-NC 3.0</a>.</p>
            <ul><li>Siren: <a href="https://freesound.org/people/guitarguy1985/sounds/54084/" target="_blank" rel="noreferrer">civildefense.wav</a> by guitarguy1985 (CC0).</li>
              <li>Glass breaking: <a href="https://freesound.org/people/gezortenplotz/sounds/20133/" target="_blank" rel="noreferrer">breaking_glass.wav</a> by gezortenplotz (CC BY 3.0).</li>
              <li>Human: <a href="https://freesound.org/people/the_yura/sounds/211527/" target="_blank" rel="noreferrer">Crying newborn baby child 3.WAV</a> by the_yura (CC0).</li>
              <li>Horn: <a href="https://freesound.org/people/cognito%20perceptu/sounds/17124/" target="_blank" rel="noreferrer">car sharp horn.wav</a> by cognito perceptu (CC0).</li>
              <li>Rain: <a href="https://freesound.org/people/cognito%20perceptu/sounds/17367/" target="_blank" rel="noreferrer">unlocki door to mod rain.wav</a> by cognito perceptu (CC0).</li></ul>
            <a href="https://github.com/karolpiczak/ESC-50/blob/33c8ce9eb2cf0b1c2f8bcf322eb349b6be34dbb6/LICENSE" target="_blank" rel="noreferrer">Complete upstream notices</a>
          </details>
          {!targetOnline && <p className="muted small">Controls become available when this microphone reports a heartbeat.</p>}
          {pendingScenario && <p className="small" role="status">{humanize(pendingScenario.category)} · command #{pendingScenario.commandId}: <strong>{pendingEvent ? humanize(pendingEvent.status) : 'Queued'}</strong>. <Link to={`/devices?tab=data&car_id=${carId}`}>View audio processing</Link></p>}
        </> : <p className="muted">Cloud Staff can monitor the live feed and classifications. Sign in as IoT Staff to control the route and scenarios.</p>}
      </section>
    </div>
    <div className="sim-bottom-grid">
      <section className="panel"><h2>Last classification</h2>{s?.last_alert ? <>
        <dl className="kv"><div className="kv-row"><dt>Sound type</dt><dd><SoundType value={s.last_alert.sound_type} /></dd></div><div className="kv-row"><dt>Confidence</dt><dd>{formatPercent(s.last_alert.confidence)}</dd></div><div className="kv-row"><dt>Severity</dt><dd><StatusBadge value={s.last_alert.severity} /></dd></div></dl>
        <div className="action-row">{role === 'cloud_staff' && <><Link className="btn btn-sm" to={`/alerts/${s.last_alert.id}`}>Open alert (F7)</Link><Link className="btn btn-sm" to={`/map?car_id=${carId}`}>Show on map</Link></>}<Link className="btn btn-sm" to={`/devices?tab=data&car_id=${carId}`}>Device data</Link></div>
      </> : <p className="muted">No classification for this car yet. Send a scenario and follow its audio record through processing.</p>}</section>
      <section className="sim-event-log"><h2>Event log</h2><DataTable rows={s?.events} loading={state.loading && !s} error={state.error} empty="No commands for this car yet" rowKey={(r) => r.command_id ?? `${r.ts}-${r.command}`} columns={[
        { key: 'ts', header: 'Time', render: (r) => formatDateTime(r.ts) }, { key: 'command', header: 'Command', render: (r) => humanize(r.command) },
        { key: 'status', header: 'Status', render: (r) => <StatusBadge value={r.status} /> }, { key: 'detail', header: 'Result', render: (r) => r.detail || '—' },
      ]} /></section>
    </div>
  </div>;
}
