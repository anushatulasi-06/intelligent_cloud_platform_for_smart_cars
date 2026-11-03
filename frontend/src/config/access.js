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

// Home page per role (RoleRouter.route in C1): owner -> F2, iot_staff -> F4, cloud_staff -> F6.
export const ROLE_HOME = {
  owner: '/dashboard',
  iot_staff: '/devices',
  cloud_staff: '/dashboard',
};

export const ROLE_LABELS = {
  owner: 'Owner',
  iot_staff: 'IoT Staff',
  cloud_staff: 'Cloud Staff',
};

export function canAccess(role, pageKey) {
  return (ACCESS[role] || []).includes(pageKey);
}
