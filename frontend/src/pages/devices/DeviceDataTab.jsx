import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { getDeviceDataAudio, getDeviceDataRecord, getDeviceDataStats, listDeviceData, retryDeviceData } from '../../api/endpoints.js';
import { useRole } from '../../config/RoleContext.jsx';
import { useAsync } from '../../utils/useAsync.js';
import { usePolling } from '../../utils/usePolling.js';
import { formatDateTime, formatBytes, humanize } from '../../utils/format.js';
import DataTable from '../../components/DataTable.jsx';
import FilterBar from '../../components/FilterBar.jsx';
import Pagination from '../../components/Pagination.jsx';
import StatusBadge from '../../components/StatusBadge.jsx';
import KpiCard from '../../components/KpiCard.jsx';
import Modal from '../../components/Modal.jsx';
import { useToast } from '../../components/Toast.jsx';

const STATUSES = ['pending', 'processing', 'processed', 'failed'];
const FILTERS = [
  { name: 'status', label: 'Status', type: 'select', options: STATUSES },
  { name: 'car_id', label: 'Car ID', type: 'number' },
  { name: 'device_id', label: 'Device ID', type: 'number' },
  { name: 'from', label: 'From', type: 'date' },
  { name: 'to', label: 'To', type: 'date' },
];
const EMPTY = Object.fromEntries(FILTERS.map((f) => [f.name, '']));
const PAGE_SIZE = 20;

function RecordDetail({ id, onClose }) {
  const { role } = useRole();
  const record = useAsync(() => getDeviceDataRecord(id), [id, role]);
  const audio = useAsync(() => getDeviceDataAudio(id), [id, role]);
  usePolling(record.reload, 10000, !record.loading);
  useEffect(() => {
    if (!audio.data) return undefined;
    const timer = setTimeout(audio.reload, Math.max(5, audio.data.expires_in - 15) * 1000);
    return () => clearTimeout(timer);
  }, [audio.data, audio.reload]);
  const r = record.data;
  return (
    <Modal open title="Audio record" onClose={onClose}>
      <div className="stack">
        {record.error && <p className="state-error">{record.error.message}</p>}
        {record.loading && !r && <p>Loading record...</p>}
        {r && <>
          <code>{r.record_id}</code>
          <dl className="kv">
            {[
              ['Car / device', `${r.car_id} / ${r.device_id}`],
              ['State', <StatusBadge key="state" value={r.status} />],
              ['Received', formatDateTime(r.received_timestamp)], ['Captured', formatDateTime(r.captured_at)],
              ['Attempts', r.attempts], ['Clip', `${r.duration_s ?? '?'} seconds · ${formatBytes(r.size_bytes)}`],
              ['Trigger', r.trigger || '-'], ['Error', r.error || '-'],
            ].map(([k, v]) => <div className="kv-row" key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
          </dl>
          {r.alert_id && (role === 'cloud_staff' ? <Link to={`/alerts/${r.alert_id}`}>View generated alert</Link> : <p className="small">Alert ID: <code>{r.alert_id}</code></p>)}
        </>}
        <div><h2>Audio clip</h2>
          {audio.loading && !audio.data && <p>Loading audio...</p>}
          {audio.error && <p className="state-error">{audio.error.message} <button className="btn btn-sm" onClick={audio.reload}>Retry link</button></p>}
          {audio.data && <audio className="audio" controls preload="none" src={audio.data.url} />}
        </div>
      </div>
    </Modal>
  );
}

export default function DeviceDataTab() {
  const { role } = useRole();
  const toast = useToast();
  const [filters, setFilters] = useState(EMPTY);
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState(null);
  const [retrying, setRetrying] = useState(null);
  const records = useAsync(() => listDeviceData({
    ...filters, page, page_size: PAGE_SIZE,
    from: filters.from ? `${filters.from}T00:00:00Z` : undefined,
    to: filters.to ? `${filters.to}T23:59:59.999Z` : undefined,
  }), [filters, page, role]);
  const stats = useAsync(() => getDeviceDataStats({ window_hours: 24 }), [role]);
  usePolling(() => { records.reload(); stats.reload(); }, 10000, !records.loading && !stats.loading);
  async function retry(record) {
    setRetrying(record.record_id);
    try { await retryDeviceData(record.record_id); toast('Record queued for retry'); records.reload(); stats.reload(); }
    catch (e) { toast(e.message, 'error'); }
    finally { setRetrying(null); }
  }
  const hours = stats.data?.per_hour?.slice(-12) || [];
  const max = Math.max(1, ...hours.map((h) => h.count));
  return (
    <div className="stack">
      <div className="kpi-row">
        {STATUSES.map((status, i) => <KpiCard key={status} label={status === 'processed' ? 'Processed (24 h)' : humanize(status)} value={stats.data?.counts?.[status]} color={['warning', 'sky', 'success', 'danger'][i]} />)}
      </div>
      {stats.error && <p className="state-error">{stats.error.message}</p>}
      <section className="panel">
        <div className="section-head"><h2>Clips received · last 12 hours</h2><span className="small muted">Refreshes every 10 seconds</span></div>
        {hours.length ? <div className="intake-chart" role="img" aria-label={hours.map((h) => `${formatDateTime(h.hour)}: ${h.count} clips`).join('; ')}>
          {hours.map((h) => <div className="intake-column" key={h.hour} title={`${formatDateTime(h.hour)} · ${h.count} clips`}><span className="small">{h.count}</span><div className="intake-track"><div className="intake-bar" style={{ height: `${h.count / max * 100}%` }} /></div><span className="small muted">{new Date(h.hour).toLocaleTimeString([], { hour: '2-digit', hour12: false })}</span></div>)}
        </div> : <p className="muted">{stats.loading ? 'Loading intake...' : 'No clips received in this period.'}</p>}
        {stats.data && <p className="small muted">Stored: {formatBytes(stats.data.bytes_stored)} · Average processing latency: {stats.data.avg_latency_s == null ? '-' : `${stats.data.avg_latency_s.toFixed(2)} s`}</p>}
      </section>
      <FilterBar fields={FILTERS} values={filters} onApply={(v) => { setFilters(v); setPage(1); }} />
      <DataTable rows={records.data?.items} rowKey="record_id" loading={records.loading && !records.data} error={records.error} empty="No records match the filters" onRowClick={(r) => setSelected(r.record_id)} columns={[
        { key: 'record_id', header: 'Record', render: (r) => <button className="btn btn-sm" title={r.record_id} onClick={(e) => { e.stopPropagation(); setSelected(r.record_id); }}>{r.record_id.slice(0, 8)}…</button> },
        { key: 'received_timestamp', header: 'Received', render: (r) => formatDateTime(r.received_timestamp) },
        { key: 'car_id', header: 'Car', render: (r) => `Car ${r.car_id}` },
        { key: 'device_id', header: 'Device' },
        { key: 'status', header: 'Status', render: (r) => <StatusBadge value={r.status} tone={r.status === 'pending' ? 'medium' : undefined} /> },
        { key: 'attempts', header: 'Attempts' },
        ...(role === 'cloud_staff' ? [{ key: 'retry', header: 'Actions', render: (r) => r.status === 'failed' && <button className="btn btn-sm" disabled={retrying === r.record_id} onClick={(e) => { e.stopPropagation(); retry(r); }}>{retrying === r.record_id ? 'Retrying...' : 'Retry'}</button> }] : []),
      ]} />
      {records.data && <Pagination page={page} pageSize={PAGE_SIZE} total={records.data.total} onChange={setPage} />}
      {selected && <RecordDetail key={selected} id={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
