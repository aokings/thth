"""段 3: 設定の面（設計 上限と連投の柵 §8・§12 の段 3・§13 の T-P-5〜7・§14・末尾の「段 3 で決めたこと」）。

見るのは:
  - T-P-5 別名: 今の鍵（`daily_max_bookmarks` など）だけでも表の行に効く・`limits` があれば `limits` が勝つ・
    知らない id・形違い・別の台帳の行・口座の媒体に当たらない行・`null` は `invalid_guard_limits`。
  - T-P-6 set: `thth principal set p bookmark.burst 20 10` が `limits` に書く・今の名前は今の鍵に書き、指す行の
    `limits` を外す・`-` で外す・締める向きだけの口（`tighten_only`）。
  - T-P-7 VM の台帳の形: 写し（秘密なし）が loader を通り、効く数が 4.1.0 と同じ・ファイルを書き換えない。
  - 最低線 `write.floor` は持ち主が明示すれば上げられる（理由が要る・10 分に 100 まで・変更ログ・show）。
  - `GUARD_DEFAULTS` などは表（媒体の台帳）から引く。
  - `show`（`thth account status`・`thth principal show`）の表: 行ごとの数・出所・確かさ。

はてなにも SNS にも出ない（`guard.check` を直に・記録は `sent.write` で置く）。
"""
from __future__ import annotations

import datetime
import json
import pathlib
import shutil

import pytest

from tests.hatena_s1_flow_helpers import call
from tests.hatena_s1_helpers import world  # noqa: F401
from tests.test_assess import ACCOUNT, NOW, SNS, bm_url, hatena_loose, hatena_row, sns_post
from thth import (accounts, admin_log, guard, hatena_fotolife, jst, limits_config, limits_policy,
                  media_traits as mt, principals)

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "limits_stage3"


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


def minutes_before(n):
    return NOW - datetime.timedelta(minutes=n)


def refused(fn):
    with pytest.raises(guard.GuardRefused) as info:
        fn()
    return info.value


def resolved(rule_id, *, cfg=None, principal_cfg=None):
    return limits_policy.resolved(rule_id, cfg=cfg or {}, principal_cfg=principal_cfg)


def sns_account(world, name=SNS, **values):
    """SNS の口座の台帳（threads・隔離した置き場）。"""
    root = pathlib.Path(__file__).resolve().parent.parent / "accounts.example"
    data = json.loads((root / "threads.json").read_text(encoding="utf-8"))
    data.update({"account": name, "project": name, "repo_dir": str(world.tmp / "repo"),
                 "env": str(world.tmp / "x.env"), "token": str(world.tmp / "x.token")})
    data.update(values)
    world.write_account(data)
    return name


# ---------------------------------------------------------------- 静的な形が表と食い違わない

def test_書ける行の形は表と同じ():
    """柵: `limits_config` の単位・窓の種類が表と食い違うと赤（書けるのに効かない・効くのに書けない）。"""
    for holder, rows in limits_config.HOLDERS.items():
        for rule_id, (kind, alias, _fits) in rows.items():
            row = limits_policy.rule(rule_id)
            assert row["scope"] == holder, rule_id
            window = row["window"]
            if kind in ("day", "uploads"):
                assert window == "day", rule_id
            else:
                assert isinstance(window, dict) and "minutes" in window, rule_id
            if alias is not None and not alias.startswith("fotolife."):
                assert row["param"][1] == alias, rule_id
    # 書けない行は今の場所（夜間・最短間隔・ブログの欄・1 URL に 1 回・媒体の断りの印）
    for rule_id in ("post.quiet", "post.interval", "entry.blog_daily", "bookmark.once", "entry.platform_refusal"):
        assert limits_config.holder_of(rule_id) is None


