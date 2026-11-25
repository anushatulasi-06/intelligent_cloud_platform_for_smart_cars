#!/usr/bin/env python3
"""Prepare/verify local seeded-alert audio. No downloads and no AWS operations.

Match only the tracked alert object keys, validate WAV headers, and hash file contents.
The resulting JSON is a local review artifact, not authorization to upload anything.
"""
import argparse
import json
import os
from pathlib import Path
import sys

# Keep the review CLI dependency-free; the canonical implementation ships in backend images.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.db.history_media import ALERTS, build, describe, expected_keys, verify  # noqa: E402,F401


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
