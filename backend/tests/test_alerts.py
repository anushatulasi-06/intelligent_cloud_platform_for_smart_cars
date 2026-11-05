"""/alerts (C4): owner scoping in the query, every status transition, 404 vs 403, summary counts,
audio links, and the notification decision table."""
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import mongomock
import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import mongo, postgres, s3
from app.db.models import AlertTypeConfig, Car, Subscription, User
from app.db.repositories import alert_repo
from app.main import create_app
from app.services.notification_service import Decision, NotificationService

T0 = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
OWNER_A = {"X-Role": "owner", "X-User-Id": "1"}     # cars 10, 11
OWNER_B = {"X-Role": "owner", "X-User-Id": "2"}     # car 20
OWNER_NONE = {"X-Role": "owner", "X-User-Id": "4"}  # owns no cars
STAFF = {"X-Role": "cloud_staff", "X-User-Id": "3"}

# (car, sound, severity, status, anomaly, hours ago)
ROWS = [
    (10, "emergency", "high", "new", False, 0),
    (10, "human", "medium", "acknowledged", True, 1),
    (11, "traffic", "low", "resolved", False, 2),
    (11, "in_vehicle", "high", "new", False, 3),
    (20, "emergency", "critical", "new", True, 4),
    (20, "environmental", "low", "acknowledged", False, 5),
]


@pytest.fixture
def mdb(monkeypatch):
    db = mongomock.MongoClient(tz_aware=True)["autosound_test"]
    monkeypatch.setattr(mongo, "get_db", lambda: db)
    return db


@pytest.fixture
def ids(sql_db, mdb):
    with postgres.session_scope() as s:
        s.add_all([User(id=1, name="A", email="a@x.dev", role="owner", password_hash="h"),
                   User(id=2, name="B", email="b@x.dev", role="owner", password_hash="h"),
                   User(id=3, name="S", email="s@x.dev", role="cloud_staff", password_hash="h"),
                   User(id=4, name="N", email="n@x.dev", role="owner", password_hash="h")])
        s.flush()
        s.add_all([Car(id=10, owner_id=1, make="Toyota", model="Camry", plate="A10"), Car(id=11, owner_id=1, make="Honda", model="Civic", plate="A11"),
                   Car(id=20, owner_id=2, make="Ford", model="Escape", plate="B20")])
    out = {}
    for i, (car, sound, sev, status, anomaly, hours) in enumerate(ROWS):
        _id = ObjectId()
        mdb["alerts"].insert_one({
            "_id": _id, "record_id": f"rec-{i}", "car_id": car, "device_id": car * 10, "sound_type": sound,
            "confidence": 0.9 if not anomaly else 0.6, "probs": {sound: 0.9}, "is_anomaly": anomaly,
            "severity": sev, "status": status, "model_version": "cnn-v3",
            "s3_object_key": f"audio/{car}/2026/11/03/rec-{i}.wav", "lat": 37.3, "lon": -121.9,
            "timestamp": T0 - timedelta(hours=hours)})
        out[i] = str(_id)
    return out


@pytest.fixture
def client(ids):
    with TestClient(create_app()) as c:
        yield c


def assert_error(r, code):
    assert r.status_code == code, r.text
    assert set(r.json()) == {"detail"} and isinstance(r.json()["detail"], str)


def by_status(alert_id):
    return {i for i, row in enumerate(ROWS)}


# ---- roles ------------------------------------------------------------------------------

def routes(ids):
    a = ids[0]
    return [("get", "/alerts", None), ("get", "/alerts/summary", None), ("get", f"/alerts/{a}", None),
            ("put", f"/alerts/{a}", {"status": "acknowledged"}), ("get", f"/alerts/{a}/audio", None)]


def test_iot_staff_403_and_no_role_401(client, ids, mdb):
    for method, url, body in routes(ids):
        assert_error(client.request(method, url, json=body, headers={"X-Role": "iot_staff"}), 403)
        assert_error(client.request(method, url, json=body), 401)
    assert mdb["alerts"].find_one({"record_id": "rec-0"})["status"] == "new"


# ---- GET /alerts --------------------------------------------------------------------------

def test_owner_sees_only_own_cars(client):
    r = client.get("/alerts", headers=OWNER_A)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 4 and {a["car_id"] for a in body["items"]} == {10, 11}
    assert client.get("/alerts", headers=OWNER_B).json()["total"] == 2
    assert client.get("/alerts", headers=OWNER_NONE).json() == {"items": [], "page": 1, "page_size": 20, "total": 0}


