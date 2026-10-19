"""Arabic numerals -> kanji numerals, so the tokenizer gives the spoken reading."""
from __future__ import annotations

import re

_DIGITS = "〇一二三四五六七八九"
_FULLWIDTH = str.maketrans("０１２３４５６７８９", "0123456789")


def int_to_kanji(n: int) -> str:
    if n == 0:
        return "〇"
    out = []
    for unit_value, unit in ((10**8, "億"), (10**4, "万")):
        if n >= unit_value:
            out.append(int_to_kanji(n // unit_value) + unit)
            n %= unit_value
    for unit_value, unit in ((1000, "千"), (100, "百"), (10, "十")):
        if n >= unit_value:
            d = n // unit_value
            out.append(("" if d == 1 else _DIGITS[d]) + unit)
            n %= unit_value
    if n:
        out.append(_DIGITS[n])
    return "".join(out)


def digits_to_kanji(text: str) -> str:
    """'５年' -> '五年' so the tokenizer reads ゴネン instead of splitting oddly."""
    text = text.translate(_FULLWIDTH)
    text = re.sub(r"(\d+)\s*[%％]", lambda m: m.group(1) + "パーセント", text)
    return re.sub(r"\d+", lambda m: int_to_kanji(int(m.group())), text)
