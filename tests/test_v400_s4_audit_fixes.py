"""4.0 S4 監査（Fable）の直し: 中-1・低-1〜低-4 と、確かめ (a)(b)。

はてなには出ない（偽物の HTTP）。秘密の値は明らかな偽物だけ。
"""
from __future__ import annotations

import json
import types

import pytest

from tests.hatena_s1_flow_helpers import approve, call
from tests.hatena_s4_helpers import (ACCOUNT, EXTERNAL, OWN_URL, bm, bookmark, cfg, fake_http, published,  # noqa: F401
                                     rows, world)
from tests.test_v400_s4_announce import ARTICLE_URL, HATENA, SNS, Spy, reasons, sns, throw  # noqa: F401
from thth import announce, core, deliveries, guard, hatena_bookmark, remote, select, sent


# ---------------------------------------------------------------- 中-1 遠くの道の結果不明

def _args(**kw):
    base = dict(comment="c", tags=None, confirm="abcdef012345", url=EXTERNAL, delivery=None, json=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


@pytest.mark.parametrize("as_json", [False, True])
def test_中1_遠くの道の結果不明はrc1で打ち直さない案内(monkeypatch, capsys, as_json):
    """柵: `status: unknown` を見ずに「ブックマークしました」と rc 0 にすると赤。"""
    answer = {"account": ACCOUNT, "status": "unknown", "result": "unknown", "url": EXTERNAL,
              "error": "hatena_http_500", "via": "api"}
    monkeypatch.setattr(remote, "call", lambda operation, account, **fields: dict(answer))
    rc = remote._bookmark_add(_args(json=as_json), ACCOUNT)
    out, err = capsys.readouterr()
    assert rc == 1
    if as_json:
        assert json.loads(out)["status"] == "unknown"
    else:
        assert "ブックマークしました" not in out + err
        assert f"結果が分かりません（hatena_http_500）: {EXTERNAL}" in err
        assert f"打ち直しません。thth bookmark reconcile {ACCOUNT} で確かめてください" in err


def test_中1_遠くの道の成功はrc0(monkeypatch, capsys):
    monkeypatch.setattr(remote, "call", lambda operation, account, **fields: {
        "status": "bookmarked", "result": "confirmed", "url": EXTERNAL})
    assert remote._bookmark_add(_args(), ACCOUNT) == 0
    assert "ブックマークしました" in capsys.readouterr().out


# ---------------------------------------------------------------- 低-1 attempts[] も枠に数える

def _unknown_then_not_found(bm, **target):  # noqa: F811
    bm.bookmarks.post = lambda url, form: (500, b"oops")
    assert bookmark(ACCOUNT, **target)["result"] == "unknown"
    bm.bookmarks.post = None
    out = hatena_bookmark.reconcile(ACCOUNT, cfg(ACCOUNT), by="tester", sleep=lambda s: None)
    assert out["not_found"] == 1
    assert bookmark(ACCOUNT, **target)["result"] == "confirmed"      # 打ち直し（attempts[] に前の試み）


def test_低1_打ち直しのあと自分の記事の枠は前の試みも数える(bm):
    """柵: `bookmark_counts` が `attempts[]` を数えないと赤（2 件目の自分の記事が通る）。"""
    bm.edit_principal("hatena-main", daily_max_self_bookmarks=2, bookmark_same_host_daily=10,
                      daily_max_bookmarks=10, burst={"count": 10, "minutes": 30})
    d1 = published(ACCOUNT, OWN_URL)
    d2 = published(ACCOUNT, "https://a.hatenablog.com/entry/2026/09/09/130000")
    _unknown_then_not_found(bm, delivery=d1)
    assert guard.bookmark_counts("hatena-main")["own_today"] == 2
    with pytest.raises(guard.GuardRefused) as caught:
        bookmark(ACCOUNT, delivery=d2)
    assert str(caught.value) == "self_bookmark_limit"


def test_低1_打ち直しのあと同じホストの枠も前の試みを数える(bm):
    bm.edit_principal("hatena-main", bookmark_same_host_daily=2, daily_max_bookmarks=10,
                      burst={"count": 10, "minutes": 30})
    _unknown_then_not_found(bm, url=EXTERNAL, comment="c")
    assert guard.bookmark_counts("hatena-main", url=EXTERNAL)["same_host_today"] == 2
    with pytest.raises(guard.GuardRefused) as caught:
        bookmark(ACCOUNT, url="https://example.com/another", comment="c")
    assert str(caught.value) == "bookmark_host_limit"


# ---------------------------------------------------------------- 低-2 記録を書けない

def _broken_write(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(sent, "write", boom)


def test_低2_記録を書けなければbookmark_record_failed(bm, monkeypatch):
    """柵: OSError を受けずに上げると赤（traceback）。"""
    _broken_write(monkeypatch)
    with pytest.raises(hatena_bookmark.BookmarkError) as caught:
        bookmark(ACCOUNT, url=EXTERNAL, comment="c")
    assert caught.value.code == "bookmark_record_failed"


def test_低2_手元の口はrc2で符丁を返す(bm, monkeypatch):
    rc, out, err = call(["bookmark", "add", ACCOUNT, "--url", EXTERNAL, "--comment", "c", "--json"])
    digest = json.loads(out)["digest"]
    _broken_write(monkeypatch)
    rc, out, err = call(["bookmark", "add", ACCOUNT, "--url", EXTERNAL, "--comment", "c", "--confirm", digest,
                         "--by", "tester", "--production", "--json"])
    assert rc == 2 and json.loads(out)["error"] == "bookmark_record_failed"
    assert "Traceback" not in err


def test_低2_遠くの道も静的な符丁(bm, monkeypatch):
    from thth import server_writes as writes
    assert "bookmark_record_failed" in writes.SAFE_ERRORS
    monkeypatch.setattr(writes, "current", lambda context, account, write=False: cfg(account))
    monkeypatch.setattr(writes, "_origin", lambda context, via: {"via": via, "credential": None})
    import contextlib
    monkeypatch.setattr(writes.server_files, "account_locks", lambda account, c: contextlib.nullcontext())
    context = types.SimpleNamespace(actor="person")
    first = writes.bookmark(context, {"operation": "bookmark_request", "account": ACCOUNT, "url": EXTERNAL,
                                      "comment": "c"}, "mcp")
    _broken_write(monkeypatch)
    from thth.report_service import ReportServiceError
    with pytest.raises(ReportServiceError) as caught:
        writes.bookmark(context, {"operation": "bookmark_request", "account": ACCOUNT, "url": EXTERNAL,
                                  "comment": "c", "confirm": first["digest"]}, "mcp")
    assert str(caught.value) == "bookmark_record_failed"


# ---------------------------------------------------------------- 低-3 本物の adapter の既定の柵

def test_低3_偽物のHTTPが無ければ本物の口を作れない():
    """柵: conftest の既定の柵を外すと赤（本物の factory が既定のまま）。"""
    from tests.conftest import no_real_hatena_adapter
    from thth import hatena_fotolife, hatena_publish
    for module in (hatena_publish, hatena_fotolife, hatena_bookmark):
        assert module.ADAPTER_FACTORY is no_real_hatena_adapter
    with pytest.raises(pytest.fail.Exception):
        hatena_bookmark.ADAPTER_FACTORY({}, {})


def test_低3_偽物のHTTPを置けば本物の口に戻る(fake_http):  # noqa: F811
    from thth import hatena_fotolife, hatena_publish
    fake_http(lambda *a: (200, {}, b""))
    for module in (hatena_publish, hatena_fotolife, hatena_bookmark):
        assert module.ADAPTER_FACTORY is None


# ---------------------------------------------------------------- 低-4 置き換えたあとの長さ

LONG_URL = "https://a.hatenablog.com/entry/" + "x" * 300


def test_低4_本物のURLで上限を越えればheldで出さない(sns, world):  # noqa: F811
    """柵: select の長さの検査を消すと赤（出てしまう・core でも止まるが exit 2 の断りになる）。"""
    body = "あ" * 400 + "{article_url}"
    path = sns.manuscript(body=body)
    approve(path)                       # 仮の URL（49 字）なら 500 字に収まる
    sns.advance("published", LONG_URL)
    spy = Spy(sns)
    result = throw(spy)
    assert spy.posts == []
    reason = "announces_unresolved: too_long_after_fill"
    assert reason in reasons(result) and select.held_category(reason) == "announces_unresolved"


def test_低4_coreも置き換えたあとの長さを見る(sns, world, monkeypatch):  # noqa: F811
    path = sns.manuscript(body="あ" * 400 + "{article_url}")
    approve(path)
    sns.advance("published", LONG_URL)
    monkeypatch.setattr(announce, "too_long_after_fill", lambda *a, **k: False)   # select を素通りさせる
    spy = Spy(sns)
    result = throw(spy)
    assert spy.posts == [] and result.exit_code == 2
    assert result.error == "announces_unresolved: too_long_after_fill"
    assert sent.records(sns.state) == []


# ---------------------------------------------------------------- 確かめ (a)(b)

def test_確かめa_projectの無い口座同士は結ばない(sns, world):  # noqa: F811
    for name in (SNS, HATENA):
        data = world.account(name)
        data.pop("project", None)
        world.write_account(data)
    sns_cfg = dict(world.account(SNS))
    with pytest.raises(announce.AnnounceError) as caught:
        announce.find_delivery(sns_cfg, sns.delivery)
    assert caught.value.code == "announces_not_found"


def test_確かめb_publishedなのにURLが無ければheldで待ち続けない(sns, world):  # noqa: F811
    """S1 の人の解き（`inflight resolve --published`）で読み戻せなかった create は URL の無い published になる。"""
    path = sns.manuscript()
    approve(path)
    sns.advance("published")            # published_url を書かない
    assert deliveries.read(HATENA, sns.delivery).get("published_url") is None
    result = throw(Spy(sns))
    reason = "announces_unresolved: target_no_url"
    assert reason in reasons(result)
    assert select.held_category(reason) == "announces_unresolved" and select.waiting_target(reason) is None