def test_owner_cannot_widen_scope_with_car_id(client):
    assert client.get("/alerts?car_id=20", headers=OWNER_A).json()["total"] == 0
    assert client.get("/alerts?car_id=11", headers=OWNER_A).json()["total"] == 2


def test_owner_filter_is_in_the_mongo_query(client, monkeypatch):
    """The owner's car ids must reach the Mongo filter (not be applied in Python afterwards)."""
    seen = []
    original = alert_repo.build_filter

    def spy(**kw):
        q = original(**kw)
        seen.append(q)
        return q

    monkeypatch.setattr(alert_repo, "build_filter", spy)
    client.get("/alerts", headers=OWNER_A)
    client.get("/alerts/summary", headers=OWNER_A)
    assert seen and all(q["car_id"] == {"$in": [10, 11]} for q in seen)
    seen.clear()
    client.get("/alerts", headers=STAFF)
    assert "car_id" not in seen[0]


def test_staff_sees_all_newest_first(client):
    body = client.get("/alerts", headers=STAFF).json()
    assert body["total"] == 6
    assert [a["record_id"] for a in body["items"]] == [f"rec-{i}" for i in range(6)]


def test_list_item_shape(client, ids):
    item = client.get("/alerts", headers=STAFF).json()["items"][0]
    assert set(item) == {"id", "record_id", "car_id", "device_id", "sound_type", "confidence", "is_anomaly",
                         "severity", "status", "timestamp"}
    assert item["id"] == ids[0] and len(item["id"]) == 24
    assert "probs" not in item and "s3_object_key" not in item


@pytest.mark.parametrize("query,expected", [
    ("status=new", 3), ("severity=high", 2), ("sound_type=emergency", 2), ("is_anomaly=true", 2),
    ("is_anomaly=false", 4), ("car_id=11", 2),
    ("from=2026-11-03T09:30:00Z&to=2026-11-03T11:30:00Z", 2), ("from=2026-11-03T10:00:00", 3),
    ("to=2026-11-02", 0),
])
def test_filters(client, query, expected):
    assert client.get(f"/alerts?{query}", headers=STAFF).json()["total"] == expected


def test_pagination(client):
    p2 = client.get("/alerts?page=2&page_size=4", headers=STAFF).json()
    assert (p2["page"], p2["page_size"], p2["total"], len(p2["items"])) == (2, 4, 6, 2)
    assert client.get("/alerts?page_size=100", headers=STAFF).status_code == 200


@pytest.mark.parametrize("query", ["status=open", "severity=urgent", "sound_type=music", "is_anomaly=maybe",
                                   "from=yesterday", "to=2026-13-45", "page=0", "page_size=0", "page_size=101",
                                   "car_id=abc"])
def test_list_422(client, query):
    assert_error(client.get(f"/alerts?{query}", headers=STAFF), 422)


# ---- GET /alerts/{id} -----------------------------------------------------------------------

def test_get_full_document(client, ids):
    r = client.get(f"/alerts/{ids[1]}", headers=OWNER_A)
    assert r.status_code == 200
    a = r.json()
    assert a["id"] == ids[1] and a["probs"] == {"human": 0.9} and a["model_version"] == "cnn-v3"
    assert (a["lat"], a["lon"]) == (37.3, -121.9) and a["s3_object_key"].startswith("audio/10/")
    assert a["is_anomaly"] is True


def test_other_owners_alert_is_404_like_unknown(client, ids):
    other = client.get(f"/alerts/{ids[4]}", headers=OWNER_A)          # car 20 belongs to owner B
    unknown = client.get(f"/alerts/{ObjectId()}", headers=OWNER_A)
    garbage = client.get("/alerts/not-an-id", headers=OWNER_A)
    for r in (other, unknown, garbage):
        assert_error(r, 404)
    assert other.json() == unknown.json()                              # nothing reveals that it exists
    assert client.get(f"/alerts/{ids[4]}", headers=STAFF).status_code == 200
    assert client.get(f"/alerts/{ids[4]}", headers=OWNER_B).status_code == 200


# ---- PUT /alerts/{id} -----------------------------------------------------------------------

def set_status(mdb, ids, i, status):
    mdb["alerts"].update_one({"_id": ObjectId(ids[i])}, {"$set": {"status": status}})


