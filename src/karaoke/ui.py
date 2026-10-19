"""karaoke ui: a local web page for `time`, `preview` and `learn` (stdlib only, 127.0.0.1).

The page builds the same command lines as the CLI and runs them as a subprocess, one job at a
time; it shows the log live, lists the output folder and plays rendered previews. Its Words
section asks the same questions as `karaoke learn` and saves the answers the same way."""
from __future__ import annotations

import json
import mimetypes
import os
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PAGE = Path(__file__).with_name("ui.html")
SHOWN = {".ass", ".srt", ".mp4", ".tsv", ".json", ".md", ".wav"}


def clean_path(p: str | None) -> str:
    """Windows "Copy as path" wraps paths in quotes."""
    return (p or "").strip().strip('"').strip("'").strip()


def out_dir(form: dict) -> Path:
    """Where `time` writes: --out, else work/<media stem> (same rule as cli.cmd_time)."""
    if clean_path(form.get("out")):
        return Path(clean_path(form["out"]))
    return Path("work") / Path(clean_path(form.get("media"))).stem


def time_args(form: dict) -> list[str]:
    """`karaoke time` arguments for the page's form."""
    media, subs = clean_path(form.get("media")), clean_path(form.get("subs"))
    if not media:
        raise ValueError("choose the episode first")
    for p in (media, subs, clean_path(form.get("en_subs"))):
        if p and not Path(p).is_file():
            raise ValueError(f"file not found: {p}")
    args = ["time", media] + ([subs] if subs else [])  # no subtitles: `time` finds them
    gloss = [g for g in form.get("gloss", []) if g in ("dict", "llm", "claude")]
    if gloss:
        args += ["--gloss", ",".join(gloss)]
        if "claude" in gloss:
            args += ["--claude-model", form.get("claude_model") or "claude-haiku-5-5",
                     "--claude-effort", form.get("claude_effort") or "xhigh"]
        if "llm" in gloss and (model := str(form.get("model") or "").strip()):
            args += ["--model", model]
        if form.get("glossary") and ("claude" in gloss or "llm" in gloss):  # any LLM glosser
            args.append("--glossary")
    if en := clean_path(form.get("en_subs")):
        args += ["--en-subs", en]
    for flag in ("romaji", "review", "no_sync", "vocals", "no_verify"):
        if form.get(flag):
            args.append("--" + flag.replace("_", "-"))
    if form.get("method") == "proportional":
        args += ["--method", "proportional"]
    for key in ("lead", "stream", "out", "tag", "reference", "llm_model", "fixes"):
        if v := clean_path(str(form.get(key) or "")):
            args += ["--" + key.replace("_", "-"), v]
    return args


def find_subs(form: dict) -> dict:
    """Download the chosen video's Japanese subtitles (what `time` would use)."""
    from .subfind import CatalogError, find_online

    media = Path(clean_path(form.get("media")))
    if not media.is_file():
        raise ValueError(f"episode not found: {media}")
    try:
        found = find_online(media)
    except CatalogError as e:
        raise ValueError(str(e)) from None
    return {"subs": str(found.path) if found else "", "how": found.how if found else ""}


def model_list() -> dict:
    """Model profiles for the page's dropdown (same config as the `time` runs it starts)."""
    from .providers import config_path, load_config

    profiles, default = load_config()
    return {"default": default, "config": str(config_path()), "profiles": [
        {"name": p.name, "kind": p.kind, "needs": p.missing_key(),
         "label": Path(p.gguf).name if p.kind == "llama-server" else f"{p.model} ({p.kind})"}
        for p in profiles.values()]}


def preview_args(form: dict) -> list[str]:
    media, gloss = clean_path(form.get("media")), clean_path(form.get("gloss"))
    if not Path(media).is_file():
        raise ValueError(f"episode not found: {media}")
    if not Path(gloss).is_file():
        raise ValueError(f"subtitles not found: {gloss}")
    args = ["preview", media, gloss]
    if form.get("romaji"):
        args.append("--romaji")
    for key in ("start", "duration"):
        if v := str(form.get(key) or "").strip():
            args += ["--" + key, str(float(v))]
    return args


