"""3.7.0 §C3 `thth where <account> <語…> --aggregate [--lexicon <file>]`。

語ごとの件数・期間・1 日あたり・問いの形・lexicon の分類ごとの件数だけを返す。
**本文・username・author_key・post_id は返さない**。検索語は数える前に本文から外す。
**表示だけで、道具は保存しない**。出力に「保存するなら Meta への説明の申請のあとで
（世間の層と同じ条件）」を固定で添える。
"""
from __future__ import annotations

import json
import os

import pytest

from thth import accounts, adapters, cli, runs, where_cli

ROWS = [
    {"message_id": "POSTID-111", "author_key": "a1a1a1a1a1a1a1a1", "username": "USERNAME-A",
     "timestamp": "2026-09-01T00:00:00Z", "text": "BODYTEXT-1 苦い珈琲の淹れ方は？"},
    {"message_id": "POSTID-222", "author_key": "b2b2b2b2b2b2b2b2", "username": "USERNAME-B",
     "timestamp": "2026-09-05T00:00:00Z", "text": "BODYTEXT-2 珈琲？ 豆が甘い"},
    {"message_id": "POSTID-333", "author_key": "c3c3c3c3c3c3c3c3", "username": "USERNAME-C",
     "timestamp": "2026-09-09T00:00:00Z", "text": "BODYTEXT-3 酸っぱい珈琲"},
]
SECRETS = ("BODYTEXT", "USERNAME", "POSTID", "a1a1a1a1a1a1a1a1", "b2b2b2b2b2b2b2b2",
           "c3c3c3c3c3c3c3c3", "淹れ方", "豆が")


@pytest.fixture
def account(isolated_account_factory, monkeypatch, thth_root):
    isolated_account_factory("one", media="threads")
    monkeypatch.setattr(accounts, "load_token", lambda cfg: {"access_token": "FAKE"})
    calls = []

    class Fake:
        def keyword_search(self, word, **kwargs):
            calls.append(word)
            return [dict(row) for row in ROWS]
    monkeypatch.setattr(adapters, "make_adapter", lambda *a: Fake())
    return {"calls": calls, "root": thth_root}


def _snapshot(root):
    out = {}
    for base, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(base, name)
            with open(path, "rb") as f:
                out[path] = f.read()
    return out


def _lexicon(tmp_path):
    path = tmp_path / "lexicon.json"
    path.write_text(json.dumps({"味": ["苦い", "甘い", "酸っぱい"], "問い": ["淹れ方", "珈琲"]},
                               ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_aggregateは数と時刻だけ返し本文もusernameもpost_idも返さない(account, tmp_path, capsys):
    assert cli.main(["where", "one", "珈琲", "--aggregate", "--lexicon", _lexicon(tmp_path),
                     "--json"]) == 0
    raw = capsys.readouterr().out
    for secret in SECRETS:
        assert secret not in raw, secret
    result = json.loads(raw)
    assert result["mode"] == "aggregate" and result["storage"] == "display_only"
    assert result["storage_note"] == "保存するなら Meta への説明の申請のあとで（世間の層と同じ条件）"
    node = result["by_account"]["one"]
    assert set(node) == {"medium", "mode", "by_word", "by_tag", "cannot_say"}
    agg = node["by_word"]["珈琲"]["aggregate"]
    assert agg["n"] == 3
    assert agg["period"]["span_days"] == 8.0 and agg["per_day"] == 0.38
    assert agg["question_forms"]["ends_with_question_mark"] == 1
    assert agg["question_forms"]["contains_question_mark"] == 2
    assert agg["lexicon"]["味"] == {"n_posts": 3, "denominator": 3}
    # 検索語「珈琲」は外してから数える——「問い」の分類に「珈琲」を入れても数えない。
    assert agg["lexicon"]["問い"] == {"n_posts": 1, "denominator": 3}
    assert agg["search_words_removed"] is True


def test_aggregateの画面も本文を出さず保存の注意を添える(account, tmp_path, capsys):
    assert cli.main(["where", "one", "珈琲", "--aggregate"]) == 0
    out = capsys.readouterr().out
    for secret in SECRETS:
        assert secret not in out, secret
    assert "件数=3" in out
    assert "表示だけで、道具は保存しません。保存するなら Meta への説明の申請のあとで（世間の層と同じ条件）" in out


def test_aggregateは保存しない_runsは語と件数の1行だけ(account, tmp_path, capsys):
    lexicon = _lexicon(tmp_path)
    before = _snapshot(account["root"])
    assert cli.main(["where", "one", "珈琲", "--aggregate", "--lexicon", lexicon, "--json"]) == 0
    after = _snapshot(account["root"])
    # 退出の門のロック（`state/_leave/<account>.lock`）は集計ではないので除く。
    changed = {path for path in after if before.get(path) != after[path]
               and not path.endswith(".lock")}
    state = accounts.state_dir_for("one")
    # 変わってよいのは state/one/runs-*.ndjson（語と件数の 1 行）だけ。data/ にも集計は残らない。
    assert changed and all(os.path.basename(path).startswith("runs-")
                           and path.split(os.sep)[-3:-1] == ["state", "one"]
                           for path in changed), sorted(p.split(os.sep)[-3:] for p in changed)
    row = runs.read_runs(state)[-1]
    assert row["action"] == "where_to_appear" and row["n"] == 3
    text = json.dumps(row, ensure_ascii=False)
    for secret in SECRETS + ("味", "lexicon", "question"):
        assert secret not in text, secret
    assert account["calls"] == ["珈琲"]


def test_lexiconはaggregateと一緒にだけ_形を検査する(account, tmp_path, capsys):
    assert cli.main(["where", "one", "珈琲", "--lexicon", _lexicon(tmp_path)]) == 2
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"味": []}), encoding="utf-8")
    assert cli.main(["where", "one", "珈琲", "--aggregate", "--lexicon", str(bad)]) == 2
    with pytest.raises(where_cli.WhereError):
        where_cli.load_lexicon(str(tmp_path / "無い.json"))
