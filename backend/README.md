# Backend – Smart Car Platform API

FastAPI app for the Smart Car Intelligent Cloud Platform (CMPE 281, Group 8). One process serves
the HTTP API for all five components, and it also runs the C3 processing worker and the C2 device
sweeper as background threads.

| Component | What the backend provides | Routers | Services |
|---|---|---|---|
| **C1** System Dashboard | JWT login, owner and fleet dashboards, alert report, subscriptions, map markers, route playback | `auth`, `dashboard`, `subscriptions`, `fleet` | `auth_service`, `dashboard_service`, `subscription_service`, `map_service` |
| **C2** IoT Device Manager | Device inventory, encrypted Wi-Fi, one-time device keys, commands, heartbeats, camera frames, Simulator state | `devices`, `simulator` | `device_service` |
| **C3** Data Management | Audio upload and validation, durable processing queue, processing worker, Device data monitor and retry | `device_data` | `ingest_service`, `processing_worker`, `edge_buffer` |
| **C4** Alert Tracking and ML Orchestrator | CNN classification, anomaly flag, severity, one alert per record, notifications, alerts API | `alerts` | `orchestrator`, `alert_service`, `notification_service`, `app/ml/` |
| **C5** Database Management and Configuration | All data access, alert types, model versions, store health and stats, backups, retention | `admin_config`, `admin_db` | `alert_type_service`, `model_service`, `maintenance_service`, `app/db/` |

---

## Run it

The full local setups are in [`infra/README.md`](../infra/README.md):
- **without Docker** (Homebrew PostgreSQL and MongoDB, MinIO for S3)
- **all in Docker**

For AWS, see [`infra/aws/SETUP.md`](../infra/aws/SETUP.md) (scripts) or
[`infra/aws/docker/SETUP.md`](../infra/aws/docker/SETUP.md) (Docker).

The short version, with PostgreSQL 16 and MongoDB 7 already running:

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate      # Python 3.12+
pip install -r requirements.txt
cp .env.example .env                                     # then set the values below
alembic upgrade head                                     # 8 PostgreSQL tables
python -m app.db.seed                                    # demo accounts and data; safe to rerun
uvicorn app.main:app --reload                            # http://localhost:8000/docs
```

**Check that the model loads:** this reads the active `model_versions` row's files from
`../model/artifacts/cnn-v1`.
```bash
python -c "from app.services.orchestrator import get_orchestrator; m = get_orchestrator().reload_model(); print(m.version_label, m.threshold)"
# expect: cnn-v1 0.75
```

**Tests** need no databases, AWS or CARLA. They run on SQLite, mongomock and stubbed S3/SES:
```bash
python -m pytest
```
The 10 PostgreSQL migration tests are skipped unless `TEST_DATABASE_URL` points at a **throwaway**
database, which the test empties:
```bash
TEST_DATABASE_URL=postgresql+psycopg2://user:pass@localhost:5432/autosound_test python -m pytest
```

**Seeded accounts** (development only): `owner@autosound.dev` (cars 1–3), `owner2@autosound.dev`
(car 4), `iot@autosound.dev` and `cloud@autosound.dev`. The passwords and full list are in
[`infra/README.md`](../infra/README.md#seeded-accounts).

---

## Configuration (`backend/.env`)

All settings are read by `app/config.py`. The template is `.env.example`; the production template
is [`infra/aws/backend.env.production.example`](../infra/aws/backend.env.production.example).

| Setting | Default | Used by | Notes |
|---|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg2://user:pass@localhost:5432/autosound` | C5 | PostgreSQL |
| `MONGO_URI` | `mongodb://localhost:27017/autosound` | C5 | MongoDB |
| `S3_BUCKET`, `AWS_REGION` | `autosound-audio-dev`, `us-west-2` | C2, C3, C4, C5 | Audio, frames, backups, archives |
| `S3_ENDPOINT_URL`, `S3_PUBLIC_ENDPOINT_URL` | empty | C2–C5 | Set for MinIO. Leave empty for AWS S3. The public one is used only for browser links. |
| `JWT_SECRET`, `JWT_TTL_MINUTES` | dev key, `60` | C1 | **Set your own secret anywhere but your laptop.** |
| `WIFI_ENCRYPTION_KEY` | empty | C2 | A Fernet key; required for secured Wi-Fi. Keep it forever. |
| `CORS_ORIGINS` | `http://localhost:5173` | all | Comma-separated UI origins |
| `MODEL_DIR`, `CONFIDENCE_THRESHOLD` | `../model/artifacts`, `0.75` | C4 | The active model's own threshold takes precedence |
| `SES_FROM_EMAIL`, `DEV_FAKE_EMAIL` | `alerts@autosound.dev`, `true` | C4 | `true` only logs emails |
| `DEV_FAKE_BACKUP`, `LOCAL_BACKUP_DIR` | `true`, `.local_backups` | C5 | `true` simulates `pg_dump`/`mongodump` |
| `LOCAL_AUDIO_DIR` | unset | C4 | Dev only: serve seeded alert clips from a folder. Leave empty with MinIO or S3. |
| `PROCESSING_WORKER_ENABLED`, `PROCESSING_WORKER_POLL_SECONDS` | `true`, `2` | C3 | The audio worker thread |
| `DEVICE_SWEEPER_ENABLED` | `true` | C2 | Marks devices offline and expires commands, every 30 s |
| `LOAD_MODEL_ON_STARTUP`, `MONGO_ENSURE_INDEXES_ON_STARTUP`, `FAIL_INTERRUPTED_JOBS_ON_STARTUP` | `true` | C4, C5 | Tests turn them off |

