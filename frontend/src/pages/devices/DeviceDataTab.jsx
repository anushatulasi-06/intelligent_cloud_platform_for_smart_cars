import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
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
import './device-data.css';
import { dataFilterParams, parseDataFilters } from './dataFilters.js';

const STATUSES = ['pending', 'processing', 'processed', 'failed'];
const FILTERS = [
  { name: 'status', label: 'Status', type: 'select', options: STATUSES },
  { name: 'car_id', label: 'Car ID', type: 'number', min: 1, step: 1 },
  { name: 'device_id', label: 'Device ID', type: 'number', min: 1, step: 1 },
  { name: 'from', label: 'From', type: 'date' },
  { name: 'to', label: 'To', type: 'date' },
];
const PAGE_SIZE = 20;

function RecordDetail({ id, onClose, onRetry, retrying }) {
  const { role } = useRole();
  const record = useAsync(() => getDeviceDataRecord(id), [id, role]);
  const audio = useAsync(() => getDeviceDataAudio(id), [id, role]);
  const [audioFailed, setAudioFailed] = useState(false);
  const [audioVersion, setAudioVersion] = useState(0);
  useEffect(() => { setAudioFailed(false); }, [audio.data?.url]);
  function reloadAudio() { setAudioFailed(false); setAudioVersion((value) => value + 1); audio.reload(); }
  usePolling(record.reload, 10000, !record.loading);
  useEffect(() => {
    if (!audio.data) return undefined;
    const timer = setTimeout(audio.reload, Math.max(5, audio.data.expires_in - 15) * 1000);
    return () => clearTimeout(timer);
  }, [audio.data, audio.reload]);
  const r = record.data;
  return (
    <Modal open title="Audio record" onClose={onClose}>
      <div className="stack record-detail">
        {record.error && <p className="state-error" role="alert">{record.error.message} <button className="btn btn-sm" onClick={record.reload}>Reload record</button></p>}
        {record.loading && !r && <p>Loading record...</p>}
        {r && <>
          <code>{r.record_id}</code>
          <dl className="kv">
            {[
              ['Car / device', `${r.car_id} / ${r.device_id}`],
              ['State', <StatusBadge key="state" value={r.status} tone={r.status === 'pending' ? 'medium' : undefined} />],
              ['Received', formatDateTime(r.received_timestamp)], ['Captured', formatDateTime(r.captured_at)],
              ['Attempts', `${r.attempts} / 3`], ['Processed', formatDateTime(r.processed_at)],
              ...(r.status === 'processing' ? [['Lease expires', formatDateTime(r.lease_until)]] : []),
              ['Format', r.content_type || '-'], ['Clip ID', r.clip_id || '-'], ['Clip', `${r.duration_s ?? '?'} seconds · ${formatBytes(r.size_bytes)}`],
              ['Trigger', r.trigger || '-'],
              ...(r.lat != null && r.lon != null ? [['Capture position', `${r.lat}, ${r.lon}`]] : []),
              ['Error', r.error || '-'],
            ].map(([k, v]) => <div className="kv-row" key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
          </dl>
          {r.status === 'failed' && role === 'cloud_staff' && <button className="btn btn-primary" disabled={retrying != null} onClick={async () => { if (await onRetry(r)) record.reload(); }}>{retrying === r.record_id ? 'Retrying...' : 'Retry failed record'}</button>}
          {r.status === 'failed' && role === 'iot_staff' && <p className="muted small">Cloud staff can retry failed records after the processing issue is corrected.</p>}
          {r.alert_id && (role === 'cloud_staff' ? <Link to={`/alerts/${r.alert_id}`}>View generated alert</Link> : <p className="small">Alert ID: <code>{r.alert_id}</code></p>)}
        </>}
        <div><h2>Audio clip</h2>
          {audio.loading && !audio.data && <p>Loading audio...</p>}
          {audio.error && <p className="state-error" role="alert">{audio.error.message} <button className="btn btn-sm" onClick={reloadAudio}>Retry link</button></p>}
          {audio.data && <>
            <audio key={`${audio.data.url}-${audioVersion}`} aria-label="Raw audio recording" className="audio" controls preload="none" src={audio.data.url} onError={() => setAudioFailed(true)} />
            {audioFailed && <p className="state-error" role="alert">Audio could not be played. Refresh the link and try again. <button className="btn btn-sm" onClick={reloadAudio}>Refresh audio link</button></p>}
            <p className="small"><a href={audio.data.url} target="_blank" rel="noreferrer">Open raw audio</a> <span className="muted">· Link expires in {Math.round(audio.data.expires_in / 60)} minutes</span></p>
          </>}
        </div>
      </div>
    </Modal>
  );
}

export default function DeviceDataTab() {
  const { role } = useRole();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => parseDataFilters(params), [params]);
  const [page, setPage] = useState(1);
  useEffect(() => { setPage(1); }, [filters]);
  const [selected, setSelected] = useState(null);
  const [retrying, setRetrying] = useState(null);
  const [filterError, setFilterError] = useState('');
  const records = useAsync(() => listDeviceData({
    ...filters, page, page_size: PAGE_SIZE,
    from: filters.from ? new Date(`${filters.from}T00:00:00`).toISOString() : undefined,
    to: filters.to ? new Date(`${filters.to}T23:59:59.999`).toISOString() : undefined,
  }), [filters, page, role]);
  const stats = useAsync(() => getDeviceDataStats({ window_hours: 12 }), [role]);
  usePolling(() => { records.reload(); stats.reload(); }, 10000, !records.loading && !stats.loading);
  async function retry(record) {
    setRetrying(record.record_id);
    try { await retryDeviceData(record.record_id); toast('Record queued for retry'); records.reload(); stats.reload(); return true; }
    catch (e) { toast(e.message, 'error'); return false; }
    finally { setRetrying(null); }
  }
  function applyFilters(values) {
    if (values.from && values.to && values.from > values.to) {
      setFilterError('From date must be on or before To date.');
      return;
    }
    setFilterError(''); setParams(dataFilterParams(params, values)); setPage(1);
  }
  const hours = stats.data?.per_hour?.slice(-12) || [];
  const max = Math.max(1, ...hours.map((h) => h.count));
  return (
    <div className="stack device-data-monitor">
      <div className="section-head"><p className="muted small">Audio intake and processing · updates every 10 seconds</p><button className="btn btn-sm" disabled={records.loading || stats.loading} onClick={() => { records.reload(); stats.reload(); }}>Refresh records</button></div>
      <div className="kpi-row">
        {STATUSES.map((status, i) => <KpiCard key={status} label={status === 'processed' ? 'Processed (24 h)' : humanize(status)} value={stats.data?.counts?.[status]} color={['warning', 'sky', 'success', 'danger'][i]} />)}
      </div>
      {stats.error && <p className="state-error">{stats.error.message}</p>}
      <section className="panel">
        <div className="section-head"><h2>Clips received per hour</h2><span className="small muted">Last 12 hours · Local time</span></div>
        {hours.length ? <div className="intake-chart" role="img" aria-label={hours.map((h) => `${formatDateTime(h.hour)}: ${h.count} clips`).join('; ')}>
          {hours.map((h) => <div className="intake-column" key={h.hour} title={`${formatDateTime(h.hour)} · ${h.count} clips`}><span className="small">{h.count}</span><div className="intake-track"><div className="intake-bar" style={{ height: `${h.count / max * 100}%` }} /></div><span className="small muted">{new Date(h.hour).toLocaleTimeString([], { hour: '2-digit', hour12: false })}</span></div>)}
        </div> : <p className="muted">{stats.loading ? 'Loading intake...' : 'No clips received in this period.'}</p>}
        {stats.data && <p className="small muted">Stored: {formatBytes(stats.data.bytes_stored)} · Average processing latency: {stats.data.avg_latency_s == null ? '-' : `${stats.data.avg_latency_s.toFixed(2)} s`}</p>}
      </section>
      <FilterBar fields={FILTERS} values={filters} onApply={applyFilters} applyLabel="Search" />
      {filterError && <p className="state-error" role="alert">{filterError}</p>}
      <p className="muted small">Dates use your local time zone. Select a record for audio, processing details and the generated alert.</p>
      <DataTable rows={records.data?.items} rowKey="record_id" loading={records.loading && !records.data} error={records.error} empty="No records match the filters" onRowClick={(r) => setSelected(r.record_id)} columns={[
        { key: 'record_id', header: 'Record', render: (r) => <button className="btn btn-sm" title={r.record_id} onClick={(e) => { e.stopPropagation(); setSelected(r.record_id); }}>{r.record_id.slice(0, 8)}…</button> },
        { key: 'car_id', header: 'Car', render: (r) => `Car ${r.car_id}` },
        { key: 'received_timestamp', header: 'Received', render: (r) => formatDateTime(r.received_timestamp) },
        { key: 'status', header: 'Status', render: (r) => <StatusBadge value={r.status} tone={r.status === 'pending' ? 'medium' : undefined} /> },
        { key: 'attempts', header: 'Tries' },
        { key: 'retry', header: 'Actions', render: (r) => role === 'cloud_staff' && r.status === 'failed' ? <button className="btn btn-sm" disabled={retrying != null} onClick={(e) => { e.stopPropagation(); retry(r); }}>{retrying === r.record_id ? 'Retrying...' : 'Retry'}</button> : null },
      ]} />
      {records.data && <Pagination page={page} pageSize={PAGE_SIZE} total={records.data.total} onChange={setPage} />}
      {selected && <RecordDetail key={selected} id={selected} onClose={() => setSelected(null)} onRetry={retry} retrying={retrying} />}
    </div>
  );
}
