"""4.0 S4 §13: 主体の owner と上限の鍵（T-S4-1a・1b）、URL の正規化と `sent/bm:`（T-S4-2a〜2c）、
配信の台帳から外したブックマークと出来事（T-S4-3a・3b）、承認の指紋（T-S4-4a・4b）。

はてなには出ない（偽物の HTTP）。秘密の値は明らかな偽物だけ。
"""
from __future__ import annotations

import hashlib
import json
import os

import pytest

from tests.hatena_s1_flow_helpers import call
from tests.hatena_s4_helpers import (ACCOUNT, EXTERNAL, OWN_URL, bm, bookmark, fake_http, published,  # noqa: F401
                                     record_row, rows, world)
from thth import (accounts, approval, deliveries, events_cli, fingerprint_golden, hatena_bookmark, principals,
                  sent)


# ---------------------------------------------------------------- T-S4-1a

def test_T_S4_1a_ownerが無ければ主体の名がowner(world):
    data = world.principal("hatena-main")
    data.pop("owner", None)
    world.write_principal(data)
    assert principals.owner_of("hatena-main") == "hatena-main"
    other = world.principal("hatena-other")
    other.pop("owner", None)
    world.write_principal(other)
    assert [n for n, _ in principals.siblings("hatena-main")] == ["hatena-main"]


def test_T_S4_1a_siblingsは同じownerの主体を名前の順に返し読めない台帳は飛ばす(world):
    world.edit_principal("hatena-main", owner="owner-m")
    world.edit_principal("hatena-other", owner="owner-m")
    assert [n for n, _ in principals.siblings("hatena-other")] == ["hatena-main", "hatena-other"]
    # 読めない台帳（壊れた JSON）と、形の違う台帳は飛ばす
    (world.principals_dir / "aaa-broken.json").write_text("{", encoding="utf-8")
    bad = world.principal("hatena-other")
    bad.update(principal="hatena-bad", hatena_id="")
    world.write_principal(bad)
    assert [n for n, _ in principals.siblings("hatena-main")] == ["hatena-main", "hatena-other"]
    # owner の値は名前の規則に従う
    world.edit_principal("hatena-other", owner="../x")
    with pytest.raises(principals.PrincipalError) as caught:
        principals.load_principal("hatena-other")
    assert caught.value.code == "owner_invalid"


def test_T_S4_1a_principal_addはownerを渡したときだけ書く(world):
    from tests.conftest import run_thth
    env = {"THTH_ROOT": str(world.root), "THTH_ACCOUNTS_DIR": str(world.accounts_dir)}
    r = run_thth(["principal", "add", "hatena-new", "--hatena-id", "newone", "--blog", "n.hatenablog.com=lane-n",
                  "--owner", "owner-m", "--by", "tester"], env=env)
    assert r.returncode == 0, r.stderr
    assert world.principal("hatena-new")["owner"] == "owner-m"
    r = run_thth(["principal", "add", "hatena-new2", "--hatena-id", "newtwo", "--blog", "m.hatenablog.com=lane-m",
                  "--by", "tester"], env=env)
    assert r.returncode == 0, r.stderr
    assert "owner" not in world.principal("hatena-new2")   # 雛形の名を黙って写さない
    r = run_thth(["principal", "show", "hatena-new", "--json"], env=env)
    assert json.loads(r.stdout)["owner"] == "owner-m"


# ---------------------------------------------------------------- T-S4-1b

@pytest.mark.parametrize("key", ["daily_max_self_bookmarks", "bookmark_same_host_daily"])
def test_T_S4_1b_新しい上限の鍵の検査(world, key):
    assert principals.valid_limit(key, 0) and principals.valid_limit(key, 5)
    for bad in (-1, "1", 1.5, True, None):
        assert not principals.valid_limit(key, bad)
    world.edit_principal("hatena-main", **{key: -1})
    with pytest.raises(principals.PrincipalError) as caught:
        principals.load_principal("hatena-main")
    assert caught.value.code == "invalid_guard_limits"