class Job:
    """One CLI run; log lines are kept for the page to poll."""

    def __init__(self, args: list[str]):
        self.args = args
        self.lines: list[str] = ["> karaoke " + " ".join(f'"{a}"' if " " in a else a for a in args)]
        self.status = "running"
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "karaoke.cli", *args], stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env, text=True,
            encoding="utf-8", errors="replace", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            start_new_session=os.name != "nt")  # POSIX: its own process group, stopped as one
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.proc.stdout:
            self.lines.append(line.rstrip("\n"))
        code = self.proc.wait()
        if self.status == "running":
            self.status = "done" if code == 0 else "failed"
        self.lines.append(f"[{self.status}, exit code {code}]")

    def stop(self) -> None:
        if self.status != "running":
            return
        self.status = "stopped"
        # The whole tree: a lazily started llama-server holds ~6 GB VRAM.
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)], capture_output=True)
        else:
            import signal
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass


def pick_file(kind: str) -> str:
    """Native file dialog, in its own process (tkinter wants the main thread)."""
    types = {
        "media": [("Video / audio", "*.mkv *.mp4 *.webm *.avi *.mov *.mp3 *.m4a *.wav *.flac *.aac *.opus")],
        "subs": [("Subtitles", "*.srt *.ass *.ssa *.vtt")],
        "tsv": [("Review sheet", "*.tsv")],
        "gguf": [("Model", "*.gguf")],
        "folder": [],
    }.get(kind, [])
    if kind == "folder":
        call = "filedialog.askdirectory()"
    else:
        call = f"filedialog.askopenfilename(filetypes={types + [('All files', '*.*')]!r})"
    code = ("import tkinter as tk; from tkinter import filedialog; r = tk.Tk(); r.withdraw(); "
            f"r.attributes('-topmost', True); print({call} or '')")
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8")
    if res.returncode != 0:  # e.g. Linux without python3-tk
        raise ValueError("no file dialog available (tkinter missing): type or paste the path instead")
    return res.stdout.strip().replace("/", os.sep)


def open_folder(folder: Path) -> None:
    if os.name == "nt":
        os.startfile(folder)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
    else:
        subprocess.Popen(["xdg-open", str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def list_outputs(folder: Path) -> list[dict]:
    if not folder.is_dir():
        return []
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix in SHOWN]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"name": p.name, "path": str(p.resolve()), "size": p.stat().st_size,
             "mtime": p.stat().st_mtime} for p in files]


LEARN_LOCK = threading.Lock()  # known_words.txt and words.json are rewritten after each answer


def learn_cards(user_dir: Path | None = None, limit: int = 30) -> dict:
    """The questions of `karaoke learn`: words heard often, then each series' most heard words
    not asked yet. Read from disk each time, so words from a `time` run that just ended show up."""
    from .vocab import open_user, plan_review, romaji_of

    known, stats = open_user(user_dir)
    due, plan = plan_review(stats, known, limit)

    def card(kind: str, word: str, heard: int) -> dict:
        e = stats.words[word]
        return {"kind": kind, "word": word, "romaji": romaji_of(word), "reading": e["reading"],
                "example": e["example"], "gloss": e["gloss"], "heard": heard}

    sections = [{"title": "Heard often", "note": f"{len(due)} words heard enough times to ask",
                 "cards": [card("due", d.word, stats.count(d.entry)) for d in due]}]
    for s in plan:
        sections.append({"title": s.series,
                         "note": f"{s.episodes} episodes, {s.unasked} words not asked yet",
                         "cards": [card("series", w, n) for w, n in s.words]})
    return {"known": len(known.words), "sections": sections}


def learn_answer(body: dict, user_dir: Path | None = None) -> dict:
    """One answer, saved exactly as the terminal `learn` saves it."""
    from .vocab import Due, open_user

    known, stats = open_user(user_dir)
    word, knew = body.get("word"), bool(body.get("knew"))
    if word not in stats.words:
        raise ValueError(f"unknown word {word!r}")
    if body.get("kind") == "due":
        e = stats.words[word]
        stats.answer(Due(word, e), knew, known)
        return {"ok": True, "again_after": None if knew else e["ask_at"] - stats.count(e)}
    if body.get("kind") == "series":
        stats.answer_series(word, knew, known)
        return {"ok": True}
    raise ValueError(f"unknown kind {body.get('kind')!r}")