ALIASES = [
    ("account", {"media": "threads"}, "daily_max_posts", 12, ("post.daily",)),
    ("account", {"media": "threads"}, "daily_max_retracts", 4, ("retract.daily",)),
    ("account", {"media": "threads"}, "burst", {"count": 5, "minutes": 20}, ("post.burst", "reply.burst")),
    ("account", {"media": "x"}, "daily_max_reactions", 7, ("reaction.daily",)),
    ("account", {"media": "hatenablog"}, "daily_max_posts", 3, ("entry.daily",)),
    ("account", {"media": "hatenablog"}, "daily_max_retracts", 2, ("entry_delete.daily",)),
    ("account", {"media": "hatenablog"}, "burst", {"count": 4, "minutes": 15}, ("entry.burst",)),
    ("principal", {}, "daily_max_posts", 9, ("entry.principal_daily",)),
    ("principal", {}, "daily_max_retracts", 1, ("entry_delete.principal_daily",)),
    ("principal", {}, "burst", {"count": 2, "minutes": 40}, ("entry.principal_burst",)),
    ("principal", {}, "daily_max_bookmarks", 50, ("bookmark.daily",)),
    ("principal", {}, "daily_max_self_bookmarks", 2, ("bookmark.self_daily",)),
    ("principal", {}, "bookmark_same_host_daily", 5, ("bookmark.same_host_daily",)),
    ("principal", {}, "bookmark_burst", {"count": 20, "minutes": 10}, ("bookmark.burst",)),
]


@pytest.mark.parametrize("holder,base,key,value,rows", ALIASES)
def test_T_P_5_今の鍵だけで表の行に効く(holder, base, key, value, rows):
    """柵: 別名の表から 1 行消すと赤（VM の台帳の今の鍵が黙って効かなくなる）。"""
    cfg = dict(base, **{key: value})
    for rule_id in rows:
        row = resolved(rule_id, **({"cfg": cfg} if holder == "account" else {"principal_cfg": cfg}))
        count = value["count"] if isinstance(value, dict) else value
        assert (row["count"], row["origin"], row["via"]) == (count, holder, f"alias:{key}"), rule_id
        if isinstance(value, dict):
            assert row["window"] == {"minutes": value["minutes"]}


def test_T_P_5_フォトライフの今の鍵も別名():
    row = resolved("upload.daily", principal_cfg={"fotolife": {"daily_max_uploads": 70}})
    assert (row["count"], row["origin"], row["via"]) == (70, "principal", "alias:fotolife.daily_max_uploads")
    row = resolved("upload.daily", principal_cfg={"fotolife": {"daily_max_uploads": 70},
                                                  "limits": {"upload.daily": 90}})
    assert (row["count"], row["via"]) == (90, "limits")
    assert hatena_fotolife.daily_max_uploads({"limits": {"upload.daily": 90}}) == 90
    assert hatena_fotolife.limits({"limits": {"upload.daily": 90}})["daily_max_uploads"] == 90


def test_T_P_5_limitsが今の鍵より勝つ():
    p = {"daily_max_bookmarks": 3, "limits": {"bookmark.daily": 40, "bookmark.burst": {"count": 20, "minutes": 5}}}
    row = resolved("bookmark.daily", principal_cfg=p)
    assert (row["count"], row["origin"], row["via"]) == (40, "principal", "limits")
    assert principals.limits(p)["daily_max_bookmarks"] == 40
    assert principals.limits(p)["bookmark_burst"] == {"count": 20, "minutes": 5}
    a = {"media": "threads", "burst": {"count": 9, "minutes": 10}, "limits": {"post.burst": {"count": 2, "minutes": 30}}}
    assert resolved("post.burst", cfg=a)["count"] == 2 and resolved("post.burst", cfg=a)["window"] == {"minutes": 30}
    # 返信の窓は今の鍵 `burst` のまま（`limits` に `reply.burst` が無い）
    assert (resolved("reply.burst", cfg=a)["count"], resolved("reply.burst", cfg=a)["via"]) == (9, "alias:burst")
    assert accounts.guard_limits(a)["burst"] == {"count": 2, "minutes": 30}


