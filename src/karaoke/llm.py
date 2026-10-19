"""LLMs over the OpenAI-compatible HTTP API, and the local llama.cpp server.

The local server is started on demand (paths.llama_server_exe) and reused if already up.
Replies are constrained with a JSON schema where the server supports it (llama.cpp turns
it into a grammar), so the model can only answer with the expected ids.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .paths import llama_server_exe, log_dir, resolve_model

# Use a CUDA build on NVIDIA GPUs: the Vulkan build prompt-processes Qwen3.5 at ~13 tok/s (CPU fallback).
DEFAULT_MODEL = "models/Qwen3.5-9B-Q4_K_M.gguf"


class LlamaServer:
    def __init__(self, model: Path | str = DEFAULT_MODEL, port: int = 8090, ctx: int = 4096,
                 exe: Path | str | None = None):
        self.model = resolve_model(model)
        self.exe = str(exe or llama_server_exe())
        self.url = f"http://127.0.0.1:{port}"
        self.port, self.ctx = port, ctx
        self.proc: subprocess.Popen | None = None

    def healthy(self) -> bool:
        try:
            with urllib.request.urlopen(self.url + "/health", timeout=2) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def start(self, timeout: float = 300) -> None:
        if self.healthy():
            return
        if not self.model.is_file():
            raise RuntimeError(f"model file not found: {self.model} (set gguf in the profile or --llm-model)")
        if not (Path(self.exe).is_file() or shutil.which(self.exe)):
            raise RuntimeError(f"llama-server not found ({self.exe}): install llama.cpp and put llama-server on "
                               "PATH, or set `server` in the profile")
        log = open(log_dir() / "llama-server.log", "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            [self.exe, "-m", str(self.model), "--host", "127.0.0.1", "--port", str(self.port),
             "-c", str(self.ctx), "-ngl", "99", "--jinja", "-np", "1"],
            stdout=log, stderr=subprocess.STDOUT)
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.proc.poll() is not None:
                raise RuntimeError(f"llama-server exited, see {log.name}")
            if self.healthy():
                return
            time.sleep(1)
        raise TimeoutError("llama-server did not become healthy")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(timeout=30)


def parse_json(text: str) -> dict:
    """The JSON object in a reply; models without schema support may wrap it in ```json fences
    or a sentence."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        a, b = text.find("{"), text.rfind("}")
        if a < 0 or b < a:
            raise ValueError(f"no JSON object in the reply: {text[:200]!r}") from None
        return json.loads(text[a:b + 1])


class OpenAIClient:
    """Any OpenAI-compatible /chat/completions endpoint: OpenAI, OpenRouter, Gemini, Groq,
    DeepSeek, Mistral, Together, Ollama, LM Studio, vLLM, llama.cpp's llama-server.

    json_mode: "schema" sends the JSON schema as response_format (the answer is constrained);
    "object" asks for any JSON object and puts the schema in the prompt; "none" only puts it in
    the prompt (for servers that reject response_format)."""

    def __init__(self, base_url: str, model: str, api_key: str | None = None, *, api_model: str | None = None,
                 temperature: float | None = 0.2, max_tokens: int = 4096, json_mode: str = "schema",
                 strict: bool = False, extra: dict | None = None, timeout: float = 300,
                 ascii_keys: bool = False, before=None, retries: int = 3):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model  # cache id; the API gets api_model when it differs (llama-server: gguf stem)
        self.api_model = api_model or model
        self.api_key, self.temperature, self.max_tokens = api_key, temperature, max_tokens
        self.json_mode, self.strict, self.extra, self.timeout = json_mode, strict, extra or {}, timeout
        self.ascii_keys, self.before, self.retries = ascii_keys, before, retries
        self.usage = {"calls": 0, "input": 0, "output": 0}
        self._lock = threading.Lock()

    def body(self, system: str, user: str, schema: dict) -> dict:
        if self.json_mode != "schema":
            user += ("\n\nAnswer with only a JSON object that matches this JSON schema:\n"
                     + json.dumps(schema, ensure_ascii=False))
        body = {"model": self.api_model, "max_tokens": self.max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.json_mode == "schema":
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "answer", "strict": self.strict, "schema": schema}}
        elif self.json_mode == "object":
            body["response_format"] = {"type": "json_object"}
        return body | self.extra

    def chat_json(self, system: str, user: str, schema: dict) -> dict:
        if self.before:
            self.before()  # llama-server starts lazily: fully cached runs never load the model
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = json.dumps(self.body(system, user, schema)).encode()
        for attempt in range(self.retries):
            try:
                req = urllib.request.Request(self.url, data=data, headers=headers)
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    reply = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:400]
                if e.code in (429, 500, 502, 503, 504) and attempt + 1 < self.retries:
                    time.sleep(float(e.headers.get("retry-after") or 5 * 2 ** attempt))
                    continue
                raise RuntimeError(f"HTTP {e.code} from {self.url}: {detail}") from None
        u = reply.get("usage") or {}
        with self._lock:
            self.usage["calls"] += 1
            self.usage["input"] += u.get("prompt_tokens") or 0
            self.usage["output"] += u.get("completion_tokens") or 0
        return parse_json(reply["choices"][0]["message"]["content"] or "")
