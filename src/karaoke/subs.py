"""Load Japanese subtitle files and clean them into spoken text.

The subtitle file only decides *line* boundaries (when a line appears and
disappears). Everything that is not spoken is stripped here:
speaker labels （千空）, sound tags （笑い声）, ♪ song markers, dashes, ellipses.
Furigana written as 漢字(かな) is removed from the display text but kept as a
reading hint, because it is the most reliable source for names.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pysubs2

from .jpnum import digits_to_kanji

KANJI = r"[一-鿿㐀-䶿々〆ヵヶ]"
KANA = r"[ぁ-ゖァ-ーー]"

# Full-width parentheses = speaker label or sound description (never spoken).
# They may contain half-width furigana inside, e.g. （大樹(たいじゅ)）.
LABEL_RE = re.compile(r"（[^（）]*）|\[[^\]]*\]|【[^】]*】")
FURIGANA_RE = re.compile(rf"({KANJI}+)\(({KANA}+)\)")
# Characters that only carry layout/emphasis.
DROP_RE = re.compile(r"[―─…‥♪♬→]+|\.\.\.|　")
# A wave dash after a sound means "stretch it": ほ～ん is spoken ほーん.
STRETCH_RE = re.compile(r"[～〜]+")


@dataclass
class Furigana:
    start: int  # char offset in Line.text
    end: int
    reading: str  # hiragana/katakana as written in the subtitle


@dataclass
class Line:
    index: int
    start: float  # seconds
    end: float
    raw: str
    text: str  # cleaned spoken text (no furigana, no labels)
    furigana: list[Furigana] = field(default_factory=list)


def clean_text(raw: str) -> tuple[str, list[Furigana]]:
    text = raw.replace("\\N", " ").replace("\\n", " ").replace("\n", " ")
    text = re.sub(r"\{[^}]*\}", "", text)  # ASS override tags
    text = LABEL_RE.sub("", text)
    text = DROP_RE.sub(" ", text)
    text = STRETCH_RE.sub("ー", text)
    text = digits_to_kanji(text)

    out: list[str] = []
    furigana: list[Furigana] = []
    pos = 0
    length = 0
    for m in FURIGANA_RE.finditer(text):
        before = text[pos:m.start()]
        out.append(before)
        length += len(before)
        base = m.group(1)
        furigana.append(Furigana(length, length + len(base), m.group(2)))
        out.append(base)
        length += len(base)
        pos = m.end()
    out.append(text[pos:])
    cleaned = "".join(out)

    # Collapse whitespace and keep furigana offsets valid.
    result: list[str] = []
    mapping: list[int] = []  # old offset -> new offset
    prev_space = True
    for ch in cleaned:
        mapping.append(len(result))
        if ch.isspace():
            if not prev_space:
                result.append(" ")
            prev_space = True
        else:
            result.append(ch)
            prev_space = False
    mapping.append(len(result))
    final = "".join(result).rstrip()
    for f in furigana:
        f.start, f.end = mapping[f.start], mapping[f.end]
    return final, furigana


def has_speech(text: str) -> bool:
    return re.search(rf"{KANJI}|{KANA}|[ぁ-ゖA-Za-zＡ-Ｚａ-ｚ0-9０-９]", text) is not None


def load_lines(path: Path, names_from: list[Path] = ()) -> list[Line]:
    """`names_from`: other subtitle files of the same series. Furigana is given for a name only
    the first time it appears, which may be in another episode (夏音(かのん) in Ep 5 only)."""
    subs = pysubs2.load(str(path), encoding="utf-8-sig")
    lines: list[Line] = []
    for ev in sorted(subs.events, key=lambda e: e.start):
        if ev.is_comment:
            continue
        raw = ev.text
        # Pure song lines (♪ ... ♪) are sung over music; skip them for now.
        if raw.strip().startswith("♪"):
            continue
        text, furi = clean_text(raw)
        if not has_speech(text):
            continue
        lines.append(Line(len(lines), ev.start / 1000, ev.end / 1000, raw, text, furi))
    others = [ln for other in names_from if Path(other) != Path(path) for ln in load_lines(Path(other))]
    _propagate_furigana(lines, others)
    return lines


def _propagate_furigana(lines: list[Line], others: list[Line] = ()) -> None:
    """Subtitles give furigana for a name only on first appearance; reuse it.

    Only names are reused. Creative one-off readings (Dr. Stone writes
    世界 with ruby ストーンワールド once) must not leak onto ordinary 世界.
    """
    known: dict[str, str] = {}
    for line in [*lines, *others]:  # this episode's readings first
        for f in line.furigana:
            base = line.text[f.start:f.end]
            if base not in known and _is_name_reading(base, f.reading):
                known[base] = f.reading
    for line in lines:
        for base, reading in sorted(known.items(), key=lambda kv: -len(kv[0])):
            for m in re.finditer(re.escape(base), line.text):
                if not any(f.start < m.end() and m.start() < f.end for f in line.furigana):
                    line.furigana.append(Furigana(m.start(), m.end(), reading))
        line.furigana.sort(key=lambda f: f.start)


def _is_name_reading(base: str, reading: str) -> bool:
    """Hiragana furigana is a reading aid (names: 杠(ゆずりは)) -> reuse.
    Katakana ruby is usually a creative gloss (世界(ストーンワールド)) -> reuse
    only if the dictionary doesn't know `base` as a common word read differently.
    """
    from .reading import hira_to_kata, tagger

    if re.fullmatch(r"[ぁ-ゖー]+", reading):
        return True
    words = list(tagger()(base))
    if len(words) != 1:
        return True  # dictionary doesn't know it as a word: likely a name (千空 -> 千+空)
    f = words[0].feature
    if f.pos2 == "固有名詞":
        return True
    return hira_to_kata(f.kana or "") == hira_to_kata(reading)
