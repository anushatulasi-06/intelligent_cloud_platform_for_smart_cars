// One function per backend endpoint (Component Design, sections C1-C5).
// C2-C5 call the real backend at
// VITE_API_BASE_URL when VITE_USE_MOCK=false, sending the header role dropdown
// as X-Role / X-User-Id (see http() below). C1, and the other components when
// VITE_USE_MOCK is unset or "true", returns mock data from src/mocks/*.json
// after a 200 ms delay; nothing is fetched. Names, parameters and response shapes
// follow the design document so each body can later be replaced with a real
// HTTP call (e.g. `return http('GET', '/alerts', { params })`) without
// touching the pages.
//
// Errors are thrown as ApiError with the HTTP status the real backend would
// return (404, 409, 422, ...), so pages already handle them.
//
// Edge-only endpoints (POST /device-data upload, /devices/{id}/heartbeat,
// /devices/{id}/commands/{command_id}/ack, /devices/{id}/frame) are called by
// the car client, not the UI, so they have no function here.
//
// Usage:
//   import { listAlerts } from '../api/endpoints.js';
//   const { items, total } = await listAlerts({ status: 'new', page: 1, page_size: 20 });

import alertsJson from '../mocks/alerts.json';
import dashboardJson from '../mocks/dashboard.json';
import subscriptionsJson from '../mocks/subscriptions.json';
import fleetJson from '../mocks/fleet.json';
import devicesJson from '../mocks/devices.json';
import deviceDataJson from '../mocks/deviceData.json';
import configJson from '../mocks/config.json';
import sessionJson from '../mocks/session.json';

const DELAY_MS = 200;

// ---- real backend -----------------------------------------------------------
// VITE_USE_MOCK defaults to true so teammates without a running backend keep the mocks.
export const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false';
export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/$/, '');

// Demo auth (backend app/auth_stub.py): the role from the header dropdown plus a
// seeded user id for that role (backend/app/db/seed.py). Replaced by the C1 JWT later.
export const USER_ID_FOR_ROLE = { owner: 1, iot_staff: 3, cloud_staff: 4 };
let currentRole = 'cloud_staff';
export function setApiRole(role) { currentRole = role; }

