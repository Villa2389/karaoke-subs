"""Compare a run with a reference ("gold") karaoke script, token by token, grouped by
grammar category, so differences turn into general rules rather than word fixes.

Run:  *.gloss-llm.labels.json (written by `time`): per line, the parts each token shows
      and the token's (surface, pos, pos2, dictionary form).
Gold: one or more JSON lists of {"line": n, "show": [one string per token], "note": ...}.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

PUNCT = "!?,.\"'“”…"


def norm(parts: list[str] | str) -> str:
    """What a token shows, without glue dashes or punctuation: ["CALL", "-n!"] -> "CALL-n"."""
    if isinstance(parts, str):
        parts = [parts]
    return "-".join(p.strip(PUNCT + " ").strip("-") for p in parts if p.strip(PUNCT + " ").strip("-"))


def kind(s: str) -> str:
    """english (has an UPPERCASE word), name (Senku), romaji, or empty."""
    if not s:
        return "empty"
    if re.search(r"(^|-)[A-Z0-9][A-Z0-9']*(?=-|$)", s) and not re.fullmatch(r"[A-Z][a-z]+(-[a-z]+)*", s):
        return "english"
    if re.fullmatch(r"[A-Z][a-z]+(-[a-z]+)*", s):
        return "name"
    return "romaji"


def split(s: str) -> tuple[str, str]:
    """English part and romaji ending: "o-FATHER-san" -> ("FATHER", "o-san"), "BIG-ka" -> ("BIG", "ka")."""
    seg = s.split("-")
    return ("-".join(x for x in seg if not re.search(r"[a-z]", x)),
            "-".join(x for x in seg if re.search(r"[a-z]", x)))


def category(tok: list[str], prev: list[str] | None) -> str:
    surface, pos, pos2, _ = tok
    cat = f"{pos}/{pos2}" if pos2 not in ("*", "") else pos
    if prev is not None and prev[0] in ("て", "で") and pos == "動詞":
        cat += " after te"
    return cat


def compare(run: list[dict], gold: dict[int, dict]) -> dict:
    """Per-token rows + summary. Lines whose token count differs are skipped (reported)."""
    rows, skipped = [], []
    for n, line in enumerate(run):
        g = gold.get(n)
        if g is None:
            continue
        if len(g["show"]) != len(line["tokens"]):
            skipped.append(n)
            continue
        for i, (tok, parts, want) in enumerate(zip(line["tokens"], line["show"], g["show"])):
            have, want = norm(parts), norm(want)
            rows.append({"line": n, "i": i, "surface": tok[0], "dict_form": tok[3],
                         "cat": category(tok, line["tokens"][i - 1] if i else None),
                         "have": have, "want": want, "kh": kind(have), "kw": kind(want),
                         "japanese": line["japanese"]})
    return {"rows": rows, "skipped": skipped}


def _pct(a: int, b: int) -> str:
    return f"{100 * a / b:.0f} %" if b else "-"


def report(res: dict, notes: list[str], max_examples: int = 6) -> str:
    rows = res["rows"]
    out = ["# Program vs reference script", ""]
    n = len(rows)
    same_kind = sum(r["kh"] == r["kw"] for r in rows)
    exact = sum(r["have"].lower() == r["want"].lower() for r in rows)
    both_en = [r for r in rows if r["kh"] == r["kw"] == "english"]
    root_ok = sum(split(r["have"])[0] == split(r["want"])[0] for r in both_en)
    end_ok = sum(split(r["have"])[1] == split(r["want"])[1] for r in both_en)
    out += [f"{n} tokens, {len(res['skipped'])} lines skipped (token count differs).", "",
            "| Measure | Agreement |", "|---|---|",
            f"| Same decision (English / romaji / name / nothing) | {_pct(same_kind, n)} |",
            f"| Exactly the same text | {_pct(exact, n)} |",
            f"| Both English: same English word(s) | {_pct(root_ok, len(both_en))} of {len(both_en)} |",
            f"| Both English: same romaji ending | {_pct(end_ok, len(both_en))} of {len(both_en)} |", ""]

    out += ["## Decision confusion (rows: reference, columns: program)", ""]
    kinds = ["english", "romaji", "name", "empty"]
    conf = Counter((r["kw"], r["kh"]) for r in rows)
    out += ["| ref \\ program | " + " | ".join(kinds) + " |", "|---" * (len(kinds) + 1) + "|"]
    out += [f"| {a} | " + " | ".join(str(conf[(a, b)]) for b in kinds) + " |" for a in kinds]
    out.append("")

    out += ["## Categories by number of differences", "",
            "| Category | Tokens | Wrong decision | Wrong English | Wrong ending |", "|---|---|---|---|---|"]
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_cat[r["cat"]].append(r)

    def faults(rs):
        dec = [r for r in rs if r["kh"] != r["kw"]]
        en = [r for r in rs if r["kh"] == r["kw"] == "english" and split(r["have"])[0] != split(r["want"])[0]]
        end = [r for r in rs if r["kh"] == r["kw"] == "english" and split(r["have"])[0] == split(r["want"])[0]
               and split(r["have"])[1] != split(r["want"])[1]]
        return dec, en, end

    ranked = sorted(by_cat.items(), key=lambda kv: -sum(map(len, faults(kv[1]))))
    for cat, rs in ranked:
        dec, en, end = faults(rs)
        if dec or en or end:
            out.append(f"| {cat} | {len(rs)} | {len(dec)} | {len(en)} | {len(end)} |")
    out.append("")

    out += ["## Examples per category", ""]
    for cat, rs in ranked:
        dec, en, end = faults(rs)
        if not (dec or en or end):
            continue
        out.append(f"### {cat}")
        for label, xs in (("decision", dec), ("english", en), ("ending", end)):
            for r in xs[:max_examples]:
                out.append(f"- {label}: {r['surface']} ({r['dict_form']}) program `{r['have'] or '∅'}` "
                           f"→ ref `{r['want'] or '∅'}` · {r['japanese']}")
        out.append("")

    if notes:
        out += ["## Reference notes (one per line, deduplicated)", ""]
        out += [f"- {x}" for x in sorted(set(notes))]
    return "\n".join(out) + "\n"


def load_gold(paths: list[Path]) -> tuple[dict[int, dict], list[str]]:
    gold, notes = {}, []
    for p in paths:
        for g in json.loads(p.read_text(encoding="utf-8")):
            gold[g["line"]] = g
            notes += [s.strip() for s in re.split(r"(?<=[.;])\s+(?=[A-Z(])", g.get("note") or "") if s.strip()]
    return gold, notes


def main(run_path: Path, gold_paths: list[Path], out: Path) -> Path:
    run = json.loads(run_path.read_text(encoding="utf-8"))
    gold, notes = load_gold(gold_paths)
    res = compare(run, gold)
    out.write_text(report(res, notes), encoding="utf-8")
    out.with_suffix(".rows.json").write_text(json.dumps(res, ensure_ascii=False, indent=0), encoding="utf-8")
    return out