def test_T_P_5_明示が無ければ媒体の台帳の既定():
    row = resolved("bookmark.daily", principal_cfg={})
    assert (row["count"], row["origin"], row["via"]) == (30, "default", None)
    row = resolved("post.burst", cfg={"media": "threads"})
    assert (row["count"], row["window"], row["origin"]) == (3, {"minutes": 10}, "default")
    assert resolved("bookmark.stop", principal_cfg={})["count"] == 30


BAD_PRINCIPAL = [
    {"bookmark.weekly": 3},                         # 知らない id
    {"post.daily": 3},                              # 口座の行
    {"bookmark.daily": "30"},                       # 形違い
    {"bookmark.daily": None},                       # null は受けない
    {"bookmark.daily": -1},
    {"bookmark.burst": 10},                         # 窓の行に数だけ
    {"bookmark.burst": {"count": 10}},
    {"bookmark.burst": {"count": 10, "minutes": 10, "extra": 1}},
    {"bookmark.burst": {"count": 0, "minutes": 10}},
    {"upload.daily": 0},                            # フォトライフの範囲（1〜500）
    {"write.floor": {"count": 20, "minutes": 10}},  # 最低線は口座の行
]


@pytest.mark.parametrize("limits", BAD_PRINCIPAL)
def test_T_P_5_主体の受け取れないlimitsは断る(world, limits):
    world.edit_principal("hatena-main", limits=limits)
    with pytest.raises(principals.PrincipalError) as info:
        principals.load_principal("hatena-main")
    assert info.value.code == "invalid_guard_limits"


BAD_ACCOUNT = [
    {"entry.daily": 3},                             # はてなの行（SNS の口座）
    {"bookmark.daily": 3},                          # 主体の行
    {"post.daily": True},
    {"write.floor": {"count": 40, "minutes": 10}},  # 上げるのに理由が無い
    {"write.floor": {"count": 101, "minutes": 10, "reason": "r"}},   # 10 分に 100 を超える
    {"write.floor": {"count": 50, "minutes": 4, "reason": "r"}},     # 1 分に 10 を超える
    {"write.floor": {"count": 40, "minutes": 10, "reason": ""}},
    [],
]


@pytest.mark.parametrize("limits", BAD_ACCOUNT)
def test_T_P_5_口座の受け取れないlimitsは断る(world, limits):
    name = sns_account(world, limits=limits)
    with pytest.raises(accounts.AccountError) as info:
        accounts.load_account(name)
    assert "invalid_guard_limits" in str(info.value)


def test_T_P_5_はてなの口座にSNSの行は書けない(world):
    data = world.account(ACCOUNT)
    data["limits"] = {"post.daily": 3}
    world.write_account(data)
    with pytest.raises(accounts.AccountError, match="invalid_guard_limits"):
        accounts.load_account(ACCOUNT)
    data["limits"] = {"entry.daily": 3, "entry.burst": {"count": 2, "minutes": 10}}
    world.write_account(data)
    assert accounts.load_account(ACCOUNT)["limits"]["entry.daily"] == 3


# ---------------------------------------------------------------- 柵が limits を読む

def test_SNSの口座の連投の窓をlimitsで締めると2件目のあとで止める(world):
    cfg = {"account": SNS, "media": "threads", "daily_max_posts": 100, "daily_max_retracts": 100,
           "limits": {"post.burst": {"count": 2, "minutes": 10}}}
    sns_post(world, SNS, minutes_before(3))
    sns_post(world, SNS, minutes_before(2))
    e = refused(lambda: guard.check(SNS, cfg, "publish", now=NOW))
    assert str(e) == "account_stopped" and e.assessment["rule"] == "post.burst"
    assert (e.assessment["count"], e.assessment["origin"]) == (2, "account")