Generate the two secrets:
```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"                                   # JWT_SECRET
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"      # WIFI_ENCRYPTION_KEY
```

S3 credentials come from the standard AWS sources: environment variables, `~/.aws`, or the EC2
instance role. They're **not** read from `.env`.

---

## Authentication and roles

- **`POST /login`** with a seeded account returns a JWT. Send it as `Authorization: Bearer <token>`;
  the **Authorize** button in `/docs` does this for you.
- The token carries the user id and role, so checking it costs no database query. It lasts
  `JWT_TTL_MINUTES`.
- No token, or an expired one, returns **401**. A role that isn't allowed returns **403**.
- Every route except `/health`, `POST /login` and the edge routes uses `require_role` from
  `app.auth`. `app.auth_stub` only re-exports it for older imports.
- **Edge routes** (device heartbeats, frames, command acks, audio upload) authenticate with
  **`X-Device-Key`**, independently of staff logins.
- **Owners see only their own cars.** The owner's car ids go *into* the repository query, never
  into a filter applied afterwards. Another owner's alert or device answers 404, like an unknown id.

| Role | Can use |
|---|---|
| `owner` | C1 owner dashboard, subscriptions, map and track (own cars), C4 alerts (own cars, acknowledge only), own devices (read) |
| `iot_staff` | C2 devices (create, edit, delete, Wi-Fi, commands), Simulator, C3 device data (read) |
| `cloud_staff` | Everything above except device changes: C1 fleet dashboard and report, C4 alerts (acknowledge and resolve), C3 retry, C5 config and database |

---

## API by component

Interactive docs: `http://localhost:8000/docs`. All request and response shapes are in
`app/schemas/`.

### C1 – System Dashboard
| Method and path | Roles | Purpose |
|---|---|---|
| `POST /login` | public | Email and password, returns `{ access_token, expires_in, role, user }` |
| `GET /me` | any signed-in user | The current user |
| `GET /dashboard/owner` | owner | F2: KPIs and recent alerts for the owner's cars |
| `GET /admin/fleet-summary?window_days=7` | cloud_staff | F6: fleet KPIs, alerts by type and day, device status |
| `GET /admin/reports/alerts?from=&to=` | cloud_staff | F6: alert report for a date range |
| `GET /subscriptions` · `POST /subscriptions` · `DELETE /subscriptions/{id}` | owner | F3: per car and category, `email` or `in_app`. No row means no notification (BR-1.4). |
| `GET /fleet/map` | owner, cloud_staff | F9: one marker per car. Its colour follows DT-MV-1: offline if the last position is over 90 s old, otherwise online, alert or urgent depending on open alerts. |
| `GET /cars/{car_id}/track?from=&to=` | owner, cloud_staff | F10: route playback with the alerts along it |

