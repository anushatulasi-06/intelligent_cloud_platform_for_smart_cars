"""Inference: preprocessing parity with training, classifier, anomaly flag, severity, orchestrator
(idempotent handle_record, suppressed alerts, reload_model switching versions)."""
import importlib.util
import json
import math
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import mongomock
import numpy as np
import pytest
import soundfile as sf
import torch

from app.config import BACKEND_DIR
from app.db import mongo, postgres, s3
from app.db.models import AlertTypeConfig, Car, ModelVersion, User
from app.ml.anomaly import AnomalyDetector
from app.ml.classifier import EXPECTED_LABELS, AudioCNN, CnnClassifier, ModelLoadError
from app.ml.model_config import ModelConfigError, load_model_config
from app.ml.preprocessor import AudioLoadError, AudioPreprocessor
from app.ml.severity import SeverityPolicy, UnknownAlertType
from tests.auth_headers import bearer
from app.services import orchestrator as orch_mod
from app.services.orchestrator import MLOrchestrator, ModelNotReady

REPO_ROOT = BACKEND_DIR.parent
TRAINING_SUMMARY = REPO_ROOT / "model" / "checkpoints" / "training_summary.json"
TRAINED_WEIGHTS = REPO_ROOT / "model" / "checkpoints" / "cnn_weights.pth"


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


make_dummy_model = _load_script("make_dummy_model")


@pytest.fixture(scope="session")
def artifacts(tmp_path_factory):
    """Two dummy versions (different random weights) built by scripts/make_dummy_model.py."""
    out = tmp_path_factory.mktemp("artifacts")
    make_dummy_model.make_dummy("cnn-v1", out, seed=1)
    make_dummy_model.make_dummy("cnn-v2", out, seed=2)
    return out


@pytest.fixture(scope="session")
def cfg(artifacts):
    return load_model_config(artifacts / "cnn-v1")


@pytest.fixture(scope="session")
def pre(cfg):
    return AudioPreprocessor(cfg)


