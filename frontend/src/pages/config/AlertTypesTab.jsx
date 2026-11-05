// F8a Config: Alert types. Table of the five categories; Edit opens an inline
// form below the table (severity, description, enabled). Save -> updateAlertType.
// BR-CF-3: a change affects new alerts only.
import { useState } from 'react';
import { listAlertTypes, updateAlertType } from '../../api/endpoints.js';
import { useAsync } from '../../utils/useAsync.js';
import { humanize } from '../../utils/format.js';
import DataTable from '../../components/DataTable.jsx';
import StatusBadge from '../../components/StatusBadge.jsx';
import SoundType from '../../components/SoundType.jsx';
import { useToast } from '../../components/Toast.jsx';

const SEVERITIES = ['low', 'medium', 'high', 'critical'];

export default function AlertTypesTab() {
  const toast = useToast();
  const { data, loading, error, reload } = useAsync(listAlertTypes, []);
  const [form, setForm] = useState(null); // { alert_type, default_severity, description, enabled }
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState(null);

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const open = (row) => { setForm({ ...row }); setFormError(null); };

  async function save(e) {
    e.preventDefault();
    if (!form.description.trim()) { setFormError('Description is required.'); return; }
    setSaving(true);
    setFormError(null);
    try {
      const { alert_type, ...body } = form;
      await updateAlertType(alert_type, { ...body, description: body.description.trim() });
      toast(`${humanize(alert_type)} saved. Applies to new alerts only.`);
      setForm(null);
      reload();
    } catch (err) {
      setFormError(`${err.status ?? 'Error'}: ${err.message}`);
    } finally {
      setSaving(false);
    }
  }

  const columns = [
    { key: 'alert_type', header: 'Alert type', render: (r) => <SoundType value={r.alert_type} /> },
    { key: 'default_severity', header: 'Default severity', render: (r) => <StatusBadge value={r.default_severity} /> },
    { key: 'description', header: 'Description' },
    { key: 'enabled', header: 'Enabled', render: (r) => <StatusBadge value={r.enabled ? 'enabled' : 'disabled'} label={r.enabled ? 'on' : 'off'} /> },
    {
      key: 'actions', header: '',
      render: (r) => (
        <button className="btn btn-sm" disabled={form?.alert_type === r.alert_type} onClick={() => open(r)}>Edit</button>
      ),
    },
  ];

  return (
    <div className="stack">
      <section>
        <h2>Alert types</h2>
        <DataTable columns={columns} rows={data} loading={loading && !data} error={error} rowKey="alert_type" empty="No alert types" />
      </section>

      {form && (
        <form className="panel" onSubmit={save} aria-label="Edit alert type">
          <h2>Edit alert type: <SoundType value={form.alert_type} /></h2>
          <div className="action-row">
            <label className="field">
              <span>Severity</span>
              <select value={form.default_severity} onChange={(e) => set('default_severity', e.target.value)}>
                {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </label>
            <label className="field grow">
              <span>Description</span>
              <input type="text" maxLength={120} value={form.description} onChange={(e) => set('description', e.target.value)} />
            </label>
            <label className="toggle">
              <input type="checkbox" role="switch" checked={form.enabled} onChange={(e) => set('enabled', e.target.checked)} />
              <span className="toggle-track" aria-hidden="true" />
              Enabled
            </label>
            <div className="filter-actions">
              <button type="submit" className="btn btn-primary" disabled={saving}>{saving ? 'Saving...' : 'Save'}</button>
              <button type="button" className="btn" disabled={saving} onClick={() => setForm(null)}>Cancel</button>
            </div>
          </div>
          {formError && <p className="state-error small">{formError}</p>}
          <p className="muted small">Changes apply to new alerts only; existing alerts keep their severity.</p>
        </form>
      )}
    </div>
  );
}
