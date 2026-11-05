"""/admin/db/*: health, stats, backups (single-backup guard, background job) and retention
(dry run, archive-before-delete, no delete when archiving fails, 30-day minimum)."""
import gzip
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import mongomock
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.db import mongo, postgres, s3
from app.db.models import Car, MaintenanceJob, User
from app.db.repositories import JobRepo
from app.db.repositories.base import MongoRepo
from app.main import create_app
from app.services import maintenance_service as svc

STAFF = {"X-Role": "cloud_staff", "X-User-Id": "7"}
NOW = datetime.now(timezone.utc)


# ---- fixtures ---------------------------------------------------------------------------

@pytest.fixture
def mdb(monkeypatch):
    db = mongomock.MongoClient(tz_aware=True)["autosound_test"]
    monkeypatch.setattr(mongo, "get_db", lambda: db)
    return db


class FakeS3:
    """Records uploads (with the uploaded bytes) in a shared event log."""

    def __init__(self, events):
        self.events = events
        self.objects: dict[str, bytes] = {}
        self.fail_upload = False

    def upload_file(self, path, key, content_type=None):
        if self.fail_upload:
            raise RuntimeError("S3 unavailable: AccessDenied")
        self.objects[key] = Path(path).read_bytes()
        self.events.append(("upload", key))
        return key

    def exists(self, key):
        return key in self.objects

    def jsonl(self, key) -> list[dict]:
        return [json.loads(line) for line in gzip.decompress(self.objects[key]).decode().splitlines()]


@pytest.fixture
def events():
    return []


@pytest.fixture
def fake_s3(monkeypatch, events):
    fake = FakeS3(events)
    monkeypatch.setattr(s3, "upload_file", fake.upload_file)
    monkeypatch.setattr(s3, "exists", fake.exists)
    return fake


@pytest.fixture
def delete_log(monkeypatch, events):
    """Wrap MongoRepo.delete_ids so deletes land in the same event log as uploads."""
    original = MongoRepo.delete_ids

    def logged(self, ids):
        n = original(self, ids)
        events.append(("delete", self.collection.name, len(ids)))
        return n

    monkeypatch.setattr(MongoRepo, "delete_ids", logged)


@pytest.fixture
def client(sql_db, mdb):
    with postgres.session_scope() as s:
        s.add(User(id=7, name="Staff", email="s@x.dev", role="cloud_staff", password_hash="h"))
    with TestClient(create_app()) as c:
        yield c


def add_positions(mdb, old: int, recent: int):
    docs = [{"car_id": 1, "ts": NOW - timedelta(days=40, seconds=i), "lat": 37.0, "lon": -121.0}
            for i in range(old)]
    docs += [{"car_id": 1, "ts": NOW - timedelta(days=1, seconds=i), "lat": 37.0, "lon": -121.0}
             for i in range(recent)]
    mdb["car_positions"].insert_many(docs)


def jobs(job_type=None):
    with postgres.session_scope() as s:
        q = select(MaintenanceJob)
        if job_type:
            q = q.where(MaintenanceJob.job_type == job_type)
        return [(j.job_type, j.status, j.params, j.result, j.error, j.created_by) for j in s.scalars(q)]


def assert_error(r, code):
    assert r.status_code == code, r.text
    assert set(r.json()) == {"detail"} and isinstance(r.json()["detail"], str)


# ---- roles ------------------------------------------------------------------------------

ROUTES = [("get", "/admin/db/health", None), ("get", "/admin/db/stats", None), ("post", "/admin/db/backup", None),
          ("get", "/admin/db/backups", None),
          ("post", "/admin/db/retention/run", {"collection": "car_positions", "older_than_days": 30})]


@pytest.mark.parametrize("method,url,body", ROUTES)
def test_roles(client, fake_s3, method, url, body):
    assert_error(client.request(method, url, json=body), 401)
    for role in ("owner", "iot_staff"):
        assert_error(client.request(method, url, json=body, headers={"X-Role": role}), 403)
    assert jobs() == []


# ---- health -----------------------------------------------------------------------------

def patch_pings(monkeypatch, pg, mg, s3_ms):
    def make(v):
        def ping():
            if v is None:
                raise ConnectionError("unreachable")
            return v
        return ping
    monkeypatch.setattr(postgres, "ping", make(pg))
    monkeypatch.setattr(mongo, "ping", make(mg))
    monkeypatch.setattr(s3, "ping", make(s3_ms))


