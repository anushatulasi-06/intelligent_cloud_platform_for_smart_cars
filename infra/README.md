# Infrastructure

| Path | Purpose |
|---|---|
| [`docker-compose.yml`](docker-compose.yml) | Local PostgreSQL 16 and MongoDB 7 only, for running the backend and frontend on the host. See the [root README](../README.md#run-it). |
| [`demo-compose.yml`](demo-compose.yml) | Full local stack in Docker: PostgreSQL, MongoDB, MinIO (S3-compatible), API, frontend and the local edge client. |
| [`aws/`](aws/) | EC2 / S3 deployment scripts. See the [root README](../README.md#deployment-aws). |
| [`carla/`](carla/) | CARLA 0.9.16 server launcher, hardware probe and matching client requirements. |

---

## Full local stack (Docker)

Needs Docker Engine or Docker Desktop with the Compose plugin. Run from the repository root. The
first build downloads the CPU inference dependencies and database images.

```bash
python3 infra/init_demo_env.py                     # creates infra/.env.demo once (gitignored)
docker compose --env-file infra/.env.demo -f infra/demo-compose.yml up --build -d
docker compose --env-file infra/.env.demo -f infra/demo-compose.yml ps
```

- Keep `infra/.env.demo` between restarts. It holds the Wi-Fi encryption key, so stored Wi-Fi
  passwords can't be decrypted without it. Don't commit it or show it on screen.
- The API runs migrations and seeds the database before it serves requests.
- MinIO provides real S3-compatible uploads and signed downloads, so no AWS account is needed.
- PostgreSQL and MongoDB are only reachable inside the Compose network.

| Surface | Address |
|---|---|
| UI and login | http://localhost:5173/login |
| API docs | http://localhost:8000/docs |
| Object downloads (MinIO) | http://localhost:9000 |
| MinIO console | http://localhost:9001 (credentials in `infra/.env.demo`) |

The frontend forwards `/api` to the API. The edge client reaches the API over the internal network.

```bash
# Logs, without resetting any data
docker compose --env-file infra/.env.demo -f infra/demo-compose.yml logs --tail=80 api edge-demo

# Stop; database, object and edge-buffer volumes are kept
docker compose --env-file infra/.env.demo -f infra/demo-compose.yml down
```

The defaults bind everything to the local machine. A remote deployment needs reachable
UI/API/object-storage URLs, private secrets and HTTPS. With AWS S3, leave `S3_ENDPOINT_URL` unset
and provide the bucket, region and credentials or an instance role.

### Seeded accounts (local development only)

| Account | Password | Access |
|---|---|---|
| `iot@autosound.dev` | `iot-dev-pass` | Devices: create, edit, Wi-Fi, commands; Simulator |
| `cloud@autosound.dev` | `cloud-dev-pass` | Inspect devices and Simulator, retry audio records, Alerts and Config |
| `owner@autosound.dev` | `owner-dev-pass` | Owner dashboard and alerts; staff pages are locked |

- A fresh seed creates car 1 with microphone `mic-1` and camera `cam-1`, and the Compose edge
  client uses those two devices.
- A device created in F4 gets a new random key. An edge process must be configured with that key
  before the device sends heartbeats or acknowledges commands. Registering a device doesn't start
  a device process.
- To check it works, open `/simulator?car_id=1` as IoT Staff. Heartbeats produce telemetry and a
  labelled camera frame. **Start route** moves the car; **Stop route** stops it. A device's
  status comes from its recent heartbeats, not from the seed.

### Live verification

With the stack running, from the repository root:

```bash
backend/.venv/bin/python scripts/verify_components.py --base-url http://localhost:8000
```

The script tests the running system end to end: real HTTP, JWT login, the databases, object
storage, the processing worker and the deployed model.
- It creates one uniquely named temporary microphone, checks its lifecycle, and deletes only that
  device.
- The test-tone audio it uploads and the alert it creates are left in the system.
- Add `--output <file>.json` to save a result file. The file contains no credentials.
- The tone only checks that ingestion and inference run. Use real labelled recordings to judge
  classification accuracy.

---

## Edge clients and CARLA

There are two edge clients:
- the **local edge demo**, a virtual car with an illustrated camera, which runs in the stack above
  as `edge-demo`
- the **CARLA client**

Both send heartbeats, camera frames, command acknowledgements and audio uploads to the API.
Configuration, commands, audio scenarios and buffering are covered in the
[edge client guide](../backend/edge/README.md). Recording credits and the CC BY-NC 3.0 license are
in [`demo_clips/README.md`](../backend/edge/demo_clips/README.md).

To use your own recordings, pass `--scenarios <file>.json`. The mapping file has one entry per
category; copy [`scenarios.example.json`](../backend/edge/scenarios.example.json) to start.
- Paths in the file are relative to the file itself.
- Each recording must be a non-empty WAV or MP3, at most 10 MiB and 15 s long.

### CARLA server (Linux x86-64)

Needs Python 3.12, a working hardware Vulkan driver, and room for the 8 GB server archive. From the
repository root:

```bash
python3.12 -m venv .local_carla/client-env
.local_carla/client-env/bin/python -m pip install -r infra/carla/requirements-client.txt
export CARLA_CLIENT_PYTHON="$PWD/.local_carla/client-env/bin/python"
export CARLA_TASK_ROOT="$PWD/.local_carla"
"$CARLA_CLIENT_PYTHON" infra/carla/probe.py --work-dir "$CARLA_TASK_ROOT" --require-gpu
bash infra/carla/launch.sh prepare        # downloads the official 0.9.16 server, checks its size
bash infra/carla/launch.sh start camera   # low-quality offscreen rendering, 0.05 s physics step
```

- Everything generated stays under `.local_carla/`, which is gitignored.
- `CARLA_MAP` optionally overrides the default map.
- The server uses RPC port 2000. The edge client uses Traffic Manager port 8001, to stay clear of
  the API port.

Then connect the Docker stack to CARLA. Stop the lightweight edge first, because two edge clients
must never use the same device IDs:

```bash
docker compose --env-file infra/.env.demo -f infra/demo-compose.yml stop edge-demo
cd backend
"$CARLA_CLIENT_PYTHON" -m edge.carla_demo --camera-width 320 --camera-height 180 --no-edge-audio
```

- Set `EDGE_API_URL` and the `EDGE_*` device IDs and keys as described in the
  [edge client guide](../backend/edge/README.md).
- `edge.carla_demo` creates its own vehicle and camera and removes them when it exits.
  `edge.carla_client --actor-id <id>` attaches to an existing vehicle instead.
- To stop: press Ctrl+C in the edge terminal, then run `bash infra/carla/launch.sh stop` from the
  repository root.
- Wi-Fi and reboot commands only act on the simulated device. Real hardware needs its own
  executor for them.
