"""The user's own vocabulary, kept across episodes and series (rule 8).

known_words.txt: words the user knows, one per line as `dictionary form|romaji` (食べる|taberu;
the Japanese may also be the reading, たべる), sorted by the Japanese so it is easy to edit by
hand. Only the Japanese is matched; the romaji is for the reader. Known words are shown in
romaji instead of English.

words.json: every word the program translated, how often it was heard in each episode,
the English it got, and when to ask about it. A word is asked in `karaoke learn` once it was
heard THRESHOLDS[0] times; known -> added to known_words.txt; not known -> asked again after
THRESHOLDS[1] more times, then THRESHOLDS[2] more, and so on (the last step repeats).
Words heard in a series (the series name comes from the episode's file name, see series_of) are
also asked once per series, most often heard first, whatever the count.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
import unicodedata
from bisect import bisect_left
from dataclasses import dataclass
from pathlib import Path

from .gloss import NAME, needs_gloss, word_key
from .paths import USER_DIR
from .reading import Token, tokenize
from .subs import Line, clean_text

THRESHOLDS = [5, 10, 20, 40, 80]
SEP = "|"


def kata_to_hira(s: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)


def word_id(t: Token) -> str:
    return word_key(t)[0]


def romaji_of(word: str) -> str:
    """Romaji of a dictionary form, as the karaoke would show it (食べる -> taberu)."""
    c, f = clean_text(word)
    return "".join(t.romaji for t in tokenize(Line(0, 0.0, 1.0, word, c, f)))


def series_of(episode: str) -> str:
    """Series name from an episode's file name: "[Group] Some Show - 01 [1080p]" -> "Some Show",
    "Dr_Stone_01" -> "Dr Stone"."""
    s = re.sub(r"\[[^\]]*\]", " ", episode).strip()
    s = re.sub(r"\s*[-_]\s*\d+$", "", s)
    return " ".join(s.replace("_", " ").split())


def _word(line: str) -> str:
    return line.split(SEP)[0].strip()


class KnownWords:
    """Known words sorted by their Japanese, searched with binary search."""

    def __init__(self, path: Path):
        self.path = path
        self.lines: list[str] = []  # sorted, as in the file (word|romaji)
        self.words: list[str] = []  # the Japanese of each line, same order
        if path.exists():
            raw = [ln.strip() for ln in path.read_text(encoding="utf-8-sig").splitlines()]
            raw = [ln for ln in raw if ln and not ln.startswith("#")]
            if raw != sorted(raw, key=_word):  # edited by hand out of order: fix it once
                raw.sort(key=_word)
                self._write(raw)
            for ln in raw:
                w = _word(ln)
                if not self.words or self.words[-1] != w:
                    self.lines.append(ln)
                    self.words.append(w)

    def __contains__(self, word: str) -> bool:
        i = bisect_left(self.words, word)
        return i < len(self.words) and self.words[i] == word

    def knows(self, t: Token) -> bool:
        # As written (友だち), its UniDic lemma (友達), or its reading (ともだち).
        return (word_id(t) in self or t.lemma in self
                or (bool(t.lemma_kana) and kata_to_hira(t.lemma_kana) in self))

    def add(self, word: str, romaji: str | None = None, save: bool = True) -> bool:
        """Insert in sorted place; False if already known."""
        if not word or word in self:
            return False
        i = bisect_left(self.words, word)
        self.words.insert(i, word)
        self.lines.insert(i, f"{word}{SEP}{romaji_of(word) if romaji is None else romaji}")
        if save:
            self.save()
        return True

    def save(self) -> None:
        self._write(self.lines)

    def _write(self, lines: list[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("".join(ln + "\n" for ln in lines), encoding="utf-8")


def blank_known(lines: list[list[Token]], glosses: list[dict[int, str]], known: KnownWords) -> int:
    """Known words keep their Japanese (romaji). Returns how many tokens were changed."""
    n = 0
    for toks, g in zip(lines, glosses):
        for i, t in enumerate(toks):
            if g.get(i) not in (None, "", NAME) and known.knows(t):
                g[i] = ""
                n += 1
    return n


@dataclass
class Due:
    word: str
    entry: dict


class WordStats:
    """words.json: {word: {reading, gloss, example, seen: {episode: count}, level, ask_at}}."""

    def __init__(self, path: Path):
        self.path = path
        self.words: dict[str, dict] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def record(self, episode: str, lines: list[list[Token]], texts: list[str],
               glosses: list[dict[int, str]], known: KnownWords) -> int:
        """Counts of every translated word in this episode (a re-run replaces, never adds)."""
        found: dict[str, dict] = {}
        for toks, text, g in zip(lines, texts, glosses):
            for i, t in enumerate(toks):
                if g.get(i) in (None, "", NAME) or not needs_gloss(toks, i) or known.knows(t):
                    continue
                w = word_id(t)
                f = found.setdefault(w, {"n": 0, "gloss": g[i], "example": text,
                                         "reading": kata_to_hira(t.lemma_kana or t.pron)})
                f["n"] += 1
        for w, f in found.items():
            e = self.words.setdefault(w, {"level": 0, "ask_at": THRESHOLDS[0], "seen": {}})
            e.update(reading=f["reading"], gloss=f["gloss"], example=f["example"])
            e["seen"][episode] = f["n"]
        self.save()
        return len(found)

    @staticmethod
    def count(e: dict) -> int:
        return sum(e["seen"].values())

    def due(self, known: KnownWords) -> list[Due]:
        out = [Due(w, e) for w, e in self.words.items() if self.count(e) >= e["ask_at"] and w not in known]
        return sorted(out, key=lambda d: -self.count(d.entry))

    def answer(self, d: Due, knew: bool, known: KnownWords) -> None:
        e = d.entry
        if knew:
            known.add(d.word)
        else:
            e["level"] += 1
            e["ask_at"] = self.count(e) + THRESHOLDS[min(e["level"], len(THRESHOLDS) - 1)]
        e["asked"] = True  # not asked in a series section too
        self.save()

    def series_names(self) -> list[str]:
        return sorted({series_of(ep) for e in self.words.values() for ep in e["seen"]})

    def episodes(self, series: str) -> int:
        return len({ep for e in self.words.values() for ep in e["seen"] if series_of(ep) == series})

    def series_words(self, series: str, known: KnownWords) -> list[tuple[str, int]]:
        """Words heard in this series' episodes, not known and not asked yet (in any question):
        (word, times heard in the series), most often first."""
        out = []
        for w, e in self.words.items():
            n = sum(c for ep, c in e["seen"].items() if series_of(ep) == series)
            if n and not e.get("asked") and w not in known:
                out.append((w, n))
        return sorted(out, key=lambda x: (-x[1], x[0]))

    def answer_series(self, word: str, knew: bool, known: KnownWords) -> None:
        """A word answered in a series section is not asked again, whatever the answer."""
        if knew:
            known.add(word)
        self.words[word]["asked"] = True
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.words, ensure_ascii=False, indent=1), encoding="utf-8")


@dataclass
class SeriesReview:
    series: str
    episodes: int
    unasked: int  # words of the series not asked yet
    words: list[tuple[str, int]]  # the ones to ask now: (word, times heard in this series)


def plan_review(stats: WordStats, known: KnownWords, limit: int) -> tuple[list[Due], list[SeriesReview]]:
    """What `learn` asks: the words heard often (in total), then per series the words heard in it
    that were not asked yet, most often first, `limit` per series (0 = all). A word is asked once:
    in the first list, or in the first series it is among the most often heard in."""
    due = stats.due(known)
    taken = {d.word for d in due}
    plan = []
    for series in stats.series_names():
        words = [(w, n) for w, n in stats.series_words(series, known) if w not in taken]
        todo = words[:limit] if limit else words
        taken.update(w for w, _ in todo)
        plan.append(SeriesReview(series, stats.episodes(series), len(words), todo))
    return due, plan


def open_user(user_dir: Path | None = None) -> tuple[KnownWords, WordStats]:
    d = user_dir or USER_DIR
    return KnownWords(d / "known_words.txt"), WordStats(d / "words.json")


def anki_collection() -> Path:
    """The first Anki profile's collection (<Anki data folder>/Anki2/<profile>/collection.anki2)."""
    bases = [Path(os.environ["APPDATA"])] if os.environ.get("APPDATA") else []
    bases += [Path.home() / "Library" / "Application Support",
              Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")]
    found = sorted(p for b in bases for p in b.glob("Anki2/*/collection.anki2"))
    if not found:
        raise SystemExit("no Anki collection found; pass --anki-collection")
    return found[0]


def anki_words(collection: Path, deck: str, field: str) -> list[str]:
    """Words of the studied cards of `deck` and its subdecks: not new, not suspended or
    buried. `field` holds the words separated by spaces (Jlab's lemma field). Reads a
    copy: an open Anki locks its collection."""
    tmp = Path(tempfile.mkdtemp())
    for suffix in ("", "-wal"):
        if (src := Path(str(collection) + suffix)).exists():
            shutil.copy(src, tmp / ("c.anki2" + suffix))
    db = sqlite3.connect(tmp / "c.anki2")
    db.create_collation("unicase", lambda a, b: (a.lower() > b.lower()) - (a.lower() < b.lower()))
    decks = [i for i, name in db.execute("select id, name from decks")
             if name == deck or name.startswith(deck + "\x1f")]
    if not decks:
        raise SystemExit(f"no Anki deck named {deck!r}")
    words: list[str] = []
    for _, mid, flds in db.execute(
            "select distinct n.id, n.mid, n.flds from cards c join notes n on n.id = c.nid "
            f"where c.did in ({','.join('?' * len(decks))}) and c.type != 0 and c.queue >= 0", decks):
        names = [r[0] for r in db.execute("select name from fields where ntid = ? order by ord", (mid,))]
        if field in names:
            for w in flds.split("\x1f")[names.index(field)].split():
                # Drop invisible marks (U+202A) and punctuation copied into the field.
                w = "".join(c for c in w if unicodedata.category(c)[0] not in "CPZ")
                if w:
                    words.append(w)
    db.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return words
