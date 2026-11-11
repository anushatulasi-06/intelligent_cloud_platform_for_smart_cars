# CMPE 281 Deliverable 3 Individual Component Implementation

**Author: Rishi Sheth | Group 8 | November 10, 2026**

This submission covers **C2 IoT Device Manager** and **C3 Data Management**. It implements the Devices inventory and registration form (F4), device detail and Wi-Fi controls (F5), Simulator (F11), and audio processing monitor (F13). The reference is the team's Deliverable 2 *Component Design Group 8*, sections C2 and C3.

C2 manages device identity, encrypted Wi-Fi provisioning, commands, heartbeat state, camera frames and simulator telemetry. C3 validates audio, stores accepted clips in S3-compatible object storage, tracks records in MongoDB and dispatches them to the shared ML Orchestrator. C1 provides JWT login; C4 and C5 provide alert classification and model administration. Their integration is demonstrated without claiming those components as Rishi's individual implementation.

## Rubric mapping

| Requirement | Component implementation and demo evidence |
| --- | --- |
| Complete UI and backend live demo, 3 points | F4 registration, search, filters, edit and delete; F5 Wi-Fi, state and command history; F11 camera, route, scenario and classification; F13 intake, record detail and playback. Demonstrate actual API requests and persisted state. |
| Component unit demo | Run the focused C2/C3 tests below. They exercise validation, authorization, encrypted configuration, command transitions, upload rejection and replay, retries, lease recovery, buffering and the packaged model integration. |
| Complete recorded scenario demo, 2 points | Follow the seven-minute scenario below, record the UI and terminal evidence, and upload the recording with the individual submission. |
| UI, model management and deployment for an AI component | C2/C3 are device and ingestion components. Shared AI integration is visible through Cloud Staff's Config → Models, the active packaged model and the generated alert's model version. The reproducible deployment below runs that integration. Model accuracy requires real labeled audio and its separate evaluation report. |

## Implementation map

| Frame or service | Source | Behavior to demonstrate |
| --- | --- | --- |
| F4 and F5 | [DevicesTab.jsx](../../frontend/src/pages/devices/DevicesTab.jsx), [device_service.py](../../backend/app/services/device_service.py) | Create with car, type, SSID, security, masked password and IP mode; show the device key once; inspect and change configuration. |
| F11 | [SimulatorPage.jsx](../../frontend/src/pages/simulator/SimulatorPage.jsx), [demo_client.py](../../backend/edge/demo_client.py), [carla_client.py](../../backend/edge/carla_client.py) | Live camera and position, route commands, five audio scenarios, recent events and linked classification. |
| F13 | [DeviceDataTab.jsx](../../frontend/src/pages/devices/DeviceDataTab.jsx), [ingest_service.py](../../backend/app/services/ingest_service.py), [processing_worker.py](../../backend/app/services/processing_worker.py) | State cards, twelve-hour intake, filters, record details, audio and Cloud Staff retry. |
| Durable queues | [record_repo.py](../../backend/app/db/repositories/record_repo.py), [edge_buffer.py](../../backend/app/services/edge_buffer.py) | Atomic 120-second processing lease, three attempts, manual retry and persistent FIFO of up to 200 edge clips. |
| Live verification | [verify_components.py](../../scripts/verify_components.py) | Exercise a running API, actual stores and active model; retain a credential-free result and generated alert. |

## Start the component deployment

Run from the repository root with Docker Engine or Docker Desktop and the Compose plugin available. The first build downloads the CPU inference dependencies and database images.

```bash
python3 infra/init_demo_env.py
docker compose --env-file infra/.env.demo -f infra/demo-compose.yml up --build -d
docker compose --env-file infra/.env.demo -f infra/demo-compose.yml ps
```

The initializer creates private development configuration once. Preserve `infra/.env.demo` between restarts so stored Wi-Fi credentials remain decryptable. It is excluded from Git.

The deployment contains PostgreSQL, MongoDB, MinIO, the bucket initializer, API, frontend and local edge client. The API migrates and seeds the database before serving requests. MinIO provides actual S3-compatible object uploads and signed downloads; no AWS account is needed for this local deployment. PostgreSQL and MongoDB remain on the Compose network.

| Surface | Local address |
| --- | --- |
| UI and JWT login | `http://localhost:5173/login` |
| API documentation | `http://localhost:8000/docs` |
| Object download endpoint | `http://localhost:9000` |
| MinIO console | `http://localhost:9001` |