TRANSITIONS = [
    # role, from, to, expected status code
    ("owner", "new", "acknowledged", 200),
    ("owner", "new", "resolved", 403),
    ("owner", "acknowledged", "resolved", 403),
    ("owner", "resolved", "resolved", 403),
    ("owner", "acknowledged", "acknowledged", 409),
    ("owner", "resolved", "acknowledged", 409),
    ("owner", "acknowledged", "new", 409),
    ("owner", "new", "new", 409),
    ("cloud_staff", "new", "acknowledged", 200),
    ("cloud_staff", "acknowledged", "resolved", 200),
    ("cloud_staff", "new", "resolved", 409),              # no skipping a step
    ("cloud_staff", "acknowledged", "acknowledged", 409),
    ("cloud_staff", "resolved", "resolved", 409),
    ("cloud_staff", "resolved", "acknowledged", 409),     # no reopen
    ("cloud_staff", "resolved", "new", 409),
    ("cloud_staff", "acknowledged", "new", 409),
    ("cloud_staff", "new", "new", 409),
]


@pytest.mark.parametrize("role,start,target,code", TRANSITIONS)
def test_every_transition(client, mdb, ids, role, start, target, code):
    set_status(mdb, ids, 0, start)                      # alert 0 is on car 10 (owner A)
    headers = OWNER_A if role == "owner" else STAFF
    r = client.put(f"/alerts/{ids[0]}", json={"status": target, "note": "checked"}, headers=headers)
    doc = mdb["alerts"].find_one({"_id": ObjectId(ids[0])})
    if code == 200:
        assert r.status_code == 200, r.text
        assert r.json()["status"] == target == doc["status"]
    else:
        assert_error(r, code)
        assert doc["status"] == start                   # unchanged


def test_acknowledge_stores_who_and_when(client, mdb, ids):
    r = client.put(f"/alerts/{ids[0]}", json={"status": "acknowledged", "note": "on my way"}, headers=OWNER_A)
    doc = mdb["alerts"].find_one({"_id": ObjectId(ids[0])})
    assert doc["acknowledged_by"] == 1 and doc["acknowledged_at"] is not None
    assert "resolved_at" not in doc and "resolution_note" not in doc          # note is kept only on resolve
    assert r.json()["acknowledged_by"] == 1


def test_resolve_stores_who_when_and_note(client, mdb, ids):
    client.put(f"/alerts/{ids[0]}", json={"status": "acknowledged"}, headers=OWNER_A)
    r = client.put(f"/alerts/{ids[0]}", json={"status": "resolved", "note": "Siren confirmed"}, headers=STAFF)
    assert r.status_code == 200
    doc = mdb["alerts"].find_one({"_id": ObjectId(ids[0])})
    assert (doc["resolved_by"], doc["resolution_note"]) == (3, "Siren confirmed")
    assert doc["resolved_at"] >= doc["acknowledged_at"] and doc["acknowledged_by"] == 1
    assert r.json()["resolution_note"] == "Siren confirmed"


def test_owner_cannot_touch_other_owners_alert_404_not_403(client, mdb, ids):
    for target in ("acknowledged", "resolved"):
        assert_error(client.put(f"/alerts/{ids[4]}", json={"status": target}, headers=OWNER_A), 404)
    assert mdb["alerts"].find_one({"_id": ObjectId(ids[4])})["status"] == "new"


def test_unknown_alert_put_404(client):
    assert_error(client.put(f"/alerts/{ObjectId()}", json={"status": "acknowledged"}, headers=STAFF), 404)


@pytest.mark.parametrize("body", [{"status": "closed"}, {}, {"status": "resolved", "extra": 1},
                                  {"status": "resolved", "note": "x" * 501}])
def test_put_422(client, ids, body):
    assert_error(client.put(f"/alerts/{ids[3]}", json=body, headers=STAFF), 422)


def test_concurrent_change_is_409(client, mdb, ids, monkeypatch):
    """Someone else acknowledged between our read and our write: the conditional update finds nothing."""
    original = alert_repo.AlertRepo.transition

    def racing(self, alert_id, car_ids, from_status, fields):
        self.collection.update_one({"_id": ObjectId(alert_id)}, {"$set": {"status": "acknowledged"}})
        return original(self, alert_id, car_ids, from_status, fields)

    monkeypatch.setattr(alert_repo.AlertRepo, "transition", racing)
    r = client.put(f"/alerts/{ids[0]}", json={"status": "acknowledged"}, headers=STAFF)
    assert_error(r, 409)
    assert "already acknowledged" in r.json()["detail"]


# ---- GET /alerts/summary --------------------------------------------------------------------

