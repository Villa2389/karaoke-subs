"""Flag words where the LLM gloss is suspicious, for a human to check.

The dictionary is the reference point. Per glossed word:
- agree:    shares a word stem with any dictionary meaning (incl. another sense
            or a near-form: COMPLETELY ~ COMPLETE). Not flagged.
- kept:     LLM left it in Japanese. Not flagged.
- unusual:  matches no dictionary meaning. Often fine (ILLEGAL-MAKE for 密造)
            but this is where wrong senses hide (ひでえ -> AWESOME).      1 point
- nonword:  contains a word that never appears in JMdict's English
            (BOOTHER, HAH-HAH): typo or romaji leaking through.          2 points
- long:     more than 2 words or ~14 letters (rule 1.1: short and literal)
            (PUTTING-ON-THE-MARKET).                                       1 point
- number:   a numeral whose gloss disagrees with the dictionary
            (億 -> BILLION; it is a hundred million).                     3 points

Flagged words go to the verifier (verify.py). Lines are ranked by points and
written to a TSV (opens in Excel/LibreOffice) with the verifier's decision,
its reason and an empty `fix` column. Fill in `fix` and pass the file back with --fixes:
"=" keeps the word in Japanese, anything else replaces the gloss.
"""
from __future__ import annotations

import csv
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .align import AlignedLine
from .dictionary import Dictionary, to_display
from .gloss import NAME, PLURAL, is_plural, needs_gloss
from .reading import Token

STOP = {"THE", "A", "AN", "TO", "OF", "BE", "SOMETHING", "SOMEONE", "IN", "ON", "AT", "FOR"}
COLUMNS = ["score", "line", "time", "word", "reading", "dict", "llm", "verdict",
           "verifier", "verifier_reason", "candidates", "line_text", "fix"]
POINTS = {"agree": 0, "kept": 0, "unusual": 1, "long": 1, "nonword": 2, "number": 3}
MAX_LETTERS = 14


def _words(gloss: str) -> list[str]:
    return [w for w in re.findall(r"[A-Z0-9']+", gloss.upper()) if w not in STOP]


def _near(a: str, b: str) -> bool:
    """Same word up to inflection/derivation: COMPLETE~COMPLETELY, CREATE~CREATING."""
    if a == b:
        return True
    n = min(len(a), len(b))
    common = next((i for i in range(n) if a[i] != b[i]), n)
    return n >= 4 and common >= max(4, n - 3)


# What each numeral kanji must be glossed with (the dictionary's first sense
# for 一 is "BEST", so it can't be the judge here).
NUMBER_WORDS = {
    "一": {"ONE"}, "二": {"TWO"}, "三": {"THREE"}, "四": {"FOUR"}, "五": {"FIVE"},
    "六": {"SIX"}, "七": {"SEVEN"}, "八": {"EIGHT"}, "九": {"NINE"}, "十": {"TEN"},
    "百": {"HUNDRED"}, "千": {"THOUSAND"}, "万": {"TEN", "THOUSAND"}, "億": {"HUNDRED", "MILLION"},
}


def _plain_romaji(s: str) -> str:
    """Spelling-insensitive romaji: taijuu ~ TAIJU, yuuki ~ YUKI, ou ~ o."""
    s = re.sub(r"[^a-z]", "", s.lower())
    s = re.sub(r"ou", "o", s)
    return re.sub(r"a+|e+|i+|o+|u+", lambda m: m.group()[0], s)  # long vowels -> one


def verdict(llm: str, candidates: list[str], tok: Token | None = None,
            vocab: set[str] | None = None) -> str:
    if llm == "":
        return "kept"
    if llm == NAME:
        return "agree"
    mine = _words(llm)
    theirs = [w for c in candidates for w in _words(c)]
    agrees = any(_near(m, t) for m in mine for t in theirs)
    if tok is not None and tok.surface in NUMBER_WORDS:  # single numeral kanji: check the value
        expected = NUMBER_WORDS[tok.surface]
        ok = all(any(_near(e, m) for m in mine) for e in expected)  # HUNDREDS ~ HUNDRED
        return "agree" if ok else "number"
    if tok is not None and tok.romaji and _plain_romaji(llm) == _plain_romaji(tok.romaji):
        return "agree"  # written out as a name (TAIJU, KUSUNOKI): nothing to translate
    if vocab is not None and any(w not in vocab and not w.isdigit() for w in mine):
        return "nonword"
    if len(mine) > 2 or len(re.sub(r"[^A-Z]", "", llm.upper())) > MAX_LETTERS:
        return "long"
    if agrees or not candidates or not mine:
        return "agree"
    return "unusual"


