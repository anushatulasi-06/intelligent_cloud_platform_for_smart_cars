"""Dev only: serves alert clips from LOCAL_AUDIO_DIR so audio plays without S3.

GET /local-audio/{key} returns LOCAL_AUDIO_DIR/<key>. It needs no headers, because an <audio> tag
cannot send an Authorization header (a presigned S3 URL needs none either). Answers 404 for everything
when LOCAL_AUDIO_DIR is unset, which is how it stays off on EC2.
"""
from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.services.alert_service import local_audio_path
from app.services.errors import NotFound

router = APIRouter(prefix="/local-audio", tags=["dev"], include_in_schema=False)


@router.get("/{key:path}")
def local_audio(key: str):
    path = local_audio_path(key)
    if path is None:
        raise NotFound("Audio clip not found")
    return FileResponse(path, media_type="audio/wav" if path.suffix.lower() == ".wav" else None)
