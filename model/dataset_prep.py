"""One-time data-prep script: raw Audio_Datasets/ -> organized model/data/.

One job: take the professor-provided raw folders and produce a clean,
training-ready dataset plus a train/val/test manifest. Run once (re-run only if
the raw data or model/config.py changes).

What it does
    1. Walks every folder listed in config.SOURCE_FOLDERS (folder -> category;
       Alert_sounds is deliberately excluded, see config.EXCLUDED_SOURCES).
    2. Normalizes every file to config.SAMPLE_RATE / mono / 16-bit PCM WAV:
         .mp3             -> converted with ffmpeg
         .wav, compliant  -> copied byte-for-byte
         .wav, other      -> re-encoded with ffmpeg
    3. Writes results to model/data/<category>/<name>.wav
       (the original files in Audio_Datasets/ are never modified).
    4. Skips -- never crashes on -- corrupt/unreadable/empty/silent files and
       exact duplicates, and lists them in a summary.
    5. Prints per-category file counts and total duration.
    6. Builds a seeded, stratified train/val/test split and writes
       model/data/manifest.json.

Leakage protection
    Clips derived from the same source recording must never be spread over
    train/val/test, otherwise near-copies of training clips end up in test and
    inflate every metric. Each clip gets a "group"; whole groups go to ONE split.
    Groups are built from file names (see assign_groups):
      1. augmented copies: "Rain (1)_aug1", "0-0_aug2"  -> same group as the original
      2. numbered families: names that differ only by a small trailing index
         ("scream-1", "scream-2", crash clips "12-0", "12-3" -> family "12")
         -> one group per family, if the family has <= FAMILY_MAX members
      3. long numbered series ("road (1)".."road (120)", "wind (N)", "ambulanceN"):
         consecutive numbers are consecutive chunks of the same long recording
         (verified: neighbours are markedly more similar than distant clips), so
         they are cut into blocks of SERIES_BLOCK consecutive clips and each BLOCK
         is one group
      4. the same family appearing in two categories is forced into one split
    Groups are inferred from names only -- the same recording under unrelated
    file names can't be detected. `--check-only` re-audits an existing manifest.

Manifest format (model/data/manifest.json)
    {"meta": {...audio params, categories, counts, seed...},
     "items": [{"filepath": "<relative to model/data>", "category": ...,
                "split": "train|val|test", "source": ..., "group": ...,
                "duration_s": ..., "action": "copied|reencoded|converted"}],
     "skipped": [...], "excluded": [...]}

NOTE: output clips are NOT cropped/padded here -- fixed-length handling is done
in audio_utils at load time, so the same code serves training and inference.

Requires ffmpeg on PATH (or `pip install imageio-ffmpeg` as a fallback).
Python standard library only.

Usage:
    python -m model.dataset_prep             # run from the repo root
    python -m model.dataset_prep --dry-run   # show mapping/counts, write nothing
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import shutil
import statistics
import subprocess
import sys
import wave
from array import array
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

try:
    from . import config
except ImportError:  # executed as a plain script
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from model import config  # type: ignore

AUDIO_EXTS = {".wav", ".mp3"}
AUG_SUFFIX = re.compile(r"(?:_aug\d*)+$")   # "_aug1", "_aug0_aug0", ...


# --------------------------------------------------------------------------- #
# ffmpeg
# --------------------------------------------------------------------------- #
def find_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:  # optional fallback: pip-installed static binary
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        raise SystemExit(
            "error: ffmpeg not found. Install it (macOS: `brew install ffmpeg`) "
            "or `pip install imageio-ffmpeg`."
        )


def ffmpeg_to_wav(ffmpeg: str, src: Path, dst: Path) -> None:
    proc = subprocess.run(
        [ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(src),
         "-vn", "-map", "0:a:0", "-ac", str(config.CHANNELS),
         "-ar", str(config.SAMPLE_RATE), "-c:a", "pcm_s16le", "-f", "wav", str(dst)],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        msg = (proc.stderr.strip().splitlines() or [f"exit {proc.returncode}"])[-1]
        raise RuntimeError(f"ffmpeg failed: {msg}")


# --------------------------------------------------------------------------- #
# WAV inspection (stdlib only)
# --------------------------------------------------------------------------- #
@dataclass
class WavInfo:
    sample_rate: int
    channels: int
    sample_width: int
    frames: int
    truncated: bool      # header promised more data than the file contains
    silent: bool         # every sample is exactly zero (16-bit only)

    @property
    def duration_s(self) -> float:
        return self.frames / self.sample_rate if self.sample_rate else 0.0

    @property
    def compliant(self) -> bool:
        return (self.sample_rate == config.SAMPLE_RATE
                and self.channels == config.CHANNELS
                and self.sample_width == 2
                and not self.truncated)


def inspect_wav(path: Path) -> WavInfo:
    """Raises wave.Error / EOFError / OSError if the file is not a readable PCM WAV."""
    with wave.open(str(path), "rb") as w:
        sr, ch, sw, nframes = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        data = w.readframes(nframes)
    expected = nframes * ch * sw
    truncated = len(data) < expected
    frames = len(data) // (ch * sw) if ch and sw else 0
    silent = False
    if sw == 2 and data:
        a = array("h")
        a.frombytes(data[: len(data) // 2 * 2])
        silent = (max(a) == 0 and min(a) == 0)
    elif not data:
        silent = True
    return WavInfo(sr, ch, sw, frames, truncated, silent)


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
@dataclass
class Item:
    src: Path
    category: str
    group: str
    dst: Path | None = None
    sha1: str = ""
    # filled by processing
    action: str = ""
    duration_s: float = 0.0

    @property
    def rel_src(self) -> str:
        return self.src.relative_to(config.RAW_DATASET_DIR).as_posix()


@dataclass
class Discovery:
    items: list[Item] = field(default_factory=list)
    excluded: list[dict] = field(default_factory=list)   # {"file"/"folder", "reason"}
    skipped: list[dict] = field(default_factory=list)    # {"file", "reason"}


FAMILY_MAX = 15       # a numbered family up to this many distinct clips = ONE group
SERIES_BLOCK = 10     # longer series are cut into blocks of this many consecutive clips
# "road (4)", "scream-3", "ambulance116", "12-0" -> (family, small index). The lookbehind
# rejects long numbers (database ids like "glass-breaking-72956" are not sequence indices).
_TRAILING_INDEX = re.compile(r"^(.*?)[\s_\-]*\(?(?<!\d)(\d{1,3})\)?$")


def base_stem(stem: str) -> str:
    """File stem without augmentation suffix / stray whitespace."""
    return AUG_SUFFIX.sub("", stem.strip()).strip()


def family_and_index(stem: str) -> tuple[str, int | None]:
    """'road (4)' -> ('road', 4);  '12-0' -> ('12', 0);  'glass-breaking-72956' -> (stem, None)."""
    m = _TRAILING_INDEX.match(stem)
    if not m:
        return stem, None
    fam = m.group(1).strip(" _-(")
    return (fam or stem), int(m.group(2))


def assign_groups(items: list["Item"]) -> None:
    """Fill Item.group for every item (see 'Leakage protection' in the module docstring).
    Families are keyed on the name only, so one family appearing in two categories
    (e.g. a family present in two folders/categories) shares a group."""
    parsed = {}
    members: dict[str, set[int]] = defaultdict(set)
    for it in items:
        b = base_stem(it.src.stem)
        fam, n = family_and_index(b)
        parsed[id(it)] = (b, fam, n)
        if n is not None:
            members[fam].add(n)
    for it in items:
        b, fam, n = parsed[id(it)]
        if n is None:
            it.group = b
        elif len(members[fam]) <= FAMILY_MAX:
            it.group = f"family:{fam}"
        else:
            rank = sorted(members[fam]).index(n)
            it.group = f"series:{fam}#block{rank // SERIES_BLOCK:02d}"


def discover() -> Discovery:
    d = Discovery()
    for rule in config.SOURCE_FOLDERS:
        folder = config.RAW_DATASET_DIR / rule["folder"]
        if not folder.is_dir():
            d.skipped.append({"file": str(folder), "reason": "source folder missing"})
            continue
        pattern = re.compile(rule["name_regex"]) if rule["name_regex"] else None
        for p in sorted(folder.rglob("*")):
            if not p.is_file() or p.name.startswith("."):
                continue
            if p.suffix.lower() not in AUDIO_EXTS:
                d.skipped.append({"file": p.relative_to(config.RAW_DATASET_DIR).as_posix(),
                                  "reason": f"unsupported extension {p.suffix!r}"})
                continue
            if pattern and not pattern.fullmatch(p.stem):
                d.excluded.append({"file": p.relative_to(config.RAW_DATASET_DIR).as_posix(),
                                   "reason": f"not matching {rule['name_regex']!r} for "
                                             f"{rule['folder']} (non-{rule['category']} content)"})
                continue
            category = rule["category"]
            for rx, cat in rule.get("relabel", []):                 # e.g. crash clips misfiled under Human_Scream
                if re.fullmatch(rx, base_stem(p.stem)):
                    category = cat
                    break
            d.items.append(Item(p, category, group=""))             # group assigned below

    for ex in config.EXCLUDED_SOURCES:
        folder = config.RAW_DATASET_DIR / ex["folder"]
        n = sum(1 for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXTS) \
            if folder.is_dir() else 0
        d.excluded.append({"folder": ex["folder"], "files": n, "reason": ex["reason"]})
    assign_groups(d.items)
    return d


def assign_output_names(items: list[Item]) -> None:
    """<DATA_DIR>/<category>/<stem>.wav, de-duplicating name clashes (e.g. a.mp3 + a.wav)."""
    used: set[tuple[str, str]] = set()      # (category, lowercase filename)
    for it in items:
        base = it.src.stem
        name, n = f"{base}.wav", 2
        while (it.category, name.lower()) in used:   # case-insensitive (macOS FS)
            name = f"{base}__{n}.wav"
            n += 1
        used.add((it.category, name.lower()))
        it.dst = config.DATA_DIR / it.category / name


def sha1_of(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# Per-file processing
# --------------------------------------------------------------------------- #
def process(it: Item, ffmpeg: str) -> tuple[Item, str | None]:
    """Normalize one file into it.dst. Returns (item, None) on success or
    (item, reason) if the file must be skipped. Never raises."""
    assert it.dst is not None
    it.dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        if it.src.suffix.lower() == ".mp3":
            ffmpeg_to_wav(ffmpeg, it.src, it.dst)
            it.action = "converted"
        else:
            try:
                info = inspect_wav(it.src)
            except (wave.Error, EOFError):
                info = None      # e.g. float/24-bit/odd header: let ffmpeg try
            if info is not None and info.compliant:
                shutil.copyfile(it.src, it.dst)
                it.action = "copied"
            else:
                ffmpeg_to_wav(ffmpeg, it.src, it.dst)
                it.action = "reencoded"

        out = inspect_wav(it.dst)           # verify what we actually produced
        if out.sample_rate != config.SAMPLE_RATE or out.channels != config.CHANNELS:
            raise RuntimeError(f"output is {out.sample_rate} Hz/{out.channels} ch")
        if out.frames == 0:
            raise RuntimeError("no audio samples (empty file)")
        if out.silent:
            raise RuntimeError("audio is digital silence (all samples zero)")
        it.duration_s = out.duration_s
        return it, None
    except Exception as e:  # corrupt / unsupported codec / timeout / ...
        it.dst.unlink(missing_ok=True)
        return it, f"{type(e).__name__}: {e}"


# --------------------------------------------------------------------------- #
# Stratified, group-aware split
# --------------------------------------------------------------------------- #
def split_items(items: list[Item]) -> tuple[dict[str, str], list[str]]:
    """Return ({filepath_key: split}, warnings). Splits whole groups, per category,
    so each category's file counts follow config.SPLIT_RATIOS as closely as possible."""
    warnings: list[str] = []
    assignment: dict[str, str] = {}
    by_cat: dict[str, dict[str, list[Item]]] = defaultdict(lambda: defaultdict(list))
    for it in items:
        by_cat[it.category][it.group].append(it)

    forced: dict[str, str] = {}      # group -> split, for groups that occur in several categories
    for cat in config.CATEGORIES:
        groups = by_cat.get(cat, {})
        if not groups:
            warnings.append(f"{cat}: no usable files")
            continue
        total = sum(len(v) for v in groups.values())
        counts = {s: 0 for s in config.SPLIT_RATIOS}

        def put(k: str, s: str) -> None:
            counts[s] += len(groups[k])
            for it in groups[k]:
                assignment[it.dst.relative_to(config.DATA_DIR).as_posix()] = s

        for k in sorted(k for k in groups if k in forced):      # already decided by an earlier category
            put(k, forced[k])
        rng = random.Random(f"{config.SPLIT_SEED}-{cat}")
        keys = sorted(k for k in groups if k not in forced)
        rng.shuffle(keys)
        keys.sort(key=lambda k: -len(groups[k]))     # big groups first (stable => shuffled ties)
        for k in keys:
            # pick the split furthest below its target share
            s = max(config.SPLIT_RATIOS, key=lambda s: config.SPLIT_RATIOS[s] * total - counts[s])
            put(k, s)
            forced[k] = s
        if len(groups) < len(config.SPLIT_RATIOS):
            warnings.append(f"{cat}: only {len(groups)} recording group(s); cannot fill all splits")
        empty = [s for s, n in counts.items() if n == 0]
        if empty and len(groups) >= len(config.SPLIT_RATIOS):
            warnings.append(f"{cat}: split(s) {empty} ended up empty")
    return assignment, warnings


