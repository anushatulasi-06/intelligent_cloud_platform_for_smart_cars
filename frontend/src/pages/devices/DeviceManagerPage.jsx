import { PAGES } from '../../config/pages.js';
import { useRole } from '../../config/RoleContext.jsx';
import { useActiveTab } from '../../components/PageTabs.jsx';
import DevicesTab from './DevicesTab.jsx';
import DeviceDataTab from './DeviceDataTab.jsx';

const page = PAGES.find((p) => p.key === 'devices');

export default function DeviceManagerPage() {
  const tab = useActiveTab(page.tabs);
  const { role } = useRole();
  return (
    <div className="page">
      <div className="page-head">
        <h1>Device Manager · {tab === 'data' ? 'Device data' : 'Devices'}</h1>
        <span className="frame-id">{tab === 'data' ? 'F13' : 'F4'}</span>
      </div>
      {tab === 'data' ? <DeviceDataTab key={role} /> : <DevicesTab key={role} />}
    </div>
  );
}
