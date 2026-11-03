import { useCallback, useEffect, useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { pageForPath } from '../config/pages.js';
import { getAlertSummary } from '../api/endpoints.js';
import { canAccess } from '../config/access.js';
import { useRole } from '../config/RoleContext.jsx';
import Header from './Header.jsx';
import Sidebar from './Sidebar.jsx';
import AlertsDrawer from './AlertsDrawer.jsx';
import PageTabs from '../components/PageTabs.jsx';

// Pages dispatch this after changing an alert so the header count refreshes.
export const ALERTS_CHANGED = 'alerts:changed';

export default function AppLayout() {
  const { pathname } = useLocation();
  const page = pageForPath(pathname);
  const { role } = useRole();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [alertCount, setAlertCount] = useState(null);

  const refreshCount = useCallback(() => {
    getAlertSummary({ status: 'new' }).then((s) => setAlertCount(s.total)).catch(() => setAlertCount(null));
  }, []);

  useEffect(refreshCount, [pathname, refreshCount]);
  useEffect(() => {
    window.addEventListener(ALERTS_CHANGED, refreshCount);
    return () => window.removeEventListener(ALERTS_CHANGED, refreshCount);
  }, [refreshCount]);

  const showTabs = page && page.tabs.length >= 2 && pathname === page.path && canAccess(role, page.key);

  return (
    <div className="app">
      <Sidebar />
      <div className="app-main">
        <Header title={page?.label ?? ''} alertCount={alertCount} onAlertsClick={() => setDrawerOpen(true)} />
        {showTabs && <PageTabs tabs={page.tabs} />}
        <div className="scroll-area">
          <main className="content">
            <Outlet />
          </main>
        </div>
      </div>
      <AlertsDrawer open={drawerOpen} onClose={() => setDrawerOpen(false)} />
    </div>
  );
}
