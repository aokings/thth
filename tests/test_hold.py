"""意図して止めた draft の印（`thth hold`・11/25 masaru の決定 6）。

試しでわざと止めた原稿が、hub で毎日「未承認・急ぎ」として上がってきた（THTH運用 第8・アスモン関東 第18）。
理由と期限を付けて止めた原稿は `unapproved_soon` の数えから外し、board の `held_soon` に別の数で出す。
期限（その日の終わり・JST）を過ぎたら未承認に戻る。判定は `report.is_held()` の 1 か所。
正本: media-hub の docs/設計_h2dの要望の発展と設計_2026-11-25.md の「決めること 6」・§N2 の 4。
"""
from __future__ import annotations

import datetime
import json

from tests.conftest import approve_via_cli, run_thth, write_queue_file
from thth import approval_ledger, jst, remote, report

KEY = "docs/sns/queue/{}"


def _now():
    return datetime.datetime.now(jst.JST)


def _day(days: int) -> str:
    return (_now().date() + datetime.timedelta(days=days)).isoformat()


def _draft(account, name, hours=3):
    at = (_now() + datetime.timedelta(hours=hours)).isoformat()
    return write_queue_file(account["queue_dir"], name,
                            fm_overrides={"status": "draft", "approved_sha": None, "publish_at": at},
                            body=f"## threads\n\n{name} の本文です。\n")


def _hold(path, *extra, until=None, reason="12/02 までの試しでわざと止めている"):
    args = ["hold", *([path] if isinstance(path, str) else path), "--by", "テスト"]
    if until is not False:
        args += ["--until", until or _day(7)]
    if reason is not None:
        args += ["--reason", reason]
    return run_thth([*args, *extra])


def _board_row(name, now=None):
    rows = report.board_summary(now=now or _now())["accounts"]
    return next(row for row in rows if row["account"] == name)


def _cells(name, now):
    cell = report.unapproved_soon(name, now=now)[name]
    return report.board_unapproved_cell(cell), report.board_held_cell(cell)


# ---- 止める・期限内は数えない・board の形 -------------------------------------------

def test_止めると未承認の数えから外れheld_soonに出る(isolated_account):
    name = isolated_account["name"]
    held = _draft(isolated_account, "held.md", hours=2)
    _draft(isolated_account, "open.md", hours=4)
    with open(held, encoding="utf-8") as f:
        before_text = f.read()
    until = _day(7)

    result = _hold(held, "--json", until=until)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["count"] == 1 and out["recorded"] == "ledger" and out["until"] == until
    assert out["done"][0]["path"] == KEY.format("held.md") and out["done"][0]["held_until"] == until
    # 本文には触らない。台帳は held・status は draft のまま。
    with open(held, encoding="utf-8") as f:
        assert f.read() == before_text
    entry = approval_ledger.load(name)[KEY.format("held.md")]
    assert entry["state"] == "held" and entry["fields"]["status"] == "draft"
    assert entry["fields"]["held_until"] == until and entry["fields"]["held_by"] == "テスト"

    row = _board_row(name)
    assert row["unapproved_soon"]["count"] == 1
    assert row["unapproved_soon"]["paths"] == [KEY.format("open.md")]
    assert row["held_soon"] == {
        "count": 1, "until_min": until,
        "items": [{"path": KEY.format("held.md"), "until": until,
                   "reason": "12/02 までの試しでわざと止めている"}]}


def test_held_soonは0でも出る_until_minは一番早い期限(isolated_account):
    name = isolated_account["name"]
    assert _board_row(name)["held_soon"] == {"count": 0, "until_min": None, "items": []}
    a = _draft(isolated_account, "a.md", hours=1)
    b = _draft(isolated_account, "b.md", hours=2)
    assert _hold(a, until=_day(9), reason="試し A").returncode == 0
    assert _hold(b, until=_day(3), reason="試し B").returncode == 0
    cell = _board_row(name)["held_soon"]
    assert cell["count"] == 2 and cell["until_min"] == _day(3)
    assert [(i["path"], i["until"], i["reason"]) for i in cell["items"]] == [
        (KEY.format("a.md"), _day(9), "試し A"), (KEY.format("b.md"), _day(3), "試し B")]
    assert set(cell) == {"count", "until_min", "items"}
    assert all(set(i) == {"path", "until", "reason"} for i in cell["items"])
    assert _board_row(name)["unapproved_soon"]["count"] == 0


def test_読めない口座はheld_soonもcannot_say(isolated_account):
    import os
    with open(os.path.join(isolated_account["accounts_dir"], "broken.json"), "w", encoding="utf-8") as f:
        f.write("{ not json")
    row = _board_row("broken")
    assert set(row["held_soon"]) == {"cannot_say"} and row["held_soon"]["cannot_say"]


