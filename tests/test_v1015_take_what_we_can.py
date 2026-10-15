"""「取れるのに取らない欄」を取り、本文を全文で見せる（masaru 10/15・点検_絞りすぎ §3.1）。

B-2 画面の全文（thread・mentions・unanswered・list）／B-3 X の長文（note_tweet）／
B-4 Threads の返信の欄／B-5 Threads 検索の since・until・media_type／B-7 フォロワーの属性／
B-9 自分の投稿の欄。B-6（where --aggregate の仮名）と B-8（場所の緯度・経度）は
`test_v370_where_aggregate.py`・`test_v21b_approved_writes.py` で確かめる。

**保存しない決まりは変えない**: 返信の台帳には足した欄を写さない・runs には本文が入らない。
"""
from __future__ import annotations

import argparse
import json

import pytest

from tests.conftest import run_thth
from tests.test_threads_read_permissions import _adapter, _server
from tests.test_where_cli import _data_snapshot, two_media_project  # noqa: F401
from tests.test_x_search_thread import (USERS, adapter_for, fake, tweet,  # noqa: F401
                                        x_account)
from thth import accounts as accounts_mod
from thth import collect, thread_read, threads_read_cli, unanswered
from thth.adapters import base as adapter_base
from thth.adapters import threads as threads_mod
from thth.adapters import x as x_adapter

# 120 字（以前の thread の切り）も 60 字（mentions の切り）も超える、改行のある本文。
LONG = "あ" * 130 + "の末尾\n二行目の終わり"


# ------------------------------------------------------------------ B-2 画面の全文

def _thread_result(**message_extra):
    message = {"depth": 1, "is_own": False, "timestamp": "2026-10-15T10:00:00+09:00",
               "text": LONG, "username": "bob", "author_key": "k1", "message_id": "M1",
               "already_replied": False, **message_extra}
    return {"root": {"post_id": "R1", "username": "me", "is_own": True, "text": LONG},
            "messages": [message],
            "counts": {"messages": 1, "participants": 1, "own": 0, "truncated": False}}


def test_threadの画面は本文を切らず改行も保つ(capsys):
    thread_read._render_human(_thread_result())
    out = capsys.readouterr().out
    assert out.count("あ" * 130 + "の末尾") == 2, out          # 根と返信の両方
    assert "\n    二行目の終わり" in out and "\n      二行目の終わり" in out


def test_threadの画面に種類と引用と隠れと認証の印(capsys):
    thread_read._render_human(_thread_result(media_type="IMAGE", is_quote_post=True,
                                             quoted_post="Q9", hide_status="HIDDEN",
                                             is_verified=True))
    out = capsys.readouterr().out
    assert "id=M1 [IMAGE・引用 Q9・HIDDEN・認証]:" in out
    # 見えている返信・文字だけの投稿には印を付けない。
    thread_read._render_human(_thread_result(media_type="TEXT_POST", hide_status="NOT_HUSHED",
                                             is_quote_post=False, is_verified=False))
    assert "id=M1:" in capsys.readouterr().out


def test_mentionsの画面は本文を切らない(capsys):
    threads_read_cli.show_mentions("a", [{"timestamp": "2026-10-15T10:00:00+09:00",
                                          "username": "bob", "message_id": "M1", "text": LONG}])
    out = capsys.readouterr().out
    assert "あ" * 130 + "の末尾" in out and "      二行目の終わり" in out


def test_unansweredの画面と_jsonは本文の全文(monkeypatch, capsys):
    row = {"post_id": "P1", "reply_id": "R1", "author_key": "k", "username": "bob",
           "preview": threads_read_cli._one_line(LONG), "text": LONG, "age_hours": 1.0,
           "verified": "ledger_only"}
    result = {"account": "a", "n_total": 1, "replies": [row], "cannot_say": [],
              "window": {}, "last_fetch_at": None, "collection_stale_hours": None,
              "summary": {"n": 1}}
    monkeypatch.setattr(unanswered, "answer", lambda *a, **k: json.loads(json.dumps(result)))
    recorded = []
    monkeypatch.setattr(unanswered.runs, "record_minimal",
                        lambda name, line, **k: recorded.append(line))
    monkeypatch.setattr(unanswered, "_seen_until_line", lambda *a, **k: "")
    args = argparse.Namespace(account="a", since=None, refresh=False, wait=0, json=False)
    assert unanswered.cmd(args) == 0
    out = capsys.readouterr().out
    assert "あ" * 130 + "の末尾" in out and "    二行目の終わり" in out
    # runs には本文が入らない（件数だけ）。
    assert recorded and "あ" * 10 not in json.dumps(recorded, ensure_ascii=False)


