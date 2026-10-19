"""Model profiles: which LLM writes the glosses, and how to reach it.

A profile is a name (used in `--gloss NAME` / `--model NAME`) plus how to call the model.
Built-in profiles cover the defaults; a TOML file adds or overrides them:
$KARAOKE_CONFIG, else ~/.karaoke-subs/config.toml (see config.example.toml).

kinds:
- openai       any OpenAI-compatible /chat/completions endpoint (OpenAI, OpenRouter, Gemini,
               Groq, DeepSeek, Mistral, Ollama, LM Studio, vLLM, a llama-server you run)
- anthropic    the Claude API with an API key (`uv sync --extra anthropic`)
- claude-code  `claude -p` on your Claude login (subscription, no API key)
- llama-server a local GGUF; this program starts and stops llama.cpp's server itself

API keys are never written in the file: `api_key_env` names the environment variable.
"""
from __future__ import annotations

import json
import os
import re
import threading
import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

from .paths import config_path, resolve_model

KINDS = ("openai", "anthropic", "claude-code", "llama-server")
DEFAULT_PROFILE = "local"


@dataclass
class Profile:
    name: str
    kind: str
    model: str = ""
    base_url: str = ""
    api_key_env: str = ""
    effort: str = ""  # anthropic / claude-code: low, medium, high, xhigh, max
    batch: int = 4  # subtitle lines per request
    jobs: int = 1  # requests running at once
    context: int = 3  # previous lines shown as context
    verify: bool = True  # second look at suspicious glosses (verify.py)
    ascii_keys: bool = False  # JSON property names must be ASCII (word goes in the description)
    json_mode: str = "schema"  # openai: schema | object | none (see llm.OpenAIClient)
    strict: bool = False  # openai: strict json_schema (OpenAI itself; others may reject it)
    temperature: float | None = 0.2
    max_tokens: int = 4096
    timeout: float = 300
    extra: dict = field(default_factory=dict)  # openai: merged into the request body
    gguf: str = ""  # llama-server: model file (relative paths: from ~/.karaoke-subs, see paths.resolve_model)
    server: str = ""  # llama-server: the executable (default: paths.llama_server_exe)
    port: int = 8090
    ctx: int = 4096
    source: str = "built-in"

    @property
    def cache_id(self) -> str:
        """Part of the cache file names (gloss_cache.<id>.json): one cache per model setting."""
        if self.kind == "llama-server":
            return Path(self.gguf).stem
        return re.sub(r"[^\w.-]+", "_", self.model + (f"-{self.effort}" if self.effort else ""))

    def model_path(self) -> Path:
        """llama-server: the GGUF file (relative paths: see paths.resolve_model)."""
        return resolve_model(self.gguf)

    def missing_key(self) -> str | None:
        """The environment variable this profile needs and is not set, if any."""
        if self.api_key_env and not os.environ.get(self.api_key_env):
            return self.api_key_env
        return None


BUILT_IN = {
    "local": Profile("local", "llama-server", gguf="models/Qwen3.5-9B-Q4_K_M.gguf", max_tokens=2048,
                     # Qwen3.5 thinks by default; glossing doesn't need it and it is 5-10x slower.
                     extra={"chat_template_kwargs": {"enable_thinking": False}}),
    "haiku": Profile("haiku", "claude-code", "claude-haiku-5-5", effort="xhigh", batch=20, jobs=4, context=10,
                     verify=False, ascii_keys=True),
    "sonnet": Profile("sonnet", "claude-code", "claude-sonnet-5-5", effort="high", batch=20, jobs=4, context=10,
                      verify=False, ascii_keys=True),
    "opus": Profile("opus", "claude-code", "claude-opus-5-5", effort="medium", batch=20, jobs=4, context=10,
                    verify=False, ascii_keys=True),
}
# Defaults per kind for profiles the config file defines from scratch.
KIND_DEFAULTS = {
    "openai": {"batch": 10, "jobs": 2, "context": 5},
    "anthropic": {"batch": 20, "jobs": 4, "context": 10, "ascii_keys": True, "temperature": None},
    "claude-code": {"batch": 20, "jobs": 4, "context": 10, "ascii_keys": True, "verify": False},
    "llama-server": {"max_tokens": 2048},
}
FIELDS = {f.name for f in fields(Profile)} - {"name", "source"}


