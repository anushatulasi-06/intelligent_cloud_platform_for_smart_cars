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
    assert keys == {"DATABASE_URL", "MONGO_URI", "S3_BUCKET", "AWS_REGION", "MODEL_DIR",
                    "CONFIDENCE_THRESHOLD", "CORS_ORIGINS", "DEV_FAKE_EMAIL", "DEV_FAKE_BACKUP",
                    "LOCAL_AUDIO_DIR", "WIFI_ENCRYPTION_KEY", "DEVICE_SWEEPER_ENABLED",
                    "PROCESSING_WORKER_ENABLED", "PROCESSING_WORKER_POLL_SECONDS",
                    "JWT_SECRET", "JWT_TTL_MINUTES", "S3_ENDPOINT_URL", "S3_PUBLIC_ENDPOINT_URL"}
    for k in keys:
        assert hasattr(Settings, k) or k in Settings.model_fields


def test_five_categories():
    assert SOUND_CATEGORIES == ("emergency", "in_vehicle", "human", "traffic", "environmental")
