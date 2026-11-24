import base64
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import BACKEND_DIR, SOUND_CATEGORIES, Settings


def test_model_dir_resolves_relative_to_backend():
    s = Settings(MODEL_DIR="../model/artifacts")
    assert s.MODEL_DIR == (BACKEND_DIR.parent / "model" / "artifacts").resolve()
    assert s.MODEL_DIR.is_absolute()


def test_absolute_model_dir_kept():
    assert Settings(MODEL_DIR="/opt/models").MODEL_DIR == Path("/opt/models")


def test_blank_local_audio_dir_means_off():
    assert Settings(LOCAL_AUDIO_DIR="").LOCAL_AUDIO_DIR is None
    assert Settings(LOCAL_AUDIO_DIR="  ").LOCAL_AUDIO_DIR is None
    assert Settings(LOCAL_AUDIO_DIR=".local_audio").LOCAL_AUDIO_DIR.name == ".local_audio"


def test_cors_origins_split():
    s = Settings(CORS_ORIGINS="http://localhost:5173, https://app.example.com ,")
    assert s.cors_origin_list == ["http://localhost:5173", "https://app.example.com"]


@pytest.mark.parametrize("value", [0.49, 0.96])
def test_threshold_outside_050_095_rejected(value):
    with pytest.raises(ValidationError):
        Settings(CONFIDENCE_THRESHOLD=value)


def test_defaults_match_env_example():
    s = Settings(_env_file=None)
    assert s.CONFIDENCE_THRESHOLD == 0.75
    assert s.DEV_FAKE_EMAIL is True and s.DEV_FAKE_BACKUP is True


def test_env_example_has_every_documented_key():
    keys = {line.split("=", 1)[0] for line in (BACKEND_DIR / ".env.example").read_text().splitlines()
            if line and not line.startswith("#")}
    assert keys == {"APP_ENV", "DATABASE_URL", "MONGO_URI", "S3_BUCKET", "AWS_REGION", "MODEL_DIR",
                    "CONFIDENCE_THRESHOLD", "CORS_ORIGINS", "DEV_FAKE_EMAIL", "DEV_FAKE_BACKUP",
                    "LOCAL_AUDIO_DIR", "WIFI_ENCRYPTION_KEY", "DEVICE_SWEEPER_ENABLED",
                    "PROCESSING_WORKER_ENABLED", "PROCESSING_WORKER_POLL_SECONDS",
                    "JWT_SECRET", "JWT_TTL_MINUTES", "S3_ENDPOINT_URL", "S3_PUBLIC_ENDPOINT_URL"}
    for k in keys:
        assert hasattr(Settings, k) or k in Settings.model_fields


def test_five_categories():
    assert SOUND_CATEGORIES == ("emergency", "in_vehicle", "human", "traffic", "environmental")


def production(**overrides):
    values = {"APP_ENV": "production", "JWT_SECRET": "Q9s8F7w6d5J4k3L2n1X0zAbCdEfGhIjKlMnOpQrStUv",
              "WIFI_ENCRYPTION_KEY": base64.urlsafe_b64encode(bytes(range(32))).decode(),
              "CORS_ORIGINS": "https://app.example.com"}
    return Settings(_env_file=None, **(values | overrides))


@pytest.mark.parametrize("origin", ["https://app.example.com", "http://localhost:5173",
                                  "http://127.0.0.1:8080", "http://[::1]:8080"])
def test_production_accepts_explicit_https_or_loopback_origins(origin):
    assert production(CORS_ORIGINS=origin).APP_ENV == "production"


@pytest.mark.parametrize("origin", ["", "*", "https://*.example.com", "http://54.1.2.3",
    "https://app.example.com/path", "https://user:pass@app.example.com", "https://app.example.com?key=x",
    "https://app.example.com#fragment", "https://app.example.com:bad", "<frontend-url>"])
def test_production_rejects_public_plaintext_or_invalid_origins(origin):
    with pytest.raises(ValidationError, match="Production"):
        production(CORS_ORIGINS=origin)


@pytest.mark.parametrize("secret", ["", "short", "x" * 48, "dev-only-secret-change-me-before-deploying",
                                    "change-me-and-replace-this-in-production-123456"])
def test_production_refuses_weak_or_public_signing_keys(secret):
    with pytest.raises(ValidationError, match="JWT_SECRET") as error:
        production(JWT_SECRET=secret)
    if secret:
        assert secret not in str(error.value)


@pytest.mark.parametrize("key", ["", "invalid", "é" * 44,
    base64.urlsafe_b64encode(b"x" * 32).decode(), base64.urlsafe_b64encode(bytes(range(31))).decode()])
def test_production_requires_valid_nontrivial_wifi_key(key):
    with pytest.raises(ValidationError, match="WIFI_ENCRYPTION_KEY"):
        production(WIFI_ENCRYPTION_KEY=key)


def test_settings_repr_does_not_expose_secrets():
    settings = production()
    assert settings.JWT_SECRET not in repr(settings)
    assert settings.WIFI_ENCRYPTION_KEY not in repr(settings)
