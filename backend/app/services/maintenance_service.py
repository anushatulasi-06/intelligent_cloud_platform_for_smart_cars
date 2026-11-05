"""Database maintenance (C5, F14): store health, store stats, backups and retention.

Retention defaults (what a scheduled purge would use; the API always takes an explicit value):
    car_positions        30 days
    device_data_records  90 days
    alerts               never auto-purged (only removed by an explicit retention run)
Every run, whatever the collection, must use at least RETENTION_MIN_DAYS (30).

Retention order is archive FIRST, delete SECOND: matching documents are written to
s3://BUCKET/archive/<collection>/<date>.jsonl.gz, and only the documents in that archive are deleted.
If the upload fails, nothing is deleted.
"""
import gzip
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from bson import json_util
from botocore.exceptions import EndpointConnectionError, NoCredentialsError, PartialCredentialsError
from sqlalchemy.engine import make_url

from app.config import BACKEND_DIR, get_settings
from app.db import mongo, postgres, s3
from app.db.repositories import (AlertRepo, CarRepo, DeviceRepo, JobRepo, PositionRepo, RecordRepo,
                                 SubscriptionRepo, UserRepo)
from app.services.errors import Conflict, ServiceFailure, Unprocessable

log = logging.getLogger(__name__)

DEGRADED_MS = 500                       # >= 500 ms round trip counts as degraded
RETENTION_MIN_DAYS = 30
RETENTION_DEFAULT_DAYS: dict[str, int | None] = {
    "car_positions": 30,
    "device_data_records": 90,
    "alerts": None,                     # never auto-purged
}
RETENTION_COLLECTIONS = ("device_data_records", "car_positions", "alerts")
DELETE_BATCH = 1000
DUMP_TIMEOUT_S = 30 * 60
S3_PREFIXES = {"audio": "audio/", "backups": "backups/"}

_MONGO_REPOS = {"alerts": AlertRepo, "device_data_records": RecordRepo, "car_positions": PositionRepo}
_SQL_REPOS = {"users": UserRepo, "cars": CarRepo, "devices": DeviceRepo, "subscriptions": SubscriptionRepo}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---- health -------------------------------------------------------------------------------

def _status(latency_ms: float | None) -> str:
    if latency_ms is None:
        return "down"
    return "healthy" if latency_ms < DEGRADED_MS else "degraded"


def _timed(ping: Callable[[], float]) -> float | None:
    try:
        return ping()
    except Exception:  # noqa: BLE001 – a down store is reported, never raised
        log.warning("health ping failed: %s", getattr(ping, "__module__", ping), exc_info=True)
        return None


def health() -> dict[str, Any]:
    """Ping the three stores in parallel (each has its own timeout) and classify them."""
    with ThreadPoolExecutor(max_workers=3) as pool:
        pg, mg, s3_ms = (f.result() for f in [pool.submit(_timed, postgres.ping), pool.submit(_timed, mongo.ping),
                                              pool.submit(_timed, s3.ping)])
    pool_in_use = pool_size = None
    try:
        engine_pool = postgres.get_engine().pool
        pool_in_use = engine_pool.checkedout() if hasattr(engine_pool, "checkedout") else None
        pool_size = engine_pool.size() if hasattr(engine_pool, "size") else None
    except Exception:  # noqa: BLE001
        pass
    return {
        "postgres": {"status": _status(pg), "latency_ms": pg, "pool_in_use": pool_in_use, "pool_size": pool_size},
        "mongodb": {"status": _status(mg), "latency_ms": mg},
        "s3": {"status": _status(s3_ms), "latency_ms": s3_ms},
        "checked_at": _now(),
    }


# ---- stats --------------------------------------------------------------------------------

def _empty(name: str, engine: str) -> dict[str, Any]:
    return {"name": name, "engine": engine, "count": None, "size_bytes": None, "oldest": None}


def stats() -> dict[str, Any]:
    """Count, size and oldest record per store. A store that cannot be reached shows nulls."""
    stores: list[dict[str, Any]] = []
    for name, repo_cls in _MONGO_REPOS.items():
        try:
            stores.append({"name": name, "engine": "mongodb", **repo_cls().collection_stats()})
        except Exception:  # noqa: BLE001
            log.warning("stats failed for %s", name, exc_info=True)
            stores.append(_empty(name, "mongodb"))
    for name, repo_cls in _SQL_REPOS.items():
        try:
            with postgres.session_scope() as session:
                stores.append({"name": name, "engine": "postgresql", **repo_cls(session).table_stats()})
        except Exception:  # noqa: BLE001
            log.warning("stats failed for %s", name, exc_info=True)
            stores.append(_empty(name, "postgresql"))
    for name, prefix in S3_PREFIXES.items():
        try:
            stores.append({"name": name, "engine": "s3", **s3.prefix_stats(prefix)})
        except Exception:  # noqa: BLE001
            log.warning("stats failed for s3 prefix %s", prefix, exc_info=True)
            stores.append(_empty(name, "s3"))
    return {"stores": stores}


