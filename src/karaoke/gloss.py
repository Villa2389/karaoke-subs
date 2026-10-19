"""English glosses for karaoke tokens, following docs/glossing-rules.md:
content words -> UPPERCASE English (root only, ending in romaji: EAT-eru),
grammar -> lowercase romaji, names -> Senku, numbers -> digits.

Two glossers share the same token selection and display rules:
- "dict": first JMdict sense matching the part of speech (pure code).
- "llm":  a local model picks/writes the gloss per word, in context.
          Its JSON is validated; anything missing falls back to "dict".
Gloss values: "" = keep in Japanese, "@" = it is a name.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import Counter, defaultdict
from pathlib import Path

from .dictionary import Dictionary, to_display
from .reading import HONORIFIC_PREFIX, Token, hira_to_kata, is_sound, kata_to_romaji

CONTENT_POS = {"名詞", "代名詞", "動詞", "形容詞", "形状詞", "副詞", "連体詞", "接頭辞", "接続詞"}
# Verbs used as grammar after て/で; the reference leaves them as romaji (CHEERING-shi-te-iru).
LIGHT_AFTER_TE = {"居る", "呉れる", "仕舞う", "貰う", "遣る", "置く", "見る", "見せる", "来る", "行く"}
# Suffixes (rule 1.11): grammar-like and honorific ones stay romaji (SCIENCE-teki, ACTIVE-ka, LONG-sa,
# Taiju-kun); plural markers fold into the word before (俺ら -> WE); any other suffix carries
# a meaning of its own and is glossed like a word (市販品 -> STORE-BOUGHT-GOODS, 七百歳 -> 700-YEARS-OLD).
ROMAJI_SUFFIX = {"的", "化", "性", "さ", "み", "げ", "さん", "君", "くん", "ちゃん", "様", "殿", "氏", "姉", "兄"}
PLURAL_SUFFIX = {"等", "達", "共"}  # ら, たち, ども (方 too, after a pronoun: あなた方)
PLURAL = {"I": "WE", "ME": "WE", "YOU": "YOU-ALL", "HE": "THEY", "SHE": "THEY", "HIM": "THEM",
          "HER": "THEM", "IT": "THEY", "THIS": "THESE", "THAT": "THOSE", "GUY": "GUYS"}
OBJECT = {"I": "ME", "WE": "US", "HE": "HIM", "SHE": "HER", "THEY": "THEM"}  # 俺を -> ME o
# Rule 1.7: counters read as English units are plural after a number other than 1 (3 WEEKS).
UNITS = {"YEAR", "MONTH", "WEEK", "DAY", "HOUR", "MINUTE", "SECOND", "TIME", "STEP", "CENTURY",
         "GENERATION", "METER", "MILLIMETER", "CENTIMETER", "KILOMETER", "GRAM", "KILOGRAM", "LITER",
         "PIECE", "BOTTLE", "CUP", "PAGE", "FLOOR", "POINT", "DOLLAR", "PERSON", "MAN", "CHILD"}
IRREGULAR_PLURAL = {"PERSON": "PEOPLE", "MAN": "MEN", "CHILD": "CHILDREN"}
MANY = {"HOW-MANY", "SEVERAL", "MANY", "SOME", "TENS", "HUNDREDS", "THOUSANDS", "A-FEW"}
ERA = {"西暦", "紀元", "紀元前", "令和", "平成", "昭和", "大正", "明治"}
JP_PUNCT = str.maketrans({"！": "!", "？": "?", "、": ",", "。": ".", "「": '"', "」": '"', "『": '"', "』": '"'})
NAME = "@"

# ---------------------------------------------------------------- code-decided glosses

_NUM = {"〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_UNIT = {"十": 10, "百": 100, "千": 1000}
_BIG = {"万": 10**4, "億": 10**8, "兆": 10**12}


def numeral_value(s: str) -> int | None:
    """三百 -> 300, 千百六十四 -> 1164, 億 -> 100000000; None if not a pure numeral."""
    if not s or any(c not in _NUM and c not in _UNIT and c not in _BIG for c in s):
        return None
    total = section = digit = 0
    for c in s:
        if c in _NUM:
            digit = _NUM[c]
        elif c in _UNIT:
            section += (digit or 1) * _UNIT[c]
            digit = 0
        else:
            total += (section + digit or 1) * _BIG[c]
            section = digit = 0
    return total + section + digit


def number_gloss(n: int) -> str:
    """Rule 1.7: digits; round big units read as words (100-MILLION, 10-THOUSAND)."""
    if n < 10_000:
        return str(n)
    for unit, word in ((10**12, "TRILLION"), (10**9, "BILLION"), (10**6, "MILLION"), (10**3, "THOUSAND")):
        if n % unit == 0 and n // unit < 1000:
            return f"{n // unit}-{word}"
    return f"{n:,}"


def name_display(romaji: str) -> str:
    """Rule 1.5: Senku, Taiju, Yuzuriha (first capital, long vowels dropped)."""
    r = re.sub(r"ou", "o", romaji.lower())
    r = re.sub(r"a+|e+|i+|o+|u+", lambda m: m.group()[0], r)
    return r[:1].upper() + r[1:]


def conjugation_split(t: Token) -> tuple[str, str] | None:
    """Rule 1.2: the ending is what changed from the dictionary form, shown in romaji:
    呼ん (呼ぶ) -> ("yo", "n") = CALL-n; 大きかっ (大きい) -> BIG-ka(tta).
    A potential verb is compared with its plain verb: 頼れ (頼る) -> RELY-re, 使え (使う) -> USE-e.
    Dictionary forms and stems are not split (駆け上がる -> DASH-UP, 生き -> LIVE);
    neither are kana-only adjectives (すげえ) or a lone っ (that becomes -tta on the next syllable).
    Kana-only verbs are (かけれ -> POUR-re-ba)."""
    base = t.plain or t.base
    if t.pos not in ("動詞", "形容詞") or not base:
        return None
    k = next((i for i, (a, b) in enumerate(zip(t.surface, base)) if a != b), min(len(t.surface), len(base)))
    stem, ending = t.surface[:k], t.surface[k:]
    if (not stem or (t.pos == "形容詞" and not re.search(r"[一-鿿々]", stem))
            or not re.fullmatch(r"[ぁ-ゖ]+", ending) or ending == "っ"):
        return None
    tail_kata = hira_to_kata(ending)
    if not t.pron.endswith(tail_kata) or len(t.pron) == len(tail_kata):
        return None
    root = kata_to_romaji(t.pron[: len(t.pron) - len(tail_kata)])
    if not root or not t.romaji.startswith(root) or len(t.romaji) == len(root):
        return None
    return root, t.romaji[len(root):]


def is_plural(tokens: list[Token], i: int) -> bool:
    """ら / たち / ども, or 方 after a pronoun (あなた方); 方 after a verb is "way" (やり方)."""
    t = tokens[i]
    return i > 0 and t.pos == "接尾辞" and (
        t.lemma in PLURAL_SUFFIX or (t.lemma == "方" and tokens[i - 1].pos == "代名詞"))


def not_a_name(t: Token) -> bool:
    """Rule 1.5b: a shout cut by a glottal stop (フンッ, 甘っ, あっ) or a single kana (ホ, こ)
    is never a name."""
    return bool(re.fullmatch(r"[ぁ-ゖァ-ヺー]", t.surface)) or t.surface[-1:] in ("っ", "ッ")


def is_katakana(s: str) -> bool:
    return bool(re.fullmatch(r"[ァ-ヺー]+", s))


def _is_loanword(t: Token) -> bool:
    """A katakana common noun: a thing (ヨモギ, エタノール), never a character name."""
    return t.pos == "名詞" and t.pos2 == "普通名詞" and bool(re.fullmatch(r"[ァ-ヺー]+", t.surface))


def is_latin(t: Token) -> bool:
    """English written in the Japanese subtitles (Help!): shown as itself."""
    return bool(re.fullmatch(r"[A-Za-z']+", t.surface))


def is_free_nai(tokens: list[Token], i: int) -> bool:
    """Rule 7.4: the adjective ない standing on its own (わけねえ, じゃねえ, 機材もねえ) is NOT;
    the negative ending glued to a verb (分からん, 死んでない) stays romaji."""
    t = tokens[i]
    prev = tokens[i - 1] if i else None
    return (t.pos == "形容詞" and t.base == "ない" and not t.attach
            and not (prev is not None and prev.surface in ("て", "で")))


def needs_gloss(tokens: list[Token], i: int) -> bool:
    """Does the LLM/dictionary decide this word? (Names, numbers, grammar, sounds: code does.)"""
    t = tokens[i]
    nxt = tokens[i + 1] if i + 1 < len(tokens) else None
    if t.name or t.prefix or not t.romaji or is_sound(t, nxt) or is_latin(t) or is_free_nai(tokens, i):
        return False
    if t.pos == "接頭辞" and t.surface in HONORIFIC_PREFIX:  # not before a word: part of a shout
        return False
    if t.pos == "形容詞" and t.base == "ない":  # bound to て/で: grammar (rule 7.4)
        return False
    if t.pos2 == "数詞" and numeral_value(t.surface) is not None:
        return False
    if t.pos == "接尾辞":
        return t.pos2 in ("名詞的", "形状詞的") and t.lemma not in ROMAJI_SUFFIX and not is_plural(tokens, i)
    if t.pos not in CONTENT_POS or t.lemma == "為る":  # する is grammar: 応援shi
        return False
    prev = tokens[i - 1] if i else None
    if prev is not None and prev.surface in ("て", "で") and prev.pos2 != "格助詞" and t.lemma in LIGHT_AFTER_TE:
        return False  # 〜ている / 〜てくれ / 〜てしまう: auxiliary use
    return True


def _pause_after(tokens: list[Token], i: int) -> bool:
    return bool(tokens[i].trail) or i + 1 >= len(tokens) or tokens[i + 1].start > tokens[i].end


def _numbers(tokens: list[Token]) -> dict[int, int | None]:
    """Rule 1.7: a number split over tokens (百|億, 五千|七百|三十|八) is one value, shown on the
    first token; the others map to None (show nothing)."""
    out: dict[int, int | None] = {}
    i = 0
    while i < len(tokens):
        j = i
        while (tokens[j].pos2 == "数詞" and numeral_value(tokens[j].surface) is not None
               and j + 1 < len(tokens) and tokens[j + 1].pos2 == "数詞"
               and numeral_value(tokens[j + 1].surface) is not None and not _pause_after(tokens, j)):
            j += 1
        if tokens[i].pos2 == "数詞" and (v := numeral_value("".join(t.surface for t in tokens[i:j + 1]))) is not None:
            out[i] = v
            out.update({k: None for k in range(i + 1, j + 1)})
        i = j + 1
    return out


def _is_date(tokens: list[Token], first_number: int) -> bool:
    """十月五日, 西暦五千年: calendar counters stay singular."""
    before = tokens[first_number - 1].surface if first_number else ""
    return before in ERA or any(t.surface == "月" and i and tokens[i - 1].pos2 == "数詞"
                                for i, t in enumerate(tokens))


def pluralize(g: str) -> str:
    """YEAR -> YEARS, PERSON -> PEOPLE, YEAR-OLD -> YEARS-OLD; not a unit (YEN, PERCENT): unchanged."""
    words = g.split("-")
    k = len(words) - 2 if g.endswith("-OLD") and len(words) > 1 else len(words) - 1
    w = words[k]
    if w in IRREGULAR_PLURAL:
        words[k] = IRREGULAR_PLURAL[w]
    elif w in UNITS:
        words[k] = w + "S"
    return "-".join(words)


_E_TO_U = dict(zip("えけげせてねべめれ", "うくぐすつぬぶむる"))


def _merge(tokens: list[Token], i: int, j: int, **fields) -> Token:
    """One token for tokens[i:j], keeping the aligner's timing."""
    part = tokens[i:j]
    t = Token(surface="".join(x.surface for x in part), start=part[0].start, end=part[-1].end,
              pos=part[0].pos, pos2=part[0].pos2, lemma="", pron="".join(x.pron for x in part),
              romaji="".join(x.romaji for x in part), lemma_kana="".join(x.lemma_kana or x.pron for x in part),
              attach=part[0].attach, trail=part[-1].trail, t_start=part[0].t_start, t_end=part[-1].t_end,
              char_times=[c for x in part for c in x.char_times])
    t.lemma = t.base = t.surface
    for k, v in fields.items():
        setattr(t, k, v)
    return t