The frontend forwards `/api` to the API. The edge client connects to the API through the internal network. Use the MinIO credentials generated in the private environment file only when inspecting its console; do not show that file in a recording.

```bash
# Inspect failures without resetting any data.
docker compose --env-file infra/.env.demo -f infra/demo-compose.yml logs --tail=80 api edge-demo

# Stop the deployment and preserve its database, object and edge-buffer volumes.
docker compose --env-file infra/.env.demo -f infra/demo-compose.yml down
```

The committed defaults bind the demonstration to the local machine. A remote deployment needs its reachable UI/API/object-storage URLs, private secrets and HTTPS configured for that host. With AWS S3, leave the custom S3 endpoint unset and provide the bucket, region and AWS credentials or instance role.

## Sign in and establish the demo state

These are the seeded **local development** accounts. The browser obtains a signed JWT from `/login`; every staff request uses that token.

| Account | Password | Component access |
| --- | --- | --- |
| `iot@autosound.dev` | `iot-dev-pass` | Device creation, edits, Wi-Fi, control commands and Simulator. |
| `cloud@autosound.dev` | `cloud-dev-pass` | Device and Simulator inspection, audio record retry, Alerts and Config. |
| `owner@autosound.dev` | `owner-dev-pass` | Owner-scoped dashboard and alerts; staff component pages are locked. |

A fresh seed creates car 1 with microphone `mic-1` and camera `cam-1`. The Compose edge client uses these seeded devices. A device created in F4 has a new random key; configure an edge process with that key before expecting it to heartbeat or acknowledge commands. Registration alone does not create a physical or virtual device process.

Open `/simulator?car_id=1` as IoT Staff. The local edge client begins with the route stopped. Its heartbeat should produce current telemetry and a labeled camera frame. **Start route** changes speed and position; **Stop route** stops movement. The status depends on a recent heartbeat, rather than the seeded `online` value.

## Seven minute scenario recording

Keep the API terminal and UI available before recording. Prepare any real scenario recordings in advance. Use a separate temporary device for the creation/deletion steps and keep `mic-1`/`cam-1` for the connected demonstration.

| Time | Presenter action | Visible result and explanation |
| --- | --- | --- |
| 0:00–0:25 | Introduce “Rishi Sheth, C2 IoT Device Manager and C3 Data Management”; show running services and sign in as IoT Staff. | Establish individual ownership, real backend and JWT role. |
| 0:25–1:25 | In F4, search/filter the inventory. Open **+ Add Device**, enter a temporary name, car 1, microphone, SSID, WPA2, password and DHCP. First submit a short password, then a valid one. | Inline validation, successful registration and one-time key. Save the key privately and close the key dialog. Explain that the new device remains registered until an edge process connects. |
| 1:25–2:35 | Open **Wi-Fi** for the connected `mic-1`. Show F5 facts. Enter a fresh Wi-Fi password and **Save and push to device**. Show the resulting command history. Queue **Stop capture**, **Start capture**, **Reboot** and **Sync config**; inspect their acknowledgements. | `configuring` and `sync_config` progress to acknowledged on the next heartbeat; password fields are never pre-filled. Controls travel through the device queue. In local mode Wi-Fi applies to the virtual device only. |
| 2:35–3:55 | Open F11. Show the mode label, current camera, GPS, heading and speed. Start the route, observe movement, then stop it. Select the five scenario categories and submit their events. | Camera/position update every two seconds; commands acquire delivered/acknowledged states. Explain the distinction between scenario trigger metadata and the model's predicted label. |
| 3:55–4:55 | Open F13. Refresh records, filter by car/device, inspect a new record and play its audio. | Cards and twelve-hour intake reflect new clips. Detail shows attempts, timestamps, state and the alert ID. Processing may finish between refreshes; the record detail and test output establish the lifecycle. |
| 4:55–5:35 | Log out, sign in as Cloud Staff, inspect the same record and open its generated alert. Open Config → Models. | Cloud Staff has no device mutation controls. The alert shows its model version; Config shows the active model and threshold. State that this is shared C4/C5 integration. |
| 5:35–6:35 | Show focused tests and the live verification command below. | Demonstrate invalid audio rejection, repeat-upload idempotency, authorization, retry guards, queue processing and actual audio download. For manual failed-record retry, use the focused unit demo; the live worker should not be deliberately broken. |
| 6:35–7:15 | Return as IoT Staff, rename and delete the temporary UI-created device. Show the confirmation wording. End on the working Simulator/monitor. | Queued commands are removed; existing audio records remain. Finish with the component UI and evidence visible. |