def test_health_all_healthy(client, monkeypatch):
    patch_pings(monkeypatch, 4.2, 7.0, 31.5)
    r = client.get("/admin/db/health", headers=STAFF)
    assert r.status_code == 200
    body = r.json()
    assert body["postgres"]["status"] == "healthy" and body["postgres"]["latency_ms"] == 4.2
    assert set(body["postgres"]) == {"status", "latency_ms", "pool_in_use", "pool_size"}
    assert body["mongodb"] == {"status": "healthy", "latency_ms": 7.0}
    assert body["s3"] == {"status": "healthy", "latency_ms": 31.5}
    assert datetime.fromisoformat(body["checked_at"]).tzinfo is not None


def test_health_degraded_at_500ms(client, monkeypatch):
    patch_pings(monkeypatch, 499.9, 500.0, 1200.0)
    b = client.get("/admin/db/health", headers=STAFF).json()
    assert [b[k]["status"] for k in ("postgres", "mongodb", "s3")] == ["healthy", "degraded", "degraded"]


def test_health_down_stores_return_200_not_error(client, monkeypatch):
    patch_pings(monkeypatch, None, None, None)
    r = client.get("/admin/db/health", headers=STAFF)
    assert r.status_code == 200
    b = r.json()
    for k in ("postgres", "mongodb", "s3"):
        assert b[k]["status"] == "down" and b[k]["latency_ms"] is None


def test_health_real_sqlite_ping_and_pool_fields(client, monkeypatch):
    monkeypatch.setattr(mongo, "ping", lambda: 1.0)
    monkeypatch.setattr(s3, "ping", lambda: 1.0)
    b = client.get("/admin/db/health", headers=STAFF).json()
    assert b["postgres"]["status"] == "healthy" and b["postgres"]["latency_ms"] >= 0


# ---- stats ------------------------------------------------------------------------------

def test_stats_every_store(client, mdb, monkeypatch):
    add_positions(mdb, old=3, recent=2)
    mdb["alerts"].insert_one({"record_id": "r1", "timestamp": NOW - timedelta(days=5)})
    with postgres.session_scope() as s:
        s.add(Car(owner_id=7, make="Toyota", model="Camry", plate="P1"))
    monkeypatch.setattr(s3, "prefix_stats", lambda p: {"count": 2, "size_bytes": 2048,
                                                       "oldest": NOW - timedelta(days=9)})
    r = client.get("/admin/db/stats", headers=STAFF)
    assert r.status_code == 200
    stores = {s["name"]: s for s in r.json()["stores"]}
    assert [(s["name"], s["engine"]) for s in r.json()["stores"]] == [
        ("alerts", "mongodb"), ("device_data_records", "mongodb"), ("car_positions", "mongodb"),
        ("users", "postgresql"), ("cars", "postgresql"), ("devices", "postgresql"), ("subscriptions", "postgresql"),
        ("audio", "s3"), ("backups", "s3")]
    assert stores["car_positions"]["count"] == 5 and stores["alerts"]["count"] == 1
    assert stores["device_data_records"]["count"] == 0 and stores["device_data_records"]["oldest"] is None
    assert datetime.fromisoformat(stores["car_positions"]["oldest"]) < NOW - timedelta(days=39)
    assert stores["users"]["count"] == 1 and stores["cars"]["count"] == 1
    assert stores["audio"] == {"name": "audio", "engine": "s3", "count": 2, "size_bytes": 2048,
                               "oldest": stores["audio"]["oldest"]}


def test_stats_unreachable_store_shows_nulls(client, monkeypatch):
    def boom(_):
        raise ConnectionError("no S3")
    monkeypatch.setattr(s3, "prefix_stats", boom)
    monkeypatch.setattr(MongoRepo, "collection_stats", lambda self: (_ for _ in ()).throw(TimeoutError()))
    r = client.get("/admin/db/stats", headers=STAFF)
    assert r.status_code == 200
    stores = {s["name"]: s for s in r.json()["stores"]}
    assert stores["alerts"]["count"] is None and stores["audio"]["size_bytes"] is None
    assert stores["users"]["count"] == 1                       # PostgreSQL still reported


# ---- backups ----------------------------------------------------------------------------

