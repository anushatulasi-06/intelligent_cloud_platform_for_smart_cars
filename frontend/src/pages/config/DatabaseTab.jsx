// F14 Config: Database. Health cards, stores table and backups refresh every
// 30 s (usePolling). Run backup now -> startBackup; the new job row moves
// queued -> running -> succeeded. Retention: dry run shows "N candidates";
// a real run asks for confirmation, then shows "deleted N, archived to S3".
import { useState } from 'react';
import {
  getDbHealth, getDbStats, startBackup, listDbBackups, runRetention,
  RETENTION_COLLECTIONS, RETENTION_MIN_DAYS,
} from '../../api/endpoints.js';
import { useAsync } from '../../utils/useAsync.js';
import { usePolling } from '../../utils/usePolling.js';
import { formatBytes, formatDateTime } from '../../utils/format.js';
import DataTable from '../../components/DataTable.jsx';
import KpiCard from '../../components/KpiCard.jsx';
import StatusBadge from '../../components/StatusBadge.jsx';
import ConfirmDialog from '../../components/ConfirmDialog.jsx';
import { useToast } from '../../components/Toast.jsx';
import Icon from '../../layout/icons.jsx';

const REFRESH_MS = 30000;
const JOB_POLL_MS = 1000;   // faster while a backup job is in progress

const HEALTH = {
  healthy: { text: 'Healthy', color: 'success' },
  degraded: { text: 'Degraded', color: 'warning' },
  down: { text: 'Down', color: 'danger' },
};
const ENGINE_SHORT = { postgresql: 'PG', mongodb: 'Mongo', s3: 'S3' };

export default function DatabaseTab() {
  return (
    <div className="stack">
      <HealthCards />
      <StoresTable />
      <div className="two-col">
        <Backups />
        <Retention />
      </div>
    </div>
  );
}

function HealthCards() {
  const { data, error, reload } = useAsync(getDbHealth, []);
  usePolling(reload, REFRESH_MS);

  if (error) return <p className="state-error">Health check failed: {error.message}</p>;
  const card = (label, store, icon) => {
    const h = HEALTH[store?.status] || { text: store?.status ?? '...', color: 'primary' };
    const latency = store?.latency_ms != null ? `${store.latency_ms} ms` : null; // null when the store is down
    return (
      <KpiCard
        label={label}
        color={h.color}
        icon={<Icon name={icon} size={22} />}
        value={<span className="health-value">{h.text}{latency && <span className="health-latency"> {latency}</span>}</span>}
        hint={label === 'PostgreSQL' && store?.pool_size != null ? `pool ${store.pool_in_use}/${store.pool_size}` : undefined}
      />
    );
  };
  return (
    <section>
      <div className="kpi-row">
        {card('PostgreSQL', data?.postgres, 'database')}
        {card('MongoDB', data?.mongodb, 'database')}
        {card('Amazon S3', data?.s3, 'cloud')}
      </div>
      <p className="muted small">Checked {data ? formatDateTime(data.checked_at) : '...'} · refreshes every 30 s</p>
    </section>
  );
}

function StoresTable() {
  const { data, loading, error, reload } = useAsync(getDbStats, []);
  usePolling(reload, REFRESH_MS);
  const columns = [
    { key: 'name', header: 'Store', render: (s) => `${s.name} (${ENGINE_SHORT[s.engine] ?? s.engine})` },
    { key: 'count', header: 'Rows / docs', render: (s) => (s.count == null ? '-' : s.count.toLocaleString()) }, // null = store not reachable (e.g. S3 without AWS)
    { key: 'size_bytes', header: 'Size', render: (s) => formatBytes(s.size_bytes) },
    { key: 'oldest', header: 'Oldest record', render: (s) => formatDateTime(s.oldest) },
  ];
  return (
    <section>
      <h2>Stores</h2>
      <DataTable columns={columns} rows={data?.stores} loading={loading && !data} error={error} rowKey="name" />
    </section>
  );
}

function Backups() {
  const toast = useToast();
  const { data, loading, error, reload } = useAsync(() => listDbBackups({ limit: 10 }), []);
  const [starting, setStarting] = useState(false);
  const running = !!data?.some((b) => b.status === 'queued' || b.status === 'running');
  usePolling(reload, running ? JOB_POLL_MS : REFRESH_MS);

  async function start() {
    setStarting(true);
    try {
      const r = await startBackup();
      toast(`Backup ${r.job_id} queued`);
      reload();
    } catch (e) {
      toast(`Backup not started: ${e.message}`, 'error');
    } finally {
      setStarting(false);
    }
  }

  const columns = [
    { key: 'started_at', header: 'Date', render: (b) => formatDateTime(b.started_at) },
    { key: 'size_bytes', header: 'Size', render: (b) => formatBytes(b.size_bytes) },
    { key: 'status', header: 'Status', render: (b) => <StatusBadge value={b.status} /> },
  ];

  return (
    <section className="panel">
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
  const [collection, setCollection] = useState('device_data_records');
  const [days, setDays] = useState('90');
  const [dryRun, setDryRun] = useState(true);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [runError, setRunError] = useState(null);

  const daysNum = Number(days);
  const valid = Number.isInteger(daysNum) && daysNum >= RETENTION_MIN_DAYS;

  async function run() {
    setBusy(true);
    setRunError(null);
    try {
      const r = await runRetention({ collection, older_than_days: daysNum, dry_run: dryRun });
      setResult({ ...r, dry_run: dryRun });
      if (!dryRun) toast(`Deleted ${r.deleted.toLocaleString()} from ${collection}`);
    } catch (e) {
      setRunError(`${e.status ?? 'Error'}: ${e.message}`);
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  return (
    <section className="panel">
      <h2>Retention (purge old data)</h2>
      <div className="stack-sm">
        <label className="field">
          <span>Collection</span>
          <select value={collection} onChange={(e) => { setCollection(e.target.value); setResult(null); }}>
            {RETENTION_COLLECTIONS.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
        <label className="field">
          <span>Older than (days)</span>
          <input
            type="number" min={RETENTION_MIN_DAYS} step="1" className="input-narrow"
            value={days}
            aria-invalid={!valid}
            onChange={(e) => { setDays(e.target.value); setResult(null); }}
          />
        </label>
        {!valid && <p className="state-error small">Minimum is {RETENTION_MIN_DAYS} days.</p>}
        <label className="toggle">
          <input type="checkbox" role="switch" checked={dryRun} onChange={(e) => { setDryRun(e.target.checked); setResult(null); }} />
          <span className="toggle-track" aria-hidden="true" />
          Dry run (count only)
        </label>
        <div>
          <button
            className="btn btn-danger"
            disabled={!valid || busy}
            onClick={() => (dryRun ? run() : setConfirming(true))}
          >
            {busy ? 'Running...' : 'Run'}
          </button>
        </div>
        {result && (
          <p className="retention-result" role="status">
            {result.dry_run
              ? `${result.candidates.toLocaleString()} candidates`
              : `Deleted ${result.deleted.toLocaleString()}, archived to S3${result.archive_key ? ` (${result.archive_key})` : ''}`}
          </p>
        )}
        {runError && <p className="state-error small">{runError}</p>}
        <p className="muted small">Matching documents are archived to S3 before deletion.</p>
      </div>
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
