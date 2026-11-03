// Which sidebar pages each role may open. The sidebar always shows all 7
// pages; pages missing from a role's list are greyed out with a lock.
// UI-only: the backend re-checks every call with require_role.

export const ROLES = ['owner', 'iot_staff', 'cloud_staff'];
export const DEFAULT_ROLE = 'cloud_staff';

export const PAGE_KEYS = ['dashboard', 'map', 'tracking', 'alerts', 'simulator', 'devices', 'config'];

export const ACCESS = {
  owner: ['dashboard', 'map', 'tracking', 'alerts'],
  iot_staff: ['simulator', 'devices'],
  cloud_staff: [...PAGE_KEYS],
};

export function canAccess(role, pageKey) {
  return (ACCESS[role] || []).includes(pageKey);
}
