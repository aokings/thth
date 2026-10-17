"""段 2: ブックマークと安全装置の分離・台帳との写像（設計 上限と連投の柵 §6・§12 の段 2・§13 の T-P-2〜4・T-P-10）。

見るのは:
  - 写像（監査 中-1）: 台帳の `defaults` の行はすべて `LEDGER_MAP` か `LEDGER_NOT_ROWS` を通る。写像に無い行・
    形の食い違う行は `build_policy` が断る。台帳から既定を消すと台帳から生まれる行（`bookmark.stop`）が消える。
  - 表は import のときに作らない（壊れた台帳でも `thth media check` が理由を言う・柵は `invalid_media_traits`）。
  - 数: 口座・主体の台帳の明示の値（VM の `daily_max_bookmarks: 50` など）が勝ち、無ければ媒体の台帳の数。
  - T-P-2 分けた窓: 10 分にブックマーク 4 件 → 記事の公開が通る・11 件目で `burst_limit`（止めない）・
    31 件目で `principal_stopped`（止める）。
  - T-P-3 最低線: 行の無い媒体で 20 件まで通り 21 件目で `account_stopped`（`write.floor`）・SNS は `post.burst`。
  - T-P-4 順: 表の順の first-hit（今の振る舞い）。設計 §6.6 の順（安全装置が先・強い方）にすると変わる 2 か所。
  - T-P-10 鍵: 同じ配信の 2 回目は `target` の行で 1 件・`op` の行で 2 件。
  - `upload.daily`: フォトライフは数を表から引くだけ。

はてなにも SNS にも出ない（`guard.check` を直に・記録は `sent.write` で置く）。
"""
from __future__ import annotations

import datetime
import json
import shutil

import pytest

from tests.hatena_s1_helpers import world  # noqa: F401
from tests.test_assess import (ACCOUNT, NOW, SIBLING, SNS, bm_url, hatena_loose, hatena_row, refused,
                               sns_cfg, sns_post)
from thth import accounts, assess, cli, guard, hatena_fotolife, jst, limits_policy, media_traits as mt
from thth import principals


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """repo の媒体の台帳の写し（書き換えてよい）を柵が読むようにする。"""
    dest = tmp_path / "media_traits"
    shutil.copytree(mt.TRAITS_DIR, dest)
    monkeypatch.setattr(mt, "TRAITS_DIR", dest)
    limits_policy.reset()
    yield dest
    limits_policy.reset()


def edit_ledger(directory, media, fn):
    path = directory / f"{media}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    fn(doc)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    limits_policy.reset()


def trait_of(doc, name):
    return next(t for t in doc["traits"] if t["id"] == f"{doc['media']}.{name}")


def by_id(rows):
    return {row["id"]: row for row in rows}


def minutes_before(n):
    return NOW - datetime.timedelta(minutes=n)


# ---------------------------------------------------------------- 写像（中-1）

def test_台帳の既定の行はすべて写像か理由つきの除外を通る():
    """柵: 台帳に行を足して写像に書き忘れると赤（黙って効かない行を作らない）。"""
    seen = set()
    for row in mt.collect_defaults():
        where = (row["media"], row["id"])
        assert (where in limits_policy.LEDGER_MAP) != (where in limits_policy.LEDGER_NOT_ROWS), where
        seen.add(where)
    # 写像の側にも余りが無い（台帳から消えた行を写像が指し続けない）
    assert set(limits_policy.LEDGER_MAP) | set(limits_policy.LEDGER_NOT_ROWS) == seen
    assert set(limits_policy.LEDGER_MAP.values()) <= set(limits_policy.ids())
    assert set(limits_policy.LEDGER_BORN) <= set(limits_policy.LEDGER_MAP.values())


