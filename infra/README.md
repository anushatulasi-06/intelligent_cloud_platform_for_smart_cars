# Infrastructure

How to run the whole system (C1–C5), locally or on AWS, plus demo data, verification and the edge
clients, including CARLA.

## Ways to run it

| Where | How | Needs | Guide |
|---|---|---|---|
| **Local, no Docker** | Homebrew PostgreSQL and MongoDB, MinIO for S3, backend and frontend in terminals | macOS with Homebrew | [Local setup without Docker](#local-setup-without-docker-homebrew--minio) |
| **Local, Docker databases** | `docker-compose.yml` runs PostgreSQL and MongoDB; backend and frontend in terminals | Docker | `docker compose -f infra/docker-compose.yml up -d`, then [backend README → Run it](../backend/README.md#run-it) |
| **Local, all in Docker** | `demo-compose.yml` runs everything: databases, MinIO, API, frontend and edge client | Docker | [Full local stack (Docker)](#full-local-stack-docker) |
| **AWS, option A** | Native API on EC2, local frontend through SSH | Separate authorization and positive approved credit budget | [`aws/SETUP.md`](aws/SETUP.md) |
| **AWS, option B** | API/UI on EC2 loopback through SSH | Separate authorization and positive approved credit budget | [`aws/docker/SETUP.md`](aws/docker/SETUP.md) |

## What's in this folder

| Path | Purpose |
|---|---|
| [`docker-compose.yml`](docker-compose.yml) | PostgreSQL 16 and MongoDB 7 only, for local development |
| [`demo-compose.yml`](demo-compose.yml) · [`init_demo_env.py`](init_demo_env.py) · [`init_demo_storage.py`](init_demo_storage.py) · [`start_demo_api.sh`](start_demo_api.sh) · [`Minio.Dockerfile`](Minio.Dockerfile) · [`extract_demo_minio.py`](extract_demo_minio.py) | Full local Docker stack: compose file, private settings generator, bucket creation, API start-up, MinIO image |
| [`aws/`](aws/) | AWS deployment, option A (scripts) and option B (Docker) |
| [`carla/`](carla/) | CARLA 0.9.16 server launcher, hardware probe, matching client requirements |

---

## Seeded accounts

`python -m app.db.seed` creates these **local development** accounts. Their passwords are public
in the repository. AWS production setup refuses that seed and uses the private empty-database
bootstrap described in the AWS guides; never deploy these known credentials publicly.

| Account | Password | Role | Can open |
|---|---|---|---|
| `owner@autosound.dev` | `owner-dev-pass` | owner of cars 1–3 | Dashboard, Subscriptions, Map, Tracking, Alerts (own cars) |
| `owner2@autosound.dev` | `owner2-dev-pass` | owner of car 4 | The same pages, car 4 only |
| `iot@autosound.dev` | `iot-dev-pass` | IoT staff | Device Manager (create, edit, Wi-Fi, commands), Simulator |
| `cloud@autosound.dev` | `cloud-dev-pass` | cloud staff | Everything, including Config, device data retry and the fleet dashboard |

The seed also creates:
- 4 cars and 5 devices: a microphone on each car, plus camera `cam-1` on car 1. They start as
  *registered*; a device only shows *online* while an edge client sends heartbeats.
- 5 alert types, the active model `cnn-v1`, and 25 alerts.

The seed is idempotent: it adds only missing rows and never overwrites anything.

---

## Local setup without Docker (Homebrew + MinIO)

For macOS without Docker. MinIO is a local S3-compatible server, so audio uploads, camera frames
and presigned links work just as they do on AWS.

### One-time setup
```bash
brew install postgresql@16 mongodb/brew/mongodb-community@7.0 minio
brew services start postgresql@16
brew services start mongodb/brew/mongodb-community@7.0
createuser -s user 2>/dev/null; psql -d postgres -c "ALTER USER \"user\" PASSWORD 'pass'"
createdb -h localhost -U user autosound

cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env
cd ../frontend && npm install && cp .env.example .env.local && cd ..
```

In `backend/.env`, set these values:

| Setting | Value |
|---|---|
| `S3_BUCKET` | `autosound-audio-dev` |
| `S3_ENDPOINT_URL` | `http://127.0.0.1:9000` |
| `S3_PUBLIC_ENDPOINT_URL` | empty |
| `LOCAL_AUDIO_DIR` | empty |
| `WIFI_ENCRYPTION_KEY` | generate once with `backend/.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`, and keep it |

`LOCAL_AUDIO_DIR` must stay empty, or audio from new alerts won't play.

### Start (one terminal each, from the repository root)

| Terminal | Command |
|---|---|
| 1. MinIO | `MINIO_ROOT_USER=minioadmin MINIO_ROOT_PASSWORD=minioadmin minio server ~/minio-data --console-address :9001` |
| 2. API | `cd backend && source .venv/bin/activate && export AWS_ACCESS_KEY_ID=minioadmin AWS_SECRET_ACCESS_KEY=minioadmin && uvicorn app.main:app --reload` |
| 3. UI | `cd frontend && npm run dev` |
| 4. Edge client (car 1) | `cd backend && source .venv/bin/activate && EDGE_API_URL=http://localhost:8000 EDGE_CAR_ID=1 EDGE_DEVICE_ID=1 EDGE_DEVICE_KEY=dev-device-key-mic-1 EDGE_CAMERA_DEVICE_ID=5 EDGE_CAMERA_DEVICE_KEY=dev-device-key-cam-1 python -m edge.replay_client --no-audio-playback` |
| 5. Map positions | `cd backend && source .venv/bin/activate && python ../scripts/demo_data.py --demo-only --live --offline 1 4` |

The backend reads S3 credentials from the shell environment, not from `.env`, so keep the
`export` in terminal 2.

Terminal 4 plays bundled CARLA recordings, with sound captured at each scenario's event. It
needs no GPU or CARLA server. For the illustrated virtual car and independent Start/Stop route,
use `python -m edge.demo_client` instead. The full local Docker stack keeps that virtual client
as its default; stop its edge-demo before running replay against the same devices.

**First time only**, and again after a database reset: run these in terminal 2 before `uvicorn`.
```bash
S3_ENDPOINT_URL=http://127.0.0.1:9000 S3_BUCKET=autosound-audio-dev AWS_REGION=us-west-2 \
  python ../infra/init_demo_storage.py     # create the bucket
alembic upgrade head                       # 8 tables
python -m app.db.seed                      # seeded accounts and data
```
Then upload the seeded alerts' audio, as described in [Demo data](#demo-data).

| Address | What |
|---|---|
| http://localhost:5173 | UI |
| http://localhost:8000/docs | API docs |
| http://localhost:9001 | MinIO console (`minioadmin` / `minioadmin`) |

**Reset the data:**
```bash
dropdb -h localhost -U user autosound && createdb -h localhost -U user autosound
mongosh autosound --quiet --eval 'db.dropDatabase()'
```
Then repeat the first-time commands.

---

## Full local stack (Docker)

Needs Docker Engine or Docker Desktop with the Compose plugin. Run from the repository root. The
first build downloads the CPU inference dependencies and database images.

```bash
python3 infra/init_demo_env.py                     # creates infra/.env.demo once (gitignored)
docker compose --env-file infra/.env.demo -f infra/demo-compose.yml up --build -d
docker compose --env-file infra/.env.demo -f infra/demo-compose.yml ps
```

It starts PostgreSQL, MongoDB, MinIO, a one-off bucket initializer, the API, the frontend and the
local edge client.
- The API runs the migrations and the seed before it serves requests.
- PostgreSQL and MongoDB are reachable only inside the Compose network.
- Keep `infra/.env.demo` between restarts. It holds the Wi-Fi encryption key, so stored Wi-Fi
  passwords can't be decrypted without it. Don't commit it or show it on screen.
- The MinIO image is x86-64 only, so on Apple Silicon it runs under emulation.

| Address | What |
|---|---|
| http://localhost:5173/login | UI. nginx also forwards `/api` to the API. |
| http://localhost:8000/docs | API docs |
| http://localhost:9000 | Object downloads (MinIO) |
| http://localhost:9001 | MinIO console (credentials in `infra/.env.demo`) |

```bash
# Logs, without resetting any data
docker compose --env-file infra/.env.demo -f infra/demo-compose.yml logs --tail=80 api edge-demo

# Stop; database, object and edge-buffer volumes are kept
docker compose --env-file infra/.env.demo -f infra/demo-compose.yml down
```

---

## Demo data

Besides the seed, these give each component something to show:

| Data | For | How |
|---|---|---|
| **Car positions** | C1 Map (F9), Tracking (F10), "today" counts | `python scripts/demo_data.py --demo-only` creates marked synthetic history only for seed-matching cars with no positions. Add `--live` for ongoing demo points, `--offline 1 4` to omit those cars from live updates, and `--minutes N` for the initial history length. Existing history and every alert timestamp stay intact. Cars with real or legacy unmarked telemetry are skipped; the feed stops for a car when real telemetry appears. A car is online while its last position is under 90 s old. |
| **Live Simulator, F13 records, new alerts** | C2, C3, C4 | Run an edge client, then trigger the five sound scenarios in F11 (`/simulator?car_id=1`). |
| **Audio for the 25 seeded alerts** | C4 F7 | The seed creates no audio. Upload the clips from `backend/.local_audio/` (created by `python scripts/make_local_audio.py`, which needs `model/data/`) to the bucket, using the backend's own S3 settings. See below. |
| **Subscriptions** | C4 notifications | None are seeded. Sign in as an owner, open **Dashboard → Subscriptions (F3)**, and turn categories on. |
| **Backup jobs** | C5 F14 | Click **Run backup now**. |

To upload the seeded audio, run this from `backend/`, with the virtualenv active and the
storage credentials exported:

```bash
python -c "
from pathlib import Path
from app.db import s3
root = Path('.local_audio'); files = sorted(root.rglob('*.wav'))
for p in files: s3.upload_file(str(p), p.relative_to(root).as_posix(), 'audio/wav')
print('uploaded', len(files), 'clips')"
```

When the edge client drives car 1, keep car 1 in `demo_data.py --demo-only --offline`, so the two sources
never write different routes for the same car.

---

## Live verification

With any setup running, from the repository root:

```bash
backend/.venv/bin/python scripts/verify_components.py --base-url http://localhost:8000
```

It tests the running system end to end: real HTTP, JWT login, the databases, object storage, the
processing worker and the deployed model.
- It creates one uniquely named temporary microphone, checks its lifecycle, and deletes only that
  device.
- The test-tone audio it uploads and the alert it creates are left in the system.
- Add `--output <file>.json` to save a result file. The file contains no credentials.
- The tone only checks that ingestion and inference run. Use real labelled recordings to judge
  classification accuracy.

---

## Edge clients and CARLA

The edge clients send heartbeats, camera frames, command acknowledgements and audio uploads to
the API, using the seeded devices `mic-1` (ID 1) and `cam-1` (ID 5) on car 1:

| Client | What it is | Camera | Needs |
|---|---|---|---|
| `edge.replay_client` | Replays bundled CARLA scenarios; choose a scenario, then Start/Stop route resume/pause it | Recorded CARLA RGB frames | No GPU or CARLA server |
| `edge.demo_client` | An illustrated virtual car driven by Start/Stop route | Illustration labelled "LOCAL EDGE DEMO" | No simulator |
| `edge.carla_demo` / `edge.carla_client` | A live vehicle in a CARLA 0.9.16 world | Live CARLA RGB frames | A working compatible CARLA server |

Both AWS options default to `EDGE_CLIENT=replay_client` when the optional edge feed is explicitly
enabled. `EDGE_CLIENT=demo_client` selects the virtual alternative; other selectors are rejected.
The AWS installers keep feeds off until their separate cost acknowledgement is supplied. Replay
heartbeats every second, uploads changing frames up to once per second, and resends a still frame
every five seconds while idle/paused. This differs from the virtual client's two-second cadence.
See [replay timing and recordings](../backend/edge/README.md#replay-recorded-carla-scenarios)
and [AWS request-cost assumptions](aws/README.md#planning-costs-before-credits).

**Run only one edge client at a time** for the same devices.

Configuration, commands, audio scenarios and buffering are covered in the
[edge client guide](../backend/edge/README.md). Recording credits and the CC BY-NC 3.0 license are
in [`demo_clips/README.md`](../backend/edge/demo_clips/README.md).

To use your own recordings, pass `--scenarios <file>.json`. The mapping file has one entry per
category; copy [`edge_scenarios.json`](../backend/edge/edge_scenarios.json) to start.
- Paths in the file are relative to the file itself.
- Each recording must be a non-empty WAV or MP3, at most 10 MiB and 15 s long.

### CARLA server (Linux x86-64 with an NVIDIA GPU)

**Requirements:**
- Linux x86-64 with Python 3.12, room for the 8 GB server archive, and a working hardware
  **Vulkan** graphics driver.
- macOS isn't supported.
- **Google Colab can't render CARLA's camera.** Its GPUs are exposed for compute only, so Vulkan
  fails with `ERROR_INCOMPATIBLE_DRIVER`. There, only physics mode (`start physics`, no camera)
  works.

From the repository root:

```bash
python3.12 -m venv .local_carla/client-env
.local_carla/client-env/bin/python -m pip install -r infra/carla/requirements-client.txt
export CARLA_CLIENT_PYTHON="$PWD/.local_carla/client-env/bin/python"
export CARLA_TASK_ROOT="$PWD/.local_carla"
"$CARLA_CLIENT_PYTHON" infra/carla/probe.py --work-dir "$CARLA_TASK_ROOT" --require-gpu
bash infra/carla/launch.sh prepare        # downloads the official 0.9.16 server, checks its size
bash infra/carla/launch.sh start camera   # or: start physics (no camera)
```

- Everything generated stays under `.local_carla/`, which is gitignored.
- `CARLA_MAP` optionally overrides the default map.
- The server uses RPC port 2000. The edge client uses Traffic Manager port 8001, to stay clear of
  the API port.

Then stop any other edge client using the same devices, and connect CARLA to the API:

| Where the API runs | Stop the other edge client with | `EDGE_API_URL` |
|---|---|---|
| Local, no Docker | Ctrl+C in terminal 4 | `http://localhost:8000` (on the same machine) |
| Full local Docker stack | `docker compose --env-file infra/.env.demo -f infra/demo-compose.yml stop edge-demo` | `http://localhost:8000` |
| AWS | use an authorized private SSH API tunnel from [`aws/SETUP.md`](aws/SETUP.md) or [`aws/docker/SETUP.md`](aws/docker/SETUP.md) | `http://127.0.0.1:8000` through SSH |

```bash
cd backend
export EDGE_API_URL=http://localhost:8000 EDGE_CAR_ID=1 \
       EDGE_DEVICE_ID=1 EDGE_DEVICE_KEY=dev-device-key-mic-1 \
       EDGE_CAMERA_DEVICE_ID=5 EDGE_CAMERA_DEVICE_KEY=dev-device-key-cam-1
"$CARLA_CLIENT_PYTHON" -m edge.carla_demo --camera-width 320 --camera-height 180 --no-edge-audio
```

- Add `--no-camera` when the server runs in physics mode.
- `edge.carla_demo` creates its own vehicle and camera and removes them when it exits.
  `edge.carla_client --actor-id <id>` attaches to an existing vehicle instead.
- To stop: press Ctrl+C in the edge terminal, then run `bash infra/carla/launch.sh stop` from the
  repository root.
- Wi-Fi and reboot commands only act on the simulated device. Real hardware needs its own
  executor for them.

---

## AWS deployment

One EC2 instance with RDS PostgreSQL, MongoDB Atlas, S3 and SES. Pick an option and follow its
guide from start to finish: **[option A, scripts](aws/SETUP.md)** or
**[option B, Docker](aws/docker/SETUP.md)**.
