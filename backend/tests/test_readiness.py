"""Readiness uses fakes only: no object uploads, email, model reload or queue claims."""
from contextlib import contextmanager
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.config import Settings
from app.services import readiness_service as svc
from app.services.processing_worker import ProcessingWorker
from tests.auth_headers import bearer


class FakeThread:
    def __init__(self):
        self.alive = True

    def is_alive(self):
        return self.alive


def runtime(clock=lambda: 100):
    tracker = svc.WorkerRuntime(clock=clock)
    for name in ("audio-worker", "device-sweeper"):
        tracker.register(name, enabled=True, max_age=60, thread=FakeThread())
        tracker.observe(name, True)
    return tracker


@pytest.fixture
def probes(monkeypatch):
    calls = []
    for name, module in (("postgres", svc.postgres), ("mongodb", svc.mongo), ("s3", svc.s3)):
        def ping(name=name):
            calls.append(name)
            return 1.2  # Existing ping functions return numeric timing, not mappings.
        monkeypatch.setattr(module, "ping", ping)
    monkeypatch.setattr(svc, "_model", lambda: {"model_id": 1, "version_label": "cnn-v1"})
    for name in ("put_object", "upload_file", "get_object"):
        monkeypatch.setattr(svc.s3, name, lambda *a, **kw: pytest.fail("readiness attempted object I/O"))
    return calls


def test_readiness_role_guards_do_not_probe_stores(probes):
    with TestClient(create_app()) as client:
        assert client.get("/admin/readiness").status_code == 401
        for role in ("owner", "iot_staff"):
            assert client.get("/admin/readiness", headers=bearer(role)).status_code == 403
    assert probes == []


def test_readiness_success_only_after_all_required_checks(probes):
    with TestClient(create_app()) as client:
        client.app.state.worker_runtime = runtime()
        response = client.get("/admin/readiness", headers=bearer("cloud_staff"))
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert set(probes) == {"postgres", "mongodb", "s3"}
    assert body["checks"]["model"]["version_label"] == "cnn-v1"
    assert body["scope"]["s3_probe"] == "HeadBucket"
    assert body["scope"]["side_effect_modes"] == {"email": "logged", "backup": "simulated"}
    assert "S3 object read/write permissions" in body["scope"]["not_verified"]
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("dependency", ["postgres", "mongodb", "s3", "model"])
def test_readiness_fails_closed_without_leaking_vendor_errors(probes, monkeypatch, dependency):
    def unavailable():
        raise RuntimeError("https://secret:password@provider.example/?token=private")
    module = {"postgres": svc.postgres, "mongodb": svc.mongo, "s3": svc.s3}.get(dependency)
    monkeypatch.setattr(module or svc, "ping" if module else "_model", unavailable)
    with TestClient(create_app()) as client:
        client.app.state.worker_runtime = runtime()
        response = client.get("/admin/readiness", headers=bearer("cloud_staff"))
    assert response.status_code == 503
    assert response.json()["checks"][dependency] == {"status": "unavailable"}
    assert all(secret not in response.text for secret in ("secret", "password", "provider.example", "token"))


def test_alive_worker_requires_successful_recent_cycle(probes):
    now = [100]
    tracker = runtime(lambda: now[0])
    tracker.observe("audio-worker", False)
    assert svc.readiness(tracker)["status"] == "not_ready"
    tracker.observe("audio-worker", True)
    assert svc.readiness(tracker)["status"] == "ready"
    now[0] = 161
    result = svc.readiness(tracker)
    assert result["status"] == "not_ready"
    assert result["checks"]["workers"]["items"]["audio-worker"]["status"] == "stale"


@pytest.mark.parametrize("enabled,thread,status", [
    (False, None, "disabled"), (True, None, "stopped"), (True, FakeThread(), "starting")])
def test_disabled_stopped_and_starting_workers_are_not_ready(probes, enabled, thread, status):
    tracker = runtime()
    tracker.register("audio-worker", enabled=enabled, max_age=60, thread=thread)
    result = svc.readiness(tracker)
    assert result["status"] == "not_ready"
    assert result["checks"]["workers"]["items"]["audio-worker"]["status"] == status


def test_startup_does_not_claim_disabled_workers_are_ready(probes):
    with TestClient(create_app()) as client:
        response = client.get("/admin/readiness", headers=bearer("cloud_staff"))
    assert response.status_code == 503
    assert all(row["status"] == "disabled" for row in response.json()["checks"]["workers"]["items"].values())


def test_startup_and_shutdown_observe_real_worker_threads_with_fake_tasks(probes, monkeypatch):
    from app.services import device_service, processing_worker
    monkeypatch.setattr(processing_worker, "RecordRepo", lambda: object())
    monkeypatch.setattr(ProcessingWorker, "run_once", lambda self: False)
    monkeypatch.setattr(device_service, "sweep_devices", lambda: None)
    settings = Settings(_env_file=None, PROCESSING_WORKER_ENABLED=True, DEVICE_SWEEPER_ENABLED=True,
                        PROCESSING_WORKER_POLL_SECONDS=.01)
    with TestClient(create_app(settings)) as client:
        tracker = client.app.state.worker_runtime
        deadline = time.monotonic() + 2
        while any(row["status"] != "ready" for row in tracker.snapshot().values()):
            assert time.monotonic() < deadline
            time.sleep(.005)
        assert client.get("/admin/readiness", headers=bearer("cloud_staff")).status_code == 200
    assert all(not row["alive"] for row in tracker.snapshot().values())


@pytest.mark.parametrize("loaded_id,active_id,loaded_label,active_label", [
    (None, 1, "cnn-v1", "cnn-v1"), (1, None, "cnn-v1", "cnn-v1"),
    (1, 2, "cnn-v1", "cnn-v2"), (1, 1, "cnn-v1", "cnn-v2"), (1, 1, "cnn-v1", "cnn-v1")])
def test_model_readiness_observes_loaded_model_without_reloading(monkeypatch, loaded_id, active_id,
                                                               loaded_label, active_label):
    from app.services import orchestrator
    loaded = None if loaded_id is None else SimpleNamespace(model_id=loaded_id, version_label=loaded_label,
                                                           classifier=object(), preprocessor=object())
    active = None if active_id is None else SimpleNamespace(id=active_id, version_label=active_label)
    monkeypatch.setattr(orchestrator, "get_orchestrator", lambda: SimpleNamespace(loaded=loaded))
    @contextmanager
    def session_scope():
        yield object()
    monkeypatch.setattr(svc.postgres, "session_scope", session_scope)
    monkeypatch.setattr(svc, "ModelRepo", lambda session: SimpleNamespace(get_active=lambda: active))
    if loaded_id == active_id and loaded_id is not None and loaded_label == active_label:
        assert svc._model() == {"model_id": 1, "version_label": "cnn-v1"}
    else:
        with pytest.raises(RuntimeError):
            svc._model()


def test_processing_observer_tracks_queue_failure_and_recovery(monkeypatch):
    class Stop:
        cycles = 0
        def is_set(self):
            return self.cycles >= 2
        def wait(self, seconds):
            self.cycles += 1
    worker = ProcessingWorker(repo=object())
    calls = []
    def run_once():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("queue down")
        return False
    monkeypatch.setattr(worker, "run_once", run_once)
    observations = []
    worker.run(Stop(), on_cycle=observations.append)
    assert observations == [False, True]
