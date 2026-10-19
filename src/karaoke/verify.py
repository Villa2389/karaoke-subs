"""Second look at suspicious LLM glosses (docs/glossing-rules.md, section 4).

Only words the review flags (gloss outside every dictionary meaning, not an
English word, too long) are sent back to the model, one request per line, with
the rules, the context and the dictionary meanings. For each word it writes a
short analysis *first* and then its final gloss, so it reasons before it
commits. The result replaces the gloss; every decision is kept for the review
sheet.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .align import AlignedLine
from .dictionary import Dictionary, to_display
from .gloss import NAME, SYSTEM
from .review import Row

VERIFY_SYSTEM = SYSTEM + """

You are now REVIEWING glosses another pass produced. For each word you get its current gloss and why it was flagged.
Keep the current gloss if it is a correct, short, literal meaning in this context (being outside the dictionary is fine
for slang and context). Replace it if the sense is wrong, it is not an English word, or it is too long.
First write a one-sentence analysis, then the final gloss ("=" keeps the word in Japanese, "@" marks a name)."""

FLAG_REASON = {
    "unusual": "matches none of the dictionary meanings",
    "nonword": "contains a word that is not English (typo or romaji?)",
    "long": "longer than the 2-word / 14-letter budget",
    "number": "number value disagrees with the word",
}


@dataclass
class Decision:
    line: int
    token: int
    before: str
    after: str
    analysis: str


def _schema(keys: list[str], words: list[str] | None = None) -> dict:
    item = {"type": "object",
            "properties": {"analysis": {"type": "string", "maxLength": 240},
                           "gloss": {"type": "string", "maxLength": 30}},
            "required": ["analysis", "gloss"], "additionalProperties": False}
    props = {k: dict(item) for k in keys}
    for k, w in zip(keys, words or []):  # ASCII keys: the word goes in the description
        props[k]["description"] = w
    return {"type": "object", "properties": props, "required": keys, "additionalProperties": False}


SENSE_SLACK = 3  # letters a sense-only swap may add: AWESOME -> TERRIBLE ok, ALWAYS -> CONTINUOUSLY not


def _longer(new: str, old: str) -> bool:
    def letters(g: str) -> int:
        return len(re.sub(r"[^A-Z]", "", g))
    return bool(old) and old != NAME and letters(new) > letters(old) + SENSE_SLACK


class Verifier:
    def __init__(self, client, dictionary: Dictionary, cache_path: Path):
        self.english: dict[int, str] = {}  # position in `aligned` -> paired English subtitle
        self.client = client
        self.d = dictionary
        self.cache_path = cache_path
        self.cache: dict[str, dict] = (
            json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {})

    def _key(self, prompt: str) -> str:
        return hashlib.sha1(f"{self.client.model}|{VERIFY_SYSTEM}|{prompt}".encode()).hexdigest()

    def run(self, aligned: list[AlignedLine], glosses: list[dict[int, str]],
            rows: list[Row]) -> list[Decision]:
        flagged: dict[int, list[Row]] = {}
        for r in rows:
            if r.verdict in FLAG_REASON:
                flagged.setdefault(r.line, []).append(r)
        by_line = {al.line.index: n for n, al in enumerate(aligned)}
        decisions: list[Decision] = []
        for line_no, line_rows in flagged.items():
            n = by_line[line_no]
            al = aligned[n]
            context = "\n".join(a.line.text for a in aligned[max(0, n - 3):n]) or "-"
            words = []
            keys = []
            ascii_keys = getattr(self.client, "ascii_keys", False)  # Claude: property names must be ASCII
            for r in line_rows:
                key = f"T{r.token}" if ascii_keys else f"{r.token} {r.word}"
                keys.append(key)
                words.append(f'  "{key}"{" " + r.word if ascii_keys else ""}: current gloss {r.llm_gloss or "="}; flagged: {FLAG_REASON[r.verdict]}; '
                             f"dictionary: {', '.join(r.candidates[:6]) or '-'}")
            line_view = " ".join(t.surface for t in al.tokens)
            en = self.english.get(n, "")
            en_hint = f"Official English subtitle (free translation, a hint only): {en}\n" if en else ""
            prompt = (f"Previous lines:\n{context}\n\nLine: {al.line.text}\nWords: {line_view}\n{en_hint}\n"
                      + "\n".join(words))
            ck = self._key(prompt)
            if ck not in self.cache:
                try:
                    self.cache[ck] = self.client.chat_json(
                        VERIFY_SYSTEM, prompt, _schema(keys, [r.word for r in line_rows] if ascii_keys else None))
                except Exception as e:
                    print(f"  verify failed for line {line_no} ({e}); keeping glosses")
                    continue
            reply = self.cache[ck]
            for r, key in zip(line_rows, keys):
                ans = reply.get(key) or {}
                raw = str(ans.get("gloss", "")).strip()
                if raw in ("=", NAME):
                    after = "" if raw == "=" else NAME
                else:
                    after = to_display(raw) or r.llm_gloss
                    if r.verdict == "unusual" and _longer(after, r.llm_gloss):
                        # On a sense doubt alone the verifier may not trade a real word for a
                        # longer one (rule 1.1); in Ep 1 those swaps were mostly worse.
                        after = r.llm_gloss
                glosses[n][r.token] = after
                decisions.append(Decision(line_no, r.token, r.llm_gloss, after,
                                          str(ans.get("analysis", "")).strip()))
        self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False), encoding="utf-8")
        return decisions
