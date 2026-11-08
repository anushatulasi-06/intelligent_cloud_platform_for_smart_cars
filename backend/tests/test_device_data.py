"""C3 upload validation, durable queue processing, and staff API contracts."""
import hashlib
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import mongomock
import numpy as np
import pytest
import soundfile as sf
from botocore.exceptions import ClientError, ReadTimeoutError
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError

from app.config import SOUND_CATEGORIES
from app.db import mongo, postgres, s3
from app.db.models import AlertTypeConfig, Car, Device, ModelVersion, User
from app.db.repositories.record_repo import RecordRepo
from app.main import create_app
from app.services.processing_worker import ProcessingWorker

IOT = {"X-Role": "iot_staff"}
CLOUD = {"X-Role": "cloud_staff"}
EDGE = {"X-Device-Key": "device-secret"}


def audio(seconds=0.25, format="WAV"):
    stream = BytesIO()
    wave = (0.2 * np.sin(2 * np.pi * 440 * np.arange(int(16000 * seconds)) / 16000)).astype("float32")
    sf.write(stream, wave, 16000, format=format)
    return stream.getvalue()


@pytest.fixture
def stores(sql_db, monkeypatch):
    db = mongomock.MongoClient(tz_aware=True)["autosound_test"]
    monkeypatch.setattr(mongo, "get_db", lambda: db)
    mongo.ensure_indexes()
    with postgres.session_scope() as session:
        session.add(User(id=1, name="Owner", email="owner@test.dev", role="owner", password_hash="hash"))
        session.flush()
        session.add_all([Car(id=1, owner_id=1, make="Toyota", model="Camry"),
                         Car(id=2, owner_id=1, make="Honda", model="Civic")])
        session.flush()
        for i in (1, 2):
            session.add(Device(id=i, car_id=i, name=f"Mic {i}", device_type="microphone",
                               device_key_hash=hashlib.sha256(b"device-secret").hexdigest()))
    objects = {}
    monkeypatch.setattr(s3, "put_object", lambda key, data, **kw: objects.setdefault(key, data))
    monkeypatch.setattr(s3, "get_object", lambda key: objects[key])
    monkeypatch.setattr(s3, "presigned_url", lambda key, expires=300: f"https://audio.test/{key}?expires={expires}")
    return db, objects


@pytest.fixture
def client(stores):
    with TestClient(create_app()) as client:
        yield client


def upload(client, data=None, content_type="audio/wav", headers=None, **meta):
    return client.post("/device-data", headers=EDGE if headers is None else headers,
                       data={"device_id": 1, "car_id": 1, **meta},
                       files={"audio_file": ("clip.wav", audio() if data is None else data, content_type)})


def assert_error(response, code):
    assert response.status_code == code, response.text
    assert set(response.json()) == {"detail"}
    assert isinstance(response.json()["detail"], str)


def test_upload_replay_and_staff_monitor(client, stores):
    clip_id = str(uuid4())
    response = upload(client, clip_id=clip_id, captured_at="2026-11-08T12:00:00Z", lat=37.3, lon=-121.9)
    assert response.status_code == 202, response.text
    receipt = response.json()
    assert receipt["status"] == "pending"
    record_id = receipt["record_id"]
    assert upload(client, data=b"not read on replay", clip_id=clip_id).json() == receipt
    assert upload(client, clip_id=clip_id).status_code == 200
    assert len(stores[1]) == 1
    detail = client.get(f"/device-data/{record_id}", headers=IOT).json()
    assert detail["clip_id"] == clip_id and detail["attempts"] == 0
    assert detail["duration_s"] == pytest.approx(0.25)
    assert detail["captured_at"] == "2026-11-08T12:00:00Z"
    assert detail["s3_object_key"].startswith("audio/1/")
    assert "lease_token" not in detail
    assert client.get("/device-data?car_id=2", headers=CLOUD).json()["total"] == 0
    assert client.get("/device-data?car_id=1&page_size=1", headers=CLOUD).json()["total"] == 1
    stats = client.get("/device-data/stats?window_hours=4", headers=IOT).json()
    assert stats["counts"]["pending"] == 1 and len(stats["per_hour"]) == 4
    assert stats["bytes_stored"] == len(audio())
    link = client.get(f"/device-data/{record_id}/audio", headers=CLOUD).json()
    assert link["expires_in"] == 300 and "expires=300" in link["url"]
    assert_error(upload(client, device_id=2, car_id=2, clip_id=clip_id), 409)


