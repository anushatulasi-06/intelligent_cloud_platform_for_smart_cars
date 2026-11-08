# Frontend – Smart Car Platform UI

Web UI for the Smart Car Intelligent Cloud Platform (CMPE 281, Group 8).
React 18 + Vite + React Router v6, plain JavaScript and plain CSS, no UI library.

> **TL;DR for teammates**
> - The **shell** (sidebar, header, alerts drawer, tabs), **12 shared components** and a
>   **mock API** for every endpoint in the Component Design doc are done. You don't need the backend
>   to build your pages.
> - **There is a real login now.** The role comes from the token, not from a dropdown. Sign in with
>   one of the seeded accounts; in mock mode any password works.
> - **Login (F1), Dashboards (F2, F3, F6), Map (F9), Tracking (F10), Alerts (F12, F7)** and
>   **Config (F8a, F8b, F14)** are finished. The Device Manager and Simulator frames are still
>   placeholders. Find your frames in [What's left, by owner](#4-whats-left-by-owner).
> - Build a page in 4 steps: [How to build your page](#5-how-to-build-your-page).
> - Please read [Rules](#6-rules-please-follow) before opening a PR.
> - Pulled before? Check [What changed recently](#what-changed-recently): the role dropdown is gone.

---

## What changed recently

**2026-11-08 – C1 finished: login, dashboards, map and tracking** (branch `feature/component-c1`)

⚠️ **The demo Role dropdown is gone.** Open `/login` and sign in; the role comes from the JWT.
`useRole()` still returns `{ role }` exactly as before, so pages need no changes, and it now also
gives `{ user, signedIn, signIn, signOut }`. `setApiRole()` and `USER_ID_FOR_ROLE` were removed.

**Behaviour changes**
- Every request sends `Authorization: Bearer <token>`. A 401 from any call clears the session and
  returns to F1 (BR-1.1).
- With `VITE_USE_MOCK=false`, the C1 endpoints now call the real backend too. C2 and C3 still use
  mock data, because those endpoints are not implemented on the backend yet.
- The backend no longer accepts `X-Role` / `X-User-Id`, so pull the backend as well.

**New files**
- `src/api/session.js` (token storage, `SESSION_EXPIRED` event)
- `src/pages/login/`, `src/pages/dashboard/`, `src/pages/map/`, `src/pages/tracking/`
- `src/components/BarChart.jsx` and `src/components/MapCanvas.jsx`


**2026-11-03 – Alerts and Config pages finished to spec** (branch `feature/component-c4c5`)

⚠️ **Renamed API functions.** If you call any of these, update your code:

| Old name | New name |
|---|---|
| `updateAlertStatus(id, body, role)` | `updateAlert(id, { status, note }, role)` |
| `getAlertAudio(id)` | `alertAudio(id)` |
| `startDbBackup()` | `startBackup()` |

**Behaviour changes**
- **Alert status changes are strictly step by step:** new → acknowledged → resolved. Cloud staff
  can no longer resolve a `new` alert directly; the mock returns 403 if a step is skipped. The
  button rules are shared in `src/pages/alerts/alertActions.js`, so reuse it on F6.
- **F8a** edits an alert type in a form below the table instead of in the row.
- **F8b** validates the threshold when you click Save and shows an error outside 0.50–0.95.
  The confirm text reads "Only one model is active at a time. Activate <version>?".
- **F14** refreshes with the new `usePolling` hook. Health cards show text plus latency.
  Retention can now purge `car_positions` too.
- **Drawer** items show "new - click to open F7".

**Files moved**
- `src/pages/AlertsPage.jsx` and `AlertDetailPage.jsx` → `src/pages/alerts/`
- `src/pages/ConfigPage.jsx` → `src/pages/config/`

**New files**
- `src/pages/alerts/alertActions.js` (which Ack/Resolve buttons a role sees)
- `src/utils/usePolling.js` (repeat a refresh every N ms)

**Mock data regenerated**
- 25 alerts across 4 cars with exactly 3 anomalies
- models: the single real trained model, cnn-v1 (test accuracy 0.839, threshold 0.75, active)
- alert-type descriptions list 3 example sounds each
- device-data stats are now counted from the records

Alert and record ids changed, so update any id you hard-coded while testing.

**2026-11-02 – Skeleton**
- App shell, shared components, mock API for every endpoint, colourful theme, role routing.
- Then aligned with the Component Design doc, and the app moved from `frontend/smart-car-platform-ui/`
  into `frontend/`.

---

## 1. Run it

```bash
cd frontend
npm install
npm run dev       # http://localhost:5173
npm run build     # must pass before you open a PR
```

Requires Node 18+ (tested on Node 24).

Sign in at `/login` with one of the seeded accounts; the role router then opens that role's home
page (owner → F2, iot_staff → F4, cloud_staff → F6). In mock mode any password works, and the
login page lists the accounts while you run `npm run dev`.

| Email | Role | Password (backend seed) |
|---|---|---|
| `owner@autosound.dev` | owner | `owner-dev-pass` |
| `owner2@autosound.dev` | owner | `owner2-dev-pass` |
| `iot@autosound.dev` | iot_staff | `iot-dev-pass` |
| `cloud@autosound.dev` | cloud_staff | `cloud-dev-pass` |

To run against the real backend, copy `.env.example` to `.env.local` (`VITE_USE_MOCK=false`), start
the API, and run `python scripts/demo_data.py --live` from the repo root so the map has positions.

---

## 2. Status at a glance

| Frame | Page | Route / tab | Owner (design doc) | Status |
|---|---|---|---|---|
| Shell | Header, sidebar, alerts drawer, tabs | all pages | Anusha (built for C1) | ✅ Done |
| F1 | Login | `/login` | Pavan | ✅ Done |
| F2 | Owner dashboard | `/dashboard?tab=overview` (owner) | Pavan | ✅ Done |
| F3 | Subscriptions | `/dashboard?tab=subscriptions` (owner) | Pavan | ✅ Done |
| F6 | Cloud dashboard | `/dashboard?tab=overview` (cloud staff) | Pavan | ✅ Done |
| F9 | Map View | `/map` | Pavan | ✅ Done |
| F10 | Tracking | `/tracking` | Pavan | ✅ Done |
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
  - the signed-in user's role and name, and a **Log out** button
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
  - Ack/Resolve: alerts move one step at a time, new → acknowledged → resolved. Owners see only
    Ack; only cloud staff see Resolve. Buttons not valid for the alert's status are disabled with a
    tooltip. The rules live in [`alertActions.js`](src/pages/alerts/alertActions.js).
  - Code: [`AlertsPage.jsx`](src/pages/alerts/AlertsPage.jsx)
- **F7 Alert detail:**
  - facts card (car/device, sound type, severity, confidence, anomaly, time, position) next to the
    class-probability bars (emergency, traffic, in_vehicle, human, environmental)
  - audio player using `alertAudio(id)`; the link auto-refreshes before it expires
  - status and notes card: status badge, optional note, Acknowledge/Resolve, and
    **Show on map** (links to `/map?car_id=<id>`). Success shows a toast; a 403/409 shows an error line.
  - Code: [`AlertDetailPage.jsx`](src/pages/alerts/AlertDetailPage.jsx)
- **Alerts drawer:** the 5 newest alerts, each with "new - click to open F7", and **Mark all as seen**.
- **Config** (`cloud_staff` only). Code: [`pages/config/`](src/pages/config/), one file per tab.
  - **F8a Alert types:** table of the five categories. **Edit** opens a form below the table
    (severity, description, enabled toggle) that saves with `updateAlertType()`. Changes apply to
    new alerts only.
  - **F8b Models:** version table with active/standby badges. **Activate** asks "Only one model is
    active at a time. Activate <version>?" first (shown on standby versions; the mock has only the active one). The threshold form for the active model (default 0.75)
    shows an error outside 0.50–0.95.
  - **F14 Database:** refreshes every 30 s with `usePolling`.
    - health cards for PostgreSQL, MongoDB and Amazon S3 (Healthy/Degraded/Down plus latency)
    - stores table
    - **Run backup now** (`startBackup()`): the new job row moves queued → running → succeeded
    - retention: `device_data_records`, `car_positions` or `alerts`; minimum 30 days; dry run on by
      default and shows "N candidates"; a real run asks for confirmation, then shows
      "Deleted N, archived to S3".

---

## 4. What's left, by owner

Each frame lists what the design doc's GUI template asks for, and which mock API functions are
**already written** for it. All functions are in [`src/api/endpoints.js`](src/api/endpoints.js).

### Pavan – C1 System Dashboard (F1, F2, F3, F6, F9, F10)

Done. Login and the session live in `src/api/session.js` and `src/config/RoleContext.jsx`; the
pages are in `src/pages/login/`, `src/pages/dashboard/`, `src/pages/map/` and `src/pages/tracking/`.
Two components came out of it that anyone can reuse: `BarChart` (plain bars, no chart library) and
`MapCanvas` (an SVG map of latitude and longitude, no tile server and no dependency).

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

**Step 1 – Folder and route.** Put your page in its own folder, `src/pages/<page>/`, like
`pages/alerts/` and `pages/config/`. Use one file per frame or tab, plus any helpers that only that
page uses. For example: `pages/dashboard/`, `pages/map/`, `pages/tracking/`, `pages/devices/`,
`pages/simulator/`.

Your route is already in [`src/App.jsx`](src/App.jsx) and renders `PlaceholderPage`. Swap in your
component and keep the `guard`:

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
// from src/pages/devices/DevicesPage.jsx
import { PAGES, visibleTabs } from '../../config/pages.js';
import { useActiveTab } from '../../components/PageTabs.jsx';
import { useRole } from '../../config/RoleContext.jsx';

const page = PAGES.find((p) => p.key === 'devices');
const { role } = useRole();
const tab = useActiveTab(visibleTabs(page, role));   // 'devices' | 'data'
```

See [`pages/config/ConfigPage.jsx`](src/pages/config/ConfigPage.jsx) for a complete tabbed page.

**Step 3 – Data.** Load data through `endpoints.js` with the `useAsync` hook. Never call `fetch` from a page.

```jsx
const { data, loading, error, reload } = useAsync(() => listDevices(filters), [filters]);
// polling (src/utils/usePolling.js):
usePolling(reload, 15000);              // every 15 s
usePolling(reload, 2000, jobRunning);   // only while a condition holds
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
| `BarChart` | Simple bar charts (`data={[{ label, value }]}`), no chart library. |
| `MapCanvas` | SVG map from latitude and longitude: car markers, a route, numbered pins and a playback cursor. |

**On/off toggle** (a CSS pattern rather than a component; useful for F3 subscription toggles):

```jsx
<label className="toggle">
  <input type="checkbox" role="switch" checked={on} onChange={(e) => setOn(e.target.checked)} />
  <span className="toggle-track" aria-hidden="true" />
  Enabled
</label>
```

Icons: `<Icon name="..." />` from `src/layout/icons.jsx`. Helpers: `formatDateTime`,
`formatPercent`, `formatBytes` and `humanize` in `src/utils/format.js`. Hooks: `useAsync` and
`usePolling` in `src/utils/`.

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
5. **Hide actions the role can't use.** Use `useRole()` and `canAccess()`; see how `alertActions.js`
   decides which Ack/Resolve buttons to show.
6. **Run `npm run build` before you push**, and click through your page as each of the three roles.

---

## 7. Mock API details

- Every function returns data from `src/mocks/*.json` after a **200 ms** delay. Writes (create,
  update, ack, …) stay in memory until you reload the page.
- Errors are thrown as `ApiError` with the HTTP status the backend would return, so
  `catch (e) { e.status, e.message }` works the same now and later.
- **Constants exported for pages:**
  - `ALERT_TRANSITIONS` (who may move an alert to which status)
  - `THRESHOLD_MIN` / `THRESHOLD_MAX` / `THRESHOLD_DEFAULT` (0.50 / 0.95 / 0.75)
  - `RETENTION_COLLECTIONS` and `RETENTION_MIN_DAYS` (30)
  - `DEVICE_COMMANDS`
- **Mock data:**
  - 4 cars, 8 devices, 25 alerts (3 anomalies, all four severities, all three statuses), 31 device-data
    records, 1 model version (cnn-v1, the real trained model: accuracy 0.839, active), 3 backups
  - Alert types: one row per category (5), each describing 3 example sounds. Default severities follow
    the doc: emergency and in_vehicle high, human medium, traffic and environmental low.
  - The 3 oldest emergency alerts are `critical`. They were "created before" the default changed, and
    alerts keep their original severity (BR-CF-3). This puts all four severities in the list for
    filtering.
  - `car_positions` has only a count and an oldest date, so retention estimates its candidates.
    Alerts and records are all recent, so they show 0 candidates at 30+ days.
  - Field names follow the doc's data tables, e.g. S3 keys `audio/{car}/{yyyy}/{mm}/{dd}/{record_id}.wav`
    and 24-character hex alert ids
- **Edge-only endpoints** have no UI function: `POST /device-data`, `/devices/{id}/heartbeat`,
  `/devices/{id}/commands/{command_id}/ack`, `/devices/{id}/frame`.

---

## 8. Open decisions for the team

| Question | Context |
|---|---|
| ~~Map library for F9/F10~~ | Settled: `MapCanvas` draws the map as an SVG from latitude and longitude. No dependency and no tile server, so it still works on EC2 without outbound internet. Swap in Leaflet later if real streets are wanted. |
| ~~Charts for F6/F13~~ | Settled: `BarChart`, plain elements, no library. |
| Alerts tab bar | The doc lists "List, Detail" tabs for Alerts; the current UI uses a breadcrumb instead. |
| Badge colours | The doc says teal/gold/coral; we use the agreed palette in `theme.css`. |
| Wordmark position | The doc puts it in the header; it's currently at the top of the sidebar. |
| Alert types: "3 per category" | Implemented as 5 rows (one per category), each describing 3 example sounds, matching the doc's `alert_type_config` (five rows) and the CNN's 5 classes. 15 separate sub-type rows would change the schema, subscriptions and alerts, so agree on that first if needed. |
| Design doc inconsistencies | `PUT /admin/models/{id}/activate` returns the model (API table) or `{ active }` (pseudocode). "Weights missing" is 409 (API table) or 422 (DT-CF-1). The mock follows the API table for the response shape. |

---

## 9. Project structure

```
frontend/
├── index.html, vite.config.js, package.json
└── src/
    ├── main.jsx, App.jsx        # router + route table (swap placeholders here)
    ├── api/endpoints.js         # one function per backend endpoint (real for C1/C4/C5, mock for C2/C3)
    ├── mocks/*.json             # mock data
    ├── config/
    │   ├── access.js            # role -> pages, ROLE_HOME, ROLE_LABELS
    │   ├── pages.js             # sidebar order, paths, frame ids, tabs (roles, frameByRole)
    │   └── RoleContext.jsx      # useRole()
    ├── layout/                  # AppLayout, Sidebar, Header, AlertsDrawer, RequireAccess, icons, alertEvents
    ├── components/              # shared components
    ├── api/session.js           # token + signed-in user (sessionStorage), SESSION_EXPIRED event
    ├── pages/                   # login/ (F1), dashboard/ (F2, F3, F6), map/ (F9), tracking/ (F10),
    │                            #   alerts/ (F12, F7), config/ (F8a, F8b, F14), PlaceholderPage
    ├── styles/theme.css         # ALL colours (CSS variables)
    ├── styles/app.css           # layout + component styles
    └── utils/                   # format helpers, useAsync, usePolling
```
