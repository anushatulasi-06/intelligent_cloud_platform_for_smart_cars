import { ROLES } from '../config/access.js';
import { useRole } from '../config/RoleContext.jsx';
import Icon from './icons.jsx';

export default function Header({ title, alertCount, onAlertsClick }) {
  const { role, setRole } = useRole();
  return (
    <header className="header">
      <div className="header-title">{title}</div>
      <div className="header-right">
        <button className="alerts-pill" onClick={onAlertsClick}>
          <Icon name="alerts" size={16} />
          Alerts <span className="count-pill">{alertCount ?? '-'}</span>
        </button>
        <span className="header-user"><strong>{role}</strong>: XXXXX</span>
        <label className="header-role">
          <span>Role</span>
          <select value={role} onChange={(e) => setRole(e.target.value)} aria-label="Demo role">
            {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </label>
      </div>
    </header>
  );
}