def test_返信の窓はreply_burstで別に締められる(world):
    """`limits` の `reply.burst` を広げると、今の鍵 `burst` の窓を超えても返信は並ばない。

    柵: 返信の順番待ちの時刻（`burst_slot`）が `reply.burst` でなく投稿の窓を読むと赤。"""
    cfg = {"account": SNS, "media": "threads", "daily_max_posts": 100, "daily_max_retracts": 100,
           "burst": {"count": 1, "minutes": 10}, "limits": {"reply.burst": {"count": 5, "minutes": 10}}}
    for i in range(3):
        sns_post(world, SNS, minutes_before(5 - i))
    guard.check(SNS, cfg, "publish", now=NOW, reply=True)          # 3 件のあとの返信は通る（5 まで）
    cfg["limits"] = {"reply.burst": {"count": 3, "minutes": 10}}
    e = refused(lambda: guard.check(SNS, cfg, "publish", now=NOW, reply=True))
    assert str(e) == "burst_limit" and e.next_at
    # 順番待ちの時刻も reply.burst の窓で（今の鍵 burst の 1 件でなく 3 件・1 件なら 3 分前 + 11 分）
    slot = guard.burst_slot(SNS, cfg, now=NOW)
    assert slot == minutes_before(5) + datetime.timedelta(minutes=11)


def test_主体のブックマークの窓をlimitsで締める(world):
    hatena_loose(world)
    world.edit_principal("hatena-main", limits={"bookmark.burst": {"count": 2, "minutes": 10}})
    for i in range(2):
        hatena_row(world, ACCOUNT, minutes_before(5 - i), kind="bookmark_add", url=bm_url(i))
    e = refused(lambda: guard.check(ACCOUNT, accounts.load_account(ACCOUNT), "bookmark", now=NOW,
                                    bookmark_url=bm_url(9)))
    assert str(e) == "burst_limit" and e.reason == "bookmark_burst" and e.assessment["origin"] == "principal"
    assert principals.stopped("hatena-main") is None


def test_ブックマークの止め線もlimitsで動かせる(world):
    hatena_loose(world)   # bookmark_burst 100（待たせない）
    world.edit_principal("hatena-main", limits={"bookmark.stop": {"count": 4, "minutes": 10}})
    for i in range(4):
        hatena_row(world, ACCOUNT, minutes_before(5 - i), kind="bookmark_add", url=bm_url(i))
    e = refused(lambda: guard.check(ACCOUNT, accounts.load_account(ACCOUNT), "bookmark", now=NOW,
                                    bookmark_url=bm_url(9)))
    assert str(e) == "principal_stopped" and e.assessment["rule"] == "bookmark.stop"


# ---------------------------------------------------------------- 最低線（masaru 10/18）

FAKE = "fake-floor"


def test_最低線は持ち主が理由つきで上げれば上がる(world):
    """柵: 理由を見ずに上げさせると赤。上げたら 21 件目は通り、41 件目で止める。"""
    cfg = {"account": FAKE, "media": "fake",
           "limits": {"write.floor": {"count": 40, "minutes": 10, "reason": "キャンペーンの日"}}}
    for i in range(39):
        sns_post(world, FAKE, minutes_before(9) + datetime.timedelta(seconds=i))
    guard.check(FAKE, cfg, "publish", now=NOW)                       # 40 件目（既定なら 21 件目で止まる）
    sns_post(world, FAKE, minutes_before(1))
    e = refused(lambda: guard.check(FAKE, cfg, "publish", now=NOW))
    assert str(e) == "account_stopped" and e.assessment["rule"] == "write.floor"
    assert (e.assessment["count"], e.assessment["origin"]) == (40, "account")
    row = resolved("write.floor", cfg=cfg)
    assert row["floor_raised"] is True and row["floor_reason"] == "キャンペーンの日"
    # 理由の無い値は効かない（台帳の読み込みが断る形・ここでは既定に倒れて 20）
    assert resolved("write.floor", cfg={"limits": {"write.floor": {"count": 40, "minutes": 10}}})["count"] == 20


