"""4.0 S4 の試験（`tests/test_v400_s4_*.py`）が共有する、主体の owner と偽物のはてなブックマーク。

- `world`（`hatena_s1_helpers`）の主体 `hatena-main`（はてな ID lane-owner）と `hatena-other`
  （someone-else）を **同じ owner `owner-m`** にし、別の owner の主体 `hatena-z`（zed）と口座
  `lane-z-hatenablog` を足す。3 つの主体とも口座のレーンにブックマークを許す。
- HTTP は `tests/hatena_s2_http.py` の `fake_http`（`httpsafe.urlopen` の差し替え）。偽物の
  ブックマークの REST API と公開のエントリー情報（`Bookmarks`）に答えさせる。**本物のはてなには
  1 回も出ない。** adapter は本物（`hatenabookmark.factory`）。
- 秘密の値は明らかな偽物（`fake-…`）だけ。
"""
from __future__ import annotations

import io
import json
import os
import urllib.parse

import pytest

from tests.hatena_s1_helpers import account_data, principal_data, world  # noqa: F401
from tests.hatena_s2_http import FAKE_CONSUMER, FAKE_TOKEN, fake_http, put_secrets  # noqa: F401
from thth import deliveries, hatena_bookmark, jst, sent

OWN_URL = "https://a.hatenablog.com/entry/2026/09/09/100000"
OWN_URL_2 = "https://a.hatenablog.com/entry/2026/09/09/120000"
OTHER_LANE_URL = "https://c.hatenablog.com/entry/2026/09/09/110000"
EXTERNAL = "https://example.com/coffee/arabica-2026"
EXTERNAL_2 = "https://news.example.org/market/2026-09"
API = "https://bookmark.hatenaapis.com/rest/1/my/bookmark"
PUBLIC = "https://b.hatena.ne.jp/entry/jsonlite/"
ACCOUNT = "lane-a-hatenablog"
OTHER = "lane-c-hatenablog"
ZED = "lane-z-hatenablog"


def bookmark_object(url, *, user="lane-owner", comment="c", tags=()):
    return {"comment": comment, "created_datetime": "2026-09-09T10:00:00+09:00", "created_epoch": 1789000000,
            "user": user, "permalink": f"https://b.hatena.ne.jp/{user}/20260909#bookmark-1", "private": False,
            "tags": list(tags), "url": url}


class Bookmarks:
    """偽物のはてなブックマーク（REST API と公開のエントリー情報）。

    - `marks[(hatena_id, url)]`: 付いているブックマーク。POST で足し、DELETE で消す。
    - `public[url]`: 公開のエントリー情報の `bookmarks[].user`（無ければ `null`＝エントリーが無い）。
    - `post`・`get`・`delete`・`public_answer`・`me`: 差し込み（`(status, bytes)`・例外・`callable`）。
    """

    def __init__(self):
        self.marks = {}
        self.public = {}
        self.post = None
        self.get = None
        self.delete = None
        self.public_answer = None
        self.me = None

    @staticmethod
    def _answer(value, *args):
        if callable(value) and not isinstance(value, BaseException):
            value = value(*args)
        if isinstance(value, BaseException):
            raise value
        status, raw = value
        return status, {"Content-Type": "application/json"}, raw

    @staticmethod
    def _who(headers):
        from thth import oauth1
        token = oauth1.parse_header(headers["authorization"])["oauth_token"]
        return {"fake-oauth-token-0001": "lane-owner", "fake-oauth-token-other": "someone-else",
                "fake-oauth-token-zed": "zed"}[token]

    def __call__(self, method, url, headers, body):
        parts = urllib.parse.urlsplit(url)
        query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
        if url.startswith(PUBLIC):
            assert method == "GET"
            if self.public_answer is not None:
                return self._answer(self.public_answer, query["url"])
            users = self.public.get(query["url"])
            if users is None:
                return 200, {"Content-Type": "application/json"}, b"null"
            data = {"title": "t", "count": len(users), "url": query["url"], "eid": "1",
                    "entry_url": "https://b.hatena.ne.jp/entry/s/x",
                    "bookmarks": [{"user": u, "tags": [], "timestamp": "2026/09/09 10:00", "comment": ""}
                                  for u in users]}
            return 200, {"Content-Type": "application/json"}, json.dumps(data).encode()
        if url == "https://bookmark.hatenaapis.com/rest/1/my":
            if self.me is not None:
                return self._answer(self.me)
            return 200, {}, json.dumps({"name": self._who(headers)}).encode()
        assert url.startswith(API + "?"), url
        target = query["url"]
        who = self._who(headers)
        if method == "GET":
            if self.get is not None:
                return self._answer(self.get, target)
            mark = self.marks.get((who, target))
            if mark is None:
                return 404, {}, b"Not Found"
            return 200, {}, json.dumps(mark).encode()
        if method == "POST":
            form = urllib.parse.parse_qs((body or b"").decode(), keep_blank_values=True)
            if self.post is not None:
                return self._answer(self.post, target, form)
            mark = bookmark_object(target, user=who, comment=form.get("comment", [""])[0],
                                   tags=form.get("tags", []))
            self.marks[(who, target)] = mark
            return 200, {}, json.dumps(mark).encode()
        if method == "DELETE":
            if self.delete is not None:
                return self._answer(self.delete, target)
            if self.marks.pop((who, target), None) is None:
                return 404, {}, b"Not Found"
            return 204, {}, b""
        raise AssertionError(f"unexpected {method} {url}")


