#!/usr/bin/env python3
"""Private historical-media staging and local-only authenticated integration checks.

The source WAV directory is never copied into Git/source snapshots or evidence.
Callers must already own/isolate their local stores. No AWS hostname is accepted.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def ensure(condition, message):
    if not condition:
        raise RuntimeError(message)


def media_module(root=ROOT):
    if not (root / "backend/app/db/history_media.py").is_file():
        from app.db import history_media
        return history_media
    spec = importlib.util.spec_from_file_location("qa_history_media", root / "backend/app/db/history_media.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stage_audio(directory, destination, *, readable_mount=False):
    """Validate every WAV before a DB starts; copy only verified bytes into private QA storage.

    A Docker bind mount needs readable children for the image's non-root UID. The
    enclosing temporary directory remains 0700, and only this subtree is mounted
    read-only; neither host peers nor the container can reach the source directory.
    """
    media = media_module()
    manifest = media.build(Path(directory))
    ensure(len(manifest["objects"]) == 25, "Expected exactly 25 historical audio objects")
    destination = Path(destination)
    destination.mkdir(mode=0o755 if readable_mount else 0o700)
    public = {"schema": 1, "objects": []}
    for entry in manifest["objects"]:
        target = destination / entry["key"]
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755 if readable_mount else 0o700)
        with Path(entry["file"]).open("rb") as source, target.open("xb") as output:
            shutil.copyfileobj(source, output)
        target.chmod(0o444 if readable_mount else 0o400)
        actual = media.describe(target)
        ensure(actual["sha256"] == entry["sha256"] and actual["bytes"] == entry["bytes"],
               "Historical media changed during its private staging copy")
        public["objects"].append({key: entry[key] for key in ("key", "bytes", "sha256")})
    media.verify(media.build(destination))
    if readable_mount:
        # mkdir's requested mode is filtered by the caller's private umask.
        for child in destination.rglob("*"):
            if child.is_dir():
                child.chmod(0o755)
        destination.chmod(0o755)
    return public


def credentials_env(bundle):
    env = {}
    owners = sorted((row for row in bundle["users"] if row["role"] == "owner"), key=lambda row: row["id"])
    roles = {"OWNER": owners[0], "OWNER2": owners[1],
             "IOT": next(row for row in bundle["users"] if row["role"] == "iot_staff"),
             "CLOUD": next(row for row in bundle["users"] if row["role"] == "cloud_staff")}
    for name, row in roles.items():
        env[f"VERIFY_{name}_EMAIL"], env[f"VERIFY_{name}_PASSWORD"] = row["email"], row["password"]
    mic = next(row for row in bundle["devices"] if row["name"] == "mic-1")
    camera = next(row for row in bundle["devices"] if row["name"] == "cam-1")
    env.update(VERIFY_CAR_ID=str(mic["car_id"]), EDGE_CAR_ID=str(mic["car_id"]),
               EDGE_DEVICE_ID=str(mic["id"]), EDGE_DEVICE_KEY=mic["device_key"],
               EDGE_CAMERA_DEVICE_ID=str(camera["id"]), EDGE_CAMERA_DEVICE_KEY=camera["device_key"])
    return env


def apply_local_history(directory):
    from app.db.import_history import import_history, validate_audio_dir
    manifest = validate_audio_dir(Path(directory))
    first = import_history(Path(directory), apply=True, local=True, manifest=manifest)
    ensure(first["historical_alerts"] == 25 and first["historical_records"] == 25,
           "Historical importer did not validate all 25 linked records and alerts")
    repeated = import_history(Path(directory), apply=True, local=True, manifest=manifest)
    ensure(all(repeated[key] == 0 for key in ("objects_inserted", "alerts_inserted", "records_inserted")),
           "Repeated historical import changed existing data")
    return {"first": first, "repeat": repeated, "repeat_idempotent": True}


def local_origin(value):
    url = urlsplit(value)
    ensure(url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost", "minio"}
           and url.username is None and url.password is None and not url.fragment,
           "Historical verifier accepts only explicit local HTTP endpoints")
    return (url.scheme, url.hostname, url.port or 80)


def verify_history(base_url, directory, bundle, *, object_origin, fixtures=None, replay=None):
    import httpx
    ensure(urlsplit(base_url).hostname == "127.0.0.1", "Historical API must use literal loopback")
    local_origin(base_url)
    allowed_origin = local_origin(object_origin)
    fixtures = fixtures or media_module().ALERTS
    entries = media_module().build(Path(directory))["objects"]
    manifest = {entry["key"]: entry for entry in entries}
    expected = json.loads(Path(fixtures).read_text())
    ensure(len(expected) == len(entries) == 25, "Historical fixture/media count differs from 25")
    env = credentials_env(bundle)
    evidence = {"status": "running", "count": 25, "alerts": [], "source": "historical-alerts-v1",
                "prediction_origin": "preserved historical fixtures; not new model inference",
                "signed_downloads_verified": 0, "signed_download_bytes": 0,
                "unique_audio_bytes": sum(row["bytes"] for row in entries)}
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=30, trust_env=False, follow_redirects=False) as client:
        def request(path, role, expected_status=200):
            result = client.get(path, headers={"Authorization": "Bearer " + tokens[role]})
            ensure(result.status_code == expected_status, f"Historical API {path}: unexpected HTTP status")
            return result.json()
        tokens = {}
        for role in ("CLOUD", "IOT", "OWNER", "OWNER2"):
            response = client.post("/login", json={"email": env[f"VERIFY_{role}_EMAIL"],
                                                   "password": env[f"VERIFY_{role}_PASSWORD"]})
            ensure(response.status_code == 200, "Historical verification private login failed")
            tokens[role] = response.json()["access_token"]
        ensure(client.get("/alerts").status_code == 401, "Historical alerts allowed anonymous access")
        before = request("/alerts?page_size=100", "CLOUD")
        evidence["observed_alert_count"] = before["total"]
        for source in expected:
            mic = next(row for row in bundle["devices"] if row["name"] == f"mic-{source['car_id']}")
            alert = request(f"/alerts/{source['id']}", "CLOUD")
            record = request(f"/device-data/{source['record_id']}", "IOT")
            key_parts = source["s3_object_key"].split("/")
            key_parts[1] = str(mic["car_id"])
            key = "/".join(key_parts)
            for document in (alert, record):
                ensure(document["car_id"] == mic["car_id"] and document["device_id"] == mic["id"]
                       and document["record_id"] == source["record_id"] and document["s3_object_key"] == key,
                       "Historical alert/record is linked to an incorrect private car, microphone or object")
            ensure(record["status"] == "processed" and record["processed"] and record["alert_id"] == alert["id"]
                   and record["trigger"] == "historical-alerts-v1", "Historical F13 record lacks its processed alert link")
            owner = "OWNER2" if source["car_id"] == 4 else "OWNER"
            other = "OWNER" if owner == "OWNER2" else "OWNER2"
            request(f"/alerts/{source['id']}", owner)
            request(f"/alerts/{source['id']}", other, 404)
            request(f"/alerts/{source['id']}/audio", other, 404)
            expected_media = manifest[source["s3_object_key"]]
            for path, role in ((f"/alerts/{source['id']}/audio", "CLOUD"),
                               (f"/alerts/{source['id']}/audio", owner),
                               (f"/device-data/{source['record_id']}/audio", "IOT")):
                link = request(path, role)
                ensure(local_origin(link["url"]) == allowed_origin, "Signed media URL left the owned local object store")
                ensure("X-Amz-Signature=" in link["url"], "Historical media must use signed object links")
                size, digest = 0, hashlib.sha256()
                with client.stream("GET", link["url"]) as audio:
                    ensure(audio.status_code == 200, "Historical signed audio download failed")
                    for block in audio.iter_bytes():
                        size += len(block)
                        ensure(size <= expected_media["bytes"], "Historical signed audio exceeds its validated size")
                        digest.update(block)
                ensure(size == expected_media["bytes"] and digest.hexdigest() == expected_media["sha256"],
                       "Historical signed audio bytes differ from the validated WAV")
                evidence["signed_downloads_verified"] += 1
                evidence["signed_download_bytes"] += size
            evidence["alerts"].append({"id": alert["id"], "record_id": record["record_id"],
                "car_id": mic["car_id"], "device_id": mic["id"], "s3_object_key": key,
                "audio_sha256": expected_media["sha256"], "size_bytes": expected_media["bytes"]})
        if replay is not None:
            scenarios = replay["scenarios"]
            historical_ids = {row["id"] for row in evidence["alerts"]}
            ensure(len(scenarios) == 5 and len({row["alert_id"] for row in scenarios}) == 5
                   and not historical_ids.intersection(row["alert_id"] for row in scenarios),
                   "Expected five distinct new replay alerts in addition to the historical 25")
            for row in scenarios:
                alert = request(f"/alerts/{row['alert_id']}", "CLOUD")
                ensure(alert["record_id"] == row["record_id"], "New replay alert lost its record link")
            evidence["new_replay_alert_ids"] = [row["alert_id"] for row in scenarios]
            evidence["historical_preserved_after_five_replays"] = True
    evidence["status"] = "passed"
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--object-origin", required=True)
    parser.add_argument("--audio-dir", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply-local", action="store_true")
    parser.add_argument("--replay-evidence", type=Path)
    args = parser.parse_args()
    bundle = json.loads(args.bundle.read_text())
    # Check API/object endpoint locality before the optional DB-writing import.
    local_origin(args.base_url); local_origin(args.object_origin)
    imports = apply_local_history(args.audio_dir) if args.apply_local else None
    replay = json.loads(args.replay_evidence.read_text()) if args.replay_evidence else None
    evidence = verify_history(args.base_url, args.audio_dir, bundle, object_origin=args.object_origin, replay=replay)
    if imports:
        evidence["import"] = imports
        ensure(evidence["observed_alert_count"] == 25, "Fresh private import must contain exactly 25 alerts")
        evidence["before_replay_alert_count"] = 25
    args.output.write_text(json.dumps(evidence, indent=2) + "\n")
    print("PASS historical25: private car/microphone links, staff/owner access, exact signed audio bytes")


if __name__ == "__main__":
    main()
