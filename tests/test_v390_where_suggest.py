"""3.9.0 §B 検索の語の候補（`thth where … --also … --suggest`）。

- `--also` と一緒なら語ごとの「取れた件数・also に合った件数・当たり率」を当たり率の順に。
  合った件数 0 の語に「also に合った投稿が 0 件（直近 n 日）」。
- `--suggest`: also に合った投稿の本文から語の候補を上位 10 まで（漢字・カタカナ・英字の
  2〜12 字の連なり・検索語と also の語と数字だけの語を除く・2 投稿以上）。
  **本文・username・post_id・author_key は出さない**。**保存しない**。
- 自分の台帳からの候補（地図の点・原稿の topic・反応の多かった topic）を先頭に。
"""
from __future__ import annotations

import datetime
import json
import os

import pytest

from tests.conftest import write_queue_file
from tests.test_after_cli import _insight_path, _write_ndjson
from thth import accounts, adapters, cli, jst, map_store, runs, where_cli, where_suggest

NOW = jst.parse("2026-09-24T12:00:00+09:00")
STAMP = "2026-09-23T00:00:00Z"
LONG = "スペシャルティコーヒーロースター"   # 16 字——12 字を超える連なりは語にしない

COFFEE = [
    {"message_id": "POSTID111", "author_key": "a1a1a1a1a1a1a1a1", "username": "roaster",
     "timestamp": STAMP,
     "text": f"深煎りの珈琲豆をハンドドリップで淹れた {LONG} https://x.example/beans"},
    {"message_id": "POSTID222", "author_key": "b2b2b2b2b2b2b2b2", "username": "USERNAMEB",
     "timestamp": STAMP, "text": f"珈琲豆は深煎りが好き。ハンドドリップ最高 2026 {LONG}"},
    {"message_id": "POSTID333", "author_key": "c3c3c3c3c3c3c3c3", "username": "USERNAMEC",
     "timestamp": STAMP, "text": "浅煎りの豆もハンドドリップで。roasterの店 2026 @USERNAMEB"},
    {"message_id": "POSTID444", "author_key": "d4d4d4d4d4d4d4d4", "username": "USERNAMED",
     "timestamp": STAMP, "text": "紅茶の話"},
]
TEA = [
    {"message_id": "POSTID444", "author_key": "d4d4d4d4d4d4d4d4", "username": "USERNAMED",
     "timestamp": STAMP, "text": "紅茶の話"},
    {"message_id": "POSTID555", "author_key": "e5e5e5e5e5e5e5e5", "username": "USERNAMEE",
     "timestamp": STAMP, "text": "紅茶とケーキ"},
]
SECRETS = ([row["text"] for row in COFFEE + TEA] + [row["message_id"] for row in COFFEE + TEA]
           + [row["author_key"] for row in COFFEE + TEA]
           + [row["username"] for row in COFFEE + TEA] + ["USERNAMEB".casefold(), "POSTID",
                                                          "x.example"])


@pytest.fixture
def account(isolated_account_factory, monkeypatch, thth_root):
    made = isolated_account_factory("one", media="threads", project="cafe")
    monkeypatch.setattr(accounts, "load_token", lambda cfg: {"access_token": "FAKE"})
    calls = []

    class Fake:
        def keyword_search(self, word, **kwargs):
            calls.append(word)
            return [dict(row) for row in (COFFEE if word == "珈琲" else TEA)]
    monkeypatch.setattr(adapters, "make_adapter", lambda *a: Fake())
    return {"calls": calls, "root": thth_root, "made": made}


def _answer(**kw):
    return where_cli.answer(account_name="one", words=["珈琲", "紅茶"], now=NOW,
                            also=["豆", "ハンドドリップ"], **kw)