class Handler(BaseHTTPRequestHandler):
    job: Job | None = None
    allowed: set[Path] = set()  # output folders the page has listed: /file serves only these
    user_dir: Path | None = None  # known words and word counts (default ~/.karaoke-subs)

    def log_message(self, *a) -> None:
        pass

    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path == "/":
            body = PAGE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif url.path == "/api/job":
            job, since = Handler.job, int(q.get("since", 0))
            if job is None:
                return self._json({"status": "idle", "lines": [], "next": 0})
            lines = job.lines[since:]
            self._json({"status": job.status, "lines": lines, "next": since + len(lines),
                        "cmd": job.args[0]})
        elif url.path == "/api/outputs":
            folder = out_dir(q).resolve()
            Handler.allowed.add(folder)
            self._json({"folder": str(folder), "files": list_outputs(folder)})
        elif url.path == "/api/models":
            try:
                self._json(model_list())
            except (ValueError, OSError) as e:  # a broken config.toml: say why in the page
                self._json({"error": str(e)}, 400)
        elif url.path == "/api/learn":
            with LEARN_LOCK:
                self._json(learn_cards(Handler.user_dir, int(q.get("limit", 30))))
        elif url.path == "/file":
            self._file(Path(q.get("path", "")).resolve(), q.get("download") == "1")
        else:
            self.send_error(404)

    def _file(self, path: Path, download: bool) -> None:
        if path.parent not in Handler.allowed or not path.is_file():
            return self.send_error(404)
        size = path.stat().st_size
        start, end = 0, size - 1
        rng = self.headers.get("Range", "")
        if rng.startswith("bytes="):  # seeking in the video player
            a, _, b = rng[6:].partition("-")
            start = int(a) if a else max(0, size - int(b))
            end = int(b) if a and b else size - 1
        self.send_response(206 if rng else 200)
        ctype = mimetypes.guess_type(path.name)[0] or "text/plain; charset=utf-8"
        if path.suffix in (".ass", ".srt", ".tsv", ".md"):
            ctype = "text/plain; charset=utf-8"
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.end_headers()
        with path.open("rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0 and (chunk := f.read(min(1 << 20, left))):
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                pass

    def do_POST(self) -> None:
        n = int(self.headers.get("Content-Length", 0))
        form = json.loads(self.rfile.read(n) or b"{}")
        path = urlparse(self.path).path
        try:
            if path in ("/api/time", "/api/preview", "/api/test-model"):
                if Handler.job and Handler.job.status == "running":
                    raise ValueError("a job is already running: stop it or wait")
                if path == "/api/test-model":
                    args = ["models", "--test", str(form.get("model") or "").strip() or "local"]
                else:
                    args = time_args(form) if path == "/api/time" else preview_args(form)
                if path == "/api/time" and Handler.user_dir:  # same words the page asks about
                    args += ["--user-dir", str(Handler.user_dir)]
                Handler.job = Job(args)
                self._json({"ok": True})
            elif path == "/api/stop":
                if Handler.job:
                    Handler.job.stop()
                self._json({"ok": True})
            elif path == "/api/learn/answer":
                with LEARN_LOCK:
                    self._json(learn_answer(form, Handler.user_dir))
            elif path == "/api/find-subs":
                self._json(find_subs(form))
            elif path == "/api/pick":
                self._json({"path": pick_file(form.get("kind", ""))})
            elif path == "/api/open":
                folder = out_dir(form)
                folder.mkdir(parents=True, exist_ok=True)
                open_folder(folder.resolve())
                self._json({"ok": True})
            else:
                self.send_error(404)
        except ValueError as e:
            self._json({"error": str(e)}, 400)


class Server(ThreadingHTTPServer):
    # HTTPServer sets SO_REUSEADDR, which on Windows lets a second process bind a port already in
    # use: an old `karaoke ui` then keeps answering the browser with its old page.
    allow_reuse_address = False


def serve(port: int = 8765, open_browser: bool = True, user_dir: Path | None = None) -> None:
    Handler.user_dir = user_dir
    try:
        server = Server(("127.0.0.1", port), Handler)
    except OSError as e:
        raise SystemExit(f"port {port} is already in use (another `karaoke ui` is running?): {e}")
    url = f"http://127.0.0.1:{port}/"
    print(f"karaoke ui on {url}  (Ctrl+C to quit)", flush=True)
    if open_browser:
        threading.Timer(0.5, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if Handler.job:
            Handler.job.stop()
