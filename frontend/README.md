# Frontend – Smart Car Platform UI

Web UI for the Smart Car Intelligent Cloud Platform (CMPE 281, Group 8).
React 18 + Vite 7 + React Router 7, plain JavaScript and plain CSS, no UI library.

> **TL;DR for teammates**
> - The **shell** (sidebar, header, alerts drawer, tabs), **12 shared components** and a
>   API integration for every component are implemented. The real backend is the default.
>   Set `VITE_USE_MOCK=true` only for a UI prototype without a backend.
> - **There is a real login now.** The role comes from the token, not from a dropdown. Sign in with
>   one of the seeded accounts; in mock mode any password works.
> - **Login (F1), Dashboards (F2, F3, F6), Map (F9), Tracking (F10), Alerts (F12, F7)** and
>   **Config (F8a, F8b, F14)** are implemented alongside **Devices (F4/F5), Simulator (F11)** and
>   **Device data (F13)**. See the [individual C2/C3 demo guide](../docs/deliverable3/README.md).
> - Build a page in 4 steps: [How to build your page](#5-how-to-build-your-page).
> - Please read [Rules](#6-rules-please-follow) before opening a PR.
> - Pulled before? Check [What changed recently](#what-changed-recently): the role dropdown is gone.

---

## What changed recently

**2026-11-10 – C2 and C3 implemented and C1 integrated**

F4 includes complete Wi-Fi registration and a one-time device key; F5 provides configuration and
control history; F11 supports the connected local edge or CARLA client; F13 monitors real ingestion,
processing, playback and retries. All C1-C5 endpoints use the backend by default. Set
`VITE_USE_MOCK=true` only for a UI prototype. Follow [the current runbook](../docs/deliverable3/README.md)
for deployment, tests and the individual scenario demo.

**2026-11-08 – C1 finished: login, dashboards, map and tracking** (branch `feature/component-c1`)

⚠️ **The demo Role dropdown is gone.** Open `/login` and sign in; the role comes from the JWT.
`useRole()` still returns `{ role }` exactly as before, so pages need no changes, and it now also
gives `{ user, signedIn, signIn, signOut }`. `setApiRole()` and `USER_ID_FOR_ROLE` were removed.

**Behaviour changes**
- Every request sends `Authorization: Bearer <token>`. A 401 from any call clears the session and
  returns to F1 (BR-1.1).
- All C1-C5 endpoints now use the real backend unless `VITE_USE_MOCK=true` is explicitly selected.
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

Requires Node 22.12+ or Node 24 (tested on Node 24).

Sign in at `/login` with one of the seeded accounts; the role router then opens that role's home
page (owner → F2, iot_staff → F4, cloud_staff → F6). In mock mode any password works, and the
login page lists the accounts while you run `npm run dev`.

| Email | Role | Password (backend seed) |
|---|---|---|
| `owner@autosound.dev` | owner | `owner-dev-pass` |
| `owner2@autosound.dev` | owner | `owner2-dev-pass` |
| `iot@autosound.dev` | iot_staff | `iot-dev-pass` |
| `cloud@autosound.dev` | cloud_staff | `cloud-dev-pass` |

The real backend is the default. Copy `.env.example` to `.env.local` when customizing its URL and
keep `VITE_USE_MOCK=false`. The [current runbook](../docs/deliverable3/README.md) starts the API, stores
and connected edge client. `scripts/demo_data.py --live` remains an optional map-history fixture;
the edge client supplies current positions for the component demo.

---

## Browser component verification

Start the real backend, data stores and connected edge demo using the
[Deliverable 3 runbook](../docs/deliverable3/README.md), then run from `frontend/`:

```bash
npm ci
npx playwright install chromium
UI_BASE_URL=http://localhost:5173 npm run test:components
```

The browser check signs in through JWT login, starts/stops the route, submits all five sound
scenarios, inspects F13, plays the stored audio, follows the generated alert and model administration,
and verifies Cloud Staff has read-only device access. It writes screenshots and a result JSON under
`docs/deliverable3/evidence/ui/`; it requires a running deployment and creates real audio/alert records.
`UI_EVIDENCE_DIR` can select another output directory. The generated scenario tones demonstrate
pipeline execution; model accuracy needs labeled real recordings.

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
| F4 | Device Manager: Devices | `/devices?tab=devices` | Rishi Sheth | ✅ Implemented |
| F5 | Device detail + Wi-Fi | `/devices?tab=devices&device_id=<id>` | Rishi Sheth | ✅ Implemented |
| F11 | Simulator | `/simulator?car_id=1` | Rishi Sheth | ✅ Implemented |
| F13 | Device Manager: Device data | `/devices?tab=data` | Rishi Sheth | ✅ Implemented |
| F12 | Alerts list | `/alerts` | Anusha | ✅ Done |
| F7 | Alert detail | `/alerts/:id` | Anusha | ✅ Done |
| F8a | Config: Alert types | `/config?tab=alert-types` | Anusha | ✅ Done |
| F8b | Config: Models | `/config?tab=models` | Anusha | ✅ Done |
| F14 | Config: Database | `/config?tab=database` | Anusha | ✅ Done |

F5 is selected from a device row or its Wi-Fi button and preserves its selection in the URL.

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

## 4. Component implementation by owner

The sections below map component frames to their UI and API functions. All HTTP functions are in
[`src/api/endpoints.js`](src/api/endpoints.js); prototype data requires `VITE_USE_MOCK=true`.

### Pavan – C1 System Dashboard (F1, F2, F3, F6, F9, F10)

Done. Login and the session live in `src/api/session.js` and `src/config/RoleContext.jsx`; the
pages are in `src/pages/login/`, `src/pages/dashboard/`, `src/pages/map/` and `src/pages/tracking/`.
Two components came out of it that anyone can reuse: `BarChart` (plain bars, no chart library) and
`MapCanvas` (a Leaflet street map with OpenStreetMap tiles; the viewer's browser needs internet).

### Rishi Sheth – C2 IoT Device Manager + C3 Data Management

Implemented against F4, F5, F11 and F13. The [Deliverable 3 runbook](../docs/deliverable3/README.md)
contains the deployment, seven-minute recording script and unit/live verification commands.

- **F4 Devices** (`/devices?tab=devices`): searchable/filterable inventory, status and last seen,
  edit/delete actions, and a registration modal with car, name, type, SSID, security, masked password
  and IP mode. The device key is shown once. IoT Staff can mutate; Cloud Staff can inspect.
- **F5 Detail and Wi-Fi** (`/devices?tab=devices&device_id=<id>`): device facts, inline validation,
  Save and push, capture/reboot/sync controls and newest-first command history. A saved configuration
  queues `sync_config`; a connected edge client delivers the acknowledgement.
- **F11 Simulator** (`/simulator?car_id=1`): current frame/telemetry, route controls, five scenario
  categories, classification and recent event state. It polls every two seconds and labels the
  synthetic local edge mode separately from CARLA.
- **F13 Device data** (`/devices?tab=data`): state cards, twelve-hour intake, status/car/device/date
  search, pagination, record detail, raw audio and Cloud Staff retry for failed records. It refreshes
  every ten seconds and supports manual refresh and audio-link recovery.

The UI calls the actual C2/C3 endpoints. Device keys authenticate the edge-only heartbeat, frame,
acknowledgement and audio upload routes; staff calls use JWTs. A UI prototype cannot demonstrate
real edge execution or backend persistence.

### Shared / anyone
| Task | Notes |
|---|---|
| Backend connection | Implemented in `endpoints.js`; use `VITE_API_BASE_URL`, JWT headers and 401 session expiry. |
| Map library for F9/F10 | Leaflet; see the settled decision below. |
| Charts for F6/F13 | Plain elements; see the settled decision below. |
| Automated tests | Backend tests and `scripts/verify_components.py`; see the C2/C3 runbook for commands. |

---

## 5. How to build your page

**Step 1 – Folder and route.** Put your page in its own folder, `src/pages/<page>/`, like
`pages/alerts/` and `pages/config/`. Use one file per frame or tab, plus any helpers that only that
page uses. For example: `pages/dashboard/`, `pages/map/`, `pages/tracking/`, `pages/devices/`,
`pages/simulator/`.

Existing frames already have routes in [`src/App.jsx`](src/App.jsx). When adding a new frame,
register its component and keep the `guard`:

```jsx
<Route path="/map" element={guard('map', <MapViewPage />)} />
```

For a future sub-route, add it next to its parent. F5 currently uses the `device_id` query on `/devices`:

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
| `MapCanvas` | Leaflet street map (OpenStreetMap tiles): car markers, a route, numbered pins and a playback cursor. |

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

This section describes the explicitly selected `VITE_USE_MOCK=true` UI prototype only. Default
operation uses real API requests and persistent stores.

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
| ~~Map library for F9/F10~~ | Settled: `MapCanvas` uses Leaflet with OpenStreetMap tiles. No API key. The tiles load in the viewer's browser, so the viewer needs internet but the EC2 server does not. |
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
    ├── main.jsx, App.jsx        # router + route table
    ├── api/endpoints.js         # real backend for all C1-C5; optional prototype data
    ├── mocks/*.json             # mock data
    ├── config/
    │   ├── access.js            # role -> pages, ROLE_HOME, ROLE_LABELS
    │   ├── pages.js             # sidebar order, paths, frame ids, tabs (roles, frameByRole)
    │   └── RoleContext.jsx      # useRole()
    ├── layout/                  # AppLayout, Sidebar, Header, AlertsDrawer, RequireAccess, icons, alertEvents
    ├── components/              # shared components
    ├── api/session.js           # token + signed-in user (sessionStorage), SESSION_EXPIRED event
    ├── pages/                   # login/ (F1), dashboard/ (F2, F3, F6), map/ (F9), tracking/ (F10),
    │                            #   alerts/ (F12, F7), config/ (F8a, F8b, F14), devices/ (F4, F5, F13), simulator/ (F11)
    ├── styles/theme.css         # ALL colours (CSS variables)
    ├── styles/app.css           # layout + component styles
    └── utils/                   # format helpers, useAsync, usePolling
```