def test_backup_fake_runs_in_background_and_succeeds(client, fake_s3):
    r = client.post("/admin/db/backup", headers=STAFF)
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    assert r.json() == {"job_id": job_id, "status": "queued"}
    # TestClient runs the BackgroundTask after sending the response
    [(jtype, status, params, result, error, created_by)] = jobs("backup")
    assert (jtype, status, error, created_by) == ("backup", "succeeded", None, 7)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert result["postgres_key"] == f"backups/{day}/{job_id}-postgres.dump"
    assert result["mongo_key"] == f"backups/{day}/{job_id}-mongo.archive.gz"
    assert result["size_bytes"] == sum(len(v) for v in fake_s3.objects.values()) > 0
    listed = client.get("/admin/db/backups", headers=STAFF).json()
    assert listed[0]["job_id"] == job_id and listed[0]["status"] == "succeeded"
    assert listed[0]["size_bytes"] == result["size_bytes"] and listed[0]["error"] is None
    assert listed[0]["started_at"] and listed[0]["finished_at"]


@pytest.mark.parametrize("state", ["queued", "running"])
def test_single_backup_guard_409(client, fake_s3, state):
    with postgres.session_scope() as s:
        job = JobRepo(s).create("backup")
        if state == "running":
            JobRepo(s).mark_running(job.id)
    assert_error(client.post("/admin/db/backup", headers=STAFF), 409)
    assert len(jobs("backup")) == 1 and fake_s3.objects == {}


def test_backup_allowed_again_after_previous_finished(client, fake_s3):
    assert client.post("/admin/db/backup", headers=STAFF).status_code == 202
    assert client.post("/admin/db/backup", headers=STAFF).status_code == 202
    assert [j[1] for j in jobs("backup")] == ["succeeded", "succeeded"]


def test_running_retention_does_not_block_backup(client, fake_s3):
    with postgres.session_scope() as s:
        JobRepo(s).mark_running(JobRepo(s).create("retention").id)
    assert client.post("/admin/db/backup", headers=STAFF).status_code == 202


def test_backup_upload_failure_marks_job_failed(client, fake_s3):
    fake_s3.fail_upload = True
    assert client.post("/admin/db/backup", headers=STAFF).status_code == 202
    [(_, status, _, _, error, _)] = jobs("backup")
    assert status == "failed" and "S3 unavailable" in error and len(error) <= 200
    assert client.get("/admin/db/backups", headers=STAFF).json()[0]["error"] == error


def test_real_backup_without_tools_fails_cleanly(client, fake_s3, monkeypatch):
    monkeypatch.setattr(get_settings(), "DEV_FAKE_BACKUP", False)
    monkeypatch.setattr(svc.shutil, "which", lambda name: None)
    client.post("/admin/db/backup", headers=STAFF)
    [(_, status, _, _, error, _)] = jobs("backup")
    assert status == "failed" and error == "pg_dump not found on this server"


def test_real_backup_keeps_credentials_off_the_command_line(client, fake_s3, monkeypatch):
    monkeypatch.setattr(get_settings(), "DEV_FAKE_BACKUP", False)
    monkeypatch.setattr(get_settings(), "DATABASE_URL", "postgresql+psycopg2://dbuser:s3cret@db.host:5433/autosound")
    monkeypatch.setattr(get_settings(), "MONGO_URI", "mongodb://muser:m0ngopass@mongo.host:27017/autosound")
    monkeypatch.setattr(svc.shutil, "which", lambda name: f"/usr/bin/{name}")
    calls = []

    class Done:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(argv, env, **kw):
        calls.append((argv, env))
        out = next(a.split("=", 1)[1] for a in argv if a.startswith("--archive=")) if "mongodump" in argv[0] \
            else argv[argv.index("-f") + 1]
        Path(out).write_bytes(b"dump-bytes")
        if "mongodump" in argv[0]:
            config = next(a.split("=", 1)[1] for a in argv if a.startswith("--config="))
            calls.append(("config", Path(config).read_text(), oct(Path(config).stat().st_mode & 0o777)))
        return Done()

    monkeypatch.setattr(svc.subprocess, "run", fake_run)
    client.post("/admin/db/backup", headers=STAFF)
    [(_, status, _, result, error, _)] = jobs("backup")
    assert status == "succeeded", error
    (pg_argv, pg_env), (mg_argv, _), (_, config_text, mode) = calls
    assert pg_argv[:2] == ["/usr/bin/pg_dump", "-Fc"]
    assert pg_env["PGPASSWORD"] == "s3cret" and pg_env["PGUSER"] == "dbuser" and pg_env["PGPORT"] == "5433"
    assert "--gzip" in mg_argv and any(a.startswith("--archive=") for a in mg_argv)
    argv_text = " ".join(pg_argv + mg_argv)
    assert "s3cret" not in argv_text and "m0ngopass" not in argv_text
    assert "m0ngopass" in config_text and mode == "0o600"
    assert result["size_bytes"] == 2 * len(b"dump-bytes")