def test_当たり率の順に並べ_0件の語に知らせる(account):
    result = _answer(since="7d")
    hits = result["by_account"]["one"]["also_hits"]
    assert [row["word"] for row in hits] == ["珈琲", "紅茶"], "当たり率の高い順"
    assert hits[0] == {"word": "珈琲", "n_fetched": 4, "n_matched": 3, "hit_rate": 0.75,
                       "denominator": 4, "reason": None}
    assert hits[1]["n_matched"] == 0 and hits[1]["hit_rate"] == 0.0
    assert hits[1]["zero_note"] == "also に合った投稿が 0 件（直近 7 日）"
    assert account["calls"] == ["珈琲", "紅茶"], "also の語で検索しない"
    assert "suggest" not in result["by_account"]["one"], "--suggest が無ければ候補は出さない"


def test_当たり率の期間と取れなかった語():
    rows = where_suggest.also_hits([("a", 0, 0), ("b", 10, 1), ("c", 4, 2)],
                                   period_days={"a": None, "b": 3})
    assert [row["word"] for row in rows] == ["c", "b", "a"]
    assert rows[2]["hit_rate"] is None and rows[2]["reason"] == "no_posts_fetched"
    assert rows[2]["zero_note"] == "also に合った投稿が 0 件（期間は不明）"
    stamps = [{"timestamp": "2026-09-21T13:00:00+09:00"}]
    assert where_cli._period_days(stamps, None, NOW) == 3
    assert where_cli._period_days([], None, NOW) is None


def test_suggestは語と数だけ_検索語とalsoの語と数字と長い連なりと名前を除く(account):
    result = _answer(suggest=True)
    search = result["by_account"]["one"]["suggest"]["from_search"]
    assert search["label"] == "その場の人が書いた語"
    assert search["candidates"] == [{"word": "深煎", "n_posts": 2, "denominator": 3},
                                    {"word": "珈琲豆", "n_posts": 2, "denominator": 3}]
    words = {row["word"] for row in search["candidates"]}
    assert "ハンドドリップ" not in words, "also の語は除く"
    assert "珈琲" not in words and "2026" not in words and LONG not in words
    assert "roaster" not in words, "取れた投稿の username と同じ語は候補にしない"
    assert "浅煎" not in words and "最高" not in words, "1 投稿にしか出ない語は出さない"
    text = json.dumps(result["by_account"]["one"]["suggest"], ensure_ascii=False)
    for secret in SECRETS:
        assert secret not in text, secret


def test_aggregateとsuggestの画面にも本文や名前を出さない(account, capsys):
    assert cli.main(["where", "one", "珈琲", "紅茶", "--also", "豆", "--also", "ハンドドリップ",
                     "--aggregate", "--suggest", "--json"]) == 0
    raw = capsys.readouterr().out
    for secret in SECRETS:
        assert secret not in raw, secret
    result = json.loads(raw)
    assert result["suggest"] is True and result["suggest_storage"] == "display_only"
    assert cli.main(["where", "one", "珈琲", "紅茶", "--also", "豆", "--also", "ハンドドリップ",
                     "--aggregate", "--suggest"]) == 0
    out = capsys.readouterr().out
    for secret in SECRETS:
        assert secret not in out, secret
    assert "語の候補（その場の人が書いた語）" in out and "深煎  2/3 投稿" in out
    assert "also の当たり率（高い順）" in out and "also に合った投稿が 0 件" in out
    assert "候補は表示だけで、道具は保存しません" in out


def _snapshot(root):
    out = {}
    for base, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(base, name)
            with open(path, "rb") as f:
                out[path] = f.read()
    return out


def test_suggestは保存しない_runsは語と件数の1行だけ(account, capsys):
    before = _snapshot(account["root"])
    assert cli.main(["where", "one", "珈琲", "紅茶", "--also", "豆", "--also", "ハンドドリップ",
                     "--suggest", "--json"]) == 0
    capsys.readouterr()
    after = _snapshot(account["root"])
    changed = {path for path in after if before.get(path) != after[path]
               and not path.endswith(".lock")}
    assert changed and all(os.path.basename(path).startswith("runs-") for path in changed), \
        sorted(changed)
    (path,) = changed
    with open(path, encoding="utf-8") as f:
        text = f.read()
    last = json.loads(text.splitlines()[-1])
    assert last["action"] == "where_to_appear" and last["words"] == ["珈琲", "紅茶"]
    assert last["n"] == 3, "also に合った件数（珈琲 3・紅茶 0）"
    assert runs.read_runs(accounts.state_dir_for("one"))[-1]["n"] == 3
    for word in ("深煎", "珈琲豆", "suggest", "hit_rate", "also"):
        assert word not in text, word


