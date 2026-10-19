import json
import subprocess

import pytest

from karaoke import claude_code
from karaoke.claude_code import ClaudeCodeClient
from karaoke.gloss import LLMGlosser
from karaoke.reading import tokenize
from karaoke.subs import Line, clean_text


def toks(text):
    c, f = clean_text(text)
    return tokenize(Line(0, 0.0, 1.0, text, c, f))


def fake_run(reply: dict, seen: dict):
    def run(cmd, input, cwd, env, **kw):
        seen.update(cmd=cmd, input=input, cwd=cwd, env=env)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(reply), stderr="")
    return run


def test_client_uses_the_claude_login_and_returns_structured_output(monkeypatch):
    seen = {}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(claude_code.shutil, "which", lambda name: "claude")
    monkeypatch.setattr(claude_code.subprocess, "run", fake_run(
        {"is_error": False, "structured_output": {"L0_0": "STONE"}, "total_cost_usd": 0.01,
         "usage": {"input_tokens": 5, "cache_creation_input_tokens": 600, "output_tokens": 40}}, seen))
    c = ClaudeCodeClient("claude-haiku-5-5", "xhigh")
    assert c.chat_json("rules", "Line 0: 石", {"type": "object"}) == {"L0_0": "STONE"}
    cmd = seen["cmd"]
    assert cmd[cmd.index("--model") + 1] == "claude-haiku-5-5" and cmd[cmd.index("--effort") + 1] == "xhigh"
    assert cmd[cmd.index("--setting-sources") + 1] == "project"
    assert seen["input"] == "Line 0: 石"  # the prompt goes through stdin, not the command line
    assert "ANTHROPIC_API_KEY" not in seen["env"]  # subscription login, not API billing
    assert c.usage["calls"] == 1 and c.usage["cache_write"] == 600 and c.usage["output"] == 40


def test_client_raises_on_an_error_reply(monkeypatch):
    monkeypatch.setattr(claude_code.shutil, "which", lambda name: "claude")
    monkeypatch.setattr(claude_code.subprocess, "run", fake_run(
        {"is_error": True, "result": "Not logged in · Please run /login"}, {}))
    with pytest.raises(RuntimeError, match="Not logged in"):
        ClaudeCodeClient().chat_json("rules", "x", {"type": "object"})


def test_glosser_with_ascii_keys(tmp_path):
    class Fake:
        model, ascii_keys = "fake", True

        def chat_json(self, system, prompt, schema):
            self.prompt, self.schema = prompt, schema
            return {"L0_0": "STONE", "L0_2": "BECOME"}

    class Dict:
        def candidates(self, tok, n=5):
            return []

    t = toks("石になった")
    client = Fake()
    g = LLMGlosser(client, Dict(), tmp_path / "cache.json")
    assert g.gloss_batch([(0, "石になった", t)], []) == [{0: "STONE", 2: "BECOME"}]
    assert client.schema["properties"]["L0_0"]["description"] == "石"
    assert '"L0_2": なっ,' in client.prompt  # the word is shown next to its ASCII key


def test_glossary_hints_reach_the_line_prompt(tmp_path):
    from karaoke.gloss import build_glossary

    class Fake:
        model, ascii_keys = "fake", True

        def __init__(self):
            self.prompts = []

        def chat_json(self, system, prompt, schema):
            self.prompts.append(prompt)
            if "default gloss" in system:  # the glossary pass: one key per distinct word
                return {k: "STONE" if schema["properties"][k]["description"] == "石" else "BECOME"
                        for k in schema["required"]}
            return {"L0_0": "STONE", "L0_2": "BECOME"}

    class Dict:
        def candidates(self, tok, n=5):
            return []

    t = toks("石になった")
    client = Fake()
    glossary = build_glossary(client, Dict(), [("石になった", t, "turned to stone")], tmp_path / "g.json")
    assert sorted(glossary.values()) == ["BECOME", "STONE"]
    assert "turned to stone" in client.prompts[0]  # example lines carry their English
    g = LLMGlosser(client, Dict(), tmp_path / "cache.json")
    g.glossary = glossary
    assert g.gloss_batch([(0, "石になった", t)], []) == [{0: "STONE", 2: "BECOME"}]
    assert "episode gloss: STONE" in client.prompts[1]
