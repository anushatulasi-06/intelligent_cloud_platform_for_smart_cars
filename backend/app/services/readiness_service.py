"""Staff readiness: read probes and observed in-process runtime state.

Read-only is not a billing guarantee: HeadBucket and other managed-service reads
can be metered. Do not invoke this endpoint against AWS during a zero-use review.
It never reloads a model, claims a queue record, uploads an object or sends email.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Lock
import time

from app.db import mongo, postgres, s3
from app.db.repositories import ModelRepo
from app.config import get_settings


class WorkerRuntime:
    """Track successful polling cycles; an alive but failing/stalled loop is not ready."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = Lock()
        self._workers = {}

    def register(self, name, *, enabled, max_age, thread=None):
        with self._lock:
            self._workers[name] = {"enabled": enabled, "max_age": max_age, "thread": thread,
                                   "last_success": None, "last_cycle_ok": None}

    def observe(self, name, ok):
        with self._lock:
            worker = self._workers[name]
            worker["last_cycle_ok"] = bool(ok)
            if ok:
                worker["last_success"] = self._clock()

    def snapshot(self):
        now = self._clock()
        with self._lock:
            result = {}
            for name, worker in self._workers.items():
                alive = bool(worker["thread"] and worker["thread"].is_alive())
                age = None if worker["last_success"] is None else max(0, now - worker["last_success"])
                if not worker["enabled"]:
                    status = "disabled"
                elif not alive:
                    status = "stopped"
                elif worker["last_cycle_ok"] is None:
                    status = "starting"
                elif not worker["last_cycle_ok"]:
                    status = "unavailable"
                elif age is None or age > worker["max_age"]:
                    status = "stale"
                else:
                    status = "ready"
                result[name] = {"status": status, "enabled": worker["enabled"], "alive": alive,
                                "last_success_age_seconds": None if age is None else round(age, 3)}
            return result


def _probe(call):
    try:
        detail = call()
        return {"status": "ready", **(detail if isinstance(detail, dict) else {})}
    except Exception:
        # SDK errors/URLs can include credentials. Return neither their text nor repr.
        return {"status": "unavailable"}


def _model():
    from app.services.orchestrator import get_orchestrator

    loaded = get_orchestrator().loaded  # Do not call reload_model/_require_model from a GET.
    with postgres.session_scope() as session:
        active = ModelRepo(session).get_active()
        if (loaded is None or active is None or loaded.model_id != active.id
                or loaded.version_label != active.version_label
                or loaded.classifier is None or loaded.preprocessor is None):
            raise RuntimeError("Active model is not loaded")
    return {"version_label": loaded.version_label, "model_id": loaded.model_id}


def readiness(runtime):
    probes = {"postgres": postgres.ping, "mongodb": mongo.ping, "s3": s3.ping, "model": _model}
    # Reuse datastore connection timeouts; independent reads run concurrently.
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {name: pool.submit(_probe, call) for name, call in probes.items()}
        checks = {name: future.result() for name, future in futures.items()}
    workers = runtime.snapshot() if runtime is not None else {}
    checks["workers"] = {"status": "ready" if (set(workers) == {"audio-worker", "device-sweeper"}
                         and all(row["status"] == "ready" for row in workers.values())) else "unavailable",
                         "items": workers}
    ready = all(check["status"] == "ready" for check in checks.values())
    settings = get_settings()
    return {"status": "ready" if ready else "not_ready", "checks": checks,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "scope": {"read_only": True, "s3_probe": "HeadBucket",
                      "side_effect_modes": {"email": "logged" if settings.DEV_FAKE_EMAIL else "ses",
                                            "backup": "simulated" if settings.DEV_FAKE_BACKUP else "real"},
                      "not_verified": ["S3 object read/write permissions", "SES delivery", "backup/restore",
                                       "end-to-end classification", "external network/TLS"]}}
