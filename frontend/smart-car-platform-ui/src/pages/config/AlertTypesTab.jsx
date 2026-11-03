// F8a: default severity, description and enabled flag per alert type, edited inline.
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
  const [editing, setEditing] = useState(null); // { alert_type, default_severity, description, enabled }
  const [saving, setSaving] = useState(false);

  const isEditing = (row) => editing?.alert_type === row.alert_type;
  const set = (k, v) => setEditing((e) => ({ ...e, [k]: v }));

  async function save() {
    setSaving(true);
    try {
      const { alert_type, ...body } = editing;
      await updateAlertType(alert_type, body);
      toast(`${humanize(alert_type)} saved. Applies to new alerts only.`);
      setEditing(null);
      reload();
    } catch (e) {
      toast(`Save failed: ${e.message}`, 'error');
    } finally {
      setSaving(false);
    }
  }

  const columns = [
    { key: 'alert_type', header: 'Alert type', render: (r) => <SoundType value={r.alert_type} /> },
    {
      key: 'default_severity', header: 'Default severity',
      render: (r) => isEditing(r) ? (
        <select value={editing.default_severity} onChange={(e) => set('default_severity', e.target.value)}>
          {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      ) : <StatusBadge value={r.default_severity} />,
    },
    {
      key: 'description', header: 'Description',
      render: (r) => isEditing(r) ? (
        <input type="text" className="grow" value={editing.description} onChange={(e) => set('description', e.target.value)} />
      ) : r.description,
    },
    {
      key: 'enabled', header: 'Enabled',
      render: (r) => isEditing(r) ? (
        <label className="check">
          <input type="checkbox" checked={editing.enabled} onChange={(e) => set('enabled', e.target.checked)} />
          {editing.enabled ? 'enabled' : 'disabled'}
        </label>
      ) : <StatusBadge value={r.enabled ? 'enabled' : 'disabled'} />,
    },
    {
      key: 'actions', header: '',
      render: (r) => isEditing(r) ? (
        <span className="cell-inline">
          <button className="btn btn-sm btn-primary" disabled={saving} onClick={save}>Save</button>
          <button className="btn btn-sm" disabled={saving} onClick={() => setEditing(null)}>Cancel</button>
        </span>
      ) : (
        <button className="btn btn-sm" disabled={!!editing} onClick={() => setEditing({ ...r })}>Edit</button>
      ),
    },
  ];

  return (
    <DataTable columns={columns} rows={data} loading={loading} error={error} rowKey="alert_type" empty="No alert types" />
  );
}
