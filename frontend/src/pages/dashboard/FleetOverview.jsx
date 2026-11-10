// F6 Cloud staff dashboard: fleet counters, two bar charts and the latest alerts with the
// same Ack / Resolve rules as the Alerts page.
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { getFleetSummary, listAlerts, updateAlert } from '../../api/endpoints.js';
import { useAsync } from '../../utils/useAsync.js';
import { usePolling } from '../../utils/usePolling.js';
import { formatDateTime, humanize } from '../../utils/format.js';
import { useRole } from '../../config/RoleContext.jsx';
import { ALERTS_CHANGED } from '../../layout/alertEvents.js';
import BarChart from '../../components/BarChart.jsx';
import DataTable from '../../components/DataTable.jsx';
import KpiCard from '../../components/KpiCard.jsx';
import StatusBadge from '../../components/StatusBadge.jsx';
import SoundType from '../../components/SoundType.jsx';
import { useToast } from '../../components/Toast.jsx';
import Icon from '../../layout/icons.jsx';
import { alertActions } from '../alerts/alertActions.js';

const REFRESH_MS = 15000;
const LATEST_ALERTS = 8;
const WINDOWS = [7, 14, 30];

export default function FleetOverview() {
  const navigate = useNavigate();
  const toast = useToast();
  const { role } = useRole();
  const [windowDays, setWindowDays] = useState(7);
  const [busyId, setBusyId] = useState(null);

  const summary = useAsync(() => getFleetSummary({ window_days: windowDays }), [windowDays]);
  const latest = useAsync(() => listAlerts({ page: 1, page_size: LATEST_ALERTS }), []);
  usePolling(summary.reload, REFRESH_MS);
  usePolling(latest.reload, REFRESH_MS);

  const data = summary.data;
  const devices = data?.devices ?? {};
  const devicesTotal = Object.values(devices).reduce((sum, n) => sum + n, 0);

  const byType = Object.entries(data?.alerts_by_type ?? {})
    .map(([label, value]) => ({ label: humanize(label), value }));
  const perDay = (data?.alerts_per_day ?? [])
    .map((d) => ({ label: d.date.slice(5), value: d.count }));

  async function change(alert, status) {
    setBusyId(alert.id);
    try {
      await updateAlert(alert.id, { status }, role);
      toast(`Alert ${status}`);
      window.dispatchEvent(new Event(ALERTS_CHANGED));
      latest.reload();
      summary.reload();
    } catch (e) {
      toast(`Could not update alert (${e.status ?? 'error'}): ${e.message}`, 'error');
    } finally {
      setBusyId(null);
    }
  }

  const columns = [
    { key: 'timestamp', header: 'Time', render: (a) => formatDateTime(a.timestamp) },
    { key: 'car_id', header: 'Car', render: (a) => `Car ${a.car_id}` },
    { key: 'sound_type', header: 'Sound type', render: (a) => <SoundType value={a.sound_type} /> },
    { key: 'severity', header: 'Severity', render: (a) => <StatusBadge value={a.severity} /> },
    { key: 'status', header: 'Status', render: (a) => <StatusBadge value={a.status} /> },
    {
      key: 'actions',
      header: 'Actions',
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
    <div className="stack">
      <div className="kpi-row">
        <KpiCard label="Total cars" value={data?.total_cars} color="primary" icon={<Icon name="car" />} />
        <KpiCard label="Devices online" value={`${devices.online ?? 0} / ${devicesTotal}`} color="success"
                 icon={<Icon name="devices" />} />
        <KpiCard label="Alerts today" value={data?.alerts_today} color="warning" icon={<Icon name="alerts" />} />
        <KpiCard label="Anomalies today" value={data?.anomalies_today} color="danger"
                 hint="low confidence, needs review" icon={<Icon name="cloud" />} />
      </div>

      {summary.error && <p className="state-error">Could not load the fleet summary: {summary.error.message}</p>}

      <div className="two-col">
        <section className="panel">
          <div className="section-head">
            <h2>Alerts by type</h2>
            <label className="field">
              <span>Window</span>
              <select value={windowDays} onChange={(e) => setWindowDays(Number(e.target.value))}>
                {WINDOWS.map((d) => <option key={d} value={d}>{d} days</option>)}
              </select>
            </label>
          </div>
          <BarChart data={byType} empty="No alerts in this window" />
        </section>

        <section className="panel">
          <div className="section-head">
            <h2>Alerts per day</h2>
          </div>
          <BarChart data={perDay} color="var(--sky)" empty="No alerts in this window" />
        </section>
      </div>

      <section className="panel">
        <div className="section-head">
          <h2>Latest alerts</h2>
          <button className="btn btn-sm" onClick={() => navigate('/alerts')}>All alerts</button>
        </div>
        <DataTable columns={columns} rows={latest.data?.items} loading={latest.loading && !latest.data}
                   error={latest.error} empty="No alerts yet"
                   onRowClick={(a) => navigate(`/alerts/${a.id}`)} />
      </section>
    </div>
  );
}
