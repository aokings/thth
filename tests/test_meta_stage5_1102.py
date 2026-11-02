"""Meta 計画 段 5 の直しと足し（2026-11-02・本番の試しの 1 本で分かったこと）。

偽の Graph API（`tests/test_meta_stage5.py` の状態を持つ偽の Meta）だけを使う。**本物の Meta は叩かない。**

縛ること:
- stop の読み直し: pause の直後の `IN_PROCESS` は待って読み直す（量の制限では待たない）。待ち切っても
  残れば、上が止まっていると読めたときだけ「止まった」。ACTIVE のままなら待たずに「確かめられない」。
- stop の spend_cap: `amount_spent` の 110% へ下げる。Meta の断りは符丁と伏せた文で残す（鍵を出さない）。
- extend の 2 段目の返り値に hub の番号。insights の今日を含まない期間の一言・maximum・--since/--until。
- boost の reach（OUTCOME_AWARENESS＋REACH・destination_type を送らない）。
- watch: 達したら stop と同じに止める・指定なしは始まったら知らせる・--until で必ず終わる・読めなかった回を数える。
- show・doctor の配信の診断（予算が丸ごと残ったまま表示 0 が N 時間）。
- resume（masaru 11/02 の例外）: 2 段＋hub の番号必須・digest 違いで断る・外しても広告は PAUSED のまま・
  owner 以外の印と確かめられていない stop のあとは断る・MCP に無い・上げる引数が無い。
"""
from __future__ import annotations

import datetime
import json
import re
import secrets
import sys

import pytest

from thth import cli, jst
from thth.ads import digest, graphwrite, ledger, ops, watch
from thth.ads import cli as ads_cli
from tests.test_meta_stage5 import (ACT, NAME, REPO, STORY, V, _ads_parser, _walk, end_in, env, posts,  # noqa: F401
                                    setup_ledger, status_posts, two_stage)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(ops, "_sleep", lambda s: slept.append(s))
    return slept


# ================================================================== 1. stop の読み直し

def _in_process_for(meta, reads, kinds=("ad",)):
    """`reads` 回の木の読みのあいだ、`kinds` の物を IN_PROCESS で返す。"""
    original = type(meta).effective
    state = {"tree_reads": 0}

    def effective(obj):
        if obj["kind"] in kinds and obj.get("status") == "PAUSED" and state["tree_reads"] <= reads:
            return "IN_PROCESS"
        return original(meta, obj)
    meta.effective = effective
    return state


def test_stopはpause直後のIN_PROCESSを待って読み直し_止まったと言う(env, no_sleep, monkeypatch):
    setup_ledger(env)
    meta = env["meta"]
    meta.campaign(status="ACTIVE")
    state = _in_process_for(meta, reads=3)   # ① の読み（1）・② の読み（2・3）が IN_PROCESS
    from thth.ads import graphread
    original = graphread.Reader.tree

    def counted(self, *, urgent=False):
        state["tree_reads"] += 1
        return original(self, urgent=urgent)
    monkeypatch.setattr(graphread.Reader, "tree", counted)
    row = ops.stop(NAME, by="masaru")
    assert row["confirmed"] is True and row["result"] == "ok"
    assert no_sleep == [5, 10]          # 2 回待って 3 回目の読み直しで全部止まった
    assert row["steps"]["verify"]["attempts"][-1] == {"unpaused": 0}


def test_stopは待ち切ってもIN_PROCESSなら上が止まっていると読めたときだけ止まったと言う(env, no_sleep):
    setup_ledger(env)
    meta = env["meta"]
    meta.campaign(status="ACTIVE")
    _in_process_for(meta, reads=10**6)   # ずっと IN_PROCESS（上のキャンペーン・広告セットは PAUSED）
    row = ops.stop(NAME, by="masaru")
    assert no_sleep == list(ops.STOP_VERIFY_WAITS)
    verify = row["steps"]["verify"]
    assert row["confirmed"] is True and verify["by_parent"] and verify["unpaused"]
    # 上も IN_PROCESS（止まったと読めない）なら「止まった」と言わない。
    _in_process_for(meta, reads=10**6, kinds=("campaign", "adset", "ad"))
    no_sleep.clear()
    row = ops.stop(NAME, by="masaru")
    assert row["confirmed"] is False and "hub_line" in row


