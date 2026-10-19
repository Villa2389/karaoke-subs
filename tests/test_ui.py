from pathlib import Path

import pytest

from karaoke.reading import tokenize
from karaoke.subs import Line, clean_text
from karaoke.ui import learn_answer, learn_cards, out_dir, preview_args, time_args
from karaoke.vocab import THRESHOLDS, KnownWords, WordStats


@pytest.fixture
def files(tmp_path):
    media, subs = tmp_path / "ep 01.mkv", tmp_path / "ep01.ja.srt"
    media.write_bytes(b"")
    subs.write_text("")
    return media, subs


def test_time_args_match_the_cli(files):
    media, subs = files
    form = {"media": f'"{media}"', "subs": str(subs), "gloss": ["claude", "bogus"],
            "claude_model": "claude-opus-5-5", "claude_effort": "medium", "glossary": True,
            "romaji": True, "review": False, "no_sync": True, "method": "ctc", "lead": "0.05", "out": ""}
    assert time_args(form) == [
        "time", str(media), str(subs), "--gloss", "claude", "--claude-model", "claude-opus-5-5",
        "--claude-effort", "medium", "--glossary", "--romaji", "--no-sync", "--lead", "0.05"]


def test_timing_only_and_missing_files(files):
    media, subs = files
    assert time_args({"media": str(media), "subs": str(subs)}) == ["time", str(media), str(subs)]
    assert time_args({"media": str(media), "subs": ""}) == ["time", str(media)]  # `time` finds them
    with pytest.raises(ValueError):
        time_args({"media": "", "subs": str(subs)})
    with pytest.raises(ValueError):
        time_args({"media": str(media), "subs": str(subs), "en_subs": str(subs) + ".missing"})


def test_glossary_pass_for_any_llm_glosser(files):
    media, subs = files
    assert time_args({"media": str(media), "subs": str(subs), "gloss": ["llm"], "model": "opus",
                      "glossary": True}) == ["time", str(media), str(subs), "--gloss", "llm", "--model", "opus", "--glossary"]
    assert "--glossary" not in time_args({"media": str(media), "subs": str(subs), "gloss": ["dict"], "glossary": True})


def test_out_dir_and_preview(files):
    media, subs = files
    assert out_dir({"media": str(media)}) == Path("work") / "ep 01"
    assert out_dir({"media": str(media), "out": "x"}) == Path("x")
    assert preview_args({"media": str(media), "gloss": str(subs), "romaji": True, "start": "60",
                         "duration": ""}) == ["preview", str(media), str(subs), "--romaji", "--start", "60.0"]


def test_words_section_asks_like_learn(tmp_path):
    def toks(text):
        c, f = clean_text(text)
        return tokenize(Line(0, 0.0, 1.0, text, c, f))

    known = KnownWords(tmp_path / "known_words.txt")
    stats = WordStats(tmp_path / "words.json")
    for ep in range(THRESHOLDS[0]):
        stats.record(f"[G] Show [{ep:02}]", [toks("石だ")], ["石だ"], [{0: "STONE"}], known)
    stats.record("[G] Show [99]", [toks("火だ")], ["火だ"], [{0: "FIRE"}], known)
    data = learn_cards(tmp_path)
    assert [(s["title"], [c["word"] for c in s["cards"]]) for s in data["sections"]] == \
        [("Heard often", ["石"]), ("Show", ["火"])]  # 石 is asked once, in the first section
    assert data["sections"][0]["cards"][0] | {"example": ""} == {
        "kind": "due", "word": "石", "romaji": "ishi", "reading": "いし", "example": "", "gloss": "STONE",
        "heard": THRESHOLDS[0]}
    assert learn_answer({"word": "石", "kind": "due", "knew": True}, tmp_path) == {"ok": True, "again_after": None}
    assert learn_answer({"word": "火", "kind": "series", "knew": False}, tmp_path) == {"ok": True}
    assert "石" in KnownWords(tmp_path / "known_words.txt") and "火" not in KnownWords(tmp_path / "known_words.txt")
    assert [s["cards"] for s in learn_cards(tmp_path)["sections"]] == [[], []]  # nothing left to ask
    with pytest.raises(ValueError):
        learn_answer({"word": "水", "kind": "due", "knew": True}, tmp_path)