def test_upload_authentication_precedes_audio_checks(client, stores):
    for headers in ({}, {"X-Device-Key": "bad"}):
        assert_error(upload(client, data=b"bad", content_type="text/plain", headers=headers), 401)
    assert_error(upload(client, device_id=999), 401)
    assert_error(upload(client, data=b"bad", content_type="text/plain", car_id=2), 403)
    assert stores[0].device_data_records.count_documents({}) == 0
    assert not stores[1]


@pytest.mark.parametrize("data,mime,code", [
    (b"garbage", "text/plain", 415),
    (b"garbage", "audio/wav", 422),
    (b"", "audio/wav", 422),
    (b"x" * (10 * 1024 * 1024 + 1), "audio/wav", 413),
])
def test_bad_uploads_leave_no_record_or_object(client, stores, data, mime, code):
    assert_error(upload(client, data=data, content_type=mime), code)
    assert stores[0].device_data_records.count_documents({}) == 0
    assert not stores[1]


def test_duration_actual_format_and_position_checks(client, stores):
    assert_error(upload(client, data=audio(15.01)), 422)
    assert_error(upload(client, data=audio(format="FLAC")), 415)
    assert_error(upload(client, lat=40), 422)
    assert_error(upload(client, lat=91, lon=0), 422)
    assert_error(upload(client, clip_id="not-a-uuid"), 422)
    assert upload(client, data=audio(15)).status_code == 202


def test_mp3_upload(client):
    response = upload(client, data=audio(format="MP3"), content_type="audio/mpeg")
    assert response.status_code == 202, response.text
    record = client.get(f"/device-data/{response.json()['record_id']}", headers=IOT).json()
    assert record["content_type"] == "audio/mpeg" and record["s3_object_key"].endswith(".mp3")


def test_s3_failure_has_no_record_and_timeout_is_retried(client, stores, monkeypatch):
    def unavailable(*args, **kwargs):
        raise ClientError({"Error": {"Code": "ServiceUnavailable"}}, "PutObject")
    monkeypatch.setattr(s3, "put_object", unavailable)
    assert_error(upload(client), 503)
    assert stores[0].device_data_records.count_documents({}) == 0
    calls = []
    def transient(key, data, **kwargs):
        calls.append(key)
        if len(calls) == 1:
            raise ReadTimeoutError(endpoint_url="https://s3.test")
        stores[1][key] = data
    monkeypatch.setattr(s3, "put_object", transient)
    assert upload(client).status_code == 202
    assert len(calls) == 2 and calls[0] == calls[1]


def test_simultaneous_duplicate_returns_existing_receipt(client, stores, monkeypatch):
    create = RecordRepo.create_pending
    def race(repo, metadata):
        create(repo, metadata)
        raise DuplicateKeyError("another request already inserted this clip")
    monkeypatch.setattr(RecordRepo, "create_pending", race)
    response = upload(client, clip_id=str(uuid4()))
    assert response.status_code == 200, response.text
    assert stores[0].device_data_records.count_documents({}) == 1


def test_staff_roles_validation_missing_and_manual_retry(client, stores):
    record_id = upload(client).json()["record_id"]
    urls = ["/device-data", "/device-data/stats", f"/device-data/{record_id}", f"/device-data/{record_id}/audio"]
    for url in urls:
        assert_error(client.get(url), 401)
        assert_error(client.get(url, headers={"X-Role": "owner"}), 403)
        assert client.get(url, headers=IOT).status_code == 200
    retry = f"/device-data/{record_id}/retry"
    assert_error(client.post(retry, headers=IOT), 403)
    assert_error(client.post(retry, headers=CLOUD), 409)
    stores[0].device_data_records.update_one({"record_id": record_id}, {"$set": {"status": "failed", "attempts": 3}})
    assert client.post(retry, headers=CLOUD).json() == {"record_id": record_id, "status": "pending"}
    assert_error(client.get(f"/device-data/{uuid4()}", headers=IOT), 404)
    for url in ("/device-data/stats?window_hours=0", "/device-data?status=bogus", "/device-data?page_size=101",
                "/device-data?from=2026-11-09&to=2026-11-08"):
        assert_error(client.get(url, headers=IOT), 422)