def test_段0にだけあった5つのidの行き先():
    """監査 中-1 の 5 つ: 2 つは表の行（台帳から生まれる）、3 つは理由つきで表の行にしない。"""
    assert {"bookmark.stop", "upload.daily"} <= set(limits_policy.ids())
    assert limits_policy.rule("bookmark.stop")["trait"] == "hatenabookmark.mechanical_high_frequency"
    assert limits_policy.rule("upload.daily")["trait"] == "fotolife.no_api_limit"
    excluded = {rid for _m, rid in limits_policy.LEDGER_NOT_ROWS}
    assert excluded == {"bookmark.retry_once", "post.platform_refusal", "reply.counterpart"}
    assert all(limits_policy.LEDGER_NOT_ROWS.values())


def test_SNSの4媒体の行は表の1行に集まり根拠は媒体ごと():
    row = limits_policy.rule("post.daily")
    assert set(row["ledger"]) == {"threads", "x", "bluesky", "mastodon"}
    traits = {m: limits_policy.resolve(row, cfg={"media": m})["trait"] for m in row["ledger"]}
    assert traits == {"threads": "threads.api_posts_24h", "x": "x.account_post_limit",
                      "bluesky": "bluesky.pds_points", "mastodon": "mastodon.api_rate"}
    # Threads の反応は台帳に根拠が無い → thth の線
    assert limits_policy.resolve(limits_policy.rule("reaction.daily"), cfg={"media": "threads"})["trait"] \
        == "thth:reaction.daily"


def test_写像に無い台帳の行は断る():
    item = dict(next(r for r in mt.collect_defaults() if r["id"] == "bookmark.daily"), id="bookmark.weekly")
    with pytest.raises(ValueError, match="unmapped_ledger_row"):
        limits_policy.build_policy([item])
    with pytest.raises(ValueError, match="invalid_ledger_row"):
        limits_policy.build_policy([{"id": "bookmark.daily", "media": "hatenabookmark"}])


@pytest.mark.parametrize("field,value", [
    ("on_exceed", "refuse"), ("scope", "account"), ("signal", "same_host"), ("key", "url"),
    ("actions", ["bookmark_add"]), ("applies_to", ["bookmark_add"]), ("refusal", "bookmark_limit"),
    ("window", {"day": "jst"}),
])
def test_台帳と表の形が食い違えば断る(field, value):
    """柵: 台帳の形を表と違えて書くと赤（4.0.1 と段 1 の振る舞いと食い違う台帳を黙って受けない）。"""
    rows = mt.collect_defaults()
    for row in rows:
        if row["id"] == "bookmark.burst":
            row[field] = value
    with pytest.raises(ValueError, match="ledger_shape_mismatch"):
        limits_policy.build_policy(rows)


def test_台帳から既定を消すと台帳から生まれる行が消える(ledger):
    edit_ledger(ledger, "hatenabookmark",
                lambda d: trait_of(d, "mechanical_high_frequency").pop("defaults"))
    assert "bookmark.stop" not in limits_policy.ids()
    # THTH の行は残り、根拠は thth: に戻る
    edit_ledger(ledger, "hatenabookmark", lambda d: trait_of(d, "normal_batch_use")["defaults"].pop("bookmark.burst"))
    assert limits_policy.rule("bookmark.burst")["trait"] == "thth:bookmark.burst"


def test_表はimportのときに作らない_壊れた台帳でもmedia_checkは理由を言う(ledger, capsys):
    edit_ledger(ledger, "hatenabookmark",
                lambda d: trait_of(d, "normal_batch_use").update(confidence="high"))
    assert limits_policy._POLICY is None              # reset のあと誰も作っていない
    assert cli.main(["media", "check"]) == 1
    assert "inferred_must_be_low" in capsys.readouterr().err
    # 柵は既定に倒さず断る
    with pytest.raises(mt.MediaTraitsError) as caught:
        limits_policy.rules_for("hatenabookmark", "bookmark_add", cfg={}, principal_cfg={})
    assert caught.value.code == mt.INVALID


# ---------------------------------------------------------------- 数の出所

