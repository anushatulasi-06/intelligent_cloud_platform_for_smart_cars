"""Dev only: give every seeded alert a playable clip in backend/.local_audio (no S3 needed).

For each alert in backend/app/db/seed_data/alerts.json, copies a real clip of the same sound type
from model/data/<sound_type>/ to backend/.local_audio/<s3_object_key>. Then set
LOCAL_AUDIO_DIR=.local_audio in backend/.env and restart the API.

    python scripts/make_local_audio.py               # from the repo root
    python scripts/make_local_audio.py --dest /tmp/clips

Existing files are left alone, so running it again changes nothing.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "backend" / "app" / "db" / "seed_data" / "alerts.json"
DATA = ROOT / "model" / "data"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", type=Path, default=ROOT / "backend" / ".local_audio")
    args = ap.parse_args()

    alerts = json.loads(SEED.read_text())
    used: dict[str, int] = {}
    copied = skipped = 0
    for a in alerts:
        key, sound = a.get("s3_object_key"), a["sound_type"]
        if not key:
            continue
        target = (args.dest / key).resolve()
        if not target.is_relative_to(args.dest.resolve()):
            print(f"skipping unsafe key {key!r}", file=sys.stderr)
            continue
        if target.exists():
            skipped += 1
            continue
        clips = sorted((DATA / sound).glob("*.wav"))
        if not clips:
            print(f"no clips in {DATA / sound}; is model/data present?", file=sys.stderr)
            return 1
        i = used.get(sound, 0)
        used[sound] = i + 1
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(clips[i % len(clips)], target)
        copied += 1
    print(f"{copied} clip(s) copied, {skipped} already there -> {args.dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
