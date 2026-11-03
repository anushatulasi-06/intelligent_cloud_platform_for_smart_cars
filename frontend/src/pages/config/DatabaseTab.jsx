// F14: store health (refresh every 30 s), per-store stats, backups, retention.
import { useEffect, useState } from 'react';
import {
  getDbHealth, getDbStats, startDbBackup, listDbBackups, runRetention,
} from '../../api/endpoints.js';
import { useAsync } from '../../utils/useAsync.js';
import { formatBytes, formatDateTime } from '../../utils/format.js';
import DataTable from '../../components/DataTable.jsx';
import KpiCard from '../../components/KpiCard.jsx';
import StatusBadge from '../../components/StatusBadge.jsx';
import ConfirmDialog from '../../components/ConfirmDialog.jsx';
import { useToast } from '../../components/Toast.jsx';
import Icon from '../../layout/icons.jsx';

const HEALTH_REFRESH_MS = 30000;
const BACKUP_POLL_MS = 2000;
const HEALTH_COLOR = { healthy: 'success', degraded: 'warning', down: 'danger' };
const healthColor = (s) => HEALTH_COLOR[s] || 'primary';

export default function DatabaseTab() {
  return (
    <div className="stack">
      <HealthCards />
      <section>
        <h2>Stores</h2>
        <StoresTable />
      </section>
      <Backups />
      <Retention />
    </div>
  );
}

function HealthCards() {
  const { data, error, reload } = useAsync(getDbHealth, []);
  useEffect(() => {
    const t = setInterval(reload, HEALTH_REFRESH_MS);
    return () => clearInterval(t);
  }, [reload]);

  if (error) return <p className="state-error">Health check failed: {error.message}</p>;
  return (
    <section>
      <div className="kpi-row">
        <KpiCard
          label="PostgreSQL"
          color={healthColor(data?.postgres.status)}
          icon={<Icon name="database" size={22} />}
          value={<StatusBadge value={data?.postgres.status} />}
          hint={data && `${data.postgres.latency_ms} ms · pool ${data.postgres.pool_in_use}/${data.postgres.pool_size}`}
        />
        <KpiCard label="MongoDB" color={healthColor(data?.mongodb.status)} icon={<Icon name="database" size={22} />} value={<StatusBadge value={data?.mongodb.status} />} hint={data && `${data.mongodb.latency_ms} ms`} />
        <KpiCard label="S3" color={healthColor(data?.s3.status)} icon={<Icon name="cloud" size={22} />} value={<StatusBadge value={data?.s3.status} />} hint="Object storage" />
      </div>
      <p className="muted small">Checked {data ? formatDateTime(data.checked_at) : '...'} · refreshes every 30 s</p>
    </section>
  );
}

function StoresTable() {
  const { data, loading, error } = useAsync(getDbStats, []);
  const columns = [
    { key: 'name', header: 'Store' },
    { key: 'engine', header: 'Engine' },
    { key: 'count', header: 'Count' },
    { key: 'size_bytes', header: 'Size', render: (s) => formatBytes(s.size_bytes) },
    { key: 'oldest', header: 'Oldest', render: (s) => formatDateTime(s.oldest) },
  ];
  return <DataTable columns={columns} rows={data?.stores} loading={loading} error={error} rowKey="name" />;
}

function Backups() {
  const toast = useToast();
  const { data, loading, error, reload } = useAsync(() => listDbBackups({ limit: 10 }), []);
  const [starting, setStarting] = useState(false);
  const running = data?.some((b) => b.status === 'queued' || b.status === 'running');

  // Keep refreshing while a job is in progress.
  useEffect(() => {
    if (!running) return undefined;
    const t = setTimeout(reload, BACKUP_POLL_MS);
    return () => clearTimeout(t);
  }, [data, running, reload]);

  async function start() {
    setStarting(true);
    try {
      const r = await startDbBackup();
      toast(`Backup ${r.job_id} queued`);
      reload();
    } catch (e) {
      toast(`Backup not started: ${e.message}`, 'error');
    } finally {
      setStarting(false);
    }
  }

  const columns = [
    { key: 'job_id', header: 'Job' },
    { key: 'started_at', header: 'Started', render: (b) => formatDateTime(b.started_at) },
    { key: 'finished_at', header: 'Finished', render: (b) => formatDateTime(b.finished_at) },
    { key: 'size_bytes', header: 'Size', render: (b) => formatBytes(b.size_bytes) },
    { key: 'status', header: 'Status', render: (b) => <StatusBadge value={b.status} /> },
    { key: 'detail', header: 'Location / error', render: (b) => b.error || b.s3_prefix || '-' },
  ];

  return (
    <section>
      <div className="section-head">
        <h2>Backups</h2>
        <button className="btn btn-primary" onClick={start} disabled={starting || running}>
          {running ? 'Backup running...' : 'Run backup now'}
        </button>
      </div>
      <DataTable columns={columns} rows={data} loading={loading && !data} error={error} rowKey="job_id" empty="No backups yet" />
    </section>
  );
}

function Retention() {
  const toast = useToast();
  const [collection, setCollection] = useState('alerts');
  const [days, setDays] = useState('90');
  const [dryRun, setDryRun] = useState(true);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);

  const daysNum = Number(days);
  const valid = Number.isInteger(daysNum) && daysNum >= 30;

  async function run() {
    setBusy(true);
    try {
      const r = await runRetention({ collection, older_than_days: daysNum, dry_run: dryRun });
      setResult({ ...r, dry_run: dryRun });
      toast(dryRun ? `${r.candidates} documents would be purged` : `${r.deleted} documents archived and purged`);
    } catch (e) {
      toast(`Retention failed: ${e.message}`, 'error');
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  return (
    <section>
      <h2>Retention</h2>
      <div className="filter-bar">
        <label className="field">
          <span>Collection</span>
          <select value={collection} onChange={(e) => setCollection(e.target.value)}>
            <option value="alerts">alerts</option>
            <option value="device_data_records">device_data_records</option>
          </select>
        </label>
        <label className="field">
          <span>Older than (days, min 30)</span>
          <input type="number" min="30" value={days} onChange={(e) => setDays(e.target.value)} className="input-narrow" />
        </label>
        <label className="check">
          <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
          Dry run (count only)
        </label>
        <div className="filter-actions">
          <button
            className="btn btn-danger"
            disabled={!valid || busy}
            onClick={() => (dryRun ? run() : setConfirming(true))}
          >
            Run
          </button>
        </div>
      </div>
      <p className="muted small">Matching documents are archived to S3 before deletion. Days must be 30 or more.</p>
      {result && (
        <p className="small">
          {result.dry_run
            ? `Dry run: ${result.candidates} documents in ${collection} are older than ${days} days.`
            : `Deleted ${result.deleted} of ${result.candidates}. Archive: ${result.archive_key ?? '-'}`}
        </p>
      )}
      <ConfirmDialog
        open={confirming}
        title="Purge old documents"
        message={`Archive to S3 and permanently delete documents in ${collection} older than ${days} days?`}
        confirmLabel="Purge"
        danger
        busy={busy}
        onConfirm={run}
        onCancel={() => setConfirming(false)}
      />
    </section>
  );
}
