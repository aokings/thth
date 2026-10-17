"""4.0 S6 の試験（`tests/test_v400_s6_*.py`）が共有する組み立て。

- S1 の `lane`（`lane-a-hatenablog`・本物の git repo と bare origin）の上で、主体 `hatena-main`（lane-owner）と
  `hatena-other`（someone-else）を同じ owner にする（自分の分＝この 2 つの ID）。
- はてなの公開の読む口（件数 `count/entries`・エントリー情報 `entry/jsonlite`・スター `entry.json`）は
  `FakeHatena`（`fake_http` で `httpsafe.urlopen` を差し替える）。**本物のはてなには 1 回も出ない。**
- 配信の台帳は `deliveries.create`／`advance` で直に置く（公開の時刻を決めるため）。
- 秘密の値は置かない（公開の読む口は認証なし）。
"""
from __future__ import annotations

import datetime
import json
import os
import urllib.parse

import pytest

from tests.hatena_s1_flow_helpers import ACCOUNT, NOW, call, lane, world  # noqa: F401
from tests.hatena_s2_http import fake_http  # noqa: F401
from thth import accounts, deliveries, hatena_reactions, jst

COUNT = "https://bookmark.hatenaapis.com/count/entries?"
ENTRY = "https://b.hatena.ne.jp/entry/jsonlite/?"
STAR = "https://s.hatena.ne.jp/entry.json?"
ARTICLE = "lane-a/arabica"
URL1 = "https://a.hatenablog.com/entry/2026/09/08/090000"
URL2 = "https://a.hatenablog.com/entry/2026/09/07/090000"
URL3 = "https://a.hatenablog.com/entry/2026/09/06/090000"
SELF = "lane-owner"
SIBLING = "someone-else"


def bm(user, comment="", tags=(), timestamp="2026/09/08 21:03"):
    return {"user": user, "comment": comment, "tags": list(tags), "timestamp": timestamp}


class FakeHatena:
    """偽物の公開の読む口。`counts[url]`・`entries[url]`（bookmarks の list か None）・`stars[url]`（`{"stars", "colored_stars"}`）。

    `fail[kind] = 500` で失敗させる（kind は `counts`・`entry`・`stars`）。呼ばれた口を `calls` に残す。
    件数の答えには `counts` に載っている URL だけを入れる（載っていない URL は鍵ごと無い）。
    """

    def __init__(self):
        self.counts, self.entries, self.stars, self.fail = {}, {}, {}, {}
        self.calls = []

    def __call__(self, method, url, headers, body):
        assert method == "GET", (method, url)
        assert "authorization" not in headers, "公開の読む口に Authorization を付けた"
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        ok = {"Content-Type": "application/json"}
        if url.startswith(COUNT):
            self.calls.append(("counts", query["url"]))
            if "counts" in self.fail:
                return self.fail["counts"], {}, b"err"
            return 200, ok, json.dumps({u: self.counts[u] for u in query["url"] if u in self.counts}).encode()
        if url.startswith(ENTRY):
            (target,) = query["url"]
            self.calls.append(("entry", target))
            if "entry" in self.fail:
                return self.fail["entry"], {}, b"err"
            marks = self.entries.get(target)
            if marks is None:
                return 200, ok, b"null"
            return 200, ok, json.dumps({"title": "t", "count": len(marks), "url": target, "eid": "1",
                                        "bookmarks": marks}).encode()
        if url.startswith(STAR):
            self.calls.append(("stars", query["uri"]))
            if "stars" in self.fail:
                return self.fail["stars"], {}, b"err"
            entries = [{"uri": u, **self.stars[u]} for u in query["uri"] if u in self.stars]
            return 200, ok, json.dumps({"entries": entries}).encode()
        raise AssertionError(f"unexpected {method} {url}")

    def kinds(self):
        return [c[0] for c in self.calls]


@pytest.fixture
def s6(lane, fake_http, monkeypatch):  # noqa: F811
    """`lane` に owner を組み、偽物の公開の読む口を置いた世界。`s6.hatena.calls` で呼び出しを見る。"""
    lane.world.edit_principal("hatena-main", owner="owner-m")
    lane.world.edit_principal("hatena-other", owner="owner-m")
    lane.hatena = FakeHatena()
    lane.http = fake_http(lane.hatena)
    return lane


def put_delivery(url, *, hours_ago, account=ACCOUNT, article=ARTICLE, state="published", history=None):
    """配信の台帳を 1 本（`hours_ago` 時間前に published）。`history` は `[(状態, 何時間前)]` を後ろに足す。"""
    at = NOW - datetime.timedelta(hours=hours_ago)
    record = deliveries.create(account, principal="hatena-main", blog_id="a.hatenablog.com", article=article,
                               at=jst.iso(at - datetime.timedelta(minutes=5)))
    deliveries.advance(account, record["delivery"], state="published", at=jst.iso(at),
                       fields={"published_url": url})
    for later_state, later_hours in history or []:
        deliveries.advance(account, record["delivery"], state=later_state,
                           at=jst.iso(NOW - datetime.timedelta(hours=later_hours)))
    if state != "published" and not history:
        deliveries.advance(account, record["delivery"], state=state, at=jst.iso(at + datetime.timedelta(minutes=1)))
    return record["delivery"]


def ledger_rows(delivery, account=ACCOUNT):
    cfg = accounts.load_account(account)
    rows, broken = hatena_reactions.read_rows(cfg, account, delivery)
    assert not broken
    return rows


def ledger_file(delivery, account=ACCOUNT):
    cfg = accounts.load_account(account)
    return hatena_reactions.ledger_path(cfg, account, delivery)


def collect(at=None, account=ACCOUNT, **kwargs):
    """`run_collect` を時刻を決めて 1 回（`thth run` の末尾と同じ入口）。"""
    return hatena_reactions.run_collect(account, now=at or NOW, log=lambda *_a: None, **kwargs)


def snapshot(root):
    """`root` の下のファイルの中身（相対パス → bytes）。"""
    out = {}
    for base, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(base, name)
            with open(path, "rb") as f:
                out[os.path.relpath(path, root)] = f.read()
    return out
