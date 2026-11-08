import { useState } from 'react';
import { Link } from 'react-router-dom';
import {
  createDevice, listDevices, getDevice, updateDevice, deleteDevice,
  updateDeviceWifi, queueDeviceCommand, listDeviceCommands,
} from '../../api/endpoints.js';
import { useRole } from '../../config/RoleContext.jsx';
import { useAsync } from '../../utils/useAsync.js';
import { usePolling } from '../../utils/usePolling.js';
import { formatDateTime, humanize } from '../../utils/format.js';
import DataTable from '../../components/DataTable.jsx';
import Pagination from '../../components/Pagination.jsx';
import FilterBar from '../../components/FilterBar.jsx';
import StatusBadge from '../../components/StatusBadge.jsx';
import Modal from '../../components/Modal.jsx';
import ConfirmDialog from '../../components/ConfirmDialog.jsx';
import { useToast } from '../../components/Toast.jsx';

const PAGE_SIZE = 20;
const TYPES = ['microphone', 'camera'];
const COMMANDS = ['start_capture', 'stop_capture', 'reboot', 'sync_config'];
const FILTERS = [
  { name: 'car_id', label: 'Car ID', type: 'number' },
  { name: 'status', label: 'Status', type: 'select', options: ['registered', 'online', 'offline', 'configuring'] },
  { name: 'device_type', label: 'Type', type: 'select', options: TYPES },
  { name: 'q', label: 'Search', placeholder: 'Device name' },
];
const EMPTY = Object.fromEntries(FILTERS.map((f) => [f.name, '']));
const WIFI = { ssid: '', security: 'wpa2', password: '', ip_mode: 'dhcp' };

function wifiError(wifi) {
  const bytes = new TextEncoder().encode(wifi.ssid).length;
  if (bytes < 1 || bytes > 32) return 'SSID must be 1 to 32 bytes.';
  if (wifi.security === 'open' && wifi.password) return 'Open networks must have no password.';
  if (wifi.security !== 'open' && (wifi.password.length < 8 || wifi.password.length > 63)) {
    return 'Wi-Fi password must be 8 to 63 characters.';
  }
  return null;
}

function WifiFields({ value, onChange }) {
  const set = (key, val) => onChange({ ...value, [key]: val });
  return (
    <div className="device-form-grid">
      <label className="field"><span>SSID</span><input required type="text" value={value.ssid} onChange={(e) => set('ssid', e.target.value)} /></label>
      <label className="field"><span>Security</span>
        <select value={value.security} onChange={(e) => onChange({ ...value, security: e.target.value, password: '' })}>
          {['open', 'wpa2', 'wpa3'].map((v) => <option key={v} value={v}>{v.toUpperCase()}</option>)}
        </select>
      </label>
      {value.security !== 'open' && <label className="field"><span>New password</span><input required type="password" autoComplete="new-password" minLength={8} maxLength={63} value={value.password} onChange={(e) => set('password', e.target.value)} /></label>}
      <label className="field"><span>IP mode</span><select value={value.ip_mode} onChange={(e) => set('ip_mode', e.target.value)}><option value="dhcp">DHCP</option><option value="static">Static</option></select></label>
    </div>
  );
}