def merge_compounds(tokens: list[Token], is_compound) -> list[Token]:
    """Rule 2.7: UniDic cuts compounds into short units (市|立, 図書|館). Neighbouring nouns and
    meaning suffixes that the dictionary lists together as one common word, read the same, are
    one word (MUNICIPAL, LIBRARY). Not across a pause, names, numbers (rule 1.7), honorific or
    romaji suffixes (rule 1.11: SCIENCE-teki), or plural suffixes (俺ら -> WE)."""
    def part(t: Token) -> bool:
        if t.name or t.prefix or t.pos2 in ("数詞", "固有名詞"):
            return False
        return t.pos == "名詞" or (t.pos == "接尾辞" and t.lemma not in ROMAJI_SUFFIX | PLURAL_SUFFIX
                                    and t.surface not in ("ら", "たち", "ども", "方"))

    out: list[Token] = []
    i = 0
    while i < len(tokens):
        for j in range(min(len(tokens), i + 4), i + 1, -1):  # longest first, up to 4 pieces
            run = tokens[i:j]
            if (run[0].pos == "名詞" and all(part(t) for t in run)
                    and not any(t.trail for t in run[:-1])  # only the last piece may end in punctuation
                    and all(run[k].start == run[k - 1].end for k in range(1, len(run)))):
                word = "".join(t.surface for t in run)
                if is_compound(word, "".join(t.lemma_kana or t.pron for t in run)):
                    out.append(_merge(tokens, i, j, pos="名詞", pos2="普通名詞"))
                    i = j
                    break
        else:
            out.append(tokens[i])
            i += 1
    return out