def test_明示が無ければ媒体の台帳の数_明示があればそれが勝つ(ledger):
    edit_ledger(ledger, "hatenabookmark",
                lambda d: trait_of(d, "normal_batch_use")["defaults"]["bookmark.burst"].update(
                    count=12, window={"minutes": 15}))
    rows = by_id(limits_policy.rules_for("hatenabookmark", "bookmark_add", cfg={}, principal_cfg={}))
    assert (rows["bookmark.burst"]["count"], rows["bookmark.burst"]["window"]) == (12, {"minutes": 15})
    assert rows["bookmark.burst"]["origin"] == "default"
    rows = by_id(limits_policy.rules_for("hatenabookmark", "bookmark_add", cfg={},
                                         principal_cfg={"bookmark_burst": {"count": 4, "minutes": 10}}))
    assert (rows["bookmark.burst"]["count"], rows["bookmark.burst"]["origin"]) == (4, "principal")


def test_VMの台帳の明示値がそのまま勝つ():
    """VM の主体の台帳（`daily_max_bookmarks: 50` など）は媒体の台帳の既定より強い。"""
    vm = {"daily_max_bookmarks": 50, "bookmark_same_host_daily": 5, "daily_max_self_bookmarks": 2,
          "bookmark_burst": {"count": 15, "minutes": 10}}
    rows = by_id(limits_policy.rules_for("hatenabookmark", "bookmark_add", cfg={}, principal_cfg=vm))
    assert rows["bookmark.daily"]["count"] == 50 and rows["bookmark.daily"]["origin"] == "principal"
    assert rows["bookmark.same_host_daily"]["count"] == 5
    assert rows["bookmark.self_daily"]["count"] == 2
    assert rows["bookmark.burst"]["count"] == 15
    rows = by_id(limits_policy.rules_for("sns", "post", cfg={"media": "x", "daily_max_posts": 20}))
    assert rows["post.daily"]["count"] == 20 and rows["post.daily"]["origin"] == "account"


def test_推測の数は見立ての根拠に確かさlowで出る(world):
    hatena_loose(world)
    world.edit_principal("hatena-main", bookmark_burst={"count": 1, "minutes": 10})
    hatena_row(world, ACCOUNT, minutes_before(1), kind="bookmark_add", url=bm_url(1))
    e = refused(lambda: guard.check(ACCOUNT, accounts.load_account(ACCOUNT), "bookmark", now=NOW,
                                    bookmark_url=bm_url(2)))
    assert e.assessment["trait"] == {"id": "hatenabookmark.normal_batch_use", "confidence": "low",
                                     "count_evidence": "inferred", "count_confidence": "low"}


# ---------------------------------------------------------------- T-P-2 分けた窓

def bookmarks(world, account, n, start=0, *, minutes=9):
    for i in range(start, start + n):
        hatena_row(world, account, NOW - datetime.timedelta(minutes=minutes, seconds=-i), kind="bookmark_add",
                   url=bm_url(1000 + i))


def test_T_P_2_ブックマーク4件でも記事の公開は通る(world):
    """柵: `entry.principal_burst`／`entry.burst` の `actions` にブックマークを戻すと赤（4.0.1 の記録 4）。"""
    bookmarks(world, ACCOUNT, 4)
    cfg = accounts.load_account(ACCOUNT)
    guard.check(ACCOUNT, cfg, "entry_create", now=NOW)
    guard.check(SIBLING, accounts.load_account(SIBLING), "entry_create", now=NOW)
    assert principals.stopped("hatena-main") is None and guard.stopped(ACCOUNT) is None


def test_T_P_2_11件目は待たせるだけで止めない(world):
    bookmarks(world, ACCOUNT, 10)
    e = refused(lambda: guard.check(ACCOUNT, accounts.load_account(ACCOUNT), "bookmark", now=NOW,
                                    bookmark_url=bm_url(1)))
    assert str(e) == "burst_limit" and e.reason == "bookmark_burst"
    assert e.assessment["rule"] == "bookmark.burst" and e.assessment["outcome"] == "wait"
    # 窓の最も古い 1 件（9 分前）が出る時刻
    assert e.next_at == jst.iso(NOW - datetime.timedelta(minutes=9) + datetime.timedelta(minutes=10))
    assert principals.stopped("hatena-main") is None and guard.stopped(ACCOUNT) is None
    # 消すの連投にも同じ窓（4.0.1・台帳の applies_to を配列にした・中-1）
    e = refused(lambda: guard.check(ACCOUNT, accounts.load_account(ACCOUNT), "bookmark_delete", now=NOW))
    assert str(e) == "burst_limit" and e.assessment["rule"] == "bookmark.burst"


