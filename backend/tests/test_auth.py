"""POST /login, GET /me and the role guard (C1, FR-9): token issue, generic login errors,
expiry, and 401 vs 403 on protected routes."""
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth import CurrentUser, get_current_user, owns_car, require_role
from app.config import get_settings
from app.db import postgres
from app.db.models import Car, User
from app.main import create_app
from app.services.auth_service import hash_password, issue_token
from tests.auth_headers import bearer

PASSWORD = "owner-dev-pass"
ACCOUNTS = [
    (1, "Olivia Owner", "owner@autosound.dev", "owner"),
    (2, "Oscar Owner", "owner2@autosound.dev", "owner"),
    (3, "Ian IoT", "iot@autosound.dev", "iot_staff"),
    (4, "Chloe Cloud", "cloud@autosound.dev", "cloud_staff"),
]


@pytest.fixture
def users(sql_db):
    with postgres.session_scope() as s:
        for uid, name, email, role in ACCOUNTS:
            s.add(User(id=uid, name=name, email=email, role=role,
                       password_hash=hash_password(PASSWORD)))
        s.flush()
        s.add(Car(id=10, owner_id=1, make="Toyota", model="Camry", plate="A10"))


@pytest.fixture
def client(users):
    with TestClient(create_app()) as c:
        yield c


def token_with(claims: dict) -> str:
    s = get_settings()
    return jwt.encode(claims, s.JWT_SECRET, algorithm=s.JWT_ALGORITHM)


# ---- POST /login -------------------------------------------------------------------------

def test_login_returns_a_token_and_the_user(client):
    r = client.post("/login", json={"email": "owner@autosound.dev", "password": PASSWORD})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == get_settings().JWT_TTL_MINUTES * 60
    assert body["role"] == "owner"
    assert body["user"] == {"id": 1, "name": "Olivia Owner", "email": "owner@autosound.dev"}
    claims = jwt.decode(body["access_token"], get_settings().JWT_SECRET, algorithms=["HS256"])
    assert claims["sub"] == "1" and claims["role"] == "owner"
    assert claims["exp"] - claims["iat"] == get_settings().JWT_TTL_MINUTES * 60


def test_login_is_case_insensitive_and_trims(client):
    r = client.post("/login", json={"email": "  OWNER@AutoSound.dev ", "password": PASSWORD})
    assert r.status_code == 200
    assert r.json()["user"]["email"] == "owner@autosound.dev"


@pytest.mark.parametrize("email,password", [
    ("owner@autosound.dev", "wrong-password"),
    ("nobody@autosound.dev", PASSWORD),
])
def test_wrong_credentials_use_one_message(client, email, password):
    """BR-1.2: the answer must not reveal whether the address exists."""
    r = client.post("/login", json={"email": email, "password": password})
    assert r.status_code == 401
    assert r.json() == {"detail": "Invalid email or password"}


@pytest.mark.parametrize("body", [
    {"email": "not-an-email", "password": PASSWORD},
    {"email": "owner@autosound.dev"},
    {"email": "owner@autosound.dev", "password": ""},
    {"email": "owner@autosound.dev", "password": PASSWORD, "role": "cloud_staff"},
])
def test_malformed_login_is_422(client, body):
    assert client.post("/login", json=body).status_code == 422


def test_login_does_not_need_a_token(client):
    assert client.post("/login", json={"email": "x@y.dev", "password": "p"}).status_code == 401


def test_a_hash_that_is_not_bcrypt_cannot_log_in(client):
    with postgres.session_scope() as s:
        s.add(User(id=9, name="Broken", email="broken@autosound.dev", role="owner", password_hash="h"))
    r = client.post("/login", json={"email": "broken@autosound.dev", "password": "h"})
    assert r.status_code == 401


# ---- GET /me -----------------------------------------------------------------------------

def test_me_returns_the_signed_in_user(client):
    r = client.get("/me", headers=bearer("owner", 1))
    assert r.status_code == 200
    assert r.json() == {"id": 1, "name": "Olivia Owner", "email": "owner@autosound.dev", "role": "owner"}