def merge_kana_names(lines: list[list[Token]]) -> list[list[Token]]:
    """Rule 1.5: a name written in kana, maybe split and stretched by a shout (つき|しまーっ for
    月島), is that name: same spelling. Names known from kanji (UniDic, furigana, English subs)."""
    known: dict[str, Token] = {}
    for tokens in lines:
        for t in tokens:
            # 3+ kana: shorter keys (王 オー) would catch shouts (おー, おっ)
            if t.name and t.pron and re.search(r"[一-龯]", t.surface):
                for reading in (t.pron, t.lemma_kana):  # said セージ, written セイジ (聖司)
                    if reading and len(_kana_key(reading)) >= 3:
                        known.setdefault(_kana_key(reading), t)
    if not known:
        return lines
    out = []
    for tokens in lines:
        new: list[Token] = []
        i = 0
        while i < len(tokens):
            for j in range(min(len(tokens), i + 4), i, -1):
                run = tokens[i:j]
                if (all(re.fullmatch(r"[ぁ-ゖァ-ヺー]+", t.surface) for t in run) and not any(t.trail for t in run[:-1])
                        and (name := known.get(_kana_key("".join(t.pron or hira_to_kata(t.surface) for t in run)))
                             or known.get(_kana_key("".join(t.surface for t in run))))
                        and (j - i > 1 or not run[0].spelling)):
                    new.append(_merge(tokens, i, j, pos="名詞", pos2="固有名詞", name=True, spelling=name.spelling))
                    i = j
                    break
            else:
                new.append(tokens[i])
                i += 1
        out.append(new)
    return out


