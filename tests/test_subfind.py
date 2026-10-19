import io
import json
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

from karaoke.subfind import (Catalog, episode_number, find_online, name_language, same_series, series_online,
                             series_title, text_language)

JA = "1\n00:00:01,000 --> 00:00:02,000\nおはよう、千空。今日も科学の時間だ。\n\n" * 5
ZH = "1\n00:00:01,000 --> 00:00:02,000\n早上好，千空。今天也是科学时间。\n\n" * 5
EN = "1\n00:00:01,000 --> 00:00:02,000\nGood morning, Senku. It's time for the science of the day.\n\n" * 5


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
    assert not same_series("x", "BEYBLADE X")
    assert not same_series("Mimi wo Sumaseba", "180-byou de Kimi no Mimi wo Shiawase ni Dekiru ka")


def test_language_from_name_and_text():
    assert name_language(Path("ep01.ja.srt")) == "ja"
    assert name_language(Path("Show - 01 [JPN].ass")) == "ja"
    assert name_language(Path("Show - 01.chs.ass")) == "other"
    assert name_language(Path("Show - 01.srt")) is None
    assert text_language(JA) == "ja"
    assert text_language(ZH) == "other"  # kanji without kana is Chinese
    assert text_language(EN) == "en"
    assert text_language("short") is None


class FakeCatalog(Catalog):
    """The catalog answered from memory: {(section, folder): {relative path: bytes}}."""

    def __init__(self, folders: dict, cache: Path):
        super().__init__(cache)
        self.folders, self.calls = folders, []

    def _request(self, url: str) -> bytes:
        self.calls.append(url)
        if "/git/trees/" in url:
            tree = unquote(urlparse(url).path.split("/git/trees/main:subtitles/")[1])
            section, _, folder = tree.partition("/")
            if not folder:
                return json.dumps({"tree": [{"path": f, "type": "tree"} for s, f in self.folders if s == section]
                                   }).encode()
            return json.dumps({"tree": [{"path": n, "type": "blob", "size": len(b)}
                                        for n, b in self.folders[(section, folder)].items()]}).encode()
        section, folder, name = unquote(url.split("/main/subtitles/")[1]).split("/", 2)
        return self.folders[(section, folder)][name]


def series(name: str, files: dict, section: str = "anime_tv") -> dict:
    return {(section, name): {n: b.encode() if isinstance(b, str) else b for n, b in files.items()}}


def test_episode_downloaded_from_the_right_series(tmp_path):
    catalog = FakeCatalog(series("Dr. STONE", {"Dr.STONE.S01E03.ja.srt": JA})
                          | series("Dr. STONE. STONE WARS", {"Dr.STONE.S02E03.ja.srt": JA})
                          | series("Watashi ni Tenshi ga Maiorita!", {
                              "[Erai-raws] Watashi ni Tenshi ga Maiorita! - 02 [1080p].srt": JA,
                              "[Erai-raws] Watashi ni Tenshi ga Maiorita! - 03 [1080p].chs.srt": ZH,
                              "[Erai-raws] Watashi ni Tenshi ga Maiorita! - 03 [1080p].srt": JA}), tmp_path / "cache")
    media = tmp_path / "[VCB-Studio] Watashi ni Tenshi ga Maiorita! [03][1080p].mkv"
    found = find_online(media, catalog)
    assert found.path == tmp_path / "cache" / "Watashi ni Tenshi ga Maiorita!" /         "[Erai-raws] Watashi ni Tenshi ga Maiorita! - 03 [1080p].srt"
    assert find_online(tmp_path / "Dr_Stone_03.mkv", catalog).path.name == "Dr.STONE.S01E03.ja.srt"
    calls = len(catalog.calls)
    assert find_online(media, catalog).path == found.path  # second time: listings and file cached
    assert len(catalog.calls) == calls


def test_series_from_the_folder_name_and_season_zip(tmp_path):
    pack = io.BytesIO()
    with zipfile.ZipFile(pack, "w") as z:
        for n in (1, 2, 3):
            z.writestr(f"Show/Some Show - {n:02}.ja.srt", JA)
    catalog = FakeCatalog(series("Some Show", {"Some Show S1 (BD).zip": pack.getvalue()}), tmp_path / "cache")
    media = tmp_path / "Some Show [BD 1080p]" / "Episode 02.mkv"  # no series in the file name
    assert find_online(media, catalog).path.name == "Some Show - 02.ja.srt"


def test_other_episodes_for_names(tmp_path):
    catalog = FakeCatalog(series("Some Show", {f"Some Show - {n:02}.srt": JA for n in (1, 2, 3)}), tmp_path)
    assert [p.name for p in series_online(tmp_path / "Some Show - 02.mkv", catalog)] ==         ["Some Show - 01.srt", "Some Show - 03.srt"]


def test_unknown_series_and_generic_folders(tmp_path):
    catalog = FakeCatalog(series("Downloads Club", {"Downloads Club - 01.srt": JA})
                          | series("BEYBLADE X", {"BEYBLADE X - 01.srt": JA}), tmp_path)
    assert find_online(tmp_path / "Downloads" / "Totally Unknown Show - 01.mkv", catalog) is None
    assert find_online(tmp_path / "x" / "Other Show - 01.mkv", catalog) is None  # one shared letter is not a match
