// F7 Alert detail (/alerts/:id): facts and class probabilities side by side,
// then the audio player, then status and notes with the actions the role may use.
import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { getAlert, alertAudio, updateAlert } from '../../api/endpoints.js';
import { useAsync } from '../../utils/useAsync.js';
import { formatDateTime, formatPercent } from '../../utils/format.js';
import { useRole } from '../../config/RoleContext.jsx';
import { canAccess } from '../../config/access.js';
import { ALERTS_CHANGED } from '../../layout/alertEvents.js';
import StatusBadge from '../../components/StatusBadge.jsx';
import SoundType from '../../components/SoundType.jsx';
import { useToast } from '../../components/Toast.jsx';
import { alertActions } from './alertActions.js';

// Bar order from the F7 template.
const PROB_ORDER = ['emergency', 'traffic', 'in_vehicle', 'human', 'environmental'];

export default function AlertDetailPage() {
  const { id } = useParams();
  const { role } = useRole();
  const toast = useToast();
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState(null);
  const alert = useAsync(() => getAlert(id), [id]);
  const audio = useAsync(() => alertAudio(id), [id]);

  // The presigned link expires after expires_in seconds; fetch a new one shortly before.
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
      await updateAlert(id, { status, note: note || undefined }, role);
      toast(`Alert ${status}`);
      setNote('');
      window.dispatchEvent(new Event(ALERTS_CHANGED));
      alert.reload();
    } catch (e) {
      setActionError(`${e.status ?? 'Error'}: ${e.message}`);
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
  const actions = alertActions(role, a.status);
  const facts = [
    ['Car / device', `Car ${a.car_id} / device ${a.device_id}`],
    ['Sound type', <SoundType key="type" value={a.sound_type} />],
    ['Severity', <StatusBadge key="sev" value={a.severity} />],
    ['Confidence', formatPercent(a.confidence)],
    ['Anomaly', a.is_anomaly ? <StatusBadge key="an" value="review" label="yes – review" /> : 'No'],
    ['Time', formatDateTime(a.timestamp)],
    ['Position', `${a.lat}, ${a.lon}`],
  ];

  return (
    <div className="page">
      {back}
      <div className="page-head">
        <h1>Alert {a.id.slice(0, 6)}…</h1>
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
            {PROB_ORDER.map((cls) => {
              const p = a.probs?.[cls] ?? 0;
              return (
                <li key={cls}>
                  <span className="prob-label"><SoundType value={cls} /></span>
                  <span className="prob-track">
                    <span className="prob-fill" style={{ width: `${p * 100}%`, background: `var(--sound-${cls})` }} />
                  </span>
                  <span className="prob-value">{formatPercent(p)}</span>
                </li>
              );
            })}
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
            <audio controls preload="none" src={audio.data.url} className="audio" />
            <p className="muted small">Mock link, refreshed automatically before it expires: <code>{audio.data.url}</code></p>
          </>
        )}
      </section>

      <section className="panel">
        <h2>Status and notes</h2>
        <p className="cell-inline">Status: <StatusBadge value={a.status} /></p>
        <div className="action-row">
          <input
            type="text"
            placeholder="Note (optional)"
            aria-label="Note"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            className="grow"
            disabled={!actions.some((x) => x.enabled)}
          />
          {actions.map((act) => (
            <button
              key={act.status}
              className={`btn ${act.variant}`}
              disabled={!act.enabled || busy}
              title={act.reason}
              onClick={() => change(act.status)}
            >
              {act.label}
            </button>
          ))}
        </div>
        {actionError && <p className="state-error">{actionError}</p>}
        {a.resolution_note && <p className="muted small">Resolution note: {a.resolution_note}</p>}
        {canAccess(role, 'map') && (
          <div className="action-row action-row-top">
            <Link to={`/map?car_id=${a.car_id}`} className="btn">Show on map</Link>
          </div>
        )}
      </section>
    </div>
  );
}
