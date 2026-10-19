"""段 1: 頼みごとの見立て（`thth/assess.py`）と `guard.check` の「止め → 行の表 → 結論」（設計 上限と連投の柵
§5・§6.3・§7.1・§13 の T-P-1）。

見るのは:
  - T-P-1（表から）: 数のある行はすべて、`count` ちょうどまで通り、次の 1 件で `on_exceed` の振る舞い
    （`stop` なら印・`refuse` なら印なし・`wait` なら `next_at` が窓の空く時刻）。`scope` の外（別の口座・
    別のブログ・別の主体）に同じ数を置いても数えない。`assessment.rule`／`trait` が行のもの。
  - 数の無い行（夜間・間隔・媒体の断りの印・1 URL に 1 回・反応の夜間）の規則。
  - 安全装置: 止めの印の断りの `assessment`・印を置けない置き場は止めずに断る・first-hit（日の上限と burst が
    同時なら日の上限で断り止めない＝今の順）。
  - `assess.evaluate` の `advise`（断らず `advisories` に積む）と `strongest`。

はてなにも SNS にも出ない（`guard.check` を直に・記録は `sent.write`・`reactions.write` で置く）。
"""
from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from typing import Callable

import pytest

from tests.hatena_s1_helpers import EXAMPLES, world  # noqa: F401
from thth import accounts, assess, guard, jst, limits_policy, principals, publish_hold, reactions, sent

NOW = datetime.datetime(2026, 9, 9, 10, 0, 0, tzinfo=jst.JST)
ACCOUNT = "lane-a-hatenablog"     # 主体 hatena-main・ブログ a
SIBLING = "lane-b-hatenablog"     # 同じ主体・別のブログ b
STRANGER = "lane-c-hatenablog"    # 別の主体 hatena-other
SNS, SNS_OTHER = "sns-a", "sns-b"
_SEQ = [0]


def seq():
    _SEQ[0] += 1
    return _SEQ[0]


def sns_cfg(**values):
    base = {"account": SNS, "media": "threads", "daily_max_posts": 100, "daily_max_retracts": 100,
            "burst": {"count": 100, "minutes": 10}, "daily_max_reactions": 100}
    base.update(values)
    return base


def edit_account(world, name, **values):
    data = world.account(name)
    data.update(values)
    world.write_account(data)


def hatena_loose(world):
    """はてなの数をすべて緩める（試す行だけをあとで締める）。"""
    for name in (ACCOUNT, SIBLING, STRANGER):
        edit_account(world, name, daily_max_posts=100, daily_max_retracts=100, burst={"count": 100, "minutes": 10})
    world.edit_principal("hatena-main", daily_max_posts=100, daily_max_retracts=100, daily_max_bookmarks=100,
                         burst={"count": 100, "minutes": 30}, bookmark_burst={"count": 100, "minutes": 10},
                         daily_max_self_bookmarks=100, bookmark_same_host_daily=100)


def sns_post(world, account, at, *, retract=False, reply_to=None):
    state = world.state(account)
    post_id = str(1000000 + seq())
    sent.write(state, post_id=post_id, text="本文", body_hash="0" * 64, sent_at=jst.iso(at), reply_to=reply_to)
    if retract:
        sent.mark_retracted(state, post_id, retracted_at=jst.iso(at), retracted_by="t", retract_reason="t")


def hatena_row(world, account, at, *, kind="entry_create", url=None, target=None, delivery=None):
    n = seq()
    post_id = sent.bookmark_post_id(url) if kind.startswith("bookmark") else f"tag:blog.hatena.ne.jp,2013:blog-ta-{n}"
    sent.write(world.state(account), post_id=post_id, text="本文", body_hash="0" * 64, sent_at=jst.iso(at),
               kind=kind, blog_id=world.account(account)["blog_id"], delivery=delivery or f"dl_{n:012x}",
               url=url, target=target)


def bm_url(i, host=None):
    return f"https://{host or f'site{i}.example.com'}/page{i}"


@dataclass
class Scenario:
    """行 1 つの試し方。`place(i, inside)` が i 件目の出来事を scope の中／外に置き、`ask()` が依頼を出す。"""

    place: Callable
    ask: Callable
    count: int = 2
    next_at: Callable | None = None   # 断りの `next_at`（`count` 件置いたあと）
    stop_target: tuple | None = None  # ("account"|"principal", 名前)