# ------------------------------------------------------------------ B-3 X の長文

LONG_X = "長文" * 200      # 400 字（280 字を超える）


def test_xの検索は_note_tweetを頼み長文の全文を本文に使う(x_account, fake):
    data = [tweet(str(1900000000000000000 + i), text="切れた先頭…") for i in range(10)]
    data[0]["note_tweet"] = {"text": LONG_X}
    data[1]["note_post"] = {"text": LONG_X + "B"}     # OpenAPI 2.171 の名前でも読む
    data[2]["note_tweet"] = {"text": "  "}            # 空なら text に戻る
    fake["routes"]["/2/tweets/search/recent"] = {"data": data, "includes": USERS,
                                                 "meta": {"result_count": 10}}
    rows = adapter_for().keyword_search("コーヒー", limit=10)
    _m, _p, query = fake["calls"][0]
    assert "note_tweet" in query["tweet.fields"].split(",")
    assert rows[0]["text"] == LONG_X and rows[1]["text"] == LONG_X + "B"
    assert rows[2]["text"] == "切れた先頭…" and rows[3]["text"] == "切れた先頭…"


def test_xの自分の投稿も_note_tweetを頼む(x_account, fake):
    fake["routes"]["/2/users/123/tweets"] = {"data": [
        {"id": "1900000000000000001", "text": "切れた先頭…", "created_at": "2026-09-09T00:00:00Z",
         "note_tweet": {"text": LONG_X}}]}
    rows = adapter_for().recent_posts(limit=5)
    _m, _p, query = fake["calls"][0]
    assert query["tweet.fields"] == "created_at,text,note_tweet"
    assert rows[0]["text"] == LONG_X


def test_xの読みの欄は_note_tweetを含む():
    assert "note_tweet" in x_adapter.XAdapter.READ_FIELDS["tweet.fields"].split(",")


# ------------------------------------------------------------------ B-4 返信の欄

class _Get:
    """`ThreadsAdapter._get` の偽物（叩いた path と params を覚え、決めた応答を返す）。"""

    def __init__(self, answer):
        self.calls, self.answer = [], answer

    def __call__(self, path, params, **kwargs):
        self.calls.append((path, dict(params)))
        value = self.answer(path, params)
        if isinstance(value, BaseException):
            raise value
        return value


def _threads(monkeypatch, answer):
    adapter = threads_mod.ThreadsAdapter(base_url="http://127.0.0.1:9", access_token="FAKE",
                                         user_id="999", timeout=1.0)
    get = _Get(answer)
    monkeypatch.setattr(adapter, "_get", get)
    return adapter, get


REPLY = {"id": "M1", "text": LONG, "username": "bob", "timestamp": "2026-10-15T01:00:00+0000",
         "replied_to": {"id": "R1"}, "root_post": {"id": "R1"}, "is_reply": True,
         "media_type": "IMAGE", "is_quote_post": True, "quoted_post": "Q9",
         "is_reply_owned_by_me": False, "hide_status": "HIDDEN", "is_verified": True}


@pytest.mark.parametrize("call, path", [("conversation", "/v1.0/R1/conversation"),
                                        ("branch_replies", "/v1.0/R1/replies")])
def test_返信の要求に欄を足し行に載せる(monkeypatch, call, path):
    adapter, get = _threads(monkeypatch, lambda p, q: {"data": [dict(REPLY)]})
    rows = getattr(adapter, call)("R1")
    (got_path, params), = get.calls
    assert got_path == path
    fields = params["fields"].split(",")
    for key in ("media_type", "is_quote_post", "quoted_post", "is_reply_owned_by_me",
                "hide_status", "is_verified"):
        assert key in fields
    # URL の欄（profile_picture_url・media_url）と自分の投稿だけの reply_audience は頼まない。
    assert not {"profile_picture_url", "media_url", "reply_audience"} & set(fields)
    assert rows[0]["hide_status"] == "HIDDEN" and rows[0]["is_verified"] is True


