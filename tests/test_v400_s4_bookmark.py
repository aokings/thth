"""4.0 S4 §13: 主体をまたいだ「同じページは 1 回だけ」（T-S4-5a〜5e）、枠と 1 段目（T-S4-6a・6b）、
結果の写しと照合（T-S4-7a・7b）、削除（T-S4-8a・8b）、口の一覧（T-S4-10b）、127.0.0.1 の本物の HTTP（T-S4-10c）。

HTTP は偽物（`httpsafe.urlopen` の差し替え）か 127.0.0.1 のサーバ。はてなには出ない。秘密の値は偽物だけ。
"""
from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.parse

import pytest

from tests.hatena_s1_flow_helpers import call, lane  # noqa: F401
from tests.test_v400_s1_remote import key  # noqa: F401
from tests.hatena_s2_http import FAKE_CONSUMER, FAKE_TOKEN
from tests.hatena_s4_helpers import (ACCOUNT, API, EXTERNAL, EXTERNAL_2, OTHER, OTHER_LANE_URL, OWN_URL, OWN_URL_2,  # noqa: F401
                                     PUBLIC, ZED, big_response, bm, bookmark, bookmark_object, calls, cfg,
                                     fake_http, json_bytes, published, record_row, rows, world)
from thth import guard, hatena_bookmark, httpsafe, lock as lock_mod, oauth1, principals, sent
from thth.adapters import hatenabookmark


def refused(account, **kwargs):
    with pytest.raises(guard.GuardRefused) as caught:
        bookmark(account, **kwargs)
    return caught.value


def failed(account, **kwargs):
    with pytest.raises(hatena_bookmark.BookmarkError) as caught:
        bookmark(account, **kwargs)
    return caught.value.code


def posts(world):
    return calls(world, "POST")


# ---------------------------------------------------------------- T-S4-5a 柵 1（台帳）

def test_T_S4_5a_同じownerの別の主体が付けたURLはbookmark_once_ownerが違えば通る(bm):
    """柵: `siblings` を自分だけにすると赤（other_principal で断らない）。"""
    done = bookmark(ACCOUNT, url=EXTERNAL, comment="相場の頁")
    assert done["result"] == "confirmed"
    e = refused(OTHER, url=EXTERNAL, comment="同じ頁")
    assert str(e) == "bookmark_once" and e.reason == "other_principal:hatena-main"
    assert len(posts(bm)) == 1
    # owner が違う主体は通る
    assert bookmark(ZED, url=EXTERNAL, comment="別の owner")["result"] == "confirmed"
    # guard の段（`bookmark_once=True` の既定）でも同じ
    with pytest.raises(guard.GuardRefused) as caught:
        guard.check(OTHER, cfg(OTHER), "bookmark", bookmark_url=EXTERNAL)
    assert caught.value.reason == "other_principal:hatena-main"


def test_T_S4_5a_not_foundの行は無視し_unknownと削除済みは数える(bm):
    bm.edit_principal("hatena-other", bookmark_same_host_daily=10)
    record_row(bm, ACCOUNT, EXTERNAL, result="not_found")
    assert guard.owner_bookmarked("hatena-other", EXTERNAL) is None
    record_row(bm, ACCOUNT, EXTERNAL_2, result="unknown")
    assert guard.owner_bookmarked("hatena-other", EXTERNAL_2) == "other_principal:hatena-main"
    record_row(bm, ACCOUNT, "https://example.com/gone", retracted=True)
    assert guard.owner_bookmarked("hatena-main", "https://example.com/gone#x") == "same_principal"


# ---------------------------------------------------------------- T-S4-5b 柵 2（公開のエントリー情報）

def test_T_S4_5b_siblingのIDが公開の情報にあれば1段目で言い2段目で断る(bm):
    """柵: 柵 2 を消すと赤（POST まで進む）。"""
    bm.bookmarks.public[EXTERNAL] = ["someone-else", "stranger"]
    p = hatena_bookmark.prepare(ACCOUNT, cfg(ACCOUNT), url=EXTERNAL, comment="c")
    assert p["others"]["checked"] and p["others"]["hits"] == [
        {"principal": "hatena-other", "hatena_id": "someone-else", "self": False}]
    assert any("hatena-other（someone-else）が付けています → 断ります" in line
               for line in hatena_bookmark.lines(p))
    e = refused(ACCOUNT, url=EXTERNAL, comment="c")
    assert str(e) == "bookmark_once" and e.reason == "other_principal_remote:someone-else"
    assert posts(bm) == [] and rows(bm, ACCOUNT) == []
    # 自分の ID なら self_remote
    bm.bookmarks.public[EXTERNAL_2] = ["lane-owner"]
    assert refused(ACCOUNT, url=EXTERNAL_2, comment="c").reason == "self_remote"