def hours_ago(i):
    return NOW - datetime.timedelta(hours=i + 1)


def minutes_ago(i):
    return NOW - datetime.timedelta(minutes=9 - i)


MIDNIGHT = jst.iso(datetime.datetime(2026, 9, 10, 0, 0, tzinfo=jst.JST))


def scenario(rid, world):
    """行ごとの試し方（`count` 2 に締め、ほかの行は緩める）。"""
    hatena_loose(world)
    hcheck = lambda kind, **kw: guard.check(ACCOUNT, accounts.load_account(ACCOUNT), kind, now=NOW, **kw)
    if rid in ("post.daily", "retract.daily", "reply.burst", "post.burst", "reply.daily"):
        values = {"post.daily": {"daily_max_posts": 2}, "retract.daily": {"daily_max_retracts": 2},
                  "reply.burst": {"burst": {"count": 2, "minutes": 10}},
                  "reply.daily": {"limits": {"reply.daily": 2}},
                  "post.burst": {"burst": {"count": 2, "minutes": 10}}}[rid]
        cfg = sns_cfg(**values)
        when = minutes_ago if rid.endswith("burst") else hours_ago
        kind = "retract" if rid == "retract.daily" else "publish"
        return Scenario(
            # 4.3: 返信の窓（reply.burst）は返信だけを数える → 返信の行は返信の記録を置く。
            place=lambda i, inside: sns_post(world, SNS if inside else SNS_OTHER, when(i), retract=rid == "retract.daily",
                                             reply_to="1799" if rid.startswith("reply.") else None),
            ask=lambda: guard.check(SNS, cfg, kind, now=NOW, reply=rid.startswith("reply.")),
            next_at=(lambda: jst.iso(minutes_ago(0) + datetime.timedelta(minutes=11))) if rid == "reply.burst"
            else (None if rid == "post.burst" else lambda: MIDNIGHT),
            stop_target=("account", SNS) if rid == "post.burst" else None)
    if rid == "reaction.daily":
        cfg = sns_cfg(daily_max_reactions=2)
        return Scenario(
            place=lambda i, inside: reactions.write(world.state(SNS if inside else SNS_OTHER), kind="like",
                                                    post_id=str(2000000 + seq()), platform_id="p", by="t",
                                                    at=jst.iso(hours_ago(i))),
            ask=lambda: guard.check_reaction(SNS, cfg, "like", now=NOW),
            next_at=lambda: MIDNIGHT)
    entry_kind = "entry_delete" if rid.startswith("entry_delete") else "entry_create"
    if rid in ("entry.daily", "entry_delete.daily"):
        edit_account(world, ACCOUNT, **{"daily_max_posts" if rid == "entry.daily" else "daily_max_retracts": 2})
        return Scenario(place=lambda i, inside: hatena_row(world, ACCOUNT if inside else SIBLING, hours_ago(i),
                                                           kind=entry_kind),
                        ask=lambda: hcheck(entry_kind), next_at=lambda: MIDNIGHT)
    if rid in ("entry.blog_daily", "entry_delete.blog_daily"):
        data = world.principal("hatena-main")
        for blog in data["blogs"]:
            if blog["blog_id"] == "a.hatenablog.com":
                blog["daily_max_posts" if rid == "entry.blog_daily" else "daily_max_retracts"] = 2
        world.write_principal(data)
        return Scenario(place=lambda i, inside: hatena_row(world, ACCOUNT if inside else SIBLING, hours_ago(i),
                                                           kind=entry_kind),
                        ask=lambda: hcheck(entry_kind), next_at=lambda: MIDNIGHT)
    if rid in ("entry.principal_daily", "entry_delete.principal_daily"):
        world.edit_principal("hatena-main", **{"daily_max_posts" if rid == "entry.principal_daily"
                                               else "daily_max_retracts": 2})
        # 主体の中なら別の口座（SIBLING）でも数える。外は別の主体（STRANGER）。
        return Scenario(place=lambda i, inside: hatena_row(world, SIBLING if inside else STRANGER, hours_ago(i),
                                                           kind=entry_kind),
                        ask=lambda: hcheck(entry_kind), next_at=lambda: MIDNIGHT)
    if rid == "entry.burst":
        edit_account(world, ACCOUNT, burst={"count": 2, "minutes": 10})
        return Scenario(place=lambda i, inside: hatena_row(world, ACCOUNT if inside else SIBLING, minutes_ago(i)),
                        ask=lambda: hcheck("entry_create"), stop_target=("account", ACCOUNT))
    if rid == "entry.principal_burst":
        world.edit_principal("hatena-main", burst={"count": 2, "minutes": 30})
        return Scenario(place=lambda i, inside: hatena_row(world, SIBLING if inside else STRANGER, minutes_ago(i)),
                        ask=lambda: hcheck("entry_create"), stop_target=("principal", "hatena-main"))
    if rid == "bookmark.daily":
        world.edit_principal("hatena-main", daily_max_bookmarks=2)
        return Scenario(place=lambda i, inside: hatena_row(world, ACCOUNT if inside else STRANGER, hours_ago(i),
                                                           kind="bookmark_add", url=bm_url(i)),
                        ask=lambda: hcheck("bookmark", bookmark_url=bm_url(99)), next_at=lambda: MIDNIGHT)
    if rid == "bookmark.self_daily":
        world.edit_principal("hatena-main", daily_max_self_bookmarks=2)
        return Scenario(place=lambda i, inside: hatena_row(world, ACCOUNT if inside else STRANGER, hours_ago(i),
                                                           kind="bookmark_add", url=bm_url(i), target="own"),
                        ask=lambda: hcheck("bookmark", bookmark_url=bm_url(99), bookmark_target="own"),
                        next_at=lambda: MIDNIGHT)
    if rid == "bookmark.same_host_daily":
        world.edit_principal("hatena-main", bookmark_same_host_daily=2)
        return Scenario(place=lambda i, inside: hatena_row(world, ACCOUNT if inside else STRANGER, hours_ago(i),
                                                           kind="bookmark_add", url=bm_url(i, "same.example.com")),
                        ask=lambda: hcheck("bookmark", bookmark_url=bm_url(99, "same.example.com")),
                        next_at=lambda: MIDNIGHT)
    if rid == "bookmark.burst":
        world.edit_principal("hatena-main", bookmark_burst={"count": 2, "minutes": 10})
        return Scenario(place=lambda i, inside: hatena_row(world, ACCOUNT if inside else STRANGER, minutes_ago(i),
                                                           kind="bookmark_add", url=bm_url(i)),
                        ask=lambda: hcheck("bookmark", bookmark_url=bm_url(99)),
                        next_at=lambda: jst.iso(minutes_ago(0) + datetime.timedelta(minutes=10)))
    raise KeyError(rid)


