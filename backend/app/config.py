"""Application settings, read from environment variables and backend/.env.

Usage:
    from app.config import get_settings
    settings = get_settings()
    settings.DATABASE_URL
"""
import base64
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ — relative paths in .env (e.g. MODEL_DIR=../model/artifacts) resolve from here,
# so the app behaves the same whatever directory uvicorn or pytest is started from.
BACKEND_DIR = Path(__file__).resolve().parent.parent

APP_VERSION = "0.1.0"

# The five sound categories (one CNN output class each).
SOUND_CATEGORIES = ("emergency", "in_vehicle", "human", "traffic", "environmental")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    APP_NAME: str = "Smart Car Platform API"
    APP_VERSION: str = APP_VERSION
    APP_ENV: Literal["development", "test", "production"] = "development"

    # Stores
    DATABASE_URL: str = "postgresql+psycopg2://user:pass@localhost:5432/autosound"
    MONGO_URI: str = "mongodb://localhost:27017/autosound"
    S3_BUCKET: str = "autosound-audio-dev"
    AWS_REGION: str = "us-west-2"
    # Optional S3-compatible service; unset uses AWS. Public endpoint is used only for browser links.
    S3_ENDPOINT_URL: str | None = None
    S3_PUBLIC_ENDPOINT_URL: str | None = None

    # ML
    MODEL_DIR: Path = Path("../model/artifacts")
    CONFIDENCE_THRESHOLD: float = 0.75   # default only; the active model's threshold wins

    # Auth (C1). Change JWT_SECRET in backend/.env before deploying; tokens signed with the
    # default are only safe on a developer machine.
    JWT_SECRET: str = Field(default="dev-only-secret-change-me-before-deploying", repr=False)
    JWT_ALGORITHM: str = "HS256"
    JWT_TTL_MINUTES: int = 60

    # Web
    CORS_ORIGINS: str = "http://localhost:5173"   # comma-separated

    # Dev switches: fake external side effects instead of calling AWS
    DEV_FAKE_EMAIL: bool = True    # log emails instead of sending through SES
    DEV_FAKE_BACKUP: bool = True   # simulate backups instead of running pg_dump/mongodump
    SES_FROM_EMAIL: str = "alerts@autosound.dev"   # verified SES sender for alert emails
    # Dev only: where DEV_FAKE_BACKUP writes backup files when S3 is not configured on this machine.
    LOCAL_BACKUP_DIR: Path = Path(".local_backups")
    # Dev only: when set, GET /alerts/{id}/audio links to LOCAL_AUDIO_DIR/<s3_object_key>, served by this
    # API under /local-audio/, instead of a presigned S3 URL. Fill it with scripts/make_local_audio.py.
    # Leave unset on EC2.
    LOCAL_AUDIO_DIR: Path | None = None

    # Timeouts (seconds) so /health fails fast when a store is down
    DB_CONNECT_TIMEOUT_S: int = 3
    MONGO_TIMEOUT_MS: int = 2000

    # Create MongoDB indexes when the app starts (idempotent). Tests turn this off.
    MONGO_ENSURE_INDEXES_ON_STARTUP: bool = True

    # Background jobs run inside the API process, so jobs still queued/running at startup can never
    # finish: mark them failed. Turn off if you run several API workers or processes.
    FAIL_INTERRUPTED_JOBS_ON_STARTUP: bool = True

    # Load the active model version's artifacts when the app starts (a failure is logged, not fatal).
    LOAD_MODEL_ON_STARTUP: bool = True

    # Keep this Fernet key stable across restarts; encrypted Wi-Fi settings depend on it.
    WIFI_ENCRYPTION_KEY: str = Field(default="", repr=False)
    DEVICE_SWEEPER_ENABLED: bool = True
    PROCESSING_WORKER_ENABLED: bool = True
    PROCESSING_WORKER_POLL_SECONDS: float = Field(default=2.0, gt=0)
    @field_validator("LOCAL_AUDIO_DIR", "S3_ENDPOINT_URL", "S3_PUBLIC_ENDPOINT_URL", mode="before")
    @classmethod
    def _blank_audio_dir_is_off(cls, v):
        # LOCAL_AUDIO_DIR= (empty) means "off", the same as leaving it unset.
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("MODEL_DIR", "LOCAL_BACKUP_DIR", "LOCAL_AUDIO_DIR")
    @classmethod
    def _resolve_model_dir(cls, v: Path | None) -> Path | None:
        if v is None:
            return None
        return v if v.is_absolute() else (BACKEND_DIR / v).resolve()

    @field_validator("CONFIDENCE_THRESHOLD")
    @classmethod
    def _check_threshold(cls, v: float) -> float:
        # BR-CF-2: a confidence threshold must be between 0.50 and 0.95.
        if not 0.50 <= v <= 0.95:
            raise ValueError("CONFIDENCE_THRESHOLD must be between 0.50 and 0.95")
        return v

    @model_validator(mode="after")
    def _production_secrets_and_origins(self):
        """Fail closed for production; local development retains its documented defaults."""
        if self.APP_ENV != "production":
            return self
        secret = self.JWT_SECRET.strip()
        weak = ("dev-only", "change-me", "changeme", "your-secret", "default", "replace-me")
        if len(secret.encode()) < 32 or len(set(secret)) < 8 or any(word in secret.lower() for word in weak):
            raise ValueError("Production requires a unique JWT_SECRET of at least 32 bytes")
        try:
            Fernet(self.WIFI_ENCRYPTION_KEY.encode("ascii"))
            raw_key = base64.b64decode(self.WIFI_ENCRYPTION_KEY, altchars=b"-_", validate=True)
            if len(set(raw_key)) < 8:
                raise ValueError
        except (ValueError, TypeError, UnicodeError):
            raise ValueError("Production requires a valid, nontrivial WIFI_ENCRYPTION_KEY") from None
        origins = self.cors_origin_list
        if not origins:
            raise ValueError("Production requires explicit CORS_ORIGINS")
        for origin in origins:
            try:
                parsed = urlsplit(origin)
                parsed.port  # Reject malformed ports before accepting an origin.
            except ValueError:
                raise ValueError("Production CORS_ORIGINS must be HTTPS origins or HTTP loopback origins") from None
            loopback = parsed.hostname in ("localhost", "127.0.0.1", "::1")
            if (not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query
                    or parsed.fragment or "*" in origin or "<" in origin or ">" in origin
                    or parsed.scheme not in ("http", "https") or (parsed.scheme == "http" and not loopback)):
                raise ValueError("Production CORS_ORIGINS must be HTTPS origins or HTTP loopback origins")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
