#!/usr/bin/env python3
"""Read-only, dependency-free validation before either AWS installer changes anything.

Never source a settings file as shell code; never print values or credentials.
The application also validates APP_ENV=production at runtime.
"""
import argparse
import base64
import re
from pathlib import Path
from urllib.parse import urlsplit


def validate(path: Path) -> list[str]:
    errors: list[str] = []
    values: dict[str, str] = {}
    try:
        lines = path.read_text().splitlines()
    except (OSError, UnicodeError):
        return ["Settings file cannot be read."]
    for number, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            errors.append(f"Invalid setting syntax on line {number}.")
            continue
        if key in values:
            errors.append(f"Duplicate setting: {key}.")
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                errors.append(f"Unterminated quoted setting: {key}.")
                continue
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        values[key] = value
        if "<" in value or ">" in value:
            errors.append(f"Unfilled placeholder: {key}.")

    required = ("DATABASE_URL", "MONGO_URI", "S3_BUCKET", "AWS_REGION", "JWT_SECRET",
                "WIFI_ENCRYPTION_KEY", "CORS_ORIGINS", "SES_FROM_EMAIL")
    for key in required:
        if not values.get(key):
            errors.append(f"Required setting is blank or missing: {key}.")
    if values.get("APP_ENV") != "production":
        errors.append("APP_ENV must be production.")
    jwt = values.get("JWT_SECRET", "")
    if (len(jwt.encode()) < 32 or len(set(jwt)) < 8 or
            any(word in jwt.lower() for word in ("default", "dev-only", "change-me", "changeme", "replace-me", "your-secret"))):
        errors.append("JWT_SECRET must be a newly generated random secret of at least 32 bytes.")
    key = values.get("WIFI_ENCRYPTION_KEY", "")
    try:
        decoded = base64.b64decode(key.encode("ascii"), altchars=b"-_", validate=True)
        valid_key = len(decoded) == 32 and len(set(decoded)) >= 8 and len(key) == 44
    except (ValueError, UnicodeError):
        valid_key = False
    if not valid_key:
        errors.append("WIFI_ENCRYPTION_KEY must be a newly generated Fernet key (32 random bytes, URL-safe base64).")
    origins = values.get("CORS_ORIGINS", "").split(",")
    for origin in origins:
        origin = origin.strip()
        try:
            parsed = urlsplit(origin)
            allowed = (parsed.scheme == "https" or
                       (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}))
            valid_origin = (allowed and bool(parsed.hostname) and parsed.username is None and
                            parsed.password is None and parsed.path == "" and
                            not parsed.query and not parsed.fragment and "*" not in origin)
            _ = parsed.port
        except ValueError:
            valid_origin = False
        if not valid_origin:
            errors.append("CORS_ORIGINS must contain explicit HTTPS origins or HTTP loopback origins for SSH.")
            break
    for key in ("DEV_FAKE_EMAIL", "DEV_FAKE_BACKUP"):
        if values.get(key, "").lower() != "false":
            errors.append(f"{key} must explicitly be false for this AWS production configuration.")
    if values.get("S3_ENDPOINT_URL") or values.get("S3_PUBLIC_ENDPOINT_URL"):
        errors.append("AWS deployment requires blank S3 endpoint overrides; use the separate local demo for MinIO.")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edge-settings", action="store_true", help="Check a private edge settings file instead")
    parser.add_argument("env_file", type=Path)
    args = parser.parse_args()
    errors = validate_edge(args.env_file) if args.edge_settings else validate(args.env_file)
    if errors:
        for error in errors:
            print(f"Configuration blocked: {error}")
        return 1
    print("PASS production settings syntax and secret checks (no network or deployment).")
    return 0


def validate_edge(path: Path) -> list[str]:
    try:
        values = dict(line.strip().split("=", 1) for line in path.read_text().splitlines()
                      if line.strip() and not line.lstrip().startswith("#"))
    except (OSError, UnicodeError, ValueError):
        return ["Private edge settings cannot be parsed."]
    errors = []
    for name in ("EDGE_CAR_ID", "EDGE_DEVICE_ID", "EDGE_CAMERA_DEVICE_ID"):
        if not values.get(name, "").isdigit() or int(values[name]) < 1:
            errors.append(f"{name} must be a positive private device/car ID.")
    for name in ("EDGE_DEVICE_KEY", "EDGE_CAMERA_DEVICE_KEY"):
        key = values.get(name, "").strip().strip("\"'")
        if len(key) < 24 or "<" in key or "dev-device-key" in key:
            errors.append(f"{name} must be a private registered device key, not the public development key.")
    if "--demo-only" not in values.get("DEMO_DATA_ARGS", "").split():
        errors.append("DEMO_DATA_ARGS must include --demo-only.")
    return errors


if __name__ == "__main__":
    raise SystemExit(main())