def test_T_P_2_31件目は主体を止める(world):
    """柵: `bookmark.stop` を表から外す・`bookmark.burst` の後ろに置くと赤（31 件目が待たせるだけになる）。"""
    bookmarks(world, ACCOUNT, 15)
    bookmarks(world, SIBLING, 15, start=100)       # 2 レーンで 30（口座の最低線 20 には届かない）
    e = refused(lambda: guard.check(ACCOUNT, accounts.load_account(ACCOUNT), "bookmark", now=NOW,
                                    bookmark_url=bm_url(1)))
    assert str(e) == "principal_stopped" and e.reason == "burst" and e.next_at is None
    a = e.assessment
    assert a["rule"] == "bookmark.stop" and a["outcome"] == "stopped" and (a["used"], a["count"]) == (30, 30)
    assert a["trait"]["id"] == "hatenabookmark.mechanical_high_frequency" and a["trait"]["count_confidence"] == "low"
    assert principals.stopped("hatena-main")["reason"] == "burst"
    assert guard.stopped(ACCOUNT) is None
    # 主体の止めはすべてに効く（S1 のまま）: 記事も断る
    e = refused(lambda: guard.check(SIBLING, accounts.load_account(SIBLING), "entry_create", now=NOW))
    assert str(e) == "principal_stopped"


def test_T_P_2_30件ちょうどまでは止めない(world):
    world.edit_principal("hatena-main", bookmark_burst={"count": 100, "minutes": 10})
    bookmarks(world, ACCOUNT, 14)
    bookmarks(world, SIBLING, 15, start=100)
    guard.check(ACCOUNT, accounts.load_account(ACCOUNT), "bookmark", now=NOW, bookmark_url=bm_url(1))
    assert principals.stopped("hatena-main") is None


# ---------------------------------------------------------------- T-P-3 共通の最低線

FAKE = "fake-a"


def test_T_P_3_行の無い媒体は20件まで通り21件目で口座を止める(world):
    """柵: `write.floor` を表から外すと赤（行を書き忘れた媒体が止まらない）。"""
    cfg = {"account": FAKE, "media": "fake"}
    for i in range(20):
        sns_post(world, FAKE, minutes_before(9) + datetime.timedelta(seconds=i))
    for i in range(3):   # 日の上限・burst の行は当たらない（行の無い媒体）
        sns_post(world, FAKE, NOW - datetime.timedelta(hours=2 + i))
    e = refused(lambda: guard.check(FAKE, cfg, "publish", now=NOW))
    assert str(e) == "account_stopped" and e.reason == "burst"
    assert e.assessment["rule"] == "write.floor" and (e.assessment["used"], e.assessment["count"]) == (20, 20)
    assert guard.stopped(FAKE)["reason"] == "burst"


def test_T_P_3_行の無い媒体の20件目は通る(world):
    cfg = {"account": FAKE, "media": "fake"}
    for i in range(19):
        sns_post(world, FAKE, minutes_before(9) + datetime.timedelta(seconds=i))
    guard.check(FAKE, cfg, "publish", now=NOW)
    assert guard.stopped(FAKE) is None


def test_T_P_3_SNSは先にpost_burstが当たる(world):
    cfg = sns_cfg(burst={"count": 3, "minutes": 10})
    for i in range(25):
        sns_post(world, SNS, minutes_before(9) + datetime.timedelta(seconds=i))
    e = refused(lambda: guard.check(SNS, cfg, "publish", now=NOW))
    assert str(e) == "account_stopped" and e.assessment["rule"] == "post.burst"


