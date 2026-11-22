// Where the signed-in user and the access token live.
//
// The token is kept in sessionStorage, so a page reload keeps you signed in but closing the
// tab signs you out. Reads and writes are wrapped because sessionStorage throws in a few
// browser settings; the app then just works without the "stay signed in" part.
//
//   import { getSession, setSession, clearSession } from './session.js';

const KEY = 'smartcar.session';

// Fired when the backend answers 401: the token expired, so the app returns to F1 (BR-1.1).
export const SESSION_EXPIRED = 'auth:expired';

let session = read();

function read() {
  try {
    return JSON.parse(sessionStorage.getItem(KEY)) || null;
  } catch {
    return null;
  }
}

function write(value) {
  try {
    if (value) sessionStorage.setItem(KEY, JSON.stringify(value));
    else sessionStorage.removeItem(KEY);
  } catch {
    // no storage available, the session stays in memory only
  }
}

export function getSession() {
  return session;
}

export function getToken() {
  return session?.token ?? null;
}

export function setSession({ token, user, role }) {
  session = { token, user, role };
  write(session);
  return session;
}

export function clearSession() {
  session = null;
  write(null);
}

export function sessionExpired() {
  if (!session) return;
  clearSession();
  window.dispatchEvent(new Event(SESSION_EXPIRED));
}