@dataclass
class Row:
    line: int
    time: float
    token: int
    word: str
    reading: str
    dict_gloss: str
    llm_gloss: str
    verdict: str
    candidates: list[str]
    line_text: str
    verified: str | None = None  # verifier's final gloss (None = not checked)
    reason: str = ""


def build_rows(aligned: list[AlignedLine], llm: list[dict[int, str]], d: Dictionary) -> list[Row]:
    vocab = d.english_vocab()
    rows = []
    for al, glosses in zip(aligned, llm):
        for i, tok in enumerate(al.tokens):
            if not needs_gloss(al.tokens, i) or i not in glosses:
                continue
            cands = [to_display(c) for c in d.candidates(tok, 8)]
            if i + 1 < len(al.tokens) and is_plural(al.tokens, i + 1):  # 俺ら: WE is a sense of 俺 here
                cands += [PLURAL[c] for c in cands if c in PLURAL and PLURAL[c] not in cands]
            rows.append(Row(al.line.index, tok.t_start, i, tok.surface, tok.romaji,
                            cands[0] if cands else "", glosses[i],
                            verdict(glosses[i], cands, tok, vocab), cands, al.line.text))
    return rows


def _ts(t: float) -> str:
    m, s = divmod(t, 60)
    return f"{int(m):02d}:{s:05.2f}"


def write_review(rows: list[Row], path: Path, min_score: int = 1) -> tuple[int, int, Path]:
    """Write every word of lines scoring >= min_score, worst lines first.
    Returns (lines flagged, suspicious words in them, path written)."""
    score: dict[int, int] = {}
    for r in rows:
        score[r.line] = score.get(r.line, 0) + POINTS[r.verdict]
    flagged = {ln for ln, s in score.items() if s >= min_score}
    keep = [r for r in rows if r.line in flagged]
    keep.sort(key=lambda r: (-score[r.line], r.line, r.token))
    try:
        f = open(path, "w", encoding="utf-8-sig", newline="")
    except PermissionError:  # open in Excel: Windows locks it
        path = path.with_name(f"{path.stem}.{time.strftime('%H%M%S')}{path.suffix}")
        print(f"  review sheet is open in another program; writing {path.name} instead")
        f = open(path, "w", encoding="utf-8-sig", newline="")
    with f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(COLUMNS)
        for r in keep:
            checked = "" if r.verified is None else (r.verified or "=")
            w.writerow([score[r.line], r.line, _ts(r.time), r.word, r.reading, r.dict_gloss,
                        r.llm_gloss or "=", r.verdict, checked, r.reason,
                        ", ".join(r.candidates), r.line_text, ""])
    return len(flagged), sum(POINTS[r.verdict] > 0 for r in keep), path


def load_fixes(path: Path) -> dict[tuple[int, str], str]:
    """(line, word) -> fix. Keyed by the Japanese word, not the token index, so a
    fix still lands if tokenization of the line changes slightly."""
    fixes = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            fix = (row.get("fix") or "").strip()
            if fix:
                fixes[(int(row["line"]), row["word"])] = fix
    return fixes


def apply_fixes(aligned: list[AlignedLine], glosses: list[dict[int, str]],
                fixes: dict[tuple[int, str], str]) -> int:
    applied = 0
    for al, g in zip(aligned, glosses):
        for i, tok in enumerate(al.tokens):
            fix = fixes.get((al.line.index, tok.surface))
            if fix is not None:
                g[i] = "" if fix == "=" else to_display(fix)
                applied += 1
    return applied
