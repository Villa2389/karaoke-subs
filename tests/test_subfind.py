from pathlib import Path

import pytest

from karaoke.subfind import (episode_number, find_subtitles, name_language, same_series, series_subtitles,
                             series_title, text_language)

JA = "1\n00:00:01,000 --> 00:00:02,000\nおはよう、千空。今日も科学の時間だ。\n\n" * 5
ZH = "1\n00:00:01,000 --> 00:00:02,000\n早上好，千空。今天也是科学时间。\n\n" * 5
EN = "1\n00:00:01,000 --> 00:00:02,000\nGood morning, Senku. It's time for the science of the day.\n\n" * 5


def touch(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("name, n", [
    ("[Airota&VCB-Studio] Watashi ni Tenshi ga Maiorita! [03][Ma10p_1080p][x265_flac].mkv", 3),
    ("[SubsPlease] Dr. Stone - 05 (1080p) [A1B2C3D4].mkv", 5),
    ("Dr_Stone_01.mp3", 1),
    ("ep12.ja.srt", 12),
    ("Show.S02E07.1080p.WEB.x264.mkv", 7),
    ("ドクターストーン 第8話.ass", 8),
    ("Mob Psycho 100 - 04v2.mkv", 4),
    ("Whisper of the Heart (1995) [BD 1080p].mkv", None),
])
def test_episode_number(name, n):
    assert episode_number(name) == n


def test_series_title_and_matching():
    assert series_title("[Airota&VCB-Studio] Watashi ni Tenshi ga Maiorita! [03][Ma10p_1080p].mkv") \
        == "Watashi ni Tenshi ga Maiorita!"
    assert series_title("[SubsPlease] Dr. Stone - 05 (1080p).mkv") == "Dr Stone"
    assert same_series("dr_stone", "Dr. Stone")
    assert same_series("wataten", "Watashi ni Tenshi ga Maiorita!")  # abbreviation
    assert same_series("Konosuba", "Kono Subarashii Sekai ni Shukufuku wo!")
    assert not same_series("Dr. Stone", "Watashi ni Tenshi ga Maiorita!")
    assert not same_series("wataten", "Dr. Stone")


def test_language_from_name_and_text():
    assert name_language(Path("ep01.ja.srt")) == "ja"
    assert name_language(Path("Show - 01 [JPN].ass")) == "ja"
    assert name_language(Path("Show - 01.chs.ass")) == "other"
    assert name_language(Path("Show - 01.srt")) is None
    assert text_language(JA) == "ja"
    assert text_language(ZH) == "other"  # kanji without kana is Chinese
    assert text_language(EN) == "en"
    assert text_language("short") is None


def test_same_name_next_to_video_wins_and_chinese_is_skipped(tmp_path):
    media = touch(tmp_path / "[G] Show [01][1080p].mkv")
    touch(tmp_path / "[G] Show [01][1080p].chs.ass", ZH)
    touch(tmp_path / "[G] Show [01][1080p].cht.ass", ZH)
    ja = touch(tmp_path / "[G] Show [01][1080p].ass", JA)  # untagged: the text says Japanese
    touch(tmp_path / "[G] Show [02][1080p].ja.ass", JA)
    found = find_subtitles(media, "ja", [])
    assert found.path == ja and "same name" in found.how
    assert find_subtitles(media, "en", []) is None


def test_episode_in_subs_folder_and_outputs_ignored(tmp_path):
    media = touch(tmp_path / "Show - 03.mkv")
    touch(tmp_path / "Subs" / "Show - 02.ja.srt", JA)
    ja = touch(tmp_path / "Subs" / "Show - 03.ja.srt", JA)
    en = touch(tmp_path / "Subs" / "Show - 03.en.srt", EN)
    touch(tmp_path / "Show - 03.ja.ctc.gloss-llm.ass", JA)  # this program's output, not a source
    assert find_subtitles(media, "ja", []).path == ja
    assert find_subtitles(media, "en", []).path == en


def test_library_folder_named_after_the_series(tmp_path):
    videos = tmp_path / "Downloads" / "[VCB-Studio] Watashi ni Tenshi ga Maiorita! [1080p]"
    media = touch(videos / "[VCB-Studio] Watashi ni Tenshi ga Maiorita! [05][1080p].mkv")
    library = tmp_path / "subs"
    touch(library / "dr_stone" / "ep05.ja.srt", JA)  # same episode number, other series
    ja = touch(library / "wataten" / "subs" / "ep05.ja.srt", JA)
    others = [touch(library / "wataten" / "subs" / f"ep0{n}.ja.srt", JA) for n in (1, 2)]
    found = find_subtitles(media, "ja", [library])
    assert found.path == ja and "library" in found.how
    assert sorted(series_subtitles(ja)) == others


def test_other_series_with_same_episode_number_is_not_taken(tmp_path):
    media = touch(tmp_path / "Dr. Stone - 05.mkv")
    touch(tmp_path / "Other Show - 05.ja.srt", JA)
    assert find_subtitles(media, "ja", []) is None


def test_film_with_one_video_in_its_folder(tmp_path):
    media = touch(tmp_path / "Whisper of the Heart (1995) [BD 1080p].mkv")
    ja = touch(tmp_path / "subtitles" / "Mimi wo Sumaseba.srt", JA)
    assert find_subtitles(media, "ja", []).path == ja