# ---- 期限を過ぎたら戻る --------------------------------------------------------------

def test_期限の日の終わりまでは止め_過ぎたら未承認に戻る(isolated_account):
    name = isolated_account["name"]
    path = _draft(isolated_account, "t.md", hours=3)
    until = _day(1)
    assert _hold(path, until=until).returncode == 0
    day = datetime.date.fromisoformat(until)
    last = datetime.datetime.combine(day, datetime.time(23, 59, 59), tzinfo=jst.JST)
    unapproved, held = _cells(name, last)
    assert unapproved["count"] == 0 and held["count"] == 1
    # 翌日の 0 時（JST）からは未承認に戻る。窓（-3d）の中に publish_at があるので数える。
    after = last + datetime.timedelta(seconds=1)
    unapproved, held = _cells(name, after)
    assert unapproved["paths"] == [KEY.format("t.md")] and held["count"] == 0
    # UTC で渡しても JST の日で決める（UTC 15:00 は JST の翌日 0:00）。
    utc_after = after.astimezone(datetime.timezone.utc)
    assert _cells(name, utc_after)[0]["count"] == 1


def test_is_heldは読めない期限を止めていないと見る():
    now = datetime.datetime(2026, 11, 25, 12, 0, tzinfo=jst.JST)
    assert report.is_held({"held_until": "2026-11-25"}, now=now)
    assert not report.is_held({"held_until": "2026-11-24"}, now=now)
    for bad in (None, "", "2026/12/02", "2026-13-01", "12-02", 20261202):
        assert not report.is_held({"held_until": bad}, now=now), bad


# ---- schedule の行 -----------------------------------------------------------------

def test_scheduleの行にheld_untilとheld_reasonが出てunapprovedには出ない(isolated_account):
    name = isolated_account["name"]
    path = _draft(isolated_account, "s.md", hours=2)
    _draft(isolated_account, "o.md", hours=5)
    until = _day(5)
    assert _hold(path, until=until, reason="試しの対照").returncode == 0

    rows = json.loads(run_thth(["schedule", name, "--json"]).stdout)
    by_file = {r["file"]: r for r in rows}
    assert by_file["s.md"]["held_until"] == until and by_file["s.md"]["held_reason"] == "試しの対照"
    assert by_file["s.md"]["status"] == "draft"
    assert by_file["o.md"]["held_until"] is None and by_file["o.md"]["held_reason"] is None

    human = run_thth(["schedule", name])
    assert f"止めています（{until} まで・試しの対照）" in human.stdout, human.stdout

    unapproved = run_thth(["schedule", "--unapproved", "--json", name])
    assert unapproved.returncode == 0
    assert [r["file"] for r in json.loads(unapproved.stdout)] == ["o.md"]
    text = run_thth(["schedule", "--unapproved", name])
    assert "s.md" not in text.stdout and "止めている 1 本は数えていません" in text.stdout, text.stdout


# ---- undo・承認で解ける・select -----------------------------------------------------

def test_undoで止める前に戻り_ディレクトリのundoは止めたものだけ拾う(isolated_account):
    name = isolated_account["name"]
    a = _draft(isolated_account, "a.md", hours=1)
    _draft(isolated_account, "b.md", hours=2)
    assert _hold(a).returncode == 0
    undo = run_thth(["hold", isolated_account["queue_dir"], "--undo", "--by", "テスト", "--json"])
    assert undo.returncode == 0, undo.stderr
    out = json.loads(undo.stdout)
    assert [d["path"] for d in out["done"]] == [KEY.format("a.md")] and out["refused"] == []
    assert KEY.format("a.md") not in approval_ledger.load(name)
    assert _board_row(name)["unapproved_soon"]["count"] == 2
    # 止めていないものの --undo（名指し）は断る。
    again = run_thth(["hold", a, "--undo", "--by", "テスト"])
    assert again.returncode == 1 and "止めた記録がありません" in again.stderr
    # --undo に --until・--reason は付けない。
    assert run_thth(["hold", a, "--undo", "--by", "テスト", "--until", _day(3)]).returncode == 2


def test_取り消した原稿を止め_undoで取り消しの記録に戻る(isolated_account):
    name = isolated_account["name"]
    path = _draft(isolated_account, "r.md", hours=3)
    assert approve_via_cli(path).returncode == 0
    assert run_thth(["revoke", path, "--by", "テスト"]).returncode == 0
    assert _hold(path).returncode == 0
    assert _board_row(name)["held_soon"]["count"] == 1
    assert run_thth(["hold", path, "--undo", "--by", "テスト"]).returncode == 0
    entry = approval_ledger.load(name)[KEY.format("r.md")]
    assert entry["state"] == "revoked"
    assert [h["event"] for h in entry["history"]][-3:] == ["revoked", "held", "unheld"]