def test_stopは止めの印をpauseの直後_待ちの前に書き_待ちの途中で落ちても印が残る(env, no_sleep, monkeypatch):
    # 監査 5.6.2 の 4。記録の steps も pause → thth_stop → verify → spend_cap の順。
    setup_ledger(env)
    meta = env["meta"]
    meta.campaign(status="ACTIVE")
    row = ops.stop(NAME, by="masaru")
    assert list(row["steps"]) == ["pause", "thth_stop", "verify", "spend_cap"]
    ledger_dir = ledger.state_dir_for(NAME)
    import os
    import shutil
    shutil.rmtree(os.path.join(ledger_dir, "guard"))   # 印を消して、もう一度止める（待ちの途中で落ちる）
    assert not ledger.stopped(NAME)
    meta.campaign(status="ACTIVE")

    def crash(reader):
        raise KeyboardInterrupt
    monkeypatch.setattr(ops, "verify_stopped", crash)
    with pytest.raises(KeyboardInterrupt):
        ops.stop(NAME, by="masaru")
    assert ledger.stopped(NAME)["reason"] == "owner"
    last = [r for r in ops.read_ops(NAME) if r["op"] == "stop"][-1]
    assert last["steps"]["thth_stop"]["ok"] is True and "verify" not in last["steps"]


def test_stopはACTIVEのままなら待たずに確かめられないと言う(env, no_sleep):
    setup_ledger(env)
    meta = env["meta"]
    cid = meta.campaign(status="ACTIVE")
    meta.stuck.add(cid)
    row = ops.stop(NAME, by="masaru")
    assert row["confirmed"] is False and no_sleep == []


# ================================================================== 2. spend_cap

def test_stopのspend_capの断りは符丁と伏せた文で残し_鍵を出さない(env, capsys):
    setup_ledger(env)
    meta = env["meta"]
    meta.campaign(status="ACTIVE")
    other = "EAAB" + secrets.token_urlsafe(24)   # THTH が知らない鍵（Meta が別の鍵を文面に載せた形）
    meta.fail[("POST", V + "/act_" + ACT)] = (400, {"error": {
        "code": 100, "error_subcode": 1885650, "message": "Invalid parameter",
        "error_user_msg": f"spend cap too low https://graph.facebook.com/x?access_token={other}&y=1 "
                          f"raw {env['token']}"}})
    rc = cli.main(["ads", "stop", NAME, "--by", "masaru", "--json"])
    out = capsys.readouterr()
    assert rc == 0
    step = [r for r in ops.read_ops(NAME) if r["op"] == "stop"][-1]["steps"]["spend_cap"]
    assert step["ok"] is False and step["reason"] == "ads_spend_cap_refused" and step["graph_code"] == 100
    assert step["subcode"] == 1885650 and step["tried"] == 1320 and "spend cap too low" in step["meta_says"]
    text = out.out + out.err + json.dumps(step, ensure_ascii=False)
    assert env["token"] not in text and env["app_secret"] not in text and other not in text


def test_spend_capの下げる先は使った額の110パーセントで今の値以上なら書かない(env):
    assert ops.spend_cap_target(608) == 669 and ops.spend_cap_target(0) is None
    setup_ledger(env)
    meta = env["meta"]
    meta.campaign(status="ACTIVE")
    meta.spend_cap = "1300"               # 1200 の 110% = 1320 は今の 1300 より上（上げることになる）
    row = ops.stop(NAME, by="masaru")
    assert row["steps"]["spend_cap"]["ok"] is None and meta.spend_cap == "1300"


def test_spend_capの余白は1操作の上限lifetime_budget_maxで抑える(env):
    # 監査 5.6.2 の 3: 通算 30 万円の口座で 10% の余白（3 万円）は 1 操作の上限 5,000 円を越える。
    assert ops.spend_cap_target(300000, 5000) == 305000 and ops.spend_cap_target(608, 5000) == 669
    assert ops.spend_cap_target(608, 0) == 608
    setup_ledger(env, lifetime_max=5000)
    meta = env["meta"]
    meta.campaign(status="ACTIVE")
    meta.amount_spent, meta.spend_cap = "300000", "400000"
    row = ops.stop(NAME, by="masaru")
    assert row["steps"]["spend_cap"]["after"] == 305000 and meta.spend_cap == "305000"


