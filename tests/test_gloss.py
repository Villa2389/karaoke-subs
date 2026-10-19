from karaoke.gloss import (conjugation_split, display_tokens, name_display, needs_gloss,
                           number_gloss, numeral_value)
from karaoke.reading import tokenize
from karaoke.subs import Line, clean_text


def toks(text):
    c, f = clean_text(text)
    return tokenize(Line(0, 0.0, 1.0, text, c, f))


def flat(parts):
    return [p for ps in parts for p in ps]


def test_reference_style_display():
    t = toks("石になった")
    assert flat(display_tokens(t, {0: "STONE", 2: "BECOME"})) == ["STONE", "ni", "BECOME", "-tta"]


def test_conjugated_word_shows_root_gloss_and_changed_ending():
    t = toks("呼んでる")
    assert conjugation_split(t[0]) == ("yo", "n")
    assert flat(display_tokens(t, {0: "CALL"})) == ["CALL", "-n", "-deru"]
    t = toks("大きかった")
    assert flat(display_tokens(t, {0: "BIG"})) == ["BIG", "-ka", "-tta"]


def test_dictionary_forms_and_stems_are_not_split():
    for word, gloss in (("駆け上がる", "DASH-UP"), ("抱きつく", "CLING"), ("伝える", "CONVEY")):
        assert display_tokens(toks(word), {0: gloss}) == [[gloss]]
    t = toks("生きてた")
    assert conjugation_split(t[0]) is None  # 生き is the stem of 生きる: nothing changed
    assert flat(display_tokens(t, {0: "LIVE"})) == ["LIVE", "-te", "-ta"]


def test_grammar_words_are_not_glossed():
    t = toks("応援してる")
    assert [needs_gloss(t, i) for i in range(len(t))] == [True, False, False]


def test_auxiliary_verb_after_te_is_not_glossed():
    t = toks("聞いてくれ")
    assert [x.surface for x in t] == ["聞い", "て", "くれ"]
    assert not needs_gloss(t, 2)


def test_names():
    assert name_display("senkuu") == "Senku"
    assert name_display("taijuu") == "Taiju"
    t = toks("千空(せんくう)！")
    assert not needs_gloss(t, 0)
    assert display_tokens(t, {}) == [["Senku!"]]
    t = toks("杠(ゆずりは)に")
    assert flat(display_tokens(t, {0: "@"})) == ["Yuzuriha", "ni"]


def test_numbers_are_digits():
    assert numeral_value("三百") == 300
    assert numeral_value("千百六十四") == 1164
    assert numeral_value("億") == 10**8
    assert numeral_value("何百") is None
    assert number_gloss(300) == "300" and number_gloss(10**8) == "100-MILLION"
    assert number_gloss(10**4) == "10-THOUSAND"
    t = toks("三百円")
    assert not needs_gloss(t, 0)
    assert flat(display_tokens(t, {1: "YEN"})) == ["300", "YEN"]


def test_honorific_prefix_glued():
    t = toks("お父さん")
    assert not needs_gloss(t, 0)
    assert flat(display_tokens(t, {1: "FATHER"})) == ["o", "-FATHER", "-san"]


def test_meaning_suffixes_are_glossed():
    t = toks("市販品")
    assert needs_gloss(t, 1)
    assert flat(display_tokens(t, {0: "STORE-BOUGHT", 1: "GOODS"})) == ["STORE-BOUGHT", "-GOODS"]
    assert needs_gloss(toks("ナントカ酸だ"), 1)  # 酸 is ACID, not the honorific -san
    t = toks("七百歳")
    assert flat(display_tokens(t, {1: "YEARS-OLD"})) == ["700", "-YEARS-OLD"]


def test_grammar_and_honorific_suffixes_stay_romaji():
    for text, i in (("科学的に", 1), ("活性化", 1), ("長さ", 1), ("大樹(たいじゅ)君", 1), ("お父さん", 2)):
        assert not needs_gloss(toks(text), i)
    assert flat(display_tokens(toks("科学的に"), {0: "SCIENCE"})) == ["SCIENCE", "-teki", "-ni"]


