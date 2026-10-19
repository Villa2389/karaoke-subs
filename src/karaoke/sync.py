"""Shift subtitle lines onto *this* audio.

Downloaded subs are timed for some release (Netflix, TV, Crunchyroll with a
bumper...). We detect speech with Silero VAD and cross-correlate it with the
subtitle on/off pattern. A global offset is found first, then each ~2 minute
block is refined around it so removed ad breaks / recaps are handled too.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .audio import SAMPLE_RATE
from .subs import Line

STEP = 0.01  # 10 ms resolution


@dataclass
class SyncResult:
    global_offset: float
    block_offsets: list[tuple[float, float]]  # (block start time in subs, offset)
    score: float


def speech_mask(audio: np.ndarray) -> np.ndarray:
    import torch
    from silero_vad import get_speech_timestamps, load_silero_vad

    model = load_silero_vad()
    stamps = get_speech_timestamps(
        torch.from_numpy(audio), model, sampling_rate=SAMPLE_RATE,
        threshold=0.4, min_silence_duration_ms=150, speech_pad_ms=30,
    )
    mask = np.zeros(int(len(audio) / SAMPLE_RATE / STEP) + 1, dtype=np.float32)
    for s in stamps:
        mask[int(s["start"] / SAMPLE_RATE / STEP):int(s["end"] / SAMPLE_RATE / STEP)] = 1
    return mask


def subs_mask(lines: list[Line], length: int, t0: float = 0, t1: float = 1e9) -> np.ndarray:
    mask = np.zeros(length, dtype=np.float32)
    for ln in lines:
        if ln.end < t0 or ln.start > t1:
            continue
        mask[max(0, int(ln.start / STEP)):min(length, int(ln.end / STEP))] = 1
    return mask


def _best_lag(a: np.ndarray, b: np.ndarray, max_lag: int) -> tuple[int, float]:
    """Lag L (in steps) maximising sum(a[t + L] * b[t]); centred +/-1 masks."""
    n = len(a) + len(b)
    size = 1 << (n - 1).bit_length()
    fa = np.fft.rfft(a * 2 - 1, size)
    fb = np.fft.rfft(b * 2 - 1, size)
    corr = np.fft.irfft(fa * np.conj(fb), size)
    lags = np.r_[0:max_lag + 1, -max_lag:0]
    vals = corr[lags % size]
    i = int(np.argmax(vals))
    return int(lags[i]), float(vals[i])


def estimate_offset(audio: np.ndarray, lines: list[Line], max_shift: float = 60.0,
                    block: float = 120.0, local_shift: float = 3.0) -> SyncResult:
    vad = speech_mask(audio)
    n = len(vad)
    lag, score = _best_lag(vad, subs_mask(lines, n), int(max_shift / STEP))
    g = lag * STEP

    blocks: list[tuple[float, float]] = []
    t = 0.0
    last = max(ln.end for ln in lines)
    while t < last:
        sm = subs_mask(lines, n, t, t + block)
        if sm.sum() * STEP > 10:  # need enough dialogue to trust a block
            shifted = np.roll(sm, lag)
            local, _ = _best_lag(vad, shifted, int(local_shift / STEP))
            blocks.append((t, g + local * STEP))
        t += block
    return SyncResult(g, blocks, score / max(1, n))


def apply_offset(lines: list[Line], sync: SyncResult) -> None:
    """Shift each line by the offset of the block it falls in (median-smoothed)."""
    if not sync.block_offsets:
        offsets = [(0.0, sync.global_offset)]
    else:
        raw = [o for _, o in sync.block_offsets]
        smooth = [float(np.median(raw[max(0, i - 1):i + 2])) for i in range(len(raw))]
        offsets = [(t, o) for (t, _), o in zip(sync.block_offsets, smooth)]
    for ln in lines:
        off = offsets[0][1]
        for t, o in offsets:
            if ln.start >= t:
                off = o
        ln.start += off
        ln.end += off