# 数のある行（`count` 2 に締めて試す）。数の無い行は下の個別の試験。
# 4.3（監査 中-1）: 返信だけの日の上限 `reply.daily` を足した。
COUNTED = ("post.daily", "retract.daily", "reply.burst", "reply.daily", "post.burst", "reaction.daily",
           "entry.daily", "entry.blog_daily", "entry.principal_daily",
           "entry_delete.daily", "entry_delete.blog_daily", "entry_delete.principal_daily",
           "entry.burst", "entry.principal_burst",
           "bookmark.daily", "bookmark.self_daily", "bookmark.same_host_daily", "bookmark.burst")
UNCOUNTED = ("post.quiet", "post.interval", "entry.quiet", "entry.interval", "entry.platform_refusal",
             "bookmark.once", "reaction.quiet")
# 段 2 で足した行（数が媒体の台帳・固定なので `count` 2 に締められない）。試験は `tests/test_limits_stage2.py`。
STAGE2 = ("bookmark.stop", "write.floor", "upload.daily")


def test_表のすべての行に試験がある():
    """柵: 表に行を足して試験を書き忘れると赤。"""
    assert set(COUNTED) | set(UNCOUNTED) | set(STAGE2) == set(limits_policy.ids())
    assert not set(COUNTED) & set(UNCOUNTED) and not set(STAGE2) & (set(COUNTED) | set(UNCOUNTED))


def expected_trait(rule, rid):
    """試し方の口座の媒体で解いた根拠の id（SNS は sns_cfg の threads・はてなは行の媒体）。"""
    media = "threads" if "sns" in rule["family"] or "reaction" in rule["family"] else None
    return limits_policy.resolve(rule, cfg={"media": media} if media else {}, principal_cfg={})["trait"]