def english_titles(lines: list[list[Token]], glosses: list[dict[int, str]], english: list[str],
                   d: Dictionary) -> int:
    """Rule 1.5: a "name" that is a dictionary word, whose meaning the English subtitles write
    capitalised mid-sentence (男爵 -> "The Baron gets bored"), is a title or nickname: translated,
    everywhere in the episode (BARON). Names spelled by the English subs (雫 Shizuku) stay names."""
    found: dict[str, str] = {}
    for tokens, g, en in zip(lines, glosses, english):
        caps = {w for w in re.findall(r"(?<=[a-z,] )[A-Z][a-z]+\b|(?<=[a-z,] ')[A-Z][a-z]+\b", en or "")}
        if not caps:
            continue
        for i, t in enumerate(tokens):
            if ((t.name or g.get(i) == NAME) and not t.spelling and t.pos2 != "固有名詞"
                    and t.surface not in found):
                for c in d.candidates(t):
                    if " " not in c and c.capitalize() in caps:
                        found[t.surface] = to_display(c)
                        break
    n = 0
    for tokens, g in zip(lines, glosses):
        for i, t in enumerate(tokens):
            if t.surface in found and (t.name or g.get(i) in (None, NAME)):
                t.name, g[i] = False, found[t.surface]
                n += 1
    return n


def _kana_key(kata: str) -> str:
    """ツキシマーッ -> ツキシマ: a shout's stretch and stop don't change the name."""
    return re.sub(r"[ーッ]+$", "", hira_to_kata(kata))  # inside a word ー counts: ほしいの is not 星野


def mark_potential(tokens: list[Token], is_word, is_common) -> None:
    """Rule 7.5: a verb whose dictionary form is the potential of a plain verb (頼れる <- 頼る,
    終われる <- 終わる) is glossed as that verb and shows the potential ending (RELY-re).
    Not when the -eru verb is a common word of its own (届ける deliver, 続ける continue)."""
    for t in tokens:
        b = t.base
        if t.pos == "動詞" and len(b) >= 3 and b[-1] == "る" and b[-2] in _E_TO_U:
            plain = b[:-2] + _E_TO_U[b[-2]]
            if is_word(plain) and not is_common(b):
                t.plain = plain


def word_key(t: Token) -> tuple[str, str]:
    """The same word across an episode: dictionary form (the plain verb for a potential) + part of speech."""
    return t.plain or t.base or t.lemma, t.pos