# --------------------------------------------------------------------------- #
# Leakage audit (works on the manifest's items, so it can re-check any manifest)
# --------------------------------------------------------------------------- #
def audit_leakage(items: list[dict]) -> dict:
    g_splits: dict[str, set] = defaultdict(set)
    g_cats: dict[str, set] = defaultdict(set)
    for it in items:
        g_splits[it["group"]].add(it["split"])
        g_cats[it["group"]].add(it["category"])
    spanning = sorted(g for g, s in g_splits.items() if len(s) > 1)

    # consecutive numbers inside long series ("road (7)" next to "road (8)") that sit in different splits
    series: dict[tuple, dict[int, set]] = defaultdict(lambda: defaultdict(set))
    for it in items:
        fam, n = family_and_index(base_stem(Path(it["source"]).stem))
        if n is not None:
            series[(it["category"], fam)][n].add(it["split"])
    long_series = {}
    for key, nums in series.items():
        if len(nums) <= FAMILY_MAX:
            continue
        pairs = [(n, n + 1) for n in nums if n + 1 in nums]
        crossing = sum(1 for a, b in pairs if len(nums[a] | nums[b]) > 1)
        long_series[f"{key[0]}/{key[1]}"] = {"clips": len(nums), "adjacent_pairs": len(pairs),
                                             "adjacent_pairs_in_different_splits": crossing}
    return {
        "groups": len(g_splits),
        "groups_spanning_multiple_splits": spanning,
        "groups_in_multiple_categories": sorted(g for g, c in g_cats.items() if len(c) > 1),
        "long_series": long_series,
        "largest_groups": sorted(((sum(1 for it in items if it["group"] == g), g) for g in g_splits),
                                 reverse=True)[:5],
    }


