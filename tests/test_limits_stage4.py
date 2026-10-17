"""段 4: フィードバックと予告（設計 上限と連投の柵 §7・§9.2・§12 の段 4・§13 の T-P-8〜9・T-M-3）と、
段 3 から回った 2 つ（結果不明で止める・`bookmark_batch_approved` の記録）。

見るのは:
  - T-P-8 文の型: 1 行目は今の文のまま（符丁・言い出しを変えない）、2 行目に根拠（台帳の項目・確かさ・数の推測）、
    3 行目に通すには（`how_to_pass`）。全符丁 × 全動作で落ちない。推測は「推測」と書く。止めは戻す命令。
  - T-P-9 1 段目の予告: 印を置かず断らず、2 段目と同じ材料で同じ行を返す（`guard.foresee`）。
  - 止めた行（`rule`）が止めの印に残り、`thth events`・板が読む。遠くの道の断りの本文に `assessment`。
  - lint の予告（断らない）: X の返信の相手・直近と同じ本文・リンクの多さ・反応を頼む型。
  - T-M-3 観測: runs の `x_not_permitted`・`provider_rate_limited` が 1 行ずつ・2 回目で増えない・台帳に無い符丁に印。
  - 結果不明の札が照合を待つ間は次の札へ進まない（低-3）。変更ログの `bookmark_batch_approved` は残り、通知は飛ばない。

媒体には出ない（偽物の HTTP・`guard.check` を直に）。
"""
from __future__ import annotations

import datetime
import json
import os

import pytest

from tests.hatena_s1_flow_helpers import call, lane  # noqa: F401
from tests.hatena_s1_helpers import world  # noqa: F401
from tests.hatena_s4_helpers import ACCOUNT as BM_ACCOUNT, EXTERNAL, EXTERNAL_2, bm, bookmark, calls, cfg  # noqa: F401
from tests.hatena_s4_helpers import fake_http  # noqa: F401
from tests.test_assess import NOW, SNS, refused, sns_cfg, sns_post
from tests.test_v400_s1_remote import key  # noqa: F401
from thth import (admin_log, advise, assess, bookmark_batch, core, guard, jst, lint, media_observations,
                  principals, runs, sent)


# ---------------------------------------------------------------- T-P-8 文の型

def test_T_P_8_ブックマークの窓の断りは3行_1行目は今のまま_根拠は推測_通すには(bm):
    bm.edit_principal("hatena-main", bookmark_burst={"count": 1, "minutes": 10})
    bookmark(BM_ACCOUNT, url=EXTERNAL, comment="c")
    p = __import__("thth.hatena_bookmark", fromlist=["x"]).prepare(BM_ACCOUNT, cfg(BM_ACCOUNT), url=EXTERNAL_2,
                                                                     comment="c", check_remote=False)
    e = refused(lambda: __import__("thth.hatena_bookmark", fromlist=["x"]).execute_locked(
        BM_ACCOUNT, cfg(BM_ACCOUNT), p, actor="t", via="cli"))
    lines = core.refusal_lines(BM_ACCOUNT, e)
    assert lines[0] == core.guard_refusal_message(BM_ACCOUNT, e)          # 今の文は 1 バイトも変えない
    assert "行 bookmark.burst（主体・10 分・1/1）" in lines[1] and "数は THTH の推測" in lines[1]
    assert "hatenabookmark.normal_batch_use" in lines[1] and "確かさ 低" in lines[1]
    assert lines[2].startswith("  通すには: ") and "以降に打ち直す" in lines[2]
    assert "thth principal set hatena-main bookmark.burst <件数> <分> --by <名前>" in lines[2]
    assert e.assessment["how_to_pass"] and str(e) == "burst_limit"
    # CLI の --json にも assessment
    rc, out, err = call(["bookmark", "add", BM_ACCOUNT, "--url", EXTERNAL_2, "--comment", "c", "--json"])
    first = json.loads(out)
    rc, out, err = call(["bookmark", "add", BM_ACCOUNT, "--url", EXTERNAL_2, "--comment", "c", "--confirm",
                         first["digest"], "--by", "m", "--production", "--json"])
    body = json.loads(out)
    assert rc == 1 and body["error"] == "burst_limit" and body["assessment"]["rule"] == "bookmark.burst"
    assert body["assessment"]["how_to_pass"]
    rc, out, err = call(["bookmark", "add", BM_ACCOUNT, "--url", EXTERNAL_2, "--comment", "c", "--confirm",
                         first["digest"], "--by", "m", "--production"])
    assert "通すには: " in err and "根拠: " in err