// Real HTTP call. Query params that are undefined, null or '' are left out (the
// filter forms use '' for "any"). Errors become ApiError(status, detail).
async function http(method, path, { params, body } = {}) {
  const url = new URL(API_BASE_URL + path);
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v);
  });
  const headers = { 'X-Role': currentRole, 'X-User-Id': String(USER_ID_FOR_ROLE[currentRole] ?? '') };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  let res;
  try {
    res = await fetch(url, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (e) {
    throw new ApiError(0, `Backend not reachable at ${API_BASE_URL} (${e.message})`);
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) throw new ApiError(res.status, (data && data.detail) || res.statusText || 'Request failed');
  return data;
}
const SEVERITIES = ['low', 'medium', 'high', 'critical'];
const SOUND_TYPES = ['emergency', 'in_vehicle', 'human', 'traffic', 'environmental'];

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

// In-memory copy so writes (ack, edit, activate, ...) persist until reload.
const clone = (x) => JSON.parse(JSON.stringify(x));
const db = {
  alerts: clone(alertsJson),
  subscriptions: clone(subscriptionsJson),
  devices: clone(devicesJson.devices),
  commands: clone(devicesJson.commands),
  records: clone(deviceDataJson.records),
  alertTypes: clone(configJson.alert_types),
  models: clone(configJson.models),
  backups: clone(configJson.backups),
  stores: clone(configJson.db_stats.stores),
};

function respond(value) {
  return new Promise((resolve) => setTimeout(() => resolve(clone(value)), DELAY_MS));
}

function fail(status, message) {
  return new Promise((_, reject) => setTimeout(() => reject(new ApiError(status, message)), DELAY_MS));
}

function paginate(items, page = 1, page_size = 20) {
  const start = (page - 1) * page_size;
  return { items: items.slice(start, start + page_size), page, page_size, total: items.length };
}

const nextId = (rows, key = 'id') => Math.max(0, ...rows.map((r) => r[key])) + 1;
const now = () => new Date().toISOString();
const inRange = (iso, from, to) => (!from || iso >= from) && (!to || iso <= to);

/* ------------------------------------------------------------------ */
/* C1 Identity, dashboards and maps                                    */
/* ------------------------------------------------------------------ */

// POST /login -> { access_token, token_type, expires_in, role, user: { id, name, email } }
// Not used by the UI yet (no authentication in this build).
export function login({ email, password }) {
  if (!email || !password) return fail(422, 'Email and password are required');
  const { me } = sessionJson;
  return respond({ access_token: 'mock-token', token_type: 'bearer', expires_in: 3600, role: me.role,
    user: { id: me.id, name: me.name, email } });
}

// GET /me -> { id, name, email, role }
export function getMe() {
  return respond(sessionJson.me);
}

// GET /dashboard/owner -> { cars: [...], counts: { alerts_today, unresolved }, recent_alerts: [alert x5] }
export function getOwnerDashboard() {
  return respond(dashboardJson.owner);
}

// GET /admin/fleet-summary?window_days -> { total_cars, devices, alerts_today, anomalies_today, alerts_by_type, alerts_per_day }
export function getFleetSummary({ window_days = 7 } = {}) {
  if (window_days < 1 || window_days > 90) return fail(422, 'window_days out of range');
  const s = dashboardJson.fleet_summary;
  return respond({ ...s, alerts_per_day: s.alerts_per_day.slice(-window_days) });
}

// GET /admin/reports/alerts?from&to&group_by&car_id -> { group_by, rows: [{ key, count, anomalies, avg_confidence }] }
export function getAlertReport({ from, to, group_by = 'sound_type' } = {}) {
  if (!from || !to) return fail(422, 'from and to are required');
  const days = (Date.parse(to) - Date.parse(from)) / 86400000;
  if (Number.isNaN(days) || days < 0) return fail(422, 'Invalid dates');
  if (days > 90) return fail(422, 'Range over 90 days');
  return respond({ ...dashboardJson.alert_report, group_by });
}

// GET /subscriptions?car_id -> [{ id, car_id, alert_type, enabled, notify_method }]
export function listSubscriptions({ car_id } = {}) {
  return respond(db.subscriptions.filter((s) => !car_id || s.car_id === Number(car_id)));
}

// POST /subscriptions (upsert) -> { id, car_id, alert_type, enabled, notify_method }
export function upsertSubscription({ car_id, alert_type, enabled, notify_method = 'email' }) {
  if (!SOUND_TYPES.includes(alert_type)) return fail(422, 'Invalid alert type');
  if (!['email', 'in_app'].includes(notify_method)) return fail(422, 'Invalid notify method');
  let row = db.subscriptions.find((s) => s.car_id === car_id && s.alert_type === alert_type);
  if (row) Object.assign(row, { enabled, notify_method });
  else {
    row = { id: nextId(db.subscriptions), car_id, alert_type, enabled, notify_method };
    db.subscriptions.push(row);
  }
  return respond(row);
}

// DELETE /subscriptions/{id} -> 204
export function deleteSubscription(id) {
  const i = db.subscriptions.findIndex((s) => s.id === id);
  if (i < 0) return fail(404, 'Subscription not found');
  db.subscriptions.splice(i, 1);
  return respond(null);
}

// GET /health -> { status, version, time }
export function getHealth() {
  return respond(sessionJson.health);
}

// GET /fleet/map?status&sound_type&car_id -> { cars: [{ car_id, name, lat, lon, heading, speed_kmh, position_at, status, open_alerts, top_severity }] }
export function getFleetMap({ status, sound_type, car_id } = {}) {
  const hasOpen = (carId) => db.alerts.some((a) => a.car_id === carId && a.status !== 'resolved' && a.sound_type === sound_type);
  const cars = fleetJson.map.cars.filter((c) =>
    (!status || c.status === status) &&
    (!car_id || c.car_id === Number(car_id)) &&
    (!sound_type || hasOpen(c.car_id)));
  return respond({ cars });
}

// GET /cars/{car_id}/track?from&to&max_points -> { car_id, points: [{ ts, lat, lon, speed_kmh }], alerts: [...] }
export function getCarTrack(car_id, { from, to } = {}) {
  const track = fleetJson.tracks[car_id];
  if (!track) return fail(404, 'Car not found');
  if (from && to && Date.parse(to) - Date.parse(from) > 24 * 3600 * 1000) return fail(422, 'Range over 24 h');
  return respond(track);
}

/* ------------------------------------------------------------------ */
/* C2 IoT Device Manager                                               */
/* ------------------------------------------------------------------ */

// POST /devices -> { device, device_key }
// DT-C2-2 Wi-Fi validation. Returns an error message or null.
function wifiError({ ssid, security, password }) {
  if (!ssid || new TextEncoder().encode(ssid).length > 32) return 'ssid must be 1 to 32 bytes';
  if (!['open', 'wpa2', 'wpa3'].includes(security)) return 'security must be open, wpa2 or wpa3';
  if (security === 'open' && password) return 'password not allowed for open networks';
  if (security !== 'open' && (!password || password.length < 8 || password.length > 63)) return 'password must be 8 to 63 characters';
  return null;
}

export function createDevice({ car_id, name, device_type, wifi }) {
  if (!USE_MOCK) return http('POST', '/devices', { body: { car_id, name, device_type, wifi } });
  if (!car_id || !name || !device_type) return fail(422, 'car_id, name and device_type are required');
  if (!['microphone', 'camera'].includes(device_type)) return fail(422, 'device_type must be microphone or camera');
  if (wifi && wifiError(wifi)) return fail(422, wifiError(wifi));
  if (db.devices.some((d) => d.car_id === Number(car_id) && d.name === name)) return fail(409, 'Duplicate device name');
  const device = { id: nextId(db.devices), car_id: Number(car_id), name, device_type, status: 'registered',
    firmware: null, last_seen: null, wifi: wifi ? { ssid: wifi.ssid, security: wifi.security, ip_mode: wifi.ip_mode } : null };
  db.devices.push(device);
  queueCommand(device.id, 'sync_config');
  return respond({ device, device_key: 'dk_' + Math.random().toString(36).slice(2, 18) });
}

// GET /devices?car_id&status&device_type&q&page&page_size -> { items: [device], page, page_size, total }
export function listDevices({ car_id, status, device_type, q, page = 1, page_size = 20 } = {}) {
  if (!USE_MOCK) return http('GET', '/devices', { params: { car_id, status, device_type, q, page, page_size } });
  const items = db.devices.filter((d) =>
    (!car_id || d.car_id === Number(car_id)) &&
    (!status || d.status === status) &&
    (!device_type || d.device_type === device_type) &&
    (!q || d.name.toLowerCase().includes(q.toLowerCase())));
  return respond(paginate(items, page, page_size));
}

// GET /devices/{id} -> device (Wi-Fi SSID and security, never the password)
export function getDevice(id) {
  if (!USE_MOCK) return http('GET', `/devices/${id}`);
  const d = db.devices.find((x) => x.id === Number(id));
  return d ? respond(d) : fail(404, 'Device not found');
}

// PUT /devices/{id} -> updated device
export function updateDevice(id, { name, device_type, car_id }) {
  if (!USE_MOCK) return http('PUT', `/devices/${id}`, { body: { name, device_type, car_id } });
  const d = db.devices.find((x) => x.id === Number(id));
  if (!d) return fail(404, 'Device not found');
  const targetCar = car_id !== undefined ? Number(car_id) : d.car_id;
  const targetName = name !== undefined ? name : d.name;
  if (db.devices.some((x) => x.id !== d.id && x.car_id === targetCar && x.name === targetName)) return fail(409, 'Duplicate device name');
  Object.assign(d, name !== undefined && { name }, device_type !== undefined && { device_type },
    car_id !== undefined && { car_id: Number(car_id) });
  return respond(d);
}

// DELETE /devices/{id} -> 204
export function deleteDevice(id) {
  if (!USE_MOCK) return http('DELETE', `/devices/${id}`);
  const i = db.devices.findIndex((x) => x.id === Number(id));
  if (i < 0) return fail(404, 'Device not found');
  db.devices.splice(i, 1);
  db.commands = db.commands.filter((c) => c.device_id !== Number(id));
  return respond(null);
}

function queueCommand(device_id, command) {
  const c = { command_id: nextId(db.commands, 'command_id'), device_id, command, status: 'queued', created_at: now(), acked_at: null };
  db.commands.push(c);
  return { command_id: c.command_id, status: 'queued' };
}

// PUT /devices/{id}/wifi -> 202 { command_id, status: "queued" }
export function updateDeviceWifi(id, { ssid, security, password, ip_mode }) {
  if (!USE_MOCK) return http('PUT', `/devices/${id}/wifi`, { body: { ssid, security, password, ip_mode } });
  const d = db.devices.find((x) => x.id === Number(id));
  if (!d) return fail(404, 'Device not found');
  const err = wifiError({ ssid, security, password });
  if (err) return fail(422, err);
  d.wifi = { ssid, security, ip_mode: ip_mode || 'dhcp' };
  d.status = 'configuring';
  return respond(queueCommand(d.id, 'sync_config'));
}

// POST /devices/{id}/commands -> 202 { command_id, status: "queued" }
export const DEVICE_COMMANDS = ['start_capture', 'stop_capture', 'reboot', 'sync_config', 'start_route', 'stop_route', 'simulate_event'];

export function queueDeviceCommand(id, { command, payload }) {
  if (!USE_MOCK) return http('POST', `/devices/${id}/commands`, { body: { command, payload } });
  if (!DEVICE_COMMANDS.includes(command)) return fail(422, 'Unknown command');
  const d = db.devices.find((x) => x.id === Number(id));
  if (!d) return fail(404, 'Device not found');
  if (db.commands.some((c) => c.device_id === d.id && c.command === command && c.status === 'queued')) {
    return fail(409, 'Same command already queued');
  }
  return respond(queueCommand(d.id, command));
}

// GET /devices/{id}/commands?status&limit -> [{ command_id, command, status, created_at, acked_at }]
export function listDeviceCommands(id, { status, limit = 20 } = {}) {
  if (!USE_MOCK) return http('GET', `/devices/${id}/commands`, { params: { status, limit } });
  const rows = db.commands
    .filter((c) => c.device_id === Number(id) && (!status || c.status === status))
    .sort((a, b) => b.created_at.localeCompare(a.created_at))
    .slice(0, limit)
    .map(({ device_id, ...rest }) => rest);
  return respond(rows);
}

// GET /simulator/{car_id}/state -> { car_id, online, position, frame_url, events, last_alert }
export function getSimulatorState(car_id) {
  if (!USE_MOCK) return http('GET', `/simulator/${car_id}/state`);
  const s = devicesJson.simulator[car_id];
  return s ? respond(s) : fail(404, 'Car not found');
}

/* ------------------------------------------------------------------ */
/* C3 Data Management                                                  */
/* ------------------------------------------------------------------ */

// GET /device-data?status&car_id&device_id&from&to&page&page_size -> { items: [record], page, page_size, total }
export function listDeviceData({ status, car_id, device_id, from, to, page = 1, page_size = 20 } = {}) {
  if (!USE_MOCK) return http('GET', '/device-data', { params: { status, car_id, device_id, from, to, page, page_size } });
  const items = db.records.filter((r) =>
    (!status || r.status === status) &&
    (!car_id || r.car_id === Number(car_id)) &&
    (!device_id || r.device_id === Number(device_id)) &&
    inRange(r.received_timestamp, from, to));
  return respond(paginate(items, page, page_size));
}

// GET /device-data/{record_id} -> record
export function getDeviceDataRecord(record_id) {
  if (!USE_MOCK) return http('GET', `/device-data/${encodeURIComponent(record_id)}`);
  const r = db.records.find((x) => x.record_id === record_id);
  return r ? respond(r) : fail(404, 'Record not found');
}

// GET /device-data/{record_id}/audio -> { url, expires_in: 300 }
export function getDeviceDataAudio(record_id) {
  if (!USE_MOCK) return http('GET', `/device-data/${encodeURIComponent(record_id)}/audio`);
  const r = db.records.find((x) => x.record_id === record_id);
  return r ? respond({ url: `https://mock-bucket.s3.amazonaws.com/${r.s3_object_key}?mock=1`, expires_in: 300 })
    : fail(404, 'Record not found');
}

// GET /device-data/stats?window_hours -> { counts, per_hour: [{ hour, count }], avg_latency_s, bytes_stored }
export function getDeviceDataStats({ window_hours = 12 } = {}) {
  if (!USE_MOCK) return http('GET', '/device-data/stats', { params: { window_hours } });
  const s = deviceDataJson.stats;
  return respond({ ...s, per_hour: s.per_hour.slice(-window_hours) });
}

// POST /device-data/{record_id}/retry -> 202 { record_id, status: "pending" }
export function retryDeviceData(record_id) {
  if (!USE_MOCK) return http('POST', `/device-data/${encodeURIComponent(record_id)}/retry`);
  const r = db.records.find((x) => x.record_id === record_id);
  if (!r) return fail(404, 'Record not found');
  if (r.status !== 'failed') return fail(409, 'Record is not failed');
  Object.assign(r, { status: 'pending', error: null, attempts: 0, processed: false });
  return respond({ record_id, status: 'pending' });
}

/* ------------------------------------------------------------------ */
/* C4 Alerts                                                           */
/* ------------------------------------------------------------------ */

const byNewest = (a, b) => b.timestamp.localeCompare(a.timestamp);

function filterAlerts({ car_id, severity, status, sound_type, is_anomaly, from, to } = {}) {
  return db.alerts.filter((a) =>
    (!car_id || a.car_id === Number(car_id)) &&
    (!severity || a.severity === severity) &&
    (!status || a.status === status) &&
    (!sound_type || a.sound_type === sound_type) &&
    (is_anomaly === undefined || is_anomaly === '' || a.is_anomaly === (is_anomaly === true || is_anomaly === 'true')) &&
    inRange(a.timestamp, from, to));
}

// GET /alerts?car_id&severity&status&sound_type&is_anomaly&from&to&page&page_size -> { items: [alert], page, page_size, total }
export function listAlerts({ page = 1, page_size = 20, ...filters } = {}) {
  if (!USE_MOCK) return http('GET', '/alerts', { params: { ...filters, page, page_size } });
  return respond(paginate(filterAlerts(filters).sort(byNewest), page, page_size));
}

// GET /alerts/{id} -> alert (all fields, including probs and audit fields)
export function getAlert(id) {
  if (!USE_MOCK) return http('GET', `/alerts/${encodeURIComponent(id)}`);
  const a = db.alerts.find((x) => x.id === id);
  return a ? respond(a) : fail(404, 'Alert not found');
}

// Allowed status transitions per role (DT-C4-3). Alerts only move forward,
// one step at a time: new -> acknowledged -> resolved. Owners can only
// acknowledge; only cloud staff can resolve. There is no reopen.
export const ALERT_TRANSITIONS = {
  owner: { new: ['acknowledged'] },
  cloud_staff: { new: ['acknowledged'], acknowledged: ['resolved'] },
};

// PUT /alerts/{id} body { status, note } -> updated alert
// `role` is passed only because there is no token in this build; the real
// backend reads the role from the JWT.
export function updateAlert(id, { status, note }, role = 'cloud_staff') {
  if (!USE_MOCK) return http('PUT', `/alerts/${encodeURIComponent(id)}`, { body: { status, note } });
  const a = db.alerts.find((x) => x.id === id);
  if (!a) return fail(404, 'Alert not found');
  if (a.status === status || a.status === 'resolved') return fail(409, `Alert is already ${a.status}`);
  if (!(ALERT_TRANSITIONS[role]?.[a.status] || []).includes(status)) return fail(403, 'Transition not allowed for your role');
  a.status = status;
  if (status === 'acknowledged') Object.assign(a, { acknowledged_by: 201, acknowledged_at: now() });
  if (status === 'resolved') Object.assign(a, { resolved_by: 201, resolved_at: now(), resolution_note: note || null });
  return respond(a);
}

// GET /alerts/summary?from&to&car_id -> { total, by_status, by_severity, by_type }
export function getAlertSummary(filters = {}) {
  if (!USE_MOCK) return http('GET', '/alerts/summary', { params: filters });
  const rows = filterAlerts(filters);
  const count = (key) => rows.reduce((m, a) => ({ ...m, [a[key]]: (m[a[key]] || 0) + 1 }), {});
  return respond({ total: rows.length, by_status: count('status'), by_severity: count('severity'), by_type: count('sound_type') });
}

// GET /alerts/{id}/audio -> { url, expires_in: 300 }
export function alertAudio(id) {
  if (!USE_MOCK) return http('GET', `/alerts/${encodeURIComponent(id)}/audio`);
  const a = db.alerts.find((x) => x.id === id);
  return a ? respond({ url: `https://mock-bucket.s3.amazonaws.com/${a.s3_object_key}?mock=1`, expires_in: 300 })
    : fail(404, 'Alert not found');
}

/* ------------------------------------------------------------------ */
/* C5 Database management and configuration (cloud_staff only)         */
/* ------------------------------------------------------------------ */


// GET /admin/alert-types -> [{ alert_type, default_severity, description, enabled }]
export function listAlertTypes() {
  if (!USE_MOCK) return http('GET', '/admin/alert-types');
  return respond(db.alertTypes);
}

// PUT /admin/alert-types/{alert_type} -> alert type
export function updateAlertType(alert_type, { default_severity, description, enabled }) {
  if (!USE_MOCK) {
    return http('PUT', `/admin/alert-types/${encodeURIComponent(alert_type)}`, { body: { default_severity, description, enabled } });
  }
  const t = db.alertTypes.find((x) => x.alert_type === alert_type);
  if (!t) return fail(404, 'Alert type not found');
  if (default_severity !== undefined && !SEVERITIES.includes(default_severity)) return fail(422, 'Invalid severity');
  Object.assign(t, default_severity !== undefined && { default_severity },
    description !== undefined && { description }, enabled !== undefined && { enabled });
  return respond(t);
}

// GET /admin/models -> [{ id, version_label, trained_date, accuracy, confidence_threshold, is_active }]
export function listModels() {
  if (!USE_MOCK) return http('GET', '/admin/models');
  return respond(db.models);
}

// BR-CF-2: a confidence threshold must be between 0.50 and 0.95.
export const THRESHOLD_MIN = 0.5;
export const THRESHOLD_MAX = 0.95;
export const THRESHOLD_DEFAULT = 0.75;

// PUT /admin/models/{id} body { confidence_threshold } -> model
export function updateModelThreshold(id, { confidence_threshold }) {
  if (!USE_MOCK) return http('PUT', `/admin/models/${id}`, { body: { confidence_threshold: Number(confidence_threshold) } });
  const m = db.models.find((x) => x.id === id);
  if (!m) return fail(404, 'Model not found');
  const v = Number(confidence_threshold);
  if (Number.isNaN(v) || v < THRESHOLD_MIN || v > THRESHOLD_MAX) return fail(422, `Threshold must be between ${THRESHOLD_MIN} and ${THRESHOLD_MAX}`);
  m.confidence_threshold = v;
  return respond(m);
}

// PUT /admin/models/{id}/activate -> model (is_active = true); all others inactive
export function activateModel(id) {
  if (!USE_MOCK) return http('PUT', `/admin/models/${id}/activate`);
  const m = db.models.find((x) => x.id === id);
  if (!m) return fail(404, 'Model not found');
  if (m.is_active) return fail(409, 'Model is already active');     // DT-CF-1 R3 (R4 "weights missing" is server-side only)
  db.models.forEach((x) => { x.is_active = x.id === id; });
  return respond(m);
}

// GET /admin/db/health -> { postgres: {...}, mongodb: {...}, s3: {...}, checked_at }
export function getDbHealth() {
  if (!USE_MOCK) return http('GET', '/admin/db/health');
  return respond({ ...configJson.db_health, checked_at: now() });
}

// GET /admin/db/stats -> { stores: [{ name, engine, count, size_bytes, oldest }] }
export function getDbStats() {
  if (!USE_MOCK) return http('GET', '/admin/db/stats');
  return respond({ stores: db.stores });
}

// Mock jobs go queued -> running (after 1.5 s) -> succeeded (after 4 s).
function settleBackups() {
  db.backups.forEach((b) => {
    if (b.status !== 'succeeded' && b.status !== 'failed' && Date.now() - Date.parse(b.started_at) > 4000) {
      Object.assign(b, { status: 'succeeded', finished_at: now(), size_bytes: 3250000,
        s3_prefix: `backups/${b.started_at.slice(0, 10)}/` });
    } else if (b.status === 'queued' && Date.now() - Date.parse(b.started_at) > 1500) {
      b.status = 'running';
    }
  });
}

// POST /admin/db/backup -> 202 { job_id, status: "queued" }
export function startBackup() {
  if (!USE_MOCK) return http('POST', '/admin/db/backup');
  settleBackups();
  if (db.backups.some((b) => b.status === 'queued' || b.status === 'running')) return fail(409, 'A backup is already running');
  const job_id = `bk-${String(db.backups.length + 1).padStart(4, '0')}`;
  db.backups.unshift({ job_id, status: 'queued', started_at: now(), finished_at: null, size_bytes: null, s3_prefix: null, error: null });
  return respond({ job_id, status: 'queued' });
}

// GET /admin/db/backups?limit -> [{ job_id, status, started_at, finished_at, size_bytes, s3_prefix, error }]
export function listDbBackups({ limit = 10 } = {}) {
  if (!USE_MOCK) return http('GET', '/admin/db/backups', { params: { limit } });
  settleBackups();
  return respond(db.backups.slice(0, limit));
}

export const RETENTION_COLLECTIONS = ['device_data_records', 'car_positions', 'alerts'];
export const RETENTION_MIN_DAYS = 30;

// car_positions has no document-level mock data, only a count and an oldest
// date; candidates are estimated assuming positions are spread evenly over time.
function carPositionCandidates(cutoffMs) {
  const store = db.stores.find((x) => x.name === 'car_positions');
  const oldest = Date.parse(store.oldest);
  if (cutoffMs <= oldest) return 0;
  return Math.floor(store.count * Math.min(1, (cutoffMs - oldest) / (Date.now() - oldest)));
}

// POST /admin/db/retention/run body { collection, older_than_days, dry_run } -> { candidates, deleted, archive_key }
export function runRetention({ collection, older_than_days, dry_run = true }) {
  if (!USE_MOCK) return http('POST', '/admin/db/retention/run', { body: { collection, older_than_days, dry_run } });
  if (!RETENTION_COLLECTIONS.includes(collection)) return fail(422, 'Collection not allowed');
  if (!Number.isInteger(older_than_days) || older_than_days < RETENTION_MIN_DAYS) {
    return fail(422, `older_than_days must be ${RETENTION_MIN_DAYS} or more`);
  }
  const cutoffMs = Date.now() - older_than_days * 86400000;
  const cutoff = new Date(cutoffMs).toISOString();
  let candidates;
  if (collection === 'car_positions') candidates = carPositionCandidates(cutoffMs);
  else {
    const rows = collection === 'alerts' ? db.alerts : db.records;
    const tsKey = collection === 'alerts' ? 'timestamp' : 'received_timestamp';
    candidates = rows.filter((x) => x[tsKey] < cutoff).length;
    if (!dry_run) {
      const keep = rows.filter((x) => x[tsKey] >= cutoff);
      if (collection === 'alerts') db.alerts = keep; else db.records = keep;
    }
  }
  if (dry_run) return respond({ candidates, deleted: 0, archive_key: null });
  const store = db.stores.find((x) => x.name === collection);
  if (store && candidates > 0) {
    store.count -= candidates;
    if (collection === 'car_positions') store.oldest = cutoff;
  }
  const archive_key = `archive/${collection}/${now().slice(0, 10)}.jsonl.gz`;
  return respond({ candidates, deleted: candidates, archive_key });
}
