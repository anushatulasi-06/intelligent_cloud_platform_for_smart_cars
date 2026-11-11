#!/usr/bin/env bash
# Explicit CARLA 0.9.16 preparation/start/stop on a host already chosen by the user.
# Default action is status. Nothing here creates an account or reserves compute.
set -euo pipefail

action="${1:-status}"
render_mode="${2:-camera}"
kit_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
carla_task_root="${CARLA_TASK_ROOT:-$PWD/.local_carla}"
carla_python="${CARLA_CLIENT_PYTHON:-python3}"
carla_archive="$carla_task_root/CARLA_0.9.16.tar.gz"
carla_release="$carla_task_root/server"
carla_log="$carla_task_root/server.log"
carla_pidfile="$carla_task_root/server.pid"
carla_rpc_port="${CARLA_RPC_PORT:-2000}"
carla_url="https://downloads.carlasim.com/Linux/CARLA_0.9.16.tar.gz"
carla_bytes=8346095504

case "$action" in
    prepare)
        mkdir -p "$carla_task_root" "$carla_release"
        "$carla_python" - "$carla_task_root" <<'PY'
import shutil, sys
free = shutil.disk_usage(sys.argv[1]).free
if free < 25 * 1024**3:
    raise SystemExit('Require at least 25 GiB free for archive, extraction and evidence; also check provider storage quota/credits.')
PY
        current_bytes=0
        if [[ -f "$carla_archive" ]]; then current_bytes="$(stat -c %s "$carla_archive")"; fi
        if [[ "$current_bytes" != "$carla_bytes" ]]; then
            printf 'Downloading the official 8.35 GB archive on this selected host; resumable.\n'
            curl --fail --location --continue-at - --retry 3 --connect-timeout 20 \
                --output "$carla_archive" "$carla_url"
        fi
        [[ "$(stat -c %s "$carla_archive")" == "$carla_bytes" ]] || { printf 'Archive size mismatch.\n' >&2; exit 1; }
        if [[ ! -x "$carla_release/CarlaUE4.sh" ]]; then
            tar -xzf "$carla_archive" -C "$carla_release"
        fi
        [[ -x "$carla_release/CarlaUE4.sh" ]] || { printf 'CARLA launcher missing after extraction.\n' >&2; exit 1; }
        printf 'Prepared %s\n' "$carla_release"
        ;;
    start)
        [[ "$EUID" -ne 0 ]] || { printf 'Run Unreal as a regular user.\n' >&2; exit 1; }
        [[ -x "$carla_release/CarlaUE4.sh" ]] || { printf 'Run prepare on this host first.\n' >&2; exit 1; }
        if [[ -f "$carla_pidfile" ]] && kill -0 "$(cat "$carla_pidfile")" 2>/dev/null; then
            printf 'A server started by this kit is already running.\n' >&2; exit 1
        fi
        case "$render_mode" in
            camera)
                "$carla_python" "$kit_dir/probe.py" --work-dir "$carla_task_root" --require-gpu --output "$carla_task_root/host-probe.json"
                flags=(-RenderOffScreen -quality-level=Low -nosound -ResX=320 -ResY=180 -graphicsadapter=0)
                ;;
            software)
                "$carla_python" "$kit_dir/probe.py" --work-dir "$carla_task_root" --output "$carla_task_root/host-probe.json"
                flags=(-RenderOffScreen -quality-level=Low -nosound -ResX=320 -ResY=180 -graphicsadapter=0)
                ;;
            physics) flags=(-nullrhi -nosound) ;;
            *) printf 'Render mode must be camera, software or physics.\n' >&2; exit 2 ;;
        esac
        "$carla_python" - "$carla_release/CarlaUE4.sh" "$carla_log" "$carla_pidfile" "$carla_rpc_port" "${flags[@]}" <<'PY'
from pathlib import Path
import socket, subprocess, sys
launcher, log, pidfile, port, *flags = sys.argv[1:]
try:
    with socket.create_connection(('127.0.0.1', int(port)), timeout=1):
        raise SystemExit('RPC port is already in use; this kit will not stop another server.')
except OSError:
    pass
with open(log, 'ab', buffering=0) as output:
    process = subprocess.Popen([launcher, *flags, f'-carla-rpc-port={port}'], stdin=subprocess.DEVNULL,
                               stdout=output, stderr=subprocess.STDOUT, start_new_session=True, cwd=Path(launcher).parent)
Path(pidfile).write_text(str(process.pid) + '\n')
print('Started owned process group', process.pid, '; log:', log)
PY
        "$carla_python" - "$carla_rpc_port" "$carla_log" <<'PY'
import socket, sys, time
port = int(sys.argv[1])
deadline = time.monotonic() + 180
while time.monotonic() < deadline:
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=1):
            print('CARLA RPC port is open; run the real client smoke test next.')
            break
    except OSError:
        time.sleep(2)
else:
    raise SystemExit('RPC did not become ready; inspect ' + sys.argv[2] + '. Server retained for diagnosis; use stop for this kit-owned group.')
PY
        # This kit has just created this server. Fix the simulation timestep so
        # autopilot/physics stay stable when an integrated GPU renders slowly.
        "$carla_python" - "$carla_rpc_port" "${CARLA_MAP:-}" <<'PY_STEP'
import carla, sys
client = carla.Client('127.0.0.1', int(sys.argv[1]))
client.set_timeout(120)
world = client.get_world()
if sys.argv[2] and world.get_map().name.rsplit('/', 1)[-1] != sys.argv[2]:
    world = client.load_world(sys.argv[2])
settings = world.get_settings()
settings.fixed_delta_seconds = 0.05
settings.substepping = True
settings.max_substep_delta_time = 0.01
settings.max_substeps = 10
world.apply_settings(settings)
print('Configured this kit-owned CARLA server:', world.get_map().name, '0.05 s physics timestep.')
PY_STEP
        ;;
    stop)
        [[ -f "$carla_pidfile" ]] || { printf 'No kit-owned server PID recorded.\n'; exit 0; }
        "$carla_python" - "$carla_pidfile" "$carla_release" "$carla_rpc_port" <<'PYSTOP'
from pathlib import Path
import os, signal, socket, sys, time
pidfile, release, port = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
pid = int(pidfile.read_text())
try:
    command = Path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
except FileNotFoundError:
    command = None
if command is not None:
    if release not in command or os.getpgid(pid) != pid:
        raise SystemExit('PID identity no longer matches this kit; refusing to signal it.')
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass

def active_group():
    # Ignore exited zombies while retaining any live child of the owned group.
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            if int(fields[2]) == pid and fields[0] != 'Z':
                return True
        except (OSError, ValueError, IndexError):
            continue
    return False

def rpc_open():
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=1):
            return True
    except OSError:
        return False

deadline = time.monotonic() + 90
while time.monotonic() < deadline:
    if not active_group() and not rpc_open():
        pidfile.unlink(missing_ok=True)
        print('Owned CARLA process group and RPC port stopped.')
        break
    time.sleep(.25)
else:
    raise SystemExit('Owned CARLA server has not fully stopped; PID retained. Inspect server.log before retrying.')
PYSTOP
        ;;
    status)
        printf 'Runtime directory: %s\n' "$carla_task_root"
        if [[ -f "$carla_pidfile" ]]; then
            ps -p "$(cat "$carla_pidfile")" -o pid,etime,args || true
        else
            printf 'No server started by this kit.\n'
        fi
        ;;
    *) printf 'Usage: launch.sh {prepare|start [camera|software|physics]|stop|status}\n' >&2; exit 2 ;;
esac
