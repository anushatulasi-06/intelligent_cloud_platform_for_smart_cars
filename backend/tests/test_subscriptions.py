"""/subscriptions (C1, F3): owner scoping, create vs update, validation and delete."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import postgres
from app.db.models import Car, Subscription, User
from app.db.repositories import SubscriptionRepo
from app.main import create_app
from tests.auth_headers import bearer

OWNER_A = bearer("owner", 1)        # cars 10, 11
OWNER_B = bearer("owner", 2)        # car 20
STAFF = bearer("cloud_staff", 4)
IOT = bearer("iot_staff", 3)


@pytest.fixture
def data(sql_db):
    with postgres.session_scope() as s:
        s.add_all([User(id=1, name="A", email="a@x.dev", role="owner", password_hash="h"),
                   User(id=2, name="B", email="b@x.dev", role="owner", password_hash="h"),
                   User(id=3, name="I", email="i@x.dev", role="iot_staff", password_hash="h"),
                   User(id=4, name="C", email="c@x.dev", role="cloud_staff", password_hash="h")])
        s.flush()
        s.add_all([Car(id=10, owner_id=1, make="Toyota", model="Camry", plate="A10"),
                   Car(id=11, owner_id=1, make="Honda", model="Civic", plate="A11"),
                   Car(id=20, owner_id=2, make="Ford", model="Escape", plate="B20")])
        s.flush()
        s.add_all([
            Subscription(id=1, owner_id=1, car_id=10, alert_type="emergency", enabled=True,
                         notify_method="email"),
            Subscription(id=2, owner_id=1, car_id=10, alert_type="human", enabled=False,
                         notify_method="email"),
            Subscription(id=3, owner_id=1, car_id=11, alert_type="traffic", enabled=True,
                         notify_method="in_app"),
            Subscription(id=4, owner_id=2, car_id=20, alert_type="emergency", enabled=True,
                         notify_method="email"),
        ])


@pytest.fixture
def client(data):
    with TestClient(create_app()) as c:
        yield c


def rows_in_db():
    with postgres.session_scope() as s:
        return list(s.scalars(select(Subscription).order_by(Subscription.id)))


# ---- GET /subscriptions ------------------------------------------------------------------

def test_owner_sees_only_their_own_rows(client):
    body = client.get("/subscriptions", headers=OWNER_A).json()
    assert [r["id"] for r in body] == [1, 2, 3]
    assert client.get("/subscriptions", headers=OWNER_B).json()[0]["id"] == 4


def test_list_can_be_filtered_by_car(client):
    body = client.get("/subscriptions", headers=OWNER_A, params={"car_id": 11}).json()
    assert body == [{"id": 3, "car_id": 11, "alert_type": "traffic", "enabled": True,
                     "notify_method": "in_app"}]


def test_asking_about_someone_elses_car_is_403(client):
    assert client.get("/subscriptions", headers=OWNER_A, params={"car_id": 20}).status_code == 403


@pytest.mark.parametrize("headers,expected", [(None, 401), (STAFF, 403), (IOT, 403)])
def test_list_roles(client, headers, expected):
    assert client.get("/subscriptions", headers=headers).status_code == expected


# ---- POST /subscriptions -----------------------------------------------------------------

def test_new_category_is_created(client):
    body = {"car_id": 10, "alert_type": "traffic", "enabled": True, "notify_method": "in_app"}
    r = client.post("/subscriptions", headers=OWNER_A, json=body)
    assert r.status_code == 201
    assert r.json()["alert_type"] == "traffic" and r.json()["notify_method"] == "in_app"
    assert len(rows_in_db()) == 5


def test_existing_category_is_updated_in_place(client):
    body = {"car_id": 10, "alert_type": "emergency", "enabled": False, "notify_method": "in_app"}
    r = client.post("/subscriptions", headers=OWNER_A, json=body)
    assert r.status_code == 200
    assert r.json()["id"] == 1 and r.json()["enabled"] is False
    assert len(rows_in_db()) == 4                      # no duplicate row


def test_turning_a_category_back_on(client):
    body = {"car_id": 10, "alert_type": "human", "enabled": True, "notify_method": "email"}
    assert client.post("/subscriptions", headers=OWNER_A, json=body).status_code == 200
    with postgres.session_scope() as s:
        enabled, method = SubscriptionRepo(s).is_enabled(10, "human")
    assert enabled is True and method == "email"


def test_cannot_subscribe_to_someone_elses_car(client):
    body = {"car_id": 20, "alert_type": "emergency", "enabled": True, "notify_method": "email"}
    assert client.post("/subscriptions", headers=OWNER_A, json=body).status_code == 403
    assert len(rows_in_db()) == 4


@pytest.mark.parametrize("body", [
    {"car_id": 10, "alert_type": "smoke", "enabled": True},
    {"car_id": 10, "alert_type": "emergency", "enabled": True, "notify_method": "sms"},
    {"car_id": 10, "alert_type": "emergency"},
    {"car_id": 0, "alert_type": "emergency", "enabled": True},
    {"car_id": 10, "alert_type": "emergency", "enabled": True, "extra": 1},
])
def test_invalid_bodies_are_422(client, body):
    assert client.post("/subscriptions", headers=OWNER_A, json=body).status_code == 422


def test_notify_method_defaults_to_email(client):
    r = client.post("/subscriptions", headers=OWNER_A,
                    json={"car_id": 11, "alert_type": "human", "enabled": True})
    assert r.json()["notify_method"] == "email"


# ---- DELETE /subscriptions/{id} ----------------------------------------------------------

def test_delete_removes_the_row(client):
    assert client.delete("/subscriptions/1", headers=OWNER_A).status_code == 204
    assert [r.id for r in rows_in_db()] == [2, 3, 4]


def test_deleting_means_do_not_notify(client):
    client.delete("/subscriptions/1", headers=OWNER_A)
    with postgres.session_scope() as s:
        assert SubscriptionRepo(s).is_enabled(10, "emergency") == (False, None)


def test_cannot_delete_another_owners_row(client):
    assert client.delete("/subscriptions/4", headers=OWNER_A).status_code == 404
    assert len(rows_in_db()) == 4


def test_deleting_an_unknown_row_is_404(client):
    assert client.delete("/subscriptions/999", headers=OWNER_A).status_code == 404
