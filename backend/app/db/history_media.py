"""Pure local WAV/key/hash validation shared by the historical importer and review CLI.

No application settings, database clients, downloads, or network operations.
"""
import hashlib
import json
import wave
from pathlib import Path, PurePosixPath

ALERTS = Path(__file__).resolve().parent / "seed_data/alerts.json"


def expected_keys(alerts_path: Path = ALERTS) -> list[str]:
    keys = sorted({item["s3_object_key"] for item in json.loads(alerts_path.read_text())})
    for key in keys:
        path = PurePosixPath(key)
        if (path.is_absolute() or ".." in path.parts or not key.startswith("audio/")
                or path.suffix.lower() != ".wav" or "\\" in key):
            raise ValueError("Tracked seed contains an unsafe audio object key.")
    if len({PurePosixPath(key).name for key in keys}) != len(keys):
        raise ValueError("Tracked seed has ambiguous audio filenames.")
    return keys


def describe(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Audio must be a regular local file, not a symlink.")
    try:
        with wave.open(str(path), "rb") as audio:
            if audio.getnframes() < 1 or audio.getnchannels() < 1 or audio.getframerate() < 1:
                raise ValueError("Audio has an empty or invalid WAV header.")
            # Read all frames to reject a truncated payload, rather than trusting its header.
            channels, sample_width, sample_rate = audio.getnchannels(), audio.getsampwidth(), audio.getframerate()
            expected_bytes = audio.getnframes() * channels * sample_width
            read_bytes = 0
            while block := audio.readframes(65536):
                read_bytes += len(block)
            if read_bytes != expected_bytes:
                raise ValueError("Audio WAV payload is truncated.")
    except (wave.Error, EOFError) as error:
        raise ValueError("Audio is not a readable uncompressed WAV.") from error
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"file": str(path.resolve()), "bytes": path.stat().st_size, "sha256": digest.hexdigest(),
            "duration_s": expected_bytes / (channels * sample_width * sample_rate)}


def build(directory: Path, alerts_path: Path = ALERTS) -> dict:
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError("Select a regular local directory containing the downloaded WAVs.")
    candidates: dict[str, list[Path]] = {}
    for path in directory.rglob("*"):
        if path.suffix.lower() == ".wav":
            candidates.setdefault(path.name, []).append(path)
    entries = []
    for key in expected_keys(alerts_path):
        matches = candidates.get(PurePosixPath(key).name, [])
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one local file for {PurePosixPath(key).name}; found {len(matches)}.")
        path = matches[0]
        if not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError("Audio path leaves the selected directory.")
        entries.append({"key": key, **describe(path)})
    return {"schema": 1, "purpose": "local seed audio review; no upload performed", "objects": entries}


def verify(manifest: dict, alerts_path: Path = ALERTS) -> None:
    entries = manifest.get("objects", [])
    keys = [entry.get("key") for entry in entries]
    if manifest.get("schema") != 1 or sorted(keys) != expected_keys(alerts_path):
        raise ValueError("Manifest must contain exactly the tracked seed audio object keys, once each.")
    for entry in entries:
        actual = describe(Path(entry["file"]))
        if (actual["bytes"] != entry["bytes"] or actual["sha256"] != entry["sha256"]
                or ("duration_s" in entry and actual["duration_s"] != entry["duration_s"])):
            raise ValueError("A local audio file changed after manifest creation; regenerate and review.")