def load_config(path: Path | None = None) -> tuple[dict[str, Profile], str]:
    """All profiles (built-in, then the file's) and the name of the default one."""
    path = path or config_path()
    profiles = dict(BUILT_IN)
    if not path.is_file():
        return profiles, DEFAULT_PROFILE
    with open(path, "rb") as f:
        data = tomllib.load(f)
    for name, values in (data.get("profiles") or {}).items():
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            raise ValueError(f"{path}: profile name {name!r}: use letters, digits, '-', '_' or '.'")
        unknown = set(values) - FIELDS
        if unknown:
            raise ValueError(f"{path}: profile {name!r}: unknown setting(s) {', '.join(sorted(unknown))}")
        if name in profiles:  # override some settings of a built-in profile
            base = profiles[name]
            if values.get("kind", base.kind) != base.kind:
                base = Profile(name, values["kind"], **KIND_DEFAULTS.get(values["kind"], {}))
        else:
            kind = values.get("kind")
            if kind not in KINDS:
                raise ValueError(f"{path}: profile {name!r}: kind must be one of {', '.join(KINDS)}")
            base = Profile(name, kind, **KIND_DEFAULTS[kind])
        if values.get("temperature") is False:  # `temperature = false`: models that only accept the default
            values = values | {"temperature": None}
        p = replace(base, **values, source=str(path))
        if p.kind not in KINDS:
            raise ValueError(f"{path}: profile {name!r}: kind must be one of {', '.join(KINDS)}")
        if p.kind == "openai" and not (p.base_url and p.model):
            raise ValueError(f"{path}: profile {name!r}: an openai profile needs base_url and model")
        if p.kind in ("anthropic", "claude-code") and not p.model:
            raise ValueError(f"{path}: profile {name!r}: needs model")
        profiles[name] = p
    default = data.get("default", DEFAULT_PROFILE)
    if default not in profiles:
        raise ValueError(f"{path}: default = {default!r} is not a profile")
    return profiles, default


def get_profile(name: str | None, path: Path | None = None) -> Profile:
    profiles, default = load_config(path)
    name = name or default
    if name not in profiles:
        raise ValueError(f"no model profile {name!r}; known: {', '.join(profiles)} "
                         f"(add one in {path or config_path()})")
    return profiles[name]


def make_client(p: Profile):
    """A client with `model` (cache key), `chat_json(system, user, schema)`, `ascii_keys`,
    `usage` and `close()`."""
    if key := p.missing_key():
        raise RuntimeError(f"profile {p.name!r} needs the environment variable {key} (your API key)")
    if p.kind == "claude-code":
        from .claude_code import ClaudeCodeClient
        c = ClaudeCodeClient(p.model, p.effort or "xhigh", timeout=max(p.timeout, 900))
    elif p.kind == "anthropic":
        c = AnthropicClient(p.model, p.effort, os.environ.get(p.api_key_env) if p.api_key_env else None,
                            max_tokens=max(p.max_tokens, 16000), timeout=p.timeout)
    elif p.kind == "llama-server":
        from .llm import LlamaServer, OpenAIClient
        server = LlamaServer(p.model_path(), p.port, p.ctx, p.server or None)
        c = OpenAIClient(server.url + "/v1", p.model_path().stem, temperature=p.temperature, max_tokens=p.max_tokens,
                         json_mode=p.json_mode, extra=p.extra, timeout=p.timeout, before=server.start)
        c.close = server.stop  # only stops a server this run started; frees ~6 GB VRAM
    else:
        from .llm import OpenAIClient
        c = OpenAIClient(p.base_url, p.model, os.environ.get(p.api_key_env) if p.api_key_env else None,
                         temperature=p.temperature, max_tokens=p.max_tokens, json_mode=p.json_mode,
                         strict=p.strict, extra=p.extra, timeout=p.timeout)
    c.ascii_keys = p.ascii_keys
    if not hasattr(c, "close"):
        c.close = lambda: None
    return c


