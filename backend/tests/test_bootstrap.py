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


def private_email_file(tmp_path, value, *, raw=False):
    path = tmp_path / "owner-emails.json"
    path.write_text(value if raw else json.dumps(value))
    path.chmod(0o600)
    return path


def test_optional_owner_recipient_updates_sql_and_private_login_bundle_only(sql_db, tmp_path, monkeypatch):
    from app.db.models import Subscription
    import boto3
    monkeypatch.setattr(seed, "BCRYPT_ROUNDS", 4)
    monkeypatch.setattr(boto3, "client", lambda *a, **k: pytest.fail("bootstrap contacted an AWS client"))
    source = private_email_file(tmp_path, {"owner@autosound.dev": "Primary+Demo@EXAMPLE.INVALID",
                                           "owner2@autosound.dev": "secondary@example.invalid"})
    before = source.read_bytes()
    target = tmp_path / "private" / "credentials.json"
    bootstrap.initialize(target, source)
    bundle = json.loads(target.read_text())
    identities = {row["bootstrap_identity"]: row for row in bundle["users"]}
    assert set(identities) == {row["email"] for row in seed.USERS}
    assert identities["owner@autosound.dev"]["email"] == "primary+demo@example.invalid"
    assert identities["owner2@autosound.dev"]["email"] == "secondary@example.invalid"
    with postgres.session_scope() as session:
        for row in bundle["users"]:
            user = session.get(User, row["id"])
            assert user.email == row["email"] and user.role == row["role"]
            assert bcrypt.checkpw(row["password"].encode(), user.password_hash.encode())
        for car_source in seed.CARS:
            car = session.scalar(select(Car).where(Car.plate == car_source["plate"]))
            assert car.owner_id == identities[car_source["owner"]]["id"]
        assert session.scalar(select(func.count()).select_from(Subscription)) == 0
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert source.read_bytes() == before


def test_primary_only_mapping_preserves_other_defaults(sql_db):
    with postgres.session_scope() as session:
        bundle = bootstrap.provision(session, {"owner@autosound.dev": "recipient@example.invalid"})
        users = {row["bootstrap_identity"]: row for row in bundle["users"]}
        assert users["owner@autosound.dev"]["email"] == "recipient@example.invalid"
        for source in seed.USERS:
            if source["email"] != "owner@autosound.dev":
                assert users[source["email"]]["email"] == source["email"]


def test_default_bundle_retains_explicit_logical_identities(sql_db, monkeypatch):
    monkeypatch.setattr(seed, "BCRYPT_ROUNDS", 4)
    with postgres.session_scope() as session:
        bundle = bootstrap.provision(session)
        assert all(row["email"] == row["bootstrap_identity"] for row in bundle["users"])


@pytest.mark.parametrize("value", [
    {}, [], {"unknown": "recipient@example.invalid"},
    {"iot@autosound.dev": "recipient@example.invalid"},
    {"owner@autosound.dev": "cloud@autosound.dev"},
    {"owner@autosound.dev": "owner2@autosound.dev"},
    {"owner@autosound.dev": "duplicate@example.invalid", "owner2@autosound.dev": "DUPLICATE@example.invalid"},
    {"owner@autosound.dev": None}, {"owner@autosound.dev": 42},
    {"owner@autosound.dev": "not-an-email"}, {"owner@autosound.dev": "recipient@localhost"},
    {"owner@autosound.dev": " recipient@example.invalid"},
    {"owner@autosound.dev": "recipient@example.invalid\nBcc: other@example.invalid"},
    {"owner@autosound.dev": ".recipient@example.invalid"},
    {"owner@autosound.dev": "recipient..name@example.invalid"},
    {"owner@autosound.dev": "recipient@-example.invalid"},
    {"owner@autosound.dev": "recipient@exam_ple.invalid"},
    {"owner@autosound.dev": "a" * 65 + "@example.invalid"},
    {"owner@autosound.dev": "a@" + "b" * 110 + ".invalid"},
])
def test_invalid_or_colliding_mapping_fails_before_database_access(tmp_path, monkeypatch, value):
    source = private_email_file(tmp_path, value)
    target = tmp_path / "credentials.json"
    monkeypatch.setattr(postgres, "session_scope", lambda: pytest.fail("invalid mapping accessed database"))
    with pytest.raises(ValueError) as error:
        bootstrap.initialize(target, source)
    for email in value.values() if isinstance(value, dict) else []:
        if isinstance(email, str) and email:
            assert email not in str(error.value)
    assert not target.exists()


@pytest.mark.parametrize("kind", ["mode644", "oversized", "duplicate_json", "malformed", "symlink", "directory", "fifo"])
def test_unsafe_private_input_is_rejected_before_database_access(tmp_path, monkeypatch, kind):
    import os
    source = private_email_file(tmp_path, {"owner@autosound.dev": "recipient@example.invalid"})
    if kind == "mode644":
        source.chmod(0o644)
    elif kind == "oversized":
        source.write_bytes(b" " * (bootstrap.MAX_OWNER_EMAIL_FILE_BYTES + 1))
    elif kind == "duplicate_json":
        source.write_text('{"owner@autosound.dev":"a@example.invalid","owner@autosound.dev":"b@example.invalid"}')
    elif kind == "malformed":
        source.write_text('{"owner@autosound.dev":')
    else:
        source.unlink()
        if kind == "symlink":
            real = private_email_file(tmp_path, {"owner@autosound.dev": "recipient@example.invalid"})
            real.rename(tmp_path / "real.json")
            source.symlink_to(tmp_path / "real.json")
        elif kind == "directory":
            source.mkdir(mode=0o600)
        else:
            os.mkfifo(source, mode=0o600)
    monkeypatch.setattr(postgres, "session_scope", lambda: pytest.fail("unsafe input accessed database"))
    with pytest.raises(ValueError):
        bootstrap.initialize(tmp_path / "credentials.json", source)


def test_optional_mapping_does_not_allow_reinitializing_existing_users(sql_db, tmp_path, monkeypatch):
    monkeypatch.setattr(seed, "BCRYPT_ROUNDS", 4)
    first = tmp_path / "original.json"
    bootstrap.initialize(first)
    before = first.read_bytes()
    source = private_email_file(tmp_path, {"owner@autosound.dev": "later@example.invalid"})
    with pytest.raises(ValueError, match="not empty"):
        bootstrap.initialize(tmp_path / "second.json", source)
    assert first.read_bytes() == before
    assert not (tmp_path / "second.json").exists()
    with postgres.session_scope() as session:
        assert session.scalar(select(User).where(User.email == "owner@autosound.dev")) is not None
        assert session.scalar(select(User).where(User.email == "later@example.invalid")) is None
