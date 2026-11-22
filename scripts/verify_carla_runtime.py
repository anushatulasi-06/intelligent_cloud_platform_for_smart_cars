#!/usr/bin/env python3
"""Verify a running real CARLA C2/C3 deployment for Rishi Sheth.

Run with the Python environment containing the server-matching carla SDK and httpx.
Queues capture, two Start/Stop route cycles and five bundled sound scenarios; retains
accepted records/alerts as evidence. Never spawns actors or changes CARLA settings.
Credentials use VERIFY_IOT_EMAIL/PASSWORD and VERIFY_CLOUD_EMAIL/PASSWORD, as in
verify_components.py. Tokens, passwords and signed URLs are never written to evidence.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from uuid import NAMESPACE_URL, uuid5

import httpx

REPO = Path(__file__).resolve().parents[1]


class VerificationError(RuntimeError):
    pass


def ensure(condition, message):
    if not condition:
        raise VerificationError(message)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://localhost:8000')
    parser.add_argument('--edge-api-url', default=os.getenv('EDGE_API_URL'),
                        help='Exact API URL configured on edge; defaults to --base-url (used for clip UUIDs)')
    parser.add_argument('--output', type=Path, default=REPO / 'artifacts/carla-runtime-verification.json')
    parser.add_argument('--car-id', type=int, default=1)
    parser.add_argument('--device-id', type=int, default=1, help='Attached microphone device id')
    parser.add_argument('--carla-host', default='127.0.0.1')
    parser.add_argument('--carla-port', type=int, default=2000)
    parser.add_argument('--smoke-evidence', type=Path)
    parser.add_argument('--timeout', type=float, default=90, help='Maximum seconds for each live check')
    args = parser.parse_args(argv)
    ensure(args.timeout > 0 and args.car_id > 0 and args.device_id > 0, 'Timeout and ids must be positive')
    try:
        import carla
    except ImportError:
        parser.error('Use a Python environment with the server-matching carla SDK installed')
    base = args.base_url.rstrip('/')
    edge_base = (args.edge_api_url or base).rstrip('/')
    evidence = {'author': 'Rishi Sheth', 'observed_at': datetime.now(timezone.utc).isoformat(),
                'mode': 'Actual CARLA vehicle and RGB camera connected to C2/C3 APIs',
                'car_id': args.car_id, 'device_id': args.device_id,
                'checks': [], 'scenarios': [], 'route_cycles': [], 'status': 'running',
                'boundaries': ['Wi-Fi and reboot target simulated CARLA units; no physical OS hook is claimed',
                               'Five licensed clips demonstrate integration, not held-out model accuracy']}
    route_active = False
    client = httpx.Client(base_url=base, timeout=30)
    iot = {}

    def check(label):
        evidence['checks'].append(label)
        print('PASS', label, flush=True)

    def request(method, path, headers=None, **kwargs):
        try:
            response = client.request(method, path, headers=headers, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPError:
            raise VerificationError(f'{method} {path.split("?")[0]} failed') from None

    def login(role):
        body = request('POST', '/login', json={
            'email': os.getenv(f'VERIFY_{role.upper()}_EMAIL', f'{role}@autosound.dev'),
            'password': os.getenv(f'VERIFY_{role.upper()}_PASSWORD', f'{role}-dev-pass')}).json()
        ensure(body.get('role') == f'{role}_staff', f'Expected {role} staff login')
        return {'Authorization': 'Bearer ' + body['access_token']}

    def get(path, headers=None, **kwargs):
        return request('GET', path, headers=iot if headers is None else headers, **kwargs).json()

    def state():
        result = get(f'/simulator/{args.car_id}/state')
        ensure(result.get('edge_mode') == 'carla' and result.get('online'), 'Real CARLA edge must be online')
        for timestamp in (result.get('frame_at'), (result.get('position') or {}).get('ts')):
            ensure(bool(timestamp), 'CARLA frame/position timestamp missing')
            age = time.time() - datetime.fromisoformat(timestamp.replace('Z', '+00:00')).timestamp()
            ensure(-2 <= age <= 15, 'CARLA frame/position is stale; cached heartbeats are insufficient')
        return result

    def frame(result):
        body = request('GET', result['frame_url']).content
        ensure(body.startswith(b'\xff\xd8\xff'), 'Signed camera object is not a JPEG')
        return body

    def command(name, payload=None):
        command_id = request('POST', f'/devices/{args.device_id}/commands', headers=iot,
                             json={'command': name, 'payload': payload or {}}).json()['command_id']
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            row = next((r for r in get(f'/devices/{args.device_id}/commands')
                        if r['command_id'] == command_id), None)
            if row and row['status'] in ('acknowledged', 'failed', 'expired'):
                ensure(row['status'] == 'acknowledged', f'{name} command {command_id} was not acknowledged')
                return command_id, row
            time.sleep(.5)
        raise VerificationError(f'{name} command {command_id} acknowledgement timed out')

    def start_route():
        nonlocal route_active
        before = state()
        initial_hash = hashlib.sha256(frame(before)).hexdigest()
        route_active = True  # Also attempt Stop if the acknowledgement check fails.
        start_id, _ = command('start_route')
        samples = [before['position']]
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            current = state()
            samples.append(current['position'])
            moving = max(float(p.get('speed_kmh') or 0) for p in samples) > 1
            shifted = len({(p['lat'], p['lon']) for p in samples}) > 1
            if moving and shifted and len(samples) >= 4:
                image = frame(current)
                if hashlib.sha256(image).hexdigest() != initial_hash:
                    return {'start_command_id': start_id, 'telemetry_samples': samples,
                            'max_speed_kmh': max(float(p.get('speed_kmh') or 0) for p in samples),
                            'camera_sha256': hashlib.sha256(image).hexdigest(),
                            'camera_size_bytes': len(image), 'town': current['position']['town']}
            time.sleep(1)
        raise VerificationError('Real route did not produce speed, GPS motion and a changed camera frame')

    def stop_route(cycle):
        nonlocal route_active
        stop_id, _ = command('stop_route')
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            current = state()
            if float(current['position']['speed_kmh']) < .1:
                cycle.update(stop_command_id=stop_id, stopped_speed_kmh=current['position']['speed_kmh'],
                             final_frame_at=current['frame_at'])
                route_active = False
                return
            time.sleep(.5)
        raise VerificationError('Real vehicle did not stop below 0.1 km/h')

    try:
        sdk = carla.Client(args.carla_host, args.carla_port)
        sdk.set_timeout(min(args.timeout, 30))
        server_version, client_version = sdk.get_server_version(), sdk.get_client_version()
        ensure(server_version == client_version, 'CARLA server and Python SDK versions must match exactly')
        world = sdk.get_world()
        snapshot = world.get_snapshot()
        next_snapshot = world.wait_for_tick(min(args.timeout, 30))
        ensure(next_snapshot.frame > snapshot.frame, 'Actual CARLA world is not advancing')
        evidence['carla'] = {'server_version': server_version, 'client_version': client_version,
                             'map': world.get_map().name, 'frames': [snapshot.frame, next_snapshot.frame]}
        check('Matching real CARLA SDK/server, actual town and advancing world frames (read-only SDK)')
        iot, cloud = login('iot'), login('cloud')
        initial = state()
        ensure(initial['position']['town'] == world.get_map().name.rsplit('/', 1)[-1],
               'API town differs from actual CARLA server town')
        frame(initial)
        check('Real JWT login, fresh CARLA heartbeat/telemetry and signed RGB JPEG')
        command('start_capture')
        first_cycle = start_route()
        evidence['route_cycles'].append(first_cycle)
        check('First Start route: actual speed, GPS motion and changed camera image')
        manifest = json.loads((REPO / 'backend/edge/demo_clips/manifest.json').read_text())
        for clip in manifest['clips']:
            category = clip['scenario']
            command_id, ack = command('simulate_event', {'scenario': category})
            expected = str(uuid5(NAMESPACE_URL, f'{edge_base}/devices/{args.device_id}/commands/{command_id}'))
            deadline = time.monotonic() + args.timeout
            record = None
            while time.monotonic() < deadline:
                rows = get('/device-data', params={'car_id': args.car_id, 'device_id': args.device_id,
                                                  'page_size': 100})['items']
                record = next((r for r in rows if r.get('clip_id') == expected), None)
                if record and record['status'] in ('processed', 'failed'):
                    break
                state()  # Require live camera/telemetry throughout model processing.
                time.sleep(.5)
            ensure(record and record['status'] == 'processed', f'{category}: matching command clip not processed')
            ensure(record['trigger'] == category and record.get('alert_id'), f'{category}: trigger/alert missing')
            url = get('/device-data/' + record['record_id'] + '/audio')['url']
            digest = hashlib.sha256(request('GET', url).content).hexdigest()
            ensure(digest == clip['sha256'], f'{category}: downloaded audio differs from licensed source')
            alert = get('/alerts/' + record['alert_id'], headers=cloud)
            evidence['scenarios'].append({'scenario': category, 'command_id': command_id,
                'acknowledgement': ack['status'], 'record_id': record['record_id'], 'clip_id': expected,
                'status': record['status'], 'audio_sha256': digest, 'alert_id': record['alert_id'],
                'model_prediction': alert.get('sound_type'), 'confidence': alert.get('confidence'),
                'severity': alert.get('severity'), 'model_version': alert.get('model_version')})
            check(category + ': real edge command → processed model alert → byte-identical audio download')
        stop_route(first_cycle)
        check('First Stop route acknowledged; actual speed below 0.1 km/h')
        second_cycle = start_route()
        evidence['route_cycles'].append(second_cycle)
        check('Second Start route resumes actual GPS motion and camera updates')
        stop_route(second_cycle)
        check('Second Stop route acknowledged; actual speed below 0.1 km/h')
        if args.smoke_evidence:
            evidence['smoke'] = json.loads(args.smoke_evidence.read_text())
        evidence['status'] = 'passed'
    except Exception as exc:
        evidence.update(status='failed', error_type=type(exc).__name__)
        print(f'FAIL {str(exc) if isinstance(exc, VerificationError) else type(exc).__name__}', file=sys.stderr)
    finally:
        if route_active and iot:
            try:
                command('stop_route')
                evidence['cleanup_stop_route'] = 'acknowledged'
            except Exception:
                evidence['cleanup_stop_route'] = 'failed'
        client.close()
        evidence['finished_at'] = datetime.now(timezone.utc).isoformat()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(evidence, indent=2) + '\n')
    print(f"Evidence: {args.output}", flush=True)
    return 0 if evidence['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
