"""Find the subtitles of an episode automatically.

Looked for, best first:
1. next to the media file: same name (`Show - 01.mkv` + `Show - 01.ja.srt`), or the same
   episode number, in the media's folder or a subtitle subfolder of it (`Subs/`, `subtitles/`);
2. in subtitle libraries (`--subs-dir`, $KARAOKE_SUBS_DIR, `subs_dirs` in config.toml, and
   ~/.karaoke-subs/subs): the folder whose name matches the series (taken from the media's
   folder name and file name; abbreviations like `wataten` for "Watashi ni Tenshi ga
   Maiorita!" count), then the file with the episode number;
3. a text subtitle track inside the media file (mkv), extracted with ffmpeg.

The language comes from the file name (`.ja.`, `[JPN]`, `.chs.`, ...) or, without a tag, from
the text itself: Japanese has kana, Chinese has kanji without kana.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

SUB_EXTS = {".srt", ".ass", ".ssa", ".vtt"}
MEDIA_EXTS = {".mkv", ".mp4", ".webm", ".avi", ".mov", ".m4v", ".ts", ".mp3", ".m4a", ".wav", ".flac", ".aac",
              ".opus", ".ogg"}
SUB_DIR_NAMES = {"sub", "subs", "subtitle", "subtitles", "字幕", "japanese", "english", "jp", "ja", "jpn", "en",
                 "eng"}
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
# Files this program writes (ep.ja.ctc.romaji.ass, ep.ja.ctc.gloss-llm.ass, *.preview.ass, embedded.3.ja.ass).
_OUTPUT = re.compile(r"\.(?:romaji|japanese|preview)\.ass$|\.gloss-[^.]+(?:\.preview)?\.ass$|^embedded\.\d+\.", re.I)
SKIP_DIRS = {"work", ".git", "node_modules", "__pycache__"}


@dataclass
class Found:
    path: Path
    language: str
    how: str  # human-readable reason, for the log

    def __str__(self) -> str:
        return f"{self.path} ({self.how})"


def episode_number(name: str) -> int | None:
    """Episode number in a file name: S01E03, 第3話, ep03, [03], "Show - 03", "Show_03"."""
    stem = _strip_tags(name)
    for rx in _EPISODE_RES:
        if m := rx.search(stem):
            return int(m.group(1))
    loose = _LOOSE_NUMBER.findall(_BRACKETS.sub(" ", stem))
    return int(loose[-1]) if loose else None


def _strip_tags(name: str) -> str:
    """File name without the subtitle extension and language parts: "ep01.ja.srt" -> "ep01"."""
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
    common = len(set(wa) & set(wb))
    if common and common / min(len(set(wa)), len(set(wb))) >= 0.6:
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


def file_language(path: Path) -> str | None:
    return name_language(path) or text_language(_dialogue_text(path))


def library_dirs(extra: list[Path] | None = None) -> list[Path]:
    """Subtitle libraries: --subs-dir, $KARAOKE_SUBS_DIR, `subs_dirs` in config.toml, ~/.karaoke-subs/subs."""
    from .paths import USER_DIR, config_path

    dirs = list(extra or [])
    dirs += [Path(p) for p in os.environ.get("KARAOKE_SUBS_DIR", "").split(os.pathsep) if p]
    cfg = config_path()
    if cfg.is_file():
        import tomllib
        try:
            with open(cfg, "rb") as f:
                dirs += [Path(p).expanduser() for p in tomllib.load(f).get("subs_dirs", [])]
        except (OSError, tomllib.TOMLDecodeError):
            pass  # a broken config is reported by `karaoke models`; not this module's job
    dirs.append(USER_DIR / "subs")
    return [d.resolve() for d in dict.fromkeys(dirs) if d.is_dir()]


def _sub_files(folder: Path, depth: int) -> list[Path]:
    out: list[Path] = []
    try:
        entries = sorted(folder.iterdir())
    except OSError:
        return out
    for p in entries:
        if p.is_file() and p.suffix.lower() in SUB_EXTS and not _OUTPUT.search(p.name):
            out.append(p)
        elif p.is_dir() and depth > 0 and p.name.lower() not in SKIP_DIRS:
            out += _sub_files(p, depth - 1)
    return out


def _media_count(folder: Path) -> int:
    try:
        return sum(1 for p in folder.iterdir() if p.is_file() and p.suffix.lower() in MEDIA_EXTS)
    except OSError:
        return 0


def candidates(media: Path, libraries: list[Path] | None = None) -> list[tuple[int, Path, str]]:
    """(score, subtitle file, reason) for every file that may belong to `media`, best first.
    The language is not checked here."""
    media = media.resolve()
    folder = media.parent
    episode = episode_number(media.name)
    titles = [t for t in (series_title(media.name), series_title(folder.name)) if t]
    found: dict[Path, tuple[int, str]] = {}

    def consider(sub: Path, base: int, where: str) -> None:
        sub_stem = _strip_tags(sub.name)
        if sub_stem.lower() == media.stem.lower() or sub.stem.lower().startswith(media.stem.lower() + "."):
            score, how = base + 30, f"same name, {where}"
        elif episode is not None and episode_number(sub.name) == episode:
            sub_title = series_title(sub.name)
            if sub_title and titles and not any(same_series(sub_title, t) for t in titles):
                return  # another show's episode with the same number
            score, how = base + 20, f"episode {episode}, {where}"
        elif episode is None and _media_count(folder) == 1 and episode_number(sub.name) is None:
            score, how = base + 10, f"only video in its folder, {where}"  # a film
        else:
            return
        if sub not in found or found[sub][0] < score:
            found[sub] = (score, how)

    for sub in _sub_files(folder, 0):
        consider(sub, 3, "next to the video")
    for d in folder.iterdir() if folder.is_dir() else []:
        if d.is_dir() and (d.name.lower() in SUB_DIR_NAMES or any(same_series(d.name, t) for t in titles)):
            for sub in _sub_files(d, 2):
                consider(sub, 2, f"in {d.name}/")
    for lib in libraries if libraries is not None else library_dirs():
        series_dirs = [d for d in lib.iterdir() if d.is_dir() and any(same_series(d.name, t) for t in titles)]
        for d in series_dirs:
            for sub in _sub_files(d, 2):
                consider(sub, 1, f"library {d}")
    return sorted(((s, p, how) for p, (s, how) in found.items()), key=lambda x: (-x[0], str(x[1])))


def find_subtitles(media: Path, language: str = "ja", libraries: list[Path] | None = None) -> Found | None:
    """The best subtitle file in `language` ("ja" or "en") for `media`, or None."""
    for _, path, how in candidates(media, libraries):
        if file_language(path) == language:
            return Found(path, language, how)
    return None


def series_subtitles(found: Path, language: str = "ja") -> list[Path]:
    """The other episodes' subtitles in the same folder as `found` (for --names-from auto)."""
    title = series_title(found.name)
    out = []
    for p in _sub_files(found.parent, 0):
        if p == found or episode_number(p.name) is None:
            continue
        other = series_title(p.name)
        if title and other and not same_series(title, other):
            continue
        if file_language(p) == language:
            out.append(p)
    return out


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
    out_dir.mkdir(parents=True, exist_ok=True)
    for t in tagged + untagged:
        ext = ".ass" if t["codec"] in ("ass", "ssa") else ".srt"
        out = out_dir / f"embedded.{t['index']}.{language}{ext}"
        if not out.exists() or out.stat().st_mtime < media.stat().st_mtime:
            r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(media),
                                "-map", f"0:{t['index']}", str(out)], capture_output=True)
            if r.returncode != 0:
                continue
        if t in tagged or text_language(_dialogue_text(out)) == language:
            return Found(out, language, f"track {t['index']} inside the video")
    return None


def locate(media: Path, language: str, out_dir: Path, libraries: list[Path] | None = None) -> Found | None:
    """Subtitle file, else a track inside the video."""
    return find_subtitles(media, language, libraries) or extract_embedded(media, out_dir, language)
