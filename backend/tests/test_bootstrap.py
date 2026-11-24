"""Private bootstrap regression: random credentials, empty DB only, private file."""
import hashlib
import json
import stat
from contextlib import contextmanager

import bcrypt
import pytest
from sqlalchemy import func, select

from app.db import bootstrap, postgres, seed
from app.db.models import AlertTypeConfig, Car, Device, ModelVersion, User


def test_private_bootstrap_has_no_public_passwords_or_device_keys(sql_db, monkeypatch):
    monkeypatch.setattr(seed, "BCRYPT_ROUNDS", 4)
    with postgres.session_scope() as session:
        bundle = bootstrap.provision(session)
        users = {u.id: u for u in session.scalars(select(User))}
        devices = {d.id: d for d in session.scalars(select(Device))}
        assert len(users) == 4 and len(devices) == 5
        assert session.scalar(select(func.count()).select_from(Car)) == 4
        assert session.scalar(select(func.count()).select_from(AlertTypeConfig)) == 5
        assert session.scalar(select(ModelVersion)).is_active
        public_passwords = {u["password"] for u in seed.USERS}
        for private in bundle["users"]:
            assert private["password"] not in public_passwords
            assert bcrypt.checkpw(private["password"].encode(), users[private["id"]].password_hash.encode())
        for private in bundle["devices"]:
            assert private["device_key"] != f"dev-device-key-{private['name']}"
            assert devices[private["id"]].device_key_hash == hashlib.sha256(private["device_key"].encode()).hexdigest()
    with postgres.session_scope() as session:
        before = {u.id: u.password_hash for u in session.scalars(select(User))}
        with pytest.raises(ValueError, match="not empty"):
            bootstrap.provision(session)
        assert before == {u.id: u.password_hash for u in session.scalars(select(User))}


def test_initialize_writes_private_bundle_and_refuses_replacement(sql_db, tmp_path, monkeypatch):
    monkeypatch.setattr(seed, "BCRYPT_ROUNDS", 4)
    target = tmp_path / "private" / "credentials.json"
    bootstrap.initialize(target)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    bundle = json.loads(target.read_text())
    assert len(bundle["users"]) == 4 and len(bundle["devices"]) == 5
    before = target.read_bytes()
    with pytest.raises(ValueError, match="existing credential"):
        bootstrap.initialize(target)
    assert target.read_bytes() == before


def test_nonempty_database_leaves_existing_data_and_no_file(sql_db, tmp_path):
    with postgres.session_scope() as session:
        session.add(User(name="Existing", email="existing@example.invalid", role="owner", password_hash="keep"))
    target = tmp_path / "credentials.json"
    with pytest.raises(ValueError, match="not empty"):
        bootstrap.initialize(target)
    assert not target.exists()
    with postgres.session_scope() as session:
        assert session.scalar(select(User)).password_hash == "keep"
        assert session.scalar(select(func.count()).select_from(Car)) == 0


def test_file_write_failure_rolls_back_database(sql_db, tmp_path, monkeypatch):
    monkeypatch.setattr(seed, "BCRYPT_ROUNDS", 4)
    def fail(*args, **kwargs):
        raise PermissionError("simulated private file failure")
    monkeypatch.setattr(bootstrap.os, "open", fail)
    with pytest.raises(PermissionError):
        bootstrap.initialize(tmp_path / "credentials.json")
    with postgres.session_scope() as session:
        assert session.scalar(select(func.count()).select_from(User)) == 0


def test_uncertain_commit_preserves_only_copy_of_committed_credentials(sql_db, tmp_path, monkeypatch):
    """Model a database commit followed by a lost acknowledgement, entirely in SQLite."""
    monkeypatch.setattr(seed, "BCRYPT_ROUNDS", 4)
    real_scope = postgres.session_scope
    @contextmanager
    def commit_then_lose_acknowledgement():
        with real_scope() as session:
            yield session
        raise ConnectionError("simulated lost commit acknowledgement")
    target = tmp_path / "private" / "credentials.json"
    monkeypatch.setattr(postgres, "session_scope", commit_then_lose_acknowledgement)
    with pytest.raises(ConnectionError):
        bootstrap.initialize(target)
    bundle = json.loads(target.read_text())
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    with real_scope() as session:
        assert session.scalar(select(func.count()).select_from(User)) == 4
        for private in bundle["users"]:
            user = session.get(User, private["id"])
            assert bcrypt.checkpw(private["password"].encode(), user.password_hash.encode())
    # An operator must review the ambiguous outcome; retries must not replace credentials.
    before = target.read_bytes()
    with pytest.raises(ValueError, match="existing credential"):
        bootstrap.initialize(target)
    assert target.read_bytes() == before


def test_partial_bundle_is_removed_and_database_rolls_back(sql_db, tmp_path, monkeypatch):
    monkeypatch.setattr(seed, "BCRYPT_ROUNDS", 4)
    def partial_write_then_fail(bundle, stream, **kwargs):
        stream.write('{"users": [')
        raise OSError("simulated interrupted credential write")
    monkeypatch.setattr(bootstrap.json, "dump", partial_write_then_fail)
    target = tmp_path / "private" / "credentials.json"
    with pytest.raises(OSError):
        bootstrap.initialize(target)
    assert not target.exists()
    with postgres.session_scope() as session:
        assert session.scalar(select(func.count()).select_from(User)) == 0


def test_bootstrap_refuses_symlink_output_without_touching_referenced_file(sql_db, tmp_path):
    existing = tmp_path / "existing.json"
    existing.write_text("preserve")
    target = tmp_path / "credentials.json"
    target.symlink_to(existing)
    with pytest.raises(ValueError, match="symbolic link"):
        bootstrap.initialize(target)
    assert existing.read_text() == "preserve"
    with postgres.session_scope() as session:
        assert session.scalar(select(func.count()).select_from(User)) == 0
