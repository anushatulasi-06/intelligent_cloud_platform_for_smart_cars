import { NavLink } from 'react-router-dom';
import { PAGES } from '../config/pages.js';
import { canAccess } from '../config/access.js';
import { useRole } from '../config/RoleContext.jsx';
import LockIcon from './LockIcon.jsx';
import Icon from './icons.jsx';

// Always shows all 7 pages; pages the role cannot open are dimmed and locked.
// Tile colours come from --tile-<page key> in theme.css.
export default function Sidebar() {
  const { role } = useRole();
  return (
    <nav className="sidebar" aria-label="Main">
      <div className="sidebar-brand">
        <span className="brand-mark"><Icon name="car" size={18} /></span>
        Smart Car Platform
      </div>
      <ul>
        {PAGES.map((p) => {
          const tile = (
            <span className="nav-tile" style={{ background: `var(--tile-${p.key})` }}>
              <Icon name={p.key} size={16} />
            </span>
          );
          return (
            <li key={p.key}>
              {canAccess(role, p.key) ? (
                <NavLink to={p.path} className={({ isActive }) => `nav-item ${isActive ? 'nav-active' : ''}`}>
                  {tile}
                  <span className="nav-label">{p.label}</span>
                </NavLink>
              ) : (
                <span className="nav-item nav-locked" aria-disabled="true" title={`Not available for ${role}`}>
                  {tile}
                  <span className="nav-label">{p.label}</span>
                  <LockIcon />
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