def test_T_S4_5b_公開の情報に認証を付けない(bm):
    """柵: 公開の GET に Authorization を付けると赤。"""
    bookmark(ACCOUNT, url=EXTERNAL, comment="c")
    public = [c for c in bm.http.calls if c["url"].startswith(PUBLIC)]
    assert public and all("authorization" not in c["headers"] for c in public)
    assert all(c["url"] == PUBLIC + "?" + urllib.parse.urlencode([("url", EXTERNAL)]) for c in public)
    assert [c for c in bm.http.calls if c["url"].startswith(API)]
    assert all("authorization" in c["headers"] for c in bm.http.calls if c["url"].startswith(API))
    # 1 回の承認で各 1 回（1 段目と 2 段目の owner のロックの中）
    assert len(public) == 2


@pytest.mark.parametrize("answer", [(500, b"oops"), TimeoutError("slow"), (200, b"<html>"), (404, b"no")])
def test_T_S4_5b_GETが失敗なら確かめられなかったで2段目は通る(bm, answer):
    bm.bookmarks.public_answer = (lambda url: answer) if isinstance(answer, tuple) else answer
    p = hatena_bookmark.prepare(ACCOUNT, cfg(ACCOUNT), url=EXTERNAL, comment="c")
    assert p["others"]["checked"] is False
    assert any("確かめられなかった" in line for line in hatena_bookmark.lines(p))
    assert bookmark(ACCOUNT, url=EXTERNAL, comment="c")["result"] == "confirmed"
    assert len(posts(bm)) == 1


# ---------------------------------------------------------------- T-S4-5c 柵 3（自分の主体の GET）

def test_T_S4_5c_既に付いていればbookmark_exists_remoteでPOSTしない(bm):
    bm.bookmarks.marks[("lane-owner", EXTERNAL)] = bookmark_object(EXTERNAL, comment="手で付けた")
    assert str(refused(ACCOUNT, url=EXTERNAL, comment="c")) == "bookmark_exists_remote"
    assert posts(bm) == [] and rows(bm, ACCOUNT) == []
    assert bm.bookmarks.marks[("lane-owner", EXTERNAL)]["comment"] == "手で付けた"   # 上書きしない


@pytest.mark.parametrize("status", [401, 403, 500, 503])
def test_T_S4_5c_GETが401や5xxならbookmark_precheck_failedで何も書かない(bm, status):
    bm.bookmarks.get = lambda url: (status, b"x")
    assert failed(ACCOUNT, url=EXTERNAL, comment="c") == "bookmark_precheck_failed"
    assert posts(bm) == [] and rows(bm, ACCOUNT) == []


def test_T_S4_5c_GETが時間切れでも打たない(bm):
    bm.bookmarks.get = lambda url: TimeoutError("slow")
    assert failed(ACCOUNT, url=EXTERNAL, comment="c") == "bookmark_precheck_failed"
    assert posts(bm) == []


def test_T_S4_5c_404ならPOSTする(bm):
    assert bookmark(ACCOUNT, url=EXTERNAL, comment="c")["result"] == "confirmed"
    methods = [m for m, u in calls(bm) if u.startswith(API)]
    assert methods == ["GET", "POST"]


# ---------------------------------------------------------------- T-S4-5d owner のロック

HOLDER = r"""
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR, 0o644)
fcntl.flock(fd, fcntl.LOCK_EX)
print("held", flush=True)
sys.stdin.readline()
"""


