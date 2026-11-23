# Frontend – Smart Car Platform UI

Web UI for the Smart Car Intelligent Cloud Platform (CMPE 281, Group 8): every page of all five
components, built with React 18, Vite 7 and React Router 7, in plain JavaScript and plain CSS with
no UI library.

| Component | Pages (frames) | Folder | Who opens it |
|---|---|---|---|
| **C1** System Dashboard | Login (F1), Owner dashboard (F2), Subscriptions (F3), Fleet dashboard (F6), Map View (F9), Tracking (F10) | `pages/login/`, `pages/dashboard/`, `pages/map/`, `pages/tracking/` | everyone; F3 owners only, F6 cloud staff only |
| **C2** IoT Device Manager | Devices (F4), Device detail and Wi-Fi (F5), Simulator (F11) | `pages/devices/`, `pages/simulator/` | IoT staff (edit), cloud staff (read) |
| **C3** Data Management | Device data (F13) | `pages/devices/DeviceDataTab.jsx` | IoT staff, cloud staff (retry) |
| **C4** Alert Tracking | Alerts list (F12), Alert detail (F7), Alerts drawer | `pages/alerts/`, `layout/AlertsDrawer.jsx` | owners (own cars), cloud staff |
| **C5** Database and Configuration | Alert types (F8a), Models (F8b), Database (F14) | `pages/config/` | cloud staff |

---

## 1. Run it

Requires **Node 20.19+ or 22.12+** (Vite 7); tested on Node 24.

```bash
cd frontend
npm install
cp .env.example .env.local      # VITE_USE_MOCK=false, VITE_API_BASE_URL=http://localhost:8000
npm run dev                     # http://localhost:5173
npm run build                   # must pass before you open a PR
```

The UI talks to the **real backend** by default. To start the backend, data stores and an edge
client, see [`infra/README.md`](../infra/README.md). Set `VITE_USE_MOCK=true` only for a UI
prototype with no backend: it uses the data in `src/mocks/`, and nothing is saved.

Sign in at `/login`. The role comes from the login token, and each role lands on its home page:
owner → F2, IoT staff → F4, cloud staff → F6.

| Email | Role | Password |
|---|---|---|
| `owner@autosound.dev` | owner (cars 1–3) | `owner-dev-pass` |
| `owner2@autosound.dev` | owner (car 4) | `owner2-dev-pass` |
| `iot@autosound.dev` | iot_staff | `iot-dev-pass` |
| `cloud@autosound.dev` | cloud_staff | `cloud-dev-pass` |

