"""JMdict lookup: candidate English meanings for a UniDic token.

JMdict (EDRDG, CC BY-SA 4.0) is downloaded on first use (paths.jmdict_file) and parsed once
into a compact cached index. Lookup keys come from UniDic: the lemma (成る), its
written base form (なる) and its reading (ナル), so inflected forms like なっ
find their dictionary entry.
"""
from __future__ import annotations

import gzip
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from pathlib import Path

from .paths import dict_dir, jmdict_file
from .reading import Token, hira_to_kata, unvoiced

# None = the default place (paths.dict_dir); tests and scripts may point these elsewhere.
JMDICT: Path | None = None
CACHE: Path | None = None

# UniDic pos1 -> words that appear in the matching JMdict part-of-speech text.
POS_HINT = {
    "名詞": ("noun",), "代名詞": ("pronoun",), "動詞": ("verb",),
    "形容詞": ("adjective (keiyoushi)",), "形状詞": ("adjectival noun", "na-adjective"),
    "副詞": ("adverb",), "連体詞": ("pre-noun",), "感動詞": ("interjection",),
    "接頭辞": ("prefix",), "接尾辞": ("suffix", "counter"),
}


@dataclass
class Entry:
    kanji: list[str]
    readings: list[str]
    common: bool
    senses: list[tuple[list[str], list[str]]]  # (pos texts, glosses)


def _build_index(jmdict: Path) -> dict:
    entries = []
    for _, el in ET.iterparse(gzip.open(jmdict), events=("end",)):
        if el.tag != "entry":
            continue
        kanji = [k.findtext("keb") for k in el.findall("k_ele")]
        readings = [r.findtext("reb") for r in el.findall("r_ele")]
        common = el.find(".//ke_pri") is not None or el.find(".//re_pri") is not None
        senses, last_pos = [], []
        for s in el.findall("sense"):
            pos = [p.text for p in s.findall("pos")] or last_pos  # pos carries over
            last_pos = pos
            senses.append((pos, [g.text for g in s.findall("gloss") if g.text]))
        entries.append([kanji, readings, common, senses])
        el.clear()
    by_key: dict[str, list[int]] = {}
    for i, (kanji, readings, _, _) in enumerate(entries):
        for k in set(kanji + [hira_to_kata(r) for r in readings]):
            by_key.setdefault(k, []).append(i)
    return {"entries": entries, "by_key": by_key}


class Dictionary:
    def __init__(self) -> None:
        cache = CACHE or dict_dir() / "jmdict_index.json.gz"
        if not cache.exists():
            data = _build_index(JMDICT or jmdict_file())
            cache.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(cache, "wt", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        with gzip.open(cache, "rt", encoding="utf-8") as f:
            data = json.load(f)
        self.entries = [Entry(*e) for e in data["entries"]]
        self.by_key = data["by_key"]

    def is_word(self, written: str) -> bool:
        return written in self.by_key

    def has_reading(self, written: str, reading: str) -> bool:
        """A dictionary word written `written` and read `reading` (katakana)."""
        return any(hira_to_kata(r) == reading for i in self.by_key.get(written, []) for r in self.entries[i].readings)

    def is_compound(self, written: str, reading: str) -> bool:
        """A common word written `written` and read `reading` (katakana, voicing ignored)."""
        return any(self.entries[i].common and any(unvoiced(hira_to_kata(r)) == unvoiced(reading)
                                                 for r in self.entries[i].readings)
                   for i in self.by_key.get(written, []))

    def is_common(self, written: str) -> bool:
        return any(self.entries[i].common for i in self.by_key.get(written, []))

    def lookup(self, tok: Token, limit: int = 3) -> list[Entry]:
        """Best entries: written form + reading both match > one matches; common first.
        A potential verb (頼れる) is looked up as its plain verb (頼る)."""
        if tok.plain:
            return self.lookup(replace(tok, lemma=tok.plain, base=tok.plain, plain="", lemma_kana=""), limit)
        written = [k for k in (tok.lemma, tok.base, tok.surface) if k]
        reading = hira_to_kata(tok.lemma_kana) if tok.lemma_kana else ""
        scored: dict[int, int] = {}
        for k in written:
            for i in self.by_key.get(k, []) + self.by_key.get(hira_to_kata(k), []):
                e = self.entries[i]
                score = 2
                if reading and reading in (hira_to_kata(r) for r in e.readings):
                    score += 2
                if e.common:
                    score += 1
                if _pos_matches(e, tok.pos):
                    score += 1
                scored[i] = max(scored.get(i, 0), score)
        if not scored and reading:
            for i in self.by_key.get(reading, []):
                scored[i] = 1 + self.entries[i].common
        best = sorted(scored, key=lambda i: (-scored[i], i))[:limit]
        return [self.entries[i] for i in best]

    _vocab: set[str] | None = None

    def english_vocab(self) -> set[str]:
        """Every English word used in JMdict glosses (~100k): a free spell-check list."""
        if self._vocab is None:
            words: set[str] = set()
            for e in self.entries:
                for _, glosses in e.senses:
                    for g in glosses:
                        words.update(w.upper() for w in re.findall(r"[A-Za-z']+", g))
            self._vocab = words
        return self._vocab

    def candidates(self, tok: Token, n: int = 5) -> list[str]:
        """Short English candidates, senses matching the token's part of speech first."""
        out: list[str] = []
        for e in self.lookup(tok):
            senses = sorted(e.senses, key=lambda s: not _sense_pos_matches(s[0], tok.pos))
            for _, glosses in senses:
                for g in glosses[:2]:
                    g = clean_gloss(g)
                    if g and g not in out:
                        out.append(g)
        return out[:n]


def _sense_pos_matches(pos: list[str], unidic_pos: str) -> bool:
    hints = POS_HINT.get(unidic_pos, ())
    return any(h in p for p in pos for h in hints)


def _pos_matches(e: Entry, unidic_pos: str) -> bool:
    return any(_sense_pos_matches(p, unidic_pos) for p, _ in e.senses)


def clean_gloss(g: str) -> str:
    g = re.sub(r"\([^)]*\)", "", g)  # "(to be) ", "(esp. ...)"
    g = re.sub(r"^to ", "", g.strip())
    return re.sub(r"\s+", " ", g).strip(" ,;")


def to_display(g: str) -> str:
    """'human being' -> 'HUMAN-BEING' (karaoke style: one uppercase chunk per word)."""
    g = re.sub(r"[^A-Za-z0-9' -]", "", g).strip()
    return re.sub(r"[\s-]+", "-", g).upper()
