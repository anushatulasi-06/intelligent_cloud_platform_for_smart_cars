"""Amazon S3: thin boto3 wrapper for raw audio, simulator frames, backups and archives.

Credentials come from the normal AWS chain (env vars, ~/.aws, or the EC2 instance role).

Usage:
    from app.db import s3
    s3.put_object("audio/14/2026/10/14/<record_id>.wav", data, content_type="audio/wav")
    url = s3.presigned_url(key)          # 5-minute download link
"""
import time
from datetime import datetime

import boto3
from botocore.client import BaseClient
from botocore.config import Config

from app.config import get_settings

_client: BaseClient | None = None


def make_client(**kwargs) -> BaseClient:
    """Create an S3 client. SigV4 signing so presigned URLs carry X-Amz-Expires (SigV2 is deprecated)."""
    endpoint = kwargs.pop("endpoint_url", get_settings().S3_ENDPOINT_URL)
    return boto3.client(
        "s3",
        region_name=get_settings().AWS_REGION,
        endpoint_url=endpoint,
        config=Config(signature_version="s3v4", connect_timeout=3, read_timeout=10,
                      retries={"max_attempts": 2},
                      s3={"addressing_style": "path" if endpoint else "auto"}),
        **kwargs,
    )


def get_client() -> BaseClient:
    global _client
    if _client is None:
        _client = make_client()
    return _client


def configure(client: BaseClient) -> None:
    """Point the module at another client (used by tests)."""
    global _client
    _client = client


def _bucket() -> str:
    return get_settings().S3_BUCKET


def put_object(key: str, body: bytes, content_type: str | None = None) -> str:
    """Upload bytes and return the key."""
    extra = {"ContentType": content_type} if content_type else {}
    get_client().put_object(Bucket=_bucket(), Key=key, Body=body, **extra)
    return key


def get_object(key: str) -> bytes:
    """Download an object's bytes."""
    resp = get_client().get_object(Bucket=_bucket(), Key=key)
    return resp["Body"].read()


def presigned_url(key: str, expires: int = 300) -> str:
    """Short-lived GET link (default 5 minutes, as in GET /alerts/{id}/audio)."""
    public = get_settings().S3_PUBLIC_ENDPOINT_URL
    # Sign for the browser-visible host. Rewriting the hostname after signing breaks SigV4.
    client = make_client(endpoint_url=public) if public else get_client()
    try:
        return client.generate_presigned_url(
            "get_object", Params={"Bucket": _bucket(), "Key": key}, ExpiresIn=expires
        )
    finally:
        if public:
            client.close()


def list_prefix(prefix: str) -> list[str]:
    """All keys under a prefix (follows pagination)."""
    keys: list[str] = []
    paginator = get_client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=_bucket(), Prefix=prefix):
        keys.extend(obj["Key"] for obj in page.get("Contents", []))
    return keys


def exists(key: str) -> bool:
    """True if the object exists (head_object), False on 404."""
    from botocore.exceptions import ClientError
    try:
        get_client().head_object(Bucket=_bucket(), Key=key)
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def object_last_modified(key: str) -> datetime | None:
    """Actual object update time, or None when the key is absent.

    Simulator frame freshness must come from S3, because a device heartbeat can
    continue while its camera has stopped uploading.
    """
    from botocore.exceptions import ClientError
    try:
        return get_client().head_object(Bucket=_bucket(), Key=key).get("LastModified")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return None
        raise


def prefix_stats(prefix: str) -> dict:
    """{count, size_bytes, oldest} for every object under a prefix."""
    count, size, oldest = 0, 0, None
    for page in get_client().get_paginator("list_objects_v2").paginate(Bucket=_bucket(), Prefix=prefix):
        for obj in page.get("Contents", []):
            count += 1
            size += obj.get("Size", 0)
            modified = obj.get("LastModified")
            if modified and (oldest is None or modified < oldest):
                oldest = modified
    return {"count": count, "size_bytes": size, "oldest": oldest}


def upload_file(path: str, key: str, content_type: str | None = None) -> str:
    """Upload a local file (multipart for large files) and return the key."""
    extra = {"ContentType": content_type} if content_type else None
    get_client().upload_file(path, _bucket(), key, ExtraArgs=extra)
    return key


def ping() -> float:
    """head_bucket and return the round trip in milliseconds. Raises if the bucket is unreachable."""
    start = time.perf_counter()
    get_client().head_bucket(Bucket=_bucket())
    return round((time.perf_counter() - start) * 1000, 2)
