// Which status buttons a role sees on an alert, and whether each is enabled.
// Transitions (DT-C4-3): new -> acknowledged -> resolved, forward only.
//   owner:       sees Ack only (enabled on `new`)
//   cloud_staff: sees Ack and Resolve (Ack on `new`, Resolve on `acknowledged`)
// Buttons the role can never use are hidden; buttons not valid for the current
// status are shown disabled with a reason.
//
// Usage:
//   alertActions(role, alert.status).map((a) =>
//     <button disabled={!a.enabled} title={a.reason}>{a.label}</button>)
import { ALERT_TRANSITIONS } from '../../api/endpoints.js';

const ACTIONS = [
  { status: 'acknowledged', short: 'Ack', label: 'Acknowledge', variant: 'btn-primary', roles: ['owner', 'cloud_staff'], from: 'new' },
  { status: 'resolved', short: 'Resolve', label: 'Resolve', variant: 'btn-success', roles: ['cloud_staff'], from: 'acknowledged' },
];

export function alertActions(role, currentStatus) {
  const allowed = ALERT_TRANSITIONS[role]?.[currentStatus] || [];
  return ACTIONS.filter((a) => a.roles.includes(role)).map((a) => {
    const enabled = allowed.includes(a.status);
    let reason;
    if (!enabled) {
      if (currentStatus === a.status || currentStatus === 'resolved') reason = `Alert is already ${currentStatus}`;
      else reason = `Only a ${a.from} alert can be ${a.status}`;
    }
    return { ...a, enabled, reason };
  });
}