def tone(seconds: float, sr: int, freq: float = 880.0, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


# ---- config.json --------------------------------------------------------------------------

def test_config_json_copies_training_values(cfg):
    sys.path.insert(0, str(REPO_ROOT))
    from model import config as mc
    a, f = cfg["audio"], cfg["features"]
    assert (a["sample_rate"], a["duration_s"], a["clip_samples"]) == (16000, 4.0, 64000)
    assert (a["sample_rate"], a["duration_s"], a["crop_search_hop_s"]) == \
        (mc.SAMPLE_RATE, mc.CLIP_DURATION_S, mc.CROP_SEARCH_HOP_S)
    assert (f["n_mels"], f["n_fft"], f["hop_length"], f["top_db"], f["n_frames"]) == \
        (mc.N_MELS, mc.N_FFT, mc.HOP_LENGTH, mc.TOP_DB, mc.N_FRAMES) == (128, 2048, 512, 80.0, 126)
    assert cfg["categories"] == mc.CATEGORIES == EXPECTED_LABELS
    assert cfg["confidence_threshold"] == 0.75 and cfg["dummy"] is True


def test_backend_has_no_hard_coded_audio_parameters():
    """Audio/feature numbers may only come from config.json."""
    for name in ("preprocessor.py", "classifier.py"):
        src = (BACKEND_DIR / "app" / "ml" / name).read_text()
        for literal in ("16000", "16_000", "2048", "512", "128", "126", "80.0", "22050"):
            assert literal not in src, f"{name} hard-codes {literal}"


def test_incomplete_config_rejected(artifacts, tmp_path):
    bad = json.loads((artifacts / "cnn-v1" / "config.json").read_text())
    del bad["features"]["hop_length"]
    (tmp_path / "config.json").write_text(json.dumps(bad))
    with pytest.raises(ModelConfigError):
        load_model_config(tmp_path)


# ---- preprocessing --------------------------------------------------------------------------

def trained_input_shape() -> tuple[int, int, int]:
    """The shape the real CNN was trained on, from training's own summary when present."""
    if TRAINING_SUMMARY.is_file():
        p = json.loads(TRAINING_SUMMARY.read_text())["preprocessing"]
        return (1, p["n_mels"], p["n_frames"])
    sys.path.insert(0, str(REPO_ROOT))
    from model import config as mc
    return (1, mc.N_MELS, mc.N_FRAMES)


@pytest.mark.parametrize("seconds", [0.3, 4.0, 9.7])
def test_log_mel_shape_equals_trained_input_shape(pre, cfg, seconds):
    spec = pre.to_log_mel(tone(seconds, cfg["audio"]["sample_rate"]))
    assert isinstance(spec, torch.Tensor) and spec.dtype == torch.float32
    assert tuple(spec.shape) == trained_input_shape() == tuple(cfg["input_shape"])
    assert float(spec.min()) >= 0.0 and float(spec.max()) <= 1.0


def test_shape_mismatch_fails_loudly(cfg):
    wrong = json.loads(json.dumps(cfg))
    wrong["features"]["hop_length"] = 256                     # would give a different frame count
    with pytest.raises(ValueError, match="model input shape"):
        AudioPreprocessor(wrong).to_log_mel(tone(1.0, cfg["audio"]["sample_rate"]))


def test_silence_is_all_zeros(pre, cfg):
    assert float(pre.to_log_mel(np.zeros(cfg["audio"]["clip_samples"], dtype=np.float32)).abs().sum()) == 0.0


def test_matches_training_feature_code_exactly(pre, cfg):
    """Same waveform through model/audio_utils.py (training) and the backend preprocessor."""
    pytest.importorskip("librosa")
    sys.path.insert(0, str(REPO_ROOT))
    from model import audio_utils
    sr = cfg["audio"]["sample_rate"]
    rng = np.random.default_rng(0)
    long_clip = np.concatenate([0.01 * rng.standard_normal(3 * sr), tone(2.0, sr, 1500, 0.8),
                                0.01 * rng.standard_normal(4 * sr)]).astype(np.float32)
    for wave in (tone(1.0, sr), tone(4.0, sr, 440), long_clip, np.zeros(sr, dtype=np.float32)):
        np.testing.assert_array_equal(pre.to_log_mel_array(wave), audio_utils.to_mel_spectrogram(wave))


def test_long_clip_keeps_loudest_window(pre, cfg):
    sr = cfg["audio"]["sample_rate"]
    wave = np.concatenate([np.zeros(6 * sr), tone(1.0, sr, amp=0.9), np.zeros(3 * sr)]).astype(np.float32)
    kept = pre.fix_length(wave)
    assert kept.size == cfg["audio"]["clip_samples"] and np.abs(kept).max() > 0.8


def test_load_wav_resamples_and_mixes_to_mono(pre, cfg, tmp_path):
    path = tmp_path / "stereo_44k.wav"
    stereo = np.stack([tone(2.0, 44100), tone(2.0, 44100)], axis=1)
    sf.write(path, stereo, 44100)
    wave = pre.load(path)
    assert wave.ndim == 1 and wave.size == cfg["audio"]["clip_samples"]


def test_load_mp3(pre, cfg, tmp_path):
    path = tmp_path / "clip.mp3"
    try:
        sf.write(path, tone(1.5, 44100), 44100, format="MP3")
    except Exception:
        pytest.skip("this libsndfile cannot write MP3")
    assert pre.load(path).size == cfg["audio"]["clip_samples"]


def test_load_errors(pre, tmp_path):
    with pytest.raises(AudioLoadError):
        pre.load(tmp_path / "missing.wav")
    junk = tmp_path / "junk.wav"
    junk.write_bytes(b"not audio at all")
    with pytest.raises(AudioLoadError):
        pre.load(junk)


# ---- classifier -----------------------------------------------------------------------------

def test_probabilities_sum_to_one_in_label_order(artifacts, pre, cfg):
    clf = CnnClassifier.load(artifacts / "cnn-v1")
    probs = clf.predict(pre.to_log_mel(tone(2.0, cfg["audio"]["sample_rate"])))
    assert list(probs) == ["emergency", "in_vehicle", "human", "traffic", "environmental"]
    assert math.isclose(sum(probs.values()), 1.0, abs_tol=1e-6)
    assert all(0.0 <= p <= 1.0 for p in probs.values())


def test_predict_is_deterministic_and_cpu(artifacts, cfg):
    clf = CnnClassifier.load(artifacts / "cnn-v1")
    x = torch.rand(cfg["input_shape"])
    assert clf.predict(x) == clf.predict(x)
    assert all(p.device.type == "cpu" for p in clf.network.parameters())
    assert not clf.network.training


def test_wrong_input_shape_rejected(artifacts):
    with pytest.raises(ValueError, match="trained shape"):
        CnnClassifier.load(artifacts / "cnn-v1").predict(torch.zeros(1, 64, 173))


def test_real_trained_weights_load_into_backend_network(cfg):
    """The backend network must be the SAME architecture training saved (strict state_dict load)."""
    if not TRAINED_WEIGHTS.is_file():
        pytest.skip("model/checkpoints/cnn_weights.pth not present")
    state = torch.load(TRAINED_WEIGHTS, map_location="cpu", weights_only=True)
    net = AudioCNN(cfg["architecture"])
    net.load_state_dict(state, strict=True)
    assert net(torch.zeros(1, *cfg["input_shape"])).shape == (1, 5)


@pytest.mark.parametrize("change,match", [
    (lambda d: (d / "model_weights.pt").unlink(), "model_weights.pt"),
    (lambda d: (d / "labels.json").write_text(json.dumps({"0": "human", "1": "emergency", "2": "in_vehicle",
                                                          "3": "traffic", "4": "environmental"})), "labels.json"),
    (lambda d: (d / "config.json").unlink(), "config.json"),
])
def test_broken_artifacts_raise(artifacts, tmp_path, change, match):
    import shutil
    d = tmp_path / "v"
    shutil.copytree(artifacts / "cnn-v1", d)
    change(d)
    with pytest.raises(ModelLoadError, match=match):
        CnnClassifier.load(d)


# ---- anomaly + severity ---------------------------------------------------------------------

@pytest.mark.parametrize("confidence,threshold,expected", [
    (0.74, 0.75, True), (0.75, 0.75, False), (0.76, 0.75, False), (0.5, 0.6, True), (0.95, 0.95, False)])
def test_anomaly_flag(confidence, threshold, expected):
    assert AnomalyDetector.is_anomaly(confidence, threshold) is expected


def test_severity_lookup(session):
    session.add_all([AlertTypeConfig(alert_type="human", default_severity="medium", enabled=True),
                     AlertTypeConfig(alert_type="traffic", default_severity="low", enabled=False)])
    session.flush()
    policy = SeverityPolicy(session)
    assert policy.severity_for("human") == "medium"
    assert policy.lookup("traffic") == ("low", False)
    with pytest.raises(UnknownAlertType):
        policy.severity_for("emergency")


# ---- orchestrator ---------------------------------------------------------------------------

class SpyNotifier:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def notify_if_subscribed(self, alert):
        self.calls.append(alert["id"])
        if self.fail:
            raise RuntimeError("SES exploded")


@pytest.fixture
def world(sql_db, artifacts, monkeypatch, tmp_path):
    """Two model versions (v1 active), alert types, one car; S3 and Mongo replaced in memory."""
    with postgres.session_scope() as s:
        s.add(User(id=1, name="O", email="o@x.dev", role="owner", password_hash="h"))
        s.flush()
        s.add(Car(id=10, owner_id=1, make="Toyota", model="Camry", plate="P10"))
        s.add_all([ModelVersion(id=1, version_label="cnn-v1", artifact_path=str(artifacts / "cnn-v1"),
                                confidence_threshold=Decimal("0.75"), is_active=True),
                   ModelVersion(id=2, version_label="cnn-v2", artifact_path=str(artifacts / "cnn-v2"),
                                confidence_threshold=Decimal("0.60"), is_active=False)])
        for c in EXPECTED_LABELS:
            s.add(AlertTypeConfig(alert_type=c, default_severity="high", enabled=True))
    db = mongomock.MongoClient(tz_aware=True)["autosound_test"]
    monkeypatch.setattr(mongo, "get_db", lambda: db)
    clip = tmp_path / "clip.wav"
    sf.write(clip, tone(3.0, 16000), 16000)
    downloads = []

    def fake_get_object(key):
        downloads.append(key)
        if key.startswith("missing/"):
            raise FileNotFoundError(key)
        return clip.read_bytes()

    monkeypatch.setattr(s3, "get_object", fake_get_object)
    return {"db": db, "downloads": downloads}


RECORD = {"record_id": "rec-1", "device_id": 3, "car_id": 10, "s3_object_key": "audio/10/2026/11/03/rec-1.wav",
          "lat": 37.33, "lon": -121.88, "captured_at": datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)}


