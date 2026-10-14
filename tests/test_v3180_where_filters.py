"""3.18.0 A5 `thth where … --ja --question --no-replies`（表示の絞り込み・保存に触らない）。

- 日本語だけ: X は query に `lang:ja` を足す（語に `lang:` があれば重ねない・手元では絞らない）。
  他の媒体は本文にひらがなかカタカナがある行だけ。
- 「？」のあるものだけ: 検索語を外した本文に「？」か「?」（返信を求めているかは見ない・10/15）。
- 返信の付いていないものだけ: 返信の数が無いか 0。Threads は `has_replies` が真のものを外す。
- 絞った数は `dropped` に（付けた絞り込みの鍵だけ）。
"""
from __future__ import annotations

import pytest

from thth import accounts, adapters, cli, jst, where_cli
from thth.adapters import x as x_adapter

NOW = jst.parse("2026-10-13T09:00:00+09:00")
STAMP = "2026-10-12T20:00:00Z"


def _row(i, text, **extra):
    return {"message_id": f"{1900000000000000000 + i}", "author_key": f"{i:016x}",
            "username": f"user{i}", "timestamp": STAMP, "text": text, **extra}


ROWS = [_row(1, "珈琲はどれがいい？"), _row(2, "coffee is great?"), _row(3, "珈琲が好き"),
        _row(4, "珈琲の豆は?", has_replies=True), _row(5, "珈琲豆", reply_count=3),
        _row(6, "珈琲？", reply_count=0)]


@pytest.fixture
def threads_account(isolated_account_factory, monkeypatch, thth_root):
    isolated_account_factory("one", media="threads", project="cafe")
    monkeypatch.setattr(accounts, "load_token", lambda cfg: {"access_token": "FAKE"})
    calls = []

    class Fake:
        def keyword_search(self, word, **kwargs):
            calls.append(word)
            return [dict(row) for row in ROWS]
    monkeypatch.setattr(adapters, "make_adapter", lambda *a: Fake())
    return calls


@pytest.fixture
def x_account(isolated_account_factory, monkeypatch, thth_root):
    isolated_account_factory("xa", media="x", project="cafe")
    monkeypatch.setattr(accounts, "load_token", lambda cfg: {"access_token": "FAKE"})
    calls = []

    class Fake:
        def keyword_search(self, word, **kwargs):
            calls.append(word)
            return [dict(row, medium="x") for row in ROWS]
    monkeypatch.setattr(adapters, "make_adapter", lambda *a: Fake())
    return calls


def _ids(result, account, word="珈琲"):
    return [p["post_id"][-1] for p in result["by_account"][account]["by_word"][word]["posts"]]


def test_絞り込みを付けなければ今までどおり(threads_account):
    result = where_cli.answer(account_name="one", words=["珈琲"], now=NOW)
    entry = result["by_account"]["one"]["by_word"]["珈琲"]
    assert len(entry["posts"]) == 6 and "filters" not in result
    assert set(entry["dropped"]) == {"since", "exclude_engaged", "max_per_author"}


def test_日本語だけ_手元でかなのある行だけ(threads_account):
    result = where_cli.answer(account_name="one", words=["珈琲"], ja=True, now=NOW)
    # 2（英語）と 5・6（漢字と約物だけ——かなが無いので日本語と言えない）を外す。
    assert _ids(result, "one") == ["1", "3", "4"]
    assert result["by_account"]["one"]["by_word"]["珈琲"]["dropped"]["ja"] == 3
    assert threads_account == ["珈琲"]
    assert "ja" in result["filters"]


def test_問いかけだけ(threads_account):
    result = where_cli.answer(account_name="one", words=["珈琲"], question=True, now=NOW)
    assert _ids(result, "one") == ["1", "2", "4", "6"]
    assert result["by_account"]["one"]["by_word"]["珈琲"]["dropped"]["question"] == 2


def test_返信の付いていないものだけ_has_repliesも見る(threads_account):
    result = where_cli.answer(account_name="one", words=["珈琲"], no_replies=True, now=NOW)
    assert _ids(result, "one") == ["1", "2", "3", "6"]
    assert result["by_account"]["one"]["by_word"]["珈琲"]["dropped"]["no_replies"] == 2


def test_組み合わせとCLIの旗(threads_account, capsys):
    assert cli.main(["where", "one", "珈琲", "--ja", "--question", "--no-replies"]) == 0
    out = capsys.readouterr().out
    assert "絞り込み: 日本語だけ" in out and "絞り込み: 「？」のあるものだけ" in out and "返信を求めているかは見ていない" in out
    assert "珈琲はどれがいい？" in out and "coffee" not in out and "珈琲の豆は" not in out


def test_Xの日本語だけはqueryにlang_jaを足し手元では絞らない(x_account):
    result = where_cli.answer(account_name="xa", words=["珈琲", "豆 lang:ja", "茶 -lang:en"],
                              ja=True, now=NOW)
    assert x_account == ["珈琲 lang:ja", "豆 lang:ja", "茶 -lang:en"]
    entry = result["by_account"]["xa"]["by_word"]["珈琲"]
    assert entry["query_sent"] == "珈琲 lang:ja"
    assert entry["dropped"]["ja"] is None and len(entry["posts"]) == 6
    # 自分の履歴・画面の語は元の語のまま。
    assert list(result["by_account"]["xa"]["by_word"]) == ["珈琲", "豆 lang:ja", "茶 -lang:en"]


def test_Xの日本語だけを付けなければqueryは変えない(x_account):
    where_cli.answer(account_name="xa", words=["珈琲"], now=NOW)
    assert x_account == ["珈琲"]


def test_Xの返信の数はpublic_metricsのreply_countから():
    assert x_adapter.XAdapter._reply_count({"public_metrics": {"reply_count": 2}}) == 2
    assert x_adapter.XAdapter._reply_count({"public_metrics": {"reply_count": True}}) is None
    assert x_adapter.XAdapter._reply_count({}) is None


def test_絞り込みとkeepの行には符丁だけが乗る(threads_account):
    from thth import where_keep
    where_cli.answer(account_name="one", words=["珈琲"], also=["珈琲"], ja=True, keep=True, now=NOW)
    rows, _ = where_keep.read("cafe")
    assert [r["filters"] for r in rows] == [["ja"]]