def test_T_P_3_はてなの口座にも効く_窓を上げた主体の21件目(world):
    """主体の台帳でブックマークの窓を上げても、口座の書きが 10 分に 20 を超えれば口座を止める（§6.4）。"""
    hatena_loose(world)
    bookmarks(world, ACCOUNT, 20)
    e = refused(lambda: guard.check(ACCOUNT, accounts.load_account(ACCOUNT), "bookmark", now=NOW,
                                    bookmark_url=bm_url(1)))
    assert str(e) == "account_stopped" and e.assessment["rule"] == "write.floor"
    assert guard.stopped(ACCOUNT)["reason"] == "burst" and principals.stopped("hatena-main") is None


# ---------------------------------------------------------------- T-P-4 当てる順

def test_T_P_4_今の順_日の上限と連投が同時なら断るだけ_安全装置が先なら止める(world, monkeypatch):
    """段 1 の決め 1 の 1 か所目。推しは今の順（table）。`safety_first` にすると口座を止める側に変わる。"""
    cfg = sns_cfg(daily_max_posts=2, burst={"count": 2, "minutes": 10})
    sns_post(world, SNS, minutes_before(2))
    sns_post(world, SNS, minutes_before(1))
    e = refused(lambda: guard.check(SNS, cfg, "publish", now=NOW))
    assert str(e) == "daily_limit" and guard.stopped(SNS) is None
    monkeypatch.setattr(assess, "ORDER", "safety_first")
    e = refused(lambda: guard.check(SNS, cfg, "publish", now=NOW))
    assert str(e) == "account_stopped" and e.assessment["rule"] == "post.burst"
    assert guard.stopped(SNS) is not None


def test_T_P_4_今の順_窓と1URLに1回が同時なら待たせる_強い方なら断る(world, monkeypatch):
    """段 1 の決め 1 の 2 か所目。今は `burst_limit`（打ち直せば通ると LLM が読む）、強い方なら `bookmark_once`。"""
    world.edit_principal("hatena-main", bookmark_burst={"count": 1, "minutes": 10})
    hatena_row(world, ACCOUNT, minutes_before(1), kind="bookmark_add", url=bm_url(1))
    ask = lambda: guard.check(ACCOUNT, accounts.load_account(ACCOUNT), "bookmark", now=NOW, bookmark_url=bm_url(1))
    assert str(refused(ask)) == "burst_limit"
    monkeypatch.setattr(assess, "ORDER", "safety_first")
    assert str(refused(ask)) == "bookmark_once"


def test_T_P_4_aで断る依頼は印を置かない(world):
    hatena_loose(world)
    world.edit_principal("hatena-main", bookmark_same_host_daily=1)
    hatena_row(world, ACCOUNT, NOW - datetime.timedelta(hours=1), kind="bookmark_add", url=bm_url(1, "same.example.com"))
    e = refused(lambda: guard.check(ACCOUNT, accounts.load_account(ACCOUNT), "bookmark", now=NOW,
                                    bookmark_url=bm_url(2, "same.example.com")))
    assert str(e) == "bookmark_host_limit" and e.assessment["purpose"] == "platform"
    assert principals.stopped("hatena-main") is None and guard.stopped(ACCOUNT) is None


def fake_rule(rid, on_exceed, phase="assess", signal="counterpart"):
    return {"id": rid, "signal": signal, "on_exceed": on_exceed, "phase": phase, "purpose": "platform",
            "scope": "account", "window": None, "count": 0, "refusal": "burst_limit", "detail": None,
            "trait": "x." + rid, "origin": "default", "key": "op"}


@pytest.mark.parametrize("order", ["table", "safety_first"])
def test_T_P_4_adviseとwaitが同時ならwait_adviseだけならpass(order):
    req = assess.Request(account="a", cfg={}, action="reply", now=NOW, family="sns", counterpart_engaged=False)
    m = assess.Materials()
    result = assess.evaluate([fake_rule("a.advise", "advise"), fake_rule("a.wait", "wait")], req, m, order=order)
    assert result["outcome"] == "wait" and [x["rule"] for x in result["advisories"]] == ["a.advise"]
    result = assess.evaluate([fake_rule("a.advise", "advise")], req, m, order=order)
    assert result["outcome"] == "pass" and result["advisories"]