def is_stopped(target):
    scope, name = target
    return (principals.stopped(name) if scope == "principal" else guard.stopped(name)) is not None


@pytest.mark.parametrize("rid", COUNTED)
def test_T_P_1_countちょうどまで通り次で振る舞う(world, rid):
    """柵: 行の `on_exceed`・`count`・符丁・`trait` のどれを書き換えても赤。"""
    rule = limits_policy.rule(rid)
    sc = scenario(rid, world)
    sc.place(0, True)
    sc.ask()                       # count ちょうど（2 件目）まで通る
    sc.place(1, True)
    with pytest.raises(guard.GuardRefused) as caught:
        sc.ask()
    e = caught.value
    a = e.assessment
    assert str(e) == rule["refusal"] if rule["on_exceed"] != "stop" else str(e) in ("account_stopped", "principal_stopped")
    assert a["rule"] == rid and a["trait"]["id"] == expected_trait(rule, rid) and a["signal"] == rule["signal"]
    assert a["scope"] == rule["scope"] and a["count"] == 2 and a["used"] == 2
    assert a["code"] == str(e)
    json.dumps(a)  # 構造は JSON にできる
    if rule["on_exceed"] == "stop":
        assert a["outcome"] == "stopped" and e.reason == "burst" and e.next_at is None
        assert str(e) == rule["refusal"]
        assert is_stopped(sc.stop_target)
    else:
        assert a["outcome"] == ("wait" if rule["on_exceed"] == "wait" else "refuse")
        assert e.reason == rule["detail"]
        assert e.next_at == sc.next_at() == a["next_at"]
        # 断るだけ・待たせるだけで止めない
        assert guard.stopped(SNS) is None and guard.stopped(ACCOUNT) is None
        assert principals.stopped("hatena-main") is None


@pytest.mark.parametrize("rid", COUNTED)
def test_T_P_1_scopeの外は数えない(world, rid):
    """柵: 行の `scope` を広げる（別の口座・ブログ・主体の材料を数える）と赤。"""
    sc = scenario(rid, world)
    for i in range(sc.count + 1):
        sc.place(i, False)
    sc.ask()


# ---------------------------------------------------------------- 数の無い行

def refused(fn):
    with pytest.raises(guard.GuardRefused) as caught:
        fn()
    return caught.value


def test_post_quietとpost_interval(world):
    cfg = sns_cfg(quiet_hours=["09:00", "11:00"])
    e = refused(lambda: guard.check(SNS, cfg, "publish", now=NOW))
    assert str(e) == "quiet_hours" and e.assessment["rule"] == "post.quiet"
    assert e.next_at == jst.iso(NOW.replace(hour=11))
    assert str(refused(lambda: guard.check(SNS, cfg, "publish", now=NOW, reply=True))) == "quiet_hours"
    guard.check(SNS, cfg, "retract", now=NOW)      # 削除には掛けない
    cfg = sns_cfg(min_interval_hours=2)
    sns_post(world, SNS, NOW - datetime.timedelta(hours=1))
    e = refused(lambda: guard.check(SNS, cfg, "publish", now=NOW))
    assert str(e) == "too_soon" and e.assessment["rule"] == "post.interval"
    assert e.next_at == jst.iso(NOW + datetime.timedelta(hours=1))
    guard.check(SNS, cfg, "publish", now=NOW, reply=True)   # 返信には掛けない
    guard.check(SNS, cfg, "publish", now=NOW + datetime.timedelta(hours=1))


def test_entry_quietとentry_interval(world):
    hatena_loose(world)
    edit_account(world, ACCOUNT, quiet_hours=["09:00", "11:00"])
    hcheck = lambda kind, now=NOW: guard.check(ACCOUNT, accounts.load_account(ACCOUNT), kind, now=now)
    e = refused(lambda: hcheck("entry_create"))
    assert str(e) == "quiet_hours" and e.assessment["rule"] == "entry.quiet"
    hcheck("entry_delete")                      # 削除には掛けない
    edit_account(world, ACCOUNT, quiet_hours=["23:00", "06:00"], min_interval_hours=2)
    hatena_row(world, ACCOUNT, NOW - datetime.timedelta(hours=1))
    e = refused(lambda: hcheck("entry_update"))
    assert str(e) == "too_soon" and e.assessment["rule"] == "entry.interval"
    hcheck("entry_update", now=NOW + datetime.timedelta(hours=1))


