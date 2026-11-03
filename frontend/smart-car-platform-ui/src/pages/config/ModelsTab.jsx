// F8b: model versions, per-model anomaly threshold, and activation (one active at a time).
import { useEffect, useState } from 'react';
import { listModels, updateModelThreshold, activateModel } from '../../api/endpoints.js';
import { useAsync } from '../../utils/useAsync.js';
import { formatPercent } from '../../utils/format.js';
import DataTable from '../../components/DataTable.jsx';
import StatusBadge from '../../components/StatusBadge.jsx';
import ConfirmDialog from '../../components/ConfirmDialog.jsx';
import { useToast } from '../../components/Toast.jsx';

export default function ModelsTab() {
  const toast = useToast();
  const { data, loading, error, reload } = useAsync(listModels, []);
  const [thresholds, setThresholds] = useState({});
  const [pending, setPending] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (data) setThresholds(Object.fromEntries(data.map((m) => [m.id, String(m.confidence_threshold)])));
  }, [data]);

  async function saveThreshold(m) {
    try {
      await updateModelThreshold(m.id, { confidence_threshold: Number(thresholds[m.id]) });
      toast(`Threshold of ${m.version_label} saved`);
      reload();
    } catch (e) {
      toast(`Save failed: ${e.message}`, 'error');
    }
  }

  async function confirmActivate() {
    setBusy(true);
    try {
      await activateModel(pending.id);
      toast(`${pending.version_label} is now active (next record)`);
      setPending(null);
      reload();
    } catch (e) {
      toast(`Activation failed: ${e.message}`, 'error');
    } finally {
      setBusy(false);
    }
  }

  const columns = [
    { key: 'version_label', header: 'Version' },
    { key: 'trained_date', header: 'Trained' },
    { key: 'accuracy', header: 'Accuracy', render: (m) => formatPercent(m.accuracy) },
    {
      key: 'confidence_threshold', header: 'Anomaly threshold',
      render: (m) => (
        <span className="cell-inline">
          <input
            type="number" min="0.01" max="0.99" step="0.01" className="input-narrow"
            value={thresholds[m.id] ?? ''}
            onChange={(e) => setThresholds((t) => ({ ...t, [m.id]: e.target.value }))}
          />
          <button
            className="btn btn-sm"
            disabled={String(m.confidence_threshold) === thresholds[m.id]}
            onClick={() => saveThreshold(m)}
          >Save</button>
        </span>
      ),
    },
    { key: 'is_active', header: 'Status', render: (m) => <StatusBadge value={m.is_active ? 'active' : 'inactive'} /> },
    {
      key: 'actions', header: '',
      render: (m) => !m.is_active && <button className="btn btn-sm" onClick={() => setPending(m)}>Activate</button>,
    },
  ];

  return (
    <>
      <DataTable columns={columns} rows={data} loading={loading} error={error} empty="No model versions" />
      <ConfirmDialog
        open={!!pending}
        title="Activate model"
        message={`Make ${pending?.version_label} the active model? All other versions become inactive. It takes effect for the next record.`}
        confirmLabel="Activate"
        busy={busy}
        onConfirm={confirmActivate}
        onCancel={() => setPending(null)}
      />
    </>
  );
}
