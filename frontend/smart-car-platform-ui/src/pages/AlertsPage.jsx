// F12 Alerts: filterable, paged list (20 rows). Owners can Ack; cloud staff can Ack and Resolve.
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { listAlerts, updateAlertStatus, ALERT_TRANSITIONS } from '../api/endpoints.js';
import { useAsync } from '../utils/useAsync.js';
import { formatDateTime, formatPercent } from '../utils/format.js';
import { useRole } from '../config/RoleContext.jsx';
import { ALERTS_CHANGED } from '../layout/AppLayout.jsx';
import DataTable from '../components/DataTable.jsx';
import Pagination from '../components/Pagination.jsx';
import FilterBar from '../components/FilterBar.jsx';
import StatusBadge from '../components/StatusBadge.jsx';
import SoundType, { SOUND_TYPES } from '../components/SoundType.jsx';
import { useToast } from '../components/Toast.jsx';

const PAGE_SIZE = 20;
export const SEVERITIES = ['critical', 'high', 'medium', 'low'];
const STATUSES = ['new', 'acknowledged', 'resolved'];
const CARS = [1, 2, 3, 4].map((id) => ({ value: String(id), label: `Car ${id}` }));

const FILTER_FIELDS = [
  { name: 'car_id', label: 'Car', type: 'select', options: CARS },
  { name: 'sound_type', label: 'Sound type', type: 'select', options: SOUND_TYPES },
  { name: 'severity', label: 'Severity', type: 'select', options: SEVERITIES },
  { name: 'status', label: 'Status', type: 'select', options: STATUSES },
  { name: 'from', label: 'From', type: 'date' },
  { name: 'to', label: 'To', type: 'date' },
];
const NO_FILTERS = Object.fromEntries(FILTER_FIELDS.map((f) => [f.name, '']));

// Date inputs give YYYY-MM-DD; widen "to" to the end of that day.
function toQuery(filters, page) {
  return {
    ...filters,
    from: filters.from ? `${filters.from}T00:00:00.000Z` : undefined,
    to: filters.to ? `${filters.to}T23:59:59.999Z` : undefined,
    page,
    page_size: PAGE_SIZE,
  };
}

export default function AlertsPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const { role } = useRole();
  const [filters, setFilters] = useState(NO_FILTERS);
  const [page, setPage] = useState(1);
  const [busyId, setBusyId] = useState(null);
  const { data, loading, error, reload } = useAsync(() => listAlerts(toQuery(filters, page)), [filters, page]);

  const allowed = (a) => ALERT_TRANSITIONS[role]?.[a.status] || [];

  async function change(alert, status) {
    setBusyId(alert.id);
    try {
      await updateAlertStatus(alert.id, { status }, role);
      toast(`Alert ${status}`);
      window.dispatchEvent(new Event(ALERTS_CHANGED));
      reload();
    } catch (e) {
      toast(`Could not update alert: ${e.message}`, 'error');
    } finally {
      setBusyId(null);
    }
  }

  const columns = [
    { key: 'timestamp', header: 'Time', render: (a) => formatDateTime(a.timestamp) },
    { key: 'car_id', header: 'Car', render: (a) => `Car ${a.car_id}` },
    {
      key: 'sound_type', header: 'Sound type',
      render: (a) => (
        <span className="cell-inline">
          <SoundType value={a.sound_type} />
          {a.is_anomaly && <StatusBadge value="review" />}
        </span>
      ),
    },
    { key: 'severity', header: 'Severity', render: (a) => <StatusBadge value={a.severity} /> },
    { key: 'confidence', header: 'Confidence', render: (a) => formatPercent(a.confidence) },
    { key: 'status', header: 'Status', render: (a) => <StatusBadge value={a.status} /> },
    {
      key: 'actions', header: 'Actions',
      render: (a) => (
        <span className="cell-inline" onClick={(e) => e.stopPropagation()}>
          {allowed(a).includes('acknowledged') && (
            <button className="btn btn-sm" disabled={busyId === a.id} onClick={() => change(a, 'acknowledged')}>Ack</button>
          )}
          {allowed(a).includes('resolved') && (
            <button className="btn btn-sm btn-success" disabled={busyId === a.id} onClick={() => change(a, 'resolved')}>Resolve</button>
          )}
        </span>
      ),
    },
  ];

  return (
    <div className="page">
      <div className="page-head">
        <h1>Alerts</h1>
        <span className="frame-id">F12</span>
      </div>
      <FilterBar
        fields={FILTER_FIELDS}
        values={filters}
        onApply={(v) => { setFilters(v); setPage(1); }}
      />
      <DataTable
        columns={columns}
        rows={data?.items}
        loading={loading}
        error={error}
        empty="No alerts match the filters"
        onRowClick={(a) => navigate(`/alerts/${a.id}`)}
      />
      {data && <Pagination page={page} pageSize={PAGE_SIZE} total={data.total} onChange={setPage} />}
    </div>
  );
}