For data on every page (car positions, live Simulator, records, seeded alert audio), see
[`infra/README.md` → Demo data](../infra/README.md#demo-data).

---

## 2. Pages by component

### C1 – System Dashboard
- **F1 Login** (`/login`): email and password, JWT session.
  - The error message never says which of the two was wrong (BR-1.2).
  - A 401 from any later call clears the session and returns here (BR-1.1).
- **F2 Owner dashboard** (`/dashboard`, owner): counters for *My cars online*, *Alerts today* and
  *Unresolved*, the owner's cars, and their five newest alerts. Auto-refreshes.
- **F3 Subscriptions** (`/dashboard?tab=subscriptions`, owner): per car, toggle each of the five
  categories, and choose **Email** or **In app**. No row means no notification (BR-1.4).
- **F6 Fleet dashboard** (`/dashboard`, cloud staff):
  - counters for *Total cars*, *Devices online*, *Alerts today* and *Anomalies today*
  - bar charts for *Alerts by type* and *Alerts per day*
  - the latest alerts, with the same Ack/Resolve rules as F12
- **F9 Map View** (`/map`): one marker per car on an OpenStreetMap map, coloured by DT-MV-1: grey
  offline (last position over 90 s old), teal online, gold open alert, coral urgent. Refreshes every
  5 s, and `?car_id=` (linked from F2 and F7) selects a car.
- **F10 Tracking** (`/tracking`): pick a car and a time range (up to 24 h) to replay its route,
  with numbered alert pins and a playback cursor.

### C2 – IoT Device Manager
- **F4 Devices** (`/devices?tab=devices`):
  - searchable, filterable inventory with status and last seen
  - edit and delete
  - a registration form: car, name, type, SSID, security, masked password, IP mode
  - **The device key is shown once.** IoT staff can make changes; cloud staff can only look.
- **F5 Device detail and Wi-Fi** (`/devices?tab=devices&device_id=<id>`):
  - device facts and inline validation
  - **Save and push**, which queues `sync_config`
  - capture, reboot and sync controls
  - command history, newest first

  The selection is kept in the URL.
- **F11 Simulator** (`/simulator?car_id=1`):
  - live camera frame and telemetry
  - **Start/Stop route**
  - the five sound scenarios, with their classification and recent events

  It polls every 2 s and labels its source: *Local edge demo*, *CARLA* or *Edge client*. It shows
  "Waiting for an edge client" when no device is sending heartbeats.

### C3 – Data Management
- **F13 Device data** (`/devices?tab=data`):
  - state cards and a 12-hour intake chart
  - search by status, car, device and date, with pagination
  - record detail with the raw audio
  - **Retry** for failed records (cloud staff)

  It refreshes every 10 s, and a manual refresh is available. Expired audio links recover
  automatically.

### C4 – Alert Tracking
- **F12 Alerts** (`/alerts`):
  - filters for car, sound type, severity, status and dates
  - 20 rows per page, with a **review** tag on anomalies
  - **Ack/Resolve**, one step at a time: new → acknowledged → resolved. Owners see only Ack;
    cloud staff see both. Buttons that don't apply are disabled with a tooltip. The rules are in
    [`alertActions.js`](src/pages/alerts/alertActions.js), shared with F6.
- **F7 Alert detail** (`/alerts/:id`):
  - a facts card: car and device, sound type, severity, confidence, anomaly, time, position
  - bars for the five class probabilities
  - an audio player, whose link refreshes before it expires
  - status and note, with Acknowledge/Resolve
  - **Show on map**, which opens F9 with `?car_id=`
- **Alerts drawer** (header): every `new` alert, newest first. It's polled every 5 s, with unread
  dots, **Mark all as seen**, and links to F7 and F12. The header pill shows the unread count.

### C5 – Database and Configuration
All three tabs are for cloud staff only; the code is in [`pages/config/`](src/pages/config/), one
file per tab.
- **F8a Alert types** (`/config?tab=alert-types`): the five categories. **Edit** opens a form for
  severity, description and an enabled toggle. Changes apply to new alerts only.
- **F8b Models** (`/config?tab=models`):
  - versions with active and standby badges
  - **Activate**, which confirms first: "Only one model is active at a time. Activate <version>?"
  - the active model's threshold, with an error outside 0.50–0.95
- **F14 Database** (`/config?tab=database`), refreshed every 30 s:
  - **Health:** cards for PostgreSQL, MongoDB and S3, each with status and latency, plus a stores
    table.
  - **Backups:** **Run backup now**; the job moves queued → running → succeeded.
  - **Retention:** for `device_data_records`, `car_positions` or `alerts`, minimum 30 days. Dry
    run is on by default and shows "N candidates"; a real run asks first, then shows "Deleted N,
    archived to S3".

---

## 3. Shell, roles and access

- **Sidebar:** the same 7 pages for every role. Pages the role can't open are dimmed with a lock,
  and opening one by URL shows "No access".
- **Header:**
  - the page title, as a breadcrumb on sub-pages (e.g. "Alerts > Alert detail")
  - the **Alerts** pill with the unread count
  - the user's role and name, and **Log out**
- **Tab bar:** shown for pages with 2+ visible tabs. The active tab is kept in `?tab=`.

| Role | Pages | Home |
|---|---|---|
| `owner` | Dashboard (F2, F3), Map View, Tracking, Alerts | `/dashboard` (F2) |
| `iot_staff` | Simulator, Device Manager (F4, F5, F13) | `/devices` (F4) |
| `cloud_staff` | All 7 (Dashboard shows F6) | `/dashboard` (F6) |

- Page access is defined in [`src/config/access.js`](src/config/access.js).
- Tabs and per-role frames (`roles`, `frameByRole`) are defined in
  [`src/config/pages.js`](src/config/pages.js).
- This is UI-only. The backend enforces every role itself.

---

## 4. API layer

All HTTP calls go through [`src/api/endpoints.js`](src/api/endpoints.js):
- each call sends `Authorization: Bearer <token>`
- the session is kept in [`src/api/session.js`](src/api/session.js)
- errors are thrown as `ApiError`, with `e.status` and `e.message`

| Component | Functions |
|---|---|
| C1 | `login`, `getMe`, `getOwnerDashboard`, `getFleetSummary`, `getAlertReport`, `listSubscriptions`, `upsertSubscription`, `deleteSubscription`, `getFleetMap`, `getCarTrack` |
| C2 | `listDevices`, `getDevice`, `createDevice`, `updateDevice`, `deleteDevice`, `updateDeviceWifi`, `queueDeviceCommand`, `listDeviceCommands`, `getSimulatorCars`, `getSimulatorState`, `getSimulatorScenarioAudio` |
| C3 | `listDeviceData`, `getDeviceDataRecord`, `getDeviceDataAudio`, `getDeviceDataStats`, `retryDeviceData` |
| C4 | `listAlerts`, `getAlert`, `updateAlert`, `getAlertSummary`, `alertAudio` |
| C5 | `listAlertTypes`, `updateAlertType`, `listModels`, `updateModelThreshold`, `activateModel`, `getDbHealth`, `getDbStats`, `startBackup`, `listDbBackups`, `runRetention`, `getHealth` |

The edge-only endpoints (`POST /device-data`, device heartbeat, frame and command ack) have no UI
function; the edge clients call them. Endpoint details are in the
[backend README](../backend/README.md#api-by-component).

---

## 5. Project structure

```
frontend/
├── index.html, vite.config.js, package.json
├── Dockerfile, nginx.conf        # production image: static build + /api proxy (local Docker stack, AWS option B)
└── src/
    ├── main.jsx, App.jsx         # router and route table
    ├── api/endpoints.js          # every backend call, C1–C5 (plus mock mode)
    ├── api/session.js            # token and signed-in user (sessionStorage), SESSION_EXPIRED event
    ├── mocks/*.json              # mock-mode data
    ├── config/                   # access.js (role → pages), pages.js (sidebar, tabs), RoleContext.jsx (useRole)
    ├── layout/                   # AppLayout, Sidebar, Header, AlertsDrawer, RequireAccess, icons, alertEvents
    ├── components/               # shared components
    ├── pages/
    │   ├── login/                # C1 F1
    │   ├── dashboard/            # C1 F2, F3, F6
    │   ├── map/, tracking/       # C1 F9, F10
    │   ├── devices/              # C2 F4, F5 · C3 F13
    │   ├── simulator/            # C2 F11
    │   ├── alerts/               # C4 F12, F7
    │   └── config/               # C5 F8a, F8b, F14
    ├── styles/theme.css          # ALL colours (CSS variables)
    ├── styles/app.css            # layout and component styles
    └── utils/                    # format helpers, useAsync, usePolling
```
