// Dashboard: Overview (F2 for owners, F6 for cloud staff) and Subscriptions (F3, owners only),
// chosen with ?tab=. IoT staff cannot open this page at all.
import { PAGES, frameFor, visibleTabs } from '../../config/pages.js';
import { useActiveTab } from '../../components/PageTabs.jsx';
import { useRole } from '../../config/RoleContext.jsx';
import OwnerOverview from './OwnerOverview.jsx';
import FleetOverview from './FleetOverview.jsx';
import SubscriptionsTab from './SubscriptionsTab.jsx';

const page = PAGES.find((p) => p.key === 'dashboard');

export default function DashboardPage() {
  const { role } = useRole();
  const tabs = visibleTabs(page, role);
  const tabKey = useActiveTab(tabs);
  const tab = tabs.find((t) => t.key === tabKey) ?? tabs[0];

  return (
    <div className="page">
      <div className="page-head">
        <h1>Dashboard · {tab.label}</h1>
        <span className="frame-id">{frameFor(tab, role)}</span>
      </div>
      {tab.key === 'subscriptions'
        ? <SubscriptionsTab />
        : (role === 'owner' ? <OwnerOverview /> : <FleetOverview />)}
    </div>
  );
}