def test_handle_record_creates_complete_alert(world):
    notifier = SpyNotifier()
    o = MLOrchestrator(notifier)
    alert_id = o.handle_record(RECORD)
    doc = world["db"]["alerts"].find_one()
    assert str(doc["_id"]) == alert_id
    assert {k: doc[k] for k in ("record_id", "device_id", "car_id", "status", "model_version", "s3_object_key",
                                "lat", "lon", "severity")} == {
        "record_id": "rec-1", "device_id": 3, "car_id": 10, "status": "new", "model_version": "cnn-v1",
        "s3_object_key": RECORD["s3_object_key"], "lat": 37.33, "lon": -121.88, "severity": "high"}
    assert doc["sound_type"] in EXPECTED_LABELS and doc["confidence"] == max(doc["probs"].values())
    assert math.isclose(sum(doc["probs"].values()), 1.0, abs_tol=1e-6)
    assert doc["is_anomaly"] == (doc["confidence"] < 0.75)
    assert doc["timestamp"] == RECORD["captured_at"] and "suppressed" not in doc
    assert notifier.calls == [alert_id]


def test_handle_record_twice_leaves_one_alert(world):
    notifier = SpyNotifier()
    o = MLOrchestrator(notifier)
    first = o.handle_record(RECORD)
    second = o.handle_record(RECORD)
    assert first == second
    assert world["db"]["alerts"].count_documents({"record_id": "rec-1"}) == 1
    assert notifier.calls == [first]                           # not notified again on retry
    assert len(world["downloads"]) == 1                        # retry did not re-download or re-classify


