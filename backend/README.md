# Backend – Smart Car Platform API

FastAPI app for the Smart Car Intelligent Cloud Platform (CMPE 281, Group 8).

## Run it

Run these in order. Steps 1 and 7 run from the repo root; the rest run from `backend/`.

```bash
# 1. Databases: PostgreSQL 16 on :5432 and MongoDB 7 on :27017 (needs Docker). From the repo root:
docker compose -f infra/docker-compose.yml up -d
docker compose -f infra/docker-compose.yml ps        # both services "healthy"

# 2. Python environment (Python 3.12+), from backend/
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                  # defaults match the docker-compose databases

# 3. Tables: creates the 7 PostgreSQL tables
alembic upgrade head

# 4. Seed: 4 users, 4 cars, 5 devices, 5 alert types, model cnn-v1 (active), 25 alerts. Safe to rerun.
python -m app.db.seed

# 5. Model artifact check: loads the ACTIVE model_versions row's files from ../model/artifacts/cnn-v1
python -c "from app.services.orchestrator import get_orchestrator; m = get_orchestrator().reload_model(); print('active model OK:', m.version_label, 'threshold', m.threshold)"
#    expect: active model OK: cnn-v1 threshold 0.75

# 6. API (http://localhost:8000/docs)
uvicorn app.main:app --reload

# 7. Optional, playable alert audio without S3 (see "Dev audio without S3" below). From the repo root:
python scripts/make_local_audio.py
```

No Docker? Install with Homebrew (`brew install postgresql@16 mongodb/brew/mongodb-community@7.0`),
create the `user`/`pass` role and the `autosound` database, then continue at step 2.

**Frontend against this API:** in `frontend/.env.local` set `VITE_USE_MOCK=false` and
`VITE_API_BASE_URL=http://localhost:8000`, then run `npm run dev`. CORS allows
`http://localhost:5173` (`CORS_ORIGINS`) with the methods GET, POST, PUT and DELETE and the headers
`X-Role`, `X-User-Id`, `Content-Type` and `Authorization`.

**Dev logins** created by the seed (local development only):
`owner@autosound.dev / owner-dev-pass` (cars 1–3), `owner2@autosound.dev / owner2-dev-pass` (car 4),
`iot@autosound.dev / iot-dev-pass`,
`cloud@autosound.dev / cloud-dev-pass`.

**Tests** need no databases or AWS: `python -m pytest` (from `backend/`). They run on SQLite and
mongomock. To also check the migration against real PostgreSQL, point `TEST_DATABASE_URL` at a
**throwaway** database. That test drops everything in it:

```bash
TEST_DATABASE_URL=postgresql+psycopg2://user:pass@localhost:5432/autosound_test python -m pytest
```

## Deployment (EC2)

**Python 3.12** on the EC2 instance (team decision, 2026-11-04). torch, numba and psycopg2 all ship
mature builds for it, and Amazon Linux 2023 packages it (`sudo dnf install python3.12`). The code also
runs and is tested on 3.14. Install the CPU-only torch build to keep the instance small:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
```

Run the API as **one uvicorn worker** (team decision, 2026-11-04):

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

Backups run as in-process background tasks, and at startup any job left queued or running is marked
failed (`FAIL_INTERRUPTED_JOBS_ON_STARTUP=true`). Both assume a single process. Before scaling to
several workers, set that flag to `false` and move jobs to a separate worker.

**Dev backups without S3:** with `DEV_FAKE_BACKUP=true` and no S3 set up on this machine (no AWS
credentials or no reachable endpoint), backup files go to `backend/.local_backups/backups/<date>/`
(`LOCAL_BACKUP_DIR`, gitignored), and the job shows `local:<path>` keys. Other S3 errors still fail the
job. Real backups (`DEV_FAKE_BACKUP=false`) always need S3.

**Dev audio without S3:** run `python scripts/make_local_audio.py` from the repo root (copies a real
clip of the right sound type from `model/data/` for each seeded alert into `backend/.local_audio/`,
gitignored) and set `LOCAL_AUDIO_DIR=.local_audio` in `backend/.env`. `GET /alerts/{id}/audio` then
returns `http://<api>/local-audio/<s3_object_key>`, which this API serves without headers (like a
presigned URL). Unset (the default, and on EC2), `/local-audio/` answers 404 and links are presigned S3 URLs.

## Layout

```
backend/
├── app/
│   ├── main.py          # create_app(): CORS, routers, Mongo indexes on startup
│   ├── config.py        # Settings (pydantic-settings, reads backend/.env)
│   ├── auth_stub.py     # TEMPORARY role headers – replace with the C1 JWT version
│   ├── db/              # ALL database code
│   │   ├── postgres.py  # engine, session_scope(), get_session(), ping()
│   │   ├── mongo.py     # client, get_collection(), INDEXES, ensure_indexes(), ping()
│   │   ├── s3.py        # put_object, get_object, presigned_url, list_prefix, ping()
│   │   ├── models/      # SQLAlchemy tables, one file each; allowed values in constants.py
│   │   ├── repositories/# the only code that queries a store, one file per data set
│   │   ├── migrations/  # Alembic (alembic.ini is in backend/); 0001_initial_schema
│   │   ├── seed.py      # idempotent dev data; seed_data/alerts.json = the frontend's 25 mock alerts
│   ├── routers/         # HTTP endpoints: health, alerts, admin_config, admin_db, local_audio (dev)
│   ├── services/        # business logic, calls repositories
│   └── ml/              # inference; artifacts from MODEL_DIR (../model/artifacts)
├── tests/
├── alembic.ini, pytest.ini, requirements.txt, .env.example
```