def test_T_P_8_全符丁と全動作で1行目は今の文のまま():
    """柵: 型の 1 行目を作り直すと赤（今の文・符丁・言い出しを変えない）。"""
    for code in sorted(guard.REFUSALS):
        for kind in ("publish", "retract", "entry_create", "entry_update", "entry_delete"):
            e = guard.GuardRefused(code, next_at="2026-09-10T00:00:00+09:00")
            lines = core.refusal_lines("sns-a", e, kind=kind)
            assert lines == [core.guard_refusal_message("sns-a", e, kind=kind)]   # 行の無い断りは 1 行


def test_T_P_8_止めは戻す命令_止めた行が印に残る(world):
    cfg_ = sns_cfg(burst={"count": 2, "minutes": 10})
    for i in (1, 2):
        sns_post(world, SNS, NOW - datetime.timedelta(minutes=i))
    e = refused(lambda: guard.check(SNS, cfg_, "publish", now=NOW))
    assert str(e) == "account_stopped" and e.assessment["rule"] == "post.burst"
    assert e.assessment["how_to_pass"] == f"thth account resume {SNS} --by <名前>"
    assert guard.stopped(SNS)["rule"] == "post.burst"
    again = refused(lambda: guard.check(SNS, cfg_, "publish", now=NOW))
    assert again.assessment["stopped_by_rule"] == "post.burst"
    assert again.assessment["how_to_pass"] == f"thth account resume {SNS} --by <名前>"
    from thth import events_cli
    [row] = [r for r in events_cli._state_events(SNS, cfg_)[0] if r["kind"] == "account_stopped"]
    assert row["rule"] == "post.burst" and "行 post.burst" in row["summary"] and "resume" in row["summary"]


def test_推測は推測と書く_公式は種類と確かさ():
    assert "THTH の推測" in advise.basis_text({"id": "thth:post.link_heavy", "confidence": "low",
                                                "evidence_type": "inferred"})
    text = advise.basis_text(advise.trait_for("threads", "post.same_text"))
    assert "確かさ 中" in text and "threads.repeat_same_content" in text


# ---------------------------------------------------------------- T-P-9 1 段目の予告

def test_T_P_9_予告は印を置かず断らず_2段目と同じ行(world):
    """柵: foresee の中で `_enforce` が止めると赤（印が置かれる）。"""
    cfg_ = sns_cfg(burst={"count": 2, "minutes": 10})
    for i in (1, 2):
        sns_post(world, SNS, NOW - datetime.timedelta(minutes=i))
    seen = guard.foresee(SNS, cfg_, "publish", now=NOW)
    assert seen["outcome"] == "stopped" and seen["rule"] == "post.burst" and seen["how_to_pass"]
    assert guard.stopped(SNS) is None                          # 予告では止めない
    e = refused(lambda: guard.check(SNS, cfg_, "publish", now=NOW))
    assert e.assessment["rule"] == seen["rule"]                # 2 段目と同じ行
    assert guard.foresee(SNS, sns_cfg(), "retract", now=NOW)["outcome"] in ("pass", "stopped")


def test_T_P_9_ブックマークの1段目に見立ての節(bm):
    bm.edit_principal("hatena-main", bookmark_burst={"count": 1, "minutes": 10})
    rc, out, _ = call(["bookmark", "add", BM_ACCOUNT, "--url", EXTERNAL, "--comment", "c"])
    assert "見立て: 今の数では 2 段目で当たる柵はありません" in out
    bookmark(BM_ACCOUNT, url=EXTERNAL, comment="c")
    rc, out, _ = call(["bookmark", "add", BM_ACCOUNT, "--url", EXTERNAL_2, "--comment", "c"])
    assert "見立て: このまま 2 段目を打つと burst_limit（bookmark.burst・1/1）になる見込みです" in out
    rc, out, _ = call(["bookmark", "add", BM_ACCOUNT, "--url", EXTERNAL_2, "--comment", "c", "--json"])
    body = json.loads(out)
    assert rc == 1 and body["assessment"]["rule"] == "bookmark.burst" and body["advisories"] == []
    assert principals.stopped("hatena-main") is None and calls(bm, "POST").__len__() == 1


# ---------------------------------------------------------------- 遠くの道の本文