def test_failing_dump_reports_short_error(client, fake_s3, monkeypatch):
    monkeypatch.setattr(get_settings(), "DEV_FAKE_BACKUP", False)
    monkeypatch.setattr(svc.shutil, "which", lambda name: f"/usr/bin/{name}")

    class Failed:
        returncode, stdout = 1, ""
        stderr = "pg_dump: error: connection failed\n" + "x" * 500

    monkeypatch.setattr(svc.subprocess, "run", lambda *a, **k: Failed())
    client.post("/admin/db/backup", headers=STAFF)
    [(_, status, _, _, error, _)] = jobs("backup")
    assert status == "failed" and error.startswith("pg_dump exited with 1") and len(error) <= 200
    assert fake_s3.objects == {}


def test_backups_list_order_and_limit(client, fake_s3):
    with postgres.session_scope() as s:
        repo = JobRepo(s)
        for i in range(3):
            j = repo.create("backup")
            repo.update(j.id, {"status": "succeeded", "started_at": NOW - timedelta(days=i),
                               "finished_at": NOW - timedelta(days=i), "result": {"size_bytes": 100 + i}})
        repo.create("retention")
    rows = client.get("/admin/db/backups", headers=STAFF).json()
    assert [r["size_bytes"] for r in rows] == [100, 101, 102]           # newest first, backups only
    assert set(rows[0]) == {"job_id", "status", "started_at", "finished_at", "size_bytes", "error"}
    assert len(client.get("/admin/db/backups?limit=2", headers=STAFF).json()) == 2
    for bad in (0, 101, "x"):
        assert_error(client.get(f"/admin/db/backups?limit={bad}", headers=STAFF), 422)


def test_startup_fails_interrupted_jobs(sql_db, mdb, monkeypatch):
    with postgres.session_scope() as s:
        repo = JobRepo(s)
        repo.create("backup")
        repo.mark_running(repo.create("retention").id)
        done = repo.create("backup")
        repo.mark_succeeded(done.id, {})
    monkeypatch.setattr(get_settings(), "FAIL_INTERRUPTED_JOBS_ON_STARTUP", True)
    with TestClient(create_app()):
        pass
    states = sorted((j[0], j[1], j[4]) for j in jobs())
    assert states == [("backup", "failed", "Interrupted by a server restart"), ("backup", "succeeded", None),
                      ("retention", "failed", "Interrupted by a server restart")]


# ---- retention --------------------------------------------------------------------------

def retention(client, **body):
    return client.post("/admin/db/retention/run", json=body, headers=STAFF)


def test_dry_run_counts_and_deletes_nothing(client, mdb, fake_s3, events):
    add_positions(mdb, old=7, recent=3)
    r = retention(client, collection="car_positions", older_than_days=30)          # dry_run defaults to true
    assert r.status_code == 200
    assert r.json() == {"candidates": 7, "deleted": 0, "archive_key": None}
    assert mdb["car_positions"].count_documents({}) == 10
    assert events == [] and fake_s3.objects == {}
    assert jobs("retention") == []                                                    # no job row for dry runs


