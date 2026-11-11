// Shared field rules from DT-C2-2. SSIDs are limited by UTF-8 bytes, not characters.
export const EMPTY_WIFI = { ssid: '', security: 'wpa2', password: '', ip_mode: 'dhcp' };

export function wifiErrors(wifi) {
  const errors = {};
  const bytes = new TextEncoder().encode(wifi.ssid).length;
  if (bytes < 1 || bytes > 32) errors.ssid = 'SSID must be 1 to 32 UTF-8 bytes.';
  if (!['open', 'wpa2', 'wpa3'].includes(wifi.security)) errors.security = 'Choose Open, WPA2 or WPA3.';
  if (wifi.security === 'open' && wifi.password) errors.password = 'Open networks cannot have a password.';
  if (wifi.security !== 'open' && (wifi.password.length < 8 || wifi.password.length > 63)) {
    errors.password = 'Password must contain 8 to 63 characters.';
  }
  if (!['dhcp', 'static'].includes(wifi.ip_mode)) errors.ip_mode = 'Choose DHCP or Static.';
  return errors;
}

export function deviceErrors(form) {
  const errors = {};
  if (!Number.isSafeInteger(Number(form.car_id)) || Number(form.car_id) < 1) errors.car_id = 'Enter an existing car ID greater than zero.';
  if (!form.name.trim() || form.name.trim().length > 60) errors.name = 'Name must contain 1 to 60 characters.';
  if (!['microphone', 'camera'].includes(form.device_type)) errors.device_type = 'Choose Microphone or Camera.';
  return errors;
}

export function lastSeenLabel(iso) {
  if (!iso) return 'Not yet seen';
  const age = Math.max(0, Math.floor((Date.now() - Date.parse(iso)) / 1000));
  if (!Number.isFinite(age)) return 'Not yet seen';
  if (age < 60) return `${age} s ago`;
  if (age < 3600) return `${Math.floor(age / 60)} min ago`;
  if (age < 86400) return `${Math.floor(age / 3600)} h ago`;
  return `${Math.floor(age / 86400)} d ago`;
}