def test_T_P_4_強さの順():
    """柵: 強さの順（refuse > wait > advise > pass）を崩すと赤。"""
    req = assess.Request(account="a", cfg={}, action="reply", now=NOW, family="sns", counterpart_engaged=False)
    rules = [fake_rule("a.wait", "wait"), fake_rule("a.refuse", "refuse")]
    assert assess.evaluate(rules, req, assess.Materials())["rule"] == "a.wait"          # 表の順
    assert assess.evaluate(rules, req, assess.Materials(), order="safety_first")["rule"] == "a.refuse"
    rules = [fake_rule("a.refuse", "refuse"), fake_rule("a.stop", "stop", phase="safety")]
    assert assess.evaluate(rules, req, assess.Materials(), order="safety_first")["rule"] == "a.stop"
    with pytest.raises(ValueError):
        assess.evaluate(rules, req, assess.Materials(), order="nope")


def test_当てる順の既定は今の振る舞い():
    assert assess.ORDER == "table"


# ---------------------------------------------------------------- T-P-10 2 重送信

def test_T_P_10_同じ配信の2回目はtargetの行で1件_opの行で2件():
    """柵: `key` を取り違えると赤（`target` と `op` の数え方が入れ替わる）。"""
    t1, t2 = minutes_before(3), minutes_before(2)
    events = [("entry_create", t1, ("delivery", "dl_a")), ("entry_update", t2, ("delivery", "dl_a"))]
    m = assess.Materials(loaders={"events": events})
    req = assess.Request(account="a", cfg={}, action="entry_update", now=NOW, family="hatenablog", delivery="dl_a")

    def row(key):
        return {"id": "entry.burst", "signal": "frequency", "on_exceed": "refuse", "phase": "assess",
                "purpose": "safety", "scope": "account", "window": {"minutes": 10}, "count": 0, "key": key,
                "actions": ("entry_create", "entry_update", "entry_delete"), "refusal": "burst_limit",
                "detail": None, "trait": "t"}
    target = assess.SIGNAL_FUNCS["frequency"](row("target"), req, m)
    op = assess.SIGNAL_FUNCS["frequency"](row("op"), req, m)
    assert target["used"] == 1 and op["used"] == 2


def test_分の窓は行の動作だけを数える():
    """記録の出来事のうち行の `actions` に入るものだけ（SNS の記録の `post` は投稿・返信・記事の行に入る）。"""
    assert assess.counts_as("post", ("reply",)) and assess.counts_as("post", ("entry_create",))
    assert assess.counts_as("retract", ("entry_delete",))
    assert not assess.counts_as("bookmark_add", ("entry_create", "entry_update", "entry_delete"))
    assert not assess.counts_as("post", ("bookmark_add",))


# ---------------------------------------------------------------- upload.daily（フォトライフ）

def test_フォトライフの1日の上げは表から数を引く(ledger):
    assert hatena_fotolife.daily_max_uploads({}) == 30
    assert hatena_fotolife.daily_max_uploads({"fotolife": {"daily_max_uploads": 5}}) == 5
    edit_ledger(ledger, "fotolife",
                lambda d: trait_of(d, "no_api_limit")["defaults"]["upload.daily"].update(count=40))
    assert hatena_fotolife.daily_max_uploads({}) == 40
    assert hatena_fotolife.daily_max_uploads({"fotolife": {"daily_max_uploads": 5}}) == 5
    row = limits_policy.resolved("upload.daily", principal_cfg={})
    assert (row["on_exceed"], row["refusal"]) == ("hold", "fotolife_daily_limit")
    assert "fotolife_daily_limit" in hatena_fotolife.WAITING     # hold = run を失敗にせず次へ（今のまま）