def test_worker_retries_three_times_then_manual_retry(stores):
    repo = RecordRepo()
    repo.create_pending({"record_id": "record", "car_id": 1, "device_id": 1})
    def fail(record):
        raise RuntimeError("s3 secret URL should not appear in response")
    worker = ProcessingWorker(repo, SimpleNamespace(handle_record=fail))
    for expected in ("pending", "pending", "failed"):
        assert worker.run_once()
        row = repo.get_by_record_id("record")
        assert row["status"] == expected
        assert row["error"] == "Processing failed (RuntimeError)"
    assert row["attempts"] == 3 and not worker.run_once()
    assert repo.retry_failed("record")["attempts"] == 0
    worker.orchestrator = SimpleNamespace(handle_record=lambda record: "alert-id")
    assert worker.run_once()
    done = repo.get_by_record_id("record")
    assert done["status"] == "processed" and done["alert_id"] == "alert-id" and done["processed"]


def test_crashed_final_lease_exhausted_and_stale_workers_fenced(stores):
    repo = RecordRepo()
    repo.create_pending({"record_id": "record", "car_id": 1, "device_id": 1})
    first = repo.claim_next(lease_seconds=-1)
    second = repo.claim_next(lease_seconds=-1)
    third = repo.claim_next(lease_seconds=-1)
    assert [first["attempts"], second["attempts"], third["attempts"]] == [1, 2, 3]
    assert repo.claim_next() is None
    assert repo.get_by_record_id("record")["status"] == "failed"
    repo.retry_failed("record")
    current = repo.claim_next()
    assert current["attempts"] == first["attempts"] == 1
    assert repo.mark_processed("record", "old-alert", attempt=first["attempts"], lease_token=first["lease_token"]) is None
    assert repo.mark_error("record", "old-error", attempt=first["attempts"], lease_token=first["lease_token"]) is None
    assert repo.get_by_record_id("record")["lease_token"] == current["lease_token"]
    assert repo.mark_processed("record", "new-alert", attempt=current["attempts"], lease_token=current["lease_token"])


def test_processed_stats_are_24h_and_latency_is_measured(stores):
    repo = RecordRepo()
    now = datetime.now(timezone.utc)
    for i, age in enumerate((1, 25)):
        received = now - timedelta(hours=age)
        repo.create_pending({"record_id": str(i), "car_id": 1, "device_id": 1, "status": "processed",
                             "received_timestamp": received, "processed_at": received + timedelta(seconds=4),
                             "size_bytes": 10})
    stats = repo.stats(now=now)
    assert stats["counts"]["processed"] == 1
    assert stats["avg_latency_s"] == 4 and stats["bytes_stored"] == 20


def test_upload_worker_real_orchestrator_creates_one_alert(client, stores, tmp_path):
    # Use the existing test artifact builder, real preprocessing/CNN/orchestrator,
    # and real SQL/Mongo repositories; only S3's transport is replaced.
    from tests.test_ml import make_dummy_model
    from app.services.orchestrator import MLOrchestrator

    model_dir = tmp_path / "models"
    make_dummy_model.make_dummy("cnn-integration", model_dir, seed=7)
    with postgres.session_scope() as session:
        session.add(ModelVersion(version_label="cnn-integration", is_active=True,
                                 confidence_threshold=0.75, artifact_path=str(model_dir / "cnn-integration")))
        for category in SOUND_CATEGORIES:
            session.add(AlertTypeConfig(alert_type=category, default_severity="medium", enabled=True))
    record_id = upload(client, data=audio(1)).json()["record_id"]
    repo = RecordRepo()
    orchestrator = MLOrchestrator()
    worker = ProcessingWorker(repo, orchestrator)
    assert worker.run_once()
    record = repo.get_by_record_id(record_id)
    assert record["status"] == "processed", record
    alert = stores[0].alerts.find_one({"record_id": record_id})
    assert str(alert["_id"]) == record["alert_id"] and alert["sound_type"] in SOUND_CATEGORIES
    assert not worker.run_once()
    assert orchestrator.handle_record(record) == record["alert_id"]
    assert stores[0].alerts.count_documents({"record_id": record_id}) == 1
