import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth_stub import CurrentUser, get_current_user, require_role


@pytest.fixture
def client():
    app = FastAPI()
    router = APIRouter()

    @router.get("/staff-only")
    def staff_only(user: CurrentUser = Depends(require_role("cloud_staff"))):
        return {"id": user.id, "role": user.role}

    @router.get("/owner-or-staff")
    def owner_or_staff(user: CurrentUser = Depends(require_role("owner", "cloud_staff"))):
        return {"id": user.id, "role": user.role}

    # router-level protection
    admin = APIRouter(prefix="/admin", dependencies=[Depends(require_role("cloud_staff"))])

    @admin.get("/thing")
    def thing():
        return {"ok": True}

    app.include_router(router)
    app.include_router(admin)
    return TestClient(app)


def test_no_role_is_401(client):
    r = client.get("/staff-only")
    assert r.status_code == 401


@pytest.mark.parametrize("role", ["admin", "OWNER", "", " cloud_staff"])
def test_unknown_role_is_401(client, role):
    assert client.get("/staff-only", headers={"X-Role": role}).status_code == 401


def test_wrong_role_is_403(client):
    assert client.get("/staff-only", headers={"X-Role": "owner"}).status_code == 403
    assert client.get("/owner-or-staff", headers={"X-Role": "iot_staff"}).status_code == 403


def test_allowed_role_passes_with_default_user_id(client):
    r = client.get("/staff-only", headers={"X-Role": "cloud_staff"})
    assert r.status_code == 200
    assert r.json() == {"id": 1, "role": "cloud_staff"}


def test_user_id_header(client):
    r = client.get("/owner-or-staff", headers={"X-Role": "owner", "X-User-Id": "42"})
    assert r.json() == {"id": 42, "role": "owner"}


@pytest.mark.parametrize("uid", ["abc", "0", "-3", "1.5"])
def test_bad_user_id_is_401(client, uid):
    assert client.get("/owner-or-staff", headers={"X-Role": "owner", "X-User-Id": uid}).status_code == 401


def test_router_level_dependency(client):
    assert client.get("/admin/thing").status_code == 401
    assert client.get("/admin/thing", headers={"X-Role": "iot_staff"}).status_code == 403
    assert client.get("/admin/thing", headers={"X-Role": "cloud_staff"}).status_code == 200


def test_require_role_rejects_bad_config():
    with pytest.raises(ValueError):
        require_role()
    with pytest.raises(ValueError):
        require_role("admin")


def test_get_current_user_direct():
    assert get_current_user(x_role="iot_staff", x_user_id=None) == CurrentUser(id=1, role="iot_staff")
