"""Alerts (C4, F12/F7). Role and scope rules:

- Owners only ever see alerts of their own cars. Their car ids (CarRepo.cars_of_owner) go INTO the
  Mongo query (AlertRepo), so other owners' alerts are never read. Another owner's alert answers
  404, exactly like an unknown id (BR-4.5).
- Cloud staff see all alerts. iot_staff is rejected by the router (403).
- Status moves forward one step: new -> acknowledged -> resolved (DT-C4-3). Anything else is 409
  (no reopen, no skipping). Owners may only acknowledge (resolving is 403); cloud staff may do both.
"""
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from botocore.exceptions import NoCredentialsError, PartialCredentialsError

from app.auth_stub import CurrentUser
from app.config import get_settings
from app.db import postgres, s3
from app.db.repositories import AlertRepo, CarRepo
from app.services.errors import Conflict, Forbidden, NotFound, Unavailable

NEXT_STATUS = {"new": "acknowledged", "acknowledged": "resolved"}   # the only allowed transitions
AUDIO_URL_SECONDS = 300
LOCAL_AUDIO_PREFIX = "/local-audio/"


def local_audio_path(key: str) -> Path | None:
    """Dev only: the file for an S3 key inside LOCAL_AUDIO_DIR, or None when local audio is off,
    the file is missing, or the key would leave the folder."""
    root = get_settings().LOCAL_AUDIO_DIR
    if root is None:
        return None
    root = root.resolve()
    path = (root / key).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return None
    return path


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AlertService:
    def __init__(self, user: CurrentUser, repo: AlertRepo | None = None):
        self.user = user
        self.repo = repo or AlertRepo()
        self._car_ids: list[int] | None = None
        self._scoped = False

    @property
    def car_ids(self) -> list[int] | None:
        """None = every car (cloud staff); a list = only these cars (owner)."""
        if not self._scoped:
            if self.user.role == "owner":
                with postgres.session_scope() as session:
                    self._car_ids = CarRepo(session).cars_of_owner(self.user.id)
            self._scoped = True
        return self._car_ids

    def list_alerts(self, *, page: int, page_size: int, **filters) -> dict[str, Any]:
        return self.repo.search(car_ids=self.car_ids, page=page, page_size=page_size, **filters)

    def summary(self, **filters) -> dict[str, Any]:
        return self.repo.summary(car_ids=self.car_ids, **filters)

    def get(self, alert_id: str) -> dict:
        alert = self.repo.get_scoped(alert_id, self.car_ids)
        if alert is None:
            raise NotFound("Alert not found")          # same answer for unknown and not-yours
        return alert

    def update_status(self, alert_id: str, new_status: str, note: str | None = None) -> dict:
        alert = self.get(alert_id)                     # 404 first: never reveal other owners' alerts
        if self.user.role == "owner" and new_status == "resolved":
            raise Forbidden("Owners can acknowledge alerts but not resolve them")
        current = alert["status"]
        if NEXT_STATUS.get(current) != new_status:
            raise Conflict(f"Cannot change an alert from {current} to {new_status}")
        now = _now()
        if new_status == "acknowledged":
            fields = {"status": "acknowledged", "acknowledged_by": self.user.id, "acknowledged_at": now}
        else:
            fields = {"status": "resolved", "resolved_by": self.user.id, "resolved_at": now,
                      "resolution_note": (note or None)}
        updated = self.repo.transition(alert_id, self.car_ids, current, fields)
        if updated is None:                            # changed by someone else since we read it
            latest = self.get(alert_id)
            raise Conflict(f"Alert is already {latest['status']}")
        return updated

    def audio(self, alert_id: str, base_url: str = "") -> dict:
        alert = self.get(alert_id)
        key = alert.get("s3_object_key")
        if not key:
            raise NotFound("This alert has no audio clip")
        if get_settings().LOCAL_AUDIO_DIR is not None:          # dev: serve the clip from a local folder
            if local_audio_path(key) is None:
                raise NotFound("Audio clip not found in LOCAL_AUDIO_DIR (run scripts/make_local_audio.py)")
            url = base_url.rstrip("/") + LOCAL_AUDIO_PREFIX + quote(key)
            return {"url": url, "expires_in": AUDIO_URL_SECONDS}
        try:
            url = s3.presigned_url(key, expires=AUDIO_URL_SECONDS)
        except (NoCredentialsError, PartialCredentialsError):
            raise Unavailable("Audio links need AWS credentials; none are configured on this server") from None
        return {"url": url, "expires_in": AUDIO_URL_SECONDS}
