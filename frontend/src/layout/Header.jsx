import { useNavigate } from 'react-router-dom';
import { ROLES, ROLE_HOME, ROLE_LABELS } from '../config/access.js';
import { useRole } from '../config/RoleContext.jsx';
import Icon from './icons.jsx';

export default function Header({ title, alertCount, onAlertsClick }) {
  const { role, setRole } = useRole();
  const navigate = useNavigate();
  // Switching role acts like signing in as that role: go to its home page.
  const switchRole = (r) => { setRole(r); navigate(ROLE_HOME[r]); };
  return (
    <header className="header">
      <div className="header-title">{title}</div>
      <div className="header-right">
        <button className="alerts-pill" onClick={onAlertsClick}>
          <Icon name="alerts" size={16} />
          Alerts <span className="count-pill">{alertCount ?? '-'}</span>
        </button>
        <span className="header-user"><strong>{ROLE_LABELS[role]}</strong>: XXXXX</span>
        <label className="header-role">
          <span>Role</span>
          <select value={role} onChange={(e) => switchRole(e.target.value)} aria-label="Demo role">
            {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </label>
      </div>
    </header>
  );
}
