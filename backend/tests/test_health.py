import pytest
from fastapi.testclient import TestClient

from app.config import APP_VERSION
from app.db import postgres
from app.main import create_app


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


def test_health_ok_with_working_database(client, sqlite_engine):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "version": APP_VERSION}


def test_health_503_when_postgres_unreachable(client):
    # Nothing listens on port 1, so the connection is refused immediately.
    postgres.configure(postgres.make_engine("postgresql+psycopg2://user:pass@127.0.0.1:1/autosound"))
    r = client.get("/health")
    assert r.status_code == 503
    assert r.json() == {"status": "unavailable", "version": APP_VERSION}


def test_health_503_does_not_leak_error_details(client, monkeypatch):
    def boom():
        raise RuntimeError("password authentication failed for user secret_user")
    monkeypatch.setattr(postgres, "ping", boom)
    r = client.get("/health")
    assert r.status_code == 503
    assert "secret_user" not in r.text


def test_health_is_public(client, sqlite_engine):
    assert client.get("/health").status_code == 200   # no X-Role header needed


def test_cors_allows_configured_origin(client):
    r = client.options("/health", headers={
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "X-Role, X-User-Id",
    })
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"


@pytest.mark.parametrize("method", ["PUT", "POST", "GET", "DELETE"])
def test_cors_preflight_allows_role_headers_and_writes(client, method):
    r = client.options("/alerts/abc", headers={
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": method,
        "Access-Control-Request-Headers": "x-role, x-user-id, content-type",
    })
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert method in r.headers["access-control-allow-methods"]
    allowed = r.headers["access-control-allow-headers"].lower()
    assert all(h in allowed for h in ("x-role", "x-user-id", "content-type"))


def test_cors_rejects_unlisted_header(client):
    r = client.options("/alerts", headers={
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "x-something-else",
    })
    assert r.status_code == 400


def test_cors_rejects_other_origin(client):
    r = client.options("/health", headers={
        "Origin": "https://evil.example.com",
        "Access-Control-Request-Method": "GET",
    })
    assert "access-control-allow-origin" not in r.headers
