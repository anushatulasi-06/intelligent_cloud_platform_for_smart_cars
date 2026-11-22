"""Small persistent FIFO for the edge client. A failed send leaves the clip on disk."""
import json
import os
import tempfile
from pathlib import Path
from uuid import UUID, uuid4


class EdgeBuffer:
    def __init__(self, directory: str | Path, capacity: int = 200):
        if not 1 <= capacity <= 200:
            raise ValueError("capacity must be between 1 and 200")
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.capacity = capacity

    def _entries(self) -> list[Path]:
        return sorted(self.directory.glob("*.clip"), key=lambda p: p.stat().st_mtime_ns)

    def enqueue(self, audio: bytes, metadata: dict) -> str:
        """Refuse overflow so the caller can stop capture, rather than lose audio."""
        meta = dict(metadata)
        clip_id = str(UUID(str(meta.get("clip_id") or uuid4())))
        meta["clip_id"] = clip_id
        target = self.directory / f"{clip_id}.clip"
        if target.exists():
            return clip_id
        if len(self._entries()) >= self.capacity:
            raise BufferError("Edge buffer is full; pause capture until uploads resume")
        header = json.dumps(meta, separators=(",", ":")).encode() + b"\n"
        fd, name = tempfile.mkstemp(prefix=".upload-", dir=self.directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(header)
                stream.write(audio)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, target)
        finally:
            Path(name).unlink(missing_ok=True)
        return clip_id

    def flush(self, send, *, limit: int | None = None) -> int:
        """send(metadata, bytes) returns True only after HTTP 200/202. Stop on failure.

        The clip UUID persists across process restarts and ambiguous network replies,
        allowing the cloud to return the original receipt on the next attempt.
        """
        if limit is not None and (not isinstance(limit, int) or limit < 1):
            raise ValueError("limit must be a positive integer")
        sent = 0
        for path in self._entries()[:limit]:
            with path.open("rb") as stream:
                metadata = json.loads(stream.readline())
                audio = stream.read()
            try:
                accepted = send(metadata, audio)
            except (OSError, TimeoutError):
                break
            if accepted is not True:
                break
            path.unlink()
            sent += 1
        return sent

    def __len__(self) -> int:
        return len(self._entries())