def test_upsert_never_creates_second_alert_even_without_precheck(world):
    """$setOnInsert: two writers for one record end with one alert, the first one's fields."""
    from app.db.repositories import AlertRepo
    repo = AlertRepo()
    a = repo.upsert_by_record_id("r-x", {"car_id": 10, "status": "new", "sound_type": "human"})
    b = repo.upsert_by_record_id("r-x", {"car_id": 10, "status": "new", "sound_type": "traffic"})
    assert a["id"] == b["id"] and b["sound_type"] == "human"
    assert world["db"]["alerts"].count_documents({"record_id": "r-x"}) == 1


def test_disabled_alert_type_is_stored_suppressed_without_notification(world):
    with postgres.session_scope() as s:
        for row in s.query(AlertTypeConfig).all():
            row.enabled = False
    notifier = SpyNotifier()
    MLOrchestrator(notifier).handle_record(RECORD)
    doc = world["db"]["alerts"].find_one()
    assert doc["suppressed"] is True and doc["status"] == "new"
    assert notifier.calls == []


def test_notification_failure_does_not_fail_record(world):
    alert_id = MLOrchestrator(SpyNotifier(fail=True)).handle_record(RECORD)
    assert world["db"]["alerts"].count_documents({}) == 1 and alert_id


def test_download_error_raises_for_retry(world):
    with pytest.raises(FileNotFoundError):
        MLOrchestrator(SpyNotifier()).handle_record({**RECORD, "s3_object_key": "missing/clip.wav"})
    assert world["db"]["alerts"].count_documents({}) == 0


def test_no_active_model_raises(world):
    with postgres.session_scope() as s:
        s.get(ModelVersion, 1).is_active = False
    o = MLOrchestrator(SpyNotifier())
    assert o.reload_model() is None
    with pytest.raises(ModelNotReady):
        o.handle_record(RECORD)


def test_threshold_change_applies_without_reload(world):
    o = MLOrchestrator(SpyNotifier())
    o.reload_model()
    with postgres.session_scope() as s:
        s.get(ModelVersion, 1).confidence_threshold = Decimal("0.95")
    o.handle_record(RECORD)
    doc = world["db"]["alerts"].find_one()
    assert doc["is_anomaly"] == (doc["confidence"] < 0.95)


def test_reload_model_switches_active_version(world, tmp_path):
    o = MLOrchestrator(SpyNotifier())
    assert o.reload_model().version_label == "cnn-v1"
    clip = tmp_path / "c.wav"
    sf.write(clip, tone(2.0, 16000, 300), 16000)
    probs_v1 = o.preprocess_and_classify(clip)[3]
    with postgres.session_scope() as s:
        s.get(ModelVersion, 1).is_active = False
        s.flush()
        s.get(ModelVersion, 2).is_active = True
    loaded = o.reload_model()
    assert (loaded.version_label, loaded.threshold) == ("cnn-v2", 0.60)
    probs_v2 = o.preprocess_and_classify(clip)[3]
    assert probs_v1 != probs_v2                                # different weights now in use
    o.handle_record(RECORD)
    assert world["db"]["alerts"].find_one()["model_version"] == "cnn-v2"


def test_reload_failure_keeps_previous_model(world):
    o = MLOrchestrator(SpyNotifier())
    o.reload_model()
    with postgres.session_scope() as s:
        s.get(ModelVersion, 1).is_active = False
        s.flush()
        v2 = s.get(ModelVersion, 2)
        v2.is_active, v2.artifact_path = True, "/nonexistent/cnn-v2"
    with pytest.raises(ModelLoadError):
        o.reload_model()
    assert o.loaded.version_label == "cnn-v1"


def test_activate_endpoint_reloads_real_orchestrator(world, artifacts, monkeypatch):
    """PUT /admin/models/{id}/activate -> ModelService -> MLOrchestrator.reload_model (real one)."""
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.services import model_service
    o = MLOrchestrator(SpyNotifier())
    o.reload_model()
    monkeypatch.setattr(model_service, "get_orchestrator", lambda: o)
    with TestClient(create_app()) as c:
        r = c.put("/admin/models/2/activate", headers=bearer("cloud_staff"))
    assert r.status_code == 200, r.text
    assert o.loaded.version_label == "cnn-v2"


def test_startup_loads_active_model(world, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import create_app
    o = MLOrchestrator(SpyNotifier())
    monkeypatch.setattr(orch_mod, "get_orchestrator", lambda: o)
    monkeypatch.setattr(get_settings(), "LOAD_MODEL_ON_STARTUP", True)
    with TestClient(create_app()):
        pass
    assert o.loaded is not None and o.loaded.version_label == "cnn-v1"
