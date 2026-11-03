// F8b: model versions (active / standby), activation with confirmation, and the
// confidence threshold of the active model (BR-CF-2: 0.50 to 0.95).
import { useEffect, useState } from 'react';
import {
  listModels, updateModelThreshold, activateModel, THRESHOLD_MIN, THRESHOLD_MAX,
} from '../../api/endpoints.js';
import { useAsync } from '../../utils/useAsync.js';
import { formatPercent } from '../../utils/format.js';
import DataTable from '../../components/DataTable.jsx';
import StatusBadge from '../../components/StatusBadge.jsx';
import ConfirmDialog from '../../components/ConfirmDialog.jsx';
import { useToast } from '../../components/Toast.jsx';

export default function ModelsTab() {
  const toast = useToast();
  const { data, loading, error, reload } = useAsync(listModels, []);
  const [pending, setPending] = useState(null);
  const [busy, setBusy] = useState(false);
  const [threshold, setThreshold] = useState('');
  const [saving, setSaving] = useState(false);

  const active = data?.find((m) => m.is_active);
  useEffect(() => { if (active) setThreshold(String(active.confidence_threshold)); }, [active?.id, active?.confidence_threshold]);

  const t = Number(threshold);
  const thresholdValid = threshold !== '' && t >= THRESHOLD_MIN && t <= THRESHOLD_MAX;

  async function saveThreshold() {
    setSaving(true);
    try {
      await updateModelThreshold(active.id, { confidence_threshold: t });
      toast(`Threshold of ${active.version_label} set to ${t.toFixed(2)}`);
      reload();
    } catch (e) {
      toast(`Save failed: ${e.message}`, 'error');
    } finally {
      setSaving(false);
    }
  }

  async function confirmActivate() {
    setBusy(true);
    try {
      await activateModel(pending.id);
      toast(`${pending.version_label} is now active (takes effect on the next record)`);
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
    { key: 'confidence_threshold', header: 'Threshold', render: (m) => Number(m.confidence_threshold).toFixed(2) },
    { key: 'is_active', header: 'Status', render: (m) => <StatusBadge value={m.is_active ? 'active' : 'standby'} /> },
    {
      key: 'actions', header: '',
      render: (m) => !m.is_active && <button className="btn btn-sm btn-primary" onClick={() => setPending(m)}>Activate</button>,
    },
  ];

  return (
    <div className="stack">
      <section>
        <h2>Model versions (CNN)</h2>
        <DataTable columns={columns} rows={data} loading={loading && !data} error={error} empty="No model versions" />
      </section>

      <section className="panel">
        <h2>Threshold of the active model{active ? ` (${active.version_label})` : ''}</h2>
        <div className="action-row">
          <label className="field">
            <span>Confidence threshold ({THRESHOLD_MIN.toFixed(2)} to {THRESHOLD_MAX.toFixed(2)})</span>
            <input
              type="number" min={THRESHOLD_MIN} max={THRESHOLD_MAX} step="0.01" className="input-narrow"
              value={threshold}
              disabled={!active}
              onChange={(e) => setThreshold(e.target.value)}
            />
          </label>
          <button
            className="btn btn-primary"
            disabled={!active || !thresholdValid || saving || t === Number(active?.confidence_threshold)}
            onClick={saveThreshold}
          >Save</button>
        </div>
        {!thresholdValid && threshold !== '' && (
          <p className="state-error small">Must be between {THRESHOLD_MIN.toFixed(2)} and {THRESHOLD_MAX.toFixed(2)}.</p>
        )}
        <p className="muted small">
          Clips whose top confidence is below this value are flagged as anomalies. Only one model is active at a time;
          activating another version takes effect on the next record.
        </p>
      </section>

      <ConfirmDialog
        open={!!pending}
        title="Activate model"
        message={`Make ${pending?.version_label} the active model? The current model goes to standby. It takes effect on the next record.`}
        confirmLabel="Activate"
        busy={busy}
        onConfirm={confirmActivate}
        onCancel={() => setPending(null)}
      />
    </div>
  );
}