def test_遠くの道の断りの本文にassessment(bm, key):
    from thth import report_http, server_writes
    bm.edit_principal("hatena-main", bookmark_burst={"count": 1, "minutes": 10})
    bookmark(BM_ACCOUNT, url=EXTERNAL, comment="c")
    context = key({BM_ACCOUNT: "lane-a"})
    first = server_writes.execute(context, dict(operation="bookmark_request", account=BM_ACCOUNT, url=EXTERNAL_2,
                                                comment="c"), via="mcp")
    with pytest.raises(guard.GuardRefused) as caught:
        server_writes.execute(context, dict(operation="bookmark_request", account=BM_ACCOUNT, url=EXTERNAL_2,
                                            comment="c", confirm=first["digest"]), via="mcp")
    public = core.public_assessment(caught.value.assessment)
    assert public["rule"] == "bookmark.burst" and public["outcome"] == "wait" and public["next_at"]
    assert set(public) <= set(core.PUBLIC_ASSESSMENT_KEYS) | {"trait", "stopped_by_rule"}
    assert "assessment" in open(report_http.__file__, encoding="utf-8").read()


# ---------------------------------------------------------------- lint の予告

def _draft(tmp_path, body, **fm):
    head = {"thth": "1", "account": "x-acct", "publish_at": "2026-09-10T09:00:00+09:00", "status": "draft", **fm}
    text = "---\n" + "".join(f"{k}: {v}\n" for k, v in head.items()) + "---\n\n## x\n" + body + "\n"
    path = tmp_path / "d.md"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_lintの予告は断らない_Xの返信の相手とリンクと型(tmp_path, monkeypatch):
    monkeypatch.setattr(lint, "_account_cfg_or_none", lambda name: {"media": "x", "hashtags": False})
    plain = lint.lint_file(_draft(tmp_path, "こんにちは"))
    assert not [m for m in plain if "advise" in m]
    notes = lint.lint_file(_draft(tmp_path, "いいねしてください https://a.example/1 https://a.example/2 "
                                            "https://a.example/3", reply_to="1234567890"))
    advises = [m for m in notes if m.startswith("warning: advise: ")]
    assert [m.split(":")[2].strip() for m in advises] == ["counterpart", "link_heavy", "engagement_bait"]
    assert all(lint.is_warning(m) for m in advises)                      # 断らない
    assert "x.reply_requires_mention" in advises[0] and "THTH の推測" in advises[1]
    mention = lint.lint_file(_draft(tmp_path, "ありがとう", reply_to="1234567890", found_by="mention"))
    assert not [m for m in mention if "counterpart" in m]


def test_同じ本文は直近の公開と比べる(world):
    sns_post(world, SNS, jst.now_jst() - datetime.timedelta(days=1))
    assert advise.same_text("threads", "本文", account=SNS)["detail"] == "same"
    assert advise.same_text("threads", "別の本文", account=SNS) is None


# ---------------------------------------------------------------- T-M-3 観測

def test_T_M_3_観測はrunsから1行ずつ_冪等_台帳に無い符丁に印(world):
    """柵: 冪等の鍵を外すと赤（2 回目の collect で増える）。"""
    from tests.hatena_s1_helpers import EXAMPLES
    name = "x-obs"
    data = json.loads((EXAMPLES / "x.json").read_text(encoding="utf-8"))
    data.update({"account": name, "media": "x"})
    world.write_account(data)
    state = world.state(name)
    month = jst.month_str(jst.now_jst())
    for run_id, error in (("r1", "publication_failed: x_not_permitted"), ("r2", "provider_rate_limited"),
                          ("r3", "x_mystery_refusal"), ("r4", None)):
        runs.append_run(state, {"account": name, "run_id": run_id, "mode": "production", "action": "post",
                                "status": "failed" if error else "ok", "error": error}, month)
    assert media_observations.collect("x") == {"x": 3}
    assert media_observations.collect("x").get("x", 0) == 0          # 冪等
    by_code = {g["code"]: g for g in media_observations.summary("x")["rows"]}
    assert by_code["x_not_permitted"]["trait"] == "x.human_verification"
    assert by_code["provider_rate_limited"]["in_ledger"] is True
    assert by_code["x_mystery_refusal"]["in_ledger"] is False
    assert oct(os.stat(media_observations.path_for("x")).st_mode & 0o777) == "0o600"
    rc, out, _ = call(["media", "observations", "x", "--json"])
    assert rc == 0 and json.loads(out)["total"] == 3 and json.loads(out)["collected"] == 0
    rc, out, _ = call(["media", "observations", "x"])
    assert "台帳に無い" in out
    rc, out, _ = call(["media", "observe", "x", "--kind", "display_suppressed", "--note", "検索に出ない",
                       "--by", "t", "--json"])
    assert rc == 0 and json.loads(out)["kind"] == "display_suppressed"
    assert any(r["event"] == "media_observed" for r in admin_log.read()[0])
    assert "x 1 件 x_not_permitted" in media_observations.morning_line()
    assert call(["media", "observe", "x", "--kind", "bogus", "--note", "n", "--by", "t"])[0] == 2


