"""English subtitles as context (docs/glossing-rules.md, section 3).

They are a *hint* for the LLM (sense, idioms, contractions) and the best name
detector: a Capitalised English word that matches a token's romaji
(Senku ~ senkuu) marks that token as a name, spelled as the English subs do.
They are never copied: English word order and localisation differ.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pysubs2

from .align import AlignedLine
from .subs import Line
from .sync import apply_offset, estimate_offset

PAD = 0.5  # seconds: English lines are often timed a little differently


def load_english(path: Path) -> list[Line]:
    subs = pysubs2.load(str(path), encoding="utf-8-sig")
    lines = []
    for ev in sorted(subs.events, key=lambda e: e.start):
        if ev.is_comment or _is_sign(ev, subs):
            continue
        text = re.sub(r"\{[^}]*\}", "", ev.text).replace("\\N", " ").replace("\\n", " ")
        text = re.sub(r"<[^>]+>", "", text)  # srt italics etc.
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            lines.append(Line(len(lines), ev.start / 1000, ev.end / 1000, ev.text, text))
    return lines


def _is_sign(ev: pysubs2.SSAEvent, subs: pysubs2.SSAFile) -> bool:
    """Typeset on-screen text (signs, disclaimers), not speech: placed with \\pos/\\move,
    a top-of-screen style, or actor "Text" (the usual fansub/streaming convention)."""
    style = subs.styles.get(ev.style)
    top = style is not None and int(style.alignment) in (7, 8, 9)
    return top or ev.name == "Text" or re.search(r"\\(pos|move)\(", ev.text) is not None


def sync_english(audio: np.ndarray, lines: list[Line]) -> float:
    sync = estimate_offset(audio, lines)
    apply_offset(lines, sync)
    return sync.global_offset


def pair(aligned: list[AlignedLine], english: list[Line]) -> list[str]:
    """English text overlapping each Japanese line (one English line may cover several)."""
    out = []
    for al in aligned:
        s, e = al.line.start - PAD, al.line.end + PAD
        hits = [en for en in english if en.start < e and en.end > s]
        # keep the ones with the most overlap first; drop barely-touching ones if others exist
        hits.sort(key=lambda en: -(min(e, en.end) - max(s, en.start)))
        best = [h for h in hits if min(al.line.end, h.end) - max(al.line.start, h.start) > 0.2] or hits[:1]
        out.append(" ".join(h.text for h in sorted(best, key=lambda h: h.start)))
    return out


def _plain(s: str) -> str:
    s = re.sub(r"[^a-z]", "", s.lower())
    s = s.replace("ou", "o")
    return re.sub(r"a+|e+|i+|o+|u+", lambda m: m.group()[0], s)


def mark_names(aligned: list[AlignedLine], english: list[str]) -> int:
    """Tokens whose romaji matches a Capitalised word in the paired English line
    become names with that spelling. Returns how many tokens were marked."""
    marked = 0
    for al, en in zip(aligned, english):
        caps = {_plain(w): w for w in re.findall(r"\b[A-Z][a-z]{2,}\b", en)}
        for t in al.tokens:
            if t.pos in ("名詞", "代名詞") and t.romaji and len(t.romaji) >= 3 and not t.surface.isascii():
                w = caps.get(_plain(t.romaji))
                if w:
                    t.name, t.spelling = True, w
                    marked += 1
    return marked