def outvote_names(lines: list[list[Token]], glosses: list[dict[int, str]]) -> int:
    """Rule 1.5: the glosser called a word a name in some lines (今 "Ima", どこ "Doko") but
    translated it in more lines of the episode (NOW, WHERE): a slip, use that translation.
    A word that is a name more often than not (花 Hana) keeps both readings line by line."""
    names: Counter = Counter()
    english: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for toks, g in zip(lines, glosses):
        for i, t in enumerate(toks):
            if g.get(i) == NAME:
                names[word_key(t)] += 1
            elif g.get(i):
                english[word_key(t)][g[i]] += 1
    changed = 0
    for toks, g in zip(lines, glosses):
        for i, t in enumerate(toks):
            k = word_key(t)
            if g.get(i) == NAME and not t.name and sum(english[k].values()) > names[k]:
                g[i] = english[k].most_common(1)[0][0]
                changed += 1
    return changed


def harmonize(lines: list[list[Token]], glosses: list[dict[int, str]]) -> int:
    """Rule 7.1: one gloss per word per episode. Every English gloss of the same dictionary
    form and part of speech becomes the most frequent one (first seen wins a tie); names get
    the most frequent spelling of their surface. Returns how many glosses changed."""
    votes: dict[tuple[str, str], Counter] = defaultdict(Counter)
    spellings: dict[str, Counter] = defaultdict(Counter)
    for toks, g in zip(lines, glosses):
        for i, t in enumerate(toks):
            if g.get(i) not in (None, "", NAME):
                votes[word_key(t)][g[i]] += 1
            if t.name and t.romaji:
                spellings[t.surface][t.spelling or name_display(t.romaji)] += 1
    changed = 0
    for toks, g in zip(lines, glosses):
        for i, t in enumerate(toks):
            if g.get(i) not in (None, "", NAME):
                best = votes[word_key(t)].most_common(1)[0][0]
                changed += best != g[i]
                g[i] = best
            if t.name and t.surface in spellings:
                t.spelling = spellings[t.surface].most_common(1)[0][0]
    return changed


def _number_before(numbers: dict[int, int | None], i: int) -> tuple[int, int] | None:
    """(first token, value) of the number that ends right before token i."""
    k = i - 1
    if k not in numbers:
        return None
    while numbers[k] is None:
        k -= 1
    return k, numbers[k]


def display_tokens(tokens: list[Token], glosses: dict[int, str]) -> list[list[str]]:
    """Syllable texts per token: one part, or two for a split conjugation (["EAT", "-eru"]).

    A trailing geminate (なっ -> nat) moves to the next syllable when no pause comes between
    (BECOME-tta, ru ssee), otherwise it is dropped (フッ フフ -> fu fufu).
    Tokens whose meaning a neighbour already shows display nothing: a plural suffix after a
    translated word (俺ら -> WE, ""), the rest of a split number or English word (What|'|s).
    """
    out: list[list[str]] = []
    carry = ""
    absorbed: set[int] = set()
    numbers = _numbers(tokens)
    for i, t in enumerate(tokens):
        m = re.search(r"[bcdfghjkmpstz]$", t.romaji)
        gem = m.group() if m else ""
        glide = bool(gem) and not _pause_after(tokens, i)
        g = glosses.get(i, "")
        split = conjugation_split(t)
        nxt = next((j for j in range(i + 1, len(tokens)) if not is_plural(tokens, j)), None)
        if i in absorbed:
            parts = [""]
        elif is_latin(t):
            j = i
            while (j + 1 < len(tokens) and is_latin(tokens[j + 1]) and not tokens[j].trail
                   and tokens[j + 1].start == tokens[j].end):
                j += 1
            absorbed.update(range(i + 1, j + 1))
            parts = ["".join(x.surface for x in tokens[i:j + 1]).upper()]
        elif t.name or g == NAME:
            parts = [t.spelling or (name_display(t.romaji) if t.romaji else t.surface)]
        elif i in numbers:
            parts = [number_gloss(numbers[i]) if numbers[i] is not None else ""]
        elif t.prefix:
            parts = [t.romaji]
        elif is_free_nai(tokens, i):
            parts = ["NOT"]
        elif g and (g.replace("-", "").lower() != t.romaji  # "OO" for おお is not a translation
                    or (t.pos == "名詞" and re.fullmatch(r"[ァ-ヺー]+", t.surface))):  # but ゼロ -> ZERO is
            if i + 1 < len(tokens) and is_plural(tokens, i + 1):
                g = PLURAL.get(g, g)
                absorbed.add(i + 1)
            if nxt is not None and tokens[nxt].pos == "助詞" and tokens[nxt].surface in ("を", "に"):
                g = OBJECT.get(g, g)
            number = _number_before(numbers, i)
            if number is not None and number[1] != 1 and not _is_date(tokens, number[0]):
                g = pluralize(g)
            elif number is None and i and glosses.get(i - 1) in MANY:  # 何週間 -> HOW-MANY WEEKS
                g = pluralize(g)
            parts = [g, "-" + (split[1][:-1] if glide else split[1])] if split else [g]
        else:
            parts = [carry + (t.romaji[:-1] if gem else t.romaji)]
        carry = gem if glide else ""
        if t.attach and i and i not in absorbed:
            parts[0] = "-" + parts[0]
        if i and tokens[i - 1].prefix:
            parts[0] = "-" + parts[0]  # o-FATHER
        parts[-1] += t.trail.translate(JP_PUNCT)
        out.append(parts)
    return out



