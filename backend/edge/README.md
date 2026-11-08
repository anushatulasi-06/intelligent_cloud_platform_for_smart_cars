# CARLA edge client

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

Edit `edge_scenarios.json`, or pass `--scenarios /path/to/scenarios.json`, to map each
of the five categories to a local WAV/MP3 recording. Paths are relative to the JSON
file. Supply your own clips: they must be non-empty, at most 10 MiB, and at most 15
seconds. A `simulate_event` payload uses `{"scenario": "emergency"}` (or another
category). The client forwards the selected recording; it does not synthesize sound
or record a physical microphone.

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
