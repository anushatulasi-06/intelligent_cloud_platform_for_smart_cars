#!/usr/bin/env python3
"""Extra C1/C4/C5 integration checks against an isolated local deployment only.

Requires Linux network isolation containing only loopback, literal 127.0.0.1 API/
MinIO URLs, and server-confirmed logged email/simulated backups. It restores the
subscription/model/alert-type settings it changes. It resolves ONLY the fresh alert
identified by a successful verify_components.py evidence file. The resulting fake
backup job and its two small MinIO objects remain as evidence. No real AWS, mail,
production backup, CARLA or arbitrary existing alert is exercised.

Credentials: VERIFY_OWNER/OWNER2/IOT/CLOUD_EMAIL/PASSWORD, defaulting to local seed
accounts. Local MinIO uses explicit AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY env
values; shared AWS credential/profile files and metadata discovery are never used.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit

import httpx


class VerificationError(RuntimeError):
    pass


def ensure(condition, message):
    if not condition:
        raise VerificationError(message)


def loopback_url(value):
    """No DNS, embedded credentials, URL fragments or external redirect targets."""
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError:
        raise VerificationError('A local URL is malformed') from None
    ensure(parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
           and parsed.username is None and parsed.password is None and not parsed.fragment,
           'Only literal http://127.0.0.1 local URLs are allowed')
    return value.rstrip('/')


def isolated_namespace(host_netns=None):
    ensure(sys.platform == 'linux', 'This verifier requires Linux network namespace isolation')
    try:
        interfaces = {row.split(':', 1)[0].strip()
                      for row in Path('/proc/net/dev').read_text().splitlines()[2:] if ':' in row}
        # Unprivileged user namespaces often cannot inspect PID1's namespace. The
        # launcher can pass the original namespace it captured before unshare.
        baseline = host_netns or os.readlink('/proc/1/ns/net')
        ensure(re.fullmatch(r'net:\[\d+\]', baseline), 'Invalid original network namespace identifier')
        isolated = os.readlink('/proc/self/ns/net') != baseline
    except OSError:
        raise VerificationError('Could not establish network namespace isolation') from None
    ensure(isolated and interfaces == {'lo'},
           'Refusing mutation: run in a separate network namespace containing only lo')
    return {'network_namespace_separate_from_original': True, 'interfaces': ['lo']}


class LocalPlatformVerification:
    def __init__(self, base_url, s3_url, bucket, component, car_id=1, timeout=60, host_netns=None):
        self.base = loopback_url(base_url)
        self.s3_url = loopback_url(s3_url)
        ensure(not urlsplit(self.base).query and not urlsplit(self.s3_url).query,
               'Base URLs must not contain query strings')
        ensure(urlsplit(self.s3_url).path in ('', '/'), 'MinIO endpoint must not contain a path')
        self.isolation = isolated_namespace(host_netns)
        ensure(component.get('result') == 'PASS' and component.get('alert_id')
               and component.get('retained_record_ids'), 'Successful fresh C2/C3 component evidence is required')
        self.component, self.car_id, self.timeout = component, car_id, timeout
        self.bucket = bucket
        self.client = httpx.Client(base_url=self.base, timeout=20, trust_env=False, follow_redirects=False)
        self.headers = {}
        self.restore = []
        self.evidence = {'author': 'Rishi Sheth', 'started_at': datetime.now(timezone.utc).isoformat(),
                         'scope': 'Isolated local C1/C4/C5 integration; simulated dump and logged email only',
                         'isolation': self.isolation, 'checks': [], 'cleanup': [],
                         'not_verified': ['AWS deployment/IAM/billing', 'real SES delivery',
                                          'real database backup/restore', 'production TLS', 'model accuracy']}

    def request(self, method, path, expected=200, role='cloud', **kwargs):
        ensure(path.startswith('/') and not path.startswith('//'), 'API paths must remain on the local server')
        headers = self.headers.get(role, {}) if role else {}
        try:
            response = self.client.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError:
            raise VerificationError(f'{method} {path}: local connection failed') from None
        statuses = (expected,) if isinstance(expected, int) else expected
        ensure(response.status_code in statuses,
               f'{method} {path}: expected HTTP {statuses}, received {response.status_code}')
        return response

    def get(self, path, role='cloud', **kwargs):
        return self.request('GET', path, role=role, **kwargs).json()

    def passed(self, message):
        self.evidence['checks'].append(message)
        print('PASS', message, flush=True)

    def login(self):
        for role in ('owner', 'owner2', 'iot', 'cloud'):
            default_password = {'owner': 'owner-dev-pass', 'owner2': 'owner2-dev-pass',
                                'iot': 'iot-dev-pass', 'cloud': 'cloud-dev-pass'}[role]
            response = self.request('POST', '/login', role=None, json={
                'email': os.getenv(f'VERIFY_{role.upper()}_EMAIL', f'{role}@autosound.dev'),
                'password': os.getenv(f'VERIFY_{role.upper()}_PASSWORD', default_password)}).json()
            expected_role = 'owner' if role.startswith('owner') else f'{role}_staff'
            ensure(response['role'] == expected_role, 'Login returned the wrong role')
            self.headers[role] = {'Authorization': 'Bearer ' + response['access_token']}
            ensure(self.get('/me', role)['role'] == expected_role, 'Profile role differs from login')
        self.request('GET', '/me', 401, role=None)
        self.passed('Four real JWT logins/profiles and unauthenticated profile rejection')

    def readiness(self):
        self.request('GET', '/admin/readiness', 403, role='iot')
        body = self.get('/admin/readiness')
        ensure(body.get('status') == 'ready', 'Readiness did not report all required components ready')
        ensure(body.get('scope', {}).get('side_effect_modes') == {'email': 'logged', 'backup': 'simulated'},
               'Refusing mutation: server must confirm logged email and simulated backups')
        self.evidence['side_effect_modes'] = body['scope']['side_effect_modes']
        self.passed('Authenticated stores/model/workers readiness and fake-side-effect safety gate')

    def dashboards_and_scope(self):
        owner = self.get('/dashboard/owner', 'owner')
        owner2 = self.get('/dashboard/owner', 'owner2')
        ids = {row['id'] for row in owner['cars']}
        ids2 = {row['id'] for row in owner2['cars']}
        ensure(self.car_id in ids and ids2 and not ids & ids2, 'Owner car scopes are not disjoint')
        fleet = self.get('/admin/fleet-summary')
        ensure(fleet['total_cars'] >= len(ids) + len(ids2), 'Fleet summary omitted scoped cars')
        for role, expected in (('owner', ids), ('owner2', ids2)):
            markers = self.get('/fleet/map', role)['cars']
            ensure({row['car_id'] for row in markers} == expected, 'Map leaked/omitted an owner car')
        track = self.get(f'/cars/{self.car_id}/track', 'owner')
        ensure(track['car_id'] == self.car_id and isinstance(track['points'], list), 'Track contract is invalid')
        self.request('GET', f'/cars/{self.car_id}/track', 404, role='owner2')
        self.request('GET', '/fleet/map', 403, role='iot')
        self.request('GET', '/admin/fleet-summary', 403, role='owner')
        now = datetime.now(timezone.utc)
        report = self.get('/admin/reports/alerts', params={'from': (now-timedelta(days=1)).isoformat(),
                                                         'to': now.isoformat(), 'group_by': 'sound_type'})
        ensure(report['group_by'] == 'sound_type' and sum(row['count'] for row in report['rows']) >= 1,
               'Report omitted the fresh generated alert')
        self.passed('Owner/fleet dashboards, scoped maps/tracking, report and cross-role/owner restrictions')

    def subscriptions(self):
        category = 'environmental'
        rows = self.get('/subscriptions', 'owner', params={'car_id': self.car_id})
        original = next((row for row in rows if row['alert_type'] == category), None)
        def restore_subscription():
            if original is not None:
                body = {key: original[key] for key in ('car_id', 'alert_type', 'enabled', 'notify_method')}
                restored = self.request('POST', '/subscriptions', role='owner', json=body).json()
                ensure(all(restored[key] == value for key, value in body.items()), 'Subscription restoration differs')
            else:
                current = self.get('/subscriptions', 'owner', params={'car_id': self.car_id})
                for row in current:
                    if row['alert_type'] == category:
                        self.request('DELETE', f"/subscriptions/{row['id']}", 204, role='owner')
                remaining = self.get('/subscriptions', 'owner', params={'car_id': self.car_id})
                ensure(not any(row['alert_type'] == category for row in remaining), 'Temporary subscription remained')
        self.restore.append(('Subscription restored', restore_subscription))
        body = {'car_id': self.car_id, 'alert_type': category, 'enabled': True, 'notify_method': 'in_app'}
        created = self.request('POST', '/subscriptions', (200, 201), role='owner', json=body).json()
        ensure(created['enabled'] and created['notify_method'] == 'in_app', 'Subscription was not persisted')
        body['enabled'] = False
        ensure(not self.request('POST', '/subscriptions', role='owner', json=body).json()['enabled'],
               'Subscription toggle did not persist')
        self.request('POST', '/subscriptions', 403, role='cloud', json=body)
        self.passed('Owner subscription create/update/toggle and staff-role rejection (in-app only)')

    def generated_alert(self):
        alert_id = self.component['alert_id']
        alert = self.get('/alerts/' + alert_id)
        ensure(alert['record_id'] in self.component['retained_record_ids'] and alert['car_id'] == self.car_id
               and alert['status'] == 'new', 'Refusing to transition an unrelated or previously modified alert')
        record = self.get('/device-data/' + alert['record_id'])
        ensure(record.get('trigger') == 'deliverable3_verification' and record.get('status') == 'processed',
               'Alert was not produced by this component-verification flow')
        stamp = datetime.fromisoformat(record['captured_at'].replace('Z', '+00:00'))
        ensure(abs((datetime.now(timezone.utc)-stamp).total_seconds()) < 3600,
               'Refusing to modify a stale verification alert')
        self.request('GET', '/alerts/' + alert_id, 404, role='owner2')
        self.request('GET', '/alerts/' + alert_id, 403, role='iot')
        link = self.get('/alerts/' + alert_id + '/audio', 'owner')
        ensure(link['expires_in'] == 300, 'Alert audio TTL differs from contract')
        url = loopback_url(link['url'])
        ensure(urlsplit(url).port == urlsplit(self.s3_url).port, 'Signed audio URL is not the local MinIO endpoint')
        try:
            audio = self.client.get(url)
        except httpx.HTTPError:
            raise VerificationError('Local signed alert audio could not be downloaded') from None
        ensure(audio.status_code == 200 and audio.content[:4] == b'RIFF', 'Fresh alert audio is not playable WAV data')
        self.request('PUT', '/alerts/' + alert_id, 409, json={'status': 'resolved'})
        self.request('PUT', '/alerts/' + alert_id, 403, role='owner', json={'status': 'resolved'})
        acknowledged = self.request('PUT', '/alerts/' + alert_id, role='owner', json={'status': 'acknowledged'}).json()
        ensure(acknowledged['status'] == 'acknowledged', 'Owner acknowledgement did not persist')
        resolved = self.request('PUT', '/alerts/' + alert_id,
                                json={'status': 'resolved', 'note': 'Isolated local verification alert only'}).json()
        ensure(resolved['status'] == 'resolved', 'Cloud resolution did not persist')
        self.request('PUT', '/alerts/' + alert_id, 409, json={'status': 'new'})
        self.evidence['resolved_verification_alert_id'] = alert_id
        self.evidence['generated_alert_audio_sha256'] = hashlib.sha256(audio.content).hexdigest()
        self.passed('Own fresh alert audio, owner acknowledge/cloud resolve, transition guards and owner isolation')

    def configuration(self):
        types = self.get('/admin/alert-types')
        source = next(row for row in types if isinstance(row.get('description'), str))
        category = source['alert_type']
        original = {key: source[key] for key in ('default_severity', 'description', 'enabled') if source[key] is not None}
        def restore_type():
            self.request('PUT', '/admin/alert-types/' + category, json=original)
            restored = next(row for row in self.get('/admin/alert-types') if row['alert_type'] == category)
            ensure(all(restored[key] == value for key, value in original.items()), 'Alert type restoration differs')
        self.restore.append(('Alert type restored', restore_type))
        description = 'Isolated local verification description'
        self.request('PUT', '/admin/alert-types/' + category, json={'description': description})
        ensure(next(row for row in self.get('/admin/alert-types') if row['alert_type'] == category)['description']
               == description, 'Alert type edit did not persist')
        models = self.get('/admin/models')
        active = [row for row in models if row['is_active']]
        ensure(len(active) == 1, 'Expected exactly one active trained model')
        model = active[0]
        def restore_threshold():
            self.request('PUT', f"/admin/models/{model['id']}", json={'confidence_threshold': model['confidence_threshold']})
            restored = next(row for row in self.get('/admin/models') if row['id'] == model['id'])
            ensure(restored['confidence_threshold'] == model['confidence_threshold'], 'Model threshold restoration differs')
        self.restore.append(('Model threshold restored', restore_threshold))
        temporary = .76 if model['confidence_threshold'] != .76 else .75
        changed = self.request('PUT', f"/admin/models/{model['id']}", json={'confidence_threshold': temporary}).json()
        ensure(changed['confidence_threshold'] == temporary, 'Model threshold edit did not persist')
        self.request('PUT', f"/admin/models/{model['id']}", 422, json={'confidence_threshold': .49})
        self.request('PUT', f"/admin/models/{model['id']}/activate", 409)
        self.request('GET', '/admin/models', 403, role='iot')
        self.evidence['model_activation_scope'] = 'Existing active model returns409; switching/rollback covered by unit tests'
        self.passed('Alert-type/model-threshold persistence, threshold validation and duplicate activation guard')

    def maintenance(self):
        health = self.get('/admin/db/health')
        ensure(all(health[name]['status'] in ('healthy', 'degraded') for name in ('postgres', 'mongodb', 's3')),
               'A database health probe is down')
        stores = self.get('/admin/db/stats')['stores']
        ensure({'users', 'cars', 'devices', 'audio', 'backups'} <= {row['name'] for row in stores},
               'Store statistics omitted required stores')
        for row in stores:
            ensure(row['count'] is not None, 'A store statistics probe failed')
        body = {'collection': 'car_positions', 'older_than_days': 365000, 'dry_run': True}
        self.request('POST', '/admin/db/retention/run', 422, json={**body, 'older_than_days': 29})
        self.request('POST', '/admin/db/retention/run', 403, role='owner', json=body)
        result = self.request('POST', '/admin/db/retention/run', json=body).json()
        ensure(result == {'candidates': 0, 'deleted': 0, 'archive_key': None}, 'Expected a retention no-op dry run')
        self.passed('Three-store health/statistics and retention minimum/role guards with zero-deletion dry run')
        self.fake_backup()

    def fake_backup(self):
        import boto3
        from botocore.config import Config
        # Explicit credentials prevent shared profile or metadata-provider discovery.
        key, secret = os.getenv('AWS_ACCESS_KEY_ID'), os.getenv('AWS_SECRET_ACCESS_KEY')
        ensure(key and secret and not key.startswith(('AKIA', 'ASIA')), 'Explicit local MinIO credentials are required')
        storage = boto3.client('s3', endpoint_url=self.s3_url, region_name='us-west-2',
                               aws_access_key_id=key, aws_secret_access_key=secret,
                               config=Config(signature_version='s3v4', proxies={},
                                             s3={'addressing_style': 'path'}, connect_timeout=3, read_timeout=5))
        try:
            queued = self.request('POST', '/admin/db/backup', 202).json()
            job_id = queued['job_id']
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                row = next((row for row in self.get('/admin/db/backups', params={'limit': 100})
                            if row['job_id'] == job_id), None)
                if row and row['status'] in ('succeeded', 'failed'):
                    ensure(row['status'] == 'succeeded', 'Simulated backup job failed')
                    break
                time.sleep(.25)
            else:
                raise VerificationError('Simulated backup job timed out')
            keys = [obj['Key'] for page in storage.get_paginator('list_objects_v2').paginate(
                    Bucket=self.bucket, Prefix='backups/') for obj in page.get('Contents', []) if job_id in obj['Key']]
            ensure(len(keys) == 2, 'Simulated backup did not produce exactly two local MinIO objects')
            downloaded = {name: storage.get_object(Bucket=self.bucket, Key=name)['Body'].read() for name in keys}
            pg = next(value for name, value in downloaded.items() if name.endswith('-postgres.dump'))
            mg = next(value for name, value in downloaded.items() if name.endswith('-mongo.archive.gz'))
            ensure(pg == b'PGDMP fake dev backup\n' and gzip.decompress(mg) == b'fake dev mongodump archive\n',
                   'Backup bytes do not identify simulated dumps; real backup was not authorized')
            self.evidence['simulated_backup'] = {'job_id': job_id, 'status': 'succeeded', 'real_dump': False,
                'objects': [{'key': name, 'sha256': hashlib.sha256(body).hexdigest(), 'size_bytes': len(body)}
                            for name, body in downloaded.items()]}
            self.passed('Simulated backup job completed and both exact fake dump objects downloaded from local MinIO')
        finally:
            storage.close()

    def run(self):
        self.login()
        self.readiness()
        self.dashboards_and_scope()
        self.subscriptions()
        self.generated_alert()
        self.configuration()
        self.maintenance()

    def cleanup(self):
        errors = []
        for label, restore in reversed(self.restore):
            try:
                restore()
                self.evidence['cleanup'].append(label)
            except Exception:
                errors.append(label)
        self.client.close()
        ensure(not errors, 'Could not restore all temporary local configuration changes')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--base-url', default='http://127.0.0.1:18000')
    parser.add_argument('--s3-endpoint-url', required=True)
    parser.add_argument('--s3-bucket', default=os.getenv('S3_BUCKET', 'autosound-local-qa'))
    parser.add_argument('--component-evidence', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--car-id', type=int, default=1)
    parser.add_argument('--timeout', type=float, default=60)
    parser.add_argument('--host-netns', help='Original net:[id] captured by the launcher before unshare')
    args = parser.parse_args(argv)
    verifier = None
    try:
        ensure(args.car_id > 0 and args.timeout > 0, 'Car id and timeout must be positive')
        component = json.loads(args.component_evidence.read_text())
        verifier = LocalPlatformVerification(args.base_url, args.s3_endpoint_url, args.s3_bucket,
                                             component, args.car_id, args.timeout, args.host_netns)
        verifier.run()
        verifier.evidence['status'] = 'passed'
    except Exception as exc:
        if verifier is None:
            print('FAIL ' + (str(exc) if isinstance(exc, VerificationError) else type(exc).__name__), file=sys.stderr)
            return 1
        verifier.evidence.update(status='failed', error=str(exc) if isinstance(exc, VerificationError) else type(exc).__name__)
    finally:
        if verifier is not None:
            try:
                verifier.cleanup()
            except VerificationError as exc:
                verifier.evidence.update(status='failed', cleanup_error=str(exc))
            verifier.evidence['finished_at'] = datetime.now(timezone.utc).isoformat()
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(verifier.evidence, indent=2) + '\n')
    print('PASS local platform verification' if verifier.evidence['status'] == 'passed'
          else 'FAIL local platform verification; see sanitized evidence', flush=True)
    return 0 if verifier.evidence['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
