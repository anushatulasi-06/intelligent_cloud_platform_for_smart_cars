#!/usr/bin/env python3
"""One-time audio normalization step (run BEFORE training).

For every audio file under a folder (recursive):
  * .mp3  -> converted to .wav (mono, TARGET_SR, 16-bit PCM) next to the original
  * .wav  -> left untouched if already mono @ TARGET_SR; otherwise re-encoded
             in place (via a temp file + atomic replace)

Backends (first available wins):
  1. ffmpeg / ffprobe on PATH
  2. pydub  (NOTE: pydub itself shells out to ffmpeg to decode mp3, so it only
     helps when ffmpeg exists but isn't on PATH, e.g. configured via
     pydub.AudioSegment.converter. Plain .wav handling works without ffmpeg.)

Safety:
  * Originals .mp3 are KEPT unless --delete-mp3 is passed.
  * An .mp3 whose sibling <stem>.wav already exists is flagged as a CONFLICT and
    skipped (never overwritten).
  * Use --dry-run to see what would happen without touching anything.
  * Exit code is 1 if any file failed, else 0 (conflicts are reported as warnings).

Usage:
  python scripts/normalize_audio.py Audio_Datasets --dry-run
  python scripts/normalize_audio.py Audio_Datasets
  python scripts/normalize_audio.py Audio_Datasets --sr 16000 --report report.json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Default target. The repo has no project-owned preprocessing code yet; 16 kHz mono
# is what the professor's accident/traffic/glass pipeline (accident_baseline.py:
# librosa sr=16000, 128 mel bins) uses. (environmental.py uses 48 kHz for CLAP.)
# Override with --sr / --channels if our pipeline settles on something else.
DEFAULT_SR = 16000
DEFAULT_CHANNELS = 1

AUDIO_EXTS = {".wav", ".mp3"}


# --------------------------------------------------------------------------- #
# Backend detection
# --------------------------------------------------------------------------- #
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

try:  # optional
    from pydub import AudioSegment  # type: ignore

    HAVE_PYDUB = True
except Exception:  # pragma: no cover
    AudioSegment = None  # type: ignore
    HAVE_PYDUB = False


def backend_name() -> str:
    if FFMPEG:
        return "ffmpeg"
    if HAVE_PYDUB:
        return "pydub"
    return "none"


# --------------------------------------------------------------------------- #
# Probing
# --------------------------------------------------------------------------- #
@dataclass
class AudioInfo:
    sample_rate: int
    channels: int
    frames: int | None = None  # None if unknown


def probe(path: Path) -> AudioInfo:
    """Return sample rate / channels. Raises on unreadable/corrupt files."""
    errors: list[str] = []

    # 1) stdlib wave: fast, no deps, handles plain PCM wav.
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as w:
                return AudioInfo(w.getframerate(), w.getnchannels(), w.getnframes())
        except Exception as e:  # float wav, WAVE_FORMAT_EXTENSIBLE, corrupt, ...
            errors.append(f"wave: {type(e).__name__}: {e}")

    # 2) ffprobe
    if FFPROBE:
        try:
            out = subprocess.run(
                [FFPROBE, "-v", "error", "-select_streams", "a:0",
                 "-show_entries", "stream=sample_rate,channels,duration",
                 "-of", "json", str(path)],
                capture_output=True, text=True, check=True, timeout=60,
            )
            streams = json.loads(out.stdout).get("streams", [])
            if not streams:
                raise ValueError("no audio stream found")
            s = streams[0]
            return AudioInfo(int(s["sample_rate"]), int(s["channels"]))
        except Exception as e:
            errors.append(f"ffprobe: {getattr(e, 'stderr', '') or e}".strip())

    # 3) pydub
    if HAVE_PYDUB:
        try:
            seg = AudioSegment.from_file(str(path))
            return AudioInfo(seg.frame_rate, seg.channels, int(seg.frame_count()))
        except Exception as e:
            errors.append(f"pydub: {e}")

    if not errors:
        errors.append("no probing backend available (install ffmpeg or pydub)")
    raise RuntimeError("; ".join(errors))


# --------------------------------------------------------------------------- #
# Conversion
# --------------------------------------------------------------------------- #
def convert(src: Path, dst_tmp: Path, sr: int, channels: int) -> None:
    """Decode src and write mono/sr 16-bit PCM WAV to dst_tmp. Raises on failure."""
    if FFMPEG:
        proc = subprocess.run(
            [FFMPEG, "-nostdin", "-v", "error", "-y", "-i", str(src),
             "-vn", "-map", "0:a:0", "-ac", str(channels), "-ar", str(sr),
             "-c:a", "pcm_s16le", "-f", "wav", str(dst_tmp)],
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg: {proc.stderr.strip() or 'exit ' + str(proc.returncode)}")
        return
    if HAVE_PYDUB:
        seg = AudioSegment.from_file(str(src))
        seg = seg.set_frame_rate(sr).set_channels(channels).set_sample_width(2)
        seg.export(str(dst_tmp), format="wav")
        return
    raise RuntimeError("no conversion backend available (install ffmpeg or `pip install pydub`)")


# --------------------------------------------------------------------------- #
# Per-file logic
# --------------------------------------------------------------------------- #
@dataclass
class Report:
    converted: list[str] = field(default_factory=list)       # mp3 -> wav
    reencoded: list[str] = field(default_factory=list)       # wav that was non-compliant
    compliant: list[str] = field(default_factory=list)       # wav already OK (untouched)
    failed: list[dict] = field(default_factory=list)         # {"file", "reason"}
    conflicts: list[dict] = field(default_factory=list)      # mp3 skipped: wav exists
    would: list[str] = field(default_factory=list)           # dry-run actions


def _tmp_path(final: Path) -> Path:
    return final.with_name(f".{final.name}.tmp")


def _verify_output(tmp: Path, sr: int, channels: int) -> None:
    info = probe_wav_strict(tmp)
    if info.sample_rate != sr or info.channels != channels:
        raise RuntimeError(
            f"output verification failed: got {info.sample_rate} Hz / {info.channels} ch"
        )
    if not info.frames:
        raise RuntimeError("output is empty (0 samples) - source likely corrupt/silent-truncated")


def probe_wav_strict(path: Path) -> AudioInfo:
    with wave.open(str(path), "rb") as w:
        return AudioInfo(w.getframerate(), w.getnchannels(), w.getnframes())


def process_mp3(path: Path, sr: int, ch: int, rep: Report, dry: bool, delete_mp3: bool) -> None:
    dst = path.with_suffix(".wav")
    # Case-insensitive collision check (macOS default FS is case-insensitive).
    if dst.exists() or any(p.name.lower() == dst.name.lower() for p in path.parent.iterdir()
                           if p.suffix.lower() == ".wav"):
        rep.conflicts.append({"file": str(path), "reason": f"{dst.name} already exists; not overwritten"})
        return
    if dry:
        rep.would.append(f"convert {path} -> {dst.name}")
        return
    tmp = _tmp_path(dst)
    try:
        convert(path, tmp, sr, ch)
        _verify_output(tmp, sr, ch)
        os.replace(tmp, dst)
        rep.converted.append(str(path))
        if delete_mp3:
            path.unlink()
    except Exception as e:
        rep.failed.append({"file": str(path), "reason": str(e)})
        tmp.unlink(missing_ok=True)


def process_wav(path: Path, sr: int, ch: int, rep: Report, dry: bool) -> None:
    try:
        info = probe(path)
    except Exception as e:
        rep.failed.append({"file": str(path), "reason": f"unreadable/corrupt wav: {e}"})
        return

    if info.sample_rate == sr and info.channels == ch:
        if info.frames == 0:
            rep.failed.append({"file": str(path), "reason": "wav has 0 samples"})
        else:
            rep.compliant.append(str(path))
        return

    why = f"{info.sample_rate} Hz / {info.channels} ch -> {sr} Hz / {ch} ch"
    if dry:
        rep.would.append(f"re-encode {path} ({why})")
        return
    tmp = _tmp_path(path)
    try:
        convert(path, tmp, sr, ch)
        _verify_output(tmp, sr, ch)
        os.replace(tmp, path)  # atomic; original only replaced after verified success
        rep.reencoded.append(f"{path}  ({why})")
    except Exception as e:
        rep.failed.append({"file": str(path), "reason": str(e)})
        tmp.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path, help="root folder to scan recursively")
    ap.add_argument("--sr", type=int, default=DEFAULT_SR, help=f"target sample rate (default {DEFAULT_SR})")
    ap.add_argument("--channels", type=int, default=DEFAULT_CHANNELS, help=f"target channels (default {DEFAULT_CHANNELS})")
    ap.add_argument("--dry-run", action="store_true", help="report what would change; modify nothing")
    ap.add_argument("--delete-mp3", action="store_true", help="delete each .mp3 after a verified conversion")
    ap.add_argument("--report", type=Path, help="also write the full report as JSON to this path")
    args = ap.parse_args()

    if not args.folder.is_dir():
        print(f"error: {args.folder} is not a directory", file=sys.stderr)
        return 2
    if backend_name() == "none":
        print("error: need ffmpeg on PATH (brew install ffmpeg) or pydub (pip install pydub).", file=sys.stderr)
        return 2

    files = sorted(p for p in args.folder.rglob("*")
                   if p.is_file() and p.suffix.lower() in AUDIO_EXTS and not p.name.startswith("."))
    print(f"backend={backend_name()}  target={args.sr} Hz, {args.channels} ch, 16-bit PCM"
          f"  files={len(files)}{'  [DRY RUN]' if args.dry_run else ''}")
    if backend_name() == "pydub" and any(p.suffix.lower() == ".mp3" for p in files):
        print("warning: pydub needs ffmpeg to decode .mp3; conversions will fail if it's missing.")

    rep = Report()
    for i, p in enumerate(files, 1):
        if p.suffix.lower() == ".mp3":
            process_mp3(p, args.sr, args.channels, rep, args.dry_run, args.delete_mp3)
        else:
            process_wav(p, args.sr, args.channels, rep, args.dry_run)
        if i % 250 == 0:
            print(f"  ...{i}/{len(files)}")

    # ---- report ----
    print("\n=== SUMMARY ===")
    if args.dry_run:
        print(f"would change : {len(rep.would)}")
    print(f"converted mp3->wav : {len(rep.converted)}")
    print(f"re-encoded wav     : {len(rep.reencoded)}")
    print(f"already compliant  : {len(rep.compliant)}")
    print(f"conflicts (skipped): {len(rep.conflicts)}")
    print(f"FAILED             : {len(rep.failed)}")

    for title, items in (("WOULD CHANGE", rep.would), ("CONVERTED", rep.converted),
                         ("RE-ENCODED", rep.reencoded)):
        if items:
            print(f"\n--- {title} ---")
            print("\n".join(items))
    if rep.conflicts:
        print("\n--- CONFLICTS (mp3 skipped, wav with same name exists) ---")
        for c in rep.conflicts:
            print(f"{c['file']}: {c['reason']}")
    if rep.failed:
        print("\n--- FAILED (corrupt / unsupported codec / etc.) ---")
        for f in rep.failed:
            print(f"{f['file']}: {f['reason']}")

    if args.report:
        args.report.write_text(json.dumps({"target": {"sr": args.sr, "channels": args.channels},
                                           **asdict(rep)}, indent=2))
        print(f"\nfull report written to {args.report}")

    # Conflicts are warnings (e.g. on a re-run the earlier .wav already exists);
    # only genuine failures produce a non-zero exit code.
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
