"""Claude as the glosser, through Claude Code's headless mode (`claude -p`).

Runs on the user's own Claude login (subscription): no API key, no separate bill; it counts
against the plan's usage limits. Same interface as llm.Client (`model`, `chat_json`), so the
glosser, rules prompt, caching and fallbacks are shared.

- Structured output is a tool for Claude Code, whose property names must be ASCII: keys are
  "L<line>_<token>" with the Japanese word in the description (`ascii_keys`).
- The prompt goes through stdin and the system prompt through a file (Windows command lines
  are limited to 32k characters).
- Calls run in an empty folder with project settings only: no CLAUDE.md, plugins or hooks
  in every request (~600 instead of ~2,700 tokens of overhead).
- ANTHROPIC_API_KEY is removed from the environment so the Claude login is used, not API billing.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

DEFAULT_MODEL = "claude-haiku-5-5"  # the `haiku` alias may still point at an older Haiku
DEFAULT_EFFORT = "xhigh"


class ClaudeCodeClient:
    ascii_keys = True

    def __init__(self, model: str = DEFAULT_MODEL, effort: str = DEFAULT_EFFORT, timeout: float = 900):
        self.model, self.effort, self.timeout = model, effort, timeout
        self.exe = shutil.which("claude")
        self.workdir = Path(tempfile.mkdtemp(prefix="karaoke-claude-"))
        self.usage = {"calls": 0, "input": 0, "cache_write": 0, "cache_read": 0, "output": 0, "usd": 0.0}
        self._lock = threading.Lock()

    def command(self, system_file: Path, schema: dict) -> list[str]:
        return [self.exe or "claude", "-p", "--model", self.model, "--effort", self.effort,
                "--setting-sources", "project", "--no-session-persistence", "--output-format", "json",
                "--tools", "", "--system-prompt-file", str(system_file),
                "--json-schema", json.dumps(schema, ensure_ascii=False)]

    def chat_json(self, system: str, user: str, schema: dict) -> dict:
        if not self.exe:
            raise RuntimeError("the `claude` command is not on PATH (install Claude Code and run `claude` -> /login)")
        system_file = self.workdir / f"system-{hashlib.sha1(system.encode()).hexdigest()[:12]}.txt"
        if not system_file.exists():
            system_file.write_text(system, encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        r = subprocess.run(self.command(system_file, schema), input=user, capture_output=True, text=True,
                           encoding="utf-8", cwd=self.workdir, env=env, timeout=self.timeout)
        try:
            reply = json.loads(r.stdout)
        except json.JSONDecodeError:
            raise RuntimeError(f"claude exited {r.returncode}: {(r.stderr or r.stdout)[-300:]}") from None
        self._count(reply)
        if reply.get("is_error") or not isinstance(reply.get("structured_output"), dict):
            raise RuntimeError(str(reply.get("result"))[:300])
        return reply["structured_output"]

    def _count(self, reply: dict) -> None:
        u = reply.get("usage") or {}
        with self._lock:
            self.usage["calls"] += 1
            self.usage["input"] += u.get("input_tokens", 0)
            self.usage["cache_write"] += u.get("cache_creation_input_tokens", 0)
            self.usage["cache_read"] += u.get("cache_read_input_tokens", 0)
            self.usage["output"] += u.get("output_tokens", 0)
            self.usage["usd"] += reply.get("total_cost_usd") or 0.0
