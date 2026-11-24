"""Backup command regressions use subprocess fakes; actual restore has a separate local Docker proof."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from app.config import get_settings
from app.services import maintenance_service as svc


def test_pg_dump_preserves_verified_tls_session_options_and_query_host(monkeypatch, tmp_path):
    query = {"sslmode": "verify-full", "sslrootcert": "/certs/root CA.pem", "sslcert": "/certs/client.pem",
             "sslkey": "/certs/client.key", "options": "-csearch_path=app,public", "connect_timeout": "7",
             "application_name": "private backup", "host": "/tmp/postgres-socket"}
    monkeypatch.setattr(get_settings(), "DATABASE_URL",
                        "postgresql+psycopg2://owner:s3cret@ignored-host:5433/autosound?" + urlencode(query))
    calls = []
    monkeypatch.setattr(svc, "_run", lambda command, env: calls.append((command, env)))
    svc._dump_postgres(tmp_path / "postgres.dump")
    command, env = calls[0]
    assert command[:3] == ["pg_dump", "-Fc", "-w"]
    assert "s3cret" not in " ".join(command)
    assert env["PGHOST"] == "/tmp/postgres-socket" and env["PGDATABASE"] == "autosound"
    assert env["PGSSLMODE"] == "verify-full" and env["PGSSLROOTCERT"] == "/certs/root CA.pem"
    assert env["PGSSLCERT"] == "/certs/client.pem" and env["PGSSLKEY"] == "/certs/client.key"
    assert env["PGOPTIONS"] == "-csearch_path=app,public" and env["PGCONNECT_TIMEOUT"] == "7"
    assert env["PGAPPNAME"] == "private backup" and env["PGPASSWORD"] == "s3cret"


def test_pg_dump_normalizes_same_multihost_urls_as_application(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "DATABASE_URL",
        "postgresql+psycopg2://owner:private@/autosound?host=db1:5432&host=db2:5433&target_session_attrs=read-write")
    calls = []
    monkeypatch.setattr(svc, "_run", lambda command, env: calls.append(env))
    svc._dump_postgres(tmp_path / "postgres.dump")
    assert calls[0]["PGHOST"] == "db1,db2" and calls[0]["PGPORT"] == "5432,5433"
    assert calls[0]["PGTARGETSESSIONATTRS"] == "read-write"


def test_unrepresentable_pg_option_fails_before_tool_without_exposing_its_value(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "DATABASE_URL",
                        "postgresql+psycopg2://owner:private@db/autosound?sslpassword=hidden-tls-secret")
    monkeypatch.setattr(svc, "_run", lambda *args: pytest.fail("unsupported options must not be silently dropped"))
    with pytest.raises(RuntimeError, match="unsupported connection options") as error:
        svc._dump_postgres(tmp_path / "postgres.dump")
    assert "private" not in str(error.value) and "hidden-tls-secret" not in str(error.value)


@pytest.mark.parametrize("uri", [
    'mongodb://user:private%22value@host/autosound?authSource=admin',
    'mongodb://host/autosound"\npassword: "injected\\secret',
])
@pytest.mark.parametrize("tool_fails", [False, True])
def test_mongo_config_is_private_at_creation_escaped_and_removed(monkeypatch, tmp_path, uri, tool_fails):
    monkeypatch.setattr(get_settings(), "MONGO_URI", uri)
    real_open = os.open
    created = []
    def private_open(path, flags, mode=0o777, **kwargs):
        created.append((flags, mode))
        return real_open(path, flags, mode, **kwargs)
    monkeypatch.setattr(svc.os, "open", private_open)
    def run(command, env):
        config = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("--config=")))
        assert config.stat().st_mode & 0o777 == 0o600
        text = config.read_text()
        assert len(text.splitlines()) == 1
        assert json.loads(text.removeprefix("uri: ")) == uri
        assert uri not in " ".join(command)
        assert command[-1] == "--gzip"
        if tool_fails:
            raise RuntimeError("safe simulated tool failure")
    monkeypatch.setattr(svc, "_run", run)
    if tool_fails:
        with pytest.raises(RuntimeError):
            svc._dump_mongo(tmp_path / "mongo.archive.gz", tmp_path)
    else:
        svc._dump_mongo(tmp_path / "mongo.archive.gz", tmp_path)
    assert not (tmp_path / "mongodump.yaml").exists()
    assert created[0][0] & os.O_EXCL and created[0][1] == 0o600


def test_existing_mongo_config_or_symlink_is_not_overwritten(monkeypatch, tmp_path):
    sentinel = tmp_path / "preserve"
    sentinel.write_text("existing unrelated content")
    config = tmp_path / "mongodump.yaml"
    config.symlink_to(sentinel)
    monkeypatch.setattr(svc, "_run", lambda *args: pytest.fail("existing private config must not be used"))
    with pytest.raises(FileExistsError):
        svc._dump_mongo(tmp_path / "mongo.archive.gz", tmp_path)
    assert sentinel.read_text() == "existing unrelated content" and config.is_symlink()


def test_tool_failure_does_not_copy_provider_credentials_to_job_error(monkeypatch):
    monkeypatch.setattr(svc.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(svc.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=1, stdout="", stderr="mongodb://user:private-password@host/database\nconfig: private-token"))
    with pytest.raises(RuntimeError, match="mongodump exited with 1") as error:
        svc._run(["mongodump", "--config=/private/config"], {})
    assert "private-password" not in str(error.value) and "private-token" not in str(error.value)
