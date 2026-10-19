"""Audio extraction: any media file -> 16 kHz mono float32 waveform."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

SAMPLE_RATE = 16000


def extract_wav(media: Path, out_wav: Path, stream: str = "0:a:0") -> Path:
    """Decode the chosen audio stream once into 16 kHz mono PCM WAV.

    `stream` is an ffmpeg -map spec; for multi-track anime releases use
    e.g. "0:a:m:language:jpn" so the English dub is never picked by accident.
    """
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    if out_wav.exists() and out_wav.stat().st_mtime >= media.stat().st_mtime:
        return out_wav
    require_ffmpeg()
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(media),
         "-map", stream, "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(out_wav)],
        check=True,
    )
    return out_wav


def require_ffmpeg() -> None:
    """Fail with instructions instead of a FileNotFoundError traceback."""
    missing = [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]
    if missing:
        raise SystemExit(f"{' and '.join(missing)} not found on PATH. Install FFmpeg: "
                         "winget install Gyan.FFmpeg (Windows), brew install ffmpeg (macOS), "
                         "sudo apt install ffmpeg (Debian/Ubuntu)")


def load_wav(path: Path) -> np.ndarray:
    audio, sr = sf.read(str(path), dtype="float32")
    if sr != SAMPLE_RATE:
        raise ValueError(f"{path} is {sr} Hz, expected {SAMPLE_RATE}")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio
