// F8b Config: Models. Version table with active/standby badges; Activate asks
// for confirmation (one active model at a time). Threshold form for the active
// model, 0.50 to 0.95 (BR-CF-2), default 0.75.
import { useEffect, useState } from 'react';
import {
  listModels, updateModelThreshold, activateModel, THRESHOLD_MIN, THRESHOLD_MAX, THRESHOLD_DEFAULT,
} from '../../api/endpoints.js';
import { useAsync } from '../../utils/useAsync.js';
import DataTable from '../../components/DataTable.jsx';
import StatusBadge from '../../components/StatusBadge.jsx';
import ConfirmDialog from '../../components/ConfirmDialog.jsx';
import { useToast } from '../../components/Toast.jsx';

const fmt2 = (x) => Number(x).toFixed(2);

export default function ModelsTab() {
  const toast = useToast();
  const { data, loading, error, reload } = useAsync(listModels, []);
  const [pending, setPending] = useState(null);
  const [busy, setBusy] = useState(false);
  const [threshold, setThreshold] = useState(String(THRESHOLD_DEFAULT));
  const [thresholdError, setThresholdError] = useState(null);
  const [saving, setSaving] = useState(false);

  const active = data?.find((m) => m.is_active);
  // Show the active model's current threshold (0.75 unless changed).
  useEffect(() => {
    setThreshold(String(active ? active.confidence_threshold : THRESHOLD_DEFAULT));
    setThresholdError(null);
  }, [active?.id, active?.confidence_threshold]);

  async function saveThreshold(e) {
    e.preventDefault();
    const t = Number(threshold);
    if (threshold.trim() === '' || Number.isNaN(t) || t < THRESHOLD_MIN || t > THRESHOLD_MAX) {
      setThresholdError(`Threshold must be between ${fmt2(THRESHOLD_MIN)} and ${fmt2(THRESHOLD_MAX)}.`);
      return;
    }
    setThresholdError(null);
    setSaving(true);
    try {
      await updateModelThreshold(active.id, { confidence_threshold: t });
      toast(`Threshold of ${active.version_label} set to ${fmt2(t)}`);
      reload();
    } catch (err) {
      setThresholdError(`${err.status ?? 'Error'}: ${err.message}`);
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
    } catch (err) {
      toast(`Activation failed: ${err.message}`, 'error');
    } finally {
      setBusy(false);
    }
  }

  const columns = [
    { key: 'version_label', header: 'Version' },
    { key: 'trained_date', header: 'Trained' },
    { key: 'accuracy', header: 'Accuracy', render: (m) => fmt2(m.accuracy) },
    { key: 'confidence_threshold', header: 'Threshold', render: (m) => fmt2(m.confidence_threshold) },
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

      <form className="panel" onSubmit={saveThreshold} noValidate aria-label="Threshold of the active model">
        <h2>Threshold of the active model{active ? ` (${active.version_label})` : ''}</h2>
        <div className="action-row">
          <label className="field">
            <span>Confidence threshold ({fmt2(THRESHOLD_MIN)} to {fmt2(THRESHOLD_MAX)})</span>
            <input
              type="number" step="0.01" min={THRESHOLD_MIN} max={THRESHOLD_MAX} className="input-narrow"
              value={threshold}
              disabled={!active}
              aria-invalid={!!thresholdError}
              onChange={(e) => { setThreshold(e.target.value); setThresholdError(null); }}
            />
          </label>
          <button type="submit" className="btn btn-primary" disabled={!active || saving}>
            {saving ? 'Saving...' : 'Save'}
          </button>
        </div>
        {thresholdError && <p className="state-error small">{thresholdError}</p>}
        <p className="muted small">
          Clips whose top confidence is below this value are flagged as anomalies. Only one model is active at a time;
          activating another version takes effect on the next record.
        </p>
      </form>

      <ConfirmDialog
        open={!!pending}
        title="Activate model"
        message={`Only one model is active at a time. Activate ${pending?.version_label}?`}
        confirmLabel="Activate"
        busy={busy}
        onConfirm={confirmActivate}
        onCancel={() => setPending(null)}
      />
    </div>
  );
}
