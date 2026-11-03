# smart-car-platform-ui

React 18 + Vite + React Router v6 skeleton for the Smart Car Platform (CMPE 281).
Plain CSS, no UI library and no authentication: the role
dropdown in the header switches between `owner`, `iot_staff` and `cloud_staff`
for demos (default `cloud_staff`).

```bash
npm install
npm run dev      # http://localhost:5173
npm run build
```

## Styling
All colours are CSS variables in `src/styles/theme.css`; `src/styles/app.css` and the
components use only those variables (severity/status badges, sound-type dots,
sidebar tiles, KPI colours). Change a colour there and it updates everywhere.

## Layout
- `src/layout/` – header (Alerts drawer button, role dropdown), sidebar (7 pages, locked ones greyed), tab bar, alerts drawer.
- `src/config/access.js` – which role can open which page.
- `src/config/pages.js` – sidebar order, routes, frame ids and tabs (tabs sync to `?tab=`).

| Route | Page | Frame | Status |
|---|---|---|---|
| `/dashboard` | Dashboard (Overview, Subscriptions) | F2 / F3 | placeholder |
| `/map` | Map View | F9 | placeholder |
| `/tracking` | Tracking | F10 | placeholder |
| `/alerts`, `/alerts/:id` | Alerts list / detail | F12 / F7 | implemented |
| `/simulator` | Simulator | F11 | placeholder |
| `/devices` | Device Manager (Devices, Device data) | F4 / F13 | placeholder |
| `/config` | Config (Alert types, Models, Database) | F8a / F8b / F14 | implemented |

## Data
`src/api/endpoints.js` has one function per endpoint in the component design
document (same names for paths, parameters and response shapes). Each returns
data from `src/mocks/*.json` after 200 ms; writes are kept in memory until the
page reloads. To use the real backend, replace a function body with an HTTP call.

## Shared components (`src/components`)
`DataTable`, `Pagination`, `StatusBadge`, `SoundType`, `KpiCard`, `FilterBar`, `Modal`,
`ConfirmDialog`, `Toast` + `useToast`, `PageTabs` + `useActiveTab`. Each file
starts with a usage example.
