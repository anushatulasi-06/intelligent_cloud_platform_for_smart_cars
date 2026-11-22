import { useCallback, useEffect, useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { pageForPath, visibleTabs } from '../config/pages.js';
import { listAlerts } from '../api/endpoints.js';
import { canAccess } from '../config/access.js';
import { useRole } from '../config/RoleContext.jsx';
import Header from './Header.jsx';
import Sidebar from './Sidebar.jsx';
import AlertsDrawer from './AlertsDrawer.jsx';
import PageTabs from '../components/PageTabs.jsx';

import { ALERTS_CHANGED, ALERTS_POLL_MS } from './alertEvents.js';

export { ALERTS_CHANGED, ALERTS_POLL_MS };

// "Seen" is a per-browser UI state (no backend endpoint in the design):
// the unread count is the number of `new` alerts the user has not marked as seen.
export default function AppLayout() {
  const { pathname } = useLocation();
  const page = pageForPath(pathname);
  const { role } = useRole();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [newIds, setNewIds] = useState(null);
  const [seen, setSeen] = useState(() => new Set());

  const refreshCount = useCallback(() => {
    // IoT staff may not read alerts at all, so asking would only produce 403s.
    if (!canAccess(role, 'alerts')) {
      setNewIds(null);
      return;
    }
    // 100 = the backend's maximum page_size (GET /alerts answers 422 above it).
    listAlerts({ status: 'new', page: 1, page_size: 100 })
      .then((r) => setNewIds(r.items.map((a) => a.id)))
      .catch(() => setNewIds(null));
  }, [role]); // refetch when the role changes: owners only count their own cars' alerts

  useEffect(() => {
    refreshCount();
    const t = setInterval(refreshCount, ALERTS_POLL_MS);
    return () => clearInterval(t);
  }, [refreshCount]);
  useEffect(() => {
    window.addEventListener(ALERTS_CHANGED, refreshCount);
    return () => window.removeEventListener(ALERTS_CHANGED, refreshCount);
  }, [refreshCount]);

  const unread = newIds ? newIds.filter((id) => !seen.has(id)).length : null;
  const markAllSeen = () => setSeen((s) => new Set([...s, ...(newIds || [])]));

  const tabs = page ? visibleTabs(page, role) : [];
  const showTabs = tabs.length >= 2 && pathname === page.path && canAccess(role, page.key);
  // Breadcrumb for sub-pages, e.g. "Alerts > Alert detail".
  const title = page && pathname !== page.path && page.key === 'alerts'
    ? `${page.label} > Alert detail`
    : page?.label ?? '';

  return (
    <div className="app">
      <Sidebar />
      <div className="app-main">
        <Header title={title} alertCount={unread} onAlertsClick={() => setDrawerOpen(true)} />
        {showTabs && <PageTabs tabs={tabs} />}
        <div className="scroll-area">
          <main className="content">
            <Outlet />
          </main>
        </div>
      </div>
      <AlertsDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        seen={seen}
        onMarkAllSeen={markAllSeen}
      />
    </div>
  );
}