def test_safe_user_msgは綴りの無い知らない鍵が残れば文ごと落とす():
    # 監査 5.6.2 の 5。access_token= の綴りが無い、THTH が知らない鍵。
    other = "EAAB" + "".join(secrets.choice("abcdefghijk0123456789") for _ in range(30))
    said = graphwrite.safe_user_msg({"error_user_msg": f"token {other} is invalid"})
    assert other not in said and said == graphwrite.SECRET_DROPPED
    plain = graphwrite.safe_user_msg({"error_user_title": "上限が低すぎます", "error_user_msg": "608 より上に"})
    assert plain == "上限が低すぎます / 608 より上に"
    assert graphwrite.safe_user_msg({}) is None


# ================================================================== 3. extend の hub の番号

def test_extendの2段目はhubの番号を返し_表示にNoneが出ない(env, capsys):
    setup_ledger(env)
    meta = env["meta"]
    cid = meta.campaign(days=2)
    adset = next(o["id"] for o in meta.objects.values() if o["kind"] == "adset" and o["campaign_id"] == cid)
    first = ops.extend(NAME, adset, by="m", lifetime_minor=800)
    capsys.readouterr()
    rc = cli.main(["ads", "extend", NAME, adset, "--lifetime", "800", "--confirm", first["digest"],
                   "--approval", "A261102-1", "--by", "m"])
    out = capsys.readouterr().out
    assert rc == 0 and "hub の番号 A261102-1" in out and "hub の番号 None" not in out


# ================================================================== 4・7. insights

