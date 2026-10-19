"""Write karaoke .ass files with one {\\k} syllable per token."""
from __future__ import annotations

from pathlib import Path

from .align import AlignedLine
from .reading import Token

HEADER = """[Script Info]
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,64,&H0000FFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,40,40,50,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
# Aegisub "Apply karaoke template" uses this line (from gokenshadow's KaraokeTemplates.txt).
TEMPLATE = ("Comment: 0,0:00:00.00,0:00:00.00,Default,,0,0,0,template line,"
            "{\\r\\k$kdur\\3a&HFF&\\2a&HFF&\\4a&HFF&\\t($start,$mid,\\1c&HFFFFFF&\\b50\\bord10.7"
            "\\3a&H00&\\4a&H00&)\\t($mid,$end,\\b0\\bord1)}\n")


def _ts(t: float) -> str:
    cs = max(0, round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def label(tok: Token, mode: str) -> str:
    if mode == "japanese":
        return tok.surface + tok.trail
    from .gloss import JP_PUNCT

    text = tok.romaji or tok.surface
    return ("-" + text if tok.attach else text) + tok.trail.translate(JP_PUNCT)


def _split_time(tok: Token, k: int) -> float:
    """Time the romaji char at index k starts (CTC char timing, else proportional)."""
    if len(tok.char_times) == len(tok.romaji):
        return tok.char_times[k]
    return tok.t_start + (tok.t_end - tok.t_start) * k / max(1, len(tok.romaji))


def line_events(al: AlignedLine, mode: str, labels: list[list[str]] | None = None) -> tuple[float, float, str]:
    """labels: precomputed syllable texts per token (gloss mode), one part or two
    for a split conjugation (["EAT", "-eru"]); otherwise built from `mode`."""
    from .gloss import conjugation_split

    toks = al.tokens
    start = min(al.line.start, toks[0].t_start)
    end = max(al.line.end, toks[-1].t_end)
    sep = "" if mode == "japanese" else " "
    syllables: list[tuple[float, str, bool]] = []  # (start time, text, glued to previous)
    for i, tok in enumerate(toks):
        if labels is None:
            syllables.append((tok.t_start, label(tok, mode), i == 0 or tok.attach))
            continue
        parts = labels[i]
        times = [tok.t_start]
        if len(parts) == 2:
            split = conjugation_split(tok)
            k = len(split[0]) if split else len(tok.romaji) // 2
            times.append(max(tok.t_start, min(_split_time(tok, k), tok.t_end)))
        for t, text in zip(times, parts):
            # Glued: an ending (-tta), a folded plural (""), or punctuation alone ("!").
            syllables.append((t, text, not syllables or not text or text[0] in "-!?,."))
    # Each syllable lasts until the next one starts (pauses belong to the
    # word before them, like hand-timed karaoke). Work in cumulative
    # centiseconds so rounding never drifts.
    marks = [start] + [s[0] for s in syllables] + [end]
    cs = [round((m - start) * 100) for m in marks]
    out = []
    if cs[1] > 0:
        out.append(f"{{\\k{cs[1]}}}")
    for j, (_, text, glued) in enumerate(syllables):
        dur = max(0, cs[j + 2] - cs[j + 1])
        out.append(f"{'' if glued else sep}{{\\k{dur}}}{text}")
    return start, end, "".join(out)


def write_ass(aligned: list[AlignedLine], path: Path, mode: str = "romaji",
              labels: list[list[list[str]]] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(HEADER)
        f.write(TEMPLATE)
        for n, al in enumerate(aligned):
            s, e, text = line_events(al, mode, labels[n] if labels else None)
            f.write(f"Dialogue: 0,{_ts(s)},{_ts(e)},Default,,0,0,0,karaoke,{text}\n")
    return path
