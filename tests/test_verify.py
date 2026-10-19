from karaoke.align import AlignedLine
from karaoke.dictionary import Dictionary
from karaoke.reading import tokenize
from karaoke.review import Row
from karaoke.subs import Line, clean_text
from karaoke.verify import Verifier


class FakeClient:
    model = "fake"

    def __init__(self, answer):
        self.answer = answer
        self.calls = 0

    def chat_json(self, system, user, schema):
        self.calls += 1
        assert list(schema["properties"]) == ["7 ひでえ"]  # one key per flagged word
        return self.answer


def test_verifier_replaces_flagged_gloss_and_caches(tmp_path):
    text = "ひでえがな"
    c, f = clean_text(text)
    toks = tokenize(Line(3, 0, 1, text, c, f))
    al = AlignedLine(Line(3, 0, 1, text, c, f), toks, "ctc", 1.0)
    row = Row(3, 0.0, 7, "ひでえ", "hidee", "CRUEL", "AWESOME", "unusual", ["CRUEL", "TERRIBLE"], c)
    client = FakeClient({"7 ひでえ": {"analysis": "ひでえ = ひどい, terrible", "gloss": "terrible"}})
    v = Verifier(client, Dictionary.__new__(Dictionary), tmp_path / "v.json")
    glosses = [{7: "AWESOME"}]
    [d] = v.run([al], glosses, [row])
    assert (d.before, d.after) == ("AWESOME", "TERRIBLE") and glosses[0][7] == "TERRIBLE"
    # second run: answered from the cache, no model call
    v2 = Verifier(client, Dictionary.__new__(Dictionary), tmp_path / "v.json")
    v2.run([al], [{7: "AWESOME"}], [row])
    assert client.calls == 1


def test_sense_doubt_cannot_lengthen_a_real_word(tmp_path):
    text = "ずっと"
    c, f = clean_text(text)
    ln = Line(5, 0, 1, text, c, f)
    al = AlignedLine(ln, tokenize(ln), "ctc", 1.0)
    row = Row(5, 0.0, 0, "ずっと", "zutto", "CONTINUOUSLY", "ALWAYS", "unusual", ["CONTINUOUSLY"], c)

    class Client:
        model = "fake"

        def chat_json(self, system, user, schema):
            return {"0 ずっと": {"analysis": "dictionary says continuously", "gloss": "CONTINUOUSLY"}}

    glosses = [{0: "ALWAYS"}]
    Verifier(Client(), Dictionary.__new__(Dictionary), tmp_path / "v.json").run([al], glosses, [row])
    assert glosses[0][0] == "ALWAYS"