@pytest.mark.parametrize("key", ["daily_max_self_bookmarks", "bookmark_same_host_daily"])
def test_T_S4_1b_principal_setで変えられ知らない値は断る(world, key):
    rc, out, err = call(["principal", "set", "hatena-main", key, "4", "--by", "tester", "--json"])
    assert rc == 0, err
    assert json.loads(out)["after"] == 4 and world.principal("hatena-main")[key] == 4
    assert principals.limits(principals.load_principal("hatena-main"))[key] == 4
    rc, out, err = call(["principal", "set", "hatena-main", key, "-2", "--by", "tester"])
    assert rc == 2 and "invalid_setting" in err
    rc, out, err = call(["principal", "set", "hatena-main", "daily_max_something", "1", "--by", "tester"])
    assert rc == 2 and "invalid_setting" in err


# ---------------------------------------------------------------- T-S4-2a

@pytest.mark.parametrize("raw,expected", [
    ("HTTPS://Example.COM/a/B?x=1#frag", "https://example.com/a/B?x=1"),
    (" https://example.com:443/p ", "https://example.com/p"),
    ("http://example.com:80/p", "http://example.com/p"),
    ("http://example.com:8080/p", "http://example.com:8080/p"),
    ("https://example.com", "https://example.com/"),
    ("https://example.com#top", "https://example.com/"),
    ("https://example.com/p?b=2&a=1", "https://example.com/p?b=2&a=1"),   # query は並べ替えない
])
def test_T_S4_2a_canonical_url(raw, expected):
    """柵: fragment を残すと赤（`#frag`・`#top` の行が落ちる）。"""
    assert hatena_bookmark.canonical_url(raw) == expected


@pytest.mark.parametrize("raw", ["ftp://example.com/x", "mailto:a@example.com", "https:///nohost",
                                 "https://exa mple.com/", "https://example.com/a\nb", "https://example.com/\x7f",
                                 "", None, "https://user:pw@example.com/"])
def test_T_S4_2a_canonical_urlはinvalid_url(raw):
    with pytest.raises(ValueError, match="invalid_url"):
        hatena_bookmark.canonical_url(raw)


# ---------------------------------------------------------------- T-S4-2b

def test_T_S4_2b_bookmark_post_idは正規化したURLから():
    a = sent.bookmark_post_id("https://Example.com/p#a")
    b = sent.bookmark_post_id("https://example.com:443/p#b")
    assert a == b == "bm:" + hashlib.sha256(b"https://example.com/p").hexdigest()[:24]
    assert sent.bookmark_post_id("https://example.com/p?x=1") != a


# ---------------------------------------------------------------- T-S4-2c

def test_T_S4_2c_mark_bookmark_resultはunknownからしか変えない(tmp_path):
    """柵: `result_fixed` の検査を消すと赤。"""
    state = str(tmp_path)
    for name, result in (("u", "unknown"), ("c", "confirmed")):
        url = f"https://example.com/{name}"
        sent.write(state, post_id=sent.bookmark_post_id(url), text="c", body_hash="h", sent_at="2026-09-09T10:00:00+09:00",
                   kind="bookmark_add", url=url, url_canonical=url, target="external", result=result)
    pid_u = sent.bookmark_post_id("https://example.com/u")
    pid_c = sent.bookmark_post_id("https://example.com/c")
    sent.mark_bookmark_result(state, pid_u, result="confirmed", at="2026-09-09T11:00:00+09:00",
                              remote={"permalink": "p", "user": "lane-owner"})
    row = sent.read(state, pid_u)
    assert row["result"] == "confirmed" and row["remote"]["user"] == "lane-owner"
    assert row["reconciled_at"] == "2026-09-09T11:00:00+09:00"
    for pid in (pid_u, pid_c):
        with pytest.raises(ValueError, match="result_fixed"):
            sent.mark_bookmark_result(state, pid, result="not_found", at="2026-09-09T12:00:00+09:00")
    with pytest.raises(ValueError, match="invalid_result"):
        sent.mark_bookmark_result(state, pid_u, result="unknown", at="2026-09-09T12:00:00+09:00")