def test_最低線を下げるのに理由は要らない():
    assert limits_config.problem("write.floor", {"count": 10, "minutes": 10}) is None
    assert limits_config.problem("write.floor", {"count": 20, "minutes": 30}) is None
    assert limits_config.problem("write.floor", {"count": 20, "minutes": 5}) == "floor_reason_required"
    assert limits_config.problem("write.floor", {"count": 100, "minutes": 10, "reason": "r"}) is None
    assert limits_config.problem("write.floor", {"count": 101, "minutes": 10, "reason": "r"}) == "floor_above_max"


def test_最低線を上げるsetは理由が要り_変更ログとstatusに出る(world):
    name = sns_account(world)
    rc, out, err = call(["account", "set", name, "write.floor", "40", "10", "--by", "masaru"])
    assert rc == 2 and "floor_reason_required" in err
    assert "limits" not in world.account(name)
    rc, out, err = call(["account", "set", name, "write.floor", "120", "10", "--by", "masaru", "--reason", "r"])
    assert rc == 2 and "floor_above_max" in err
    rc, out, err = call(["account", "set", name, "write.floor", "40", "10", "--by", "masaru",
                         "--reason", "告知の日だけ", "--json"])
    assert rc == 0, err
    done = json.loads(out)
    assert (done["before"], done["after"], done["floor_raised"]) == (
        {"count": 20, "minutes": 10}, {"count": 40, "minutes": 10}, True)
    assert world.account(name)["limits"]["write.floor"] == {"count": 40, "minutes": 10, "reason": "告知の日だけ"}
    rows, _ = admin_log.read(event="settings_set")
    assert rows[-1]["diff"]["write.floor"] == [{"count": 20, "minutes": 10}, {"count": 40, "minutes": 10}]
    assert rows[-1]["diff"]["write.floor.reason"] == [None, "告知の日だけ"]
    rc, out, err = call(["account", "status", name])
    assert rc == 0, err
    assert "最低線（write.floor）を持ち主が上げています" in out and "告知の日だけ" in out
    rc, out, err = call(["account", "status", name, "--json"])
    floor = next(r for r in json.loads(out)["limits_table"] if r["id"] == "write.floor")
    assert floor["floor_raised"] is True and floor["count"] == 40
    # 外せば既定（表示も消える）
    assert call(["account", "set", name, "write.floor", "-", "--by", "masaru"])[0] == 0
    assert "limits" not in world.account(name)
    assert "持ち主が上げています" not in call(["account", "status", name])[1]


# ---------------------------------------------------------------- T-P-6 set

def test_T_P_6_principal_setの新しい名前はlimitsに書く(world):
    rc, out, err = call(["principal", "set", "hatena-main", "bookmark.burst", "20", "10", "--by", "t", "--json"])
    assert rc == 0, err
    done = json.loads(out)
    assert (done["key"], done["before"], done["after"], done["via"]) == (
        "bookmark.burst", {"count": 10, "minutes": 10}, {"count": 20, "minutes": 10}, "limits")
    raw = world.principal("hatena-main")
    assert raw["limits"] == {"bookmark.burst": {"count": 20, "minutes": 10}}
    assert raw["bookmark_burst"] == {"count": 10, "minutes": 10}      # 今の鍵は触らない（雛形の値のまま）
    assert principals.limits(principals.load_principal("hatena-main"))["bookmark_burst"] == {"count": 20, "minutes": 10}
    assert call(["principal", "set", "hatena-main", "bookmark.daily", "50", "--by", "t"])[0] == 0
    assert world.principal("hatena-main")["limits"]["bookmark.daily"] == 50
    # `-` で外す
    assert call(["principal", "set", "hatena-main", "bookmark.burst", "-", "--by", "t"])[0] == 0
    assert world.principal("hatena-main")["limits"] == {"bookmark.daily": 50}
    # 今の名前で書くと、指す行の limits を外す（今の鍵の変更が効く）
    assert call(["principal", "set", "hatena-main", "daily_max_bookmarks", "7", "--by", "t"])[0] == 0
    raw = world.principal("hatena-main")
    assert raw["daily_max_bookmarks"] == 7 and "limits" not in raw
    assert principals.limits(principals.load_principal("hatena-main"))["daily_max_bookmarks"] == 7


