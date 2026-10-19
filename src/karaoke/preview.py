"""Render a preview video to check subtitles by eye and ear.

Japanese karaoke on top, optionally romaji karaoke under it, English gloss
karaoke at the bottom, all highlighting word by word. Uses the video if the media has one, otherwise a black background with
the audio (e.g. an .mp3).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pysubs2

# A font with Japanese glyphs on each OS (libass falls back through fontconfig elsewhere).
JAPANESE_FONT = {"win32": "Yu Gothic UI", "darwin": "Hiragino Sans"}.get(sys.platform, "Noto Sans CJK JP")

STYLES = {
    # name: (font size, vertical margin); stacked bottom to top, skipping absent rows
    "Gloss": (54, 60),
    "Romaji": (46, 90),
    "Japanese": (62, 90),
}


def combine(gloss_ass: Path, japanese_ass: Path | None, out: Path,
            romaji_ass: Path | None = None) -> Path:
    """One .ass with Japanese (and romaji) lines above the gloss line."""
    combined = pysubs2.SSAFile()
    combined.info.update({"PlayResX": "1920", "PlayResY": "1080", "WrapStyle": "0"})
    sources = {"Gloss": gloss_ass, "Romaji": romaji_ass, "Japanese": japanese_ass}
    margin = 0
    for name, (size, step) in STYLES.items():
        if sources[name] is None:
            continue
        margin += step
        combined.styles[name] = pysubs2.SSAStyle(
            fontname=JAPANESE_FONT if name == "Japanese" else "Arial", fontsize=size, bold=True,
            primarycolor=pysubs2.Color(255, 220, 0),     # sung (highlighted): yellow
            secondarycolor=pysubs2.Color(255, 255, 255),  # not yet sung: white
            outlinecolor=pysubs2.Color(0, 0, 0), backcolor=pysubs2.Color(0, 0, 0, 128),
            outline=3, shadow=1, alignment=pysubs2.Alignment.BOTTOM_CENTER, marginv=margin)
    for style, path in sources.items():
        if path is None:
            continue
        for ev in pysubs2.load(str(path), encoding="utf-8-sig").events:
            if ev.type == "Dialogue":
                ev.style = style
                combined.events.append(ev)
    combined.sort()
    combined.save(str(out), encoding="utf-8-sig")
    return out


def _has_video(media: Path) -> bool:
    info = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(media)],
                          capture_output=True, text=True, check=True)
    return any(s["codec_type"] == "video" and s.get("disposition", {}).get("attached_pic", 0) == 0
               for s in json.loads(info.stdout)["streams"])


def render(media: Path, subs_ass: Path, out_mp4: Path, height: int = 720,
           start: float | None = None, duration: float | None = None) -> Path:
    """Burn `subs_ass` into a video. Paths for the subtitles filter are made
    relative (ffmpeg's filter syntax chokes on Windows drive letters)."""
    from .audio import require_ffmpeg

    require_ffmpeg()
    cwd = subs_ass.parent
    trim = (["-ss", str(start)] if start else []) + (["-t", str(duration)] if duration else [])
    sub_filter = f"subtitles={subs_ass.name}"
    if start:  # keep subtitle timing when seeking: shift the subs with the video
        sub_filter = f"setpts=PTS+{start}/TB,{sub_filter},setpts=PTS-STARTPTS"
    if _has_video(media):
        cmd = ["ffmpeg", "-y", *trim, "-i", str(media.resolve()),
               "-vf", f"scale=-2:{height},{sub_filter}", "-map", "0:v:0", "-map", "0:a:0"]
    else:
        cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=black:s={height * 16 // 9}x{height}:r=24",
               *trim, "-i", str(media.resolve()),
               "-vf", sub_filter, "-map", "0:v", "-map", "1:a:0", "-shortest"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "128k",
            str(out_mp4.resolve())]
    subprocess.run(cmd, cwd=cwd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out_mp4
