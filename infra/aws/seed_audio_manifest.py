#!/usr/bin/env python3
"""Prepare/verify local seeded-alert audio. No downloads and no AWS operations.

Match only the tracked alert object keys, validate WAV headers, and hash file contents.
The resulting JSON is a local review artifact, not authorization to upload anything.
"""
import argparse
import hashlib
import json
import os
import wave
from pathlib import Path, PurePosixPath

ALERTS = Path(__file__).resolve().parents[2] / "backend/app/db/seed_data/alerts.json"


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
            expected_bytes = audio.getnframes() * audio.getnchannels() * audio.getsampwidth()
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
    return {"file": str(path.resolve()), "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


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
        if actual["bytes"] != entry["bytes"] or actual["sha256"] != entry["sha256"]:
            raise ValueError("A local audio file changed after manifest creation; regenerate and review.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--directory", type=Path)
    mode.add_argument("--verify", type=Path, metavar="MANIFEST")
    parser.add_argument("--output", type=Path, help="Local JSON output; keep it private and outside git")
    args = parser.parse_args()
    try:
        if args.verify:
            manifest = json.loads(args.verify.read_text())
            verify(manifest)
        else:
            if not args.output:
                parser.error("--directory requires --output")
            manifest = build(args.directory)
            # Refuse overwriting an existing review artifact without a new explicit path.
            descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                json.dump(manifest, stream, indent=2)
                stream.write("\n")
        print(f"PASS {len(manifest['objects'])} local WAV/key/hash checks; no download or upload performed.")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Audio plan blocked: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