function DeviceForm({ device, mode, onClose, onSaved }) {
  const [form, setForm] = useState({ car_id: device?.car_id ?? '', name: device?.name ?? '', device_type: device?.device_type ?? 'microphone' });
  const [wifi, setWifi] = useState({ ...WIFI, ...(device?.wifi || {}), password: '' });
  const [withWifi, setWithWifi] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const isWifi = mode === 'wifi';
  const isNew = mode === 'create';
  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));
  async function save(e) {
    e.preventDefault();
    const validation = (isWifi || (isNew && withWifi)) && wifiError(wifi);
    if (validation) { setError(validation); return; }
    setBusy(true);
    setError(null);
    try {
      const body = { ...form, name: form.name.trim(), car_id: Number(form.car_id) };
      const result = isWifi ? await updateDeviceWifi(device.id, wifi)
        : isNew ? await createDevice({ ...body, ...(withWifi ? { wifi } : {}) })
          : await updateDevice(device.id, body);
      onSaved(result);
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }
  return (
    <Modal open title={isWifi ? `Wi-Fi · ${device.name}` : isNew ? 'Add device' : `Edit ${device.name}`} onClose={busy ? undefined : onClose}>
      <form className="stack" onSubmit={save}>
        {!isWifi && <div className="device-form-grid">
          <label className="field"><span>Car ID</span><input required type="number" min={1} step={1} value={form.car_id} onChange={(e) => set('car_id', e.target.value)} /></label>
          <label className="field"><span>Name</span><input required type="text" maxLength={60} pattern=".*\S.*" value={form.name} onChange={(e) => set('name', e.target.value)} /></label>
          <label className="field"><span>Type</span><select value={form.device_type} onChange={(e) => set('device_type', e.target.value)}>{TYPES.map((v) => <option key={v} value={v}>{humanize(v)}</option>)}</select></label>
        </div>}
        {isNew && <label className="check"><input type="checkbox" checked={withWifi} onChange={(e) => setWithWifi(e.target.checked)} />Configure Wi-Fi</label>}
        {(isWifi || (isNew && withWifi)) && <WifiFields value={wifi} onChange={setWifi} />}
        {isWifi && <p className="muted small">Saving queues a sync command. Enter the password again for a secured network.</p>}
        {error && <p role="alert" className="state-error">{error}</p>}
        <div className="action-row"><button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving...' : 'Save'}</button><button type="button" className="btn" disabled={busy} onClick={onClose}>Cancel</button></div>
      </form>
    </Modal>
  );
}

