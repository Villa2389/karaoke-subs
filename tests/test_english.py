from karaoke.align import AlignedLine
from karaoke.english import mark_names, pair
from karaoke.gloss import display_tokens
from karaoke.reading import tokenize
from karaoke.subs import Line, clean_text


def aligned(text, start, end):
    c, f = clean_text(text)
    ln = Line(0, start, end, text, c, f)
    return AlignedLine(ln, tokenize(ln), "ctc", 1.0)


def test_pairing_by_time_overlap():
    jp = [aligned("石になった", 1.0, 3.0), aligned("聞いてくれ", 4.0, 5.0)]
    en = [Line(0, 0.9, 3.2, "", "turned to stone"), Line(1, 3.9, 5.1, "", "Listen to me!")]
    assert pair(jp, en) == ["turned to stone", "Listen to me!"]


def test_one_english_line_can_cover_two_japanese_lines():
    jp = [aligned("その日", 1.0, 2.0), aligned("石になった", 2.1, 3.0)]
    en = [Line(0, 1.0, 3.0, "", "That day, everyone turned to stone.")]
    assert pair(jp, en) == ["That day, everyone turned to stone."] * 2


def test_names_from_english_subtitles():
    jp = [aligned("杠に伝える", 1.0, 3.0)]
    jp[0].tokens[0].romaji = "yuzuriha"  # as the furigana would give it
    assert mark_names(jp, ["I'll tell Yuzuriha"]) == 1
    assert display_tokens(jp[0].tokens, {2: "CONVEY"})[0] == ["Yuzuriha"]


def test_signs_are_not_dialogue(tmp_path):
    import pysubs2
    from karaoke.english import load_english
    subs = pysubs2.SSAFile()
    subs.styles["Main"] = pysubs2.SSAStyle(alignment=pysubs2.Alignment.BOTTOM_CENTER)
    subs.styles["Sign"] = pysubs2.SSAStyle(alignment=pysubs2.Alignment.TOP_CENTER)
    for text, style, name in (("Listen to me!", "Main", ""), ("Weekdays", "Sign", ""),
                              (r"{\pos(340,242)}Hours of", "Main", ""), ("Ethanol", "Main", "Text")):
        subs.append(pysubs2.SSAEvent(start=0, end=1000, text=text, style=style, name=name))
    subs.save(str(tmp_path / "en.ass"))
    assert [ln.text for ln in load_english(tmp_path / "en.ass")] == ["Listen to me!"]