If a live CARLA server is available, use its camera and vehicle for the F11 portion. Otherwise name the mode **Local edge demo** in the recording. Show the test summary as it actually appears; do not substitute a prerecorded success message.

## Unit and live component verification

For host tests, activate the existing backend virtual environment and install its requirements if needed. The focused tests use isolated databases/transports, so they need no Docker, AWS account or running CARLA server.

```bash
cd backend
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest tests/test_devices.py tests/test_device_commands.py tests/test_device_data.py tests/test_edge_buffer.py tests/test_edge_client.py tests/test_demo_edge.py -q
python -m pytest -q
alembic heads
```

The tests cover device role/ownership rules; SSID/password validation and encryption; command delivery, duplicate detection, acknowledgement and expiry; audio type, size and duration validation; S3 failure without an accepted record; idempotent replay; lease recovery and stale-worker protection; automatic/manual retries; edge FIFO recovery; and five local scenarios through the packaged CNN. PostgreSQL-specific tests require a separate disposable database and may be skipped in the default isolated run.

For live verification, keep the deployment running and run from the repository root with the backend virtual environment available:

```bash
backend/.venv/bin/python scripts/verify_components.py \
  --base-url http://localhost:8000 \
  --output docs/deliverable3/evidence/live-verification.json
```

This command uses real HTTP, JWT login, databases, object storage, the worker and the deployed model. It creates one uniquely named temporary microphone, verifies its lifecycle and deletes only that device. Its accepted test-tone audio and linked alert remain as evidence. The JSON omits passwords, device keys, JWTs and signed URLs. The test tone checks ingestion and inference execution; use labeled real recordings to assess classification accuracy.

## Local edge demo and CARLA

| Mode | What actually runs | Audio and hardware boundary |
| --- | --- | --- |
| Local edge demo | A virtual vehicle and illustrated JPEG camera send real heartbeats, frames, commands and uploads to the deployed API. The UI labels this mode. | Five attributed ESC-50 recordings are bundled by default; `--synthetic-audio` selects test tones. Wi-Fi and reboot act only on the virtual device. |
| CARLA | The adapter attaches to an existing vehicle in a running, advancing CARLA world and optionally attaches its RGB camera. | The same five recorded scenarios are bundled by default; `--scenarios` supplies another mapping. Real OS Wi-Fi application needs a deployment-specific callback; remote OS reboot is reported unsupported. |

For CARLA, install the client version matching the server, start a vehicle in the world, register microphone/camera devices on the same car and export their one-time keys. From `backend/`:

```bash
export EDGE_API_URL=http://localhost:8000
export EDGE_CAR_ID=1
export EDGE_DEVICE_ID=1
export EDGE_DEVICE_KEY=dev-device-key-mic-1
export EDGE_CAMERA_DEVICE_ID=5
export EDGE_CAMERA_DEVICE_KEY=dev-device-key-cam-1
python -m edge.carla_client --actor-id 42 --camera
```

The shown IDs/keys are for a fresh development seed; replace them for another database. Actor `42` is an example and must be replaced with an existing vehicle ID. Stop the Compose `edge-demo` service before connecting a CARLA client to those same devices. CARLA RPC normally uses 2000; this adapter's Traffic Manager uses 8001 to avoid the API port. The adapter does not start the CARLA server or create the vehicle.

Both clients include their default recordings in the repository and image, so there are no missing scenario clips or runtime audio downloads in the standard setup. To use your own recordings, add `--scenarios .local_edge/scenarios.json` with a five-category mapping. See [edge client instructions](../../backend/edge/README.md), the [recording credits and license](../../backend/edge/demo_clips/README.md), and the [scenario mapping example](../setup/c2-c3/scenarios.example.json). Recordings must be valid, nonempty WAV/MP3 files, no larger than 10 MiB and no longer than 15 seconds. The client retains up to 200 pending clips on disk and resends them with stable clip IDs after connectivity returns.

## Observed recorded-scenario evidence