# ---------------------------------------------------------------- dict

def dict_glosses(tokens: list[Token], d: Dictionary) -> dict[int, str]:
    out = {}
    for i in range(len(tokens)):
        if needs_gloss(tokens, i):
            cands = d.candidates(tokens[i], 1)
            out[i] = to_display(cands[0]) if cands else tokens[i].romaji.upper()
    return out


# ---------------------------------------------------------------- llm

SYSTEM = """You write word-for-word English glosses for Japanese anime subtitles used to learn Japanese.
The viewer sees each Japanese word replaced by ONE literal English gloss, in Japanese word order, highlighted as the word is spoken.

Rules for each gloss:
- Literal meaning of THIS word in THIS context, not a smooth translation of the sentence.
- SHORT: the shortest gloss that keeps the meaning. One word preferred, at most two (hyphenated), about 14 letters max.
  市販 -> STORE-BOUGHT (not PUTTING-ON-THE-MARKET), ずっと -> ALWAYS (not THE-WHOLE-TIME).
- Gloss the meaning of the word's root in dictionary form; the program shows the spoken ending itself.
  なった -> BECOME, 聞いて -> LISTEN, 食える -> EAT, 呼んでる -> CALL. No tense, no "to".
- Colloquial forms mean their standard form: ひでえ = ひどい -> TERRIBLE, すげえ = すごい -> AMAZING.
- Pick the sense that fits the scene; the dictionary candidates are hints, not limits. Slang and rough speech get a casual gloss.
- Pronouns: 俺/僕/私 -> I, お前/君 -> YOU (the program writes ME/US before を and に).
- A word marked "potential" (頼れる, 終われる) means "can <verb>": gloss the plain verb (RELY, END); the program shows the ending.
- A noun marked "before する" (説明, カウント, 蒸留) is glossed as an English verb: EXPLAIN, COUNT, DISTILL.
- Counters after a number: the singular English unit (YEAR, WEEK, PERSON); the program makes it plural.
- Conjunctions (しかし, そして, でも) get a gloss: BUT, AND.
- Katakana words for plants, animals, substances and other things are ordinary nouns, never "@":
  give the English word (ヨモギ -> MUGWORT, エタノール -> ETHANOL, ゼロ -> ZERO).
- Use the same gloss for the same word throughout the episode unless its meaning really changes.
- A word marked "plural" is followed by ら/たち/ども: gloss it in plural (俺 -> WE, お前 -> YOU-ALL, 子供 -> CHILDREN).
- A suffix gets its own short gloss, shown glued to the word before it: 市販品: 品 -> GOODS, 高校生: 生 -> STUDENT,
  七百歳: 歳 -> YEARS-OLD, 世界中: 中 -> THROUGHOUT. Answer "=" if it adds nothing in English.
- Names of characters, pets or unique places: answer "@" (the program writes the name itself).
  Not "@" for an ordinary word: a title or nickname that is a dictionary word is translated
  (男爵 -> BARON), and katakana is often just emphasis on an ordinary word (カゲキ = 過激 -> RADICAL,
  ヤダ = いやだ -> NO-WAY). Real countries and cities are translated too (イタリア -> ITALY, 東京 -> TOKYO).
- Interjections, fillers and pure sound words (おお, ああ, ほう, えっ, ハァハァ): answer "=" (kept in Japanese).
- Output UPPERCASE, ASCII only.
Every key in the answer is a word id followed by the Japanese word; write that word's gloss."""


def _key_name(k: int, i: int, t: Token, ascii_keys: bool = False) -> str:
    """"9.0 聞い" (the model sees the word right before its answer), or "L9_0" where
    property names must be ASCII (Claude Code); the word then goes in the description."""
    return f"L{k}_{i}" if ascii_keys else f"{k}.{i} {t.surface}"


def _schema(keys: list[str], words: list[str] | None = None) -> dict:
    """One required property per word, in order: the model sees the word it is
    glossing right before writing it, and can't skip or reorder ids."""
    props = {key: {"type": "string", "maxLength": 40} for key in keys}
    for key, w in zip(keys, words or []):
        props[key]["description"] = w
    return {
        "type": "object",
        "properties": props,
        "required": keys,
        "additionalProperties": False,
    }