def print_audit(a: dict) -> None:
    print("\n=== LEAKAGE AUDIT ===")
    print(f"recording groups: {a['groups']}")
    sp = a["groups_spanning_multiple_splits"]
    print(f"groups spread over more than one split: {len(sp)}" + ("   <-- PROBLEM" if sp else "   (good)"))
    for g in sp[:10]:
        print("   ", g)
    cc = a["groups_in_multiple_categories"]
    if cc:
        print(f"groups containing clips of >1 category (kept in ONE split; labels conflict, review): {len(cc)}")
        for g in cc:
            print("   ", g)
    for name, s in a["long_series"].items():
        print(f"series {name}: {s['clips']} clips, {s['adjacent_pairs_in_different_splits']} of "
              f"{s['adjacent_pairs']} consecutive-number pairs sit in different splits (block boundaries)")
    print("largest groups (clips, group):", [(n, g) for n, g in a["largest_groups"]])


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def fmt_dur(sec: float) -> str:
    sec = int(round(sec))
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def print_report(ok: list[Item], splits: dict[str, str], disc: Discovery,
                 skipped: list[dict], warnings: list[str]) -> None:
    print("\n=== PER-CATEGORY REPORT ===")
    hdr = f"{'category':<14}{'files':>6}{'groups':>8}{'duration':>10}  {'min/med/max s':<16}{'train':>6}{'val':>6}{'test':>6}"
    print(hdr)
    print("-" * len(hdr))
    tot_files = tot_dur = 0
    for cat in config.CATEGORIES:
        its = [i for i in ok if i.category == cat]
        durs = [i.duration_s for i in its]
        sc = Counter(splits[i.dst.relative_to(config.DATA_DIR).as_posix()] for i in its)
        mmm = f"{min(durs):.1f}/{statistics.median(durs):.1f}/{max(durs):.1f}" if durs else "-"
        print(f"{cat:<14}{len(its):>6}{len({i.group for i in its}):>8}{fmt_dur(sum(durs)):>10}  "
              f"{mmm:<16}{sc['train']:>6}{sc['val']:>6}{sc['test']:>6}")
        tot_files += len(its)
        tot_dur += sum(durs)
    print("-" * len(hdr))
    print(f"{'TOTAL':<14}{tot_files:>6}{'':>8}{fmt_dur(tot_dur):>10}")

    short = [i for i in ok if i.duration_s < 1.0]
    longer = [i for i in ok if i.duration_s > config.CLIP_DURATION_S]
    print(f"\nclips shorter than 1 s: {len(short)}; "
          f"longer than the {config.CLIP_DURATION_S:g} s model window: {len(longer)} "
          f"(padded/cropped later by audio_utils)")
    print("actions:", dict(Counter(i.action for i in ok)))

    print("\n=== EXCLUDED BY RULE (not errors) ===")
    folder_level = [e for e in disc.excluded if "folder" in e]
    for e in folder_level:
        print(f"  {e['folder']}: {e['files']} files -- {e['reason']}")
    file_level = Counter(e["reason"] for e in disc.excluded if "file" in e)
    for reason, n in file_level.items():
        print(f"  {n} files -- {reason}")

    print(f"\n=== SKIPPED (corrupt / unreadable / duplicate) : {len(skipped)} ===")
    by_reason = defaultdict(list)
    for s in skipped:
        key = ("exact duplicate of an already-kept file (e.g. no-op augmentation)"
               if s["reason"].startswith("exact duplicate") else s["reason"][:100])
        by_reason[key].append(s)
    for reason, lst in by_reason.items():
        print(f"  [{len(lst)}] {reason}")
        for s in lst[:8]:
            print(f"        {s['file']}" +
                  (f"   <- {s['reason'][len('exact duplicate of '):]}"
                   if s["reason"].startswith("exact duplicate") else ""))
        if len(lst) > 8:
            print(f"        ... and {len(lst) - 8} more (full list in manifest.json)")
    if not skipped:
        print("  none")

    if warnings:
        print("\n=== WARNINGS ===")
        for w in warnings:
            print("  -", w)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Normalize Audio_Datasets/ into model/data/ and build the manifest.")
    ap.add_argument("--dry-run", action="store_true", help="discover and report only; write nothing")
    ap.add_argument("--workers", type=int, default=4, help="parallel ffmpeg workers (default 4)")
    ap.add_argument("--check-only", action="store_true",
                    help="audit the EXISTING manifest.json for leakage; change nothing")
    args = ap.parse_args()

    if args.check_only:
        a = audit_leakage(json.loads(config.MANIFEST_PATH.read_text())["items"])
        print_audit(a)
        return 1 if a["groups_spanning_multiple_splits"] else 0

    if not config.RAW_DATASET_DIR.is_dir():
        print(f"error: raw dataset folder not found: {config.RAW_DATASET_DIR}", file=sys.stderr)
        return 2

    print(f"target: {config.SAMPLE_RATE} Hz, {config.CHANNELS} ch, 16-bit PCM WAV   "
          f"categories: {config.CATEGORIES}")
    disc = discover()
    assign_output_names(disc.items)
    print(f"discovered {len(disc.items)} candidate files in "
          f"{len(config.SOURCE_FOLDERS)} source folders")

    if args.dry_run:
        per = Counter(i.category for i in disc.items)
        grp = {c: len({i.group for i in disc.items if i.category == c}) for c in per}
        for c in config.CATEGORIES:
            print(f"  {c:<14}{per[c]:>6} files, {grp.get(c, 0)} recording groups")
        for e in (x for x in disc.excluded if "folder" in x):
            print(f"  excluded folder: {e['folder']} ({e['files']} files) - {e['reason']}")
        for reason, n in Counter(x["reason"] for x in disc.excluded if "file" in x).items():
            print(f"  excluded by rule: {n} files - {reason}")
        return 0

    ffmpeg = find_ffmpeg()
    skipped: list[dict] = list(disc.skipped)

    # --- exact-duplicate detection (keep the first occurrence) ---
    with ThreadPoolExecutor(args.workers) as ex:
        for it, h in zip(disc.items, ex.map(lambda i: sha1_of(i.src), disc.items)):
            it.sha1 = h
    seen: dict[str, Item] = {}
    unique: list[Item] = []
    for it in disc.items:
        first = seen.get(it.sha1)
        if first is None:
            seen[it.sha1] = it
            unique.append(it)
        else:
            skipped.append({"file": it.rel_src,
                            "reason": f"exact duplicate of {first.rel_src}"})

    # --- fresh output (only our own category folders + manifest are touched) ---
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    for sub in config.DATA_DIR.iterdir():        # all generated category folders, incl. stale ones
        if sub.is_dir():                         # from an earlier category list
            shutil.rmtree(sub, ignore_errors=True)
    config.MANIFEST_PATH.unlink(missing_ok=True)

    # --- normalize ---
    ok: list[Item] = []
    with ThreadPoolExecutor(args.workers) as ex:
        for n, (it, err) in enumerate(ex.map(lambda i: process(i, ffmpeg), unique), 1):
            if err:
                skipped.append({"file": it.rel_src, "reason": err})
            else:
                ok.append(it)
            if n % 250 == 0:
                print(f"  ...{n}/{len(unique)}")

    # --- split + manifest ---
    splits, warnings = split_items(ok)
    ok.sort(key=lambda i: i.dst.relative_to(config.DATA_DIR).as_posix())
    counts = {c: Counter(splits[i.dst.relative_to(config.DATA_DIR).as_posix()]
                         for i in ok if i.category == c) for c in config.CATEGORIES}
    manifest = {
        "meta": {
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sample_rate": config.SAMPLE_RATE,
            "channels": config.CHANNELS,
            "clip_duration_s": config.CLIP_DURATION_S,
            "categories": config.CATEGORIES,
            "label_to_index": {c: i for i, c in enumerate(config.CATEGORIES)},
            "split_ratios": config.SPLIT_RATIOS,
            "split_seed": config.SPLIT_SEED,
            "split_unit": "recording group inferred from file names (augmented copies, numbered "
                          "families and blocks of consecutive series stay in the same split)",
            "filepath_relative_to": "model/data",
            "counts": {c: {"total": sum(v.values()), **dict(v)} for c, v in counts.items()},
        },
        "items": [{
            "filepath": i.dst.relative_to(config.DATA_DIR).as_posix(),
            "category": i.category,
            "split": splits[i.dst.relative_to(config.DATA_DIR).as_posix()],
            "source": i.rel_src,
            "group": i.group,
            "duration_s": round(i.duration_s, 3),
            "action": i.action,
        } for i in ok],
        "skipped": skipped,
        "excluded": disc.excluded,
    }
    audit = audit_leakage(manifest["items"])
    manifest["meta"]["leakage_audit"] = {
        "groups": audit["groups"],
        "groups_spanning_multiple_splits": len(audit["groups_spanning_multiple_splits"]),
        "groups_in_multiple_categories": audit["groups_in_multiple_categories"],
        "long_series": audit["long_series"],
    }
    config.MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))

    print_report(ok, splits, disc, skipped, warnings)
    print_audit(audit)
    print(f"\nmanifest written to {config.MANIFEST_PATH}  ({len(ok)} items)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