### C2 – IoT Device Manager
| Method and path | Roles | Purpose |
|---|---|---|
| `GET /devices` · `GET /devices/{id}` | owner (own), iot_staff, cloud_staff | F4/F5: inventory with filters, device detail |
| `POST /devices` | iot_staff | Register; the response contains `device_key` **once** |
| `PUT /devices/{id}` · `DELETE /devices/{id}` | iot_staff | Edit, delete |
| `PUT /devices/{id}/wifi` | iot_staff | Wi-Fi (SSID, security, password, IP mode); queues `sync_config` |
| `POST /devices/{id}/commands` · `GET /devices/{id}/commands` | iot_staff (queue), iot_staff and cloud_staff (history) | Commands: capture, reboot, sync, `start_route`, `stop_route`, `simulate_event` |
| `GET /simulator/cars` · `GET /simulator/{car_id}/state` | iot_staff, cloud_staff | F11: cars, live position, latest frame link, recent events and classifications |
| `GET /simulator/scenarios/{category}/audio` | iot_staff, cloud_staff | Browser preview of a bundled scenario recording |
| `POST /devices/{id}/heartbeat` | edge (`X-Device-Key`) | Liveness, firmware, optional position, speed and heading; returns queued commands |
| `POST /devices/{id}/commands/{command_id}/ack` | edge | Close a delivered command with `{ "ok": true }` or `{ "ok": false }` |
| `POST /devices/{id}/frame` | edge | Multipart `frame`: a JPEG of at most 2 MiB, camera devices only |

### C3 – Data Management
| Method and path | Roles | Purpose |
|---|---|---|
| `POST /device-data` | edge (`X-Device-Key`) | Multipart `audio_file`, `car_id`, `device_id`; optional `clip_id` (UUID), `captured_at` (UTC), `trigger`, lat/lon. 202 for a new clip, 200 for a repeat. |
| `GET /device-data` · `GET /device-data/{record_id}` | iot_staff, cloud_staff | F13: records with filters, record detail |
| `GET /device-data/stats` | iot_staff, cloud_staff | F13: state cards and 12-hour intake |
| `GET /device-data/{record_id}/audio` | iot_staff, cloud_staff | Presigned link to the raw clip |
| `POST /device-data/{record_id}/retry` | cloud_staff | Requeue a failed record |

### C4 – Alert Tracking and ML Orchestrator
| Method and path | Roles | Purpose |
|---|---|---|
| `GET /alerts` | owner (own cars), cloud_staff | F12: filters for car, sound type, severity, status, anomaly and dates; 20 per page, newest first |
| `GET /alerts/summary` | owner, cloud_staff | Counts by status, severity and type |
| `GET /alerts/{id}` | owner, cloud_staff | F7: the alert with its class probabilities |
| `PUT /alerts/{id}` | owner (acknowledge), cloud_staff (acknowledge, resolve) | `{ status, note }`. Only new → acknowledged → resolved; anything else is 409 (DT-C4-3). |
| `GET /alerts/{id}/audio` | owner, cloud_staff | `{ url, expires_in: 300 }` |