def test_plural_suffix_folds_into_the_word_before():
    t = toks("俺らが")
    assert [needs_gloss(t, i) for i in range(3)] == [True, False, False]
    assert flat(display_tokens(t, {0: "I"})) == ["WE", "", "ga"]  # ら keeps its timing, no text
    assert flat(display_tokens(toks("お前ら！"), {0: "YOU"})) == ["YOU-ALL", "!"]
    assert flat(display_tokens(toks("あなた方"), {0: "YOU"})) == ["YOU-ALL", ""]
    assert flat(display_tokens(toks("子供たち"), {0: "CHILDREN"})) == ["CHILDREN", ""]
    assert needs_gloss(toks("やり方"), 1)  # 方 after a verb is "way", not plural
    t = toks("千空(せんくう)たち")  # a name has no plural gloss: keep the suffix
    assert flat(display_tokens(t, {})) == ["Senku", "-tachi"]


def test_keep_in_japanese_and_geminate_carry():
    assert flat(display_tokens(toks("るっせえな"), {})) == ["ru", "ssee", "-na"]


def test_katakana_slang_is_one_word():
    assert [x.surface for x in toks("このデカブツ")] == ["この", "デカブツ"]


def test_gloss_that_only_spells_the_romaji_stays_japanese():
    assert display_tokens(toks("おお そうか"), {0: "OO"})[0] == ["oo"]


# ---- patterns found by comparing with a reference script (docs/glossing-rules.md, section 7)

def test_no_glue_after_a_pause():
    t = toks("分からん だが")
    assert not t[2].attach  # だ starts a new clause
    assert flat(display_tokens(t, {0: "UNDERSTAND"})) == ["UNDERSTAND", "-ra", "-n", "da", "-ga"]


def test_filler_demonstrative_before_a_noun_is_glossed():
    t = toks("あの暗闇の中で")
    assert t[0].pos == "連体詞" and needs_gloss(t, 0)


def test_free_nai_is_not_and_glued_nai_stays_romaji():
    assert flat(display_tokens(toks("機材もねえ"), {0: "EQUIPMENT"})) == ["EQUIPMENT", "mo", "NOT"]
    assert flat(display_tokens(toks("問題ねえ"), {0: "PROBLEM"})) == ["PROBLEM", "NOT"]
    t = toks("やるじゃねえか")
    assert not needs_gloss(t, 2)
    assert flat(display_tokens(t, {0: "DO"})) == ["DO", "-ja", "NOT", "ka"]
    assert flat(display_tokens(toks("死んでない"), {0: "DIE"})) == ["DIE", "-n", "-de", "-nai"]


def test_sounds_stay_romaji_whatever_their_tag():
    for text in ("ハァハァ", "フッ フフ", "おおおおお！"):
        t = toks(text)
        assert not any(needs_gloss(t, i) for i in range(len(t))), text
    assert flat(display_tokens(toks("フッ フフ"), {})) == ["fu", "fufu"]  # no geminate across a pause
    assert flat(display_tokens(toks("おおおおお！"), {})) == ["o", "o", "ooo!"]  # お is no honorific here


def test_numbers_split_over_tokens_show_one_value():
    assert number_gloss(10**10) == "10-BILLION"
    assert flat(display_tokens(toks("百億％"), {2: "PERCENT"})) == ["10-BILLION", "", "PERCENT"]


def test_counters_are_plural_except_one_and_dates():
    assert flat(display_tokens(toks("三週間"), {1: "WEEK"})) == ["3", "WEEKS"]
    assert flat(display_tokens(toks("一年"), {1: "YEAR"})) == ["1", "YEAR"]
    t = toks("西暦五千七百三十八年")
    assert flat(display_tokens(t, {0: "WESTERN-CALENDAR", 5: "YEAR"}))[-5:] == ["5738", "", "", "", "YEAR"]
    assert flat(display_tokens(toks("十月五日"), {1: "MONTH", 3: "DAY"})) == ["10", "MONTH", "5", "-DAY"]


def test_conjunctions_are_glossed():
    assert needs_gloss(toks("しかし"), 0)


def test_pronoun_object_case():
    assert flat(display_tokens(toks("俺を"), {0: "I"})) == ["ME", "o"]
    assert flat(display_tokens(toks("俺らを"), {0: "I"})) == ["US", "", "o"]
    assert flat(display_tokens(toks("俺は"), {0: "I"})) == ["I", "wa"]