def test_real_run_archives_before_deleting(client, mdb, fake_s3, events, delete_log):
    add_positions(mdb, old=2500, recent=5)
    old_ids = {str(d["_id"]) for d in mdb["car_positions"].find({"ts": {"$lt": NOW - timedelta(days=30)}})}
    r = retention(client, collection="car_positions", older_than_days=30, dry_run=False)
    assert r.status_code == 200, r.text
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"archive/car_positions/{day}.jsonl.gz"
    assert r.json() == {"candidates": 2500, "deleted": 2500, "archive_key": key}
    # order: one upload, THEN deletes in batches of 1000
    assert events == [("upload", key), ("delete", "car_positions", 1000), ("delete", "car_positions", 1000),
                      ("delete", "car_positions", 500)]
    archived = fake_s3.jsonl(key)
    assert {d["_id"]["$oid"] for d in archived} == old_ids                          # exactly the deleted docs
    assert "ts" in archived[0] and "lat" in archived[0]
    assert mdb["car_positions"].count_documents({}) == 5                            # recent ones kept
    [(jtype, status, params, result, error, by)] = jobs("retention")
    assert (jtype, status, by, error) == ("retention", "succeeded", 7, None)
    assert params["collection"] == "car_positions" and params["older_than_days"] == 30
    assert result == r.json()


def test_archive_failure_deletes_nothing(client, mdb, fake_s3, events, delete_log):
    add_positions(mdb, old=4, recent=1)
    fake_s3.fail_upload = True
    r = retention(client, collection="car_positions", older_than_days=30, dry_run=False)
    assert_error(r, 500)
    assert "nothing was deleted" in r.json()["detail"]
    assert mdb["car_positions"].count_documents({}) == 5
    assert [e for e in events if e[0] == "delete"] == []
    [(_, status, _, _, error, _)] = jobs("retention")
    assert status == "failed" and error.startswith("Archive failed, nothing deleted")


def test_delete_failure_after_archive_is_reported(client, mdb, fake_s3, monkeypatch):
    add_positions(mdb, old=1500, recent=0)
    original = MongoRepo.delete_ids
    calls = {"n": 0}

    def flaky(self, ids):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ConnectionError("mongo went away")
        return original(self, ids)

    monkeypatch.setattr(MongoRepo, "delete_ids", flaky)
    r = retention(client, collection="car_positions", older_than_days=30, dry_run=False)
    assert_error(r, 500)
    assert "after 1000 of 1500" in r.json()["detail"] and "archive/car_positions/" in r.json()["detail"]
    assert mdb["car_positions"].count_documents({}) == 500
    assert jobs("retention")[0][1] == "failed"


@pytest.mark.parametrize("days", [29, 0, -5])
def test_minimum_30_days(client, mdb, fake_s3, days):
    add_positions(mdb, old=3, recent=0)
    for dry in (True, False):
        r = retention(client, collection="car_positions", older_than_days=days, dry_run=dry)
        assert_error(r, 422)
        assert "older_than_days" in r.json()["detail"]
    assert mdb["car_positions"].count_documents({}) == 3 and jobs() == []


def test_exactly_30_days_allowed(client, mdb, fake_s3):
    add_positions(mdb, old=3, recent=0)
    assert retention(client, collection="car_positions", older_than_days=30).json()["candidates"] == 3


@pytest.mark.parametrize("body", [
    {"collection": "users", "older_than_days": 90},
    {"collection": "subscriptions", "older_than_days": 90},
    {"collection": "car_positions"},
    {"collection": "car_positions", "older_than_days": "90"},
    {"collection": "car_positions", "older_than_days": 90.5},
    {"collection": "car_positions", "older_than_days": 90, "force": True},
])
def test_retention_422_bad_body(client, fake_s3, body):
    assert_error(client.post("/admin/db/retention/run", json=body, headers=STAFF), 422)


def test_records_and_alerts_use_their_own_time_fields(client, mdb, fake_s3):
    old, new = NOW - timedelta(days=100), NOW - timedelta(days=2)
    mdb["device_data_records"].insert_many([{"record_id": "a", "received_timestamp": old},
                                            {"record_id": "b", "received_timestamp": new}])
    mdb["alerts"].insert_many([{"record_id": "a", "timestamp": old}, {"record_id": "b", "timestamp": old},
                               {"record_id": "c", "timestamp": new}])
    assert retention(client, collection="device_data_records", older_than_days=90).json()["candidates"] == 1
    r = retention(client, collection="alerts", older_than_days=90, dry_run=False)
    assert r.json()["deleted"] == 2 and mdb["alerts"].count_documents({}) == 1


def test_real_run_with_no_candidates_uploads_nothing(client, mdb, fake_s3, events):
    add_positions(mdb, old=0, recent=3)
    r = retention(client, collection="car_positions", older_than_days=30, dry_run=False)
    assert r.json() == {"candidates": 0, "deleted": 0, "archive_key": None}
    assert events == [] and jobs("retention")[0][1] == "succeeded"


