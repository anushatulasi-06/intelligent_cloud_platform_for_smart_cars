// F7 Alert detail: facts and class probabilities side by side, then audio, then actions.
import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { getAlert, getAlertAudio, updateAlertStatus, ALERT_TRANSITIONS } from '../api/endpoints.js';
import { useAsync } from '../utils/useAsync.js';
import { formatDateTime, formatPercent } from '../utils/format.js';
import { useRole } from '../config/RoleContext.jsx';
import { canAccess } from '../config/access.js';
import { ALERTS_CHANGED } from '../layout/alertEvents.js';
import StatusBadge from '../components/StatusBadge.jsx';
import SoundType from '../components/SoundType.jsx';
import { useToast } from '../components/Toast.jsx';

export default function AlertDetailPage() {
  const { id } = useParams();
  const { role } = useRole();
  const toast = useToast();
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState(null);
  const alert = useAsync(() => getAlert(id), [id]);
  const audio = useAsync(() => getAlertAudio(id), [id]);
  const navigate = useNavigate();

  // The presigned link lives expires_in seconds; fetch a fresh one shortly before it expires.
  const audioReload = audio.reload;
  useEffect(() => {
    if (!audio.data) return undefined;
    const t = setTimeout(audioReload, Math.max(5, audio.data.expires_in - 15) * 1000);
    return () => clearTimeout(t);
  }, [audio.data, audioReload]);

  async function change(status) {
    setBusy(true);
    setActionError(null);
    try {
      await updateAlertStatus(id, { status, note: note || undefined }, role);
      toast(`Alert ${status}`);
      setNote('');
      window.dispatchEvent(new Event(ALERTS_CHANGED));
      alert.reload();
    } catch (e) {
      setActionError(e.status === 403 || e.status === 409 ? e.message : `Could not update alert: ${e.message}`);
    } finally {
      setBusy(false);
    }
  }

  const back = <Link to="/alerts" className="back-link">← All alerts</Link>;
  if (alert.loading && !alert.data) return <div className="page">{back}<p className="muted">Loading...</p></div>;
  if (alert.error) {
    return (
      <div className="page">
        {back}
        <p className="state-error">{alert.error.status === 404 ? 'Alert not found.' : `Error: ${alert.error.message}`}</p>
      </div>
    );
  }

  const a = alert.data;
  const allowed = ALERT_TRANSITIONS[role]?.[a.status] || [];
  const probs = Object.entries(a.probs || {}).sort((x, y) => y[1] - x[1]);
  const facts = [
    ['Alert id', <code key="id">{a.id}</code>],
    ['Car / device', `Car ${a.car_id} / device ${a.device_id}`],
    ['Type', <SoundType key="type" value={a.sound_type} />],
    ['Severity', <StatusBadge key="sev" value={a.severity} />],
    ['Confidence', formatPercent(a.confidence)],
    ['Anomaly', a.is_anomaly ? <StatusBadge key="an" value="review" /> : 'No'],
    ['Status', <StatusBadge key="st" value={a.status} />],
    ['Model', a.model_version],
    ['Captured', formatDateTime(a.timestamp)],
    ['Position', `${a.lat}, ${a.lon}`],
    ['Acknowledged', a.acknowledged_at ? `${formatDateTime(a.acknowledged_at)} by user ${a.acknowledged_by}` : '-'],
    ['Resolved', a.resolved_at ? `${formatDateTime(a.resolved_at)} by user ${a.resolved_by}` : '-'],
    ['Resolution note', a.resolution_note || '-'],
  ];

  return (
    <div className="page">
      {back}
      <div className="page-head">
        <h1>Alert detail</h1>
        <span className="frame-id">F7</span>
      </div>

      <div className="two-col">
        <section className="panel">
          <h2>Alert facts</h2>
          <dl className="kv">
            {facts.map(([k, v]) => (
              <div key={k} className="kv-row"><dt>{k}</dt><dd>{v}</dd></div>
            ))}
          </dl>
        </section>
        <section className="panel">
          <h2>Class probabilities</h2>
          <ul className="prob-bars">
            {probs.map(([cls, p]) => (
              <li key={cls}>
                <span className="prob-label"><SoundType value={cls} /></span>
                <span className="prob-track">
                  <span className="prob-fill" style={{ width: `${p * 100}%`, background: `var(--sound-${cls})` }} />
                </span>
                <span className="prob-value">{formatPercent(p)}</span>
              </li>
            ))}
          </ul>
        </section>
      </div>

      <section className="panel">
        <h2>Audio clip (presigned S3 link, 5 min)</h2>
        {audio.loading && !audio.data && <p className="muted">Loading audio link...</p>}
        {audio.error && (
          <p className="state-error">Error: {audio.error.message} <button className="btn btn-sm" onClick={audio.reload}>Retry</button></p>
        )}
        {audio.data && (
          <>
            <audio controls preload="none" src={audio.data.url} onError={() => {}} className="audio" />
            <p className="muted small">
              Mock link, refreshed automatically before it expires: <code>{audio.data.url}</code>
            </p>
          </>
        )}
      </section>

      <section className="panel">
        <h2>Status and notes</h2>
        <p className="cell-inline">Status: <StatusBadge value={a.status} /></p>
        {allowed.length === 0 ? (
          <p className="muted">No status changes available for {role} on a {a.status} alert.</p>
        ) : (
          <div className="action-row">
            <input
              type="text"
              placeholder="Note (optional)"
              aria-label="Note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              className="grow"
            />
            {allowed.includes('acknowledged') && (
              <button className="btn btn-primary" disabled={busy} onClick={() => change('acknowledged')}>Acknowledge</button>
            )}
            {allowed.includes('resolved') && (
              <button className="btn btn-success" disabled={busy} onClick={() => change('resolved')}>Resolve</button>
            )}
          </div>
        )}
        {actionError && <p className="state-error">{actionError}</p>}
        {canAccess(role, 'map') && (
          <div className="action-row action-row-top">
            <button className="btn" onClick={() => navigate(`/map?car_id=${a.car_id}`)}>Show on map</button>
          </div>
        )}
      </section>
    </div>
  );
}