def test_entry_platform_refusal_媒体の断りの印(world):
    hatena_loose(world)
    publish_hold.hold(ACCOUNT, publish_hold.X_NOT_PERMITTED, now=NOW)
    cfg = accounts.load_account(ACCOUNT)
    for kind, kwargs in (("entry_create", {}), ("bookmark", {"bookmark_url": bm_url(1)}), ("schedule", {})):
        e = refused(lambda: guard.check(ACCOUNT, cfg, kind, now=NOW, **kwargs))
        assert str(e) == "account_stopped" and e.reason is None and e.next_at is None
        assert e.assessment["rule"] == "entry.platform_refusal" and e.assessment["outcome"] == "stopped"
    assert guard.stopped(ACCOUNT) is None       # 口座の止めの印は置かない（媒体の断りの印のまま）


def test_bookmark_once(world):
    hatena_loose(world)
    hatena_row(world, ACCOUNT, NOW - datetime.timedelta(days=3), kind="bookmark_add", url=bm_url(1))
    cfg = accounts.load_account(ACCOUNT)
    e = refused(lambda: guard.check(ACCOUNT, cfg, "bookmark", now=NOW, bookmark_url=bm_url(1)))
    assert str(e) == "bookmark_once" and e.reason == "same_principal"
    assert e.assessment["rule"] == "bookmark.once" and e.assessment["detail"] == "same_principal"
    guard.check(ACCOUNT, cfg, "bookmark", now=NOW, bookmark_url=bm_url(1), bookmark_once=False)
    guard.check(ACCOUNT, cfg, "bookmark", now=NOW, bookmark_url=bm_url(2))


def test_reaction_quiet(world):
    cfg = sns_cfg(quiet_hours=["09:00", "11:00"])
    e = refused(lambda: guard.check_reaction(SNS, cfg, "repost", now=NOW))
    assert str(e) == "quiet_hours" and e.assessment["rule"] == "reaction.quiet"
    guard.check_reaction(SNS, cfg, "like", now=NOW)
    guard.check_reaction(SNS, cfg, "repost", undo=True, now=NOW)


# ---------------------------------------------------------------- 止め・安全装置・順

def test_止めの印の断りにassessment(world):
    hatena_loose(world)
    guard.stop(SNS, "owner", now=NOW)
    e = refused(lambda: guard.check(SNS, sns_cfg(), "publish", now=NOW))
    assert str(e) == "account_stopped" and e.reason == "owner"
    assert e.assessment["rule"] == "account.stopped" and e.assessment["outcome"] == "stopped"
    e = refused(lambda: guard.check_reaction(SNS, sns_cfg(), "like", now=NOW))
    assert e.assessment["rule"] == "account.stopped"
    principals.stop("hatena-main", "owner", now=NOW)
    e = refused(lambda: guard.check(ACCOUNT, accounts.load_account(ACCOUNT), "entry_create", now=NOW))
    assert str(e) == "principal_stopped" and e.assessment["rule"] == "principal.stopped"


def test_印を置けない置き場は止めずに断る(world, monkeypatch):
    """10/15: 安全装置の `stop` は印を置けない CLI では `burst_limit`（詳細 burst・時刻なし）に落ちる。"""
    monkeypatch.setattr(guard, "stop_place_usable", lambda account: False)
    cfg = sns_cfg(burst={"count": 1, "minutes": 10})
    sns_post(world, SNS, NOW - datetime.timedelta(minutes=1))
    e = refused(lambda: guard.check(SNS, cfg, "publish", now=NOW, cli=True))
    assert str(e) == "burst_limit" and e.reason == "burst" and e.next_at is None
    assert e.assessment["rule"] == "post.burst" and e.assessment["outcome"] == "refuse"
    assert guard.stopped(SNS) is None


