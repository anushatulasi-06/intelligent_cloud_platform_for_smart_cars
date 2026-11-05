import io
from urllib.parse import parse_qs, urlparse

import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber

from app.db import s3


@pytest.fixture
def stub():
    client = s3.make_client(aws_access_key_id="testing", aws_secret_access_key="testing")
    s3.configure(client)
    with Stubber(client) as stubber:
        yield stubber
        stubber.assert_no_pending_responses()
    s3.configure(None)


def test_put_object(stub):
    stub.add_response("put_object", {}, {"Bucket": "test-bucket", "Key": "audio/1/a.wav",
                                         "Body": b"RIFF", "ContentType": "audio/wav"})
    assert s3.put_object("audio/1/a.wav", b"RIFF", content_type="audio/wav") == "audio/1/a.wav"


def test_get_object(stub):
    body = StreamingBody(io.BytesIO(b"data"), 4)
    stub.add_response("get_object", {"Body": body}, {"Bucket": "test-bucket", "Key": "k"})
    assert s3.get_object("k") == b"data"


def test_list_prefix_follows_pages(stub):
    stub.add_response("list_objects_v2",
                      {"Contents": [{"Key": "audio/1"}, {"Key": "audio/2"}], "IsTruncated": True,
                       "NextContinuationToken": "t"},
                      {"Bucket": "test-bucket", "Prefix": "audio/"})
    stub.add_response("list_objects_v2", {"Contents": [{"Key": "audio/3"}], "IsTruncated": False},
                      {"Bucket": "test-bucket", "Prefix": "audio/", "ContinuationToken": "t"})
    assert s3.list_prefix("audio/") == ["audio/1", "audio/2", "audio/3"]


def test_list_prefix_empty(stub):
    stub.add_response("list_objects_v2", {"IsTruncated": False}, {"Bucket": "test-bucket", "Prefix": "none/"})
    assert s3.list_prefix("none/") == []


def test_ping_head_bucket(stub):
    stub.add_response("head_bucket", {}, {"Bucket": "test-bucket"})
    assert s3.ping() >= 0


def test_ping_raises_when_bucket_missing(stub):
    stub.add_client_error("head_bucket", service_error_code="404", http_status_code=404)
    with pytest.raises(Exception):
        s3.ping()


def test_presigned_url_default_300s(stub):
    url = s3.presigned_url("audio/1/a.wav")
    parsed = urlparse(url)
    assert "test-bucket" in parsed.netloc + parsed.path and parsed.path.endswith("audio/1/a.wav")
    query = parse_qs(parsed.query)
    assert query["X-Amz-Expires"] == ["300"]
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]


def test_presigned_url_custom_expiry(stub):
    url = s3.presigned_url("k", expires=60)
    assert parse_qs(urlparse(url).query)["X-Amz-Expires"] == ["60"]
