"""app.auth_stub is now a shim over the real JWT auth in app.auth (C1). Routers written before
the swap still import from it, so these checks make sure it keeps forwarding the same names and
that a router built on it really is protected. The auth itself is tested in test_auth.py."""
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from app import auth, auth_stub
from tests.auth_headers import bearer


def test_shim_forwards_the_real_implementation():
    assert auth_stub.CurrentUser is auth.CurrentUser
    assert auth_stub.get_current_user is auth.get_current_user
    assert auth_stub.require_role is auth.require_role
    assert auth_stub.ROLES == auth.ROLES


def test_a_router_using_the_shim_needs_a_token():
    app = FastAPI()
    router = APIRouter(dependencies=[Depends(auth_stub.require_role("cloud_staff"))])

    @router.get("/thing")
    def thing():
        return {"ok": True}

    app.include_router(router)
    client = TestClient(app)

    assert client.get("/thing").status_code == 401
    assert client.get("/thing", headers={"X-Role": "cloud_staff"}).status_code == 401   # headers are gone
    assert client.get("/thing", headers=bearer("owner")).status_code == 403
    assert client.get("/thing", headers=bearer("cloud_staff")).status_code == 200