def test_T_P_6_受け取れない名前と値は断る(world):
    rc, _out, err = call(["principal", "set", "hatena-main", "bookmark.weekly", "3", "--by", "t"])
    assert rc == 2 and "unknown_rule" in err
    rc, _out, err = call(["principal", "set", "hatena-main", "bookmark.burst", "20", "--by", "t"])
    assert rc == 2 and "invalid_value" in err
    rc, _out, err = call(["principal", "set", "hatena-main", "post.daily", "3", "--by", "t"])
    assert rc == 2 and "unknown_rule" in err
    assert "limits" not in world.principal("hatena-main")


def test_T_P_6_account_setの新しい名前と媒体(world):
    name = sns_account(world)
    rc, out, err = call(["account", "set", name, "post.daily", "12", "--by", "masaru", "--json"])
    assert rc == 0, err
    assert json.loads(out)["after"] == 12 and world.account(name)["limits"] == {"post.daily": 12}
    rc, _out, err = call(["account", "set", name, "entry.daily", "3", "--by", "masaru"])
    assert rc == 2 and "rule_not_for_media" in err
    # 今の名前（burst）で書くと post.burst・reply.burst の limits を外す
    assert call(["account", "set", name, "post.burst", "2", "10", "--by", "masaru"])[0] == 0
    assert call(["account", "set", name, "burst", "4", "10", "--by", "masaru"])[0] == 0
    raw = world.account(name)
    assert raw["burst"] == {"count": 4, "minutes": 10} and raw["limits"] == {"post.daily": 12}


def test_T_P_6_締める向きだけの口(world):
    """柵: `tightens` の向きを逆にすると赤（LLM の口から緩められる）。"""
    world.edit_principal("hatena-main", limits={"bookmark.burst": {"count": 10, "minutes": 10}})
    with pytest.raises(limits_config.LimitsError) as info:
        limits_config.change("principal", "hatena-main", "bookmark.burst", ["20", "10"], by="llm",
                             tighten_only=True)
    assert info.value.code == "settings_loosen_requires_owner"
    with pytest.raises(limits_config.LimitsError):
        limits_config.change("principal", "hatena-main", "bookmark.burst", ["10", "5"], by="llm", tighten_only=True)
    done = limits_config.change("principal", "hatena-main", "bookmark.burst", ["5", "20"], by="llm",
                                tighten_only=True)
    assert done["after"] == {"count": 5, "minutes": 20}
    with pytest.raises(limits_config.LimitsError):
        limits_config.change("principal", "hatena-main", "bookmark.daily", ["31"], by="llm", tighten_only=True)
    assert limits_config.change("principal", "hatena-main", "bookmark.daily", ["29"], by="llm",
                                tighten_only=True)["after"] == 29
    assert limits_config.tightens("bookmark.daily", 30, 30) and not limits_config.tightens("bookmark.daily", 30, 31)


# ---------------------------------------------------------------- show

def test_principal_showに行ごとの数と出所と確かさ(world):
    world.edit_principal("hatena-main", daily_max_bookmarks=50, limits={"bookmark.burst": {"count": 20, "minutes": 10}})
    hatena_row(world, ACCOUNT, jst.now_jst() - datetime.timedelta(minutes=1),
               kind="bookmark_add", url=bm_url(1))
    rc, out, err = call(["principal", "show", "hatena-main", "--json"])
    assert rc == 0, err
    table = {r["id"]: r for r in json.loads(out)["limits_table"]}
    assert (table["bookmark.daily"]["count"], table["bookmark.daily"]["origin"], table["bookmark.daily"]["via"]) == (
        50, "principal", "alias:daily_max_bookmarks")
    assert table["bookmark.daily"]["used"] == 1 and table["bookmark.burst"]["used"] == 1
    assert (table["bookmark.burst"]["count"], table["bookmark.burst"]["via"]) == (20, "limits")
    assert table["bookmark.stop"]["origin"] == "default"
    assert table["bookmark.stop"]["count_evidence"] == "inferred"
    assert table["bookmark.once"]["settable"] is None and table["upload.daily"]["used"] == 0
    assert "daily_max_bookmarks" in json.loads(out)["legacy_keys"]
    rc, out, err = call(["principal", "show", "hatena-main"])
    assert "上限と連投の柵" in out
    assert "主体の明示（今の鍵 daily_max_bookmarks・別名）" in out and "主体の明示（limits）" in out
    assert "数は推測" in out and "古い書き方（別名" in out


