# Frontend – Smart Car Platform UI

Web UI for the Smart Car Intelligent Cloud Platform (CMPE 281, Group 8).
React 18 + Vite + React Router v6, plain JavaScript and plain CSS, no UI library.

> **TL;DR for teammates**
> - The **shell** (sidebar, header, alerts drawer, tabs, role switching), **10 shared components** and a
>   **mock API** for every endpoint in the Component Design doc are done. You don't need the backend
>   to build your pages.
> - **Alerts (F12, F7)** and **Config (F8a, F8b, F14)** are finished. Every other frame is a
>   placeholder waiting for its owner. Find your frames in [What's left, by owner](#4-whats-left-by-owner).
> - Build a page in 4 steps: [How to build your page](#5-how-to-build-your-page).
> - Please read [Rules](#6-rules-please-follow) before opening a PR.

---

## 1. Run it

```bash
cd frontend
npm install
npm run dev       # http://localhost:5173
npm run build     # must pass before you open a PR
```

Requires Node 18+ (tested on Node 24).

There's no login yet. Use the **Role** dropdown in the header to act as `owner`, `iot_staff` or
`cloud_staff`. Switching role opens that role's home page, like the doc's role router:
owner → F2, iot_staff → F4, cloud_staff → F6.

---

## 2. Status at a glance

| Frame | Page | Route / tab | Owner (design doc) | Status |
|---|---|---|---|---|
| Shell | Header, sidebar, alerts drawer, tabs | all pages | Anusha (built for C1) | ✅ Done |
| F1 | Login | – (not routed yet) | Pavan | ⬜ To do |
| F2 | Owner dashboard | `/dashboard?tab=overview` (owner) | Pavan | ⬜ Placeholder |
| F3 | Subscriptions | `/dashboard?tab=subscriptions` (owner) | Pavan | ⬜ Placeholder |
| F6 | Cloud dashboard | `/dashboard?tab=overview` (cloud staff) | Pavan | ⬜ Placeholder |
| F9 | Map View | `/map` | Pavan | ⬜ Placeholder |
| F10 | Tracking | `/tracking` | Pavan | ⬜ Placeholder |
| F4 | Device Manager: Devices | `/devices?tab=devices` | Rishi | ⬜ Placeholder |
| F5 | Device detail + Wi-Fi | `/devices/:id` (route not added yet) | Rishi | ⬜ To do |
| F11 | Simulator | `/simulator` | Rishi | ⬜ Placeholder |
| F13 | Device Manager: Device data | `/devices?tab=data` | Rishi | ⬜ Placeholder |
| F12 | Alerts list | `/alerts` | Anusha | ✅ Done |
| F7 | Alert detail | `/alerts/:id` | Anusha | ✅ Done |
| F8a | Config: Alert types | `/config?tab=alert-types` | Anusha | ✅ Done |
| F8b | Config: Models | `/config?tab=models` | Anusha | ✅ Done |
| F14 | Config: Database | `/config?tab=database` | Anusha | ✅ Done |

A placeholder page shows only its title and frame id, so you can see where your page will go.

---

## 3. What's already built

### Shell (every page – don't rebuild these)
- **Sidebar:** the same 7 pages for every role. Pages the role can't open are dimmed with a lock.
- **Header:**
  - page title, shown as a breadcrumb on sub-pages, e.g. "Alerts > Alert detail"
  - **Alerts** pill with the unread count, polled every 5 s
  - `<Role>: XXXXX` and the Role dropdown
- **Alerts drawer:**
  - the 5 newest alerts, polled every 5 s, with unread dots
  - **Mark all as seen**, and links to F7 and F12
- **Tab bar:** appears automatically for pages with 2+ tabs. The active tab is in `?tab=`.
- **Access guard:** opening a locked page by URL shows "No access".

### Roles and page access ([`src/config/access.js`](src/config/access.js))
| Role | Pages | Home |
|---|---|---|
| `owner` | Dashboard, Map View, Tracking, Alerts | `/dashboard` (F2) |
| `iot_staff` | Simulator, Device Manager | `/devices` (F4) |
| `cloud_staff` | All 7 | `/dashboard` (F6) |

Tabs can be limited per role in [`src/config/pages.js`](src/config/pages.js). For example,
Subscriptions has `roles: ['owner']`, and Overview shows F2 to owners and F6 to cloud staff via
`frameByRole`. This is UI-only: the backend must still enforce roles with `require_role`.

### Finished pages (use them as examples)
- **F12 Alerts:**
  - filters for car, sound type, severity, status and dates
  - 20 rows per page, "review" tag on anomalies
  - Ack/Resolve per role: owners can only acknowledge; cloud staff can acknowledge and resolve
  - Code: [`AlertsPage.jsx`](src/pages/AlertsPage.jsx)
- **F7 Alert detail:**
  - facts next to the class-probability bars, then the audio player (link auto-refreshes before it expires)
  - note with Acknowledge/Resolve, and **Show on map** (opens `/map?car_id=<id>`)
  - Code: [`AlertDetailPage.jsx`](src/pages/AlertDetailPage.jsx)
- **F8a Alert types:** inline edit of severity, description and enabled flag.
- **F8b Models:** activate with confirm; threshold of the active model limited to 0.50–0.95.
- **F14 Database:** health cards (30 s refresh), stores table, backups, retention (dry run by default,
  Run disabled under 30 days, confirm before a real run).
- Code for the Config tabs: [`ConfigPage.jsx`](src/pages/ConfigPage.jsx) and
  [`pages/config/`](src/pages/config/).

---

## 4. What's left, by owner

Each frame lists what the design doc's GUI template asks for, and which mock API functions are
**already written** for it. All functions are in [`src/api/endpoints.js`](src/api/endpoints.js).

### Pavan – C1 System Dashboard (F1, F2, F3, F6, F9, F10)

**F1 Login** (the shared login and auth work is also C1's job)
- Email + password inputs with errors under each field, and a **Sign In** button.
- On success, store the token and open F2/F4/F6 by role (`ROLE_HOME` in `access.js`).
- Use one generic error message for wrong email or password (BR-1.2).
- When any API call returns 401, go back to F1 (token expired, BR-1.1).
- Add the **Logout** button to the header (shown in every doc template).
- Replace the demo Role dropdown with the role from the token. You could keep the dropdown behind
  a dev flag for demos.
- API: `login()`, `getMe()`.

**F2 Owner dashboard** (`/dashboard`, overview tab, owner)
- KPI cards: cars online, alerts today, unresolved.
- My cars table with a status badge. **On map** opens F9 with the car selected (`/map?car_id=`).
- Recent alerts: the 5 newest. A row opens F7 (`/alerts/:id`); **All alerts** opens `/alerts`.
- API: `getOwnerDashboard()`.

**F3 Subscriptions** (`/dashboard?tab=subscriptions`, owner)
- Selects for the car and the notify method (email / in-app).
- One toggle per alert category for the chosen car. A missing row means disabled.
- **Save** saves the changed toggles and shows a toast.
- API: `listSubscriptions({ car_id })`, `upsertSubscription(...)`, `deleteSubscription(id)`.

**F6 Cloud dashboard** (`/dashboard`, overview tab, cloud staff)
- KPI cards: cars, devices online, alerts today, anomalies.
- Bar charts: alerts by type, and alerts per day for the window.
- Latest alerts table with Ack/Resolve; a row opens F7. Copy the action logic from `AlertsPage.jsx`.
- API: `getFleetSummary({ window_days })`, `listAlerts()`, `updateAlertStatus()`, `getAlertReport()`.

**F9 Map View** (`/map`)
- Filters: status, alert type, car. Auto-refresh every 5 s.
- Markers coloured by status (DT-MV-1): online, alert, urgent (pulsing), offline (grey).
  The mock already returns these four statuses.
- Cars list: click selects and centres the car.
- Selected car panel: speed, last position time, open alerts. **Track car** opens F10,
  **Alerts** opens F12.
- **Read `?car_id=` from the URL:** F7 and F2 link here with it.
- API: `getFleetMap({ status, sound_type, car_id })`.

**F10 Tracking** (`/tracking`)
- Car select, From/To (max 24 h) and **Show**.
- Route map with a start marker, numbered alert markers and the current position.
- Playback bar at 1x–8x.
- Table of alerts on the route; a row or marker opens F7.
- API: `getCarTrack(car_id, { from, to })`, which returns 422 for ranges over 24 h.

### Rishi – C2 IoT Device Manager + C3 Data Management (F4, F5, F11, F13)

**F4 Devices** (`/devices?tab=devices`)
- Search and filters: name, status, type.
- **+ Add Device** opens a modal (`Modal`). The password is masked and never pre-filled.
  After creating, show the device key **once**.
- Device table with a status badge and last seen, refreshed every 15 s.
- Row buttons:
  - **Edit** opens the modal.
  - **Wi-Fi** opens F5.
  - **Del** asks for confirmation (`ConfirmDialog danger`) and says queued commands are removed too.
- Only `iot_staff` may create/edit/delete. `cloud_staff` sees the list read-only (DT-C2-1).
- API: `listDevices()`, `createDevice()`, `updateDevice()`, `deleteDevice()`.
  The mock returns 409 for duplicate names and 422 for bad input.

**F5 Device detail + Wi-Fi** (new route `/devices/:id`; see step 1 of the recipe)
- Device facts: car, type, status, last seen, firmware.
- Wi-Fi form: SSID, security, password, IP mode, with inline validation (DT-C2-2):
  - SSID 1–32 bytes
  - `open` takes no password
  - `wpa2`/`wpa3` need 8–63 characters
- **Save and push** queues `sync_config`; the status becomes `configuring`.
- Control buttons: start capture, stop capture, reboot, sync. Show a 409 when the same command is
  already queued.
- Command history, newest first, with a status badge.
- API: `getDevice()`, `updateDeviceWifi()`, `queueDeviceCommand()`, `listDeviceCommands()`,
  `DEVICE_COMMANDS`.

**F11 Simulator** (`/simulator`)
- Camera frame refreshed every 2 s, with a LIVE tag and a line showing speed, heading and position.
  `frame_url` is `null` in the mock, so show a placeholder box.
- Start / Stop route, and scenario buttons that queue `simulate_event` with a category.
- Last classification (type, confidence, severity) with links to F7 and the map.
- Event log: recent commands and their status.
- Poll every 2 s.
- API: `getSimulatorState(car_id)`, `queueDeviceCommand()`.

**F13 Device data** (`/devices?tab=data`)
- Status cards: pending, processing, processed (24 h), failed. Use `KpiCard` with
  `color="warning" | "sky" | "success" | "danger"`.
- Clips-per-hour bar chart for the last 12 h.
- Filters: status, car, device.
- Records table with a status badge and attempts. A click shows the detail and the audio link.
- **Retry** shows only for `failed` records and only for `cloud_staff`.
- Refresh every 10 s.
- API: `getDeviceDataStats()`, `listDeviceData()`, `getDeviceDataRecord()`,
  `getDeviceDataAudio()`, `retryDeviceData()`.

### Shared / anyone
| Task | Notes |
|---|---|
| Connect to the real backend | Add `src/api/http.js` (base URL from `import.meta.env.VITE_API_URL`, JWT header, 401 → F1). Then swap each mock body in `endpoints.js` for an HTTP call. Keep the function signatures so pages don't change. |
| Map library for F9/F10 | Decide as a team (see open decisions). |
| Charts for F6/F13 | Decide as a team (see open decisions). |
| Automated tests | None in the repo yet. |

---

## 5. How to build your page

**Step 1 – Route.** Your route is already in [`src/App.jsx`](src/App.jsx) and renders
`PlaceholderPage`. Swap in your component and keep the `guard`:

```jsx
<Route path="/map" element={guard('map', <MapViewPage />)} />
```

For a new sub-route such as F5, add it next to the parent route:

```jsx
<Route path="/devices/:id" element={guard('devices', <DeviceDetailPage />)} />
```

For a breadcrumb title, extend the `title` logic in
[`src/layout/AppLayout.jsx`](src/layout/AppLayout.jsx).

**Step 2 – Tabs (if your page has them).** Tabs come from `src/config/pages.js`. Read the active one
with `useActiveTab`:

```jsx
import { PAGES, visibleTabs } from '../config/pages.js';
import { useActiveTab } from '../components/PageTabs.jsx';
import { useRole } from '../config/RoleContext.jsx';

const page = PAGES.find((p) => p.key === 'devices');
const { role } = useRole();
const tab = useActiveTab(visibleTabs(page, role));   // 'devices' | 'data'
```

**Step 3 – Data.** Load data through `endpoints.js` with the `useAsync` hook. Never call `fetch` from a page.

```jsx
const { data, loading, error, reload } = useAsync(() => listDevices(filters), [filters]);
// polling:
useEffect(() => { const t = setInterval(reload, 15000); return () => clearInterval(t); }, [reload]);
```

**Step 4 – UI.** Build the page from the shared components and show results with a toast:

```jsx
<FilterBar fields={...} values={filters} onApply={setFilters} />
<DataTable columns={...} rows={data?.items} loading={loading} error={error} empty="No devices" />
<Pagination page={page} pageSize={20} total={data?.total ?? 0} onChange={setPage} />
const toast = useToast();  toast('Device saved');  toast(`Failed: ${e.message}`, 'error');
```

Put the page header in the same form as the finished pages:

```jsx
<div className="page">
  <div className="page-head"><h1>Devices</h1><span className="frame-id">F4</span></div>
  ...
</div>
```

If your page changes an alert, send `window.dispatchEvent(new Event(ALERTS_CHANGED))`
(from `layout/alertEvents.js`) so the header count updates.

### Shared components ([`src/components/`](src/components/); each file starts with a usage example)
| Component | Use it for |
|---|---|
| `DataTable` | Any table. Handles loading, error and empty states; `onRowClick` for navigation. |
| `Pagination` | Paged lists (`page`, `pageSize`, `total`, `onChange`). |
| `FilterBar` | Filters with Apply / Reset (select, text, date). |
| `StatusBadge` | Severity, alert, device, job and record statuses. Colour is picked from the value. |
| `SoundType` | A sound category with its coloured dot. |
| `KpiCard` | KPI cards (`label`, `value`, `hint`, `color`, `icon`). |
| `Modal` | Add/edit forms. |
| `ConfirmDialog` | Delete, purge, activate. Use `danger` for destructive actions. |
| `useToast` | Success and error messages. |
| `PageTabs` / `useActiveTab` | Tabs (the layout renders the bar; you only read the active tab). |

Icons: `<Icon name="..." />` from `src/layout/icons.jsx`. Helpers: `formatDateTime`,
`formatPercent`, `formatBytes` and `humanize` in `src/utils/format.js`.

---

## 6. Rules (please follow)

1. **Colours only from [`src/styles/theme.css`](src/styles/theme.css).** Write
   `var(--primary)` instead of hex values. If you need a new colour, add a variable there.
2. **All data goes through `src/api/endpoints.js`.** If your page needs an endpoint that isn't there,
   add a function with the doc's name, parameters and response shape, plus mock data in `src/mocks/`.
3. **Use the shared components** before writing new ones. If you write something reusable, put it in
   `src/components/` with a usage comment at the top.
4. **Follow the design doc** for your frames: the GUI template, the business rules (BR-x) and the
   decision tables (DT-x). The mock API already applies many of these rules, so show its 409/422
   messages instead of re-checking everything in the page.
5. **Hide actions the role can't use.** Use `useRole()` and `canAccess()`; see how `AlertsPage`
   shows Ack/Resolve.
6. **Run `npm run build` before you push**, and click through your page as each of the three roles.

---

## 7. Mock API details

- Every function returns data from `src/mocks/*.json` after a **200 ms** delay. Writes (create,
  update, ack, …) stay in memory until you reload the page.
- Errors are thrown as `ApiError` with the HTTP status the backend would return, so
  `catch (e) { e.status, e.message }` works the same now and later.
- **Mock data:**
  - 4 cars, 8 devices, 46 alerts, 52 device-data records, 3 model versions, 3 backups
  - Field names follow the doc's data tables, e.g. S3 keys `audio/{car}/{yyyy}/{mm}/{dd}/{record_id}.wav`
    and 24-character hex alert ids
- **Edge-only endpoints** have no UI function: `POST /device-data`, `/devices/{id}/heartbeat`,
  `/devices/{id}/commands/{command_id}/ack`, `/devices/{id}/frame`.

---

## 8. Open decisions for the team

| Question | Context |
|---|---|
| Map library for F9/F10 | Leaflet + OpenStreetMap tiles (free, no key) is the usual choice. Not installed yet. |
| Charts for F6/F13 | Simple bar charts. Hand-written SVG avoids a dependency; a library like Recharts is faster to build with. |
| Alerts tab bar | The doc lists "List, Detail" tabs for Alerts; the current UI uses a breadcrumb instead. |
| Badge colours | The doc says teal/gold/coral; we use the agreed palette in `theme.css`. |
| Wordmark position | The doc puts it in the header; it's currently at the top of the sidebar. |
| Design doc inconsistencies | `PUT /admin/models/{id}/activate` returns the model (API table) or `{ active }` (pseudocode). "Weights missing" is 409 (API table) or 422 (DT-CF-1). The mock follows the API table for the response shape. |

---

## 9. Project structure

```
frontend/
├── index.html, vite.config.js, package.json
└── src/
    ├── main.jsx, App.jsx        # router + route table (swap placeholders here)
    ├── api/endpoints.js         # one function per backend endpoint (mocked)
    ├── mocks/*.json             # mock data
    ├── config/
    │   ├── access.js            # role -> pages, ROLE_HOME, ROLE_LABELS
    │   ├── pages.js             # sidebar order, paths, frame ids, tabs (roles, frameByRole)
    │   └── RoleContext.jsx      # useRole()
    ├── layout/                  # AppLayout, Sidebar, Header, AlertsDrawer, RequireAccess, icons, alertEvents
    ├── components/              # shared components
    ├── pages/                   # AlertsPage, AlertDetailPage, ConfigPage + config/ tabs, PlaceholderPage
    ├── styles/theme.css         # ALL colours (CSS variables)
    ├── styles/app.css           # layout + component styles
    └── utils/                   # format helpers, useAsync
```
