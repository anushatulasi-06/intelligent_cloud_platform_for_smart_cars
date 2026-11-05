"""/admin/alert-types and /admin/models (C5 Config): roles, status codes, error format,
the single-active-model invariant and rollback behaviour."""
from datetime import date, datetime, timezone
from decimal import Decimal

import mongomock
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import mongo, postgres
from app.db.models import AlertTypeConfig, ModelVersion
from app.db.repositories import ModelRepo
from app.main import create_app
from app.services import model_service

STAFF = {"X-Role": "cloud_staff"}


# ---- fixtures ---------------------------------------------------------------------------

@pytest.fixture
def artifacts(tmp_path):
    """cnn-v1..v3 have model_weights.pt; cnn-v4 has a folder but no weights file."""
    for label in ("cnn-v1", "cnn-v2", "cnn-v3"):
        (tmp_path / label).mkdir()
        (tmp_path / label / "model_weights.pt").write_bytes(b"weights")
    (tmp_path / "cnn-v4").mkdir()
    return tmp_path


@pytest.fixture
def data(sql_db, artifacts):
    with postgres.session_scope() as s:
        for t, sev in [("emergency", "high"), ("in_vehicle", "high"), ("human", "medium"),
                       ("traffic", "low"), ("environmental", "low")]:
            s.add(AlertTypeConfig(alert_type=t, default_severity=sev, description=f"{t} sounds", enabled=True))
        rows = [("cnn-v1", "0.740", "0.70", False), ("cnn-v2", "0.810", "0.75", False),
                ("cnn-v3", "0.860", "0.75", True), ("cnn-v4", "0.880", "0.75", False)]
        for i, (label, acc, thr, active) in enumerate(rows, start=1):
            s.add(ModelVersion(id=i, version_label=label, trained_date=date(2026, 9, 10 + i),
                               accuracy=Decimal(acc), confidence_threshold=Decimal(thr),
                               artifact_path=str(artifacts / label), is_active=active))
    return {"v1": 1, "v2": 2, "v3": 3, "v4": 4}


class SpyOrchestrator:
    """Records reload calls and which model the DATABASE says is active at that moment
    (proves reload happens after commit)."""

    def __init__(self, fail_times: int = 0):
        self.calls: list[str | None] = []
        self.fail_times = fail_times

    def reload_model(self):
        with postgres.session_scope() as s:
            active = ModelRepo(s).get_active()
            self.calls.append(active.version_label if active else None)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("could not load weights")


@pytest.fixture
def spy(monkeypatch):
    s = SpyOrchestrator()
    monkeypatch.setattr(model_service, "get_orchestrator", lambda: s)
    return s


@pytest.fixture
def client(data):
    with TestClient(create_app()) as c:
        yield c


def active_labels() -> list[str]:
    with postgres.session_scope() as s:
        return [m.version_label for m in s.scalars(select(ModelVersion).where(ModelVersion.is_active.is_(True)))]


def assert_error(r, status: int):
    assert r.status_code == status, r.text
    body = r.json()
    assert set(body) == {"detail"} and isinstance(body["detail"], str) and body["detail"]


# ---- roles ------------------------------------------------------------------------------

ROUTES = [("get", "/admin/alert-types", None), ("put", "/admin/alert-types/human", {"enabled": False}),
          ("get", "/admin/models", None), ("put", "/admin/models/1", {"confidence_threshold": 0.8}),
          ("put", "/admin/models/1/activate", None)]


@pytest.mark.parametrize("method,url,body", ROUTES)
def test_no_role_is_401(client, method, url, body):
    assert_error(client.request(method, url, json=body), 401)


@pytest.mark.parametrize("role", ["owner", "iot_staff"])
@pytest.mark.parametrize("method,url,body", ROUTES)
def test_other_roles_are_403(client, method, url, body, role):
    assert_error(client.request(method, url, json=body, headers={"X-Role": role}), 403)
    assert active_labels() == ["cnn-v3"]                       # nothing changed


# ---- GET /admin/alert-types ---------------------------------------------------------------

def test_list_alert_types(client):
    r = client.get("/admin/alert-types", headers=STAFF)
    assert r.status_code == 200
    rows = r.json()
    assert [t["alert_type"] for t in rows] == ["emergency", "environmental", "human", "in_vehicle", "traffic"]
    assert rows[0] == {"alert_type": "emergency", "default_severity": "high",
                       "description": "emergency sounds", "enabled": True}


# ---- PUT /admin/alert-types/{alert_type} -------------------------------------------------

def test_update_alert_type_partial(client):
    r = client.put("/admin/alert-types/human", json={"default_severity": "critical"}, headers=STAFF)
    assert r.status_code == 200
    assert r.json() == {"alert_type": "human", "default_severity": "critical",
                        "description": "human sounds", "enabled": True}
    r = client.put("/admin/alert-types/human", json={"description": "Scream", "enabled": False}, headers=STAFF)
    assert r.json() == {"alert_type": "human", "default_severity": "critical", "description": "Scream",
                        "enabled": False}
    with postgres.session_scope() as s:                                  # committed
        assert s.get(AlertTypeConfig, "human").default_severity == "critical"


