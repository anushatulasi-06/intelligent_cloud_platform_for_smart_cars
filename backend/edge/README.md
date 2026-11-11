# Edge clients for C2/C3

## Runnable local component demo

`edge.demo_client` is the local alternative when a CARLA server is unavailable. It
connects a **virtual** vehicle to the actual API, sends illustrated JPEG camera
frames and GPS heartbeats, executes queued commands, and uploads audio through the
normal C3 pipeline. It uses the real databases, object storage, processing worker
and active model. The Simulator labels this mode **Local edge demo** using the
`local-demo-real-v1` or `local-demo-synth-v1` firmware heartbeat; the camera itself is visibly labeled as an
illustration. This mode does not claim to be CARLA.

Start the API and its stores first. From `backend/`, with backend dependencies
installed and registered devices on the same car:

```bash
export EDGE_API_URL=http://localhost:8000
export EDGE_CAR_ID=1
export EDGE_DEVICE_ID=1
export EDGE_DEVICE_KEY=dev-device-key-mic-1
export EDGE_CAMERA_DEVICE_ID=5
export EDGE_CAMERA_DEVICE_KEY=dev-device-key-cam-1
python -m edge.demo_client
```

Those ids and keys are for a **fresh development seed only**. For devices created
through the UI, replace them with the actual ids and the one-time keys shown at
registration. The client stops with an error if a device key is rejected or its
device was deleted. `--no-camera` runs position and audio only. The client starts
with the route stopped; open Simulator as IoT Staff and click **Start route** to see
the position, speed and illustrated road change. **Stop route** halts movement.
`--heartbeat-seconds 2` is the default; valid values are 0.2 to 30 seconds.

Five attributed, unchanged 5-second **ESC-50 recordings** are bundled in
`demo_clips/`: a siren, glass breaking, a crying baby, a car horn, and rain. The
default client needs no runtime download. See [the recording credits and
license](demo_clips/README.md) for the CC BY-NC 3.0 educational-use terms and original
creators. Selecting a scenario sends that category as `trigger` metadata; the CNN
predicts independently. These few clips do not establish model accuracy, and no
claim is made that they were excluded from model training.

To use your own recordings, supply a five-category JSON mapping as described below:

```bash
python -m edge.demo_client --scenarios .local_edge/scenarios.json
```

For a purely synthetic ingestion fixture, use `--synthetic-audio`. It generates
five distinct valid 3-second WAV test tones in `.local_edge/demo/synthetic-clips/`.
The camera and firmware explicitly identify this mode. The tones are not genuine
sirens, speech or model accuracy evidence. `--synthetic-audio` and `--scenarios`
are mutually exclusive.

The local virtual device accepts Wi-Fi changes and acknowledges virtual reboot;
neither action changes the host network or reboots the machine. Their command
details explicitly say this. Saved Wi-Fi configuration remains encrypted, and the
normal edge buffer retains pending audio across restarts. `--buffer-dir` selects a
separate persistent buffer for a different car/device pair. Do not share one
buffer between concurrently running clients.

The integration test in `tests/test_demo_edge.py` drives real HTTP routes for
registration, encrypted configuration, movement, camera frames, all five uploads,
queue processing and the shipped `cnn-v1` model. Stores are isolated in that test;
the runnable client always uses the configured API and its actual services.

## CARLA client

This optional client connects an **existing** CARLA vehicle to the platform. It sends
position heartbeats every two seconds, executes the device command queue, forwards
scenario audio through `/device-data`, and optionally sends camera frames. It never
spawns a vehicle, changes the world settings, or destroys the supplied vehicle.

Install the backend requirements first, then install the CARLA Python client version
that matches your running simulator. Camera support additionally needs `Pillow`:

```bash
# From backend/, with its virtual environment active:
pip install 'carla==<your-server-version>' Pillow
```

Register a microphone device through `POST /devices` and save its one-time key. The
car id must match that device. Export these environment variables in your terminal;
keys are never supplied as command-line arguments or written to the audio buffer:

```bash
export EDGE_API_URL=http://localhost:8000
export EDGE_CAR_ID=1
export EDGE_DEVICE_ID=6
read -rs -p 'Microphone device key: ' EDGE_DEVICE_KEY; echo
export EDGE_DEVICE_KEY
python -m edge.carla_client --actor-id 42
```

Replace `42` with an existing CARLA vehicle actor id. Use HTTPS for a deployed API.
The simulator must already be advancing; this adapter does not tick or reconfigure
the world. Start/stop route enables/disables autopilot for that actor; stop also
applies the brake. The vehicle's autopilot state is left as commanded on client exit.
Traffic Manager uses port 8001 to avoid the API's port 8000; use
`--traffic-manager-port` if your simulator already uses a different port.

For a camera, register a camera device on the same car, export
`EDGE_CAMERA_DEVICE_ID` and `EDGE_CAMERA_DEVICE_KEY`, then add `--camera`. The client
attaches one 640x360 RGB camera sensor, sends JPEG frames about every two seconds,
and stops/destroys only that sensor on exit.

## Audio scenarios and buffering

`edge_scenarios.json` defaults to the same five bundled, attributed ESC-50
recordings. Edit it, or pass `--scenarios /path/to/scenarios.json`, to use your own
WAV/MP3 recordings. Paths are relative to the JSON file. Clips must be non-empty,
at most 10 MiB, and at most 15 seconds. A `simulate_event` payload uses
`{"scenario": "emergency"}` (or another category). The CARLA client forwards the
selected recording; it does not synthesize sound or record a physical microphone.

On the microphone, `start_capture` and `stop_capture` enable/disable accepting these
explicit scenario captures. On the camera, they toggle frame forwarding. Both start
enabled. Accepted clips are first saved to the persistent
FIFO in `.local_edge/`, then uploaded with a stable `clip_id`. A scenario ACK means
the clip is saved locally; classification remains asynchronous. Upload failures keep
the clip for the next pass. Restart with the same buffer directory and device ids to
resume. The buffer holds 200 clips; when full, new scenarios fail rather than drop
recordings. Each heartbeat pass uploads at most one buffered clip and retries at
most two ACKs, so a backlog cannot monopolize the loop. ACK failures are retried
while the client is running. Existing buffered
clips continue uploading even when capture is stopped.

## Wi-Fi and unsupported hardware commands

`sync_config` decrypts the heartbeat's `password_enc` using the registered device key
and saves the **encrypted** settings in a private mode-0600 file under
`.local_edge/wifi/`. By default it reports that settings were saved but OS application
is unsupported. It never runs network configuration commands or claims that Wi-Fi
was applied. An edge deployment can construct `CarlaEdgeClient(...,
wifi_apply=callback)`; the callback receives `(device_id, decoded_settings)` and must
return `True` only after successfully applying them. Do not log those settings.

`reboot` is explicitly reported as unsupported; no OS reboot command is executed.
Unknown device ids/keys stop the client with a concise error. Keep keys and runtime
state out of Git; `.local_edge/` is ignored.

Implementation uses the official [CARLA Python API](https://carla.readthedocs.io/en/latest/python_api/)
and [RGB camera sensor format](https://carla.readthedocs.io/en/latest/ref_sensors/#rgb-camera).
Backend tests use fake actors and HTTP transport; an actual CARLA run still requires
your running simulator, registered devices, databases, S3, and audio files.
