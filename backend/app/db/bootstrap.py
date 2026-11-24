"""Initialize an EMPTY database with private demonstration credentials.

Unlike app.db.seed this creates no historical alerts or public device keys. It
never rotates existing credentials. Run explicitly after migrations, before
starting the API. The credential bundle is local, mode 0600, and printed never.
This command writes PostgreSQL; it is not an offline configuration check.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets

from sqlalchemy import func, select

from app.config import BACKEND_DIR, get_settings
from app.db import postgres, seed
from app.db.models import AlertTypeConfig, Car, Device, ModelVersion, User

TABLES = (User, Car, Device, AlertTypeConfig, ModelVersion)


def ensure_empty(session):
    if any(session.scalar(select(func.count()).select_from(table)) for table in TABLES):
        raise ValueError("Refusing initialization: application tables are not empty; existing data was preserved")


def provision(session):
    """Create private SQL demo data in the caller's transaction; no Mongo/S3/SES calls."""
    ensure_empty(session)
    bundle = {"users": [], "devices": []}
    users = {}
    for source in seed.USERS:
        password = secrets.token_urlsafe(32)
        user = User(name=source["name"], email=source["email"], role=source["role"],
                    password_hash=seed.hash_password(password))
        session.add(user)
        session.flush()
        users[source["email"]] = user
        bundle["users"].append({"id": user.id, "email": user.email, "role": user.role, "password": password})
    cars = {}
    for index, source in enumerate(seed.CARS, start=1):
        car = Car(owner_id=users[source["owner"]].id,
                  **{k: v for k, v in source.items() if k != "owner"})
        session.add(car)
        session.flush()
        cars[index] = car
    for source in seed.DEVICES:
        key = secrets.token_urlsafe(32)
        device = Device(car_id=cars[source["car"]].id, name=source["name"],
                        device_type=source["device_type"], status="registered", firmware=source["firmware"],
                        device_key_hash=hashlib.sha256(key.encode()).hexdigest())
        session.add(device)
        session.flush()
        bundle["devices"].append({"id": device.id, "car_id": device.car_id, "name": device.name, "device_key": key})
    session.add_all(AlertTypeConfig(enabled=True, **source) for source in seed.ALERT_TYPES)
    for source in seed.MODEL_VERSIONS:
        session.add(ModelVersion(category="audio_classifier", version_label=source["version_label"],
                                 trained_date=source["trained_date"], accuracy=source["accuracy"],
                                 confidence_threshold=source["confidence_threshold"],
                                 artifact_path=seed.artifact_path(source["version_label"]),
                                 is_active=source["is_active"]))
    session.flush()
    return bundle


def initialize(target: Path):
    """Prepare private credentials before committing; preserve them if commit is uncertain.

    A connection failure can occur after PostgreSQL committed but before its acknowledgement
    reached this process. Once the bundle is durable, deleting it on an exception could lose
    the only passwords for an already populated database. Keep it for operator review instead.
    """
    if target.exists() or target.is_symlink() or target.parent.is_symlink():
        raise ValueError("Refusing to replace an existing credential bundle or use a symbolic link")
    for model in seed.MODEL_VERSIONS:
        directory = get_settings().MODEL_DIR / model["version_label"]
        if not all((directory / name).is_file() for name in ("model_weights.pt", "config.json", "labels.json")):
            raise ValueError("Trained model artifacts are missing; no database changes were made")
    created = False
    prepared = False
    try:
        with postgres.session_scope() as session:
            bundle = provision(session)
            target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
            with os.fdopen(descriptor, "w") as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(bundle, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            # Persist the new directory entry as well as the file's contents before COMMIT.
            directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            prepared = True
    except BaseException:
        if created and not prepared:
            target.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--initialize-empty-database", action="store_true",
                        help="Explicitly authorize writes to the configured empty PostgreSQL database")
    parser.add_argument("--credentials-file", type=Path,
                        default=BACKEND_DIR / ".local_bootstrap" / "credentials.json")
    args = parser.parse_args()
    if not args.initialize_empty_database:
        parser.error("No database accessed. Explicit --initialize-empty-database is required")
    try:
        initialize(args.credentials_file)
    except ValueError as exc:
        parser.exit(1, str(exc) + "\n")
    except Exception as exc:
        parser.exit(1, f"Initialization failed ({type(exc).__name__}); credentials were not printed. "
                    "Preserve any prepared credential bundle and inspect the database before retrying.\n")
    print("Private SQL demo initialized. Credential bundle saved with mode 0600; values were not printed.")
    print("Historical alerts were not created. Configure private edge keys and SES owner email separately.")


if __name__ == "__main__":
    main()
