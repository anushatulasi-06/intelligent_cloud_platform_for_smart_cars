// Config (cloud_staff): tabs Alert types (F8a), Models (F8b), Database (F14), selected via ?tab=.
import { PAGES } from '../../config/pages.js';
import { useActiveTab } from '../../components/PageTabs.jsx';
import AlertTypesTab from './AlertTypesTab.jsx';
import ModelsTab from './ModelsTab.jsx';
import DatabaseTab from './DatabaseTab.jsx';

const page = PAGES.find((p) => p.key === 'config');

export default function ConfigPage() {
  const tabKey = useActiveTab(page.tabs);
  const tab = page.tabs.find((t) => t.key === tabKey);
  return (
    <div className="page">
      <div className="page-head">
        <h1>Config · {tab.label}</h1>
        <span className="frame-id">{tab.frame}</span>
      </div>
      {tabKey === 'alert-types' && <AlertTypesTab />}
      {tabKey === 'models' && <ModelsTab />}
      {tabKey === 'database' && <DatabaseTab />}
    </div>
  );
}
