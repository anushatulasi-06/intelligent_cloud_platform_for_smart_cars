"""Offline regressions for historical QA staging and local endpoint boundaries."""
import importlib.util
from pathlib import Path
import wave

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('verify_history_local', ROOT / 'scripts/verify_history_local.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def recordings(directory):
    directory.mkdir()
    for key in helper.media_module().expected_keys():
        with wave.open(str(directory / Path(key).name), 'wb') as output:
            output.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            output.writeframes(b'\0\0' * 160)


def test_stage_copies_exact25_without_disclosing_source_paths(tmp_path):
    source, target = tmp_path / 'private-source', tmp_path / 'private-runtime/staged'
    recordings(source)
    target.parent.mkdir(mode=0o700)
    manifest = helper.stage_audio(source, target, readable_mount=True)
    assert len(manifest['objects']) == 25
    assert 'file' not in repr(manifest) and str(source) not in repr(manifest)
    assert target.stat().st_mode & 0o777 == 0o755
    assert target.parent.stat().st_mode & 0o777 == 0o700
    for entry in manifest['objects']:
        path = target / entry['key']
        assert path.read_bytes() == (source / path.name).read_bytes()
        assert path.stat().st_mode & 0o777 == 0o444
        assert helper.media_module().describe(path)['sha256'] == entry['sha256']


def test_invalid_input_rejected_before_staging(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'staged'
    recordings(source)
    next(source.iterdir()).write_bytes(b'not a WAV')
    with pytest.raises(ValueError):
        helper.stage_audio(source, target)
    assert not target.exists()


def test_changed_bytes_during_copy_fail_closed(tmp_path, monkeypatch):
    source, target = tmp_path / 'source', tmp_path / 'staged'
    recordings(source)
    media = helper.media_module()
    build = media.build
    def changed(directory):
        manifest = build(directory)
        Path(manifest['objects'][0]['file']).write_bytes(b'changed after review')
        return manifest
    monkeypatch.setattr(media, 'build', changed)
    monkeypatch.setattr(helper, 'media_module', lambda: media)
    with pytest.raises((RuntimeError, ValueError)):
        helper.stage_audio(source, target)


@pytest.mark.parametrize('value', ['https://s3.amazonaws.com', 'http://minio.example.com:9000',
                                  'http://127.0.0.1.evil.test', 'http://user:pass@127.0.0.1'])
def test_object_endpoint_rejects_external_or_embedded_credentials(value):
    with pytest.raises(RuntimeError):
        helper.local_origin(value)


def test_idempotent_import_passes_explicit_reviewed_manifest(monkeypatch, tmp_path):
    from app.db import import_history
    manifest = {'schema': 1, 'objects': []}
    calls = []
    monkeypatch.setattr(import_history, 'validate_audio_dir', lambda path: manifest)
    def apply(path, **kwargs):
        calls.append(kwargs)
        return {'historical_alerts': 25, 'historical_records': 25,
                'objects_inserted': 25 if len(calls) == 1 else 0,
                'alerts_inserted': 25 if len(calls) == 1 else 0,
                'records_inserted': 25 if len(calls) == 1 else 0}
    monkeypatch.setattr(import_history, 'import_history', apply)
    assert helper.apply_local_history(tmp_path)['repeat_idempotent'] is True
    assert len(calls) == 2
    assert all(call == {'apply': True, 'local': True, 'manifest': manifest} for call in calls)


def identity_bundle(*, logical_identity=True):
    users = [
        {'id': 20 if logical_identity else 1, 'role': 'owner', 'email': 'primary@example.invalid',
         'password': 'private-primary', 'bootstrap_identity': 'owner@autosound.dev'},
        {'id': 10 if logical_identity else 2, 'role': 'owner', 'email': 'secondary@example.invalid',
         'password': 'private-secondary', 'bootstrap_identity': 'owner2@autosound.dev'},
        {'id': 30, 'role': 'iot_staff', 'email': 'iot@example.invalid', 'password': 'private-iot'},
        {'id': 40, 'role': 'cloud_staff', 'email': 'cloud@example.invalid', 'password': 'private-cloud'},
    ]
    if not logical_identity:
        for user in users:
            user.pop('bootstrap_identity', None)
    return {'users': list(reversed(users)), 'devices': [
        {'id': 51, 'car_id': 61, 'name': 'mic-1', 'device_key': 'private-mic'},
        {'id': 52, 'car_id': 61, 'name': 'cam-1', 'device_key': 'private-camera'}]}


@pytest.mark.parametrize('logical_identity', [True, False])
def test_private_owner_login_addresses_do_not_change_owner_identity(logical_identity):
    bundle = identity_bundle(logical_identity=logical_identity)
    env = helper.credentials_env(bundle)
    assert env['VERIFY_OWNER_EMAIL'] == 'primary@example.invalid'
    assert env['VERIFY_OWNER_PASSWORD'] == 'private-primary'
    assert env['VERIFY_OWNER2_EMAIL'] == 'secondary@example.invalid'
    assert env['VERIFY_OWNER2_PASSWORD'] == 'private-secondary'
    assert env['VERIFY_CAR_ID'] == '61'
    assert env['EDGE_DEVICE_KEY'] == 'private-mic'


def test_incomplete_logical_owner_mapping_fails_closed():
    bundle = identity_bundle()
    next(user for user in bundle['users'] if user['email'] == 'secondary@example.invalid')['bootstrap_identity'] = 'owner@autosound.dev'
    with pytest.raises(RuntimeError, match='logical identities'):
        helper.credentials_env(bundle)
