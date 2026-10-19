"""Japanese text -> word tokens with spoken pronunciation and romaji.

UniDic's `pron` field is the *pronunciation* (は -> ワ, を -> オ, 今日 -> キョー),
which is what the acoustic aligner must hear. Subtitle furigana overrides it.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import fugashi

from .subs import Line

_tagger: fugashi.Tagger | None = None


def tagger() -> fugashi.Tagger:
    global _tagger
    if _tagger is None:
        _tagger = fugashi.Tagger()
    return _tagger


@dataclass
class Token:
    surface: str
    start: int  # char offsets in Line.text
    end: int
    pos: str  # UniDic pos1
    pos2: str
    lemma: str
    pron: str  # katakana pronunciation, "" if unknown
    romaji: str = ""
    base: str = ""  # written dictionary form (なる for なっ)
    lemma_kana: str = ""  # reading of the lemma (ナル)
    name: bool = False  # proper noun: shown as romaji (Senku), never translated
    prefix: bool = False  # honorific お/ご: shown as o- glued to the next word
    ruby: bool = False  # merged by subtitle furigana over several dictionary words
    spelling: str = ""  # name spelling taken from the English subtitles (Senku)
    attach: bool = False  # display glued to previous token (suffix / auxiliary)
    plain: str = ""  # potential verb (頼れる): the plain verb it comes from (頼る)
    trail: str = ""  # punctuation shown after this token
    # Filled by the aligner (seconds, absolute).
    t_start: float = 0.0
    t_end: float = 0.0
    char_times: list[float] = field(default_factory=list)  # start of each romaji char (CTC)


# ---------------------------------------------------------------- romaji

def hira_to_kata(s: str) -> str:
    return "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in s)


_BASE = {
    "ア": "a", "イ": "i", "ウ": "u", "エ": "e", "オ": "o",
    "カ": "ka", "キ": "ki", "ク": "ku", "ケ": "ke", "コ": "ko",
    "サ": "sa", "シ": "shi", "ス": "su", "セ": "se", "ソ": "so",
    "タ": "ta", "チ": "chi", "ツ": "tsu", "テ": "te", "ト": "to",
    "ナ": "na", "ニ": "ni", "ヌ": "nu", "ネ": "ne", "ノ": "no",
    "ハ": "ha", "ヒ": "hi", "フ": "fu", "ヘ": "he", "ホ": "ho",
    "マ": "ma", "ミ": "mi", "ム": "mu", "メ": "me", "モ": "mo",
    "ヤ": "ya", "ユ": "yu", "ヨ": "yo",
    "ラ": "ra", "リ": "ri", "ル": "ru", "レ": "re", "ロ": "ro",
    "ワ": "wa", "ヰ": "i", "ヱ": "e", "ヲ": "o", "ン": "n",
    "ガ": "ga", "ギ": "gi", "グ": "gu", "ゲ": "ge", "ゴ": "go",
    "ザ": "za", "ジ": "ji", "ズ": "zu", "ゼ": "ze", "ゾ": "zo",
    "ダ": "da", "ヂ": "ji", "ヅ": "zu", "デ": "de", "ド": "do",
    "バ": "ba", "ビ": "bi", "ブ": "bu", "ベ": "be", "ボ": "bo",
    "パ": "pa", "ピ": "pi", "プ": "pu", "ペ": "pe", "ポ": "po",
    "ヴ": "vu",
    "ァ": "a", "ィ": "i", "ゥ": "u", "ェ": "e", "ォ": "o",
    "ャ": "ya", "ュ": "yu", "ョ": "yo", "ヮ": "wa", "ヵ": "ka", "ヶ": "ke",
}
_YOON = {"ャ": "a", "ュ": "u", "ョ": "o"}
_SMALL_VOWEL = {"ァ": "a", "ィ": "i", "ゥ": "u", "ェ": "e", "ォ": "o"}
_SPECIAL = {
    "シ": "sh", "チ": "ch", "ジ": "j", "ヂ": "j",
}


def kata_to_romaji(kata: str) -> str:
    """Hepburn-ish romaji. Trailing ッ is kept as 'q' and resolved later."""
    out: list[str] = []
    i = 0
    while i < len(kata):
        c = kata[i]
        nxt = kata[i + 1] if i + 1 < len(kata) else ""
        if c == "ー":
            prev_vowel = next((ch for ch in reversed("".join(out)) if ch in "aiueo"), "")
            out.append(prev_vowel)
        elif c == "ッ":
            out.append("q")  # geminate marker, resolved by _resolve_sokuon
        elif nxt in _YOON and c in _BASE and c not in _YOON:
            stem = _SPECIAL.get(c, _BASE[c][:-1] + "y")
            out.append(stem + _YOON[nxt])
            i += 1
        elif nxt in _SMALL_VOWEL and c in _BASE and c not in _SMALL_VOWEL:
            cons = {"テ": "t", "デ": "d", "フ": "f", "ウ": "w", "ヴ": "v", "ト": "t", "ド": "d"}.get(
                c, _SPECIAL.get(c, _BASE[c][:-1]))
            out.append(cons + _SMALL_VOWEL[nxt])
            i += 1
        elif c in _BASE:
            out.append(_BASE[c])
        i += 1
    return "".join(out)


def _resolve_sokuon(romaji: str, following: str) -> str:
    """'q' + consonant -> doubled consonant; a 'q' before a vowel/end is a glottal stop: drop."""
    def repl(m: re.Match) -> str:
        nxt = m.group(1)
        return ("t" if nxt.startswith("ch") else nxt[0]) + nxt
    romaji = re.sub(r"q([bcdfghjkmnprstvwz]\w*)", repl, romaji)
    if romaji.endswith("q"):
        romaji = romaji[:-1] + (following[0] if following and following[0] not in "aiueonyw" else "")
    return romaji.replace("q", "")


def mora_count(kata: str) -> int:
    return sum(1 for c in kata if c not in "ャュョァィゥェォヮ")


# ---------------------------------------------------------------- tokenize

ATTACH_POS = {"助動詞", "接尾辞"}
ATTACH_POS2 = {("助詞", "接続助詞")}
HONORIFIC_PREFIX = {"お", "ご", "御"}  # shown as o-/go- glued to the next word (rule 1.4)

# Readings UniDic gets wrong in dialogue (it prefers formal readings). Per
# token, so お | 母 | さん stay separate words (rule 2.4).
WORD_READINGS = {"私": "わたし"}
FAMILY_READINGS = {"母": "かあ", "父": "とう", "兄": "にい", "姉": "ねえ"}  # before さん/ちゃん

# Rule 2.1: colloquial spellings the tokenizer splits into nonsense. Replaced by
# a SAME-LENGTH standard spelling for analysis only, so character offsets (and
# therefore audio timing) stay valid; the spoken kana are restored in `pron`.
# (regex on the spoken text, standard spelling of the matched part)
COLLOQUIAL = [
    (r"っきし", "っきり"),          # 思いっきし -> 思いっきり, はっきし
    (r"っぱし", "っぱり"),          # やっぱし -> やっぱり
    (r"(?<=[がもは])ありゃ", "あれば"),  # アルコールがありゃ = if there is
    (r"^ありゃ", "あれは"),          # ありゃ何だ = that is
]


def normalize_for_analysis(text: str) -> tuple[str, list[tuple[int, str, str]]]:
    """Returns (analysis text, [(offset, spoken, standard)]) with len(analysis) == len(text)."""
    changes = []
    for pattern, standard in COLLOQUIAL:
        for m in re.finditer(pattern, text):
            changes.append((m.start(), m.group(), standard))
    out = list(text)
    for pos, spoken, standard in changes:
        out[pos:pos + len(spoken)] = standard
    return "".join(out), changes


def tokenize(line: Line) -> list[Token]:
    analysis, changes = normalize_for_analysis(line.text)
    tokens: list[Token] = []
    offset = 0
    for w in tagger()(analysis):
        start = analysis.index(w.surface, offset)
        end = start + len(w.surface)
        offset = end
        surface = line.text[start:end]  # what is actually written/said
        f = w.feature
        if f.pos1 in ("補助記号", "空白"):
            if tokens and surface in ("っ", "ッ", "ー") and not tokens[-1].trail:
                # Sound, not punctuation: るっせえ -> るっ (glottal stop / long vowel).
                tokens[-1].surface += surface
                tokens[-1].end = end
                tokens[-1].pron += hira_to_kata(surface)
            elif tokens:
                tokens[-1].trail += surface
            continue
        pron = f.pron if f.pron and f.pron != "*" else ""
        if not pron and re.fullmatch(r"[ぁ-ゖァ-ーー]+", surface):
            pron = hira_to_kata(surface)
        if analysis[start:end] != surface:  # rule 2.1: restore the spoken kana
            k = next(i for i, (a, b) in enumerate(zip(analysis[start:end], surface)) if a != b)
            std_tail, said_tail = hira_to_kata(analysis[start + k:end]), hira_to_kata(surface[k:])
            if pron.endswith(std_tail):
                pron = pron[: len(pron) - len(std_tail)] + said_tail
        latin = re.sub(r"[^a-z']", "", surface.lower()) if re.search(r"[A-Za-z]", surface) else ""
        tokens.append(Token(
            surface=surface, start=start, end=end, pos=f.pos1, pos2=f.pos2 or "",
            lemma=f.lemma or surface, pron=pron,
            # Glued to the previous word, unless a pause (space, punctuation) comes between: だが.
            attach=bool(tokens) and (f.pos1 in ATTACH_POS or (f.pos1, f.pos2) in ATTACH_POS2)
            and not tokens[-1].trail and not line.text[start - 1:start].isspace(),
            romaji=latin, base=f.orthBase or surface, lemma_kana=f.lForm or "",
            name=f.pos2 == "固有名詞" and f.pos3 == "人名",  # place names get translated
        ))
    tokens = _merge_datte(_merge_kana_symbols(tokens))
    _fix_readings(tokens)
    _particles_misread_as_nouns(tokens)
    _retag(tokens)
    _kin_after_name(tokens)
    for i, t in enumerate(tokens):
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        t.prefix = (t.pos == "接頭辞" and t.surface in HONORIFIC_PREFIX and nxt is not None
                    and nxt.pos != "接頭辞"  # お|お|おおお is a shout
                    and not is_sound(nxt, tokens[i + 2] if i + 2 < len(tokens) else None))
    tokens = _merge_katakana_nouns(tokens)
    tokens = _apply_furigana(line, tokens)
    for i, t in enumerate(tokens):
        if not t.romaji:  # English words keep their letters (MMS knows English)
            t.romaji = kata_to_romaji(hira_to_kata(t.pron))
    for i, t in enumerate(tokens):
        following = tokens[i + 1].romaji if i + 1 < len(tokens) else ""
        t.romaji = _resolve_sokuon(t.romaji, following)
    return tokens


def _fix_readings(tokens: list[Token]) -> None:
    for i, t in enumerate(tokens):
        nxt = tokens[i + 1].surface if i + 1 < len(tokens) else ""
        if t.surface in WORD_READINGS:
            t.pron = hira_to_kata(WORD_READINGS[t.surface])
        elif t.surface in FAMILY_READINGS and nxt in ("さん", "ちゃん", "様"):
            t.pron = hira_to_kata(FAMILY_READINGS[t.surface])


def _particles_misread_as_nouns(tokens: list[Token]) -> None:
    """Rule 2.3: a short hiragana "noun" whose dictionary form is kanji, right
    after a predicate at the end of the line, is a sentence-final particle
    (ひでえ|がな was read as 仮名 "kana")."""
    for i, t in enumerate(tokens):
        last = i == len(tokens) - 1 or all(x.pos in ("助詞", "助動詞") for x in tokens[i + 1:])
        prev = tokens[i - 1] if i else None
        if (last and prev is not None and t.pos == "名詞" and len(t.surface) <= 3
                and re.fullmatch(r"[ぁ-ゖ]+", t.surface) and t.lemma != t.surface
                and prev.pos in ("形容詞", "動詞", "助動詞")):
            t.pos, t.pos2, t.lemma, t.name = "助詞", "終助詞", t.surface, False  # よい|せっ: not a name


def _retag(tokens: list[Token]) -> None:
    """Rule 7.2: tags UniDic gets wrong in dialogue.
    - filler あの/その/この right before a noun is the demonstrative (あの日 = THAT DAY);
    - ねえ/ない tagged as a sentence-final particle right after a noun or a topic particle
      is the adjective ない (機材もねえ = EQUIPMENT mo NOT);
    - formal nouns tagged as final particles (いる|はず|だ, ない|かぎり) are nouns (SHOULD, AS-LONG-AS);
    - a kana "symbol" split before と (ちーっ|と) is a mimetic adverb;
    - a verb stem before 中 is a noun (休み|中 = HOLIDAY -DURING, not HOLIDAY-mi)."""
    for i, t in enumerate(tokens):
        prev = tokens[i - 1] if i else None
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if (t.pos == "感動詞" and t.surface in ("あの", "その", "この") and nxt is not None
                and nxt.pos in ("名詞", "代名詞", "接頭辞", "形状詞")):  # あの真面目バカ
            t.pos, t.pos2 = "連体詞", "*"
        elif (t.pos == "助詞" and t.surface in ("ねえ", "ない", "ねー") and prev is not None
              and (prev.pos in ("名詞", "代名詞") or prev.surface in ("も", "は", "が"))):
            t.pos, t.pos2, t.lemma, t.base, t.attach = "形容詞", "非自立可能", "無い", "ない", False
        elif t.pos == "助詞" and t.surface in FORMAL_NOUNS:
            t.pos, t.pos2 = "名詞", "普通名詞"
        elif t.pos == "記号" and mimetic_before_to(t, nxt):  # ちーっ|と tagged as a symbol
            t.pos, t.pos2 = "副詞", "*"
        elif (t.pos == "動詞" and nxt is not None and nxt.pos == "接尾辞" and nxt.lemma == "中"
              and t.surface == t.base[:-1] + _I_STEM.get(t.base[-1:], "")):
            t.pos, t.pos2, t.base, t.lemma = "名詞", "普通名詞", t.surface, t.surface  # 休み|中


# Last kana of a dictionary-form verb -> its stem (連用形) ending: 休む -> 休み.
_I_STEM = dict(zip("うくぐすつぬぶむるる", "いきぎしちにびみりり"))
FORMAL_NOUNS = {"はず", "わけ", "かぎり", "限り"}
SOUND_MARKS = set("ァィゥェォぁぃぅぇぉッっー")


def mimetic_before_to(t: Token, nxt: Token | None) -> bool:
    """ぱっ|と, さっ|と, ちーっ|と: a mimetic adverb the tokenizer split before と (no pause)."""
    return (nxt is not None and nxt.surface == "と" and nxt.start == t.end and not t.trail
            and t.surface[-1:] in ("っ", "ッ") and bool(re.fullmatch(r"[ぁ-ゖァ-ヺー]+", t.surface)))


def is_sound(t: Token, nxt: Token | None = None) -> bool:
    """Rule 7.3: a laugh, pant or shout, whatever UniDic tagged it (ハァハァ, フッ, おおお):
    kana only, and an interjection, or ending in a glottal stop, or one kana repeated (おおお),
    or (not a noun) containing a small vowel or a long mark (ハァハァ).
    Not the first half of a mimetic adverb split before と (ちーっ|と = a little): `nxt` is
    the following token."""
    s = t.surface
    if not re.fullmatch(r"[ぁ-ゖァ-ヺー]+", s) or t.name:
        return False
    if t.pos == "感動詞":
        return True
    if mimetic_before_to(t, nxt):
        return False
    if t.pos not in ("副詞", "名詞", "動詞", "形容詞", "接頭辞"):
        return False
    stem = t.pos in ("動詞", "形容詞")  # なっ|た: a glottal stop before た is conjugation
    if (s[-1] in "ッっ" and not stem) or (len(s) > 2 and len(set(s)) == 1):
        return True
    # Loanword nouns have long marks and small vowels too (ゲームオーバー, ティー).
    return t.pos != "名詞" and bool(set(s) & (SOUND_MARKS - set("ッっ")))


def _is_katakana(s: str) -> bool:
    return bool(re.fullmatch(r"[ァ-ヺー]+", s))


def _merge_katakana_nouns(tokens: list[Token]) -> list[Token]:
    """UniDic splits katakana slang/compounds: デカ|ブツ (big oaf) -> デカブツ."""
    out: list[Token] = []
    for t in tokens:
        p = out[-1] if out else None
        if (p is not None and not p.trail and p.pos == t.pos == "名詞" and not (p.name or t.name)
                and _is_katakana(p.surface) and _is_katakana(t.surface)):
            p.surface += t.surface
            p.end = t.end
            p.pron += t.pron
            p.lemma = p.base = p.surface
            p.lemma_kana = p.pron
            p.trail = t.trail
            continue
        out.append(t)
    return out


def dictionary_reading(text: str) -> str:
    """Katakana reading the dictionary gives `text` (word by word)."""
    return hira_to_kata("".join(w.feature.kana or "" for w in tagger()(text)))


def _merge_kana_symbols(tokens: list[Token]) -> list[Token]:
    """UniDic breaks words it doesn't know that start with a small kana into "symbols"
    (みゃー -> み|ゃー): a run of kana symbols with no pause between is one word."""
    out: list[Token] = []
    for t in tokens:
        p = out[-1] if out else None
        if (p is not None and t.pos == p.pos == "記号" and not p.trail and p.end == t.start
                and re.fullmatch(r"[ぁ-ゖァ-ヺー]+", p.surface + t.surface)):
            p.surface, p.end, p.trail = p.surface + t.surface, t.end, t.trail
            p.pron = hira_to_kata(p.surface)  # ゃ alone was read ヤ
            p.lemma = p.base = p.surface
            continue
        out.append(t)
    return out


KIN = {"姉": "ネエ", "兄": "ニイ"}  # after a name: みゃー姉 = Myaa-nee (read ねえ, not あね)


def _kin_after_name(tokens: list[Token]) -> None:
    """Rule 1.4b: 姉/兄 glued to a name or a kana nickname (みゃー姉, 花姉) is the address
    -nee/-nii, a suffix like -san; the word before it is a name."""
    for i, t in enumerate(tokens[1:], 1):
        p = tokens[i - 1]
        if (t.surface in KIN and t.pos == "名詞" and p.end == t.start and not p.trail
                and (p.name or (p.pos in ("名詞", "記号") and re.fullmatch(r"[ぁ-ゖァ-ヺー]+", p.surface)))):
            t.pos, t.pos2, t.pron, t.attach = "接尾辞", "名詞的", KIN[t.surface], True
            p.name, p.pos, p.pos2 = True, "名詞", "固有名詞"


def _merge_datte(tokens: list[Token]) -> list[Token]:
    """UniDic reads だって as 立つ (だっ) + て. It is the conjunction BUT/BECAUSE at the start
    of a sentence, else the particle 'even' (私だって)."""
    out: list[Token] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if (t.surface == "だっ" and t.lemma == "立つ" and nxt is not None and nxt.surface == "て"
                and nxt.start == t.end and not t.trail):
            starts = not out or bool(out[-1].trail) or not t.attach and out[-1].end < t.start
            out.append(Token(
                surface="だって", start=t.start, end=nxt.end,
                pos="接続詞" if starts else "助詞", pos2="*" if starts else "副助詞",
                lemma="だって", pron="ダッテ", base="だって", lemma_kana="ダッテ",
                attach=bool(out) and not starts, trail=nxt.trail))
            i += 2
            continue
        out.append(t)
        i += 1
    return out


def unvoiced(kata: str) -> str:
    """ビ -> ヒ: compare readings across rendaku (開放日 かいほうび, dictionary カイホウヒ)."""
    return "".join(c for c in unicodedata.normalize("NFD", kata) if c not in "゙゚")


def _reading_aid(inside: list[Token], reading: str) -> int | None:
    """Furigana that just reads ordinary words, not a name: the index in `inside` where the read
    part starts (0: the whole span, 図書|館(としょかん) → one word LIBRARY; >0: the subtitle's ruby
    only covered the tail, 二十|冊(さつ), 市|立|図書|館(としょかん)). None: a name (千|空 → Senku)."""
    want = unvoiced(hira_to_kata(reading))
    for k in range(len(inside)):
        tail = inside[k:]
        # Whole span: only if the dictionary found real words in it (図書|館, 夏期|集中|講座); a name
        # splits into single kanji read one by one (千|空 = センクウ: same reading, still a name).
        if ((k or any(len(t.surface) > 1 for t in tail))
                and want == unvoiced(dictionary_reading("".join(t.surface for t in tail)))):
            return k
        # Ruby over the tail only: out of context the dictionary may read a kanji otherwise
        # (戦 イクサ), so also try the reading it got in the sentence (三回戦(せん)). Not for the
        # whole span: a name is exactly kanji read one by one (千空(せんくう) = セン+クウ).
        if k and want == unvoiced("".join(t.pron for t in tail)):
            return k
    return None


def _apply_furigana(line: Line, tokens: list[Token]) -> list[Token]:
    """Merge tokens covered by a furigana span into one token with that reading."""
    for fu in line.furigana:
        inside = [t for t in tokens if t.start >= fu.start and t.end <= fu.end]
        straddle = [t for t in tokens if t.start < fu.end and t.end > fu.start and t not in inside]
        if not inside or straddle:
            continue
        aid = _reading_aid(inside, fu.reading) if len(inside) > 1 else None
        if aid == 0 and any(t.name for t in inside):
            continue  # reads a name + suffix (聖司|君, 天沢|医院): the tokens already say it all
        if aid:  # the ruby reads only the tail: merge that part, the rest stays as it is
            inside = inside[aid:]
            if len(inside) == 1:
                continue
        first = inside[0]
        span = line.text[first.start:inside[-1].end]
        merged = Token(
            surface=span, start=first.start, end=inside[-1].end,
            pos=first.pos, pos2=first.pos2, lemma=span,
            pron=hira_to_kata(fu.reading), attach=first.attach, trail=inside[-1].trail,
            base=span, lemma_kana=hira_to_kata(fu.reading),
            # Subtitle furigana over something the dictionary splits apart (千+空) is a name,
            # unless it just reads ordinary words (図書館: one word, translated).
            name=any(t.name for t in inside) or (len(inside) > 1 and aid is None), ruby=len(inside) > 1,
        )
        idx = tokens.index(first)
        tokens = tokens[:idx] + [merged] + tokens[idx + len(inside):]
    return tokens
