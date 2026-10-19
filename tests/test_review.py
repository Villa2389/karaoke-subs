from karaoke.align import AlignedLine
from karaoke.reading import tokenize
from karaoke.review import apply_fixes, load_fixes, verdict, write_review, Row
from karaoke.subs import Line, clean_text


def test_verdicts():
    cands = ["HUMAN-BEING", "HUMAN", "CHARACTER"]
    vocab = {"HUMAN", "HUMANS", "BEING", "CHARACTER", "PEOPLE", "COMPLETELY", "COMPLETE"}
    assert verdict("HUMANS", cands, vocab=vocab) == "agree"
    assert verdict("CHARACTER", cands, vocab=vocab) == "agree"  # another sense is fine
    assert verdict("COMPLETELY", ["COMPLETE"], vocab=vocab) == "agree"  # near form
    assert verdict("PEOPLE", cands, vocab=vocab) == "unusual"
    assert verdict("BOOTHER", ["BOTHER"], vocab=vocab) == "nonword"
    assert verdict("", cands) == "kept"
    assert verdict("SENKU", []) == "agree"  # nothing to compare against: don't flag


def test_number_disagreement():
    tok = tokenize(Line(0, 0, 1, "億", "億", []))[0]
    assert verdict("BILLION", ["HUNDRED-MILLION"], tok) == "number"
    assert verdict("HUNDRED-MILLION", ["HUNDRED-MILLION"], tok) == "agree"


def test_review_roundtrip(tmp_path):
    text = "石になった"
    c, f = clean_text(text)
    ln = Line(7, 1.0, 2.0, text, c, f)
    toks = tokenize(ln)
    rows = [Row(7, 1.0, 0, "石", "ishi", "STONE", "ROCK-SOLID", "unusual", ["STONE"], c),
            Row(7, 1.2, 2, "なっ", "nat", "BECOME", "BECOME", "agree", ["BECOME"], c)]
    path = tmp_path / "r.tsv"
    assert write_review(rows, path)[:2] == (1, 1)
    # the user fills in the fix column for the first row
    data = path.read_text(encoding="utf-8-sig").splitlines()
    data[1] = data[1] + "STONE"
    path.write_text("\n".join(data) + "\n", encoding="utf-8-sig")
    glosses = [{0: "ROCK-SOLID", 2: "BECOME"}]
    assert apply_fixes([AlignedLine(ln, toks, "ctc", 1.0)], glosses, load_fixes(path)) == 1
    assert glosses[0][0] == "STONE"


def test_numbers_checked_by_value_not_dictionary():
    one = tokenize(Line(0, 0, 1, "一", "一", []))[0]
    assert verdict("ONE", ["BEST"], one) == "agree"  # JMdict's first sense of 一 is BEST
    hundred = tokenize(Line(0, 0, 1, "百", "百", []))[0]
    assert verdict("ONE", ["HUNDRED"], hundred) == "number"


def test_name_written_as_romaji_is_fine():
    tok = tokenize(Line(0, 0, 1, "大樹", "大樹", []))[0]
    tok.romaji = "taijuu"
    assert verdict("TAIJU", ["LARGE-TREE"], tok, {"LARGE", "TREE"}) == "agree"
    assert verdict("TAIKI", ["LARGE-TREE"], tok, {"LARGE", "TREE"}) == "nonword"


def test_long_and_name_verdicts():
    vocab = {"PUTTING", "ON", "THE", "MARKET", "STORE", "BOUGHT", "COMMERCIAL"}
    assert verdict("PUTTING-ON-THE-MARKET", ["PUTTING-ON-THE-MARKET"], vocab=vocab) == "long"
    assert verdict("STORE-BOUGHT", ["PUTTING-ON-THE-MARKET", "COMMERCIAL"], vocab=vocab) == "unusual"
    assert verdict("@", ["FALSE-DAPHNE"]) == "agree"  # declared a name
