import argparse
import io
import json

import pytest

from karaoke import llm
from karaoke.align import AlignedLine
from karaoke.cli import resolve_glossers
from karaoke.dictionary import Dictionary
from karaoke.llm import OpenAIClient, parse_json
from karaoke.providers import _no_length_limits, get_profile, load_config, make_client
from karaoke.reading import tokenize
from karaoke.review import Row
from karaoke.subs import Line, clean_text
from karaoke.ui import time_args
from karaoke.verify import Verifier

CONFIG = """
default = "gpt"

[profiles.gpt]
kind = "openai"
base_url = "https://api.example.com/v1"
api_key_env = "TEST_KARAOKE_KEY"
model = "some-model"
temperature = false

[profiles.haiku]
effort = "high"

[profiles.ollama]
kind = "openai"
base_url = "http://127.0.0.1:11434/v1"
model = "qwen3:8b"
json_mode = "object"
extra = { think = false }
"""


@pytest.fixture
def config(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(CONFIG, encoding="utf-8")
    return p


def test_config_adds_profiles_and_overrides_built_ins(config):
    profiles, default = load_config(config)
    assert default == "gpt"
    gpt = profiles["gpt"]
    assert (gpt.kind, gpt.batch, gpt.jobs, gpt.temperature, gpt.cache_id) == ("openai", 10, 2, None, "some-model")
    # a built-in keeps what the file doesn't change
    assert (profiles["haiku"].model, profiles["haiku"].effort, profiles["haiku"].batch) == ("claude-haiku-5-5", "high", 20)
    assert profiles["ollama"].extra == {"think": False} and profiles["local"].kind == "llama-server"
    assert profiles["local"].cache_id == "Qwen3.5-9B-Q4_K_M"  # same cache files as before profiles


@pytest.mark.parametrize("bad", ['[profiles.x]\nkind = "gpt"\n', '[profiles.x]\nkind = "openai"\nmodel = "m"\n',
                                 '[profiles.haiku]\nefort = "low"\n', 'default = "nope"\n'])
def test_config_mistakes_are_reported(tmp_path, bad):
    p = tmp_path / "c.toml"
    p.write_text(bad, encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(p)


def test_api_key_comes_from_the_environment(config, monkeypatch):
    monkeypatch.delenv("TEST_KARAOKE_KEY", raising=False)
    p = get_profile("gpt", config)
    assert p.missing_key() == "TEST_KARAOKE_KEY"
    with pytest.raises(RuntimeError, match="TEST_KARAOKE_KEY"):
        make_client(p)
    monkeypatch.setenv("TEST_KARAOKE_KEY", "sk-test")
    assert make_client(p).api_key == "sk-test"


def test_openai_client_request_and_reply(monkeypatch):
    seen = {}

    def urlopen(req, timeout):
        seen["url"], seen["auth"] = req.full_url, req.get_header("Authorization")
        seen["body"] = json.loads(req.data)
        reply = {"choices": [{"message": {"content": '```json\n{"0.0 犬": "DOG"}\n```'}}],
                 "usage": {"prompt_tokens": 50, "completion_tokens": 5}}
        return io.BytesIO(json.dumps(reply).encode())

    monkeypatch.setattr(llm.urllib.request, "urlopen", urlopen)
    c = OpenAIClient("https://api.example.com/v1/", "m", "sk-test", temperature=None, extra={"seed": 1})
    schema = {"type": "object", "properties": {"0.0 犬": {"type": "string"}}}
    assert c.chat_json("rules", "line", schema) == {"0.0 犬": "DOG"}  # fenced JSON still parsed
    assert seen["url"] == "https://api.example.com/v1/chat/completions" and seen["auth"] == "Bearer sk-test"
    body = seen["body"]
    assert body["response_format"]["json_schema"]["schema"] == schema and "temperature" not in body
    assert body["seed"] == 1 and c.usage == {"calls": 1, "input": 50, "output": 5}
    # servers without json_schema: the schema goes into the prompt
    body = OpenAIClient("http://x/v1", "m", json_mode="object").body("rules", "line", schema)
    assert body["response_format"] == {"type": "json_object"} and "0.0 犬" in body["messages"][1]["content"]


def test_parse_json_and_schema_cleanup():
    assert parse_json('Sure! {"a": "B"} hope it helps') == {"a": "B"}
    with pytest.raises(ValueError):
        parse_json("no json")
    schema = {"type": "object", "properties": {"k": {"type": "string", "maxLength": 40}}}
    assert _no_length_limits(schema) == {"type": "object", "properties": {"k": {"type": "string"}}}


def args(**kw):
    base = dict(gloss="", review=False, model=None, config=None, claude_model="claude-haiku-5-5",
                claude_effort="xhigh", claude_batch=20, claude_jobs=4, batch=None, jobs=None, llm_model=None)
    return argparse.Namespace(**base | kw)


def test_gloss_names_resolve_to_profiles(config, monkeypatch):
    monkeypatch.setenv("TEST_KARAOKE_KEY", "sk-test")
    got = resolve_glossers(args(gloss="dict,llm,claude,ollama", config=str(config), batch=7))
    assert [(n, p and p.name) for n, p in got] == [("dict", None), ("gpt", "gpt"), ("claude", "claude"),
                                                   ("ollama", "ollama")]
    assert got[1][1].batch == 7 and got[2][1].batch == 20  # --batch is for profiles, not `claude`
    # no config: llm is the local Qwen and keeps its old file name
    gguf = config.with_name("qwen.gguf")
    gguf.write_bytes(b"")
    [(name, p)] = resolve_glossers(args(gloss="llm", config=str(config.with_name("none.toml")), llm_model=str(gguf)))
    assert (name, p.kind, p.model_path()) == ("llm", "llama-server", gguf)
    with pytest.raises(SystemExit):  # model file missing: say so before the timing work
        resolve_glossers(args(gloss="llm", config=str(config.with_name("none.toml")), llm_model=str(gguf) + "x"))
    monkeypatch.delenv("TEST_KARAOKE_KEY")
    with pytest.raises(SystemExit):  # before any audio work
        resolve_glossers(args(gloss="gpt", config=str(config)))


def test_ui_passes_the_model_profile(tmp_path):
    media, subs = tmp_path / "e.mkv", tmp_path / "e.srt"
    media.write_bytes(b"")
    subs.write_text("")
    assert time_args({"media": str(media), "subs": str(subs), "gloss": ["llm"], "model": "ollama"}) == [
        "time", str(media), str(subs), "--gloss", "llm", "--model", "ollama"]


def test_verifier_uses_ascii_keys_for_claude(tmp_path):
    text = "ひでえがな"
    c, f = clean_text(text)
    al = AlignedLine(Line(3, 0, 1, text, c, f), tokenize(Line(3, 0, 1, text, c, f)), "ctc", 1.0)
    row = Row(3, 0.0, 7, "ひでえ", "hidee", "CRUEL", "AWESOME", "unusual", ["CRUEL", "TERRIBLE"], c)

    class Client:
        model, ascii_keys = "claude", True

        def chat_json(self, system, user, schema):
            assert list(schema["properties"]) == ["T7"] and schema["properties"]["T7"]["description"] == "ひでえ"
            assert '"T7" ひでえ' in user
            return {"T7": {"analysis": "terrible", "gloss": "terrible"}}

    glosses = [{7: "AWESOME"}]
    Verifier(Client(), Dictionary.__new__(Dictionary), tmp_path / "v.json").run([al], glosses, [row])
    assert glosses[0][7] == "TERRIBLE"