def _line_prompt(k: int, line_text: str, tokens: list[Token], targets: list[int], d: Dictionary,
                 english: str = "", ascii_keys: bool = False, glossary: dict | None = None) -> str:
    rows = [f"Line {k}: {line_text}"]
    if english:
        rows.append(f"  Official English subtitle (free translation, a hint only): {english}")
    for i, t in enumerate(tokens):
        if i in targets:
            cands = ", ".join(d.candidates(t, 5)) or "-"
            nxt = tokens[i + 1] if i + 1 < len(tokens) else None
            note = (f"suffix of {tokens[i - 1].surface}" if t.pos == "接尾辞" and i else t.pos)
            if nxt is not None and is_plural(tokens, i + 1):
                note += ", plural"
            if t.plain:
                note += f", potential of {t.plain}"
            if t.pos == "名詞" and nxt is not None and nxt.lemma == "為る":
                note += ", before する"
            word = f" {t.surface}," if ascii_keys else ""
            hint = f"; episode gloss: {_glossary_text(glossary[word_key(t)])}" if glossary and word_key(t) in glossary else ""
            rows.append(f'  "{_key_name(k, i, t, ascii_keys)}":{word} dictionary form {t.plain or t.lemma}, read {t.romaji}, {note}; '
                        f"candidates: {cands}{hint}")
        else:
            rows.append(f"      {t.surface} (grammar, not glossed)")
    return "\n".join(rows)


class LLMGlosser:
    def __init__(self, client, dictionary: Dictionary, cache_path: Path):
        self.client = client
        self.d = dictionary
        self.cache_path = cache_path
        self.cache: dict[str, dict[str, str]] = (
            json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {})
        self.stats = {"llm": 0, "kept": 0, "fallback": 0, "cached": 0}
        self.english: dict[int, str] = {}  # line number -> paired English subtitle
        self.ascii_keys = getattr(client, "ascii_keys", False)
        self.context_lines = 3  # previous lines shown as context
        self.glossary: dict[tuple[str, str], str] = {}  # word_key -> episode gloss (build_glossary)
        self._lock = threading.Lock()  # gloss_batch may run in threads (Claude Code)

    def _key(self, context: str, text: str) -> str:
        # The prompt is part of the key: changing the rules must not reuse old answers.
        return hashlib.sha1(f"{self.client.model}|{SYSTEM}|{context}|{text}".encode()).hexdigest()

    def gloss_batch(self, batch: list[tuple[int, str, list[Token]]], context: list[str]) -> list[dict[int, str]]:
        """batch: (line no, line text, tokens). context: preceding lines' text."""
        results: list[dict[int, str]] = [{} for _ in batch]
        todo = []
        for b, (k, text, tokens) in enumerate(batch):
            targets = [i for i in range(len(tokens)) if needs_gloss(tokens, i)]
            if not targets:
                continue
            key = self._key(context[-1] if context else "", text + "|" + self.english.get(k, ""))
            if self.glossary:  # the hints are part of the prompt, so of the key
                key = self._key(key, "|".join(self.glossary.get(word_key(tokens[i]), "?") for i in targets))
            cached = self.cache.get(key, {})
            if all(str(i) in cached for i in targets) and not any(  # older answers may break rule 7.13
                    cached[str(i)] == NAME and _is_loanword(tokens[i]) for i in targets):
                results[b] = {i: self.cache[key][str(i)] for i in targets}
                self.stats["cached"] += len(targets)
            else:
                todo.append((b, k, text, tokens, targets, key))
        if not todo:
            return results

        got = self._ask(todo, context)
        if not got and len(todo) > 1:  # retry line by line before giving up
            for item in todo:
                got.update(self._ask([item], context))
        for b, k, text, tokens, targets, key in todo:
            entry = {}
            complete = True  # only cache lines the model fully answered
            for i in targets:
                raw = str(got.get(_key_name(k, i, tokens[i], self.ascii_keys), "")).strip()
                if raw == NAME and _is_loanword(tokens[i]):
                    raw = ""  # rule 7.13: ベニテングダケ, ナイタール are things, not names -> dictionary
                if raw in ("=", NAME):
                    entry[i] = "" if raw == "=" else NAME  # keep in Japanese / it's a name
                    self.stats["kept"] += 1
                    continue
                g = to_display(raw)
                if not g or len(g.split("-")) > 4:
                    cands = self.d.candidates(tokens[i], 1)
                    g = to_display(cands[0]) if cands else tokens[i].romaji.upper()
                    self.stats["fallback"] += 1
                    complete = False
                else:
                    self.stats["llm"] += 1
                entry[i] = g
            results[b] = entry
            if complete:
                with self._lock:
                    self.cache[key] = {str(i): g for i, g in entry.items()}
        with self._lock:
            self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False), encoding="utf-8")
        return results

    def _ask(self, todo: list, context: list[str]) -> dict[str, str]:
        keys = [_key_name(k, i, toks[i], self.ascii_keys) for _, k, _, toks, targets, _ in todo for i in targets]
        words = [toks[i].surface for _, _, _, toks, targets, _ in todo for i in targets]
        prompt = "\n\n".join(
            ["Previous lines (context):\n" + ("\n".join(context[-self.context_lines:]) or "-")]
            + [_line_prompt(k, text, toks, targets, self.d, self.english.get(k, ""), self.ascii_keys, self.glossary)
               for _, k, text, toks, targets, _ in todo]
            + (["A word's \"episode gloss\" was chosen once for the whole episode: use it unless this line "
                "uses the word in a different sense."] if self.glossary else [])
            + ["Answer with one gloss per key" + (" (each key's Japanese word is written after it)."
                                                    if self.ascii_keys else ".")])
        try:
            reply = self.client.chat_json(SYSTEM, prompt, _schema(keys, words if self.ascii_keys else None))
            return {k: v for k, v in reply.items() if isinstance(v, str)}
        except Exception as e:  # server error / invalid JSON
            print(f"  llm request failed ({e})")
            return {}


