import { useEffect } from 'react';
import { Link } from 'react-router-dom';
import { listAlerts } from '../api/endpoints.js';
import { useAsync } from '../utils/useAsync.js';
import { formatDateTime, formatPercent } from '../utils/format.js';
import { canAccess } from '../config/access.js';
import { useRole } from '../config/RoleContext.jsx';
import StatusBadge from '../components/StatusBadge.jsx';
import SoundType from '../components/SoundType.jsx';
import { ALERTS_POLL_MS } from './alertEvents.js';

// Right-side drawer with the 5 newest alerts. Polls every 5 s while open;
// a click opens F7. "Mark all as seen" clears the header's unread count.
export default function AlertsDrawer({ open, onClose, seen, onMarkAllSeen }) {
  const { role } = useRole();
  const linkable = canAccess(role, 'alerts');
  const { data, loading, error, reload } = useAsync(
    () => (open ? listAlerts({ page: 1, page_size: 5 }) : Promise.resolve(null)),
    [open]
  );

  useEffect(() => {
    if (!open) return undefined;
    const t = setInterval(reload, ALERTS_POLL_MS);
    const onKey = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => { clearInterval(t); window.removeEventListener('keydown', onKey); };
  }, [open, onClose, reload]);

  return (
    <>
      <div className={`drawer-backdrop ${open ? 'open' : ''}`} onClick={onClose} />
      <aside className={`drawer ${open ? 'open' : ''}`} aria-hidden={!open} aria-label="Newest alerts">
        <div className="drawer-head">
          <h2>Newest alerts</h2>
          <button className="btn btn-icon" onClick={onClose} aria-label="Close">×</button>
        </div>
        <div className="drawer-body">
          {loading && !data && <p className="muted">Loading...</p>}
          {error && (
            <p className="state-error">
              Error: {error.message} <button className="btn btn-sm" onClick={reload}>Retry</button>
            </p>
          )}
          {data && data.items.length === 0 && <p className="muted">No alerts</p>}
          {data && (
            <ul className="drawer-list">
              {data.items.map((a) => {
                const unread = a.status === 'new' && !seen.has(a.id);
                const body = (
                  <>
                    <div className="drawer-row">
                      <SoundType value={a.sound_type} />
                      <StatusBadge value={a.severity} />
                    </div>
                    <div className="drawer-row muted">
                      <span>Car {a.car_id} · {formatPercent(a.confidence, 0)}</span>
                      <span>{formatDateTime(a.timestamp)}</span>
                    </div>
                    <div className="drawer-row">
                      <span className="cell-inline drawer-hint">
                        {a.status}{linkable ? ' - click to open F7' : ''}
                        {a.is_anomaly && <StatusBadge value="review" />}
                      </span>
                      {unread && <span className="unread-dot" title="Unread" />}
                    </div>
                  </>
                );
                return (
                  <li key={a.id}>
                    {linkable ? (
                      <Link to={`/alerts/${a.id}`} className="drawer-item" onClick={onClose}>{body}</Link>
                    ) : (
                      <div className="drawer-item drawer-item-locked">{body}</div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
          {!linkable && <p className="muted">Your role cannot open alert details.</p>}
        </div>
        <div className="drawer-foot">
          <button className="btn" onClick={onMarkAllSeen}>Mark all as seen</button>
          {linkable && <Link to="/alerts" className="btn btn-primary" onClick={onClose}>All alerts</Link>}
        </div>
      </aside>
    </>
  );
}