def test_second_run_same_day_never_overwrites_archive(client, mdb, fake_s3):
    add_positions(mdb, old=2, recent=0)
    first = retention(client, collection="car_positions", older_than_days=30, dry_run=False).json()["archive_key"]
    add_positions(mdb, old=3, recent=0)
    second = retention(client, collection="car_positions", older_than_days=30, dry_run=False).json()["archive_key"]
    assert first != second and len(fake_s3.jsonl(first)) == 2 and len(fake_s3.jsonl(second)) == 3


def test_retention_defaults_documented():
    assert svc.RETENTION_DEFAULT_DAYS == {"car_positions": 30, "device_data_records": 90, "alerts": None}
    assert svc.RETENTION_MIN_DAYS == 30


# ---- dev backup local fallback (team decision 2026-11-04) ---------------------------------

def _no_s3(exc):
    def upload(path, key, content_type=None):
        raise exc
    return upload


def test_dev_backup_falls_back_to_local_folder_without_s3(client, monkeypatch, tmp_path):
    from botocore.exceptions import NoCredentialsError
    monkeypatch.setattr(s3, "upload_file", _no_s3(NoCredentialsError()))
    monkeypatch.setattr(get_settings(), "LOCAL_BACKUP_DIR", tmp_path)
    assert client.post("/admin/db/backup", headers=STAFF).status_code == 202
    [(_, status, _, result, error, _)] = jobs("backup")
    assert status == "succeeded" and error is None
    for key in ("postgres_key", "mongo_key"):
        assert result[key].startswith(f"local:{tmp_path}/backups/")
        assert Path(result[key].removeprefix("local:")).is_file()
    assert result["size_bytes"] > 0


def test_dev_backup_unreachable_endpoint_also_falls_back(client, monkeypatch, tmp_path):
    from botocore.exceptions import EndpointConnectionError
    monkeypatch.setattr(s3, "upload_file", _no_s3(EndpointConnectionError(endpoint_url="https://s3.example")))
    monkeypatch.setattr(get_settings(), "LOCAL_BACKUP_DIR", tmp_path)
    client.post("/admin/db/backup", headers=STAFF)
    assert jobs("backup")[0][1] == "succeeded"


def test_real_s3_errors_are_not_hidden_in_dev(client, monkeypatch, tmp_path):
    """AccessDenied is a real misconfiguration, not "S3 not set up": the job fails, nothing is written locally."""
    from botocore.exceptions import ClientError
    denied = ClientError({"Error": {"Code": "AccessDenied", "Message": "Access Denied"}}, "PutObject")
    monkeypatch.setattr(s3, "upload_file", _no_s3(denied))
    monkeypatch.setattr(get_settings(), "LOCAL_BACKUP_DIR", tmp_path)
    client.post("/admin/db/backup", headers=STAFF)
    [(_, status, _, _, error, _)] = jobs("backup")
    assert status == "failed" and "AccessDenied" in error and not any(tmp_path.iterdir())


def test_real_backups_never_fall_back(client, monkeypatch, tmp_path):
    from botocore.exceptions import NoCredentialsError
    monkeypatch.setattr(get_settings(), "DEV_FAKE_BACKUP", False)
    monkeypatch.setattr(svc.shutil, "which", lambda name: f"/usr/bin/{name}")

    class Done:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(argv, env, **kw):
        out = next(a.split("=", 1)[1] for a in argv if a.startswith("--archive=")) if "mongodump" in argv[0] \
            else argv[argv.index("-f") + 1]
        Path(out).write_bytes(b"dump")
        return Done()

    monkeypatch.setattr(svc.subprocess, "run", fake_run)
    monkeypatch.setattr(s3, "upload_file", _no_s3(NoCredentialsError()))
    monkeypatch.setattr(get_settings(), "LOCAL_BACKUP_DIR", tmp_path)
    client.post("/admin/db/backup", headers=STAFF)
    [(_, status, _, _, error, _)] = jobs("backup")
    assert status == "failed" and "credentials" in error.lower() and not any(tmp_path.iterdir())


def test_local_backup_dir_resolves_from_backend():
    from app.config import BACKEND_DIR, Settings
    assert Settings(_env_file=None).LOCAL_BACKUP_DIR == (BACKEND_DIR / ".local_backups").resolve()