def test_update_alert_type_environmental_fits(client):
    """'environmental' is 13 characters: the reason alert_type is VARCHAR(16)."""
    r = client.put("/admin/alert-types/environmental", json={"default_severity": "medium"}, headers=STAFF)
    assert r.status_code == 200 and r.json()["default_severity"] == "medium"


def test_update_unknown_alert_type_404(client):
    assert_error(client.put("/admin/alert-types/music", json={"enabled": False}, headers=STAFF), 404)


@pytest.mark.parametrize("body", [
    {"default_severity": "urgent"}, {"default_severity": "HIGH"}, {"default_severity": None},
    {"enabled": None}, {"description": "x" * 121}, {"color": "red"}, {"enabled": "maybe"},
])
def test_update_alert_type_422(client, body):
    r = client.put("/admin/alert-types/human", json=body, headers=STAFF)
    assert_error(r, 422)
    with postgres.session_scope() as s:
        row = s.get(AlertTypeConfig, "human")
        assert (row.default_severity, row.description, row.enabled) == ("medium", "human sounds", True)


def test_bad_severity_message(client):
    r = client.put("/admin/alert-types/human", json={"default_severity": "urgent"}, headers=STAFF)
    assert "default_severity" in r.json()["detail"] and "low" in r.json()["detail"]


def test_update_alert_type_never_rewrites_existing_alerts(client):
    """BR-CF-3: existing alert documents keep the severity they were created with."""
    mdb = mongomock.MongoClient(tz_aware=True)
    mongo.configure(mdb)
    try:
        alerts = mdb.get_default_database("autosound_test")["alerts"]
        alerts.insert_many([{"record_id": f"r{i}", "sound_type": "human", "severity": "medium",
                             "timestamp": datetime(2026, 11, 1, i, tzinfo=timezone.utc)} for i in range(3)])
        before = list(alerts.find({}, {"_id": 0}))
        r = client.put("/admin/alert-types/human", json={"default_severity": "critical"}, headers=STAFF)
        assert r.status_code == 200
        assert list(alerts.find({}, {"_id": 0})) == before
    finally:
        mongo.configure(None)


# ---- GET /admin/models --------------------------------------------------------------------

def test_list_models(client):
    r = client.get("/admin/models", headers=STAFF)
    assert r.status_code == 200
    rows = r.json()
    assert [m["version_label"] for m in rows] == ["cnn-v4", "cnn-v3", "cnn-v2", "cnn-v1"]
    v3 = rows[1]
    assert v3 == {"id": 3, "version_label": "cnn-v3", "trained_date": "2026-09-13", "accuracy": 0.86,
                  "confidence_threshold": 0.75, "is_active": True}
    assert set(rows[0]) == {"id", "version_label", "trained_date", "accuracy", "confidence_threshold", "is_active"}


# ---- PUT /admin/models/{id} -----------------------------------------------------------------

@pytest.mark.parametrize("value,stored", [(0.8, 0.8), (0.5, 0.5), (0.95, 0.95), (0.725, 0.72)])
def test_set_threshold(client, data, value, stored):
    r = client.put(f"/admin/models/{data['v2']}", json={"confidence_threshold": value}, headers=STAFF)
    assert r.status_code == 200, r.text
    assert r.json()["confidence_threshold"] == pytest.approx(stored, abs=0.011)
    assert r.json()["id"] == data["v2"] and r.json()["version_label"] == "cnn-v2"


@pytest.mark.parametrize("body", [{"confidence_threshold": 0.49}, {"confidence_threshold": 0.96},
                                  {"confidence_threshold": -1}, {"confidence_threshold": "high"},
                                  {}, {"confidence_threshold": 0.8, "extra": 1}])
def test_set_threshold_422(client, data, body):
    assert_error(client.put(f"/admin/models/{data['v2']}", json=body, headers=STAFF), 422)
    with postgres.session_scope() as s:
        assert s.get(ModelVersion, data["v2"]).confidence_threshold == Decimal("0.75")


def test_threshold_message_names_range(client, data):
    r = client.put(f"/admin/models/{data['v2']}", json={"confidence_threshold": 0.96}, headers=STAFF)
    assert "confidence_threshold" in r.json()["detail"] and "0.95" in r.json()["detail"]


def test_set_threshold_unknown_model_404(client):
    assert_error(client.put("/admin/models/999", json={"confidence_threshold": 0.8}, headers=STAFF), 404)


def test_non_integer_model_id_422(client):
    assert_error(client.put("/admin/models/abc", json={"confidence_threshold": 0.8}, headers=STAFF), 422)


# ---- PUT /admin/models/{id}/activate --------------------------------------------------------