def test_insightsは今日を含まない期間で行が無ければ一言添える_maximumとsince_until(env, capsys):
    setup_ledger(env)
    env["meta"].spend.clear()
    # 行の無い答え（今日だけ配信した広告を last_7d で読んだとき）。
    env["meta"].fail[("GET", V + f"/act_{ACT}/insights")] = (200, {"data": []})
    assert cli.main(["ads", "insights", NAME, "--preset", "last_7d"]) == 0
    out = capsys.readouterr().out
    assert "行がありません" in out and "今日を含みません" in out
    env["meta"].fail[("GET", V + f"/act_{ACT}/insights")] = (200, {"data": []})
    assert cli.main(["ads", "insights", NAME, "--preset", "today", "--json"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["excludes_today"] is False and "note" not in value
    assert cli.main(["ads", "insights", NAME, "--preset", "maximum", "--json"]) == 0
    capsys.readouterr()
    sent = [c for c in env["calls"] if c["path"].endswith("/insights")][-1]["query"]
    assert sent["date_preset"] == ["maximum"]
    assert cli.main(["ads", "insights", NAME, "--since", "2026-11-01", "--until", "2026-11-02", "--json"]) == 0
    value = json.loads(capsys.readouterr().out)
    sent = [c for c in env["calls"] if c["path"].endswith("/insights")][-1]["query"]
    assert json.loads(sent["time_range"][0]) == {"since": "2026-11-01", "until": "2026-11-02"}
    assert "date_preset" not in sent and value["since"] == "2026-11-01"
    assert cli.main(["ads", "insights", NAME, "--until", "2026-11-02"]) == 2
    assert cli.main(["ads", "insights", NAME, "--since", "2026-11-03", "--until", "2026-11-02"]) == 2


# ================================================================== 8. reach

def test_boostのreachはOUTCOME_AWARENESSとREACHで_destination_typeを送らない(env, capsys):
    setup_ledger(env)
    rc = cli.main(["ads", "boost", NAME, "--object-story-id", STORY, "--lifetime", "500", "--end", end_in(1),
                   "--objective", "reach", "--by", "masaru"])
    out = capsys.readouterr().out
    assert rc == 0 and "本番未確認" in out
    assert posts(env, "/campaigns")[0]["form"]["objective"] == ["OUTCOME_AWARENESS"]
    adset = posts(env, "/adsets")[0]["form"]
    assert adset["optimization_goal"] == ["REACH"] and "destination_type" not in adset
    assert adset["status"] == ["PAUSED"]
    assert graphwrite.VERIFIED_OBJECTIVES == {"engagement"}


# ================================================================== 5. watch

class Clock:
    def __init__(self):
        self.now = jst.now_jst()

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += datetime.timedelta(seconds=seconds)


def _install(env, cid, rows, monkeypatch):
    """キャンペーンの /insights の答えを順に返す（読み切ったら最後の答えを返し続ける）。"""
    from thth.ads import graphread
    original = graphread.Reader._get
    calls = {"n": 0}

    def fake_get(self, p, params=None, *, urgent=False):
        if p == f"/{cid}/insights":
            value = rows[min(calls["n"], len(rows) - 1)]
            calls["n"] += 1
            if isinstance(value, Exception):
                raise value
            return {"data": value}
        return original(self, p, params, urgent=urgent)
    monkeypatch.setattr(graphread.Reader, "_get", fake_get)
    return calls


def test_watchは指定のspendに達したらstopと同じに止める(env, monkeypatch):
    setup_ledger(env)
    meta = env["meta"]
    cid = meta.campaign(status="ACTIVE")
    from thth.ads import graphread
    calls = _install(env, cid, [[{"spend": "100", "impressions": "50"}], [{"spend": "300", "impressions": "90"}],
                                graphread.AdsReadError("ads_http_500")], monkeypatch)
    clock = Clock()
    out = ops.watch_campaign(NAME, cid, by="masaru", stop_at_spend=250, interval=60, now_fn=clock, sleep=clock.sleep)
    assert out["result"] == "stopped" and out["reads"] == 2 and calls["n"] == 2
    assert out["stop"]["confirmed"] is True and ledger.stopped(NAME)
    assert status_posts(env)[cid] == "PAUSED"
    assert [r for r in ops.read_ops(NAME) if r["op"] == "stop"]


def test_watchは指定なしなら配信が始まったら知らせて終わり_何も書かない(env, monkeypatch):
    setup_ledger(env)
    cid = env["meta"].campaign(status="ACTIVE")
    _install(env, cid, [[], [{"spend": "0", "impressions": "0"}], [{"spend": "3", "impressions": "12"}]], monkeypatch)
    clock = Clock()
    out = ops.watch_campaign(NAME, cid, by="m", interval=60, now_fn=clock, sleep=clock.sleep)
    assert out["result"] == "delivering" and out["reads"] == 3 and not posts(env)


def test_watchは閾値つきで読めない回が3回続けば止める_閾値なしなら止めずにrc3(env, monkeypatch, capsys):
    # 監査 5.6.2 の 1: 読めない間に止めずに回り続けない（止めるのは戻せる・止め損なうのはお金）。
    setup_ledger(env)
    cid = env["meta"].campaign(status="ACTIVE")
    from thth.ads import graphread
    _install(env, cid, [graphread.AdsReadError("ads_http_500")], monkeypatch)
    clock = Clock()
    out = ops.watch_campaign(NAME, cid, by="m", stop_at_impressions=1000, interval=600,
                             until=jst.iso(clock.now + datetime.timedelta(hours=1)), now_fn=clock, sleep=clock.sleep)
    assert out["result"] == "stopped_on_unreadable" and out["failed_reads"] == out["reads"] == 3
    assert out["stop"]["confirmed"] is True and ledger.stopped(NAME) and status_posts(env)[cid] == "PAUSED"
    row = [r for r in ops.read_ops(NAME) if r["op"] == "watch"][-1]
    assert row["result"] == "stopped_on_unreadable" and row["stop_op_id"] == out["stop"]["op_id"]
    # 閾値なし: 止めない・rc 3。
    writes = len(posts(env))
    capsys.readouterr()
    rc = cli.main(["ads", "watch", NAME, cid, "--interval", "60", "--by", "m", "--json"])
    value = json.loads(capsys.readouterr().out)
    assert rc == 3 and value["result"] == "unreadable" and value["reads"] == 3 and len(posts(env)) == writes


def test_watchは閾値に要る欄が数でない回も読めない回に数える(env, monkeypatch):
    setup_ledger(env)
    cid = env["meta"].campaign(status="ACTIVE")
    _install(env, cid, [[{"spend": "1.5", "impressions": "3"}]], monkeypatch)   # JPY で端数＝spend_minor None
    clock = Clock()
    out = ops.watch_campaign(NAME, cid, by="m", stop_at_spend=10**6, interval=60, now_fn=clock, sleep=clock.sleep)
    assert out["result"] == "stopped_on_unreadable" and out["reads"] == 3
    assert out["errors"][-1] == "ads_field_unreadable:spend_minor"


def test_watchは読めない回が回復すれば数え直し_閾値で止める(env, monkeypatch):
    setup_ledger(env)
    cid = env["meta"].campaign(status="ACTIVE")
    from thth.ads import graphread
    fail = graphread.AdsReadError("ads_http_500")
    _install(env, cid, [fail, fail, [{"spend": "10", "impressions": "5"}], fail, fail,
                        [{"spend": "300", "impressions": "9"}]], monkeypatch)
    clock = Clock()
    out = ops.watch_campaign(NAME, cid, by="m", stop_at_spend=250, interval=60, now_fn=clock, sleep=clock.sleep)
    assert out["result"] == "stopped" and out["reads"] == 6 and out["failed_reads"] == 4


def test_watchはuntilで必ず終わる(env, monkeypatch, capsys):
    setup_ledger(env)
    cid = env["meta"].campaign(status="ACTIVE")
    clock = Clock()
    for kwargs, code in ((dict(until=jst.iso(clock.now + datetime.timedelta(hours=49))), "ads_watch_too_long"),
                         (dict(interval=10), "ads_watch_invalid")):
        with pytest.raises(ops.OpsRefused) as exc:
            ops.watch_campaign(NAME, cid, by="m", now_fn=clock, sleep=clock.sleep, **kwargs)
        assert exc.value.code == code
    # 既定の上限（6 時間）でも終わる。
    clock2 = Clock()
    _install(env, cid, [[]], monkeypatch)
    out = ops.watch_campaign(NAME, cid, by="m", stop_at_spend=10**6, interval=3600, now_fn=clock2, sleep=clock2.sleep)
    assert out["result"] == "until_reached" and out["reads"] == 7
    assert [r for r in ops.read_ops(NAME) if r["op"] == "watch"][-1]["result"] == "until_reached"


def test_watchのCLIはJSONで結果を返す(env, monkeypatch, capsys):
    setup_ledger(env)
    cid = env["meta"].campaign(status="ACTIVE")
    _install(env, cid, [[{"spend": "3", "impressions": "12"}]], monkeypatch)
    capsys.readouterr()
    assert cli.main(["ads", "watch", NAME, cid, "--interval", "60", "--by", "m", "--json"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["result"] == "delivering" and value["failed_reads"] == 0 and value["last"]["impressions"] == 12


# ================================================================== 6. 配信の診断

def test_showとdoctorは予算が丸ごと残ったまま表示0がN時間を警告する(env, monkeypatch, capsys):
    setup_ledger(env)
    meta = env["meta"]
    cid = meta.campaign(status="ACTIVE", start_offset=-3 * 3600, days=1)
    adset = next(o for o in meta.objects.values() if o["kind"] == "adset" and o["campaign_id"] == cid)
    from thth.ads import graphread
    original = graphread.Reader._get

    def fake_get(self, p, params=None, *, urgent=False):
        if p == "/" + adset["id"] and "budget_remaining" in (params or {}).get("fields", ""):
            body = dict(original(self, p, {"fields": "id,name,campaign_id,status,effective_status,lifetime_budget,"
                                                     "start_time,end_time"}, urgent=urgent))
            return dict(body, budget_remaining=str(adset["lifetime_budget"]),
                        issues_info=[{"error_summary": "審査中", "error_code": 1}])
        if p == f"/{adset['id']}/delivery_estimate":
            return {"data": [{"estimate_ready": True, "estimate_dau": 1000}]}
        if p == f"/{adset['id']}/insights":
            return {"data": []}
        return original(self, p, params, urgent=urgent)
    monkeypatch.setattr(graphread.Reader, "_get", fake_get)
    capsys.readouterr()
    assert cli.main(["ads", "show", NAME, "--json"]) == 0
    value = json.loads(capsys.readouterr().out)
    row = value["delivery"]["adsets"][0]
    assert row["stalled"] is True and row["impressions"] == 0 and row["delivery_estimate"]["estimate_dau"] == 1000
    assert "丸ごと残ったまま表示 0" in value["delivery"]["warnings"][0]
    rows = ads_cli.doctor_rows(NAME)
    delivery = next(r for r in rows if r["key"] == "delivery")
    assert delivery["ok"] is None and "丸ごと残ったまま" in delivery["detail"]
    assert {c["method"] for c in env["calls"]} == {"GET"}


def test_配信の診断は1つ読めなくても残りを続ける(env):
    setup_ledger(env)
    meta = env["meta"]
    meta.campaign(status="ACTIVE")
    from thth.ads import graphread

    class Reader:
        def adset_delivery(self, adset_id):
            if adset_id == "1":
                raise graphread.AdsReadError("ads_http_500")
            return {"lifetime_budget": 500, "budget_remaining": 400, "start_time": None}

        def insights(self, *a, **k):
            return [{"impressions": 7}]
    tree = {"adsets": [{"id": "1", "effective_status": "ACTIVE"}, {"id": "2", "effective_status": "ACTIVE"}]}
    report = watch.delivery_report(Reader(), tree, now=jst.now_jst())
    assert report["adsets"][0]["error"] == "ads_http_500" and report["adsets"][1]["impressions"] == 7


# ================================================================== resume（masaru 11/02 の例外）

def _stopped_account(env):
    setup_ledger(env)
    meta = env["meta"]
    cid = meta.campaign()
    two_stage(cid)
    assert ops.stop(NAME, by="masaru")["confirmed"] is True
    return meta, cid


def test_resumeは2段でhubの番号が要り_外しても広告はPAUSEDのまま(env):
    meta, cid = _stopped_account(env)
    before = {o["id"]: o.get("status") for o in meta.objects.values()}
    writes = len(posts(env))
    first = ops.resume(NAME, by="masaru")
    assert first["stage"] == 1 and first["stopped"]["reason"] == "owner" and ledger.stopped(NAME)
    for approval in (None, "", "ok"):
        with pytest.raises(ops.OpsRefused) as exc:
            ops.resume(NAME, by="masaru", confirm=first["digest"], approval=approval)
        assert exc.value.code == "approval_gate_required"
    with pytest.raises(ops.OpsRefused) as exc:
        ops.resume(NAME, by="masaru", confirm="000000000000", approval="A261102-1")
    assert exc.value.code == "ads_tree_changed" and ledger.stopped(NAME)
    second = ops.resume(NAME, by="masaru", confirm=first["digest"], approval="A261102-1")
    assert second["stage"] == 2 and ledger.stopped(NAME) is None
    # Meta には何も書いていない・広告は PAUSED のまま・spend_cap も上限も旗もそのまま。
    assert len(posts(env)) == writes
    assert {o["id"]: o.get("status") for o in meta.objects.values()} == before
    cfg = ledger.load(NAME)
    assert cfg["ceilings"]["monthly"] == 10000 and cfg["production"] is True
    row = [r for r in ops.read_ops(NAME) if r["op"] == "resume"][-1]
    assert row["approval"] == "A261102-1" and row["result"] == "ok" and row["stopped"]["reason"] == "owner"
    # 監査 5.6.2 の 2: admin_log に adaccount_resumed が残る（EVENTS に無いと黙って落ちる）。
    from thth import admin_log
    logged, _broken = admin_log.read(event="adaccount_resumed")
    assert logged and logged[-1]["diff"]["approval"] == [None, "A261102-1"]
    assert set(watch._thth_activated(NAME)) == set()     # 始めた物には数えない（始めるのは activate）


def test_resumeのあとでもactivateの柵は生きている(env):
    # 監査 5.6.2 の気づき 5: 印を外しても、production・spend_cap・月の上限などの柵はそのまま効く。
    meta, cid = _stopped_account(env)
    first = ops.resume(NAME, by="masaru")
    ops.resume(NAME, by="masaru", confirm=first["digest"], approval="A261102-1")
    meta.spend_cap = "0"
    with pytest.raises(ops.OpsRefused) as exc:
        ops.activate(NAME, cid, by="masaru")
    assert exc.value.code == "ads_spend_cap_missing"
    meta.spend_cap = "30000"
    raw = json.loads(open(ledger.path_for(NAME)).read()); raw["production"] = False
    open(ledger.path_for(NAME), "w").write(json.dumps(raw))
    with pytest.raises(ops.OpsRefused) as exc:
        ops.activate(NAME, cid, by="masaru")
    assert exc.value.code == "ads_not_production"
    assert set(status_posts(env).values()) <= {"ACTIVE", "PAUSED"}
    assert all(o.get("status") != "ACTIVE" for o in meta.objects.values())


def test_resumeは1段目のあと木か印が変わればads_tree_changed(env):
    meta, cid = _stopped_account(env)
    first = ops.resume(NAME, by="masaru")
    next(o for o in meta.objects.values() if o["kind"] == "ad")["status"] = "ACTIVE"   # 外で再開された
    with pytest.raises(ops.OpsRefused) as exc:
        ops.resume(NAME, by="masaru", confirm=first["digest"], approval="A261102-1")
    assert exc.value.code == "ads_tree_changed" and ledger.stopped(NAME)


@pytest.mark.parametrize("raw,code", [
    ({"reason": "emergency", "stopped_at": "2026-11-02T10:00:00+09:00", "actor": "x", "via": "cli"},
     "ads_resume_refused"),
    ("not json", "ads_resume_refused"),
])
def test_resumeはowner以外の印と読めない印を外さない(env, raw, code):
    setup_ledger(env)
    ledger.stop(NAME, "owner", by="masaru")
    import os
    path = os.path.join(ledger.state_dir_for(NAME), "guard", ledger.STOP_FILE)
    with open(path, "w", encoding="utf-8") as f:
        f.write(raw if isinstance(raw, str) else json.dumps(raw))
    os.chmod(path, 0o600)
    with pytest.raises(ops.OpsRefused) as exc:
        ops.resume(NAME, by="masaru")
    assert exc.value.code == code
    assert ledger.stopped(NAME)


def test_resumeは最後のstopが確かめられていなければ断る_止まっていなければ断る(env):
    setup_ledger(env)
    with pytest.raises(ops.OpsRefused) as exc:
        ops.resume(NAME, by="masaru")
    assert exc.value.code == "ads_not_stopped"
    meta = env["meta"]
    cid = meta.campaign(status="ACTIVE")
    meta.stuck.add(cid)
    assert ops.stop(NAME, by="masaru")["confirmed"] is False
    with pytest.raises(ops.OpsRefused) as exc:
        ops.resume(NAME, by="masaru")
    assert exc.value.code == "ads_stop_unconfirmed"


def test_resumeはMCPに無く_引数は確かめと番号だけ():
    sys.path.insert(0, str(REPO / "mcp"))
    import server
    assert not [t for t in server.TOOLS if re.search(r"resume|unstop|watch", t["name"])]
    assert "thth_ads_resume" not in ads_cli.MCP_TOOLS
    options = set()
    for path, parser in _walk(_ads_parser()):
        if path == ("resume",):
            for action in parser._actions:
                options |= set(action.option_strings)
    assert options == {"-h", "--help", "--confirm", "--approval", "--gate", "--by", "--json"}


def test_resumeのdigestは印と木のどれを変えても変わる():
    tree = {"campaigns": [{"id": "1", "status": "PAUSED", "effective_status": "PAUSED"}],
            "adsets": [{"id": "2", "status": "PAUSED", "effective_status": "CAMPAIGN_PAUSED"}],
            "ads": [{"id": "3", "status": "PAUSED", "effective_status": "ADSET_PAUSED"}]}
    mark = {"reason": "owner", "stopped_at": "2026-11-02T10:00:00+09:00", "actor": "masaru", "via": "cli"}
    base = digest.compute_resume_digest(act_id=ACT, stopped=mark, tree=tree)
    import copy
    variants = []
    for change in (lambda t, m: t["ads"][0].update(status="ACTIVE"), lambda t, m: t["adsets"].append({"id": "4"}),
                   lambda t, m: m.update(stopped_at="2026-11-02T11:00:00+09:00"), lambda t, m: m.update(actor="x")):
        t, m = copy.deepcopy(tree), dict(mark)
        change(t, m)
        variants.append(digest.compute_resume_digest(act_id=ACT, stopped=m, tree=t))
    variants.append(digest.compute_resume_digest(act_id="999", stopped=mark, tree=tree))
    assert base not in variants and len(set(variants)) == len(variants)