def test_english_words_in_japanese_subs_are_shown_as_english():
    t = toks("What's happening？ Help Help！")
    assert not any(needs_gloss(t, i) for i in range(len(t)))
    assert flat(display_tokens(t, {})) == ["WHAT'S", "", "", "HAPPENING?", "HELP", "HELP!"]


def test_katakana_loanword_equal_to_its_romaji_is_still_english():
    assert flat(display_tokens(toks("ゼロから"), {0: "ZERO"})) == ["ZERO", "kara"]


def test_kana_verb_shows_its_changed_ending():
    assert flat(display_tokens(toks("かければ"), {0: "POUR"})) == ["POUR", "-re", "-ba"]


def test_potential_verb_shows_the_potential_ending():
    from karaoke.gloss import mark_potential
    words = {"頼る", "使う", "届く", "届ける"}
    common = {"届ける"}
    t = toks("頼れん")
    mark_potential(t, lambda w: w in words, lambda w: w in common)
    assert t[0].plain == "頼る"
    assert flat(display_tokens(t, {0: "RELY"})) == ["RELY", "-re", "-n"]
    t = toks("届ける")
    mark_potential(t, lambda w: w in words, lambda w: w in common)
    assert t[0].plain == ""  # its own common word (deliver), not "can reach"


def test_one_gloss_per_word_per_episode():
    from karaoke.gloss import harmonize
    lines = [toks("酒だ"), toks("酒か"), toks("酒さえ")]
    glosses = [{0: "LIQUOR"}, {0: "DRINK"}, {0: "LIQUOR"}]
    harmonize(lines, glosses)
    assert [g[0] for g in glosses] == ["LIQUOR", "LIQUOR", "LIQUOR"]


def test_name_spelling_is_consistent_per_episode():
    from karaoke.gloss import harmonize
    a, b, c = toks("大樹(たいじゅ)君"), toks("大樹(たいじゅ)！"), toks("大樹君")
    b[0].name = True
    c[0].name, c[0].romaji = True, "taiki"
    harmonize([a, b, c], [{}, {}, {}])
    assert display_tokens(c, {})[0] == ["Taiju"]


def test_bound_nai_is_never_sent_to_the_llm():
    t = toks("死んでないぞ")
    assert not needs_gloss(t, 2)
    assert flat(display_tokens(t, {0: "DIE"}))[:3] == ["DIE", "-n", "-de"] and flat(display_tokens(t, {0: "DIE"}))[3] == "nai"


def test_case_particle_de_is_not_the_te_form():
    t = toks("テレビで見た")
    assert needs_gloss(t, 2)  # 見 is SEE here, not the auxiliary 〜てみる
    assert not needs_gloss(toks("カバーしてみせる"), 3)


def test_formal_nouns_tagged_as_final_particles_are_nouns():
    for text, i in (("いるはずだ", 1), ("変わってないかぎり", 3)):
        t = toks(text)
        assert t[i].pos == "名詞" and needs_gloss(t, i), text


def test_english_words_are_never_names():
    t = toks("Help！")
    t[0].name = True  # as the English-subtitle name detector would mark it
    assert flat(display_tokens(t, {})) == ["HELP!"]


def test_llm_cannot_make_a_katakana_thing_a_name(tmp_path):
    from karaoke.gloss import LLMGlosser

    class Fake:
        model = "fake"

        def chat_json(self, system, prompt, schema):
            return {key: "@" for key in schema["required"]}

    class Dict:
        def candidates(self, tok, n=5):
            return ["fly agaric"]

    t = toks("ベニテングダケ")
    g = LLMGlosser(Fake(), Dict(), tmp_path / "cache.json")
    assert g.gloss_batch([(0, "ベニテングダケ", t)], []) == [{0: "FLY-AGARIC"}]


def test_filler_before_an_adjectival_noun_is_a_demonstrative():
    assert toks("あの真面目バカ")[0].pos == "連体詞"