def test_T_S4_2c_SNSの行にブックマークの欄は書けない(tmp_path):
    with pytest.raises(ValueError, match="invalid_sent_kind"):
        sent.write(str(tmp_path), post_id="1", text="t", body_hash="h", sent_at="2026-09-09T10:00:00+09:00",
                   target="own")
    with pytest.raises(ValueError, match="invalid_sent_kind"):
        sent.write(str(tmp_path), post_id="1", text="t", body_hash="h", sent_at="2026-09-09T10:00:00+09:00",
                   kind="entry_create", announces="dl_0123456789ab")


# ---------------------------------------------------------------- T-S4-3a

def test_T_S4_3a_ブックマークは配信の台帳に何も書かない(bm):
    """柵: `MUTABLE_FIELDS` に `bookmark` を戻すと赤（advance が断らない）。"""
    bm.edit_principal("hatena-main", daily_max_self_bookmarks=5)
    delivery = published(ACCOUNT, OWN_URL)
    before = deliveries.read(ACCOUNT, delivery)
    done = bookmark(ACCOUNT, delivery=delivery)
    assert done["status"] == "bookmarked" and done["result"] == "confirmed"
    after = deliveries.read(ACCOUNT, delivery)
    assert after == before and "bookmark" not in after
    assert [r["post_id"] for r in rows(bm, ACCOUNT)] == [sent.bookmark_post_id(OWN_URL)]
    with pytest.raises(deliveries.DeliveryError, match="field_not_allowed:bookmark"):
        deliveries.advance(ACCOUNT, delivery, state="published", at="2026-09-09T10:00:00+09:00",
                           fields={"bookmark": {"state": "added"}})


# ---------------------------------------------------------------- T-S4-3b

def _bookmark_events(name):
    payload = events_cli.build(name, since="7d")
    return [e for e in payload["events"] if e["kind"] in ("bookmarked", "bookmark_deleted")]


def test_T_S4_3b_出来事はsent_bmから_unknownは出さずreconcileで同じidで出る(bm):
    """柵: event_id に時刻（sent_at・reconciled_at）を混ぜると赤（最初から confirmed の行と id が違う）。"""
    pid = record_row(bm, ACCOUNT, EXTERNAL, result="unknown")
    assert _bookmark_events(ACCOUNT) == []          # 結果不明は「付いた」と読ませない
    bm.bookmarks.marks[("lane-owner", EXTERNAL)] = {"user": "lane-owner", "permalink": "p", "created_epoch": 1}
    out = hatena_bookmark.reconcile(ACCOUNT, accounts.load_account(ACCOUNT), by="tester", sleep=lambda s: None)
    assert out["confirmed"] == 1
    events = _bookmark_events(ACCOUNT)
    assert [(e["kind"], e["ref"]) for e in events] == [("bookmarked", pid)]
    # 最初から confirmed で書けていた行（別の口座・時刻も違う）と同じ id
    other = record_row(bm, "lane-b-hatenablog", EXTERNAL, result="confirmed", minutes_ago=5)
    assert other == pid
    twin = [e for e in _bookmark_events("lane-b-hatenablog") if e["kind"] == "bookmarked"]
    assert twin[0]["event_id"] == events[0]["event_id"] == hatena_bookmark.event_id(pid, "add")
    # 削除で bookmark_deleted
    sent.mark_retracted(bm.state(ACCOUNT), pid, retracted_at="2026-09-09T09:59:00+09:00", retracted_by="t",
                        retract_reason="bookmark_delete")
    kinds = [e["kind"] for e in _bookmark_events(ACCOUNT)]
    assert kinds.count("bookmark_deleted") == 1 and kinds.count("bookmarked") == 1
    deleted = next(e for e in _bookmark_events(ACCOUNT) if e["kind"] == "bookmark_deleted")
    assert deleted["event_id"] == hatena_bookmark.event_id(pid, "delete")
    # はてなの sent の行は published／retracted に出さない（同じ出来事を 2 つの名前で出さない）
    payload = events_cli.build(ACCOUNT, since="7d")
    assert not [e for e in payload["events"] if e["kind"] in ("published", "retracted")]