def put_principal_secrets(world, name, *, hatena_id, oauth_token):  # noqa: F811
    """`hatena-main` 以外の主体の token と consumer（偽物）を置く。"""
    path = str(world.principal(name)["token"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    token = dict(FAKE_TOKEN, hatena_id=hatena_id, oauth_token=oauth_token)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(token, f)
    os.chmod(path, 0o600)
    with open(path + ".consumer", "w", encoding="utf-8") as f:
        json.dump(FAKE_CONSUMER, f)
    os.chmod(path + ".consumer", 0o600)


@pytest.fixture
def bm(world, fake_http, monkeypatch):  # noqa: F811
    """owner を組み、3 つの主体の秘密（偽物）と偽物のブックマークを置いた世界。`bm.http.calls` で呼び出しを見る。"""
    monkeypatch.setattr(hatena_bookmark, "ENTRY_INFO", None)
    monkeypatch.setattr(hatena_bookmark, "ADAPTER_FACTORY", None)
    world.edit_principal("hatena-main", owner="owner-m", bookmark={"lanes": ["lane-a", "lane-b"]})
    world.edit_principal("hatena-other", owner="owner-m", bookmark={"lanes": ["lane-c"]})
    zed_token = world.tmp / "secrets" / "hatena-z.token"
    world.write_principal(principal_data(
        "hatena-z", token=zed_token, hatena_id="zed", owner="owner-z",
        blogs=[{"blog_id": "z.hatenablog.com", "lanes": ["lane-z"]}], bookmark={"lanes": ["lane-z"]}))
    world.write_account(account_data(ZED, project="lane-z", principal="hatena-z", blog_id="z.hatenablog.com",
                                     token=zed_token, handle="zed", repo_dir=str(world.tmp / "repo-z")))
    for name in (ACCOUNT, OTHER, ZED, "lane-b-hatenablog"):
        data = world.account(name)
        data["production"] = True
        world.write_account(data)
    put_secrets(world)
    put_principal_secrets(world, "hatena-other", hatena_id="someone-else", oauth_token="fake-oauth-token-other")
    put_principal_secrets(world, "hatena-z", hatena_id="zed", oauth_token="fake-oauth-token-zed")
    world.bookmarks = Bookmarks()
    world.http = fake_http(world.bookmarks)
    return world


def published(account, url, *, principal="hatena-main", blog_id="a.hatenablog.com", article="lane-a/arabica",
              state="published"):
    """配信の台帳に published の配信を 1 本置く（配信 id を返す）。"""
    record = deliveries.create(account, principal=principal, blog_id=blog_id, article=article,
                               at="2026-09-09T09:00:00+09:00", state="wanted")
    fields = {"published_url": url} if url else {}
    deliveries.advance(account, record["delivery"], state=state, at="2026-09-09T09:05:00+09:00", fields=fields)
    return record["delivery"]


def cfg(name):
    from thth import accounts
    return accounts.load_account(name)


def bookmark(account, *, delivery=None, url=None, comment="運営者の記事です", tags=None, factory=None):
    """1 段目と 2 段目をつなけて打つ（ロックは CLI と同じく呼び手の外側では取らない・関数を直に）。"""
    c = cfg(account)
    p = hatena_bookmark.prepare(account, c, delivery=delivery, url=url, comment=comment, tags=tags)
    again = hatena_bookmark.prepare(account, c, delivery=delivery, url=url, comment=comment, tags=tags,
                                    check_remote=False)
    assert again["digest"] == p["digest"]
    return hatena_bookmark.execute_locked(account, c, again, actor="tester", via="cli", adapter_factory=factory)


def rows(world, account):  # noqa: F811
    return [r for r in sent.records(world.state(account)) if r.get("kind") == "bookmark_add"]


def calls(world, method=None):  # noqa: F811
    return [(c["method"], c["url"]) for c in world.http.calls if method is None or c["method"] == method]


def record_row(world, account, url, *, result="confirmed", target="external", minutes_ago=60,  # noqa: F811
               retracted=False, delivery=None):
    """`sent/bm:` の行を直に 1 本置く（枠・柵の試験の材料）。"""
    import datetime
    from thth import approval
    at = jst.iso(jst.now_jst() - datetime.timedelta(minutes=minutes_ago))
    canonical = hatena_bookmark.canonical_url(url)
    post_id = sent.bookmark_post_id(url)
    sent.write(world.state(account), post_id=post_id, text="c", body_hash=approval.compute_body_hash("c"),
               sent_at=at, kind="bookmark_add", url=url, url_canonical=canonical, target=target,
               delivery=delivery, result=result)
    if retracted:
        sent.mark_retracted(world.state(account), post_id, retracted_at=at, retracted_by="tester",
                            retract_reason="test")
    return post_id


def json_bytes(value) -> bytes:
    return json.dumps(value).encode()


def big_response() -> bytes:
    return b"{" + b" " * (1024 * 1024 + 10) + b"}"


__all__ = ["OWN_URL", "OWN_URL_2", "OTHER_LANE_URL", "EXTERNAL", "EXTERNAL_2", "API", "PUBLIC", "ACCOUNT", "OTHER",
           "ZED", "bookmark_object", "Bookmarks", "bm", "world", "fake_http", "published", "cfg", "bookmark",
           "rows", "calls", "record_row", "json_bytes", "big_response", "io"]
