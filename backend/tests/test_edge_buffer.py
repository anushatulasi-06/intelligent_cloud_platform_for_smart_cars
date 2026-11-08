from uuid import uuid4

import pytest

from app.services.edge_buffer import EdgeBuffer


def test_buffer_survives_restart_and_drains_fifo(tmp_path):
    buffer = EdgeBuffer(tmp_path)
    first = buffer.enqueue(b"one", {"device_id": 1})
    second = buffer.enqueue(b"two", {"device_id": 1})
    buffer = EdgeBuffer(tmp_path)
    assert len(buffer) == 2
    assert buffer.flush(lambda meta, data: False) == 0
    calls = []
    def send(meta, data):
        calls.append((meta["clip_id"], data))
        return True
    assert buffer.flush(send) == 2
    assert calls == [(first, b"one"), (second, b"two")]
    assert len(buffer) == 0


def test_buffer_failure_keeps_clip_and_overflow_pauses_capture(tmp_path):
    buffer = EdgeBuffer(tmp_path, capacity=1)
    clip_id = str(uuid4())
    buffer.enqueue(b"one", {"clip_id": clip_id})
    # An ambiguous enqueue retry consumes no extra slot and never replaces audio.
    assert buffer.enqueue(b"replacement", {"clip_id": clip_id}) == clip_id
    def fail(meta, data):
        raise TimeoutError("offline")
    assert buffer.flush(fail) == 0 and len(buffer) == 1
    with pytest.raises(BufferError):
        buffer.enqueue(b"two", {})
    assert buffer.flush(lambda meta, data: meta["clip_id"] == clip_id and data == b"one") == 1


@pytest.mark.parametrize("limit", [0, -1, 1.5])
def test_flush_limit_must_be_a_positive_integer(tmp_path, limit):
    with pytest.raises(ValueError, match="positive integer"):
        EdgeBuffer(tmp_path).flush(lambda meta, data: True, limit=limit)
