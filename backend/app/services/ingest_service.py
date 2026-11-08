"""Validate and store edge audio before adding it to the processing queue."""
from datetime import datetime, timezone
from io import BytesIO
from uuid import uuid4

import numpy as np
import soundfile as sf
from botocore.exceptions import BotoCoreError, ClientError, ConnectTimeoutError, ReadTimeoutError
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.db import s3
from app.db.repositories.record_repo import RecordRepo
from app.services.errors import Conflict, Forbidden, NotFound, ServiceError, Unavailable, Unprocessable

MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_AUDIO_SECONDS = 15
CONTENT_TYPES = {"audio/wav", "audio/x-wav", "audio/wave", "audio/mpeg", "audio/mp3"}


class TooLarge(ServiceError):
    status_code = 413


class UnsupportedAudio(ServiceError):
    status_code = 415


def validate_audio(data: bytes, content_type: str | None) -> tuple[str, str, float]:
    if content_type not in CONTENT_TYPES:
        raise UnsupportedAudio("Only WAV and MP3 audio are accepted")
    if len(data) > MAX_AUDIO_BYTES:
        raise TooLarge("Audio must be at most 10 MB")
    try:
        with sf.SoundFile(BytesIO(data)) as audio:
            if audio.format in ("WAV", "WAVEX"):
                ext, canonical = "wav", "audio/wav"
            elif audio.format in ("MP3", "MPEG") and audio.subtype == "MPEG_LAYER_III":
                ext, canonical = "mp3", "audio/mpeg"
            else:
                raise UnsupportedAudio("Only WAV and MP3 audio are accepted")
            if (ext == "mp3") != (content_type in ("audio/mpeg", "audio/mp3")):
                raise UnsupportedAudio("Audio content does not match its content type")
            duration = audio.frames / audio.samplerate
            if not 0 < duration <= MAX_AUDIO_SECONDS:
                raise Unprocessable("Audio must be non-empty and at most 15 seconds")
            frames = 0
            for block in audio.blocks(blocksize=8192, dtype="float32", always_2d=True):
                if not np.isfinite(block).all():
                    raise Unprocessable("Audio contains invalid samples")
                frames += len(block)
            if frames == 0 or frames != audio.frames:
                raise Unprocessable("Audio is empty or incomplete")
    except (sf.LibsndfileError, RuntimeError, ValueError, ZeroDivisionError):
        raise Unprocessable("Invalid audio file") from None
    return ext, canonical, duration


class IngestService:
    def __init__(self, repo: RecordRepo | None = None):
        self.repo = repo or RecordRepo()

    @staticmethod
    def receipt(record: dict) -> dict:
        return {"record_id": record["record_id"], "status": record["status"]}

    def existing_clip(self, clip_id: str | None, device: dict, car_id: int) -> dict | None:
        if device["car_id"] != car_id:
            raise Forbidden("Device is not installed on this car")
        if not clip_id:
            return None
        existing = self.repo.get_by_clip_id(clip_id)
        if existing and (existing["device_id"] != device["id"] or existing["car_id"] != car_id):
            raise Conflict("Clip id is already in use")
        return existing

    def upload(self, data: bytes, content_type: str | None, *, device: dict, car_id: int,
               clip_id: str | None = None, captured_at: datetime | None = None,
               trigger: str | None = None, lat: float | None = None, lon: float | None = None) -> tuple[dict, bool]:
        if existing := self.existing_clip(clip_id, device, car_id):
            return self.receipt(existing), False
        ext, canonical, duration = validate_audio(data, content_type)
        now = datetime.now(timezone.utc)
        record_id = str(uuid4())
        key = f"audio/{car_id}/{now:%Y/%m/%d}/{record_id}.{ext}"
        try:
            for attempt in range(2):
                try:
                    s3.put_object(key, data, content_type=canonical)
                    break
                except (TimeoutError, ConnectTimeoutError, ReadTimeoutError):
                    if attempt:
                        raise
        except (BotoCoreError, ClientError, OSError):
            raise Unavailable("Audio storage is unavailable; keep the clip and retry") from None
        metadata = {"record_id": record_id, "clip_id": clip_id, "car_id": car_id,
                    "device_id": device["id"], "s3_object_key": key, "content_type": canonical,
                    "size_bytes": len(data), "duration_s": duration, "captured_at": captured_at or now,
                    "received_timestamp": now, "trigger": trigger, "lat": lat, "lon": lon}
        try:
            record = self.repo.create_pending(metadata)
        except DuplicateKeyError:
            if existing := self.existing_clip(clip_id, device, car_id):
                return self.receipt(existing), False
            raise Unavailable("Could not register the clip; retry with the same clip id") from None
        except PyMongoError:
            raise Unavailable("Could not register the clip; retry with the same clip id") from None
        return self.receipt(record), True

    def get(self, record_id: str) -> dict:
        record = self.repo.get_by_record_id(record_id)
        if record is None:
            raise NotFound("Record not found")
        return record

    def list_records(self, **filters) -> dict:
        return self.repo.search(**filters)

    def stats(self, window_hours: int) -> dict:
        return self.repo.stats(window_hours=window_hours)

    def retry(self, record_id: str) -> dict:
        self.get(record_id)
        record = self.repo.retry_failed(record_id)
        if record is None:
            raise Conflict("Only failed records can be retried")
        return self.receipt(record)

    def audio(self, record_id: str) -> dict:
        record = self.get(record_id)
        try:
            url = s3.presigned_url(record["s3_object_key"], expires=300)
        except (BotoCoreError, ClientError):
            raise Unavailable("Audio storage is unavailable") from None
        return {"url": url, "expires_in": 300}