def test_返信の台帳には足した欄を写さない(tmp_path):
    path = str(tmp_path / "replies.ndjson")
    collect._save_replies(path, "R1", [dict(REPLY)], now=adapter_base_now(), age_hours=1.0,
                          marks=[], trigger="test")
    rows = [json.loads(line) for line in open(path, encoding="utf-8")]
    reply = next(r for r in rows if r["kind"] == "reply")
    assert reply["id"] == "M1"
    for key in threads_mod.REPLY_EXTRA_FIELDS:
        assert key not in reply, key


def adapter_base_now():
    from thth import jst
    return jst.parse("2026-10-15T12:00:00+09:00")


def test_根の取得も種類と引用を頼む(monkeypatch):
    body = {"id": "R1", "username": "me", "text": "根", "timestamp": "t",
            "media_type": "VIDEO", "is_quote_post": False}
    adapter, get = _threads(monkeypatch, lambda p, q: body)
    monkeypatch.setattr(adapter, "_read", lambda fetch, **k: fetch())
    row = adapter.fetch_post("R1")
    (_p, params), = get.calls
    assert params["fields"] == "id,username,text,timestamp,permalink,media_type,is_quote_post,quoted_post"
    assert row["media_type"] == "VIDEO" and row["is_quote_post"] is False
    assert "quoted_post" not in row      # 付いていない欄を None で埋めない


# ------------------------------------------------------------------ B-5 検索の期間と種類

def _search_params(requests):
    return [q for _m, p, q in requests if p == "/v1.0/keyword_search"]


def test_threadsの検索に_since_until_media_typeを渡す():
    with _server() as (base_url, requests):
        _adapter(base_url).keyword_search("お茶", limit=3, since=1760000000, until=1760086400,
                                          media_type="IMAGE")
    params, = _search_params(requests)
    assert params["since"] == "1760000000" and params["until"] == "1760086400"
    assert params["media_type"] == "IMAGE"


def test_threadsの検索の期間と種類の誤りは叩く前に断る():
    with _server() as (base_url, requests):
        a = _adapter(base_url)
        for kwargs in ({"since": 1600000000}, {"since": 1760086400, "until": 1760000000},
                       {"media_type": "AUDIO"}, {"until": "1760000000"}):
            with pytest.raises(adapter_base.AdapterError):
                a.keyword_search("お茶", **kwargs)
    assert _search_params(requests) == []


def test_whereの_until_mediaはThreadsに渡りBlueskyでは使えないと言う(two_media_project):
    threads_acc, bluesky_acc = two_media_project
    cfg = accounts_mod.load_account(threads_acc["name"])
    before = _data_snapshot(cfg, threads_acc["name"])
    with _server() as (base_url, requests):
        r = run_thth(["where", "--project", "kopicha", "コーヒー", "--since", "2026-10-01T00:00:00+09:00",
                      "--until", "2026-10-02T00:00:00+09:00", "--media", "image", "--json"],
                     env={"THTH_THREADS_BASE_URL": base_url})
    assert r.returncode == 0, r.stdout + r.stderr
    result = json.loads(r.stdout)
    params, = _search_params(requests)
    assert params["since"] == "1790780400" and params["until"] == "1790866800"
    assert params["media_type"] == "IMAGE"
    assert result["threads_search"] == {"until": "2026-10-02T00:00:00+09:00", "media_type": "IMAGE"}
    assert bluesky_acc["name"] not in result["by_account"]
    assert any(bluesky_acc["name"] in line and "--until・--media" in line
               for line in result["cannot_say"])
    assert _data_snapshot(cfg, threads_acc["name"]) == before


def test_whereの_until_mediaは_keepと一緒に使えない(two_media_project):
    threads_acc, _ = two_media_project
    r = run_thth(["where", threads_acc["name"], "コーヒー", "--aggregate", "--keep", "--media", "text"])
    assert r.returncode == 2 and "--keep" in r.stderr


def test_topics_searchの_since_until_media(account_threads):
    with _server() as (base_url, requests):
        r = run_thth(["topics", account_threads, "--search", "お茶", "--since", "2026-10-01T00:00:00+09:00",
                      "--until", "2026-10-02T00:00:00+09:00", "--media", "video", "--json"],
                     env={"THTH_THREADS_BASE_URL": base_url})
    assert r.returncode == 0, r.stdout + r.stderr
    params, = _search_params(requests)
    assert params["since"] == "1790780400" and params["until"] == "1790866800"
    assert params["media_type"] == "VIDEO"
    assert json.loads(r.stdout)["narrow"] == {"since": 1790780400, "until": 1790866800,
                                               "media_type": "VIDEO"}