# ---------------------------------------------------------------- 段 3 から回ったもの

@pytest.fixture
def bb(bm, monkeypatch):
    monkeypatch.setattr(bookmark_batch, "ACTIVE_HOURS", (0, 24))
    monkeypatch.setattr(bookmark_batch, "RANDOM", lambda: 0.5)
    monkeypatch.setattr(bookmark_batch, "_window", lambda now: (now - datetime.timedelta(hours=1),
                                                                 now + datetime.timedelta(minutes=600)))
    data = bm.account(BM_ACCOUNT)
    data["scheduled"] = True
    bm.write_account(data)
    return bm


def _approve(world, entries):
    path = world.tmp / "day.jsonl"
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n", encoding="utf-8")
    rc, out, _ = call(["bookmark", "batch", BM_ACCOUNT, str(path), "--json"])
    digest = json.loads(out)["digest"]
    rc, out, err = call(["bookmark", "batch", BM_ACCOUNT, str(path), "--confirm", digest, "--by", "masaru",
                         "--production", "--json"])
    assert rc == 0, (out, err)


def test_低3_結果不明は照合まで次の札へ進まない(bb):
    """柵: run の頭の `unresolved_unknown` を外すと赤（5xx の間も次の札を打つ）。"""
    bb.bookmarks.post = lambda url, form: (502, b"bad gateway")
    _approve(bb, [{"url": EXTERNAL, "comment": "c"}, {"url": EXTERNAL_2, "comment": "c"}])
    run = lambda m: bookmark_batch.run_due(BM_ACCOUNT, cfg(BM_ACCOUNT), log=lambda line: None,  # noqa: E731
                                           now=jst.now_jst() + datetime.timedelta(minutes=m))
    out = run(0)
    assert out["status"] == "unknown" and out["blocked"]["code"] == "bookmark_unknown"
    bb.bookmarks.post = None
    out = run(60)
    assert out["status"] == "blocked" and len(calls(bb, "POST")) == 1
    seen = bookmark_batch.attention(BM_ACCOUNT)
    assert seen["unknown"] == 1 and "thth bookmark reconcile" in bookmark_batch.attention_line(BM_ACCOUNT, seen)
    # はてなに付いていた → reconcile → 次の run で次の札
    from tests.hatena_s4_helpers import bookmark_object
    bb.bookmarks.marks[("lane-owner", EXTERNAL)] = bookmark_object(EXTERNAL)
    assert call(["bookmark", "reconcile", BM_ACCOUNT, "--by", "t"])[0] == 0
    out = run(120)
    assert out["status"] == "placed"
    [b] = bookmark_batch.load_batches(BM_ACCOUNT)
    assert [i["state"] for i in b["items"]] == ["unknown", "placed"]


def test_bookmark_batch_approvedは変更ログに残り_通知の道を通らない(bb, monkeypatch):
    """柵: EVENTS から外すと赤（黙って捨てられる）。"""
    from thth import admin_notifications
    sent_notices = []
    monkeypatch.setattr(admin_notifications, "notify", lambda event, cfg_: sent_notices.append(event))
    _approve(bb, [{"url": EXTERNAL, "comment": "c"}])
    events = [r for r in admin_log.read()[0] if r["event"] == "bookmark_batch_approved"]
    assert len(events) == 1 and events[0]["diff"] == {"items": [0, 1]}
    with admin_log.transaction():
        admin_log.append("bookmark_batch_approved", BM_ACCOUNT, cfg(BM_ACCOUNT), by="t", diff={"items": [0, 1]})
    assert sent_notices == []
    assert "bookmark_batch_approved" not in admin_notifications.SELECTED


def test_板の行に止めた行と戻す命令(world):
    from thth import report
    cfg_ = sns_cfg(burst={"count": 1, "minutes": 10})
    sns_post(world, SNS, NOW - datetime.timedelta(minutes=1))
    refused(lambda: guard.check(SNS, cfg_, "publish", now=NOW))
    cell = report._guard_stop_cell(SNS, cfg_)["guard_stop"]
    assert cell["rule"] == "post.burst" and cell["resume"] == f"thth account resume {SNS} --by <名前>"
    assert report._guard_stop_cell("sns-b", sns_cfg()) == {}            # 止まっていなければ鍵を足さない
