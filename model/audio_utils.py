"""Shared audio preprocessing, used by BOTH training and inference.

One job: turn an audio file on disk into the model's input spectrogram, through
exactly one code path, so what the CNN sees at training time is identical to
what it sees in production.

Public API
    preprocess(filepath)         -> np.ndarray (N_MELS, N_FRAMES) float32 in [0, 1]
        THE function training (dataset.py), evaluation and inference
        (orchestrator.py) must call. Nothing else should be needed.
    load_audio(filepath)         -> 1-D float32 waveform, mono, SAMPLE_RATE Hz
    fix_length(waveform)         -> exactly CLIP_SAMPLES samples
    to_mel_spectrogram(waveform) -> (N_MELS, N_FRAMES) float32 in [0, 1]
    AudioLoadError               -> raised for missing/corrupt/empty/unsupported input

Pipeline (all parameters come from config.py -- no magic numbers here):
    decode -> mono -> resample to SAMPLE_RATE
           -> pad / crop to CLIP_DURATION_S
           -> mel power spectrogram (N_FFT, HOP_LENGTH, N_MELS)
           -> dB relative to the clip's own peak, floored at TOP_DB below it
           -> scaled to [0, 1]

Design notes
    * Deterministic: no randomness here. Augmentation (if any) belongs in
      dataset.py, applied to training data only.
    * Padding: zeros appended at the end.
    * Cropping: clips longer than CLIP_DURATION_S keep their LOUDEST window
      (searched in CROP_SEARCH_HOP_S steps) rather than blindly the first
      seconds, so a short event inside a long recording is not cropped away.
    * Level normalization is per clip (dB relative to the clip's peak), so the
      model sees no dataset-wide statistics that could drift between training
      and serving, and is insensitive to microphone gain. Consequence: absolute
      loudness is not a feature.
    * A digitally silent clip maps to an all-zero spectrogram.
    * .mp3 and other formats libsndfile cannot read are decoded with ffmpeg
      as a fallback (ffmpeg on PATH, or `pip install imageio-ffmpeg`).

Dependencies: numpy, librosa (which brings soundfile).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import librosa
import numpy as np

try:  # works as `model.audio_utils` and as a top-level module
    from . import config
except ImportError:  # pragma: no cover
    import config  # type: ignore


class AudioLoadError(Exception):
    """The file could not be turned into usable audio (missing, corrupt, empty, ...)."""


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def _find_ffmpeg() -> str | None:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _decode_with_ffmpeg(path: Path) -> np.ndarray:
    ffmpeg = _find_ffmpeg()
    if ffmpeg is None:
        raise AudioLoadError(f"{path}: cannot decode and ffmpeg is not available")
    proc = subprocess.run(
        [ffmpeg, "-nostdin", "-v", "error", "-i", str(path), "-vn", "-map", "0:a:0",
         "-ac", str(config.CHANNELS), "-ar", str(config.SAMPLE_RATE),
         "-f", "f32le", "-"],
        capture_output=True, timeout=300,
    )
    if proc.returncode != 0:
        msg = (proc.stderr.decode(errors="replace").strip().splitlines() or ["unknown error"])[-1]
        raise AudioLoadError(f"{path}: ffmpeg could not decode the file ({msg})")
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()


def load_audio(filepath: str | Path) -> np.ndarray:
    """Decode `filepath` to a mono float32 waveform at config.SAMPLE_RATE.

    Handles any input rate / channel count / (wav, mp3, ...) container.
    Raises AudioLoadError if the file is missing, unreadable, empty or non-finite.
    """
    path = Path(filepath)
    if not path.is_file():
        raise AudioLoadError(f"{path}: file not found")

    try:
        y, _ = librosa.load(path, sr=config.SAMPLE_RATE, mono=True)
    except Exception:
        # libsndfile couldn't read it (e.g. older build without mp3, odd codec)
        y = _decode_with_ffmpeg(path)

    y = np.asarray(y, dtype=np.float32)
    if y.size == 0:
        raise AudioLoadError(f"{path}: file contains no audio samples")
    if not np.all(np.isfinite(y)):
        raise AudioLoadError(f"{path}: audio contains NaN/inf samples")
    return y


# --------------------------------------------------------------------------- #
# Fixed length
# --------------------------------------------------------------------------- #
def fix_length(waveform: np.ndarray) -> np.ndarray:
    """Return exactly config.CLIP_SAMPLES samples: zero-pad short clips at the end,
    crop long clips to their loudest window."""
    n = config.CLIP_SAMPLES
    y = np.asarray(waveform, dtype=np.float32)
    if y.size == n:
        return y
    if y.size < n:
        return np.pad(y, (0, n - y.size))

    hop = max(1, int(config.SAMPLE_RATE * config.CROP_SEARCH_HOP_S))
    n_blocks = y.size // hop
    block_energy = np.square(y[: n_blocks * hop].astype(np.float64)).reshape(n_blocks, hop).sum(axis=1)
    win_blocks = max(1, n // hop)
    csum = np.concatenate([[0.0], np.cumsum(block_energy)])
    n_starts = n_blocks - win_blocks + 1
    if n_starts <= 0:
        start = 0
    else:
        window_energy = csum[win_blocks: win_blocks + n_starts] - csum[:n_starts]
        start = int(np.argmax(window_energy)) * hop    # ties (e.g. silence) -> earliest
    start = min(start, y.size - n)
    return y[start: start + n]


# --------------------------------------------------------------------------- #
# Spectrogram
# --------------------------------------------------------------------------- #
def to_mel_spectrogram(waveform: np.ndarray) -> np.ndarray:
    """Waveform -> (N_MELS, N_FRAMES) float32 spectrogram scaled to [0, 1].

    The waveform is first brought to exactly CLIP_SAMPLES (fix_length), so the
    output shape is always identical regardless of input length.
    """
    y = fix_length(waveform)
    mel = librosa.feature.melspectrogram(
        y=y, sr=config.SAMPLE_RATE, n_fft=config.N_FFT,
        hop_length=config.HOP_LENGTH, n_mels=config.N_MELS, power=2.0,
    )
    if not np.any(mel > 0):                       # digital silence
        out = np.zeros_like(mel, dtype=np.float32)
    else:
        db = librosa.power_to_db(mel, ref=np.max, top_db=config.TOP_DB)   # in [-TOP_DB, 0]
        out = ((db + config.TOP_DB) / config.TOP_DB).astype(np.float32)   # -> [0, 1]

    expected = (config.N_MELS, config.N_FRAMES)
    if out.shape != expected:   # config inconsistency; fail loudly rather than feed the CNN a wrong shape
        raise RuntimeError(f"spectrogram shape {out.shape} != expected {expected}")
    return out


# --------------------------------------------------------------------------- #
# The one entry point
# --------------------------------------------------------------------------- #
def preprocess(filepath: str | Path) -> np.ndarray:
    """audio file -> model input spectrogram (N_MELS, N_FRAMES), float32 in [0, 1].

    The single function training and inference both call.
    """
    return to_mel_spectrogram(load_audio(filepath))
