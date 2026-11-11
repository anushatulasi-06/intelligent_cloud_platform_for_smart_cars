import { useEffect, useId, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
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
import { EMPTY_WIFI, wifiErrors, deviceErrors, lastSeenLabel } from './deviceValidation.js';
import './devices.css';

const PAGE_SIZE = 20;
const TYPES = ['microphone', 'camera'];
const COMMANDS = ['start_capture', 'stop_capture', 'reboot', 'sync_config'];
const COMMAND_LABELS = { start_capture: 'Start capture', stop_capture: 'Stop capture', reboot: 'Reboot', sync_config: 'Sync config' };
const FILTERS = [
  { name: 'car_id', label: 'Car ID', type: 'number', min: 1, step: 1 },
  { name: 'status', label: 'Status', type: 'select', options: ['registered', 'online', 'offline', 'configuring'] },
  { name: 'device_type', label: 'Type', type: 'select', options: TYPES },
  { name: 'q', label: 'Search', placeholder: 'Device name' },
];
const EMPTY = Object.fromEntries(FILTERS.map((f) => [f.name, '']));

function filtersFromUrl(params) {
  const filters = { ...EMPTY };
  const carId = Number(params.get('car_id'));
  if (Number.isSafeInteger(carId) && carId > 0) filters.car_id = String(carId);
  const status = params.get('status');
  if (['registered', 'online', 'offline', 'configuring'].includes(status)) filters.status = status;
  const type = params.get('device_type');
  if (TYPES.includes(type)) filters.device_type = type;
  filters.q = (params.get('q') || '').slice(0, 60);
  return filters;
}

function WifiFields({ value, onChange, errors = {} }) {
  const id = useId();
  const set = (key, val) => onChange({ ...value, [key]: val });
  const error = (name) => errors[name] && <span id={`${id}-${name}`} className="field-error">{errors[name]}</span>;
  const props = (name) => ({ 'aria-invalid': !!errors[name], 'aria-describedby': errors[name] ? `${id}-${name}` : undefined });
  return (
    <div className="device-form-grid">
      <label className="field"><span>Wi-Fi SSID</span><input required type="text" autoComplete="off" value={value.ssid} onChange={(e) => set('ssid', e.target.value)} {...props('ssid')} />{error('ssid')}<span className="field-hint">1–32 UTF-8 bytes</span></label>
      <label className="field"><span>Security</span>
        <select value={value.security} onChange={(e) => onChange({ ...value, security: e.target.value, password: '' })} {...props('security')}>
          <option value="open">Open</option><option value="wpa2">WPA2</option><option value="wpa3">WPA3</option>
        </select>{error('security')}
      </label>
      <label className="field"><span>Password</span><input required={value.security !== 'open'} disabled={value.security === 'open'} type="password" autoComplete="new-password" minLength={8} maxLength={63} value={value.password} placeholder={value.security === 'open' ? 'Not required for Open' : 'Enter a new password'} onChange={(e) => set('password', e.target.value)} {...props('password')} />{error('password')}<span className="field-hint">{value.security === 'open' ? 'Open networks have no password.' : '8–63 characters; saved passwords are never shown.'}</span></label>
      <label className="field"><span>IP mode</span><select value={value.ip_mode} onChange={(e) => set('ip_mode', e.target.value)} {...props('ip_mode')}><option value="dhcp">DHCP</option><option value="static">Static</option></select>{error('ip_mode')}</label>
    </div>
  );
}

function DeviceForm({ device, onClose, onSaved }) {
  const isNew = !device;
  const [form, setForm] = useState({ car_id: device?.car_id ?? '', name: device?.name ?? '', device_type: device?.device_type ?? 'microphone' });
  const [wifi, setWifi] = useState({ ...EMPTY_WIFI });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [submitted, setSubmitted] = useState(false);
  const errors = submitted ? deviceErrors(form) : {};
  const wifiValidation = submitted && isNew ? wifiErrors(wifi) : {};
  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));
  async function save(e) {
    e.preventDefault();
    setSubmitted(true);
    if (Object.keys(deviceErrors(form)).length || (isNew && Object.keys(wifiErrors(wifi)).length)) return;
    setBusy(true);
    setError(null);
    try {
      const body = { ...form, name: form.name.trim(), car_id: Number(form.car_id) };
      const result = isNew ? await createDevice({ ...body, wifi }) : await updateDevice(device.id, body);
      onSaved(result);
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }
  return (
    <Modal open title={isNew ? 'Add device' : `Edit ${device.name}`} onClose={busy ? undefined : onClose}>
      <form className="stack" noValidate onSubmit={save}>
        <fieldset className="device-form-section"><legend>Device information</legend><div className="device-form-grid">
          <label className="field"><span>Car ID</span><input required type="number" min={1} step={1} value={form.car_id} onChange={(e) => set('car_id', e.target.value)} aria-invalid={!!errors.car_id} />{errors.car_id && <span className="field-error">{errors.car_id}</span>}</label>
          <label className="field"><span>Type</span><select value={form.device_type} onChange={(e) => set('device_type', e.target.value)}>{TYPES.map((v) => <option key={v} value={v}>{humanize(v)}</option>)}</select></label>
          <label className="field device-field-wide"><span>Name</span><input required type="text" maxLength={60} value={form.name} onChange={(e) => set('name', e.target.value)} aria-invalid={!!errors.name} />{errors.name && <span className="field-error">{errors.name}</span>}</label>
        </div></fieldset>
        {isNew ? <fieldset className="device-form-section"><legend>Wi-Fi configuration</legend><WifiFields value={wifi} onChange={setWifi} errors={wifiValidation} /></fieldset> : <p className="muted small">Use the Wi-Fi action to change connectivity settings and push them to this device.</p>}
        {error && <p role="alert" className="state-error">{error}</p>}
        <div className="action-row"><button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving...' : 'Save'}</button><button type="button" className="btn" disabled={busy} onClick={onClose}>Cancel</button></div>
      </form>
    </Modal>
  );
}