# ---- backups ------------------------------------------------------------------------------

def queue_backup(user_id: int | None) -> str:
    """Create a queued backup job, or raise Conflict if one is already queued or running."""
    with postgres.session_scope() as session:
        jobs = JobRepo(session)
        jobs.lock_job_type("backup")
        if jobs.has_unfinished("backup"):
            raise Conflict("A backup is already queued or running")
        job = jobs.create("backup", {"dev_fake": get_settings().DEV_FAKE_BACKUP}, created_by=user_id)
        return str(job.id)


def list_backups(limit: int) -> list[dict[str, Any]]:
    with postgres.session_scope() as session:
        return [{
            "job_id": str(j.id), "status": j.status, "started_at": j.started_at, "finished_at": j.finished_at,
            "size_bytes": (j.result or {}).get("size_bytes"), "error": j.error,
        } for j in JobRepo(session).list_recent("backup", limit)]


def _run(cmd: list[str], env: dict[str, str]) -> None:
    exe = shutil.which(cmd[0])
    if exe is None:
        raise RuntimeError(f"{cmd[0]} not found on this server")
    proc = subprocess.run([exe, *cmd[1:]], env={**os.environ, **env}, capture_output=True, text=True,
                          timeout=DUMP_TIMEOUT_S)
    if proc.returncode != 0:
        last = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or ["no output"]
        raise RuntimeError(f"{cmd[0]} exited with {proc.returncode}: {last[0][:200]}")


def _dump_postgres(path: Path) -> None:
    """pg_dump -Fc. Credentials go in PG* environment variables, never on the command line."""
    url = make_url(get_settings().DATABASE_URL)
    env = {k: str(v) for k, v in {"PGHOST": url.host or url.query.get("host"), "PGPORT": url.port,
                                  "PGUSER": url.username, "PGPASSWORD": url.password,
                                  "PGDATABASE": url.database}.items() if v}
    _run(["pg_dump", "-Fc", "-f", str(path)], env)


def _dump_mongo(path: Path, workdir: Path) -> None:
    """mongodump --archive --gzip. The URI (with password) goes in a 0600 config file, not argv."""
    config = workdir / "mongodump.yaml"
    config.write_text(f'uri: "{get_settings().MONGO_URI}"\n')
    config.chmod(0o600)
    _run(["mongodump", f"--config={config}", f"--archive={path}", "--gzip"], {})


# S3 errors that mean "S3 is not set up here" (as opposed to a real failure such as AccessDenied).
_S3_NOT_CONFIGURED = (NoCredentialsError, PartialCredentialsError, EndpointConnectionError)


def _store_backup_file(path: Path, key: str, content_type: str) -> str:
    """Upload to S3. Dev only (DEV_FAKE_BACKUP=true): when S3 is not configured on this machine, copy
    the file to LOCAL_BACKUP_DIR/<key> instead and return "local:<path>" (team decision 2026-11-04)."""
    try:
        return s3.upload_file(str(path), key, content_type)
    except _S3_NOT_CONFIGURED as exc:
        settings = get_settings()
        if not settings.DEV_FAKE_BACKUP:
            raise
        target = settings.LOCAL_BACKUP_DIR / key
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        log.warning("S3 not configured (%s); dev backup stored locally at %s", type(exc).__name__, target)
        return f"local:{target}"


def run_backup(job_id: str) -> None:
    """BackgroundTask: queued -> running -> succeeded | failed. Never raises."""
    jid = uuid.UUID(job_id)
    with postgres.session_scope() as session:
        JobRepo(session).mark_running(jid)
    try:
        day = _now().strftime("%Y-%m-%d")
        with tempfile.TemporaryDirectory(prefix="backup-") as tmp:
            work = Path(tmp)
            pg_file, mongo_file = work / "postgres.dump", work / "mongo.archive.gz"
            if get_settings().DEV_FAKE_BACKUP:
                pg_file.write_bytes(b"PGDMP fake dev backup\n")
                mongo_file.write_bytes(gzip.compress(b"fake dev mongodump archive\n"))
            else:
                _dump_postgres(pg_file)
                _dump_mongo(mongo_file, work)
            prefix = f"backups/{day}/{job_id}"
            pg_key = _store_backup_file(pg_file, f"{prefix}-postgres.dump", "application/octet-stream")
            mongo_key = _store_backup_file(mongo_file, f"{prefix}-mongo.archive.gz", "application/gzip")
            size = pg_file.stat().st_size + mongo_file.stat().st_size
        with postgres.session_scope() as session:
            JobRepo(session).mark_succeeded(jid, {"postgres_key": pg_key, "mongo_key": mongo_key, "size_bytes": size})
    except Exception as exc:  # noqa: BLE001 – the job row carries the failure
        log.exception("backup %s failed", job_id)
        with postgres.session_scope() as session:
            JobRepo(session).mark_failed(jid, _short_error(exc))