# ---------------------------------------------------------------- glossary (rule 7.14)

GLOSSARY_TASK = """
Now choose ONE default gloss per word for a whole episode. Each key is a word; you see up to three
lines where it is used (with the official English subtitle when there is one). Answer the gloss for
the sense most of those lines use, following every rule above ("=" keep in Japanese, "@" name).
Each key's Japanese word is written after it."""


def _glossary_text(g: str) -> str:
    return "(keep in Japanese)" if g == "" else "(a name)" if g == NAME else g


def build_glossary(client, d: Dictionary, lines: list[tuple[str, list[Token], str]], cache_path: Path,
                   batch: int = 60, jobs: int = 4, examples: int = 3) -> dict[tuple[str, str], str]:
    """Rule 7.14: one default gloss per distinct word of the episode, chosen with up to
    `examples` lines where it is used. lines: (text, tokens, paired English)."""
    from concurrent.futures import ThreadPoolExecutor

    uses: dict[tuple[str, str], list[int]] = {}
    first: dict[tuple[str, str], Token] = {}
    for n, (_, tokens, _) in enumerate(lines):
        for i, t in enumerate(tokens):
            if needs_gloss(tokens, i):
                ns = uses.setdefault(word_key(t), [])
                first.setdefault(word_key(t), t)
                if n not in ns:
                    ns.append(n)
    words = list(uses)
    cache: dict[str, str] = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    lock = threading.Lock()

    def row(w: tuple[str, str]) -> str:
        t = first[w]
        out = [f"{t.surface}, dictionary form {w[0]}, {w[1]}; candidates: {', '.join(d.candidates(t, 5)) or '-'}"]
        for n in uses[w][:examples]:
            text, _, en = lines[n]
            out.append(f"    - {text}" + (f"  | English: {en}" if en else ""))
        return "\n".join(out)

    def ckey(w):
        return hashlib.sha1(f"{client.model}|{SYSTEM}|{GLOSSARY_TASK}|{row(w)}".encode()).hexdigest()

    def ask(part: list[tuple[str, str]]) -> None:
        todo = [w for w in part if ckey(w) not in cache]
        if not todo:
            return
        keys = [f"W{words.index(w)}" for w in todo]
        prompt = "\n\n".join(f'"{k}": {row(w)}' for k, w in zip(keys, todo)) + "\n\nAnswer with one gloss per key."
        try:
            reply = client.chat_json(SYSTEM + "\n" + GLOSSARY_TASK, prompt,
                                     _schema(keys, [first[w].surface for w in todo]))
        except Exception as e:
            print(f"  glossary request failed ({e})")
            return
        with lock:
            for k, w in zip(keys, todo):
                raw = str(reply.get(k, "")).strip()
                if raw == NAME and _is_loanword(first[w]):
                    raw = ""
                g = "" if raw == "=" else NAME if raw == NAME else to_display(raw)
                if raw and (g or raw == "="):
                    cache[ckey(w)] = g

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        list(pool.map(ask, [words[b:b + batch] for b in range(0, len(words), batch)]))
    cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return {w: cache[ckey(w)] for w in words if ckey(w) in cache}