function WifiEditor({ device, onSaved }) {
  const toast = useToast();
  const [wifi, setWifi] = useState({ ...EMPTY_WIFI, ...(device.wifi || {}), password: '' });
  const [busy, setBusy] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState(null);
  async function save(e) {
    e.preventDefault();
    setSubmitted(true);
    if (Object.keys(wifiErrors(wifi)).length) return;
    setBusy(true); setError(null);
    try {
      await updateDeviceWifi(device.id, wifi);
      setWifi((current) => ({ ...current, password: '' }));
      setSubmitted(false);
      toast('Wi-Fi saved; sync config queued');
      onSaved();
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }
  return <form className="device-wifi-form" noValidate onSubmit={save} aria-label="Wi-Fi configuration">
    <h2>Wi-Fi configuration</h2><WifiFields value={wifi} onChange={setWifi} errors={submitted ? wifiErrors(wifi) : {}} />
    <p className="muted small">Save queues sync config. The device applies these settings on its next heartbeat and confirms the result.</p>
    {error && <p className="state-error" role="alert">{error}</p>}
    <button className="btn btn-primary" disabled={busy} type="submit">{busy ? 'Saving...' : 'Save and push to device'}</button>
  </form>;
}

function DeviceDetail({ id, onClose, canEdit, onEdit, refresh, onChanged }) {
  const { role } = useRole();
  const toast = useToast();
  const device = useAsync(() => getDevice(id), [id, role, refresh]);
  const history = useAsync(() => listDeviceCommands(id), [id, role, refresh]);
  const [busy, setBusy] = useState(null);
  usePolling(() => { device.reload(); history.reload(); }, 15000, !device.loading && !history.loading);
  function changed() { device.reload(); history.reload(); onChanged(); }
  async function send(command) {
    setBusy(command);
    try { await queueDeviceCommand(id, { command }); toast(`${humanize(command)} queued`); changed(); }
    catch (e) { toast(e.message, 'error'); }
    finally { setBusy(null); }
  }
  const d = device.data;
  return (
    <div className="stack" aria-label="Device details">
      <div className="device-topbar"><button className="btn" onClick={onClose}>← Back to devices</button><span className="muted small">Status and command history refresh every 15 seconds</span></div>
      {device.loading && !d && <p className="muted">Loading device...</p>}
      {device.error && <p className="state-error" role="alert">{device.error.message}</p>}
      {d && <><div className="device-detail-grid">
        <section className="panel"><div className="section-head"><h2>Device {d.name}</h2>{canEdit && <button className="btn btn-sm" onClick={() => onEdit(d)}>Edit</button>}</div>
          <dl className="kv">
            {[['Car', `Car ${d.car_id}`], ['Type', humanize(d.device_type)], ['Status', <StatusBadge key="status" value={d.status} />], ['Last seen', <span key="seen" title={formatDateTime(d.last_seen)}>{lastSeenLabel(d.last_seen)}</span>], ['Firmware', d.firmware || 'Not reported'], ['Wi-Fi', d.wifi ? `${d.wifi.ssid} · ${d.wifi.security?.toUpperCase() || '-'} · ${d.wifi.ip_mode?.toUpperCase() || '-'}` : 'Not configured']].map(([k, v]) => <div className="kv-row" key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
          </dl><div className="action-row action-row-top"><Link className="btn" to={`/simulator?car_id=${d.car_id}`}>Open simulator</Link></div>
        </section>
        {canEdit ? <section className="panel"><WifiEditor key={d.id} device={d} onSaved={changed} /></section> : <section className="panel"><h2>Wi-Fi configuration</h2><p className="muted">IoT Staff can change this device’s Wi-Fi settings and send control commands.</p></section>}
      </div>
      {canEdit && <section className="panel"><h2>Control</h2><div className="device-controls">{COMMANDS.map((command) => <button key={command} className="btn" disabled={!!busy} onClick={() => send(command)}>{busy === command ? 'Queueing...' : COMMAND_LABELS[command]}</button>)}</div></section>}
      </>}
      <section><h2>Command history</h2><DataTable rows={history.data} loading={history.loading && !history.data} error={history.error} rowKey="command_id" empty="No commands yet" columns={[
        { key: 'command', header: 'Command', render: (r) => humanize(r.command) },
        { key: 'created_at', header: 'Created', render: (r) => formatDateTime(r.created_at) },
        { key: 'status', header: 'Status', render: (r) => <StatusBadge value={r.status} /> },
        { key: 'acked_at', header: 'Acknowledged', render: (r) => formatDateTime(r.acked_at) },
      ]} /></section>
    </div>
  );
}

export default function DevicesTab() {
  const { role } = useRole();
  const canEdit = role === 'iot_staff';
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const selectedId = Number(params.get('device_id'));
  const selected = Number.isSafeInteger(selectedId) && selectedId > 0 ? selectedId : null;
  const filterQuery = JSON.stringify(filtersFromUrl(params));
  const filters = useMemo(() => JSON.parse(filterQuery), [filterQuery]);
  const [page, setPage] = useState(1);
  const [editor, setEditor] = useState(null);
  const [pendingDelete, setPendingDelete] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [newKey, setNewKey] = useState(null);
  const [revision, setRevision] = useState(0);
  const data = useAsync(() => listDevices({ ...filters, page, page_size: PAGE_SIZE }), [filters, page, role]);
  usePolling(data.reload, 15000, !data.loading && !selected);
  useEffect(() => { setPage(1); }, [filterQuery]);
  function applyFilters(values) {
    const next = new URLSearchParams(params);
    for (const field of FILTERS) {
      const value = values[field.name];
      if (value !== '' && value != null) next.set(field.name, value);
      else next.delete(field.name);
    }
    setPage(1);
    setParams(next);
  }
  function select(id) {
    const next = new URLSearchParams(params);
    next.set('tab', 'devices');
    if (id) next.set('device_id', id); else next.delete('device_id');
    setParams(next);
  }
  function changed() { data.reload(); }
  function saved(result) {
    setEditor(null); data.reload(); setRevision((n) => n + 1);
    if (result.device_key) setNewKey(result);
    toast('Device saved');
  }
  async function remove() {
    setDeleting(true);
    try {
      await deleteDevice(pendingDelete.id);
      if (selected === pendingDelete.id) select(null);
      setPendingDelete(null); data.reload(); toast('Device removed');
    } catch (e) { toast(e.message, 'error'); }
    finally { setDeleting(false); }
  }
  return (
    <div className="stack">
      {selected ? <DeviceDetail key={selected} id={selected} refresh={revision} canEdit={canEdit} onClose={() => select(null)} onEdit={(device) => setEditor({ device })} onChanged={changed} /> : <>
        <div className="section-head"><p className="muted small">Devices refresh every 15 seconds. No heartbeat for 90 seconds marks a device offline.</p>{canEdit && <button className="btn btn-primary" onClick={() => setEditor({})}>+ Add Device</button>}</div>
        <FilterBar fields={FILTERS} values={filters} onApply={applyFilters} />
        <DataTable rows={data.data?.items} loading={data.loading && !data.data} error={data.error} empty="No devices match the filters" onRowClick={(d) => select(d.id)} columns={[
          { key: 'name', header: 'Device', render: (d) => <button className="btn btn-sm" onClick={(e) => { e.stopPropagation(); select(d.id); }}>{d.name}</button> },
          { key: 'car_id', header: 'Car', render: (d) => `Car ${d.car_id}` },
          { key: 'device_type', header: 'Type', render: (d) => humanize(d.device_type) },
          { key: 'status', header: 'Status', render: (d) => <StatusBadge value={d.status} /> },
          { key: 'last_seen', header: 'Last seen', render: (d) => <span className="device-seen" title={formatDateTime(d.last_seen)}>{lastSeenLabel(d.last_seen)}</span> },
          ...(canEdit ? [{ key: 'actions', header: 'Actions', render: (d) => <span className="cell-inline" onClick={(e) => e.stopPropagation()}><button className="btn btn-sm" onClick={() => setEditor({ device: d })}>Edit</button><button className="btn btn-sm" onClick={() => select(d.id)}>Wi-Fi</button><button className="btn btn-sm btn-danger" onClick={() => setPendingDelete(d)}>Del</button></span> }] : []),
        ]} />
        {data.data && <Pagination page={page} pageSize={PAGE_SIZE} total={data.data.total} onChange={setPage} />}
      </>}
      {editor && <DeviceForm key={editor.device?.id || 'new'} {...editor} onClose={() => setEditor(null)} onSaved={saved} />}
      <ConfirmDialog open={!!pendingDelete} title="Delete device" message={`Remove ${pendingDelete?.name}? All queued commands will also be removed. Audio records are retained.`} confirmLabel="Delete device" danger busy={deleting} onConfirm={remove} onCancel={() => !deleting && setPendingDelete(null)} />
      <Modal open={!!newKey} title="Device registered" onClose={() => setNewKey(null)} footer={<button className="btn btn-primary" onClick={() => setNewKey(null)}>I saved the key</button>}>
        {newKey && <div className="stack"><p>Save this key now. It is shown once and authenticates the edge client.</p><dl className="kv device-key-facts"><div className="kv-row"><dt>Car ID</dt><dd>{newKey.device.car_id}</dd></div><div className="kv-row"><dt>Device ID</dt><dd>{newKey.device.id}</dd></div><div className="kv-row"><dt>Name</dt><dd>{newKey.device.name}</dd></div></dl><code className="device-key">{newKey.device_key}</code><button className="btn" onClick={async () => { try { await navigator.clipboard.writeText(newKey.device_key); toast('Key copied'); } catch { toast('Select and copy the key manually', 'error'); } }}>Copy key</button></div>}
      </Modal>
    </div>
  );
}
