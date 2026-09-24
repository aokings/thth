"""3.9.0 §C `--aggregate` の速さ（`rate_by_span`・`rough`・`more_may_exist`）。

- `per_day = n / max(期間の日数, 1)` は、期間が 1 日より短いとどの語も「n 件／日」に揃って
  見える。`rate_by_span` は返った投稿の期間（最古〜最新）の日数で割る（底上げしない）。
  分母（n・期間の時間）を添え、n < 20 は `rough: true`。
- 返った件数が要求数に届かず、期間が 24 時間未満で n が 5 以上なら
  `cannot_say: more_may_exist`（これで全部か、もっとあるかは区別できない）。
"""
from __future__ import annotations

import datetime
import json

import pytest

from thth import accounts, adapters, cli, jst, where_cli

BASE = jst.parse("2026-09-24T06:00:00+09:00")


def _rows(n, hours, base=BASE):
    step = hours / (n - 1) if n > 1 else 0
    return [{"message_id": f"m{i}", "author_key": f"{i:016x}", "username": f"u{i}",
             "timestamp": jst.iso(base + datetime.timedelta(hours=step * i)), "text": "本文"}
            for i in range(n)]


@pytest.fixture
def search(isolated_account_factory, monkeypatch):
    isolated_account_factory("one", media="threads")
    monkeypatch.setattr(accounts, "load_token", lambda cfg: {"access_token": "FAKE"})
    table = {}

    class Fake:
        def keyword_search(self, word, **kwargs):
            return [dict(row) for row in table[word]]
    monkeypatch.setattr(adapters, "make_adapter", lambda *a: Fake())
    return table


def _agg(result, word):
    return result["by_account"]["one"]["by_word"][word]["aggregate"]


def test_rate_by_spanは期間そのもので割る_per_dayと揃わない(search):
    search["にぎやか"] = _rows(10, 6)      # 6 時間に 10 件
    search["静か"] = _rows(10, 60)         # 60 時間に 10 件
    result = where_cli.answer(account_name="one", words=["にぎやか", "静か"], aggregate_mode=True,
                              now=BASE + datetime.timedelta(days=3))
    busy, calm = _agg(result, "にぎやか"), _agg(result, "静か")
    assert busy["per_day"] == 10.0, "従前の per_day は 1 日で底上げ"
    assert busy["rate_by_span"] == {"value": 40.0, "unit": "per_day", "n": 10, "span_hours": 6.0,
                                    "basis": "n ÷ 返った投稿の期間（最古〜最新）の日数",
                                    "rough": True, "rough_below_n": 20, "reason": None}
    assert calm["rate_by_span"]["value"] == 4.0 and calm["rate_by_span"]["span_hours"] == 60.0
    assert calm["per_day"] == 4.0


def test_roughはn20未満_期間が0や時刻なしはnullと理由():
    assert where_cli.rate_by_span(20, [BASE, BASE + datetime.timedelta(hours=48)])["rough"] is False
    same = where_cli.rate_by_span(3, [BASE, BASE])
    assert same["value"] is None and same["reason"] == "span_zero"
    none = where_cli.rate_by_span(0, [])
    assert none["value"] is None and none["reason"] == "no_timestamp" and none["span_hours"] is None


def test_要求数に届かず期間が短いとmore_may_exist(search):
    search["短い"] = _rows(10, 6)
    search["長い"] = _rows(10, 30)
    search["少ない"] = _rows(4, 2)
    result = where_cli.answer(account_name="one", words=["短い", "長い", "少ない"], limit=25,
                              aggregate_mode=True, now=BASE + datetime.timedelta(days=3))
    (item,) = _agg(result, "短い")["cannot_say"]
    assert item["code"] == "more_may_exist"
    assert item["message"] == "これで全部か、もっとあるかは区別できません"
    assert "一次資料で未確認" in item["unverified"]
    assert item["n_returned"] == 10 and item["requested_limit"] == 25 and item["span_hours"] == 6.0
    assert _agg(result, "長い")["cannot_say"] == [], "期間が 24 時間以上"
    assert _agg(result, "少ない")["cannot_say"] == [], "n が 5 未満"
    full = where_cli.answer(account_name="one", words=["短い"], limit=10, aggregate_mode=True,
                            now=BASE + datetime.timedelta(days=3))
    assert _agg(full, "短い")["cannot_say"] == [], "要求数に届いている"


def test_画面に期間あたりと分母とmore_may_exist(search, capsys):
    search["短い"] = _rows(6, 5, base=jst.now_jst() - datetime.timedelta(hours=6))
    assert cli.main(["where", "one", "短い", "--aggregate"]) == 0
    out = capsys.readouterr().out
    assert "期間あたり=28.8/日（n=6・期間 5.0 時間" in out and "粗い（n<20）" in out
    assert "これで全部か、もっとあるかは区別できません（more_may_exist" in out
    assert cli.main(["where", "one", "短い", "--aggregate", "--json"]) == 0
    agg = json.loads(capsys.readouterr().out)["by_account"]["one"]["by_word"]["短い"]["aggregate"]
    assert agg["rate_by_span"]["n"] == 6 and agg["rate_by_span"]["span_hours"] == 5.0