def test_me_without_a_token_is_401(client):
    assert client.get("/me").status_code == 401


def test_me_after_the_account_is_gone_is_401(client):
    headers = bearer("owner", 1)
    with postgres.session_scope() as s:
        s.delete(s.get(User, 2))
    assert client.get("/me", headers=headers).status_code == 200
    assert client.get("/me", headers=bearer("owner", 2)).status_code == 401


# ---- token checks ------------------------------------------------------------------------

def test_expired_token_is_401(client):
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    token = token_with({"sub": "1", "role": "owner", "iat": past - timedelta(hours=1), "exp": past})
    assert client.get("/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_token_signed_with_another_key_is_401(client):
    token = jwt.encode({"sub": "1", "role": "owner",
                        "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                       "some-other-signing-key-that-is-long-enough", algorithm="HS256")
    assert client.get("/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


@pytest.mark.parametrize("header", ["", "Bearer", "Bearer ", "Basic abc", "token abc", "Bearer not.a.jwt"])
def test_broken_authorization_header_is_401(client, header):
    assert client.get("/me", headers={"Authorization": header}).status_code == 401


@pytest.mark.parametrize("claims", [
    {"role": "owner"},                       # no subject
    {"sub": "abc", "role": "owner"},         # subject is not a number
    {"sub": "0", "role": "owner"},           # user ids start at 1
    {"sub": "1", "role": "admin"},           # not one of our roles
    {"sub": "1"},                            # no role
])
def test_token_with_bad_claims_is_401(client, claims):
    token = token_with({**claims, "exp": datetime.now(timezone.utc) + timedelta(hours=1)})
    assert client.get("/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


# ---- require_role ------------------------------------------------------------------------

@pytest.fixture
def guarded():
    app = FastAPI()
    router = APIRouter()

    @router.get("/staff-only")
    def staff_only(user: CurrentUser = Depends(require_role("cloud_staff"))):
        return {"id": user.id, "role": user.role}

    @router.get("/owner-or-staff")
    def owner_or_staff(user: CurrentUser = Depends(require_role("owner", "cloud_staff"))):
        return {"id": user.id, "role": user.role}

    admin = APIRouter(prefix="/admin", dependencies=[Depends(require_role("cloud_staff"))])

    @admin.get("/thing")
    def thing():
        return {"ok": True}

    app.include_router(router)
    app.include_router(admin)
    return TestClient(app)


def test_no_token_is_401(guarded):
    assert guarded.get("/staff-only").status_code == 401


def test_wrong_role_is_403(guarded):
    assert guarded.get("/staff-only", headers=bearer("owner")).status_code == 403
    assert guarded.get("/owner-or-staff", headers=bearer("iot_staff")).status_code == 403


def test_allowed_role_passes_with_its_user_id(guarded):
    r = guarded.get("/owner-or-staff", headers=bearer("owner", 42))
    assert r.status_code == 200
    assert r.json() == {"id": 42, "role": "owner"}


def test_router_level_dependency(guarded):
    assert guarded.get("/admin/thing").status_code == 401
    assert guarded.get("/admin/thing", headers=bearer("iot_staff")).status_code == 403
    assert guarded.get("/admin/thing", headers=bearer("cloud_staff")).status_code == 200


def test_require_role_rejects_bad_config():
    with pytest.raises(ValueError):
        require_role()
    with pytest.raises(ValueError):
        require_role("admin")


def test_get_current_user_without_credentials():
    with pytest.raises(Exception):
        get_current_user(None)


def test_owns_car(users):
    assert owns_car(CurrentUser(id=1, role="owner"), 10) is True
    assert owns_car(CurrentUser(id=2, role="owner"), 10) is False
    assert owns_car(CurrentUser(id=4, role="cloud_staff"), 10) is True   # staff are not owner-scoped


def test_issue_token_round_trip():
    token = issue_token(7, "iot_staff")
    claims = jwt.decode(token, get_settings().JWT_SECRET, algorithms=["HS256"])
    assert claims["sub"] == "7" and claims["role"] == "iot_staff"
