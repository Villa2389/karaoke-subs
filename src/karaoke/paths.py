"""Where the program keeps its files, on any OS and however it was installed.

User folder (KARAOKE_USER_DIR, default ~/.karaoke-subs):
  config.toml          model profiles and subs_dirs (see config.example.toml)
  known_words.txt      words you know (vocab.py), words.json: words heard per episode
  dict/                JMdict_e.gz, downloaded on first use, and its index
  models/              GGUF files for the `local` profile, vocal separation models
  tools/llama-cuda/    a llama.cpp build for the `local` profile (else llama-server on PATH)
  subs/                a subtitle library: one folder per series (subfind.py)
  logs/                llama-server.log

A source checkout's own data/dict, models and tools folders are still used when they exist,
so a development setup keeps working without moving anything.
"""
from __future__ import annotations

import os
import shutil
import sys
import urllib.request
from pathlib import Path

USER_DIR = Path(os.environ.get("KARAOKE_USER_DIR") or Path.home() / ".karaoke-subs").expanduser()
CHECKOUT = Path(__file__).resolve().parents[2]  # the repository root when run from source
JMDICT_URL = "https://www.edrdg.org/pub/Nihongo/JMdict_e.gz"
EXE = ".exe" if os.name == "nt" else ""


def config_path() -> Path:
    return Path(os.environ.get("KARAOKE_CONFIG") or USER_DIR / "config.toml")


def _first_existing(*paths: Path) -> Path | None:
    return next((p for p in paths if p.exists()), None)


def dict_dir() -> Path:
    """data/dict in a source checkout that already has JMdict, else the user folder's dict/."""
    if env := os.environ.get("KARAOKE_DICT_DIR"):
        return Path(env)
    dev = CHECKOUT / "data" / "dict"
    return dev if (dev / "JMdict_e.gz").exists() else USER_DIR / "dict"


def jmdict_file(download: bool = True) -> Path:
    """JMdict_e.gz (EDRDG, CC BY-SA 4.0), downloaded once (~10 MB) when missing."""
    path = dict_dir() / "JMdict_e.gz"
    if not path.exists() and download:
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"downloading JMdict (~10 MB) from {JMDICT_URL} ...", file=sys.stderr, flush=True)
        tmp = path.with_suffix(".part")
        try:
            with urllib.request.urlopen(JMDICT_URL, timeout=120) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f)
        except OSError as e:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"could not download JMdict ({e}); download {JMDICT_URL} yourself "
                               f"and put it at {path}") from None
        tmp.replace(path)
    return path


def models_dir() -> Path:
    return _first_existing(CHECKOUT / "models") or USER_DIR / "models"


def resolve_model(path: str | Path) -> Path:
    """A model file: absolute, else relative to the user's models folder (or the checkout)."""
    p = Path(path).expanduser()
    if p.is_absolute():
        return p
    for base in (USER_DIR, CHECKOUT, USER_DIR / "models"):
        if (base / p).exists():
            return base / p
    return USER_DIR / p


def llama_server_exe() -> str:
    """tools/llama-cuda/llama-server in the user folder or checkout, else llama-server on PATH."""
    name = "llama-server" + EXE
    found = _first_existing(USER_DIR / "tools" / "llama-cuda" / name, CHECKOUT / "tools" / "llama-cuda" / name)
    return str(found or shutil.which("llama-server") or name)


def log_dir() -> Path:
    d = USER_DIR / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d