def test_自分の台帳からの候補を先頭に(account, capsys):
    made = account["made"]
    map_store.add_node("cafe", "焙煎", by="masaru")
    map_store.add_node("cafe", "珈琲", by="masaru")                 # 検索語は候補にしない
    for index, (topic, goal) in enumerate((("水出し", "click"), ("水出し", "reach"),
                                           ("器", None))):
        fm = {"account": "one", "status": "draft", "topic": topic, "publish_at": None}
        if goal:
            fm["goal"] = goal
        write_queue_file(made["queue_dir"], f"d{index}.md", commit=False, fm_overrides=fm)
    posted = NOW - datetime.timedelta(days=3)
    for post_id, topic, likes in (("M1", "ラテアート", 9), ("M2", "ラテアート", 5),
                                  ("M3", "器", 1), ("M4", "無反応", 0)):
        _write_ndjson(_insight_path(made, post_id), [{
            "account": "one", "post_id": post_id, "posted_at": jst.iso(posted),
            "collected_at": jst.iso(posted + datetime.timedelta(hours=25)), "age_hours": 25,
            "marks": [24], "reply_to": None, "topic": topic,
            "metrics": {"views": 10, "likes": likes, "replies": 1 if likes else 0,
                        "reposts": 0}}])
    result = _answer(suggest=True)
    mine = result["by_account"]["one"]["suggest"]["from_self"]
    assert mine["label"] == "自分のデータ"
    words = [row["word"] for row in mine["candidates"]]
    assert words == ["焙煎", "水出し", "器", "ラテアート"], words
    by = {row["word"]: row for row in mine["candidates"]}
    assert by["焙煎"]["sources"] == ["map_node"]
    assert by["水出し"]["draft"] == {"n": 2, "goals": {"click": 1, "reach": 1}}
    assert by["器"]["sources"] == ["draft_topic", "reacted_topic"]
    assert by["ラテアート"]["reacted"] == {"median_likes_plus_replies_24h": 8.0,
                                          "n_observed": 2, "n_posts": 2}
    assert "無反応" not in words and "珈琲" not in words
    suggest = result["by_account"]["one"]["suggest"]
    assert list(suggest) == ["from_self", "from_search", "storage", "storage_note"], "自分のデータが先"
    assert cli.main(["where", "one", "珈琲", "--also", "豆", "--suggest"]) == 0
    out = capsys.readouterr().out
    assert out.index("語の候補（自分のデータ）") < out.index("語の候補（その場の人が書いた語）")


def test_alsoが無ければその場の候補は出さない(account):
    result = where_cli.answer(account_name="one", words=["珈琲"], now=NOW, suggest=True)
    search = result["by_account"]["one"]["suggest"]["from_search"]
    assert search["candidates"] == [] and search["reason"] == "no_also"
    assert "also_hits" not in result["by_account"]["one"]
    with pytest.raises(where_cli.WhereError):
        where_cli.answer(account_name="one", words=["珈琲"], now=NOW, suggest="yes")


def test_語の切り出しは静的な方法():
    assert where_suggest.tokens("ＩＰｈｏｎｅ１５で撮ったラテアート。２０２６年 @someone https://a.example/x") \
        == {"iphone15", "ラテアート"}
    assert where_suggest.tokens("ーー　あ") == set()
    assert where_suggest.tokens(None) == set()


def test_MCPのwhere_to_appearには足さない():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "mcp", "server.py")
    with open(path, encoding="utf-8") as f:
        source = f.read()
    assert "--suggest" not in source and "also_hits" not in source
    start = source.index('"name": "where_to_appear"')
    schema = source[start:source.index('"name": "who_is_this"')]
    assert "suggest" not in schema and "also" not in schema