function DeviceDetail({ id, onClose, canEdit, onEdit, refresh }) {
  const { role } = useRole();
  const toast = useToast();
  const device = useAsync(() => getDevice(id), [id, role, refresh]);
  const history = useAsync(() => listDeviceCommands(id), [id, role]);
  const [command, setCommand] = useState('start_capture');
  const [busy, setBusy] = useState(false);
  usePolling(() => { device.reload(); history.reload(); }, 10000, !device.loading && !history.loading);
  async function send() {
    setBusy(true);
    try { await queueDeviceCommand(id, { command }); toast('Command queued'); history.reload(); }
    catch (e) { toast(e.message, 'error'); }
    finally { setBusy(false); }
  }
  const d = device.data;
  return (
    <section className="panel stack" aria-label="Device details">
      <div className="section-head"><h2>{d?.name || `Device ${id}`}</h2><button className="btn btn-sm" onClick={onClose}>Close details</button></div>
      {device.error && <p className="state-error">{device.error.message}</p>}
      {d && <>
        <dl className="kv">
          {[['Car', `Car ${d.car_id}`], ['Type', humanize(d.device_type)], ['Status', <StatusBadge key="status" value={d.status} />], ['Firmware', d.firmware || 'Not reported'], ['Last seen', formatDateTime(d.last_seen)], ['Wi-Fi', d.wifi ? `${d.wifi.ssid} · ${d.wifi.security?.toUpperCase() || '-'} · ${d.wifi.ip_mode || '-'}` : 'Not configured']].map(([k, v]) => <div className="kv-row" key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
        </dl>
        <div className="action-row">
          <Link className="btn" to={`/simulator?car_id=${d.car_id}`}>Open simulator</Link>
          {canEdit && <><button className="btn" onClick={() => onEdit(d, 'edit')}>Edit device</button><button className="btn" onClick={() => onEdit(d, 'wifi')}>Change Wi-Fi</button></>}
        </div>
      </>}
      {canEdit && <div className="action-row"><label className="field"><span>Control command</span><select value={command} onChange={(e) => setCommand(e.target.value)}>{COMMANDS.map((v) => <option key={v} value={v}>{humanize(v)}</option>)}</select></label><button className="btn btn-primary" disabled={busy || !d} onClick={send}>{busy ? 'Queueing...' : 'Send command'}</button></div>}
      <div><h2>Command history</h2><DataTable rows={history.data} loading={history.loading && !history.data} error={history.error} rowKey="command_id" empty="No commands yet" columns={[
        { key: 'command', header: 'Command', render: (r) => humanize(r.command) },
        { key: 'status', header: 'Status', render: (r) => <StatusBadge value={r.status} /> },
        { key: 'created_at', header: 'Created', render: (r) => formatDateTime(r.created_at) },
        { key: 'acked_at', header: 'Acknowledged', render: (r) => formatDateTime(r.acked_at) },
      ]} /></div>
    </section>
  );
}

export default function DevicesTab() {
  const { role } = useRole();
  const canEdit = role === 'iot_staff';
  const toast = useToast();
  const [filters, setFilters] = useState(EMPTY);
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState(null);
  const [editor, setEditor] = useState(null);
  const [pendingDelete, setPendingDelete] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [newKey, setNewKey] = useState(null);
  const [revision, setRevision] = useState(0);
  const data = useAsync(() => listDevices({ ...filters, page, page_size: PAGE_SIZE }), [filters, page, role]);
  usePolling(data.reload, 15000, !data.loading);
  function saved(result) {
    const mode = editor.mode;
    setEditor(null);
    data.reload();
    setRevision((n) => n + 1);
    if (result.device_key) setNewKey(result);
    toast(mode === 'wifi' ? 'Wi-Fi sync queued' : 'Device saved');
  }
  async function remove() {
    setDeleting(true);
    try {
      await deleteDevice(pendingDelete.id);
      if (selected === pendingDelete.id) setSelected(null);
      setPendingDelete(null);
      data.reload();
      toast('Device removed');
    } catch (e) { toast(e.message, 'error'); }
    finally { setDeleting(false); }
  }
  return (
    <div className="stack">
      <div className="section-head"><p className="muted small">Devices report their status by heartbeat. A device goes offline after 90 seconds without one.</p>{canEdit && <button className="btn btn-primary" onClick={() => setEditor({ mode: 'create' })}>Add device</button>}</div>
      <FilterBar fields={FILTERS} values={filters} onApply={(v) => { setFilters(v); setPage(1); }} />
      <DataTable rows={data.data?.items} loading={data.loading && !data.data} error={data.error} empty="No devices match the filters" onRowClick={(d) => setSelected(d.id)} columns={[
        { key: 'name', header: 'Device', render: (d) => <button className="btn btn-sm" onClick={(e) => { e.stopPropagation(); setSelected(d.id); }}>{d.name}</button> },
        { key: 'car_id', header: 'Car', render: (d) => `Car ${d.car_id}` },
        { key: 'device_type', header: 'Type', render: (d) => humanize(d.device_type) },
        { key: 'status', header: 'Status', render: (d) => <StatusBadge value={d.status} /> },
        { key: 'last_seen', header: 'Last seen', render: (d) => formatDateTime(d.last_seen) },
        { key: 'wifi', header: 'Wi-Fi', render: (d) => d.wifi?.ssid || 'Not configured' },
        ...(canEdit ? [{ key: 'actions', header: 'Actions', render: (d) => <span className="cell-inline" onClick={(e) => e.stopPropagation()}><button className="btn btn-sm" onClick={() => setEditor({ device: d, mode: 'edit' })}>Edit</button><button className="btn btn-sm btn-danger" onClick={() => setPendingDelete(d)}>Delete</button></span> }] : []),
      ]} />
      {data.data && <Pagination page={page} pageSize={PAGE_SIZE} total={data.data.total} onChange={setPage} />}
      {selected && <DeviceDetail key={selected} id={selected} refresh={revision} canEdit={canEdit} onClose={() => setSelected(null)} onEdit={(device, mode) => setEditor({ device, mode })} />}
      {editor && <DeviceForm key={`${editor.mode}-${editor.device?.id || 'new'}`} {...editor} onClose={() => setEditor(null)} onSaved={saved} />}
      <ConfirmDialog open={!!pendingDelete} title="Delete device" message={`Remove ${pendingDelete?.name} and its commands? Audio records are retained.`} confirmLabel="Delete device" danger busy={deleting} onConfirm={remove} onCancel={() => !deleting && setPendingDelete(null)} />
      <Modal open={!!newKey} title="Device registered" onClose={() => setNewKey(null)} footer={<button className="btn btn-primary" onClick={() => setNewKey(null)}>I saved the key</button>}>
        {newKey && <div className="stack"><p>Save this key for {newKey.device.name}. It is shown only once and authenticates the edge client.</p><code className="device-key">{newKey.device_key}</code><button className="btn" onClick={async () => { try { await navigator.clipboard.writeText(newKey.device_key); toast('Key copied'); } catch { toast('Select and copy the key manually', 'error'); } }}>Copy key</button></div>}
      </Modal>
    </div>
  );
}
