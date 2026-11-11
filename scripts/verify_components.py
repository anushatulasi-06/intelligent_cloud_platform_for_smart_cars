#!/usr/bin/env python3
"""Exercise Rishi Sheth's C2/C3 component against a running deployment.

Run with backend/.venv/bin/python scripts/verify_components.py --base-url http://localhost:8000
Uses the seeded local staff accounts by default. Override credentials through
VERIFY_IOT_EMAIL, VERIFY_IOT_PASSWORD, VERIFY_CLOUD_EMAIL and VERIFY_CLOUD_PASSWORD.

This is a live integration check, not a mock. It registers and deletes one temporary
microphone on --car-id (default 1), and retains its accepted audio record and model alert
as demo evidence. It never deletes existing devices or resets any database.
The generated tone checks transport/model execution; it is not an accuracy benchmark.
No device keys, passwords, JWTs or signed download URLs are printed or saved.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from io import BytesIO
import json
import math
import os
from pathlib import Path
import secrets
import struct
import sys
import time
from uuid import uuid4
import wave

import httpx


class VerificationError(RuntimeError):
    """A check failed; the message contains no response bodies or credentials."""


def ensure(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def test_clip() -> bytes:
    """One second of valid 16 kHz mono PCM audio, created without test fixtures."""
    out = BytesIO()
    with wave.open(out, "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(b"".join(struct.pack("<h", int(5000 * math.sin(2 * math.pi * 440 * i / 16000)))
                                   for i in range(16000)))
    return out.getvalue()


class ComponentVerification:
    def __init__(self, base_url: str, car_id: int, timeout: float):
        self.client = httpx.Client(base_url=base_url.rstrip("/"), timeout=20)
        self.car_id = car_id
        self.timeout = timeout
        self.iot: dict[str, str] = {}
        self.cloud: dict[str, str] = {}
        self.device_id: int | None = None
        self.device_key = ""
        self.passwords: list[str] = []
        self.evidence = {"author": "Rishi Sheth", "started_at": datetime.now(timezone.utc).isoformat(),
                         "api_url": base_url, "checks": [], "retained_record_ids": []}

    def request(self, method: str, path: str, expected: int, **kwargs) -> httpx.Response:
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.HTTPError:
            raise VerificationError(f"{method} {path}: connection failed") from None
        ensure(response.status_code == expected,
               f"{method} {path}: expected HTTP {expected}, received {response.status_code}")
        return response

    def passed(self, description: str) -> None:
        self.evidence["checks"].append(description)
        print(f"PASS {description}", flush=True)

    def public(self, document) -> None:
        serialized = json.dumps(document)
        ensure(all(password not in serialized for password in self.passwords), "A response exposed a Wi-Fi password")
        ensure(not self.device_key or self.device_key not in serialized, "A public response exposed the device key")
        def walk(value):
            if isinstance(value, dict):
                ensure(not {"password", "password_enc", "wifi_password_enc", "device_key_hash", "device_key"} & value.keys(),
                       "A public response exposed private credential fields")
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
        walk(document)

    def login(self, role: str, default_email: str, default_password: str) -> dict[str, str]:
        prefix = f"VERIFY_{role.upper()}"
        payload = {"email": os.getenv(f"{prefix}_EMAIL", default_email),
                   "password": os.getenv(f"{prefix}_PASSWORD", default_password)}
        body = self.request("POST", "/login", 200, json=payload).json()
        ensure(body["role"] == f"{role}_staff", f"Login did not return {role} staff")
        return {"Authorization": f"Bearer {body['access_token']}"}

    @property
    def edge(self) -> dict[str, str]:
        return {"X-Device-Key": self.device_key}

    @property
    def device_path(self) -> str:
        return f"/devices/{self.device_id}"

    def heartbeat_and_ack(self, expected_command: str) -> dict:
        received = self.request("POST", f"{self.device_path}/heartbeat", 200, headers=self.edge,
                                json={"firmware": "verify-1.0", "rssi": -45}).json()
        commands = received["commands"]
        ensure(len(commands) == 1 and commands[0]["command"] == expected_command,
               f"Heartbeat did not deliver exactly one {expected_command} command")
        command = commands[0]
        if expected_command == "sync_config":
            wifi = command["payload"].get("wifi", {})
            ensure(wifi.get("password_enc") and "password" not in wifi,
                   "Protected Wi-Fi credentials were not delivered encrypted")
            ensure(all(password not in json.dumps(received) for password in self.passwords),
                   "Heartbeat exposed a plaintext Wi-Fi password")
        self.request("POST", f"{self.device_path}/commands/{command['command_id']}/ack", 200,
                     headers=self.edge, json={"ok": True})
        return command

    def run(self) -> None:
        self.request("GET", "/health", 200)
        self.iot = self.login("iot", "iot@autosound.dev", "iot-dev-pass")
        self.cloud = self.login("cloud", "cloud@autosound.dev", "cloud-dev-pass")
        self.passed("API health and real JWT staff login")
        name = f"verify-rishi-{uuid4().hex[:10]}"
        self.passwords.append(secrets.token_urlsafe(18))
        wifi = {"ssid": "Rishi verification", "security": "wpa2", "password": self.passwords[-1], "ip_mode": "dhcp"}
        body = {"car_id": self.car_id, "name": name, "device_type": "microphone", "wifi": wifi}
        created = self.request("POST", "/devices", 201, headers=self.iot, json=body).json()
        self.device_id = created["device"]["id"]
        self.device_key = created["device_key"]
        ensure(bool(self.device_key), "Registration did not return the one-time device key")
        self.public(created["device"])
        self.evidence["temporary_device_id"] = self.device_id
        self.request("POST", "/devices", 409, headers=self.iot, json=body)
        self.request("POST", "/devices", 403, headers=self.cloud, json=body)
        self.request("GET", "/devices", 401)
        self.request("POST", f"{self.device_path}/heartbeat", 401,
                     headers={"X-Device-Key": "intentionally-invalid"}, json={})
        listed = self.request("GET", "/devices", 200, headers=self.cloud,
                              params={"q": name, "car_id": self.car_id}).json()
        ensure(any(row["id"] == self.device_id for row in listed["items"]), "Filtered device inventory omitted the new device")
        self.public(listed)
        self.passed("Temporary WPA2 device registration, one-time key, inventory and role restrictions")
        self.heartbeat_and_ack("sync_config")
        detail = self.request("GET", self.device_path, 200, headers=self.iot).json()
        ensure(detail["status"] == "online", "Device did not become online after initial configuration acknowledgement")
        self.public(detail)
        self.request("PUT", self.device_path, 200, headers=self.iot, json={"name": f"{name}-edited"})
        self.request("PUT", f"{self.device_path}/wifi", 422, headers=self.iot,
                     json={**wifi, "password": "short"})
        self.request("PUT", f"{self.device_path}/wifi", 422, headers=self.iot,
                     json={**wifi, "security": "open"})
        self.passwords.append(secrets.token_urlsafe(18))
        updated_wifi = {**wifi, "ssid": "Rishi updated network", "security": "wpa3", "password": self.passwords[-1]}
        self.request("PUT", f"{self.device_path}/wifi", 202, headers=self.iot, json=updated_wifi)
        configuring = self.request("GET", self.device_path, 200, headers=self.iot).json()
        ensure(configuring["status"] == "configuring", "Wi-Fi update did not set configuring state")
        self.public(configuring)
        self.heartbeat_and_ack("sync_config")
        self.passed("Edit, Wi-Fi validation, protected configuration delivery and acknowledgement")
        queued = self.request("POST", f"{self.device_path}/commands", 202, headers=self.iot,
                              json={"command": "start_capture"}).json()
        self.request("POST", f"{self.device_path}/commands", 409, headers=self.iot,
                     json={"command": "start_capture"})
        command = self.heartbeat_and_ack("start_capture")
        ensure(command["command_id"] == queued["command_id"], "Heartbeat delivered a different command")
        self.request("POST", f"{self.device_path}/commands/{command['command_id']}/ack", 409,
                     headers=self.edge, json={"ok": True})
        history = self.request("GET", f"{self.device_path}/commands", 200, headers=self.cloud).json()
        ensure(any(row["command_id"] == command["command_id"] and row["status"] == "acknowledged" for row in history),
               "Command history did not retain acknowledgement")
        self.public(history)
        self.request("GET", f"/simulator/{self.car_id}/state", 200, headers=self.iot)
        self.passed("Live heartbeat, capture command lifecycle, duplicate protection and simulator state")

        audio = test_clip()
        clip_id = str(uuid4())
        metadata = {"car_id": str(self.car_id), "device_id": str(self.device_id), "clip_id": clip_id,
                    "captured_at": datetime.now(timezone.utc).isoformat(), "trigger": "deliverable3_verification"}
        self.request("POST", "/device-data", 401, headers={"X-Device-Key": "invalid"}, data=metadata,
                     files={"audio_file": ("verify.wav", audio, "audio/wav")})
        self.request("POST", "/device-data", 415, headers=self.edge, data=metadata,
                     files={"audio_file": ("verify.txt", b"invalid", "text/plain")})
        self.request("POST", "/device-data", 422, headers=self.edge, data=metadata,
                     files={"audio_file": ("verify.wav", b"invalid", "audio/wav")})
        receipt = self.request("POST", "/device-data", 202, headers=self.edge, data=metadata,
                               files={"audio_file": ("verify.wav", audio, "audio/wav")}).json()
        record_id = receipt["record_id"]
        self.evidence["retained_record_ids"].append(record_id)
        repeated = self.request("POST", "/device-data", 200, headers=self.edge, data=metadata,
                                files={"audio_file": ("verify.wav", audio, "audio/wav")}).json()
        ensure(repeated["record_id"] == record_id, "Repeated clip_id created a duplicate record")
        self.passed("Audio authentication/format checks, S3 acceptance and idempotent replay")
        listing = self.request("GET", "/device-data", 200, headers=self.iot,
                               params={"car_id": self.car_id, "device_id": self.device_id, "page_size": 100}).json()
        ensure(listing["total"] == 1 and listing["items"][0]["record_id"] == record_id,
               "Staff filters did not return exactly one accepted clip")
        end = time.monotonic() + self.timeout
        observed = set()
        while True:
            record = self.request("GET", f"/device-data/{record_id}", 200, headers=self.cloud).json()
            observed.add(record["status"])
            if record["status"] == "processed":
                break
            ensure(record["status"] != "failed", "Live model processing failed after automatic retries; inspect the record in Device data")
            ensure(time.monotonic() < end, "Timed out waiting for the real processing worker/model")
            time.sleep(1)
        ensure(record.get("processed") is True and record.get("alert_id"), "Processed clip has no generated alert")
        ensure(1 <= record["attempts"] <= 3, "Processing attempts exceeded the designed retry budget")
        alert = self.request("GET", f"/alerts/{record['alert_id']}", 200, headers=self.cloud).json()
        ensure(alert.get("record_id") == record_id and alert.get("model_version"), "Generated alert is missing record/model provenance")
        self.evidence.update({"alert_id": record["alert_id"], "model_version": alert["model_version"],
                              "classification": alert["sound_type"], "confidence": alert["confidence"],
                              "observed_record_states": sorted(observed)})
        self.passed("Real background worker and deployed model produced a linked alert")
        link = self.request("GET", f"/device-data/{record_id}/audio", 200, headers=self.iot).json()
        ensure(link["expires_in"] == 300, "Audio link did not use the designed five-minute TTL")
        try:
            downloaded = httpx.get(link["url"], timeout=20, follow_redirects=True)
        except httpx.HTTPError:
            raise VerificationError("Signed audio download failed (URL omitted)") from None
        ensure(downloaded.status_code == 200 and downloaded.content == audio, "Raw audio download did not match the accepted clip")
        stats = self.request("GET", "/device-data/stats", 200, headers=self.cloud,
                             params={"window_hours": 12}).json()
        ensure(len(stats["per_hour"]) == 12 and stats["counts"]["processed"] >= 1 and stats["bytes_stored"] >= len(audio),
               "Monitor statistics did not include live intake/storage")
        self.request("POST", f"/device-data/{record_id}/retry", 403, headers=self.iot)
        self.request("POST", f"/device-data/{record_id}/retry", 409, headers=self.cloud)
        self.passed("Byte-identical signed audio playback/download, monitor statistics and retry role/state guards")

    def cleanup(self) -> None:
        if self.device_id is None:
            return
        self.request("DELETE", self.device_path, 204, headers=self.iot)
        self.request("GET", self.device_path, 404, headers=self.iot)
        self.evidence["temporary_device_deleted"] = True
        self.passed("Deleted only the temporary device; accepted audio and alert retained as evidence")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--car-id", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=180, help="Maximum seconds to wait for model processing")
    parser.add_argument("--output", type=Path, help="Optional credential-free JSON evidence file")
    args = parser.parse_args()
    if args.car_id < 1 or args.timeout <= 0:
        parser.error("--car-id and --timeout must be positive")
    verifier = ComponentVerification(args.base_url, args.car_id, args.timeout)
    error = None
    try:
        verifier.run()
    except (VerificationError, KeyError, ValueError) as exc:
        error = str(exc) if isinstance(exc, VerificationError) else "Unexpected API response shape"
    finally:
        try:
            verifier.cleanup()
        except VerificationError as exc:
            error = f"{error + '; ' if error else ''}Temporary device cleanup failed: {exc}"
        verifier.client.close()
    verifier.evidence["result"] = "FAIL" if error else "PASS"
    verifier.evidence["finished_at"] = datetime.now(timezone.utc).isoformat()
    if error:
        verifier.evidence["failure"] = error
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(verifier.evidence, indent=2) + "\n", encoding="utf-8")
    if error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1
    print("PASS C2/C3 live verification complete (retained clip is a generated test tone, not an accuracy benchmark)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