def test_summary_staff(client):
    r = client.get("/alerts/summary", headers=STAFF)
    assert r.status_code == 200
    assert r.json() == {"total": 6, "by_status": {"new": 3, "acknowledged": 2, "resolved": 1},
                        "by_severity": {"high": 2, "medium": 1, "low": 2, "critical": 1},
                        "by_type": {"emergency": 2, "human": 1, "traffic": 1, "in_vehicle": 1, "environmental": 1},
                        "anomalies": 2}


def test_summary_owner_scoped(client):
    s = client.get("/alerts/summary", headers=OWNER_A).json()
    assert s["total"] == 4 and s["anomalies"] == 1 and "critical" not in s["by_severity"]
    assert client.get("/alerts/summary?car_id=20", headers=OWNER_A).json()["total"] == 0
    assert client.get("/alerts/summary", headers=OWNER_NONE).json() == {
        "total": 0, "by_status": {}, "by_severity": {}, "by_type": {}, "anomalies": 0}


def test_summary_filters(client):
    s = client.get("/alerts/summary?from=2026-11-03T07:30:00Z&car_id=20", headers=STAFF).json()
    assert s["total"] == 1 and s["by_type"] == {"emergency": 1} and s["anomalies"] == 1
    assert_error(client.get("/alerts/summary?from=nope", headers=STAFF), 422)


# ---- GET /alerts/{id}/audio -----------------------------------------------------------------

def test_audio_presigned_url(client, ids):
    s3.configure(s3.make_client(aws_access_key_id="testing", aws_secret_access_key="testing"))
    try:
        r = client.get(f"/alerts/{ids[0]}/audio", headers=OWNER_A)
    finally:
        s3.configure(None)
    assert r.status_code == 200
    body = r.json()
    assert body["expires_in"] == 300
    parsed = urlparse(body["url"])
    assert parsed.path.endswith("audio/10/2026/11/03/rec-0.wav")
    assert parse_qs(parsed.query)["X-Amz-Expires"] == ["300"]


def test_audio_404_cases(client, mdb, ids):
    mdb["alerts"].update_one({"_id": ObjectId(ids[1])}, {"$unset": {"s3_object_key": ""}})
    assert_error(client.get(f"/alerts/{ids[1]}/audio", headers=STAFF), 404)       # no key
    assert_error(client.get(f"/alerts/{ids[4]}/audio", headers=OWNER_A), 404)     # other owner
    assert_error(client.get(f"/alerts/{ObjectId()}/audio", headers=STAFF), 404)   # unknown


# ---- notifications (DT-C4-2) ----------------------------------------------------------------

class FakeSES:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send_email(self, **kw):
        if self.fail:
            raise RuntimeError("MessageRejected: Email address is not verified")
        self.sent.append(kw)
        return {"MessageId": "m-1"}


ALERT = {"id": "a1", "car_id": 10, "sound_type": "emergency", "severity": "high", "confidence": 0.93,
         "is_anomaly": False, "timestamp": T0}


def setup_notify(sub: dict | None, type_enabled: bool | None = True):
    with postgres.session_scope() as s:
        if type_enabled is not None:
            s.add(AlertTypeConfig(alert_type="emergency", default_severity="high", enabled=type_enabled))
        if sub is not None:
            s.add(Subscription(owner_id=1, car_id=10, alert_type="emergency", **sub))


@pytest.mark.parametrize("sub,type_enabled,expected", [
    (None, True, Decision.NO_SUBSCRIPTION),
    ({"enabled": False, "notify_method": "email"}, True, Decision.SUBSCRIPTION_DISABLED),
    ({"enabled": True, "notify_method": "email"}, False, Decision.TYPE_DISABLED),
    ({"enabled": True, "notify_method": "email"}, None, Decision.TYPE_DISABLED),      # no config row
    ({"enabled": True, "notify_method": "in_app"}, True, Decision.IN_APP),
])
def test_notification_skips_and_in_app(ids, sub, type_enabled, expected):
    setup_notify(sub, type_enabled)
    ses = FakeSES()
    assert NotificationService(ses).notify_if_subscribed(ALERT) == expected
    assert ses.sent == []


def test_email_dev_fake_only_logs(ids, caplog, monkeypatch):
    monkeypatch.setattr(get_settings(), "DEV_FAKE_EMAIL", True)
    setup_notify({"enabled": True, "notify_method": "email"})
    ses = FakeSES()
    with caplog.at_level("INFO"):
        assert NotificationService(ses).notify_if_subscribed(ALERT) == Decision.EMAIL_LOGGED
    assert ses.sent == [] and "a@x.dev" in caplog.text


