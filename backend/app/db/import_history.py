"""Explicit, insert-only import of 25 reviewed historical alert WAVs.

Default: validate local files only, with no database/network access. --apply-local
permits only loopback/Docker QA stores. Future --apply-cloud additionally requires
all AWS deployment/cost acknowledgements and a positive authorized credit budget.
Neither acknowledgement is a spending cap. No users, keys, SQL rows, emails or
model predictions are created/changed. Historical probabilities are fixture data.

Cross-store writes are not atomic. On interruption, keep successfully inserted
objects/documents and rerun with the same files; matching data is preserved.
Quiesce writers during import. Conflicting existing data is never overwritten.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from bson import ObjectId
from botocore.exceptions import ClientError
from pymongo.errors import DuplicateKeyError
from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.db import history_media

SOURCE = "historical-alerts-v1"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "postgres", "mongo", "minio"})


def validate_audio_dir(directory: Path) -> dict:
    """Pure local preflight; returned file paths are private and must not be logged."""
    manifest = history_media.build(Path(directory))
    if len(manifest["objects"]) != 25:
        raise ValueError("Historical import requires exactly the 25 tracked WAV keys")
    history_media.verify(manifest)
    return manifest


def validate_targets(settings, *, local: bool, environ=None) -> None:
    """Fail before opening any clients, including on zero-budget cloud reads."""
    if local:
        sql = make_url(settings.DATABASE_URL)
        mongo = urlsplit(settings.MONGO_URI)
        endpoint = urlsplit(settings.S3_ENDPOINT_URL or "")
        # No SRV lookup, multiple Mongo hosts, alternate SQL query hosts, or arbitrary DNS.
        if (sql.get_backend_name() not in ("postgresql", "sqlite")
                or (sql.get_backend_name() != "sqlite" and sql.host not in LOCAL_HOSTS)
                or any(key in sql.query for key in ("host", "hostaddr", "service"))
                or mongo.scheme != "mongodb" or mongo.hostname not in LOCAL_HOSTS
                or "," in mongo.netloc
                or set(parse_qs(mongo.query)) - {"authSource", "directConnection", "retryWrites"}
                or endpoint.scheme not in ("http", "https")
                or endpoint.hostname not in LOCAL_HOSTS or endpoint.path not in ("", "/")
                or endpoint.query or endpoint.fragment or endpoint.username):
            raise ValueError("Local import requires explicit loopback or Docker QA database/storage hosts")
        return
    env = os.environ if environ is None else environ
    try:
        budget = Decimal(env.get("AWS_CREDIT_BUDGET_USD", "0"))
    except InvalidOperation:
        budget = Decimal(0)
    if (env.get("ALLOW_AWS_DEPLOYMENT") != "I_ACCEPT_AWS_RESOURCE_CHANGES"
            or env.get("AWS_COST_ACK") != "I_ACCEPT_AWS_USAGE_COSTS"
            or not budget.is_finite() or budget <= 0):
        raise ValueError("Cloud import blocked: explicit deployment/cost consent and positive budget are required")
    if settings.APP_ENV != "production":
        raise ValueError("Cloud import requires validated production application settings")


def _sources() -> list[dict]:
    rows = json.loads(history_media.ALERTS.read_text())
    if len(rows) != 25 or len({r["record_id"] for r in rows}) != 25 or len({r["id"] for r in rows}) != 25:
        raise ValueError("Historical fixture identities must be exactly 25 unique alerts/records")
    for row in rows:
        UUID(row["record_id"])
        ObjectId(row["id"])
    return rows


def _mapping(session) -> dict:
    from app.db import seed
    from app.db.models import Car, Device
    mapping = {}
    for index, source in enumerate(seed.CARS, start=1):
        cars = list(session.scalars(select(Car).where(Car.plate == source["plate"])))
        if len(cars) != 1 or (cars[0].make, cars[0].model) != (source["make"], source["model"]):
            raise ValueError("A required bootstrap car natural identity is absent or conflicting")
        car = cars[0]
        expected = next(d for d in seed.DEVICES if d["car"] == index and d["device_type"] == "microphone")
        devices = list(session.scalars(select(Device).where(Device.car_id == car.id, Device.name == expected["name"])))
        if (len(devices) != 1 or devices[0].device_type != "microphone"
                or not devices[0].device_key_hash
                or devices[0].device_key_hash == seed.device_key_hash(expected["name"])):
            raise ValueError("A required private bootstrap microphone is absent or conflicting")
        mapping[index] = (car.id, devices[0].id)
    return mapping


def _documents(source: dict, media: dict, mapping: dict) -> tuple[dict, dict]:
    car_id, device_id = mapping[source["car_id"]]
    parts = source["s3_object_key"].split("/")
    parts[1] = str(car_id)
    key = "/".join(parts)
    timestamp = datetime.fromisoformat(source["timestamp"].replace("Z", "+00:00"))
    fingerprint = hashlib.sha256(json.dumps(source, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    provenance = {"source": SOURCE, "fixture_sha256": fingerprint, "audio_sha256": media["sha256"],
                  "source_object_key": source["s3_object_key"], "prediction_origin": "historical_fixture_not_reinferred"}
    alert = {k: deepcopy(source[k]) for k in ("record_id", "sound_type", "confidence", "probs", "is_anomaly",
                                             "severity", "status", "model_version", "lat", "lon")}
    alert.update(_id=ObjectId(source["id"]), car_id=car_id, device_id=device_id,
                 timestamp=timestamp, s3_object_key=key, import_provenance=provenance)
    record = {"record_id": source["record_id"], "clip_id": f"{SOURCE}:{source['record_id']}",
              "car_id": car_id, "device_id": device_id, "s3_object_key": key, "content_type": "audio/wav",
              "size_bytes": media["bytes"], "duration_s": media["duration_s"],
              "captured_at": timestamp, "received_timestamp": timestamp,
              "processed_at": timestamp, "trigger": SOURCE, "lat": source["lat"], "lon": source["lon"],
              "status": "processed", "processed": True, "attempts": 0, "lease_until": None,
              "error": None, "alert_id": source["id"], "import_provenance": provenance}
    return alert, record


def _same(existing: dict, expected: dict, *, alert=False) -> bool:
    # An alert may have been acknowledged/reviewed after import; preserve user lifecycle fields.
    ignored = {"status"} if alert else set()
    if alert and existing.get("status") not in {"new", "acknowledged", "resolved"}:
        return False
    for key, value in expected.items():
        if key in ignored:
            continue
        actual = existing.get(key)
        if isinstance(value, datetime) and isinstance(actual, datetime):
            actual = actual.replace(tzinfo=timezone.utc) if actual.tzinfo is None else actual
        if actual != value:
            return False
    return True


def _check_document(collection, expected: dict, *, alert=False) -> bool:
    selectors = [{"record_id": expected["record_id"]}]
    if "_id" in expected:
        selectors.append({"_id": expected["_id"]})
    if "clip_id" in expected:
        selectors.append({"clip_id": expected["clip_id"]})
    found = list(collection.find({"$or": selectors}))
    if len(found) > 1 or (found and not _same(found[0], expected, alert=alert)):
        raise ValueError("Existing historical Mongo identity/content conflicts; no replacement is allowed")
    return bool(found)


def _object_matches(client, bucket: str, key: str, media: dict) -> bool:
    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return False
        raise
    digest, size = hashlib.sha256(), 0
    body = response["Body"]
    try:
        while chunk := body.read(1024 * 1024):
            size += len(chunk)
            if size > media["bytes"]:
                raise ValueError("Existing historical audio object conflicts; never overwritten")
            digest.update(chunk)
    finally:
        body.close()
    if size != media["bytes"] or digest.hexdigest() != media["sha256"]:
        raise ValueError("Existing historical audio object conflicts; never overwritten")
    return True


def preflight(session, db, client, bucket: str, manifest: dict) -> list[dict]:
    """Read-only target preflight AFTER pure local validation; no indexes/writes here."""
    history_media.verify(manifest)
    mapping = _mapping(session)
    media = {row["key"]: row for row in manifest["objects"]}
    plan = []
    for source in _sources():
        entry = media[source["s3_object_key"]]
        alert, record = _documents(source, entry, mapping)
        _check_document(db["alerts"], alert, alert=True)
        _check_document(db["device_data_records"], record)
        _object_matches(client, bucket, alert["s3_object_key"], entry)
        plan.append({"alert": alert, "record": record, "media": entry})
    return plan


def _insert(collection, doc: dict, *, alert=False) -> bool:
    if _check_document(collection, doc, alert=alert):
        return False
    try:
        collection.insert_one(deepcopy(doc))
        return True
    except DuplicateKeyError:
        if _check_document(collection, doc, alert=alert):
            return False
        raise


def apply_plan(db, client, bucket: str, plan: list[dict]) -> dict:
    """Insert only; caller must run preflight first and authorize target access."""
    # Verify every local byte again before the first mutation; no partial upload on a stale plan.
    history_media.verify({"schema": 1, "objects": [row["media"] for row in plan]})
    for row in plan:
        _check_document(db["alerts"], row["alert"], alert=True)
        _check_document(db["device_data_records"], row["record"])
    for name in ("alerts", "device_data_records"):
        db[name].create_index("record_id", unique=True, name="record_id_unique")
    db["device_data_records"].create_index("clip_id", unique=True, sparse=True, name="clip_id_unique")
    summary = {"historical_alerts": 25, "historical_records": 25, "objects_inserted": 0,
               "alerts_inserted": 0, "records_inserted": 0, "verified_audio_bytes": 0,
               "prediction_origin": "historical_fixture_not_reinferred"}
    for row in plan:
        media, alert, record = row["media"], row["alert"], row["record"]
        key = alert["s3_object_key"]
        if not _object_matches(client, bucket, key, media):
            data = Path(media["file"]).read_bytes()
            if len(data) != media["bytes"] or hashlib.sha256(data).hexdigest() != media["sha256"]:
                raise ValueError("Local historical audio changed; rerun validation before continuing")
            try:
                client.put_object(Bucket=bucket, Key=key, Body=data, ContentType="audio/wav", IfNoneMatch="*",
                                  Metadata={"sha256": media["sha256"], "source": SOURCE})
                summary["objects_inserted"] += 1
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") not in ("PreconditionFailed", "412", "ConditionalRequestConflict", "409"):
                    raise
            if not _object_matches(client, bucket, key, media):
                raise ValueError("Historical object insertion was not confirmed; retry without deleting data")
        summary["alerts_inserted"] += int(_insert(db["alerts"], alert, alert=True))
        summary["records_inserted"] += int(_insert(db["device_data_records"], record))
        summary["verified_audio_bytes"] += media["bytes"]
    return summary


def import_history(directory: Path, *, apply: bool = False, local: bool = False,
                   manifest: dict | None = None) -> dict:
    reviewed = manifest
    manifest = validate_audio_dir(directory)
    if reviewed is not None:
        # A reviewed manifest is portable between hosts; never open its original private paths.
        entries = reviewed.get("objects", [])
        if (reviewed.get("schema") != 1
                or sorted(r.get("key", "") for r in entries) != history_media.expected_keys()):
            raise ValueError("Reviewed manifest must contain each of the 25 tracked keys exactly once")
        expected = {r["key"]: (r["bytes"], r["sha256"]) for r in entries}
        if expected != {r["key"]: (r["bytes"], r["sha256"]) for r in manifest["objects"]}:
            raise ValueError("Audio directory changed from the reviewed manifest")
    if apply and reviewed is None:
        raise ValueError("Apply requires an explicit reviewed 25-file manifest")
    if not apply:
        return {"mode": "local_validation_only", "historical_alerts": 25, "verified_wavs": 25,
                "verified_audio_bytes": sum(r["bytes"] for r in manifest["objects"]), "writes": 0}
    from app.config import get_settings
    from app.db import mongo, postgres, s3
    settings = get_settings()
    validate_targets(settings, local=local)
    if local:
        # Explicit local credentials prevent falling through to EC2 metadata/credential services.
        access, secret = os.environ.get("AWS_ACCESS_KEY_ID"), os.environ.get("AWS_SECRET_ACCESS_KEY")
        if not access or not secret:
            raise ValueError("Local import requires explicit local object-storage credentials")
        from pymongo import MongoClient
        local_mongo = MongoClient(settings.MONGO_URI, directConnection=True,
                                  serverSelectionTimeoutMS=3000, tz_aware=True)
        client = s3.make_client(aws_access_key_id=access, aws_secret_access_key=secret)
        db = local_mongo.get_default_database()
    else:
        local_mongo, client, db = None, s3.get_client(), mongo.get_db()
    try:
        with postgres.session_scope() as session:
            plan = preflight(session, db, client, settings.S3_BUCKET, manifest)
        return {"mode": "local_apply" if local else "cloud_apply", **apply_plan(db, client, settings.S3_BUCKET, plan)}
    finally:
        if local_mongo is not None:
            local_mongo.close()
            client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio-directory", required=True, type=Path)
    parser.add_argument("--manifest", type=Path, help="Reviewed private schema1 key/size/SHA256 JSON; required for apply")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply-local", action="store_true")
    mode.add_argument("--apply-cloud", action="store_true")
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text()) if args.manifest else None
        result = import_history(args.audio_directory, apply=args.apply_local or args.apply_cloud, local=args.apply_local, manifest=manifest)
    except Exception as exc:
        # Connection/tool exception messages may include credentials or private paths.
        parser.exit(1, f"Historical import blocked ({type(exc).__name__}); existing data was preserved. "
                    "Review configuration/files privately; after a partial apply, rerun the same reviewed input.\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
