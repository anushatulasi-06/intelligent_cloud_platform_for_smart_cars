import { canAccess } from '../config/access.js';
import { useRole } from '../config/RoleContext.jsx';
import LockIcon from './LockIcon.jsx';

// Blocks a page when the current demo role is not allowed to open it
// (e.g. a typed URL or a role switch while on the page).
export default function RequireAccess({ page, children }) {
  const { role } = useRole();
  if (canAccess(role, page)) return children;
  return (
    <div className="no-access">
      <LockIcon />
      <h2>No access</h2>
      <p className="muted">The <strong>{role}</strong> role cannot open this page. Switch role in the header to view it.</p>
    </div>
  );
}