def usage_text(c) -> str:
    u = getattr(c, "usage", None) or {}
    if not u.get("calls"):
        return "no model calls"
    tokens_in = u.get("input", 0) + u.get("cache_write", 0) + u.get("cache_read", 0)
    text = f"{u['calls']} calls, {tokens_in} tokens in, {u.get('output', 0)} out"
    return text + (f" (API-price equivalent ${u['usd']:.2f})" if u.get("usd") else "")


def try_profile(p: Profile) -> dict:
    """One tiny request shaped like the glosser's (same JSON schema and key style), to check a
    profile works before a whole episode: {"ok", "reply" or "error", "seconds", "usage"}."""
    import time

    from .gloss import _schema

    words = ["犬", "走る"]
    t0 = time.time()
    try:
        client = make_client(p)
    except Exception as e:
        return {"ok": False, "error": str(e), "seconds": 0.0, "usage": ""}
    try:
        keys = [f"L0_{i}" if client.ascii_keys else f"0.{i} {w}" for i, w in enumerate(words)]
        reply = client.chat_json(
            "You gloss Japanese words with a short literal English meaning in capitals.",
            "Line: 犬が走る\nGloss each word:\n" + "\n".join(f'"{k}": {w}' for k, w in zip(keys, words)),
            _schema(keys, words if client.ascii_keys else None))
        missing = [k for k in keys if not str(reply.get(k, "")).strip()]
        result = {"ok": not missing, "reply": reply}
        if missing:
            result["error"] = f"no answer for {', '.join(missing)}"
    except Exception as e:  # report every failure (HTTP, key, JSON) the same way
        result = {"ok": False, "error": str(e)}
    finally:
        client.close()
    return result | {"seconds": round(time.time() - t0, 1), "usage": usage_text(client)}


def _no_length_limits(schema):
    """Claude's structured outputs reject string length limits; they are only hints here."""
    if isinstance(schema, dict):
        return {k: _no_length_limits(v) for k, v in schema.items() if k not in ("maxLength", "minLength")}
    if isinstance(schema, list):
        return [_no_length_limits(v) for v in schema]
    return schema


class AnthropicClient:
    """The Claude API with an API key (billed per token), structured outputs for the JSON."""
    ascii_keys = True

    def __init__(self, model: str, effort: str = "", api_key: str | None = None, max_tokens: int = 16000,
                 timeout: float = 600):
        try:
            import anthropic
        except ImportError:
            raise RuntimeError("the anthropic package is missing: run `uv sync --extra anthropic`") from None
        self.model, self.effort, self.max_tokens = model, effort, max_tokens
        # No key given: the SDK finds ANTHROPIC_API_KEY (or an `ant auth login` profile) itself.
        self.api = anthropic.Anthropic(api_key=api_key, timeout=timeout) if api_key else anthropic.Anthropic(timeout=timeout)
        self.usage = {"calls": 0, "input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
        self._lock = threading.Lock()

    def chat_json(self, system: str, user: str, schema: dict) -> dict:
        output_config = {"format": {"type": "json_schema", "schema": _no_length_limits(schema)}}
        if self.effort:
            output_config["effort"] = self.effort
        r = self.api.messages.create(
            model=self.model, max_tokens=self.max_tokens,
            # The rules prompt is the same in every request: cached after the first one.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}], output_config=output_config)
        with self._lock:
            self.usage["calls"] += 1
            self.usage["input"] += r.usage.input_tokens or 0
            self.usage["cache_write"] += r.usage.cache_creation_input_tokens or 0
            self.usage["cache_read"] += r.usage.cache_read_input_tokens or 0
            self.usage["output"] += r.usage.output_tokens or 0
        if r.stop_reason in ("refusal", "max_tokens"):
            raise RuntimeError(f"stopped: {r.stop_reason}")
        return json.loads(next(b.text for b in r.content if b.type == "text"))
