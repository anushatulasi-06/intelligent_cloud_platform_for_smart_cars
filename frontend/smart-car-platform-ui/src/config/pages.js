// Sidebar pages in display order. `tabs` drives the tab bar (shown only when
// a page has 2+ tabs); the active tab is kept in ?tab=. `frame` is the frame
// id from the component design document.

export const PAGES = [
  {
    key: 'dashboard',
    label: 'Dashboard',
    path: '/dashboard',
    frame: 'F2',
    tabs: [
      { key: 'overview', label: 'Overview', frame: 'F2' },
      { key: 'subscriptions', label: 'Subscriptions', frame: 'F3' },
    ],
  },
  { key: 'map', label: 'Map View', path: '/map', frame: 'F9', tabs: [] },
  { key: 'tracking', label: 'Tracking', path: '/tracking', frame: 'F10', tabs: [] },
  { key: 'alerts', label: 'Alerts', path: '/alerts', frame: 'F12', tabs: [] },
  { key: 'simulator', label: 'Simulator', path: '/simulator', frame: 'F11', tabs: [] },
  {
    key: 'devices',
    label: 'Device Manager',
    path: '/devices',
    frame: 'F4',
    tabs: [
      { key: 'devices', label: 'Devices', frame: 'F4' },
      { key: 'data', label: 'Device data', frame: 'F13' },
    ],
  },
  {
    key: 'config',
    label: 'Config',
    path: '/config',
    frame: 'F8a',
    tabs: [
      { key: 'alert-types', label: 'Alert types', frame: 'F8a' },
      { key: 'models', label: 'Models', frame: 'F8b' },
      { key: 'database', label: 'Database', frame: 'F14' },
    ],
  },
];

export function pageForPath(pathname) {
  return PAGES.find((p) => pathname === p.path || pathname.startsWith(p.path + '/'));
}
