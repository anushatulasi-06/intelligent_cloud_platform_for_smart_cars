// F2 Owner dashboard: three counters, the owner's cars and their five newest alerts.
// Everything comes from GET /dashboard/owner, which only ever returns the caller's own cars.
import { useNavigate } from 'react-router-dom';
import { getOwnerDashboard } from '../../api/endpoints.js';
import { useAsync } from '../../utils/useAsync.js';
import { usePolling } from '../../utils/usePolling.js';
import { formatDateTime } from '../../utils/format.js';
import DataTable from '../../components/DataTable.jsx';
import KpiCard from '../../components/KpiCard.jsx';
import StatusBadge from '../../components/StatusBadge.jsx';
import SoundType from '../../components/SoundType.jsx';
import Icon from '../../layout/icons.jsx';

const REFRESH_MS = 15000;

export default function OwnerOverview() {
  const navigate = useNavigate();
  const { data, loading, error, reload } = useAsync(getOwnerDashboard, []);
  usePolling(reload, REFRESH_MS);

  const cars = data?.cars ?? [];
  const online = cars.filter((c) => c.status === 'online').length;

  const carColumns = [
    { key: 'car', header: 'Car', render: (c) => `${c.make} ${c.model}` },
    { key: 'devices', header: 'Devices', render: (c) => `${c.devices_online} of ${c.devices_total} online` },
    { key: 'status', header: 'Status', render: (c) => <StatusBadge value={c.status} /> },
    {
      key: 'actions',
      header: '',
      render: (c) => (
        <button className="btn btn-sm" onClick={(e) => { e.stopPropagation(); navigate(`/map?car_id=${c.id}`); }}>
          On map
        </button>
      ),
    },
  ];

  const alertColumns = [
    { key: 'timestamp', header: 'Time', render: (a) => formatDateTime(a.timestamp) },
    { key: 'car_id', header: 'Car', render: (a) => `Car ${a.car_id}` },
    { key: 'sound_type', header: 'Sound type', render: (a) => <SoundType value={a.sound_type} /> },
    { key: 'severity', header: 'Severity', render: (a) => <StatusBadge value={a.severity} /> },
    { key: 'status', header: 'Status', render: (a) => <StatusBadge value={a.status} /> },
  ];

  return (
    <div className="stack">
      <div className="kpi-row">
        <KpiCard label="My cars online" value={`${online} / ${cars.length}`} color="success"
                 icon={<Icon name="car" />} />
        <KpiCard label="Alerts today" value={data?.counts?.alerts_today} color="warning"
                 icon={<Icon name="alerts" />} />
        <KpiCard label="Unresolved" value={data?.counts?.unresolved} color="danger"
                 icon={<Icon name="alerts" />} />
      </div>

      <section className="panel">
        <div className="section-head">
          <h2>My cars</h2>
        </div>
        <DataTable columns={carColumns} rows={cars} loading={loading && !data} error={error}
                   empty="No cars are registered to your account" />
      </section>

      <section className="panel">
        <div className="section-head">
          <h2>Recent alerts</h2>
          <button className="btn btn-sm" onClick={() => navigate('/alerts')}>All alerts</button>
        </div>
        <DataTable columns={alertColumns} rows={data?.recent_alerts} loading={loading && !data}
                   error={error} empty="No alerts yet"
                   onRowClick={(a) => navigate(`/alerts/${a.id}`)} />
      </section>
    </div>
  );
}