def test_account_statusに表(world):
    name = sns_account(world, daily_max_posts=12)
    rc, out, err = call(["account", "status", name, "--json"])
    assert rc == 0, err
    row = json.loads(out)
    table = {r["id"]: r for r in row["limits_table"]}
    assert set(table) >= {"post.daily", "retract.daily", "reply.burst", "post.burst", "reaction.daily", "write.floor"}
    assert "entry.daily" not in table and "bookmark.daily" not in table
    assert (table["post.daily"]["count"], table["post.daily"]["via"]) == (12, "alias:daily_max_posts")
    assert table["retract.daily"]["origin"] == "default" and table["retract.daily"]["trait"] == "threads.api_deletes_24h"
    assert row["legacy_keys"] == ["daily_max_posts"]
    rc, out, err = call(["account", "status", name])
    assert "媒体の台帳の既定" in out and "THTH の既定" in out


def test_はてなの口座のstatusは主体の行も(world):
    rc, out, err = call(["account", "status", ACCOUNT, "--json"])
    assert rc == 0, err
    ids = [r["id"] for r in json.loads(out)["limits_table"]]
    assert "entry.daily" in ids and "bookmark.burst" in ids and "write.floor" in ids and "post.daily" not in ids


def test_mcpの状態の答えは今の形のまま(world):
    """遠くの道・MCP の `account_status` は表を足さない（答えの大きさと形を変えない）。"""
    from thth import account_settings
    name = sns_account(world)
    assert "limits_table" not in account_settings.status(name)


# ---------------------------------------------------------------- 既定は表から

def test_既定の数は柵の表から引く(ledger):
    """柵: `GUARD_DEFAULTS` を表から引かず定数に戻すと赤（表と既定が別々に動く）。"""
    assert accounts.GUARD_DEFAULTS["daily_max_posts"] == 8
    def bump(doc):
        for trait in doc["traits"]:
            for rule_id, row in (trait.get("defaults") or {}).items():
                if rule_id == "post.daily":
                    row["count"] = 9
    for media in ("threads", "x", "bluesky", "mastodon"):
        edit_ledger(ledger, media, bump)
    assert accounts.GUARD_DEFAULTS["daily_max_posts"] == 9
    assert accounts.guard_limits({"media": "threads"})["daily_max_posts"] == 9
    def bump_bm(doc):
        for trait in doc["traits"]:
            for rule_id, row in (trait.get("defaults") or {}).items():
                if rule_id == "bookmark.daily":
                    row["count"] = 31
    edit_ledger(ledger, "hatenabookmark", bump_bm)
    assert principals.LIMIT_DEFAULTS["daily_max_bookmarks"] == 31
    assert principals.limits({})["daily_max_bookmarks"] == 31


def test_媒体の台帳が壊れても既定は4_1_0の数(ledger):
    edit_ledger(ledger, "threads", lambda doc: doc.update(media=42))
    with pytest.raises(ValueError):
        limits_policy.policy()
    assert accounts.GUARD_DEFAULTS["daily_max_posts"] == 8 and principals.LIMIT_DEFAULTS["daily_max_bookmarks"] == 30
    assert hatena_fotolife.LIMIT_DEFAULTS["daily_max_uploads"] == 30