def test_activate_success(client, data, spy):
    r = client.put(f"/admin/models/{data['v2']}/activate", headers=STAFF)
    assert r.status_code == 200, r.text
    assert r.json() == {"id": data["v2"], "version_label": "cnn-v2", "is_active": True}
    assert active_labels() == ["cnn-v2"]
    # reload ran once, AFTER the commit (the DB already showed cnn-v2 as active)
    assert spy.calls == ["cnn-v2"]
    listed = {m["version_label"]: m["is_active"] for m in client.get("/admin/models", headers=STAFF).json()}
    assert listed == {"cnn-v1": False, "cnn-v2": True, "cnn-v3": False, "cnn-v4": False}


def test_activate_unknown_404(client, spy):
    assert_error(client.put("/admin/models/999/activate", headers=STAFF), 404)
    assert active_labels() == ["cnn-v3"] and spy.calls == []


def test_activate_already_active_409(client, data, spy):
    assert_error(client.put(f"/admin/models/{data['v3']}/activate", headers=STAFF), 409)
    assert active_labels() == ["cnn-v3"] and spy.calls == []


def test_activate_missing_weights_422_keeps_old_model(client, data, spy):
    r = client.put(f"/admin/models/{data['v4']}/activate", headers=STAFF)
    assert_error(r, 422)
    assert "model_weights.pt" in r.json()["detail"]
    assert active_labels() == ["cnn-v3"]                       # old model still active
    assert spy.calls == []                                     # nothing reloaded


def test_activate_weights_deleted_after_listing_422(client, data, artifacts, spy):
    (artifacts / "cnn-v1" / "model_weights.pt").unlink()
    assert_error(client.put(f"/admin/models/{data['v1']}/activate", headers=STAFF), 422)
    assert active_labels() == ["cnn-v3"]


def test_activate_without_artifact_path_422(client, data, spy):
    with postgres.session_scope() as s:
        s.get(ModelVersion, data["v2"]).artifact_path = None
    assert_error(client.put(f"/admin/models/{data['v2']}/activate", headers=STAFF), 422)
    assert active_labels() == ["cnn-v3"]


def test_failure_inside_transaction_rolls_back(client, data, spy, monkeypatch):
    """Deactivate succeeds, then the second write fails: the whole transaction rolls back."""
    from sqlalchemy import update

    def half_activate(self, model_id):
        self.session.execute(update(ModelVersion).values(is_active=False))
        self.session.flush()
        raise RuntimeError("database went away")

    monkeypatch.setattr(ModelRepo, "activate", half_activate)
    assert_error(client.put(f"/admin/models/{data['v2']}/activate", headers=STAFF), 500)
    assert active_labels() == ["cnn-v3"] and spy.calls == []


def test_reload_failure_restores_previous_model(client, data, monkeypatch):
    spy = SpyOrchestrator(fail_times=1)                         # new weights fail to load
    monkeypatch.setattr(model_service, "get_orchestrator", lambda: spy)
    r = client.put(f"/admin/models/{data['v2']}/activate", headers=STAFF)
    assert_error(r, 500)
    assert "cnn-v3 is still active" in r.json()["detail"]
    assert active_labels() == ["cnn-v3"]
    assert spy.calls == ["cnn-v2", "cnn-v3"]                    # tried new, then reloaded the old one


def test_concurrent_activation_conflict_409(client, data, spy, monkeypatch):
    """If the partial unique index rejects a second active row, answer 409 and change nothing."""
    from sqlalchemy.exc import IntegrityError

    def collide(self, model_id):
        raise IntegrityError("UPDATE model_versions", {}, Exception("uq_model_versions_one_active"))

    monkeypatch.setattr(ModelRepo, "activate", collide)
    assert_error(client.put(f"/admin/models/{data['v2']}/activate", headers=STAFF), 409)
    assert active_labels() == ["cnn-v3"]


def test_single_active_invariant_over_many_activations(client, data, spy):
    for label in ("v1", "v2", "v1", "v3", "v2"):
        r = client.put(f"/admin/models/{data[label]}/activate", headers=STAFF)
        assert r.status_code == 200
        assert len(active_labels()) == 1
    assert active_labels() == ["cnn-v2"]
    assert spy.calls == ["cnn-v1", "cnn-v2", "cnn-v1", "cnn-v3", "cnn-v2"]


def test_activate_when_no_model_active(client, data, spy):
    with postgres.session_scope() as s:
        s.get(ModelVersion, data["v3"]).is_active = False
    assert client.put(f"/admin/models/{data['v1']}/activate", headers=STAFF).status_code == 200
    assert active_labels() == ["cnn-v1"]


def test_reload_failure_with_no_previous_model_leaves_none_active(client, data, monkeypatch):
    with postgres.session_scope() as s:
        s.get(ModelVersion, data["v3"]).is_active = False
    spy = SpyOrchestrator(fail_times=1)
    monkeypatch.setattr(model_service, "get_orchestrator", lambda: spy)
    r = client.put(f"/admin/models/{data['v1']}/activate", headers=STAFF)
    assert_error(r, 500)
    assert active_labels() == []


def test_relative_artifact_path_resolves_from_backend():
    from app.config import BACKEND_DIR
    assert model_service.weights_path("../model/artifacts/cnn-v3") == \
        BACKEND_DIR / "../model/artifacts/cnn-v3" / "model_weights.pt"
