"""3.7.0 §C1 `thth where <account> <語> --also <語>…`。

同じ検索結果のうち、本文にどれかの語を含むものだけを残す。**追加の検索はしない**。
「25 件中 also に合ったもの 4 件」を出す。
"""
from __future__ import annotations

import json

import pytest

from thth import accounts, adapters, cli, where_cli


def _setup(isolated_account_factory, monkeypatch, texts, name="one", media="threads"):
    isolated_account_factory(name, media=media)
    monkeypatch.setattr(accounts, "load_token", lambda cfg: {"access_token": "FAKE"})
    calls = []

    class Fake:
        def keyword_search(self, word, **kwargs):
            calls.append(word)
            return [dict(message_id=f"p{i}", author_key=f"{i:016x}", username=f"u{i}",
                         timestamp="2026-09-09T00:00:00Z", text=text)
                    for i, text in enumerate(texts)]

        def tag_search(self, *a, **kw):
            return {"n": 0}

        def tag_observation(self, *a, **kw):
            return {"n": 0}
    monkeypatch.setattr(adapters, "make_adapter", lambda *a: Fake())
    return calls


def test_alsoは同じ検索結果を絞るだけで追加の検索をしない(isolated_account_factory, monkeypatch, capsys):
    texts = ["コーヒー豆の話", "紅茶もいい", "コーヒーと紅茶", "何でもない", "COFFEE and Tea"]
    calls = _setup(isolated_account_factory, monkeypatch, texts)
    assert cli.main(["where", "one", "飲み物", "--also", "紅茶", "--also", "tea", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert calls == ["飲み物"], "also の語で検索しない"
    entry = result["by_account"]["one"]["by_word"]["飲み物"]
    assert [p["post_id"] for p in entry["posts"]] == ["p1", "p2", "p4"]
    assert entry["also"] == {"words": ["紅茶", "tea"], "n_before": 5, "n_matched": 3,
                             "basis": "text_contains_any_casefold", "additional_search": False}
    assert entry["material"]["n"] == 3
    assert result["also"] == ["紅茶", "tea"]


def test_alsoの件数を画面に出す(isolated_account_factory, monkeypatch, capsys):
    _setup(isolated_account_factory, monkeypatch, ["紅茶"] * 4 + ["珈琲"] * 21)
    assert cli.main(["where", "one", "飲み物", "--also", "紅茶"]) == 0
    out = capsys.readouterr().out
    assert "25 件中 also に合ったもの 4 件（追加の検索はしていません）" in out


def test_alsoを使わなければ従前の形(isolated_account_factory, monkeypatch, capsys):
    _setup(isolated_account_factory, monkeypatch, ["a", "b"])
    assert cli.main(["where", "one", "語", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert "also" not in result and "also" not in result["by_account"]["one"]["by_word"]["語"]


@pytest.mark.parametrize("also", [[""], ["a"] * 6, "紅茶"])
def test_alsoの形を検査する(also):
    with pytest.raises(where_cli.WhereError):
        where_cli._clean_also(also)