def test_email_sent_through_ses_to_car_owner(ids, monkeypatch):
    monkeypatch.setattr(get_settings(), "DEV_FAKE_EMAIL", False)
    setup_notify({"enabled": True, "notify_method": "email"})
    ses = FakeSES()
    assert NotificationService(ses).notify_if_subscribed(ALERT) == Decision.EMAIL_SENT
    [msg] = ses.sent
    assert msg["Destination"] == {"ToAddresses": ["a@x.dev"]}
    assert msg["Source"] == get_settings().SES_FROM_EMAIL
    assert "emergency" in msg["Message"]["Subject"]["Data"] and "A10" in msg["Message"]["Subject"]["Data"]
    assert "93%" in msg["Message"]["Body"]["Text"]["Data"]


def test_other_owners_subscription_does_not_count(ids, monkeypatch):
    monkeypatch.setattr(get_settings(), "DEV_FAKE_EMAIL", False)
    with postgres.session_scope() as s:
        s.add(AlertTypeConfig(alert_type="emergency", default_severity="high", enabled=True))
        s.add(Subscription(owner_id=2, car_id=10, alert_type="emergency", enabled=True, notify_method="email"))
    ses = FakeSES()
    assert NotificationService(ses).notify_if_subscribed(ALERT) == Decision.NO_SUBSCRIPTION
    assert ses.sent == []


def test_ses_failure_is_logged_not_raised(ids, monkeypatch, caplog):
    monkeypatch.setattr(get_settings(), "DEV_FAKE_EMAIL", False)
    setup_notify({"enabled": True, "notify_method": "email"})
    with caplog.at_level("ERROR"):
        assert NotificationService(FakeSES(fail=True)).notify_if_subscribed(ALERT) == Decision.EMAIL_FAILED
    assert "SES send failed" in caplog.text


def test_database_failure_is_logged_not_raised(ids, monkeypatch):
    def broken():
        raise ConnectionError("postgres down")
    monkeypatch.setattr(postgres, "get_session_factory", lambda: broken)
    assert NotificationService(FakeSES()).notify_if_subscribed(ALERT) == Decision.ERROR


def test_unknown_car_skips(ids):
    assert NotificationService(FakeSES()).notify_if_subscribed({**ALERT, "car_id": 999}) == Decision.UNKNOWN_CAR


def test_audio_without_aws_credentials_is_503(client, ids, monkeypatch):
    from botocore.exceptions import NoCredentialsError

    def no_creds(key, expires=300):
        raise NoCredentialsError()

    monkeypatch.setattr(s3, "presigned_url", no_creds)
    r = client.get(f"/alerts/{ids[0]}/audio", headers=OWNER_A)
    assert_error(r, 503)
    assert "AWS credentials" in r.json()["detail"]


# ---- dev: LOCAL_AUDIO_DIR -------------------------------------------------------------------

def test_audio_local_dir_link_and_file(client, ids, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "LOCAL_AUDIO_DIR", tmp_path)
    clip = tmp_path / "audio/10/2026/11/03/rec-0.wav"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"RIFF0000WAVE")
    r = client.get(f"/alerts/{ids[0]}/audio", headers=OWNER_A)
    assert r.status_code == 200
    url = r.json()["url"]
    assert url == "http://testserver/local-audio/audio/10/2026/11/03/rec-0.wav"
    f = client.get(urlparse(url).path)                    # no role headers needed, like a presigned URL
    assert f.status_code == 200 and f.content == b"RIFF0000WAVE"
    assert f.headers["content-type"] == "audio/wav"


def test_audio_local_dir_missing_file_is_404(client, ids, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "LOCAL_AUDIO_DIR", tmp_path)
    r = client.get(f"/alerts/{ids[0]}/audio", headers=OWNER_A)
    assert_error(r, 404)
    assert "make_local_audio" in r.json()["detail"]
    assert_error(client.get(f"/alerts/{ids[4]}/audio", headers=OWNER_A), 404)     # other owner still 404


def test_local_audio_route_off_and_traversal(client, tmp_path, monkeypatch):
    secret = tmp_path / "secret.txt"
    secret.write_text("x")
    monkeypatch.setattr(get_settings(), "LOCAL_AUDIO_DIR", None)
    assert_error(client.get("/local-audio/secret.txt"), 404)                      # off by default
    root = tmp_path / "clips"
    root.mkdir()
    monkeypatch.setattr(get_settings(), "LOCAL_AUDIO_DIR", root)
    assert_error(client.get("/local-audio/..%2Fsecret.txt"), 404)
    assert_error(client.get("/local-audio/%2E%2E/secret.txt"), 404)