def _short_error(exc: Exception) -> str:
    msg = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return msg[:200]


# ---- retention ----------------------------------------------------------------------------

def _archive_key(collection: str, day: str) -> str:
    """archive/<collection>/<date>.jsonl.gz; a second run on the same day gets a time suffix so an
    earlier archive is never overwritten."""
    key = f"archive/{collection}/{day}.jsonl.gz"
    if s3.exists(key):
        key = f"archive/{collection}/{day}-{_now().strftime('%H%M%S%f')}.jsonl.gz"
    return key


def run_retention(collection: str, older_than_days: int, dry_run: bool, user_id: int | None) -> dict[str, Any]:
    if collection not in RETENTION_COLLECTIONS:
        raise Unprocessable(f"collection must be one of {', '.join(RETENTION_COLLECTIONS)}")
    if older_than_days < RETENTION_MIN_DAYS:
        raise Unprocessable(f"older_than_days must be {RETENTION_MIN_DAYS} or more")

    repo = _MONGO_REPOS[collection]()
    cutoff = _now() - timedelta(days=older_than_days)
    if dry_run:
        return {"candidates": repo.count_older_than(cutoff), "deleted": 0, "archive_key": None}

    params = {"collection": collection, "older_than_days": older_than_days, "cutoff": cutoff.isoformat()}
    with postgres.session_scope() as session:
        job_id = JobRepo(session).create("retention", params, created_by=user_id).id
        JobRepo(session).mark_running(job_id)

    def finish(ok: bool, result: dict | None = None, error: str | None = None) -> None:
        with postgres.session_scope() as session:
            jobs = JobRepo(session)
            if ok:
                jobs.mark_succeeded(job_id, result)
            else:
                jobs.mark_failed(job_id, error or "failed")

    # 1. Archive every matching document; remember exactly which ids were archived.
    archived_ids: list = []
    archive_key = None
    try:
        with tempfile.TemporaryDirectory(prefix="retention-") as tmp:
            path = Path(tmp) / "archive.jsonl.gz"
            with gzip.open(path, "wt", encoding="utf-8") as out:
                for doc in repo.iter_older_than(cutoff, DELETE_BATCH):
                    out.write(json_util.dumps(doc, json_options=json_util.RELAXED_JSON_OPTIONS) + "\n")
                    archived_ids.append(doc["_id"])
            if archived_ids:
                archive_key = _archive_key(collection, _now().strftime("%Y-%m-%d"))
                s3.upload_file(str(path), archive_key, "application/gzip")
    except Exception as exc:  # noqa: BLE001
        log.exception("retention archive failed for %s", collection)
        finish(False, error=f"Archive failed, nothing deleted: {_short_error(exc)}")
        raise ServiceFailure("Archiving to S3 failed; nothing was deleted") from None

    # 2. Delete only the archived documents, in batches.
    deleted = 0
    try:
        for i in range(0, len(archived_ids), DELETE_BATCH):
            deleted += repo.delete_ids(archived_ids[i:i + DELETE_BATCH])
    except Exception as exc:  # noqa: BLE001
        log.exception("retention delete failed for %s after %d documents", collection, deleted)
        finish(False, error=f"Deleted {deleted} of {len(archived_ids)}; archive {archive_key}: {_short_error(exc)}")
        raise ServiceFailure(f"Deletion stopped after {deleted} of {len(archived_ids)} documents; "
                             f"all of them are archived at {archive_key}") from None

    result = {"candidates": len(archived_ids), "deleted": deleted, "archive_key": archive_key}
    finish(True, result=result)
    return result


def fail_interrupted_jobs() -> int:
    """At startup: jobs still queued/running belonged to a previous process and will never finish."""
    with postgres.session_scope() as session:
        return JobRepo(session).fail_unfinished("Interrupted by a server restart")


__all__ = ["health", "stats", "queue_backup", "run_backup", "list_backups", "run_retention",
           "fail_interrupted_jobs", "RETENTION_COLLECTIONS", "RETENTION_DEFAULT_DAYS", "RETENTION_MIN_DAYS"]