# ---------------------------------------------------------------- T-S4-4a

BOOKMARK = dict(account="kopicha-hatenablog", principal="hatena-masaru", op="add",
                url="https://kopicha.hatenablog.com/entry/2026/10/20/080000", delivery="dl_3f9c2a7e1b4d",
                comment="運営者です。", tags=["コーヒー"])


def test_T_S4_4a_deliveryありの既知の指紋は変わらず_空のdeliveryのgoldenを足した():
    """柵: 版の語（`thth-hatena-bookmark-1`）を変えると赤（drift が `no_golden_for_version`）。"""
    assert approval.HATENA_BOOKMARK_VERSION == "thth-hatena-bookmark-1"
    assert fingerprint_golden.drift() == []
    golden = fingerprint_golden.GOLDEN["hatena_bookmark"][1]
    assert golden["bookmark_add"] == "69be00ab5be98c16" and golden["bookmark_delete"] == "c5c9d175ac229bf3"
    assert "bookmark_add_external" in golden
    external = dict(BOOKMARK, delivery="", url="https://example.com/x")
    comps = approval.compute_hatena_bookmark_components(**external)
    assert comps["delivery"] == "" and "target" not in comps
    assert approval.compute_hatena_bookmark_sha(**external) != approval.compute_hatena_bookmark_sha(**BOOKMARK)


@pytest.mark.parametrize("url", ["ftp://example.com/x", "https://exa mple.com/", "https:///x"])
def test_T_S4_4a_URLが正規化を通らなければinvalid_url(url):
    with pytest.raises(ValueError, match="invalid_url"):
        approval.compute_hatena_bookmark_components(**dict(BOOKMARK, url=url))


def test_T_S4_4a_指紋のurlは正規化しない元の文字列():
    comps = approval.compute_hatena_bookmark_components(**dict(BOOKMARK, url=" https://Example.com/x#y "))
    assert comps["url"] == "https://Example.com/x#y"
    with pytest.raises(ValueError, match="invalid_delivery"):
        approval.compute_hatena_bookmark_components(**dict(BOOKMARK, delivery="dl_xyz"))


# ---------------------------------------------------------------- T-S4-4b

def test_T_S4_4b_SNSの指紋のannouncesは末尾に足す任意項目():
    """柵: `announces` を `_OPTION_ORDER` の途中に入れると赤（期待する並びとずれる）。"""
    assert approval.FINGERPRINT_VERSION == 2
    # 4.5.6: `not_after`（出さない期限）を announces のさらに後ろに足した（足しただけ・並びは変えない）。
    # 10/30: 緊急の札（`urgent`）をさらに後ろに足した（足しただけ・並びは変えない）。
    assert approval._OPTION_ORDER[-3:] == ("announces", "not_after", "urgent")
    args = dict(section="記事を書きました。{article_url}", account="golden-threads", reply_to=None, topic=None,
                publish_at="2026-10-20T08:30:00+09:00")
    plain = approval.compute_approved_sha(**args)
    told = approval.compute_approved_sha(**args, announces="dl_3f9c2a7e1b4d", location_id="L1")
    comps = approval.compute_approved_components(**args, announces="dl_3f9c2a7e1b4d", location_id="L1")
    parts = [comps[k] for k in approval._COMPONENT_ORDER] + ["location_id=L1", "announces=dl_3f9c2a7e1b4d"]
    assert told == hashlib.sha256("\x1f".join(parts).encode()).hexdigest()
    assert approval.compute_approved_sha(**args, announces=None) == plain
    assert approval.compute_approved_sha(**args, announces="  ") == plain
    # front matter から拾う（publish_options）・無い原稿は 1 バイトも変わらない
    assert approval.publish_options({"announces": " dl_3f9c2a7e1b4d "})["announces"] == "dl_3f9c2a7e1b4d"
    assert approval.publish_options({})["announces"] is None
    golden = fingerprint_golden.GOLDEN[2]
    assert golden["threads_announces"] == fingerprint_golden.current()["threads_announces"]
    assert "announces" in approval.COMPARED_KEYS