def test_T_S4_5d_別のprocessがownerのロックを持っていればbookmark_busy(bm):
    """別の process（別主体・同じ owner のレーンの 2 段目に当たる）がロックを持つ間は打たない。"""
    path = principals.owner_bookmark_lock_path("owner-m")
    holder = subprocess.Popen([sys.executable, "-c", HOLDER, path], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        e = refused(OTHER, url=EXTERNAL, comment="c")
        assert str(e) == "bookmark_busy"
        assert posts(bm) == []
        # owner の違う主体は別のロック
        assert bookmark(ZED, url=EXTERNAL, comment="c")["result"] == "confirmed"
    finally:
        holder.stdin.write("\n")
        holder.stdin.flush()
        holder.wait(timeout=10)
    assert bookmark(OTHER, url=EXTERNAL_2, comment="c")["result"] == "confirmed"
    assert oct(os.stat(os.path.dirname(path)).st_mode & 0o777) == "0o700"


def test_T_S4_5d_ロックの順はrepo_principal_account_owner(bm, monkeypatch):
    """柵: owner のロックを repo より先に取ると赤。"""
    order = []
    original = lock_mod.acquire

    def spy(lock, seconds=0):
        order.append(lock.lock_path)
        return original(lock, seconds)
    monkeypatch.setattr(lock_mod, "acquire", spy)
    rc, out, err = call(["bookmark", "add", ACCOUNT, "--url", EXTERNAL, "--comment", "c", "--json"])
    assert rc == 1
    digest = json.loads(out)["digest"]
    order.clear()
    rc, out, err = call(["bookmark", "add", ACCOUNT, "--url", EXTERNAL, "--comment", "c", "--confirm", digest,
                         "--by", "tester", "--production", "--json"])
    assert rc == 0, (out, err)
    from thth import accounts
    acct = cfg(ACCOUNT)
    assert order == [accounts.repo_lock_path_for(acct["repo_dir"]), principals.lock_path_for("hatena-main"),
                     accounts.account_lock_path_for(ACCOUNT), principals.owner_bookmark_lock_path("owner-m")]


# ---------------------------------------------------------------- T-S4-5e 別レーンの記事

def test_T_S4_5e_siblingの配信のURLはbookmark_other_lane_article_自分の主体ならuse_delivery(bm):
    published(OTHER, OTHER_LANE_URL, principal="hatena-other", blog_id="c.hatenablog.com", article="lane-c/x")
    shouted = "HTTPS://C.hatenablog.com:443/entry/2026/09/09/110000#comments"
    with pytest.raises(hatena_bookmark.BookmarkError) as caught:
        hatena_bookmark.prepare(ACCOUNT, cfg(ACCOUNT), url=shouted, comment="c")
    assert caught.value.code == "bookmark_other_lane_article"
    published("lane-b-hatenablog", "https://b.hatenablog.com/entry/1", blog_id="b.hatenablog.com",
              article="lane-b/y")
    with pytest.raises(hatena_bookmark.BookmarkError) as caught:
        hatena_bookmark.prepare(ACCOUNT, cfg(ACCOUNT), url="https://b.hatenablog.com/entry/1", comment="c")
    assert caught.value.code == "use_delivery"
    # owner の違う主体の記事は参照先として付けられる
    published(ZED, "https://z.hatenablog.com/entry/1", principal="hatena-z", blog_id="z.hatenablog.com",
              article="lane-z/z")
    p = hatena_bookmark.prepare(ACCOUNT, cfg(ACCOUNT), url="https://z.hatenablog.com/entry/1", comment="c")
    assert p["target"] == "external" and p["delivery"] is None


# ---------------------------------------------------------------- T-S4-6a 枠

def test_T_S4_6a_自分の記事は1日1件_参照先は通る(bm):
    d1 = published(ACCOUNT, OWN_URL)
    d2 = published(ACCOUNT, OWN_URL_2)
    assert bookmark(ACCOUNT, delivery=d1)["target"] == "own"
    assert str(refused(ACCOUNT, delivery=d2)) == "self_bookmark_limit"
    assert bookmark(ACCOUNT, url=EXTERNAL, comment="c")["target"] == "external"


def test_T_S4_6a_同じホストは1日3件(bm):
    """4.0.1: 同じホストの既定は 1 → 3。"""
    bookmark(ACCOUNT, url=EXTERNAL, comment="c")
    bookmark(ACCOUNT, url="https://example.com/second", comment="c")
    bookmark(ACCOUNT, url="https://example.com/third", comment="c")
    assert str(refused(ACCOUNT, url="https://EXAMPLE.com/other-page", comment="c")) == "bookmark_host_limit"
    assert bookmark(ACCOUNT, url=EXTERNAL_2, comment="c")["result"] == "confirmed"
    # 上げれば通る（主体ごと）
    bm.edit_principal("hatena-main", bookmark_same_host_daily=4)
    assert bookmark(ACCOUNT, url="https://example.com/other-page", comment="c")["result"] == "confirmed"


def test_T_S4_6a_台帳に書いた同じホストの値が既定より勝つ(bm):
    bm.edit_principal("hatena-main", bookmark_same_host_daily=1)
    bookmark(ACCOUNT, url=EXTERNAL, comment="c")
    assert str(refused(ACCOUNT, url="https://example.com/other-page", comment="c")) == "bookmark_host_limit"


def test_T_S4_6a_全体の枠は今までどおり(bm):
    # 4.0.1: 既定は 30。台帳に書いた値（3）が勝つ。
    bm.edit_principal("hatena-main", bookmark_same_host_daily=10, daily_max_bookmarks=3)
    for i in range(3):
        bookmark(ACCOUNT, url=f"https://example.com/{i}", comment="c")
    assert str(refused(ACCOUNT, url="https://example.com/9", comment="c")) == "bookmark_limit"


# ---------------------------------------------------------------- T-S4-6b 1 段目の表示

def test_T_S4_6b_1段目の表示_参照先(bm):
    bm.bookmarks.public[EXTERNAL] = ["a", "b"]
    rc, out, err = call(["bookmark", "add", ACCOUNT, "--url", EXTERNAL, "--comment", "相場の頁", "--tags", "相場,豆"])
    assert rc == 1, err
    assert f"URL: {EXTERNAL}" in out
    assert "対象: 参照先（配信の台帳に無い）" in out
    assert "はてな側: ブックマーク 2 件・owner-m の ID は付けていない" in out
    assert "主体の今日の残り枠: 全体 30・自分の記事 1・このホスト 3" in out
    assert "タグ: 相場・豆" in out
    assert any(line.startswith("digest: ") and len(line) == len("digest: ") + 12 for line in out.splitlines())
    assert posts(bm) == [] and rows(bm, ACCOUNT) == []


def test_T_S4_6b_1段目の表示_自分の記事(bm):
    delivery = published(ACCOUNT, OWN_URL)
    rc, out, err = call(["bookmark", "add", ACCOUNT, "--delivery", delivery, "--comment", "自分の記事。"])
    assert rc == 1, err
    assert f"対象: 自分の記事: lane-a/arabica（{delivery}）" in out
    assert "運営者であることが分かる文" in out
    assert "はてなにまだエントリーが無いページ" in out
    rc, out, err = call(["bookmark", "add", ACCOUNT, "--delivery", delivery, "--comment", "自分の記事。", "--json"])
    preview = json.loads(out)["preview"]
    assert preview["target"] == "own" and preview["delivery"] == delivery
    assert preview["remaining"] == {"total": 30, "own": 1, "host": 3} and preview["remaining_today"] == 30


# ---------------------------------------------------------------- T-S4-7a 結果の写し

def _post(answer):
    return lambda url, form: answer


@pytest.mark.parametrize("answer,result,error", [
    ((200, None), "confirmed", None),
    ((201, None), "confirmed", None),
    ((200, b"{broken"), "unknown", "response_invalid"),
    ((200, b'{"comment": "x"}'), "unknown", "response_invalid"),
    ((200, "mismatch"), "unknown", "hatena_user_mismatch"),
    ((500, b"oops"), "unknown", "hatena_http_500"),
    ((503, b"oops"), "unknown", "hatena_http_503"),
    (TimeoutError("slow"), "unknown", "hatena_transport_failed"),
    (ConnectionResetError("reset"), "unknown", "hatena_transport_failed"),
    ("redirect", "unknown", "redirect_blocked"),
    ((200, "big"), "unknown", "response_too_large"),
])
def test_T_S4_7a_書くもの(bm, answer, result, error):
    """柵: 5xx を「書かない」（ambiguous False）にすると赤。"""
    if answer == "redirect":
        answer = httpsafe.RedirectBlocked(API, 302, "moved", {}, None)
    elif isinstance(answer, tuple) and answer[1] is None:
        answer = (answer[0], json_bytes(bookmark_object(EXTERNAL)))
    elif isinstance(answer, tuple) and answer[1] == "mismatch":
        answer = (200, json_bytes(bookmark_object(EXTERNAL, user="someone-else")))
    elif isinstance(answer, tuple) and answer[1] == "big":
        answer = (200, big_response())
    bm.bookmarks.post = _post(answer)
    done = bookmark(ACCOUNT, url=EXTERNAL, comment="c")
    assert done["result"] == result and done.get("error") == error
    [row] = rows(bm, ACCOUNT)
    assert row["result"] == result and row["target"] == "external" and row["url_canonical"] == EXTERNAL
    assert ("remote" in row) == (result == "confirmed")
    assert len(posts(bm)) == 1                     # 打ち直さない


@pytest.mark.parametrize("status,error", [(400, "hatena_http_400"), (401, "hatena_auth_rejected"),
                                          (403, "hatena_auth_rejected"), (404, "hatena_http_404"),
                                          (429, "provider_rate_limited"), (409, "hatena_http_409")])
def test_T_S4_7a_書かないもの(bm, status, error):
    bm.bookmarks.post = _post((status, b"no"))
    assert failed(ACCOUNT, url=EXTERNAL, comment="c") == error
    assert rows(bm, ACCOUNT) == []
    assert principals.stopped("hatena-main") is None     # 429 でも主体は止めない


def test_T_S4_7a_接続の前の断りは書かない(bm, monkeypatch):
    original = bm.bookmarks.__call__

    def reject(method, url, headers, body):
        if method == "POST":
            raise httpsafe.EndpointRejected("endpoint_invalid")
        return original(method, url, headers, body)
    bm.http.handler = reject
    assert failed(ACCOUNT, url=EXTERNAL, comment="c") == "endpoint_rejected"
    assert rows(bm, ACCOUNT) == []


def test_T_S4_7a_adapterの例外は結果不明(bm, monkeypatch):
    class Broken(hatenabookmark.HatenaBookmarkAdapter):
        def add_bookmark(self, *, url, comment, tags):
            raise RuntimeError("fake-oauth-token-secret-0002 leaked?")

    def factory(account_cfg, principal_cfg):
        from thth.adapters import hatena_common
        token, consumer = hatena_common.load_credentials(account_cfg, principal_cfg)
        return Broken(account_cfg, principal_cfg, token=token, consumer=consumer)
    done = bookmark(ACCOUNT, url=EXTERNAL, comment="c", factory=factory)
    assert done["result"] == "unknown" and done["error"] == "adapter_error"
    assert "fake-oauth" not in json.dumps(done, ensure_ascii=False)
    assert rows(bm, ACCOUNT)[0]["result"] == "unknown"


def test_T_S4_7a_送るformと応答の写し(bm):
    done = bookmark(ACCOUNT, url=EXTERNAL, comment="相場の頁", tags="相場,豆")
    post = next(c for c in bm.http.calls if c["method"] == "POST")
    assert post["url"] == API + "?" + urllib.parse.urlencode([("url", EXTERNAL)])
    form = urllib.parse.parse_qs(post["body"].decode())
    assert form == {"comment": ["相場の頁"], "tags": ["相場", "豆"]} and "private" not in form
    assert post["headers"]["content-type"].startswith("application/x-www-form-urlencoded")
    [row] = rows(bm, ACCOUNT)
    assert row["remote"]["user"] == "lane-owner" and row["remote"]["permalink"] == done["permalink"]
    assert row["tags"] == sorted(["相場", "豆"]) and row["text"] == "相場の頁"
    assert row["approved_fingerprint"] and "delivery" not in row and "article" not in row


# ---------------------------------------------------------------- T-S4-7b unknown と照合

def test_T_S4_7b_unknownの2回目はbookmark_once_reconcileで決める(bm):
    bm.edit_principal("hatena-main", bookmark_same_host_daily=10, daily_max_bookmarks=10,
                      burst={"count": 10, "minutes": 30})
    bm.bookmarks.post = _post((500, b"oops"))
    urls = ["https://example.com/a", "https://example.com/b", "https://example.com/c"]
    for url in urls:
        assert bookmark(ACCOUNT, url=url, comment="c")["result"] == "unknown"
    e = refused(ACCOUNT, url=urls[0], comment="c")
    assert str(e) == "bookmark_once" and e.reason == "same_principal"
    bm.bookmarks.post = None
    # a は付いていた・b は無い・c は 5xx で分からないまま
    bm.bookmarks.marks[("lane-owner", urls[0])] = bookmark_object(urls[0])

    def get(url):
        if url == urls[2]:
            return (500, b"oops")
        mark = bm.bookmarks.marks.get(("lane-owner", url))
        return (200, json_bytes(mark)) if mark else (404, b"no")
    bm.bookmarks.get = get
    slept = []
    out = hatena_bookmark.reconcile(ACCOUNT, cfg(ACCOUNT), by="tester", sleep=slept.append)
    assert (out["checked"], out["confirmed"], out["not_found"], out["unchanged"]) == (3, 1, 1, 1)
    assert slept == [2, 2]
    by_url = {r["url"]: r for r in rows(bm, ACCOUNT)}
    assert by_url[urls[0]]["result"] == "confirmed" and by_url[urls[0]]["remote"]["user"] == "lane-owner"
    assert by_url[urls[1]]["result"] == "not_found" and "remote" not in by_url[urls[1]]
    assert by_url[urls[2]]["result"] == "unknown"
    # not_found のあとは打てる。前の試みは attempts[] に移り、枠には数え続ける。
    assert guard.owner_bookmarked("hatena-main", urls[1]) is None
    bm.bookmarks.get = None
    before = guard.principal_summary("hatena-main", principals.load_principal("hatena-main"))["bookmarks_today"]
    assert bookmark(ACCOUNT, url=urls[1], comment="c")["result"] == "confirmed"
    row = sent.read(bm.state(ACCOUNT), sent.bookmark_post_id(urls[1]))
    assert row["result"] == "confirmed" and [a["result"] for a in row["attempts"]] == ["not_found"]
    after = guard.principal_summary("hatena-main", principals.load_principal("hatena-main"))["bookmarks_today"]
    assert after == before + 1
    # 打ち直したあとは同じ URL を断る
    assert str(refused(ACCOUNT, url=urls[1], comment="c")) == "bookmark_once"


def test_T_S4_7b_reconcileのCLI(bm):
    record_row(bm, ACCOUNT, EXTERNAL, result="unknown")
    bm.bookmarks.marks[("lane-owner", EXTERNAL)] = bookmark_object(EXTERNAL)
    rc, out, err = call(["bookmark", "reconcile", ACCOUNT, "--by", "tester", "--json"])
    assert rc == 0, err
    assert json.loads(out)["confirmed"] == 1
    rc, out, err = call(["bookmark", "list", ACCOUNT, "--json"])
    assert [r["result"] for r in json.loads(out)["bookmarks"]] == ["confirmed"]


# ---------------------------------------------------------------- T-S4-8a・8b 削除

def _delete(account, url, *, reason=None):
    c = cfg(account)
    p = hatena_bookmark.prepare(account, c, url=url, op="delete")
    return hatena_bookmark.delete_locked(account, c, p, actor="tester", via="cli", reason=reason)


def test_T_S4_8a_204と404はmark_retracted_5xxは何も書かない_再追加はbookmark_once(bm):
    bm.edit_principal("hatena-main", bookmark_same_host_daily=10, daily_max_bookmarks=10,
                      burst={"count": 10, "minutes": 30})
    bookmark(ACCOUNT, url=EXTERNAL, comment="c")
    bookmark(ACCOUNT, url=EXTERNAL_2, comment="c")
    done = _delete(ACCOUNT, EXTERNAL, reason="誤り")
    assert done["status"] == "bookmark_deleted" and not done["missing"]
    row = sent.read(bm.state(ACCOUNT), sent.bookmark_post_id(EXTERNAL))
    assert row["retract_reason"] == "誤り" and row["retracted_by"] == "tester"
    assert ("lane-owner", EXTERNAL) not in bm.bookmarks.marks
    # 404（既に無い）も消えたとして足す
    bm.bookmarks.marks.pop(("lane-owner", EXTERNAL_2))
    done = _delete(ACCOUNT, EXTERNAL_2)
    assert done["missing"] and sent.read(bm.state(ACCOUNT), sent.bookmark_post_id(EXTERNAL_2))[
        "retract_reason"] == "remote_missing"
    # 削除のあとの再追加は断る
    assert str(refused(ACCOUNT, url=EXTERNAL, comment="c")) == "bookmark_once"


def test_T_S4_8a_5xxと時間切れは何も書かない(bm):
    bookmark(ACCOUNT, url=EXTERNAL, comment="c")
    for answer in ((500, b"oops"), TimeoutError("slow")):
        bm.bookmarks.delete = (lambda a: (lambda url: a))(answer)
        with pytest.raises(hatena_bookmark.BookmarkError):
            _delete(ACCOUNT, EXTERNAL)
        assert "retracted_at" not in sent.read(bm.state(ACCOUNT), sent.bookmark_post_id(EXTERNAL))


def test_T_S4_8a_記録の無いURLはbookmark_not_found(bm):
    with pytest.raises(hatena_bookmark.BookmarkError) as caught:
        _delete(ACCOUNT, EXTERNAL)
    assert caught.value.code == "bookmark_not_found"
    assert calls(bm, "DELETE") == []
    # 削除の CLI の 2 段
    bookmark(ACCOUNT, url=EXTERNAL, comment="c")
    rc, out, err = call(["bookmark", "delete", ACCOUNT, "--url", EXTERNAL, "--json"])
    assert rc == 1, err
    digest = json.loads(out)["digest"]
    rc, out, err = call(["bookmark", "delete", ACCOUNT, "--url", EXTERNAL, "--confirm", digest, "--by", "tester",
                         "--production", "--json"])
    assert rc == 0, (out, err)
    assert json.loads(out)["status"] == "bookmark_deleted"


def test_T_S4_8b_op_updateはinvalid_op(bm):
    with pytest.raises(hatena_bookmark.BookmarkError) as caught:
        hatena_bookmark.prepare(ACCOUNT, cfg(ACCOUNT), url=EXTERNAL, comment="c", op="update")
    assert caught.value.code == "invalid_op"
    assert "invalid_op" in __import__("thth.server_writes", fromlist=["SAFE_ERRORS"]).SAFE_ERRORS


# ---------------------------------------------------------------- 遠くの道

def test_遠くの道はurlとop_deleteを受ける(bm, key):
    from thth import server_writes as writes
    from thth.report_service import ReportServiceError
    context = key({ACCOUNT: "lane-a"})
    bm.edit_principal("hatena-main", bookmark_same_host_daily=10, burst={"count": 10, "minutes": 30})

    def req(**body):
        return writes.execute(context, dict(operation="bookmark_request", account=ACCOUNT, **body), via="mcp")

    def refused_remote(**body):
        with pytest.raises(ReportServiceError) as caught:
            req(**body)
        return str(caught.value)

    first = req(url=EXTERNAL, comment="相場の頁")
    assert first["stage"] == 1 and first["preview"]["target"] == "external"
    done = req(url=EXTERNAL, comment="相場の頁", confirm=first["digest"])
    assert done["result"] == "confirmed" and done["via"] == "mcp"
    assert refused_remote(url=EXTERNAL, delivery="dl_000000000000", comment="c") == "invalid_request"
    assert refused_remote(comment="c") == "invalid_request"
    assert refused_remote(url=EXTERNAL, comment="c", op="update") == "invalid_op"
    assert refused_remote(url="ftp://example.com/x", comment="c") == "invalid_url"
    assert refused_remote(url=EXTERNAL, comment="相場の頁", confirm=first["digest"]) == "bookmark_once"
    first = req(url=EXTERNAL, op="delete")
    assert first["stage"] == 1
    gone = req(url=EXTERNAL, op="delete", confirm=first["digest"])
    assert gone["status"] == "bookmark_deleted"
    # 結果不明は status: unknown で返す（打ち直さない）
    bm.bookmarks.post = lambda url, form: (502, b"bad gateway")
    first = req(url=EXTERNAL_2, comment="c")
    assert req(url=EXTERNAL_2, comment="c", confirm=first["digest"])["status"] == "unknown"


# ---------------------------------------------------------------- T-S4-10b 口の一覧

@pytest.fixture
def mcp_server():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp"))
    import server
    return server


BEFORE_S4_SERVER_TOOLS = {"thth_draft_put", "thth_send_request", "thth_retract_request", "thth_schedule_request",
                          "thth_bookmark_request", "thth_draft_list", "thth_queue"}


def test_T_S4_10b_MCPに足すのは引数だけで道具は足さない(mcp_server):
    """柵: 道具（reconcile・list など）を足すと赤。"""
    names = {t["name"] for t in mcp_server.SERVER_TOOLS}
    assert not [n for n in names if "reconcile" in n or "bookmark_list" in n or n.startswith("thth_principal")]
    first_table = {t["name"] for t in mcp_server.SERVER_TOOLS if t["name"] in BEFORE_S4_SERVER_TOOLS}
    assert first_table == BEFORE_S4_SERVER_TOOLS
    assert not [n for n in names if "announce" in n]
    tool = next(t for t in mcp_server.SERVER_TOOLS if t["name"] == "thth_bookmark_request")
    assert set(tool["inputSchema"]["properties"]) == {"account", "delivery", "url", "comment", "tags", "confirm", "op"}
    build, _ = mcp_server.REMOTE_CLI["thth_bookmark_request"]
    assert build({"account": "a", "url": EXTERNAL, "comment": "c"}) == [
        "bookmark", "add", "a", "--url=" + EXTERNAL, "--comment=c", "--production", "--json"]
    assert build({"account": "a", "url": EXTERNAL, "op": "delete", "confirm": "d"}) == [
        "bookmark", "delete", "a", "--url=" + EXTERNAL, "--confirm=d", "--production", "--json"]
    from thth import server_writes
    assert not [op for op in server_writes.WRITE_OPERATIONS if "reconcile" in op or "principal" in op]
    assert "bookmark_reconcile" not in server_writes.WRITE_OPERATIONS


# ---------------------------------------------------------------- T-S4-10c 127.0.0.1 の本物の HTTP

class _Server:
    """127.0.0.1 の本物の HTTP サーバ。署名をサーバ側で検算する。"""

    def __init__(self):
        self.requests = []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                server.requests.append({"method": self.command, "path": self.path, "body": body,
                                        "authorization": self.headers.get("Authorization"),
                                        "user_agent": self.headers.get("User-Agent"),
                                        "content_type": self.headers.get("Content-Type")})
                raw = json.dumps(bookmark_object("x")).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        return False


def _verify(request, base_url, *, with_body=True):
    """受け取った要求から base string を組み直し、偽物の秘密で署名を検算する（RFC 5849 §3.4）。"""
    params = oauth1.parse_header(request["authorization"])
    signature = params.pop("oauth_signature")
    params.pop("realm", None)
    form = urllib.parse.parse_qsl(request["body"].decode(), keep_blank_values=True) if with_body else []
    base = oauth1.base_string("POST", base_url + request["path"], list(params.items()) + form)
    return signature == oauth1.sign_hmac_sha1(base, FAKE_CONSUMER["consumer_secret"],
                                              FAKE_TOKEN["oauth_token_secret"])


def test_T_S4_10c_本物のurllibで_formはbase_stringに_urlはクエリに_UAに連絡先(bm, monkeypatch):
    """`httpsafe.urlopen` は差し替えない。柵: body を base string から外すと赤（検算が合わない）。"""
    from thth.adapters import hatena_common
    with _Server() as srv:
        monkeypatch.setattr(hatenabookmark, "BASE", f"http://127.0.0.1:{srv.port}")
        monkeypatch.setattr(httpsafe, "urlopen", _REAL_URLOPEN)
        token, consumer = hatena_common.load_credentials(cfg(ACCOUNT), principals.load_principal("hatena-main"))
        adapter = hatenabookmark.HatenaBookmarkAdapter(cfg(ACCOUNT), principals.load_principal("hatena-main"),
                                                       token=token, consumer=consumer)
        result = adapter.add_bookmark(url=EXTERNAL, comment="相場の頁 & 豆", tags=["相場", "豆"])
        assert result.ok and result.bookmark["user"] == "lane-owner"
        [req] = srv.requests
        assert req["path"] == "/rest/1/my/bookmark?" + urllib.parse.urlencode([("url", EXTERNAL)])
        assert urllib.parse.parse_qs(req["body"].decode()) == {"comment": ["相場の頁 & 豆"], "tags": ["相場", "豆"]}
        assert req["content_type"].startswith("application/x-www-form-urlencoded")
        assert _verify(req, f"http://127.0.0.1:{srv.port}")
        assert not _verify(req, f"http://127.0.0.1:{srv.port}", with_body=False)
        assert "(+https://thth.me/)" in req["user_agent"] and req["user_agent"].startswith("thth/")
        assert FAKE_TOKEN["oauth_token_secret"] not in req["authorization"]
        assert FAKE_CONSUMER["consumer_secret"] not in req["authorization"]


_REAL_URLOPEN = httpsafe.urlopen
