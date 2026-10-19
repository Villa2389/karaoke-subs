from karaoke.compare import compare, kind, norm, split


def test_norm_kind_split():
    assert norm(["CALL", "-n!"]) == "CALL-n"
    assert norm(["-GOODS"]) == "GOODS" and norm([""]) == ""
    assert [kind(s) for s in ("o-FATHER-san", "Senku", "Taiju-kun", "ga", "", "300")] == \
        ["english", "name", "name", "romaji", "empty", "english"]
    assert split("o-FATHER-san") == ("FATHER", "o-san") and split("BIG-ka") == ("BIG", "ka")


def test_compare_skips_lines_with_other_token_counts():
    run = [{"japanese": "石になった", "show": [["STONE"], ["ni"], ["BECOME", "-t"], ["-ta"]],
            "tokens": [["石", "名詞", "普通名詞", "石"], ["に", "助詞", "格助詞", "に"],
                       ["なっ", "動詞", "非自立可能", "なる"], ["た", "助動詞", "*", "た"]]},
           {"japanese": "x", "show": [["A"]], "tokens": [["x", "名詞", "*", "x"]]}]
    gold = {0: {"show": ["STONE", "ni", "BECOME-tt", "ta"]}, 1: {"show": ["A", "B"]}}
    res = compare(run, gold)
    assert res["skipped"] == [1]
    row = res["rows"][2]
    assert (row["kh"], row["kw"], split(row["have"])[0], split(row["want"])[0]) == ("english", "english", "BECOME", "BECOME")
