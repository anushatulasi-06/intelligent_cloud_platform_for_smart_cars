"""ML orchestrator (C4): one record in, one alert out.

    reload_model()                      load the ACTIVE model_versions row's artifacts (startup, activation)
    preprocess_and_classify(path)       -> (category, confidence, is_anomaly, probs)
    handle_record(record)               INTERNAL (called by the C3 queue worker, not an HTTP endpoint)

handle_record steps:
  1. If an alert for this record_id already exists, return it (a retry never creates a second alert).
  2. Download the clip from S3 to a temp file, preprocess and classify.
  3. Severity and enabled flag from alert_type_config; anomaly = confidence < active threshold.
  4. alert_repo.upsert_by_record_id(record_id, doc) ($setOnInsert): at most one alert per record.
  5. Notify the owner (try/except: a notification failure never fails the record), unless the alert
     type is disabled, in which case the alert is stored with suppressed=true and nobody is notified.
Download and model errors are raised so the worker can retry the record.
"""
import logging
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import BACKEND_DIR, get_settings
from app.db import postgres, s3
from app.db.repositories import AlertRepo, ModelRepo
from app.ml.anomaly import AnomalyDetector
from app.ml.classifier import CnnClassifier
from app.ml.preprocessor import AudioPreprocessor
from app.ml.severity import SeverityPolicy
from app.services.notification_service import NotificationService

log = logging.getLogger(__name__)


class ModelNotReady(RuntimeError):
    """No active model is loaded (none active, or its artifacts could not be loaded)."""


@dataclass(frozen=True)
class LoadedModel:
    model_id: int
    version_label: str
    classifier: CnnClassifier
    preprocessor: AudioPreprocessor
    threshold: float


def model_dir_for(version_label: str, artifact_path: str | None) -> Path:
    """artifact_path from the row (relative paths resolve from backend/), else MODEL_DIR/<version>."""
    if artifact_path:
        p = Path(artifact_path)
        return p if p.is_absolute() else (BACKEND_DIR / p).resolve()
    return get_settings().MODEL_DIR / version_label


class MLOrchestrator:
    def __init__(self, notifier: NotificationService | None = None):
        self._lock = threading.Lock()
        self._loaded: LoadedModel | None = None
        self.notifier = notifier or NotificationService()

    # ---- model lifecycle ---------------------------------------------------------------------

    @property
    def loaded(self) -> LoadedModel | None:
        return self._loaded

    def reload_model(self) -> LoadedModel | None:
        """Load the active version's artifacts and swap them in atomically (thread-safe).
        Raises if the active version's artifacts are missing or broken; the previously loaded model
        stays in memory in that case. With no active version, nothing is loaded."""
        with self._lock:
            with postgres.session_scope() as session:
                active = ModelRepo(session).get_active()
                if active is None:
                    log.warning("No active model version; classification is unavailable")
                    self._loaded = None
                    return None
                model_id, label = active.id, active.version_label
                threshold = float(active.confidence_threshold)
                directory = model_dir_for(label, active.artifact_path)
            classifier = CnnClassifier.load(directory)
            loaded = LoadedModel(model_id, label, classifier, AudioPreprocessor(classifier.config), threshold)
            self._loaded = loaded               # single reference swap: in-flight predictions keep the old one
            log.info("Loaded model %s from %s (threshold %.2f)", label, directory, threshold)
            return loaded

    def _require_model(self) -> LoadedModel:
        loaded = self._loaded
        if loaded is None:
            loaded = self.reload_model()
        if loaded is None:
            raise ModelNotReady("No active model version is loaded")
        return loaded

    # ---- inference ---------------------------------------------------------------------------

    def preprocess_and_classify(self, audio_path: str | Path, threshold: float | None = None,
                                ) -> tuple[str, float, bool, dict[str, float]]:
        """-> (category, confidence, is_anomaly, probs). threshold defaults to the loaded model's."""
        loaded = self._require_model()
        probs = loaded.classifier.predict(loaded.preprocessor.preprocess(audio_path))
        category = max(probs, key=probs.get)
        confidence = probs[category]
        limit = loaded.threshold if threshold is None else threshold
        return category, confidence, AnomalyDetector.is_anomaly(confidence, limit), probs

    def handle_record(self, record: dict[str, Any]) -> str:
        """Classify one device_data_record and store its alert. Returns the alert id."""
        record_id = record["record_id"]
        alerts = AlertRepo()
        existing = alerts.get_by_record_id(record_id)
        if existing is not None:                       # retry after success: same alert, no re-notify
            return existing["id"]

        loaded = self._require_model()
        suffix = Path(record["s3_object_key"]).suffix or ".wav"
        with tempfile.TemporaryDirectory(prefix="clip-") as tmp:
            path = Path(tmp) / f"clip{suffix}"
            path.write_bytes(s3.get_object(record["s3_object_key"]))     # raises -> worker retries
            with postgres.session_scope() as session:
                active = ModelRepo(session).get(loaded.model_id)
                threshold = float(active.confidence_threshold) if active else loaded.threshold
                category, confidence, is_anomaly, probs = self.preprocess_and_classify(path, threshold)
                severity, type_enabled = SeverityPolicy(session).lookup(category)

        doc = {
            "record_id": record_id,
            "device_id": record.get("device_id"),
            "car_id": record["car_id"],
            "sound_type": category,
            "confidence": confidence,
            "probs": probs,
            "is_anomaly": is_anomaly,
            "severity": severity,
            "status": "new",
            "model_version": loaded.version_label,
            "s3_object_key": record["s3_object_key"],
            "lat": record.get("lat"),
            "lon": record.get("lon"),
            "timestamp": record.get("captured_at") or record.get("received_timestamp") or datetime.now(timezone.utc),
        }
        if not type_enabled:
            doc["suppressed"] = True
        alert = alerts.upsert_by_record_id(record_id, doc)

        if not type_enabled:
            log.info("Alert %s stored as suppressed: %s is disabled", alert["id"], category)
        else:
            try:
                self.notifier.notify_if_subscribed(alert)
            except Exception:  # noqa: BLE001 – notification must never fail alert creation
                log.exception("Notification failed for alert %s", alert["id"])
        return alert["id"]


_orchestrator: MLOrchestrator | None = None
_orchestrator_lock = threading.Lock()


def get_orchestrator() -> MLOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        with _orchestrator_lock:
            if _orchestrator is None:
                _orchestrator = MLOrchestrator()
    return _orchestrator
