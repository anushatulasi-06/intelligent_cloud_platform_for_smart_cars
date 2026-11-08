import { useNavigate } from 'react-router-dom';
import { ROLE_LABELS } from '../config/access.js';
import { useRole } from '../config/RoleContext.jsx';
import Icon from './icons.jsx';

export default function Header({ title, alertCount, onAlertsClick }) {
  const { role, user, signOut } = useRole();
  const navigate = useNavigate();

  const logout = () => {
    signOut();
    navigate('/login', { replace: true });
  };

  return (
    <header className="header">
      <div className="header-title">{title}</div>
      <div className="header-right">
        <button className="alerts-pill" onClick={onAlertsClick}>
          <Icon name="alerts" size={16} />
          Alerts <span className="count-pill">{alertCount ?? '-'}</span>
        </button>
        <span className="header-user">
          <strong>{ROLE_LABELS[role]}</strong>: {user?.name ?? '-'}
        </span>
        <button className="btn btn-sm" onClick={logout}>Log out</button>
      </div>
    </header>
  );
}