def test_主体の雛形の数は表の既定と同じ():
    """雛形は表の既定のまま（主体）か、締めた値（はてなの口座の 3・2）。"""
    root = pathlib.Path(__file__).resolve().parent.parent / "accounts.example"
    p = json.loads((root / "principal-hatena.json").read_text(encoding="utf-8"))
    for key in principals.LIMIT_DEFAULTS:
        assert p[key] == principals.LIMIT_DEFAULTS[key], key
    assert p["fotolife"]["daily_max_uploads"] == hatena_fotolife.LIMIT_DEFAULTS["daily_max_uploads"]
    acct = json.loads((root / "hatenablog.json").read_text(encoding="utf-8"))
    assert acct["daily_max_posts"] <= limits_policy.default_value("entry.daily", media="hatenablog")
    assert acct["daily_max_retracts"] <= limits_policy.default_value("entry_delete.daily", media="hatenablog")


# ---------------------------------------------------------------- T-P-7 VM の台帳の形

def _install_fixture(world):
    for path in sorted((FIXTURES / "principals").glob("*.json")):
        shutil.copy(path, world.principals_dir / path.name)
    for path in sorted((FIXTURES / "ledgers").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update({"repo_dir": str(world.tmp / "repo"), "env": str(world.tmp / "x.env"),
                     "token": str(world.tmp / "x.token")})
        (world.accounts_dir / path.name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    for path in world.principals_dir.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update({"token": str(world.tmp / "x.token"), "consumer": str(world.tmp / "x.consumer")})
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


EXPECTED_4_1_0 = {
    # 主体（VM の hatena-kopicha の形: ブックマークの日 50・同じホスト 5・記事の連投 3/30 分）
    ("principal", "vm-kopicha"): {"entry.principal_daily": 6, "entry_delete.principal_daily": 3,
                                  "entry.principal_burst": (3, 30), "bookmark.daily": 50,
                                  "bookmark.self_daily": 1, "bookmark.same_host_daily": 5,
                                  "bookmark.burst": (10, 10), "bookmark.stop": (30, 10), "upload.daily": 30},
    ("account", "vm-kopicha-hatenablog"): {"entry.daily": 3, "entry_delete.daily": 2, "entry.burst": (3, 10),
                                           "write.floor": (20, 10)},
    ("account", "vm-music-threads"): {"post.daily": 8, "retract.daily": 5, "post.burst": (3, 10),
                                      "reply.burst": (3, 10), "reaction.daily": 50, "write.floor": (20, 10)},
    ("account", "vm-kopicha-x"): {"post.daily": 4, "retract.daily": 5, "post.burst": (2, 30),
                                  "reply.burst": (2, 30), "reaction.daily": 20, "write.floor": (20, 10)},
}


def test_T_P_7_VMの台帳の形は書き換えずに同じ数で効く(world):
    """柵: 別名を外すと赤（VM の 16 口座と主体の数が黙って既定に戻る）。"""
    _install_fixture(world)
    before = {p: p.read_bytes() for p in list(world.accounts_dir.glob("*.json")) + list(world.principals_dir.glob("*.json"))}
    for (holder, name), want in EXPECTED_4_1_0.items():
        if holder == "principal":
            p = principals.load_principal(name)
            got = {rid: resolved(rid, principal_cfg=p) for rid in want}
        else:
            cfg = accounts.load_account(name)
            got = {rid: resolved(rid, cfg=cfg) for rid in want}
        for rid, value in want.items():
            count = value[0] if isinstance(value, tuple) else value
            assert got[rid]["count"] == count, (name, rid)
            if isinstance(value, tuple):
                assert got[rid]["window"] == {"minutes": value[1]}, (name, rid)
    # show と status も通り、台帳は 1 バイトも変わらない
    assert call(["principal", "show", "vm-kopicha"])[0] == 0
    for name in ("vm-kopicha-hatenablog", "vm-music-threads", "vm-kopicha-x"):
        rc, _out, err = call(["account", "status", name])
        assert rc == 0, err
    after = {p: p.read_bytes() for p in before}
    assert after == before
