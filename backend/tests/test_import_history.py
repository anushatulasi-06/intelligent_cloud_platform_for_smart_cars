"""Offline import safety: exact media, private natural mapping, conflicts and recovery."""
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
import wave

from botocore.exceptions import ClientError
import pytest
from sqlalchemy import select

from app.db import history_media, import_history as history, postgres, seed
from app.db.models import Car, Device, User


class Objects:
    def __init__(self):
        self.data, self.puts = {}, []

    def get_object(self, *, Bucket, Key):
        if Key not in self.data:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": BytesIO(self.data[Key])}

    def put_object(self, *, Bucket, Key, Body, IfNoneMatch, **kwargs):
        assert IfNoneMatch == "*"
        if Key in self.data:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.puts.append(Key)
        self.data[Key] = Body


@pytest.fixture
def audio(tmp_path):
    directory = tmp_path / "private-audio"
    directory.mkdir()
    for index, key in enumerate(history_media.expected_keys()):
        with wave.open(str(directory / Path(key).name), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(8000)
            wav.writeframes(bytes([index, 0]) * 80)
    return directory


@pytest.fixture
def private_cars(sql_db):
    # Deliberately different IDs: never trust historical fixture numeric identifiers.
    with postgres.session_scope() as session:
        session.add(User(id=90, name="Private fixture owner", email="private@example.invalid",
                         role="owner", password_hash="not-a-login"))
        session.flush()
        for index, source in enumerate(seed.CARS, 1):
            session.add(Car(id=100 + index, owner_id=90, plate=source["plate"],
                            make=source["make"], model=source["model"]))
            session.flush()
            session.add(Device(id=200 + index, car_id=100 + index, name=f"mic-{index}",
                               device_type="microphone", status="registered", device_key_hash=f"private-{index}"))


def plan_for(audio, mongo_db):
    objects = Objects()
    with postgres.session_scope() as session:
        plan = history.preflight(session, mongo_db, objects, "local-bucket", history.validate_audio_dir(audio))
    return plan, objects


def test_default_dry_run_never_accesses_configured_stores(audio, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "get_settings", lambda: pytest.fail("dry run read configured stores"))
    result = history.import_history(audio)
    assert result["writes"] == 0 and result["verified_wavs"] == 25
    assert "private-audio" not in json.dumps(result)


def test_apply_requires_reviewed_manifest_before_any_connection(audio, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "get_settings", lambda: pytest.fail("unreviewed apply opened stores"))
    with pytest.raises(ValueError, match="reviewed"):
        history.import_history(audio, apply=True, local=True)


def test_manifest_tampering_blocks_before_targets(audio, monkeypatch):
    from app import config
    manifest = history.validate_audio_dir(audio)
    manifest["objects"][-1]["sha256"] = "0" * 64
    monkeypatch.setattr(config, "get_settings", lambda: pytest.fail("changed media opened stores"))
    with pytest.raises(ValueError, match="changed"):
        history.import_history(audio, manifest=manifest, apply=True, local=True)


def test_import_maps_private_ids_links_f13_and_preserves_history(audio, private_cars, mongo_db):
    plan, objects = plan_for(audio, mongo_db)
    result = history.apply_plan(mongo_db, objects, "local-bucket", plan)
    assert (result["objects_inserted"], result["alerts_inserted"], result["records_inserted"]) == (25, 25, 25)
    for source in seed.load_seed_alerts():
        alert = mongo_db.alerts.find_one({"record_id": source["record_id"]})
        record = mongo_db.device_data_records.find_one({"record_id": source["record_id"]})
        assert alert["car_id"] == 100 + source["car_id"]
        assert alert["device_id"] == 200 + source["car_id"]
        assert alert["probs"] == source["probs"] and alert["confidence"] == source["confidence"]
        assert record["alert_id"] == str(alert["_id"])
        assert record["processed"] and record["status"] == "processed" and record["attempts"] == 0
        assert record["captured_at"] == alert["timestamp"] == record["received_timestamp"]
        assert record["s3_object_key"].startswith(f"audio/{alert['car_id']}/")
        assert record["import_provenance"]["prediction_origin"] == "historical_fixture_not_reinferred"
        assert objects.data[record["s3_object_key"]] == (audio / Path(source["s3_object_key"]).name).read_bytes()


def test_rerun_noops_preserves_alert_lifecycle_and_unrelated_data(audio, private_cars, mongo_db):
    plan, objects = plan_for(audio, mongo_db)
    history.apply_plan(mongo_db, objects, "local-bucket", plan)
    first = plan[0]["alert"]
    mongo_db.alerts.update_one({"_id": first["_id"]}, {"$set": {"status": "acknowledged", "review_note": "preserve"}})
    mongo_db.alerts.insert_one({"record_id": "unrelated", "content": "keep"})
    with postgres.session_scope() as session:
        again = history.preflight(session, mongo_db, objects, "local-bucket", history.validate_audio_dir(audio))
    result = history.apply_plan(mongo_db, objects, "local-bucket", again)
    assert (result["objects_inserted"], result["alerts_inserted"], result["records_inserted"]) == (0, 0, 0)
    assert mongo_db.alerts.find_one({"_id": first["_id"]})["review_note"] == "preserve"
    assert mongo_db.alerts.find_one({"record_id": "unrelated"})["content"] == "keep"
    assert len(objects.puts) == 25


@pytest.mark.parametrize("kind", ["alert", "record", "clip", "object"])
def test_late_conflict_fails_preflight_without_any_writes(audio, private_cars, mongo_db, kind):
    plan, objects = plan_for(audio, mongo_db)
    row = plan[-1]
    if kind == "alert":
        mongo_db.alerts.insert_one({"_id": row["alert"]["_id"], "record_id": "conflicting"})
    elif kind == "record":
        mongo_db.device_data_records.insert_one({"record_id": row["record"]["record_id"], "status": "failed"})
    elif kind == "clip":
        mongo_db.device_data_records.insert_one({"record_id": "conflicting", "clip_id": row["record"]["clip_id"]})
    else:
        objects.data[row["alert"]["s3_object_key"]] = b"do not overwrite"
    before = list(mongo_db.alerts.find()), list(mongo_db.device_data_records.find()), deepcopy(objects.data)
    with postgres.session_scope() as session, pytest.raises(ValueError, match="conflict"):
        history.preflight(session, mongo_db, objects, "local-bucket", history.validate_audio_dir(audio))
    assert before == (list(mongo_db.alerts.find()), list(mongo_db.device_data_records.find()), objects.data)
    assert objects.puts == []


def test_stale_file_after_plan_blocks_all_mutations(audio, private_cars, mongo_db):
    plan, objects = plan_for(audio, mongo_db)
    Path(plan[-1]["media"]["file"]).write_bytes(b"changed")
    with pytest.raises(ValueError):
        history.apply_plan(mongo_db, objects, "local-bucket", plan)
    assert objects.puts == [] and mongo_db.alerts.count_documents({}) == 0
    assert mongo_db.device_data_records.count_documents({}) == 0


def test_partial_import_resumes_without_replacing_successful_writes(audio, private_cars, mongo_db, monkeypatch):
    plan, objects = plan_for(audio, mongo_db)
    actual = mongo_db.device_data_records.insert_one
    def fail_once(document):
        monkeypatch.setattr(mongo_db.device_data_records, "insert_one", actual)
        raise RuntimeError("simulated interruption")
    monkeypatch.setattr(mongo_db.device_data_records, "insert_one", fail_once)
    with pytest.raises(RuntimeError, match="interruption"):
        history.apply_plan(mongo_db, objects, "local-bucket", plan)
    assert len(objects.puts) == mongo_db.alerts.count_documents({}) == 1
    with postgres.session_scope() as session:
        again = history.preflight(session, mongo_db, objects, "local-bucket", history.validate_audio_dir(audio))
    result = history.apply_plan(mongo_db, objects, "local-bucket", again)
    assert (result["objects_inserted"], result["alerts_inserted"], result["records_inserted"]) == (24, 24, 25)
    assert len(objects.puts) == 25


def test_racing_object_put_never_overwrites_other_bytes(audio, private_cars, mongo_db, monkeypatch):
    plan, objects = plan_for(audio, mongo_db)
    def racing_put(**kwargs):
        objects.data[kwargs["Key"]] = b"concurrent different bytes"
        raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
    monkeypatch.setattr(objects, "put_object", racing_put)
    with pytest.raises(ValueError, match="conflict"):
        history.apply_plan(mongo_db, objects, "local-bucket", plan)
    assert mongo_db.alerts.count_documents({}) == mongo_db.device_data_records.count_documents({}) == 0


@pytest.mark.parametrize("change", ["car", "mic", "public_key"])
def test_wrong_natural_identity_or_public_key_blocks_mapping(audio, private_cars, mongo_db, change):
    with postgres.session_scope() as session:
        if change == "car":
            session.scalar(select(Car).where(Car.id == 104)).model = "wrong model"
        elif change == "mic":
            session.scalar(select(Device).where(Device.id == 204)).device_type = "camera"
        else:
            session.scalar(select(Device).where(Device.id == 204)).device_key_hash = seed.device_key_hash("mic-4")
    with pytest.raises(ValueError, match="identity|microphone"):
        plan_for(audio, mongo_db)
    assert mongo_db.alerts.count_documents({}) == 0


def local_settings():
    return SimpleNamespace(DATABASE_URL="postgresql://test:test@postgres/qa",
                           MONGO_URI="mongodb://test:test@mongo/qa?authSource=admin",
                           S3_ENDPOINT_URL="http://minio:9000", APP_ENV="production")


@pytest.mark.parametrize("field,value", [
    ("DATABASE_URL", "postgresql://test:test@database.rds.amazonaws.com/qa"),
    ("DATABASE_URL", "postgresql://test:test@postgres/qa?host=external.example"),
    ("MONGO_URI", "mongodb+srv://test:test@cluster.example/qa"),
    ("MONGO_URI", "mongodb://mongo,external.example/qa"),
    ("MONGO_URI", "mongodb://mongo/qa?authMechanism=MONGODB-AWS"),
    ("S3_ENDPOINT_URL", "https://s3.us-west-2.amazonaws.com"),
    ("S3_ENDPOINT_URL", None), ("S3_ENDPOINT_URL", "http://minio.evil.example:9000"),
])
def test_local_guard_blocks_external_targets(field, value):
    settings = local_settings()
    setattr(settings, field, value)
    with pytest.raises(ValueError, match="Local import"):
        history.validate_targets(settings, local=True)


def test_local_guard_accepts_qa_hosts():
    history.validate_targets(local_settings(), local=True)


@pytest.mark.parametrize("budget", ["0", "-1", "NaN", "Infinity", "invalid"])
def test_cloud_zero_or_invalid_budget_is_fail_closed(budget):
    env = {"ALLOW_AWS_DEPLOYMENT": "I_ACCEPT_AWS_RESOURCE_CHANGES", "AWS_COST_ACK": "I_ACCEPT_AWS_USAGE_COSTS",
           "AWS_CREDIT_BUDGET_USD": budget}
    with pytest.raises(ValueError, match="Cloud import blocked"):
        history.validate_targets(local_settings(), local=False, environ=env)


def test_cloud_requires_both_acknowledgements_and_production():
    env = {"AWS_CREDIT_BUDGET_USD": "1"}
    with pytest.raises(ValueError):
        history.validate_targets(local_settings(), local=False, environ=env)
    env.update(ALLOW_AWS_DEPLOYMENT="I_ACCEPT_AWS_RESOURCE_CHANGES", AWS_COST_ACK="I_ACCEPT_AWS_USAGE_COSTS")
    history.validate_targets(local_settings(), local=False, environ=env)  # pure validation only
    settings = local_settings()
    settings.APP_ENV = "development"
    with pytest.raises(ValueError, match="production"):
        history.validate_targets(settings, local=False, environ=env)


@pytest.mark.parametrize("kind", ["missing", "duplicate", "symlink", "truncated"])
def test_media_missing_ambiguous_or_invalid_rejected(audio, tmp_path, kind):
    target = next(audio.glob("*.wav"))
    if kind == "missing":
        target.unlink()
    elif kind == "duplicate":
        nested = audio / "duplicate"
        nested.mkdir()
        (nested / target.name).write_bytes(target.read_bytes())
    elif kind == "symlink":
        original = tmp_path / "outside.wav"
        target.rename(original)
        target.symlink_to(original)
    else:
        target.write_bytes(target.read_bytes()[:50])
    with pytest.raises(ValueError):
        history.validate_audio_dir(audio)


def test_public_responses_expose_historical_provenance_without_private_paths(audio, private_cars, mongo_db):
    from app.schemas.alerts import AlertDetail
    from app.schemas.device_data import RecordDetail
    plan, _ = plan_for(audio, mongo_db)
    alert, record = plan[0]["alert"], plan[0]["record"]
    public_alert = AlertDetail.model_validate({**alert, "id": str(alert["_id"])}).model_dump(mode="json")
    public_record = RecordDetail.model_validate(record).model_dump(mode="json")
    for response in (public_alert, public_record):
        assert response["import_provenance"]["source"] == "historical-alerts-v1"
        assert response["import_provenance"]["prediction_origin"] == "historical_fixture_not_reinferred"
        assert str(audio) not in json.dumps(response)


def test_local_apply_requires_explicit_storage_credentials_before_clients(audio, monkeypatch):
    from app import config
    manifest = history.validate_audio_dir(audio)
    monkeypatch.setattr(config, "get_settings", local_settings)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    with pytest.raises(ValueError, match="explicit local object-storage credentials"):
        history.import_history(audio, manifest=manifest, apply=True, local=True)


def test_reviewed_manifest_paths_are_not_opened_on_target_host(audio, monkeypatch):
    from app import config
    manifest = history.validate_audio_dir(audio)
    for entry in manifest["objects"]:
        entry["file"] = "/never-open-original-host-path/private.wav"
    monkeypatch.setattr(config, "get_settings", lambda: pytest.fail("dry run opened configured stores"))
    result = history.import_history(audio, manifest=manifest)
    assert result["verified_wavs"] == 25 and result["writes"] == 0