def test_承認すると止めは解け_取り消すと未承認に戻る(isolated_account):
    name = isolated_account["name"]
    path = _draft(isolated_account, "ap.md", hours=3)
    assert _hold(path).returncode == 0
    assert approve_via_cli(path).returncode == 0
    entry = approval_ledger.load(name)[KEY.format("ap.md")]
    assert entry["state"] == "approved"
    assert [h["event"] for h in entry["history"]][-2:] == ["held", "approved"]
    row = _board_row(name)
    assert row["unapproved_soon"]["count"] == 0 and row["held_soon"]["count"] == 0
    # 承認を取り消すと、止めは戻らない（記録の順で承認が後）。未承認に数える。
    assert run_thth(["revoke", path, "--by", "テスト"]).returncode == 0
    row = _board_row(name)
    assert row["unapproved_soon"]["paths"] == [KEY.format("ap.md")] and row["held_soon"]["count"] == 0


def test_止めた原稿はthrowで出ない(isolated_account):
    from thth import core
    path = _draft(isolated_account, "th.md", hours=-1)
    assert _hold(path).returncode == 0
    result = core.throw_once(isolated_account["name"])
    assert result.file is None or "th.md" not in result.file


# ---- 断り ---------------------------------------------------------------------------

def test_断り_承認済み_閉じた_出たもの(isolated_account):
    approved = _draft(isolated_account, "ok.md", hours=3)
    assert approve_via_cli(approved).returncode == 0
    result = _hold(approved)
    assert result.returncode == 1
    # なぜ・通すには・もっと良く の 3 つ。
    assert "承認済み" in result.stderr and "thth revoke" in result.stderr and "publish_at" in result.stderr

    closed = _draft(isolated_account, "w.md", hours=3)
    assert run_thth(["withdraw", closed, "--by", "テスト"]).returncode == 0
    result = _hold(closed)
    assert result.returncode == 1 and "withdraw --undo" in result.stderr, result.stderr

    posted = write_queue_file(isolated_account["queue_dir"], "p.md", fm_overrides={
        "status": "posted", "post_id": "99", "publish_at": _now().isoformat(), "posted_at": _now().isoformat()})
    result = _hold(posted)
    assert result.returncode == 1 and "post_id" in result.stderr
    for key in ("ok.md", "p.md"):
        entry = approval_ledger.load(isolated_account["name"]).get(KEY.format(key))
        assert entry is None or entry["state"] != "held"


def test_断る本だけ断って他は進める(isolated_account):
    good = _draft(isolated_account, "g.md", hours=3)
    approved = _draft(isolated_account, "x.md", hours=3)
    assert approve_via_cli(approved).returncode == 0
    result = _hold([good, approved], "--json")
    assert result.returncode == 1
    out = json.loads(result.stdout)
    assert [d["path"] for d in out["done"]] == [KEY.format("g.md")]
    assert [r["file"] for r in out["refused"]] == [approved]


def test_断り_過去の日_今日_形違い_理由なし(isolated_account):
    path = _draft(isolated_account, "d.md", hours=3)
    for until in (_day(-1), _day(0), "2026/12/02", "tomorrow"):
        result = _hold(path, until=until)
        assert result.returncode == 2, (until, result.stderr)
        assert "--until" in result.stderr
    result = _hold(path, until=False)
    assert result.returncode == 2 and "--until" in result.stderr
    for reason in (None, "", "   "):
        result = _hold(path, reason=reason)
        assert result.returncode == 2 and "--reason" in result.stderr, (reason, result.stderr)
    assert _hold(path, reason="x\nstatus: approved").returncode == 2
    assert run_thth(["hold", path, "--until", _day(3), "--reason", "r"]).returncode == 2  # --by が無い
    assert approval_ledger.load(isolated_account["name"]).get(KEY.format("d.md")) is None


def test_遠くの道には出さない():
    assert "hold" in remote.UNSUPPORTED


def test_approvalsに止めた行が出る(isolated_account):
    path = _draft(isolated_account, "ap2.md", hours=3)
    assert _hold(path, until=_day(4), reason="対照").returncode == 0
    human = run_thth(["approvals", isolated_account["name"]])
    assert f"止めた {KEY.format('ap2.md')}" in human.stdout and f"{_day(4)} まで（対照）" in human.stdout, human.stdout
    rows = json.loads(run_thth(["approvals", isolated_account["name"], "--json"]).stdout)["items"]
    assert rows[0]["state"] == "held" and rows[0]["held_until"] == _day(4) and rows[0]["held_reason"] == "対照"