def test_topics_searchの期間の逆転と古すぎる_sinceは断る(account_threads):
    with _server() as (base_url, requests):
        for extra in (["--since", "2026-10-02T00:00:00+09:00", "--until", "2026-10-01T00:00:00+09:00"],
                      ["--since", "2020-01-01T00:00:00+09:00"]):
            r = run_thth(["topics", account_threads, "--search", "お茶", *extra],
                         env={"THTH_THREADS_BASE_URL": base_url})
            assert r.returncode == 2, r.stdout + r.stderr
    assert _search_params(requests) == []


@pytest.fixture
def account_threads(tmp_path, isolated_account_factory):
    from tests.test_threads_read_permissions import _write_token
    token_path = str(tmp_path / "a.token")
    acc = isolated_account_factory(token=token_path, handle="nigamilab")
    _write_token(token_path)
    return acc["name"]


# ------------------------------------------------------------------ B-7 フォロワーの属性

def _insights_answer(followers, *, fail=()):
    def answer(path, params):
        if params.get("metric") == "follower_demographics":
            if params["breakdown"] in fail:
                return RuntimeError("HTTP 400")
            return {"data": [{"name": "follower_demographics", "period": "lifetime",
                              "total_value": {"breakdowns": [{"by": params["breakdown"]}]}}]}
        return {"data": [{"name": "views", "values": [{"value": 5}]},
                         {"name": "followers_count", "total_value": {"value": followers}}]}
    return answer


def test_100フォロワー以上なら属性を内訳ごとに取る(monkeypatch):
    adapter, get = _threads(monkeypatch, _insights_answer(150, fail=("city",)))
    out = adapter.account_insights("999", since="1", until="2")
    asked = [q for _p, q in get.calls if q.get("metric") == "follower_demographics"]
    assert [q["breakdown"] for q in asked] == ["country", "city", "age", "gender"]
    assert all("since" not in q and "until" not in q for q in asked)   # 資料: since/until は使えない
    demo = out["follower_demographics"]
    assert demo["country"] == {"breakdowns": [{"by": "country"}]}
    assert demo["city"] is None                       # 取れなかった内訳は黙って None
    assert out["views"] == 5 and out["followers_count"] == 150


def test_100フォロワー未満なら属性を頼まない(monkeypatch):
    adapter, get = _threads(monkeypatch, _insights_answer(99))
    out = adapter.account_insights("999", since="1", until="2")
    assert "follower_demographics" not in out
    assert all(q.get("metric") != "follower_demographics" for _p, q in get.calls)


def test_属性が1つも取れなければNoneで日次は止めない(monkeypatch):
    adapter, _get = _threads(monkeypatch, _insights_answer(
        500, fail=threads_mod.FOLLOWER_DEMOGRAPHICS_BREAKDOWNS))
    out = adapter.account_insights("999", since="1", until="2")
    assert out["follower_demographics"] is None and out["views"] == 5


# ------------------------------------------------------------------ B-9 自分の投稿の欄

def test_自分の投稿の欄を足し付いているものだけ写す(monkeypatch):
    rows = [{"id": "1", "permalink": "p", "timestamp": "t", "text": "x", "media_type": "TEXT_POST",
             "is_quote_post": True, "quoted_post": "Q", "link_attachment_url": "https://e.example/"},
            {"id": "2", "permalink": "p2", "timestamp": "t", "text": "y",
             "media_type": "REPOST_FACADE", "reposted_post": "R"}]
    adapter, get = _threads(monkeypatch, lambda p, q: {"data": rows})
    out = adapter.recent_posts(limit=5)
    (_p, params), = get.calls
    fields = params["fields"].split(",")
    for key in ("media_type", "is_quote_post", "quoted_post", "reposted_post", "link_attachment_url"):
        assert key in fields
    assert out[0]["quoted_post"] == "Q" and out[0]["link_attachment_url"] == "https://e.example/"
    assert "reposted_post" not in out[0]
    assert out[1]["reposted_post"] == "R" and "quoted_post" not in out[1]
