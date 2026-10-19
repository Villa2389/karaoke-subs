"""Compare word timings against a hand-timed karaoke .ass reference.

The reference (gokenshadow's Dr. Stone Ep 1) glosses words in English, so
tokens can't be matched by text. We compare *boundaries* instead: for every
syllable start in the reference, the distance to the nearest syllable start
we produced. Granularity differs slightly (our tokens can be finer), so use
the numbers to compare methods against each other, not as absolute truth.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pysubs2

from .align import AlignedLine

K_RE = re.compile(r"\{\\k[fo]?(\d+)\}")


def karaoke_events(path: Path) -> list[pysubs2.SSAEvent]:
    """The hand-timed lines: templater source lines (effect 'karaoke') when the file has them
    (its Dialogue lines are then generated effects), else plain {\\k} dialogue (NOFX files)."""
    events = [ev for ev in pysubs2.load(str(path), encoding="utf-8-sig").events if "\\k" in ev.text]
    source = [ev for ev in events if ev.effect.strip() == "karaoke"]
    return source or [ev for ev in events if not ev.is_comment]


def reference_boundaries(path: Path) -> list[tuple[float, list[float]]]:
    """(line start, [syllable start times]) for every karaoke line."""
    out = []
    for ev in karaoke_events(path):
        t = ev.start / 1000
        starts = []
        for m in K_RE.finditer(ev.text):
            starts.append(t)
            t += int(m.group(1)) / 100
        out.append((ev.start / 1000, starts))
    return out


TEXT_K_RE = re.compile(r"\{\\k[fo]?(\d+)\}([^{]*)")


def _content_words(text: str) -> set[str]:
    """Uppercase words of a gloss: 'BECOME-tta' -> {'BECOME'}, 'AROUND-THE-WORLD' -> {...}."""
    return {w for w in re.findall(r"[A-Z][A-Z']+", text)} - {"THE", "A", "AN", "TO", "OF"}


def gloss_agreement(aligned: list[AlignedLine], labels: list[list[list[str]]], reference: Path,
                    max_dist: float = 0.15) -> dict:
    """How often our gloss for a word matches the hand-made reference gloss.

    Words are paired by time (nearest reference syllable start within
    `max_dist`). 'exact' = same content words, 'overlap' = share at least one.
    Synonyms count as misses, so read this as a relative score between methods.
    """
    ref: list[tuple[float, str]] = []
    for ev in karaoke_events(reference):
        t = ev.start / 1000
        for m in TEXT_K_RE.finditer(ev.text):
            ref.append((t, m.group(2)))
            t += int(m.group(1)) / 100
    ref.sort()
    ref_t = np.array([t for t, _ in ref])
    exact = overlap = n = 0
    for al, labs in zip(aligned, labels):
        for tok, lab in zip(al.tokens, labs):
            ours = _content_words(" ".join(lab))
            if not ours or tok.name:
                continue
            j = int(np.argmin(np.abs(ref_t - tok.t_start))) if len(ref_t) else -1
            if j < 0 or abs(ref_t[j] - tok.t_start) > max_dist:
                continue
            theirs = _content_words(ref[j][1])
            if not theirs:
                continue
            n += 1
            exact += ours == theirs
            overlap += bool(ours & theirs)
    return {"n": n, "exact": exact / max(1, n), "overlap": overlap / max(1, n)}


def evaluate(aligned: list[AlignedLine], reference: Path, method_prefix: str | None = None) -> dict:
    """Errors for reference boundaries that fall inside one of our lines.

    Reference lines we don't cover (songs, skipped lines) are ignored.
    `method_prefix` restricts scoring to lines aligned by that method.
    """
    spans = [(al.tokens[0].t_start - 0.3, al.tokens[-1].t_end + 0.3, al.method) for al in aligned]
    ours = np.array(sorted(t.t_start for al in aligned for t in al.tokens))
    errors = []
    for _, starts in reference_boundaries(reference):
        for s in starts[1:]:  # skip line start: it comes from the subtitle file, not the aligner
            owner = next((m for a, b, m in spans if a <= s <= b), None)
            if owner is None or (method_prefix and not owner.startswith(method_prefix)):
                continue
            j = np.searchsorted(ours, s)
            cands = [ours[k] for k in (j - 1, j) if 0 <= k < len(ours)]
            errors.append(min(cands, key=lambda c: abs(c - s)) - s)
    e = np.array(errors) if errors else np.zeros(1)
    a = np.abs(e)
    return {
        "n": int(len(errors)),
        "median_ms": float(np.median(a) * 1000),
        "mean_ms": float(np.mean(a) * 1000),
        "signed_mean_ms": float(np.mean(e) * 1000),
        "within_50ms": float(np.mean(a <= 0.05)),
        "within_100ms": float(np.mean(a <= 0.10)),
        "within_200ms": float(np.mean(a <= 0.20)),
    }