def test_表の順のfirst_hit_日の上限と連投が同時なら日の上限で断り止めない(world):
    """今の順（日の上限 → burst）を写す。§6.6 の「安全装置が先」にすると止める側に変わり赤（設計の末尾）。"""
    cfg = sns_cfg(daily_max_posts=2, burst={"count": 2, "minutes": 10})
    sns_post(world, SNS, NOW - datetime.timedelta(minutes=2))
    sns_post(world, SNS, NOW - datetime.timedelta(minutes=1))
    e = refused(lambda: guard.check(SNS, cfg, "publish", now=NOW))
    assert str(e) == "daily_limit" and e.assessment["rule"] == "post.daily"
    assert guard.stopped(SNS) is None


def test_同じ配信は窓の中で1件(world):
    """S5 §6: `entry.burst` の鍵は target。同じ配信への 2 回目は数を増やさない。"""
    hatena_loose(world)
    edit_account(world, ACCOUNT, burst={"count": 2, "minutes": 10})
    hatena_row(world, ACCOUNT, NOW - datetime.timedelta(minutes=3), delivery="dl_aaaaaaaaaaaa")
    hatena_row(world, ACCOUNT, NOW - datetime.timedelta(minutes=2), delivery="dl_bbbbbbbbbbbb")
    cfg = accounts.load_account(ACCOUNT)
    guard.check(ACCOUNT, cfg, "entry_update", now=NOW, delivery="dl_aaaaaaaaaaaa")
    e = refused(lambda: guard.check(ACCOUNT, cfg, "entry_update", now=NOW, delivery="dl_cccccccccccc"))
    assert str(e) == "account_stopped" and e.assessment["rule"] == "entry.burst"


def test_返信は並んだものの後ろ(world):
    """3.25.0: 並んでいる返信が先にあれば窓が空いていても `wait`。"""
    cfg = sns_cfg(burst={"count": 3, "minutes": 10})
    later = NOW + datetime.timedelta(minutes=5)
    e = refused(lambda: guard.check(SNS, cfg, "publish", now=NOW, reply=True, pending_replies=[later]))
    assert str(e) == "burst_limit" and e.assessment["outcome"] == "wait" and e.assessment["rule"] == "reply.burst"
    assert e.next_at == jst.iso(later + datetime.timedelta(minutes=1))


# ---------------------------------------------------------------- 見立ての道（純粋）

def fake_rule(rid, signal, on_exceed, refusal="daily_limit"):
    return {"id": rid, "signal": signal, "on_exceed": on_exceed, "phase": "assess", "purpose": "platform",
            "scope": "account", "window": None, "count": 0, "refusal": refusal, "detail": None,
            "trait": "x." + rid, "origin": "default", "key": "op"}


def test_adviseは断らずadvisoriesに積む():
    req = assess.Request(account="a", cfg={}, action="reply", now=NOW, family="sns", counterpart_engaged=False)
    m = assess.Materials()
    result = assess.evaluate([fake_rule("reply.counterpart", "counterpart", "advise")], req, m)
    assert result["outcome"] == "pass" and [a["rule"] for a in result["advisories"]] == ["reply.counterpart"]
    assert result["advisories"][0]["code"] is None
    # 相手が呼んだ（または分からない）なら何も出さない
    req.counterpart_engaged = None
    assert assess.evaluate([fake_rule("reply.counterpart", "counterpart", "advise")], req, m)["advisories"] == []


def test_adviseのあとの断りにもadvisoriesが付く():
    req = assess.Request(account="a", cfg={}, action="reply", now=NOW, family="sns", counterpart_engaged=False)
    m = assess.Materials(loaders={"publish_hold": {"reason": "x"}})
    result = assess.evaluate([fake_rule("r.c", "counterpart", "advise"),
                              fake_rule("r.h", "recent_platform_refusal", "halted", "account_stopped")], req, m)
    assert result["outcome"] == "stopped" and result["rule"] == "r.h"
    assert [a["rule"] for a in result["advisories"]] == ["r.c"]


def test_strongest():
    rows = [{"outcome": "advise"}, {"outcome": "wait"}, {"outcome": "refuse"}, {"outcome": "pass"}]
    assert assess.strongest(rows)["outcome"] == "refuse"
    assert assess.strongest([{"outcome": "wait"}, {"outcome": "advise"}])["outcome"] == "wait"
    assert assess.strongest([]) is None


def test_材料は1回だけ読む():
    calls = []
    m = assess.Materials(loaders={"x": lambda: calls.append(1) or 5})
    assert m.get("x") == 5 and m.get("x") == 5 and calls == [1]
