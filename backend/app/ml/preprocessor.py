"""Audio file -> log-mel tensor, identical to training (model/audio_utils.py).

Every parameter comes from the model's config.json (see model_config.py); nothing is hard-coded.

Steps (same order as training):
  decode -> mono -> resample to sample_rate
  -> pad with zeros at the end, or crop long clips to their LOUDEST window (searched in
     crop_search_hop_s steps)
  -> mel power spectrogram (n_fft, hop_length, n_mels, window, center, pad_mode, fmin, fmax, htk, norm)
  -> power_to_db relative to the clip's own peak (ref=max, amin, top_db)
  -> scaled to [0, 1] with (db + top_db) / top_db; digital silence -> all zeros
"""
import shutil
import subprocess
from pathlib import Path
from typing import Any

import librosa
import numpy as np
import torch


class AudioLoadError(Exception):
    """Missing, corrupt, empty or undecodable audio."""


class AudioPreprocessor:
    def __init__(self, config: dict[str, Any]):
        self.audio = config["audio"]
        self.features = config["features"]
        self.input_shape = tuple(config["input_shape"])

    # ---- loading --------------------------------------------------------------------------

    def load(self, path: str | Path) -> np.ndarray:
        """WAV or MP3 -> mono float32 waveform at sample_rate, padded/cropped to duration_s."""
        path = Path(path)
        if not path.is_file():
            raise AudioLoadError(f"{path}: file not found")
        sr = self.audio["sample_rate"]
        try:
            y, _ = librosa.load(path, sr=sr, mono=True)
        except Exception:  # libsndfile could not read it: fall back to ffmpeg, as training does
            y = self._decode_with_ffmpeg(path, sr)
        y = np.asarray(y, dtype=np.float32)
        if y.size == 0:
            raise AudioLoadError(f"{path}: no audio samples")
        if not np.all(np.isfinite(y)):
            raise AudioLoadError(f"{path}: audio contains NaN or inf")
        return self.fix_length(y)

    @staticmethod
    def _decode_with_ffmpeg(path: Path, sr: int) -> np.ndarray:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise AudioLoadError(f"{path}: cannot decode and ffmpeg is not available")
        proc = subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-i", str(path), "-vn", "-map", "0:a:0",
                               "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"], capture_output=True, timeout=300)
        if proc.returncode != 0:
            raise AudioLoadError(f"{path}: ffmpeg could not decode the file")
        return np.frombuffer(proc.stdout, dtype=np.float32).copy()

    def fix_length(self, waveform: np.ndarray) -> np.ndarray:
        """Exactly clip_samples: zero-pad at the end, or keep the loudest window of a long clip."""
        n = self.audio["clip_samples"]
        y = np.asarray(waveform, dtype=np.float32)
        if y.size == n:
            return y
        if y.size < n:
            return np.pad(y, (0, n - y.size))
        hop = max(1, int(self.audio["sample_rate"] * self.audio["crop_search_hop_s"]))
        n_blocks = y.size // hop
        block_energy = np.square(y[: n_blocks * hop].astype(np.float64)).reshape(n_blocks, hop).sum(axis=1)
        win_blocks = max(1, n // hop)
        csum = np.concatenate([[0.0], np.cumsum(block_energy)])
        n_starts = n_blocks - win_blocks + 1
        if n_starts <= 0:
            start = 0
        else:
            window_energy = csum[win_blocks: win_blocks + n_starts] - csum[:n_starts]
            start = int(np.argmax(window_energy)) * hop          # ties (e.g. silence) -> earliest
        start = min(start, y.size - n)
        return y[start: start + n]

    # ---- features -------------------------------------------------------------------------

    def to_log_mel_array(self, wave: np.ndarray) -> np.ndarray:
        """(n_mels, n_frames) float32 in [0, 1]."""
        f = self.features
        y = self.fix_length(wave)
        mel = librosa.feature.melspectrogram(
            y=y, sr=self.audio["sample_rate"], n_fft=f["n_fft"], hop_length=f["hop_length"],
            win_length=f.get("win_length"), window=f["window"], center=f["center"], pad_mode=f["pad_mode"],
            power=f["power"], n_mels=f["n_mels"], fmin=f["fmin"], fmax=f["fmax"], htk=f["htk"],
            norm=f["mel_norm"])
        if not np.any(mel > 0):                                  # digital silence
            out = np.zeros_like(mel, dtype=np.float32)
        else:
            db = librosa.power_to_db(mel, ref=np.max, amin=f["amin"], top_db=f["top_db"])
            out = ((db + f["top_db"]) / f["top_db"]).astype(np.float32)
        if (1, *out.shape) != self.input_shape:
            raise ValueError(f"log-mel shape {(1, *out.shape)} != model input shape {self.input_shape}")
        return out

    def to_log_mel(self, wave: np.ndarray) -> torch.Tensor:
        """Tensor [1, n_mels, n_frames] (one channel), ready for the CNN."""
        return torch.from_numpy(self.to_log_mel_array(wave)).unsqueeze(0)

    def preprocess(self, path: str | Path) -> torch.Tensor:
        return self.to_log_mel(self.load(path))
