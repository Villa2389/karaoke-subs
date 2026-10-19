"""Find the Japanese subtitles of an episode on the internet, no account or key needed.

Source: the Ajatt-Tools Japanese subtitle catalog on GitHub (github.com/Ajatt-Tools/kitsunekko-mirror:
volunteer uploads plus a mirror of kitsunekko.net, updated every few hours), one folder per series.
The series is taken from the video's file name, or its folder name when the file name only has the
episode ("Some Show [1080p]/Episode 02.mkv"), and matched with the catalog's folder names; the
episode number comes from the file name. Downloads are kept in ~/.karaoke-subs/subtitles/, so a
re-run needs no network.

When nothing is found online, a text subtitle track inside the video (mkv) is used.

A downloaded file must really be Japanese: the language comes from its name (`.ja.`, `.chs.`)
or from its text (Japanese has kana; Chinese has kanji without kana).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

CATALOG_API = "https://api.github.com/repos/Ajatt-Tools/kitsunekko-mirror"
CATALOG_RAW = "https://raw.githubusercontent.com/Ajatt-Tools/kitsunekko-mirror/main"
CATALOG_BRANCH = "main"
CATALOG_SECTIONS = ("anime_tv", "anime_movie")
SUB_EXTS = {".srt", ".ass", ".ssa", ".vtt"}
MEDIA_EXTS = {".mkv", ".mp4", ".webm", ".avi", ".mov", ".m4v", ".ts", ".mp3", ".m4a", ".wav", ".flac", ".aac",
              ".opus", ".ogg"}
LANG_TAGS = {
    "ja": {"ja", "jp", "jpn", "jap", "japanese", "日本語", "jpsc", "jptc"},
    "en": {"en", "eng", "english"},
}
OTHER_TAGS = {"chs", "cht", "sc", "tc", "zh", "zho", "chi", "chinese", "gb", "big5", "简体", "繁体", "简", "繁",
              "ko", "kor", "korean", "es", "spa", "fr", "fre", "fra", "de", "ger", "deu", "pt", "por", "ptbr",
              "ru", "rus", "it", "ita", "ar", "ara", "vi", "vie", "th", "tha", "id", "ind", "pl", "pol", "tr", "tur"}
TEXT_CODECS = {"ass", "ssa", "subrip", "srt", "mov_text", "webvtt", "text"}
ISO_639_2 = {"ja": {"jpn", "ja", "jp"}, "en": {"eng", "en"}}
NOISE_WORDS = {"bd", "bdrip", "web", "webrip", "web-dl", "hevc", "avc", "flac", "aac", "x264", "x265", "h264",
               "h265", "10bit", "8bit", "dual", "audio", "multi", "subs", "sub", "raw", "tv", "uncensored"}
# Folder names that say nothing about the series (the video is then named by its file only).
GENERIC_FOLDERS = {"downloads", "download", "videos", "video", "anime", "movies", "movie", "films", "film", "tv",
                   "series", "shows", "media", "desktop", "documents", "torrents", "completed", "incoming", "new folder",
                   "home", "public", "music", "watch", "to watch", "seasons", "season", "episodes", "extras"}
EN_WORDS = {"the", "you", "and", "to", "is", "it", "that", "what", "of", "a", "i", "this", "we", "me", "my",
            "are", "do", "don't", "i'm", "it's", "have", "no", "not", "be", "your", "for", "in"}

_EPISODE_RES = [
    re.compile(r"\bS\d{1,2}\s?E(\d{1,4})", re.I),
    re.compile(r"第\s*(\d{1,4})\s*[話话集]"),
    re.compile(r"(?<![A-Za-z])(?:ep|episode|e)[\s._-]?(\d{1,4})(?![\dA-Za-z])", re.I),
    re.compile(r"\[(\d{1,3})(?:v\d)?\]"),
    re.compile(r"\s-\s(\d{1,4})(?:v\d)?(?![\dA-Za-z])"),
]
_LOOSE_NUMBER = re.compile(r"(?<![\dA-Za-z])(\d{1,3})(?:v\d)?(?![\dA-Za-z])")
_BRACKETS = re.compile(r"\[[^\]]*\]|\([^)]*\)|【[^】]*】|「[^」]*」")


@dataclass
class Found:
    path: Path
    how: str  # human-readable reason, for the log

    def __str__(self) -> str:
        return f"{self.path} ({self.how})"


# --- names: episode number, series title, language ------------------------------------------

def episode_number(name: str) -> int | None:
    """Episode number in a file name: S01E03, 第3話, ep03, [03], "Show - 03", "Show_03"."""
    stem = _strip_tags(name)
    for rx in _EPISODE_RES:
        if m := rx.search(stem):
            return int(m.group(1))
    loose = _LOOSE_NUMBER.findall(_BRACKETS.sub(" ", stem))
    return int(loose[-1]) if loose else None


def _strip_tags(name: str) -> str:
    """File name without the extension and language parts: "ep01.ja.srt" -> "ep01"."""
    p = Path(name)
    if p.suffix.lower() in SUB_EXTS | MEDIA_EXTS:
        name = p.stem
    parts = name.split(".")
    while len(parts) > 1 and _tag_language(parts[-1]) is not None:
        parts.pop()
    return ".".join(parts)


def series_title(name: str) -> str:
    """The series part of a file or folder name: "[Group] Some Show - 01 [1080p].mkv" -> "Some Show"."""
    stem = _strip_tags(name)
    cut = len(stem)
    for rx in _EPISODE_RES:
        if (m := rx.search(stem)) and m.start() < cut:
            cut = m.start()
    if cut == len(stem):
        loose = list(_LOOSE_NUMBER.finditer(stem))
        if loose and not _BRACKETS.search(loose[-1].group(0)):
            cut = loose[-1].start()
    stem = _BRACKETS.sub(" ", stem[:cut])
    words = [w for w in re.split(r"[\s._]+", stem) if w and w.lower() not in NOISE_WORDS]
    return " ".join(words).strip(" -_")


def _words(title: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+|[^\x00-\x7f\s]+", title.lower()) if w not in NOISE_WORDS]


def _is_abbreviation(short: str, words: list[str]) -> bool:
    """`short` is built from prefixes (2+ letters) of at least two of `words`, in order:
    "wataten" = WATAshi ni TENshi ga Maiorita, "konosuba" = KONO SUBArashii ..."""
    if len(short) < 4 or len(words) < 2:
        return False

    def fits(s: str, i: int, used: int) -> bool:
        if not s:
            return used >= 2
        for j in range(i, len(words)):
            w = words[j]
            for n in range(min(len(w), len(s)), 1, -1):
                if s[:n] == w[:n] and fits(s[n:], j + 1, used + 1):
                    return True
        return False

    return fits(short, 0, 0)


def same_series(a: str, b: str) -> bool:
    """Two titles name the same series: shared words, one inside the other, or an abbreviation."""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return False
    ca, cb = "".join(wa), "".join(wb)
    if ca == cb or (min(len(ca), len(cb)) >= 4 and (ca in cb or cb in ca)):
        return True
    sa, sb = set(wa), set(wb)
    common = len(sa & sb)  # most words shared ("Dr Stone" / "Dr. STONE. STONE WARS"), not one in two
    if common / max(len(sa), len(sb)) > 0.5 or (common >= 2 and common / min(len(sa), len(sb)) >= 0.8):
        return True
    return (len(wa) == 1 and _is_abbreviation(wa[0], wb)) or (len(wb) == 1 and _is_abbreviation(wb[0], wa))


def _tag_language(tag: str) -> str | None:
    t = tag.lower().strip()
    for lang, tags in LANG_TAGS.items():
        if t in tags:
            return lang
    return "other" if t in OTHER_TAGS else None


def name_language(path: Path) -> str | None:
    """Language from the file name: dotted parts (`.ja.srt`, `.chs.ass`) or bracket tags (`[JPN]`)."""
    for part in reversed(path.stem.split(".")[1:]):
        if lang := _tag_language(part):
            return lang
    for tag in re.findall(r"[\[(]([^\])]+)[\])]", path.stem):
        for piece in re.split(r"[\s,&+_-]+", tag):
            if lang := _tag_language(piece):
                return lang
    return None


def text_language(text: str) -> str | None:
    """"ja", "en", "other" or None (too little text) from the characters themselves."""
    text = re.sub(r"\{[^}]*\}|\\[Nnh]", " ", text)
    kana = len(re.findall(r"[ぁ-ゖァ-ヺ]", text))
    kanji = len(re.findall(r"[一-鿿㐀-䶿]", text))
    hangul = len(re.findall(r"[가-힣]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    total = kana + kanji + hangul + latin
    if total < 20:
        return None
    if kana + kanji > 0.3 * total:
        return "ja" if kana > 0.25 * (kana + kanji) else "other"  # kanji without kana: Chinese
    if hangul > 0.3 * total:
        return "other"
    words = re.findall(r"[a-z']+", text.lower())
    if words and sum(w in EN_WORDS for w in words) > 0.08 * len(words):
        return "en"
    return "other"


def _dialogue_text(path: Path) -> str:
    try:
        import pysubs2
        subs = pysubs2.load(str(path), encoding="utf-8-sig")
        return "\n".join(ev.plaintext for ev in subs.events if not ev.is_comment)
    except Exception:  # unreadable or another encoding: fall back to the raw bytes
        try:
            return path.read_bytes()[:400_000].decode("utf-8", "replace")
        except OSError:
            return ""


def is_japanese(path: Path) -> bool:
    lang = name_language(path)
    return lang == "ja" or (lang is None and text_language(_dialogue_text(path)) in ("ja", None))


# --- the online catalog -------------------------------------------------------------------------

class CatalogError(RuntimeError):
    pass


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("karaoke-subs")
    except Exception:
        return "dev"


class Catalog:
    """The Japanese subtitle catalog on GitHub (one folder per series), read through the GitHub
    API without an account. Listings are cached for a day and files for good, so a normal run
    makes at most a few API calls (GitHub allows 60 an hour without a token; a GITHUB_TOKEN in
    the environment raises that to 5,000)."""

    def __init__(self, cache: Path | None = None, max_age: float = 86400):
        from .paths import USER_DIR

        self.cache = cache or USER_DIR / "subtitles"
        self.max_age = max_age

    def _request(self, url: str) -> bytes:
        headers = {"User-Agent": f"karaoke-subs/{_version()}"}
        if url.startswith("https://api.github.com/"):
            headers["Accept"] = "application/vnd.github+json"
            if token := os.environ.get("GITHUB_TOKEN"):
                headers["Authorization"] = f"Bearer {token}"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 429) and e.headers.get("x-ratelimit-remaining") == "0":
                wait = max(0, int(e.headers.get("x-ratelimit-reset") or time.time()) - int(time.time()))
                raise CatalogError(f"GitHub's hourly limit for the subtitle catalog is used up; try again in "
                                   f"{wait // 60 + 1} min (or set GITHUB_TOKEN)") from None
            raise CatalogError(f"subtitle catalog: HTTP {e.code} for {url}") from None
        except OSError as e:
            raise CatalogError(f"subtitle catalog unreachable (no internet?): {e}") from None

    def _tree(self, tree: str, recursive: bool = False) -> list[dict]:
        """A folder of the catalog as [{path, type, size}], cached for max_age seconds."""
        key = re.sub(r"[^\w.-]+", "_", tree)[:150] + (".r" if recursive else "") + ".json"
        cached = self.cache / ".listings" / key
        if cached.exists() and time.time() - cached.stat().st_mtime < self.max_age:
            return json.loads(cached.read_text(encoding="utf-8"))
        url = f"{CATALOG_API}/git/trees/{urllib.parse.quote(tree, safe=':/')}" + ("?recursive=1" if recursive else "")
        items = [{"path": t["path"], "type": t["type"], "size": t.get("size", 0)}
                 for t in json.loads(self._request(url))["tree"]]
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        return items

    def series(self) -> list[tuple[str, str]]:
        """(section, folder name) of every series in the catalog."""
        return [(section, t["path"]) for section in CATALOG_SECTIONS
                for t in self._tree(f"{CATALOG_BRANCH}:subtitles/{section}") if t["type"] == "tree"]

    def files(self, section: str, folder: str) -> list[dict]:
        """Files of one series folder (subfolders included): [{path, size}], path relative to it."""
        return [t for t in self._tree(f"{CATALOG_BRANCH}:subtitles/{section}/{folder}", recursive=True)
                if t["type"] == "blob"]

    def download(self, section: str, folder: str, file: dict) -> Path:
        """The file, from the cache when its size matches."""
        dest = self.cache / folder / file["path"]
        if not (dest.exists() and dest.stat().st_size == file.get("size", -1)):
            path = urllib.parse.quote(f"subtitles/{section}/{folder}/{file['path']}")
            data = self._request(f"{CATALOG_RAW}/{path}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        return dest


def _similarity(a: str, b: str) -> float:
    wa, wb = set(_words(a)), set(_words(b))
    if "".join(_words(a)) == "".join(_words(b)):
        return 2.0
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def video_titles(media: Path) -> list[str]:
    """Series titles to search for: from the file name, then from the folder name (unless the
    folder is a generic one like Downloads)."""
    media = media.resolve()
    folder = series_title(media.parent.name)
    titles = [series_title(media.name), "" if folder.lower() in GENERIC_FOLDERS else folder]
    return list(dict.fromkeys(t for t in titles if t))


def find_series(catalog: Catalog, media: Path) -> list[tuple[str, str]]:
    """Catalog folders that may hold the video's series, best first: titles from the file name,
    then the folder name; the closest name first (Dr. STONE before Dr. STONE. STONE WARS for
    "Dr Stone"); films prefer the movie section."""
    film = episode_number(media.name) is None
    folders = catalog.series()
    for title in video_titles(media):
        matches = [(s, f) for s, f in folders if same_series(title, f)]
        if matches:
            return sorted(matches, key=lambda m: (-_similarity(title, m[1]), (m[0] == "anime_movie") != film))
    return []


def _zip_members(path: Path) -> list[tuple[str, bytes]]:
    try:
        with zipfile.ZipFile(path) as z:
            return [(n, z.read(n)) for n in z.namelist() if Path(n).suffix.lower() in SUB_EXTS]
    except (zipfile.BadZipFile, OSError):
        return []


def _episode_files(catalog: Catalog, section: str, folder: str, episode: int | None) -> list[Path]:
    """Japanese subtitle files of `episode` (None: a film's), downloaded; a season .zip is
    unpacked when no single file matches."""
    files = catalog.files(section, folder)
    subs = [f for f in files if Path(f["path"]).suffix.lower() in SUB_EXTS
            and (episode is None or episode_number(Path(f["path"]).name) == episode)]
    for f in sorted(subs, key=lambda f: f["path"]):
        if is_japanese(p := catalog.download(section, folder, f)):
            return [p]
    for f in files:
        if Path(f["path"]).suffix.lower() != ".zip":
            continue
        pack = catalog.download(section, folder, f)
        out = []
        for name, data in _zip_members(pack):
            if episode is not None and episode_number(Path(name).name) != episode:
                continue
            dest = pack.with_suffix("") / Path(name).name
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
            if is_japanese(dest):
                out.append(dest)
        if out:
            return out
    return []


def find_online(media: Path, catalog: Catalog | None = None) -> Found | None:
    """Download the Japanese subtitles of `media` from the online catalog; None when not found."""
    catalog = catalog or Catalog()
    episode = episode_number(media.name)  # None: a film
    for section, folder in find_series(catalog, media)[:3]:  # an empty folder: try the next best
        if files := _episode_files(catalog, section, folder, episode):
            what = f"episode {episode}" if episode is not None else "film"
            return Found(files[0], f"online catalog: {folder}, {what}")
    return None


def series_online(media: Path, catalog: Catalog | None = None, limit: int = 30) -> list[Path]:
    """The other episodes' Japanese subtitles of the same series (for --names-from auto): one
    file per episode, at most `limit` episodes."""
    catalog = catalog or Catalog()
    matches = find_series(catalog, media)
    if not matches:
        return []
    section, folder = matches[0]
    own = episode_number(media.name)
    by_episode: dict[int, dict] = {}
    for f in sorted(catalog.files(section, folder), key=lambda f: f["path"]):
        n = episode_number(Path(f["path"]).name)
        if n is not None and n != own and Path(f["path"]).suffix.lower() in SUB_EXTS:
            by_episode.setdefault(n, f)
    out = []
    for n in sorted(by_episode)[:limit]:
        p = catalog.download(section, folder, by_episode[n])
        if is_japanese(p):
            out.append(p)
    return out


# --- a track inside the video -------------------------------------------------------------------

def embedded_tracks(media: Path) -> list[dict]:
    """Text subtitle streams inside `media`: [{index, codec, language, title}]."""
    if not shutil.which("ffprobe"):
        return []
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "s", "-show_streams", "-of", "json", str(media)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return []
    out = []
    for s in json.loads(r.stdout or "{}").get("streams", []):
        if s.get("codec_name") in TEXT_CODECS:
            tags = {k.lower(): v for k, v in (s.get("tags") or {}).items()}
            out.append({"index": s["index"], "codec": s["codec_name"], "language": tags.get("language", "").lower(),
                        "title": tags.get("title", "")})
    return out


def extract_embedded(media: Path, out_dir: Path, language: str = "ja") -> Found | None:
    """Extract the first text subtitle track in `language` (by its tag, or by its text when
    untagged) to out_dir/embedded.<index>.<language>.<ext>."""
    tracks = embedded_tracks(media)
    tagged = [t for t in tracks if t["language"] in ISO_639_2.get(language, ())
              or language == "ja" and re.search(r"japanese|日本語", t["title"], re.I)]
    untagged = [t for t in tracks if t["language"] in ("", "und") and t not in tagged]
    for t in tagged + untagged:
        out_dir.mkdir(parents=True, exist_ok=True)
        ext = ".ass" if t["codec"] in ("ass", "ssa") else ".srt"
        out = out_dir / f"embedded.{t['index']}.{language}{ext}"
        if not out.exists() or out.stat().st_mtime < media.stat().st_mtime:
            r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(media),
                                "-map", f"0:{t['index']}", str(out)], capture_output=True)
            if r.returncode != 0:
                continue
        if t in tagged or text_language(_dialogue_text(out)) == language:
            return Found(out, f"track {t['index']} inside the video")
    return None
