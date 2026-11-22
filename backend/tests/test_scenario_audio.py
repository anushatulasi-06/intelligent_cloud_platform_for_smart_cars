"""Authenticated matching recordings for the audible F11 browser scenario flow."""
import hashlib
from io import BytesIO
import json
import wave

import pytest

from app.routers import simulator
from tests.auth_headers import bearer
from tests.test_devices import client, device_env  # noqa: F401 - isolated stores


@pytest.mark.parametrize("category", ["emergency", "in_vehicle", "human", "traffic", "environmental"])
def test_staff_get_exact_attributed_wav_and_other_roles_cannot(client, category):
    path = f"/simulator/scenarios/{category}/audio"
    assert client.get(path).status_code == 401
    assert client.get(path, headers=bearer("owner")).status_code == 403
    manifest = json.loads((simulator.SCENARIO_DIRECTORY / "manifest.json").read_text())
    clip = next(row for row in manifest["clips"] if row["scenario"] == category)
    for role in ("iot_staff", "cloud_staff"):
        response = client.get(path, headers=bearer(role))
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/wav"
        assert response.headers["cache-control"].startswith("private")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "creativecommons.org/licenses/by-nc/3.0/" in response.headers["link"]
        assert hashlib.sha256(response.content).hexdigest() == clip["sha256"]
        with wave.open(BytesIO(response.content)) as audio:
            assert audio.getnframes() / audio.getframerate() == 5.0


def test_audio_category_allowlist_and_missing_or_corrupt_asset(client, monkeypatch, tmp_path):
    headers = bearer("iot_staff")
    assert client.get("/simulator/scenarios/not-a-category/audio", headers=headers).status_code == 422
    assert client.get("/simulator/scenarios/%2E%2E%2Fconfig/audio", headers=headers).status_code in (404, 422)
    monkeypatch.setattr(simulator, "SCENARIO_DIRECTORY", tmp_path)
    path = "/simulator/scenarios/emergency/audio"
    response = client.get(path, headers=headers)
    assert response.status_code == 503 and str(tmp_path) not in response.text
    (tmp_path / simulator.SCENARIO_FILES["emergency"]).write_bytes(b"not a WAV")
    assert client.get(path, headers=headers).status_code == 503
