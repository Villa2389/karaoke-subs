import argparse

from karaoke.cli import cmd_learn
from karaoke.gloss import display_tokens
from karaoke.reading import tokenize
from karaoke.subs import Line, clean_text
from karaoke.vocab import THRESHOLDS, KnownWords, WordStats, blank_known, plan_review, series_of


def toks(text):
    c, f = clean_text(text)
    return tokenize(Line(0, 0.0, 1.0, text, c, f))


def test_known_words_file_is_sorted_and_searched(tmp_path):
    p = tmp_path / "known_words.txt"
    p.write_text("食べる|taberu\n# comment\n\n石|ishi\n", encoding="utf-8")
    k = KnownWords(p)
    assert k.words == sorted(["石", "食べる"])
    assert p.read_text(encoding="utf-8").splitlines() == ["石|ishi", "食べる|taberu"]  # by the Japanese
    assert "石" in k and "食べ" not in k
    k.add("火")
    assert k.words == sorted(["石", "食べる", "火"]) and "火" in KnownWords(p)
    assert "火|hi" in p.read_text(encoding="utf-8").splitlines()


def test_known_word_shows_romaji_and_is_matched_by_reading(tmp_path):
    p = tmp_path / "known_words.txt"
    p.write_text("いし\n", encoding="utf-8")
    t = toks("石になった")
    g = [{0: "STONE", 2: "BECOME"}]
    assert blank_known([t], g, KnownWords(p)) == 1
    assert [x[0] for x in display_tokens(t, g[0])] == ["ishi", "ni", "BECOME", "-tta"]
    p.write_text("友達\n", encoding="utf-8")
    assert KnownWords(p).knows(toks("友だち")[0])  # other spelling of the same lemma


def test_word_is_asked_after_threshold_and_again_later_when_unknown(tmp_path):
    known = KnownWords(tmp_path / "known_words.txt")
    stats = WordStats(tmp_path / "words.json")
    line = toks("石だ")
    for ep in range(THRESHOLDS[0]):
        assert not stats.due(known)
        stats.record(f"ep{ep}", [line], ["石だ"], [{0: "STONE"}], known)
    stats.record("ep0", [line], ["石だ"], [{0: "STONE"}], known)  # re-run: not counted twice
    [d] = stats.due(known)
    assert d.word == "石" and stats.count(d.entry) == THRESHOLDS[0] and d.entry["gloss"] == "STONE"
    stats.answer(d, False, known)
    assert not WordStats(tmp_path / "words.json").due(known)
    assert d.entry["ask_at"] == THRESHOLDS[0] + THRESHOLDS[1]
    for ep in range(THRESHOLDS[1]):
        stats.record(f"later{ep}", [line], ["石だ"], [{0: "STONE"}], known)
    [d] = stats.due(known)
    stats.answer(d, True, known)
    assert "石" in KnownWords(tmp_path / "known_words.txt") and not stats.due(known)


def test_series_name_comes_from_the_file_name():
    assert series_of("[Airota&VCB-Studio] Watashi ni Tenshi ga Maiorita! [13(OVA)][Ma10p_1080p]") \
        == "Watashi ni Tenshi ga Maiorita!"
    assert series_of("Dr_Stone_01") == "Dr Stone"
    assert series_of("[Group] Some Show - 02 [1080p]") == "Some Show"


def test_series_words_are_asked_once_per_series(tmp_path):
    known = KnownWords(tmp_path / "known_words.txt")
    stats = WordStats(tmp_path / "words.json")
    stats.record("[G] Show [01]", [toks("石だ")], ["石だ"], [{0: "STONE"}], known)
    stats.record("[G] Show [02]", [toks("石だ")], ["石だ"], [{0: "STONE"}], known)
    stats.record("[G] Other [01]", [toks("火だ")], ["火だ"], [{0: "FIRE"}], known)
    assert stats.series_names() == ["Other", "Show"] and stats.episodes("Show") == 2
    assert stats.series_words("Show", known) == [("石", 2)]
    stats.answer_series("石", False, known)
    assert stats.series_words("Show", known) == [] and stats.series_words("Other", known) == [("火", 1)]
    assert not WordStats(tmp_path / "words.json").series_words("Show", known)  # saved


def test_learn_asks_heard_words_then_series_words(tmp_path, monkeypatch, capsys):
    known = KnownWords(tmp_path / "known_words.txt")
    stats = WordStats(tmp_path / "words.json")
    for ep in range(THRESHOLDS[0]):
        stats.record(f"[G] Show [{ep:02}]", [toks("石だ")], ["石だ"], [{0: "STONE"}], known)
    stats.record("[G] Show [99]", [toks("火だ")], ["火だ"], [{0: "FIRE"}], known)
    answers = iter(["", "y", "", "n"])  # 石: meaning, knew it; 火 (series section): meaning, not known
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    cmd_learn(argparse.Namespace(list=False, limit=30, import_anki=None, anki_field="Jlab-Lemma",
                                 anki_collection=None, user_dir=str(tmp_path)))
    out = capsys.readouterr().out
    assert "== Heard often: 1 words ==" in out and "[1/1]  石  ishi" in out
    assert "== Show: 6 episodes, 1 words not asked yet, the 1 most often heard ==" in out  # 石 is known now
    assert "[1/1]  火  hi" in out and "heard 1x in this series" in out
    assert "石|ishi" in (tmp_path / "known_words.txt").read_text(encoding="utf-8").splitlines()
    assert WordStats(tmp_path / "words.json").words["火"]["asked"] is True


def test_a_word_is_asked_once_across_series(tmp_path):
    known = KnownWords(tmp_path / "known_words.txt")
    stats = WordStats(tmp_path / "words.json")
    stats.record("[G] Aaa [01]", [toks("火だ")], ["火だ"], [{0: "FIRE"}], known)
    stats.record("[G] Bbb [01]", [toks("火だ")], ["火だ"], [{0: "FIRE"}], known)
    due, plan = plan_review(stats, known, 30)
    assert due == [] and [(s.series, s.words) for s in plan] == [("Aaa", [("火", 1)]), ("Bbb", [])]