The following observations used the packaged `cnn-v1` model and every bundled recording on November 10, 2026. These are **illustrative single clips, not a held-out accuracy estimate**. The table includes the two incorrect, low-confidence results so the demonstration represents the model's actual behavior. Scenario selection supplies the upload's `trigger`; the CNN independently determines the predicted label and confidence.

| Selected scenario and actual recording | Observed predicted label | Observed confidence | Behavior at the active 0.75 threshold |
| --- | --- | --- | --- |
| Emergency: siren, `1-54084-A-42.wav` | `emergency` | 99.8% | Regular classification. |
| In vehicle: glass breaking, `1-20133-A-39.wav` | `in_vehicle` | 99.8% | Regular classification. |
| Human: crying baby, `1-211527-A-20.wav` | `human` | 90.4% | Regular classification. |
| Traffic: car horn, `1-17124-A-43.wav` | `in_vehicle` | 53.9% | Incorrect low-confidence prediction; alert enters the anomaly route. |
| Environmental: rain, `1-17367-A-10.wav` | `traffic` | 65.9% | Incorrect low-confidence prediction; alert enters the anomaly route. |

The final two cases demonstrate actual low-confidence handling: the model's confidence is below the active threshold, so the generated alert has `is_anomaly=true` while retaining its predicted sound type. Both recordings still traverse the normal authenticated upload, object storage, durable queue, processing and alert paths. The component demo reports those outcomes directly. Glass breaking represents an in-vehicle incident scenario; the source recording was not captured in the virtual car.

The five unchanged five-second WAVs total about 2.2 MB. Their [source manifest](../../backend/edge/demo_clips/manifest.json) records pinned upstream URLs, creators, original licenses and SHA-256 checksums. They are redistributed for this educational noncommercial demo under ESC-50's [CC BY-NC 3.0 dataset license](https://creativecommons.org/licenses/by-nc/3.0/), with [complete upstream notices](../../backend/edge/demo_clips/ESC-50-LICENSE.txt) retained.

## Submission evidence

Include the individual component source/commit, the actual test summary, a saved live verification JSON and the uploaded scenario video. Useful recording views are the F4 add form, F5 Wi-Fi plus acknowledged command, F11 moving/stopped route and live frame, F13 record/audio detail, and Cloud Staff's generated alert/model screen. Screenshots support the recording; they do not replace the required video upload.

## Verified on November 10, 2026

The implementation was exercised on the local machine using PostgreSQL 16.15, MongoDB 7.0.16, a persistent MinIO S3-compatible server, real JWT login, the background worker and the shipped `cnn-v1` classifier. The browser checks used the real API; UI mock mode was disabled.

| Check | Observed result |
| --- | --- |
| Complete automated suite | 508 passed, 14 skipped |
| Separate real PostgreSQL migrations, constraints and seed | 10 passed on an isolated throwaway database |
| Remaining optional model tests | Four tests need an unavailable audio dataset; they are outside the packaged-model inference checks, which passed |
| Browser | Device form/detail validation; all five scenarios; moving/stopped telemetry; raw audio playback; linked alert; model UI; Cloud read-only device view; no JavaScript errors |
| Frontend production build | Passed with Vite 7.3.8 and React Router 7.18.5 |
| Dependency audit | Zero npm vulnerabilities |
| Docker recipe | Official Compose parser accepted it; pinned MinIO binary extracted and executed; full container build/start remains untested because the host has no Docker engine |
| CARLA | Adapter and protocol tests passed; no actual CARLA server run was available. The recorded demo uses the explicitly labeled local virtual vehicle with real sound recordings |

See [verification summary](evidence/verification-summary.json), [complete test transcript](evidence/unit-tests.txt), [PostgreSQL transcript](evidence/postgres-migrations.txt), and [dependency audit](evidence/npm-audit.json). The task output package includes the scenario recording and screenshots. The recording is silent with on-screen explanations; audio playback was verified in the live browser. Review it and upload it through the course submission page; no course submission was performed automatically.

The captured scenario walkthrough is 3 minutes 17 seconds, with on-screen explanations, actual UI interactions, seven executed component checks and a live HTTP verification run. The longer presenter script above is available if you want to record your own narration. See the [captured unit checks](evidence/recorded-unit-demo.txt), [captured live checks](evidence/recorded-live-demo.txt), [browser result](evidence/browser-verification.json), and [recording metadata](evidence/video-metadata.json).
