from karaoke.ass_out import line_events
from karaoke.align import AlignedLine
from karaoke.jpnum import digits_to_kanji, int_to_kanji
from karaoke.reading import kata_to_romaji, mora_count, tokenize
from karaoke.subs import Line, clean_text, _propagate_furigana


def line(text: str, start=0.0, end=1.0) -> Line:
    clean, furi = clean_text(text)
    return Line(0, start, end, text, clean, furi)


def test_numbers():
    assert int_to_kanji(5) == "五"
    assert int_to_kanji(1164) == "千百六十四"
    assert int_to_kanji(20000) == "二万"
    assert digits_to_kanji("５年越し") == "五年越し"
    assert digits_to_kanji("100%") == "百パーセント"


def test_clean_removes_labels_and_keeps_furigana():
    text, furi = clean_text("（大樹(たいじゅ)）聞いてくれ 千空(せんくう)！")
    assert text == "聞いてくれ 千空！"
    assert len(furi) == 1
    assert text[furi[0].start:furi[0].end] == "千空"
    assert furi[0].reading == "せんくう"


def test_wave_dash_is_a_long_vowel():
    assert clean_text("ほ～ん")[0] == "ほーん"


def test_romaji():
    assert kata_to_romaji("キョー") == "kyoo"
    assert kata_to_romaji("ワ") == "wa"
    assert kata_to_romaji("ティー") == "tii"
    assert kata_to_romaji("シャ") == "sha"
    assert mora_count("キョー") == 2
    assert mora_count("センクー") == 4


def test_tokenize_pronunciation_and_sokuon():
    toks = tokenize(line("石になった"))
    assert [t.romaji for t in toks] == ["ishi", "ni", "nat", "ta"]
    assert toks[-1].attach  # た glued to the verb for display


def test_particle_pronunciation():
    toks = tokenize(line("人間は全て"))
    assert [t.romaji for t in toks] == ["ningen", "wa", "subete"]


def test_furigana_is_propagated_and_used():
    a, b = line("聞いてくれ 千空(せんくう)！"), line("ありがとう 千空！")
    _propagate_furigana([a, b])
    assert "senkuu" in [t.romaji for t in tokenize(b)]


def test_reading_fix_keeps_words_separate():
    toks = tokenize(line("お母さん"))
    assert [t.romaji for t in toks] == ["o", "kaa", "san"]
    assert toks[0].prefix


def test_colloquial_forms_are_analysed_as_standard_but_said_as_spoken():
    toks = tokenize(line("思いっきしフラれる"))
    assert toks[0].surface == "思いっきし" and toks[0].lemma == "思い切り"
    assert toks[0].romaji == "omoikkishi"
    toks = tokenize(line("アルコールがありゃ"))
    assert "".join(t.romaji for t in toks[2:]) == "ariya"


def test_sentence_final_particle_misread_as_noun():
    toks = tokenize(line("ひでえがな"))
    assert toks[-1].surface == "がな" and toks[-1].pos == "助詞"


def test_english_words_keep_letters():
    assert [t.romaji for t in tokenize(line("Help！"))] == ["help"]


def test_karaoke_durations_sum_to_line():
    ln = line("石になった", 1.0, 2.0)
    toks = tokenize(ln)
    for i, t in enumerate(toks):
        t.t_start = 1.1 + i * 0.2
        t.t_end = t.t_start + 0.2
    start, end, text = line_events(AlignedLine(ln, toks, "ctc", 1.0), "romaji")
    import re
    total = sum(int(k) for k in re.findall(r"\\k(\d+)", text))
    assert round((end - start) * 100) == total
    assert text.startswith("{\\k10}")  # leading silence before the first word


def test_empty_or_punctuation_syllable_is_glued():
    ln = line("お前ら！俺らが", 0.0, 2.0)
    toks = tokenize(ln)
    for i, t in enumerate(toks):
        t.t_start, t.t_end = i * 0.3, i * 0.3 + 0.3
    labels = [["YOU-ALL"], ["!"], ["WE"], [""], ["ga"]]
    _, _, text = line_events(AlignedLine(ln, toks, "ctc", 1.0), "gloss", labels)
    import re
    assert re.sub(r"{[^}]*}", "", text) == "YOU-ALL! WE ga"


def test_creative_ruby_is_not_propagated():
    a, b = line("石の世界(ストーンワールド)へ"), line("世界中の人間")
    _propagate_furigana([a, b])
    assert [t.romaji for t in tokenize(b)][0] == "sekai"
    assert "sutoonwaarudo" in [t.romaji for t in tokenize(a)]  # the ruby line itself keeps it


def test_small_tsu_tagged_as_symbol_stays_in_the_word():
    toks = tokenize(line("るっせえな"))
    assert toks[0].surface == "るっ" and toks[0].trail == ""
    assert "".join(t.romaji for t in toks).startswith("rus")  # っ doubles the next consonant


def test_romaji_uses_ascii_punctuation():
    from karaoke.ass_out import label
    toks = tokenize(line("言う！必ず"))
    assert label(toks[0], "romaji") == "yuu!"


def test_furigana_that_just_reads_ordinary_words_is_not_a_name():
    toks = tokenize(line("夏期集中講座(かきしゅうちゅうこうざ)だって"))
    assert toks[0].surface == "夏期集中講座" and not toks[0].name  # one word, translated
    toks = tokenize(line("市立図書館(としょかん)"))  # the ruby only reads 図書館
    assert [t.surface for t in toks][-1] == "図書館" and not any(t.name for t in toks)
    toks = tokenize(line("二十冊(さつ)読む"))  # the ruby only reads the counter
    assert [t.surface for t in toks][:2] == ["二十", "冊"] and not any(t.name for t in toks)
    toks = tokenize(line("三回戦(せん)突破"))  # 戦 alone is イクサ in the dictionary, セン here
    assert not any(t.name for t in toks)
    toks = tokenize(line("千空(せんくう)！"))  # single kanji read one by one: a name
    assert toks[0].surface == "千空" and toks[0].name and toks[0].ruby


def test_datte_is_not_the_verb_tatsu():
    toks = tokenize(line("だって くれるんだもの"))
    assert (toks[0].surface, toks[0].pos) == ("だって", "接続詞") and toks[1].surface == "くれる"
    assert not any(t.lemma == "立つ" for t in tokenize(line("私だって")))


def test_names_from_other_episodes(tmp_path):
    from karaoke.subs import load_lines
    from karaoke.reading import tokenize
    a, b = tmp_path / "ep1.srt", tmp_path / "ep2.srt"
    a.write_text("1\n00:00:01,000 --> 00:00:02,000\n夏音ちゃん\n", encoding="utf-8")
    b.write_text("1\n00:00:01,000 --> 00:00:02,000\n夏音(かのん)です\n", encoding="utf-8")
    assert tokenize(load_lines(a)[0])[0].surface == "夏"  # alone: 夏|音
    t = tokenize(load_lines(a, [a, b])[0])[0]
    assert (t.surface, t.name, t.romaji) == ("夏音", True, "kanon")