## Rules

- **Only repositories talk to a database** (BR-5.1). Routers call services; services call repositories.
- **Owners see only their own cars:** put the owner filter in the repository query, never filter after fetching.
- **Every router except `/health` uses `require_role`** from `app.auth_stub`. The C1 JWT version will
  replace that one file and keep the names `CurrentUser`, `get_current_user` and `require_role`.
  Until then, send `X-Role: owner | iot_staff | cloud_staff` and optionally `X-User-Id` (default 1).
  A missing or unknown role returns 401; a role that isn't allowed returns 403.
- Five sound categories only: emergency, in_vehicle, human, traffic, environmental.
- `notify_method` is `email` (AWS SES) or `in_app`. No MQTT, MSK, IoT Core or push notifications.

## Data

| Store | Data | Repository |
|---|---|---|
| PostgreSQL | users | `UserRepo` (emails stored lower-case) |
| | cars | `CarRepo` (`ids_for_owner` feeds owner-scoped Mongo queries) |
| | devices | `DeviceRepo` (`search(owner_id=...)` joins cars) |
| | subscriptions | `SubscriptionRepo` (`upsert`, `is_enabled`) |
| | alert_type_config | `AlertTypeRepo` |
| | model_versions | `ModelRepo` (`activate`, `set_threshold` 0.50–0.95) |
| | maintenance_jobs | `JobRepo` |
| MongoDB | alerts | `AlertRepo` (`search(car_ids=...)`, `upsert_by_record_id`, `summary`) |
| | car_positions | `PositionRepo` (`last_per_car`, `track`) |
| | device_data_records | `RecordRepo` (`claim_next` 120 s lease, 3 attempts, `stats`) |

Owner scoping: owners pass `car_ids=CarRepo.ids_for_owner(user.id)` (or `owner_id=`). The filter is
part of the query, and an owner can't widen it with `car_id`.

**Schema note:** `alert_type` is `VARCHAR(16)` in `alert_type_config` and `subscriptions`, not the
design doc's `VARCHAR(12)`, because `'environmental'` is 13 characters.

## ML inference

`app/ml/` (preprocessor, classifier, anomaly, severity) and `app/services/orchestrator.py`.

- **Model folders:** `MODEL_DIR/<version>/` (default `../model/artifacts/<version>`) holds
  `model_weights.pt`, `labels.json` and `config.json`. A `model_versions` row points to its folder through
  `artifact_path`. The orchestrator loads the **active** row at startup and again on
  `PUT /admin/models/{id}/activate`.
- **`config.json`** is the only source of audio, feature and architecture parameters in the backend.
  It's generated from the training code (`model/config.py`, `model/cnn.py`), and every librosa setting
  is written out explicitly. Tests check that the output is identical to `model/audio_utils.py` and
  that the real trained weights load into the backend network.
- **`handle_record(record)`** is called by the queue worker, not over HTTP. It downloads the clip,
  classifies it and stores one alert per `record_id`, then notifies the owner. If the alert type is
  disabled, the alert is stored with `suppressed: true` and nobody is notified. Download and model
  errors are raised so the worker retries the record.

```bash
python scripts/export_model.py --version cnn-v1  # package the trained checkpoint (model/checkpoints)
python scripts/make_dummy_model.py               # random-weight model/artifacts/cnn-dummy (tests, no model)
```

## Migrations

```bash
alembic revision --autogenerate -m "create users"   # after adding a model and importing it in db/models/__init__.py
alembic upgrade head
```

**Next migration (team decision, 2026-11-04):** Rishi (C2) adds the `device_commands` table as
`0002_device_commands`, with `down_revision = "0001_initial_schema"`. Use the design doc's columns:
`id`, `device_id` FK `devices(id)` ON DELETE CASCADE, `command`, `payload` JSONB, `status`,
`created_at`, `delivered_at`, `acked_at`, `expires_at`, `detail`. Keep a single head:
`alembic heads` must print one revision. Add the model in `app/db/models/` and the repository as
`app/db/repositories/command_repo.py`.

## Notifications (AWS SES)

- `notify_method` is `email` (SES) or `in_app`. There are no push notifications.
- **Locally**, `DEV_FAKE_EMAIL=true` (the default) only logs the email.
- **For the demo**, SES stays in **sandbox mode** (team decision, 2026-11-04). In sandbox, SES only
  delivers to verified addresses, so before setting `DEV_FAKE_EMAIL=false`:
  1. In the SES console (region `AWS_REGION`, us-west-2), verify the sender `SES_FROM_EMAIL`
     (default `alerts@autosound.dev`; change it to an address you control).
  2. Verify each demo recipient: the owner emails that will receive alerts.
  3. Give the EC2 instance role `ses:SendEmail`.
- A failed send is logged and never fails alert creation.