def test_mimetic_adverb_split_before_to_is_a_word_not_a_sound():
    from karaoke.reading import is_sound
    for text in ("ぱっと見", "さっと逃げろ", "こっから先は ちーっと骨が折れんぞ"):
        t = toks(text)
        i = next(k for k, x in enumerate(t) if x.surface.endswith("っ") and t[k + 1].surface == "と")
        assert t[i].pos == "副詞" and not is_sound(t[i], t[i + 1]) and needs_gloss(t, i), text
    t = toks("さっと逃げろ")
    assert flat(display_tokens(t, {0: "QUICKLY", 2: "RUN-AWAY"}))[:2] == ["QUICKLY", "tto"]
    t = toks("フッ フフ")  # a real sound stays a sound
    assert is_sound(t[0], t[1])


def test_compounds_cut_by_unidic_are_one_word():
    from karaoke.gloss import merge_compounds
    known = {("市立", "シリツ"), ("日曜日", "ニチヨウビ")}
    is_compound = lambda w, r: (w, r) in known  # noqa: E731
    t = merge_compounds(toks("市立の学校"), is_compound)
    assert [x.surface for x in t][:2] == ["市立", "の"] and t[0].pos == "名詞"
    t = merge_compounds(toks("科学的"), lambda w, r: True)  # rule 1.11 suffix stays apart
    assert [x.surface for x in t] == ["科学", "的"]


def test_kana_spelling_of_a_known_name_is_that_name():
    from karaoke.gloss import merge_kana_names
    a, b = toks("月島です"), toks("つきしまーっ！")
    a[0].name, a[0].spelling = True, "Tsukishima"
    lines = merge_kana_names([a, b])
    assert lines[1][0].surface == "つきしまーっ" and lines[1][0].name and lines[1][0].spelling == "Tsukishima"
    assert not any(t.name for t in merge_kana_names([a, toks("おーっ")])[1])


def test_title_written_as_a_name_in_english_is_translated():
    from karaoke.gloss import NAME, english_titles

    class Dict:
        def candidates(self, tok, n=5):
            return ["baron", "Irish cobbler"] if tok.surface == "男爵" else []

    lines = [toks("男爵も退屈"), toks("男爵って")]
    glosses = [{0: NAME}, {0: NAME}]
    assert english_titles(lines, glosses, ["The Baron gets bored.", ""], Dict()) == 2
    assert glosses == [{0: "BARON"}, {0: "BARON"}] and not lines[1][0].name


def test_verb_stem_before_chuu_and_retagged_particles():
    t = toks("休み中に")
    assert (t[0].pos, t[0].base) == ("名詞", "休み")
    assert not any(x.name for x in toks("よいせっ と"))


def test_nickname_with_kin_suffix():
    t = toks("みゃー姉 おはよう")  # UniDic: み|ゃー symbols, 姉 read アネ
    assert (t[0].surface, t[0].name, t[0].romaji) == ("みゃー", True, "myaa")
    assert (t[1].pos, t[1].romaji) == ("接尾辞", "nee")
    assert flat(display_tokens(t, {}))[:2] == ["Mya", "-nee"]
    assert not toks("お姉ちゃん")[1].name and toks("お姉ちゃん")[1].pos == "名詞"  # no name before it


def test_shouts_and_single_kana_are_never_names():
    from karaoke.gloss import not_a_name
    assert all(not_a_name(toks(s)[0]) for s in ("フンッ", "ホ"))
    assert not not_a_name(toks("ひなた")[0])


def test_kana_word_with_a_long_vowel_is_not_a_name_without_it():
    from karaoke.gloss import merge_kana_names
    a = toks("星野です")
    a[0].name = True
    assert not any(t.name for t in merge_kana_names([a, toks("ほしいの")])[1])  # ホシーノ is not ホシノ


def test_name_slips_are_outvoted_by_the_translation():
    from karaoke.gloss import NAME, outvote_names
    lines = [toks("今行く"), toks("今だ"), toks("今？"), toks("花が"), toks("花ちゃん"), toks("花ちゃん")]
    g = [{0: "NOW"}, {0: "NOW"}, {0: NAME}, {0: "FLOWER"}, {0: NAME}, {0: NAME}]
    assert outvote_names(lines, g) == 1
    assert g[2] == {0: "NOW"} and g[3] == {0: "FLOWER"} and g[4] == {0: NAME}
