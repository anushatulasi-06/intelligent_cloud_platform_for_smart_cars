// F12 Alerts list (/alerts): filters, paged table (20 rows), Ack / Resolve per role.
// A row click opens F7 (/alerts/:id). No tab bar.
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { listAlerts, updateAlert } from '../../api/endpoints.js';
import { useAsync } from '../../utils/useAsync.js';
import { formatDateTime, formatPercent } from '../../utils/format.js';
import { useRole } from '../../config/RoleContext.jsx';
import { ALERTS_CHANGED } from '../../layout/alertEvents.js';
import DataTable from '../../components/DataTable.jsx';
import Pagination from '../../components/Pagination.jsx';
import FilterBar from '../../components/FilterBar.jsx';
import StatusBadge from '../../components/StatusBadge.jsx';
import SoundType, { SOUND_TYPES } from '../../components/SoundType.jsx';
import { useToast } from '../../components/Toast.jsx';
import { alertActions } from './alertActions.js';

const PAGE_SIZE = 20;
const SEVERITIES = ['low', 'medium', 'high', 'critical'];
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

// Date inputs give YYYY-MM-DD; "to" covers the whole day.
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

  async function change(alert, status) {
    setBusyId(alert.id);
    try {
      await updateAlert(alert.id, { status }, role);
      toast(`Alert ${status}`);
      window.dispatchEvent(new Event(ALERTS_CHANGED));
      reload();
    } catch (e) {
      toast(`Could not update alert (${e.status ?? 'error'}): ${e.message}`, 'error');
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
          {alertActions(role, a.status).map((act) => (
            <button
              key={act.status}
              className={`btn btn-sm ${act.status === 'resolved' ? 'btn-success' : ''}`}
              disabled={!act.enabled || busyId === a.id}
              title={act.reason}
              onClick={() => change(a, act.status)}
            >
              {act.short}
            </button>
          ))}
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
        loading={loading && !data}
        error={error}
        empty="No alerts match the filters"
        onRowClick={(a) => navigate(`/alerts/${a.id}`)}
      />
      {data && <Pagination page={page} pageSize={PAGE_SIZE} total={data.total} onChange={setPage} />}
      <p className="muted small">Rows tagged “review” are low-confidence (anomaly) clips.</p>
    </div>
  );
}