There's no HTTP endpoint that creates alerts: the C3 worker calls
`MLOrchestrator.handle_record(record)`. See [Processing pipeline](#processing-pipeline-c3--c4).

### C5 – Database Management and Configuration
| Method and path | Roles | Purpose |
|---|---|---|
| `GET /admin/alert-types` · `PUT /admin/alert-types/{type}` | cloud_staff | F8a: default severity, description, enabled. Changes apply to **new** alerts only (BR-CF-3). |
| `GET /admin/models` · `PUT /admin/models/{id}` | cloud_staff | F8b: versions; confidence threshold 0.50–0.95 (BR-CF-2) |
| `PUT /admin/models/{id}/activate` | cloud_staff | One transaction, then reload; 404 unknown, 409 already active, 422 weights missing (DT-CF-1) |
| `GET /admin/db/health` | cloud_staff | F14: PostgreSQL, MongoDB and S3 status and latency |
| `GET /admin/db/stats` | cloud_staff | F14: count, size and oldest item per store |
| `POST /admin/db/backup` · `GET /admin/db/backups` | cloud_staff | Start a backup (202; 409 if one is running), list jobs |
| `POST /admin/db/retention/run` | cloud_staff | Dry run, or archive then delete, for `device_data_records`, `car_positions` or `alerts` (at least 30 days) |
| `GET /health` | public | Liveness check |
| `GET /local-audio/{key}` | public, dev only | Serves clips from `LOCAL_AUDIO_DIR`; 404 when unset |

---

## Processing pipeline (C3 → C4)

```
edge ──POST /device-data──► ingest_service: validate, put clip in S3, insert record (pending)
                                  │
             processing_worker (thread, polls every 2 s): claim record (120 s lease)
                                  │
             MLOrchestrator.handle_record(record):
               1. alert for this record_id exists? return it (a retry never makes a second alert)
               2. download clip → log-mel spectrogram → 5-class CNN
               3. is_anomaly = confidence < active threshold; severity and enabled from alert_type_config
               4. upsert one alert per record_id ($setOnInsert)
               5. notify the owner if subscribed (a failure here never fails the record);
                  a disabled type stores the alert with suppressed: true and notifies no one
                                  │
             record → processed (or requeued, or failed after 3 attempts)
```

- **One API process only** (`--workers 1`). The worker, the sweeper and backup jobs run inside it,
  and interrupted jobs are marked failed at startup.
- To run the worker separately, set `PROCESSING_WORKER_ENABLED=false` on the API and run
  `python -m app.services.processing_worker`.

---

## Layout

```
backend/
├── app/
│   ├── main.py            # create_app(): CORS, routers, startup (indexes, model, worker, sweeper)
│   ├── config.py          # Settings (pydantic-settings, reads backend/.env)
│   ├── auth.py            # JWT: CurrentUser, require_role, owns_car (auth_stub.py re-exports it)
│   ├── routers/           # HTTP endpoints, one file per area (see "API by component")
│   ├── schemas/           # request and response models
│   ├── services/          # business logic per component; calls repositories
│   ├── ml/                # preprocessor, classifier, anomaly, severity, model_config
│   └── db/                # ALL data access (C5)
│       ├── postgres.py, mongo.py, s3.py
│       ├── models/        # SQLAlchemy tables; allowed values in constants.py
│       ├── repositories/  # the only code that queries a store
│       ├── migrations/    # Alembic: 0001_initial_schema, 0002_device_commands
│       └── seed.py        # idempotent demo data (seed_data/alerts.json)
├── edge/                  # edge clients (CARLA replay, local demo, live CARLA), recordings, scenario clips
├── tests/                 # pytest: one file per area; no external services needed
├── Dockerfile             # API image (local Docker stack, AWS option B)
└── alembic.ini, pytest.ini, requirements.txt, .env.example
```

## Deployment

See [`infra/aws/SETUP.md`](../infra/aws/SETUP.md) (option A, systemd and a virtualenv on EC2) or
[`infra/aws/docker/SETUP.md`](../infra/aws/docker/SETUP.md) (option B, Docker on EC2).

Either way:
- Python 3.12, CPU-only torch 2.5.1, **one uvicorn worker**.
- Set your own `JWT_SECRET` and `WIFI_ENCRYPTION_KEY`.
- `DEV_FAKE_EMAIL=false` and `DEV_FAKE_BACKUP=false`.
- Leave `LOCAL_AUDIO_DIR` unset.
