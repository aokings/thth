"""意図して止めた draft の印（`thth hold`・11/25 masaru の決定 6・監査 5.31.0 の直し）。

試しでわざと止めた原稿が、hub で毎日「未承認・急ぎ」として上がってきた（THTH運用 第8・アスモン関東 第18）。
理由と期限を付けて止めた原稿は `unapproved_soon` の数えから外し、board の `held_soon` に別の数で出す。
期限（その日の終わり・JST）を過ぎたら未承認に戻る。判定は `report.is_held()` の 1 か所。

止めは承認の台帳（`approvals.json`）に入れず、別のファイル `holds.json` に持つ（監査 D1: 古い版に戻しても
承認がそのまま読めるように）。
正本: media-hub の docs/設計_h2dの要望の発展と設計_2026-11-25.md の「決めること 6」・§N2 の 4。
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import types

import pytest

from tests.conftest import (approve_via_cli, commit_and_push_path, revoke_in_ledger, run_thth,
                            write_queue_file)
from thth import approval_ledger, jst, queuefile, remote, report

KEY = "docs/sns/queue/{}"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _now():
    return datetime.datetime.now(jst.JST)


def _day(days: int) -> str:
    return (_now().date() + datetime.timedelta(days=days)).isoformat()


def _draft(account, name, hours=3, base=None):
    at = ((base or _now()) + datetime.timedelta(hours=hours)).isoformat()
    return write_queue_file(account["queue_dir"], name,
                            fm_overrides={"status": "draft", "approved_sha": None, "publish_at": at},
                            body=f"## threads\n\n{name} の本文です。\n")


def _legacy_approved(account, name, hours=-1, base=None):
    """repo の front-matter に `status: approved`（4.4.0 までの形・指紋つき）で置く。"""
    at = ((base or _now()) + datetime.timedelta(hours=hours)).isoformat()
    return write_queue_file(account["queue_dir"], name, fm_overrides={"publish_at": at},
                            body=f"## threads\n\n{name} の本文です。\n")


def _hold(path, *extra, until=None, reason="12/02 までの試しでわざと止めている"):
    args = ["hold", *([path] if isinstance(path, str) else path), "--by", "テスト"]
    if until is not False:
        args += ["--until", until or _day(7)]
    if reason is not None:
        args += ["--reason", reason]
    return run_thth([*args, *extra])


def _fm(path):
    approval_ledger._LOAD_CACHE.clear()
    approval_ledger._HOLDS_CACHE.clear()
    return queuefile.parse(path).front_matter


def _board_row(name, now=None):
    rows = report.board_summary(now=now or _now())["accounts"]
    return next(row for row in rows if row["account"] == name)


def _cells(name, now):
    cell = report.unapproved_soon(name, now=now)[name]
    return report.board_unapproved_cell(cell), report.board_held_cell(cell)


def _noon():
    """静かな時間帯の外の今日（JST の 12:00）。throw の試験で時計をここに合わせる。"""
    return datetime.datetime.combine(_now().date(), datetime.time(12, 0), tzinfo=jst.JST)


def _throw(account, frozen_now_jst):
    from thth import core
    approval_ledger._LOAD_CACHE.clear()
    approval_ledger._HOLDS_CACHE.clear()
    frozen_now_jst(_noon())
    result = core.throw_once(account["name"])
    return os.path.basename(result.file) if result.file else None


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
    assert out["count"] == 1 and out["recorded"] == "holds" and out["until"] == until
    assert out["done"][0]["path"] == KEY.format("held.md") and out["done"][0]["held_until"] == until
    assert out["done"][0]["previous"] is None
    # 本文には触らない。止めは holds.json にあり、承認の台帳には書かない。
    with open(held, encoding="utf-8") as f:
        assert f.read() == before_text
    assert approval_ledger.load(name).get(KEY.format("held.md")) is None
    row = approval_ledger.load_holds(name)[KEY.format("held.md")]
    assert row["until"] == until and row["by"] == "テスト"
    assert _fm(held)["status"] == "draft" and _fm(held)["held_until"] == until

    board = _board_row(name)
    assert board["unapproved_soon"]["count"] == 1
    assert board["unapproved_soon"]["paths"] == [KEY.format("open.md")]
    assert board["held_soon"] == {
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
    with open(os.path.join(isolated_account["accounts_dir"], "broken.json"), "w", encoding="utf-8") as f:
        f.write("{ not json")
    row = _board_row("broken")
    assert set(row["held_soon"]) == {"cannot_say"} and row["held_soon"]["cannot_say"]


# ---- 期限を過ぎたら戻る・窓（監査 L3） ---------------------------------------------

def test_期限の日の終わりまでは止め_過ぎたら未承認に戻る(isolated_account):
    name = isolated_account["name"]
    path = _draft(isolated_account, "t.md", hours=3)
    until = _day(1)
    assert _hold(path, until=until).returncode == 0
    day = datetime.date.fromisoformat(until)
    last = datetime.datetime.combine(day, datetime.time(23, 59, 59), tzinfo=jst.JST)
    unapproved, held = _cells(name, last)
    assert unapproved["count"] == 0 and held["count"] == 1
    after = last + datetime.timedelta(seconds=1)
    unapproved, held = _cells(name, after)
    assert unapproved["paths"] == [KEY.format("t.md")] and held["count"] == 0
    # UTC で渡しても JST の日で決める（UTC 15:00 は JST の翌日 0:00）。
    assert _cells(name, after.astimezone(datetime.timezone.utc))[0]["count"] == 1


def test_長く止めても期限が切れたら3日は未承認に数え_止めている間は窓の外でもheld_soonに出る(isolated_account):
    """監査 L3: 1 週間止めると、期限の翌日には publish_at が窓（-3 日）の外で、どちらにも出なかった。"""
    name = isolated_account["name"]
    old = _draft(isolated_account, "old.md", hours=1)
    far = _draft(isolated_account, "far.md", hours=24 * 5)
    until = _day(7)
    assert _hold(old, until=until).returncode == 0
    assert _hold(far, until=until).returncode == 0
    # 止めて 4 日後: old の publish_at は 3 日より前、far は 24 時間より先。どちらも held_soon に出る。
    mid = _now() + datetime.timedelta(days=4)
    unapproved, held = _cells(name, mid)
    assert unapproved["count"] == 0
    assert [i["path"] for i in held["items"]] == [KEY.format("old.md"), KEY.format("far.md")]
    end = datetime.datetime.combine(datetime.date.fromisoformat(until) + datetime.timedelta(days=1),
                                    datetime.time(0, 0), tzinfo=jst.JST)
    # 期限が切れた直後（publish_at は 7 日前）から 3 日間は未承認に数える。
    for now in (end + datetime.timedelta(minutes=1), end + datetime.timedelta(days=3)):
        unapproved, held = _cells(name, now)
        assert KEY.format("old.md") in unapproved["paths"], now
        assert held["count"] == 0
    # 3 日を過ぎたら、止めていない原稿と同じく窓の外。
    unapproved, _ = _cells(name, end + datetime.timedelta(days=3, minutes=1))
    assert KEY.format("old.md") not in unapproved["paths"]


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


# ---- undo・止め直し・承認で解ける・select -------------------------------------------

def test_undoで外れ_ディレクトリのundoは止めたものだけ拾う(isolated_account):
    name = isolated_account["name"]
    a = _draft(isolated_account, "a.md", hours=1)
    _draft(isolated_account, "b.md", hours=2)
    assert _hold(a).returncode == 0
    undo = run_thth(["hold", isolated_account["queue_dir"], "--undo", "--by", "テスト", "--json"])
    assert undo.returncode == 0, undo.stderr
    out = json.loads(undo.stdout)
    assert [d["path"] for d in out["done"]] == [KEY.format("a.md")] and out["refused"] == []
    assert KEY.format("a.md") not in approval_ledger.load_holds(name)
    assert _board_row(name)["unapproved_soon"]["count"] == 2
    again = run_thth(["hold", a, "--undo", "--by", "テスト"])
    assert again.returncode == 1 and "止めた記録がありません" in again.stderr
    assert run_thth(["hold", a, "--undo", "--by", "テスト", "--until", _day(3)]).returncode == 2


def test_ディレクトリはもう止めてある原稿を上書きせず_名指しは止め直しと言う(isolated_account):
    """監査 L5: ディレクトリの hold が、先に止めてあった原稿の期限と理由を黙って上書きしていた。"""
    name = isolated_account["name"]
    first = _draft(isolated_account, "first.md", hours=1)
    other = _draft(isolated_account, "other.md", hours=2)
    assert _hold(first, until=_day(20), reason="A の試し").returncode == 0
    result = _hold(isolated_account["queue_dir"], until=_day(2), reason="まとめて")
    assert result.returncode == 0, result.stderr
    assert "もう止めてある 1 本は対象外" in result.stdout and "first.md" in result.stdout, result.stdout
    holds = approval_ledger.load_holds(name)
    assert holds[KEY.format("first.md")]["until"] == _day(20)
    assert holds[KEY.format("first.md")]["reason"] == "A の試し"
    assert holds[KEY.format("other.md")]["until"] == _day(2)
    # 名指しなら止め直す。前の期限と理由を言う。
    named = _hold(first, until=_day(4), reason="延長しない")
    assert named.returncode == 0
    assert f"止め直しました: {first}（{_day(4)} まで・延長しない。前は {_day(20)} まで・A の試し）" in named.stdout
    assert approval_ledger.load_holds(name)[KEY.format("first.md")]["until"] == _day(4)
    assert os.path.basename(other) == "other.md"


def test_取り消した原稿を止めても取り消しの記録はそのまま(isolated_account):
    name = isolated_account["name"]
    path = _draft(isolated_account, "r.md", hours=3)
    assert approve_via_cli(path).returncode == 0
    assert run_thth(["revoke", path, "--by", "テスト"]).returncode == 0
    assert _hold(path).returncode == 0
    fm = _fm(path)
    assert fm["status"] == "draft" and fm.get("revoked_at") and fm.get("held_until")
    assert approval_ledger.load(name)[KEY.format("r.md")]["state"] == "revoked"
    assert _board_row(name)["held_soon"]["count"] == 1
    assert run_thth(["hold", path, "--undo", "--by", "テスト"]).returncode == 0
    fm = _fm(path)
    assert fm["status"] == "draft" and fm.get("revoked_at") and not fm.get("held_until")


def test_承認すると止めは解け_取り消すと未承認に戻る(isolated_account):
    name = isolated_account["name"]
    path = _draft(isolated_account, "ap.md", hours=3)
    assert _hold(path).returncode == 0
    assert approve_via_cli(path).returncode == 0
    entry = approval_ledger.load(name)[KEY.format("ap.md")]
    assert entry["state"] == "approved"
    assert [h["event"] for h in entry["history"]][-2:] == ["approved", "hold_released"]
    assert KEY.format("ap.md") not in approval_ledger.load_holds(name)
    row = _board_row(name)
    assert row["unapproved_soon"]["count"] == 0 and row["held_soon"]["count"] == 0
    assert run_thth(["revoke", path, "--by", "テスト"]).returncode == 0
    row = _board_row(name)
    assert row["unapproved_soon"]["paths"] == [KEY.format("ap.md")] and row["held_soon"]["count"] == 0


def test_止めた原稿はthrowで出ず_承認すれば出る(isolated_account, frozen_now_jst):
    """監査 L1: 時計を今日の昼に合わせ、止めた原稿が出ないことと、同じ原稿を承認すれば出ることを対で縛る。"""
    path = _draft(isolated_account, "th.md", hours=-1, base=_noon())
    assert _hold(path).returncode == 0
    assert _throw(isolated_account, frozen_now_jst) is None
    assert approve_via_cli(path).returncode == 0
    assert _throw(isolated_account, frozen_now_jst) == "th.md"


def test_repoの古い承認を台帳で取り消して止めると_どの段でも出ない(isolated_account, frozen_now_jst):
    """監査 L1（台本 A3）: repo の `status: approved` → 台帳で取り消し → 止める → 閉じる → 戻す → 止めを外す。"""
    path = _legacy_approved(isolated_account, "a3.md", base=_noon())
    assert _fm(path)["status"] == "approved"
    revoke_in_ledger(path, isolated_account["name"])
    assert _fm(path)["status"] == "draft"
    assert _hold(path).returncode == 0
    steps = [
        ([], "draft"),
        (["withdraw", path, "--by", "テスト", "--reason", "x"], "withdrawn"),
        (["withdraw", path, "--undo", "--by", "テスト"], "draft"),
        (["hold", path, "--undo", "--by", "テスト"], "draft"),
    ]
    for args, status in steps:
        if args:
            assert run_thth(args).returncode == 0, args
        fm = _fm(path)
        assert fm["status"] == status and not fm.get("approved_sha"), (args, fm)
        assert _throw(isolated_account, frozen_now_jst) is None, args
    # 閉じたのを戻すと止めも戻っている（止めの行は閉じても残る）。
    assert approval_ledger.load(isolated_account["name"])[KEY.format("a3.md")]["state"] == "revoked"


def test_承認済みには止めの印を見せず_承認済みの上の止めは書かない(isolated_account):
    """監査 L1（M4・M8）: 承認済みの原稿に止めの行があっても approved のまま・held_* を見せない。
    読んでから書くまでに承認が入ったら、ロックの中の検査で断る。"""
    name = isolated_account["name"]
    path = _draft(isolated_account, "m.md", hours=3)
    assert approve_via_cli(path).returncode == 0
    key = KEY.format("m.md")
    repo = approval_ledger.repo_root_of(path)
    with pytest.raises(approval_ledger.LedgerError):
        approval_ledger.record_hold(name, key, repo=repo, until=_day(3), reason="r", by="t", at=jst.iso(),
                                    check=lambda: "承認済みです")
    assert key not in approval_ledger.load_holds(name)
    # 検査を通さずに行を置いても（手で書いた台帳など）、承認済みには重ねない。
    approval_ledger.record_hold(name, key, repo=repo, until=_day(3), reason="r", by="t", at=jst.iso())
    fm = _fm(path)
    assert fm["status"] == "approved" and "held_until" not in fm
    assert _board_row(name)["held_soon"]["count"] == 0


def test_承認のあとrepoのstatusを書き換えた原稿も止められる(isolated_account):
    """監査 L2: 「先に thth revoke」と言っても revoke が断る、行き止まりだった。止めは承認の記録に触らないので通す。"""
    name = isolated_account["name"]
    path = _draft(isolated_account, "a5.md", hours=3)
    assert approve_via_cli(path).returncode == 0
    text = open(path, encoding="utf-8").read().replace("status: draft", "status: approved")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    commit_and_push_path(path, message="hand edit")
    fm = _fm(path)
    assert fm["status"] == "draft" and fm.get(approval_ledger.REPO_STATUS_MARK)
    result = _hold(path)
    assert result.returncode == 0, result.stderr
    assert approval_ledger.load(name)[KEY.format("a5.md")]["state"] == "approved"
    row = _board_row(name)
    assert row["held_soon"]["count"] == 1 and row["unapproved_soon"]["count"] == 0


# ---- 古い版に戻したとき（監査 D1） ---------------------------------------------------

def _old_valid_entry(entry) -> bool:
    """5.30.1 の `approval_ledger._valid_entry` の写し（state は 3 つだけ）。"""
    return (isinstance(entry, dict) and entry.get("state") in ("approved", "revoked", "withdrawn")
            and isinstance(entry.get("file"), str) and isinstance(entry.get("fields"), dict))


def _ledger_with_every_kind(account):
    name = account["name"]
    ok = _draft(account, "ok.md", hours=3)
    assert approve_via_cli(ok).returncode == 0
    rv = _draft(account, "rv.md", hours=3)
    assert approve_via_cli(rv).returncode == 0
    assert run_thth(["revoke", rv, "--by", "テスト"]).returncode == 0
    wd = _draft(account, "wd.md", hours=3)
    assert run_thth(["withdraw", wd, "--by", "テスト"]).returncode == 0
    hd = _draft(account, "hd.md", hours=3)
    assert _hold(hd).returncode == 0
    both = _draft(account, "both.md", hours=3)
    assert approve_via_cli(both).returncode == 0
    assert run_thth(["revoke", both, "--by", "テスト"]).returncode == 0
    assert _hold(both).returncode == 0
    return approval_ledger.path_for(name)


def test_止めがあっても5_30_1の形の台帳として読める(isolated_account):
    path = _ledger_with_every_kind(isolated_account)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert data["version"] == 1
    assert all(_old_valid_entry(v) and v["file"] == k for k, v in data["items"].items())
    assert data["items"][KEY.format("ok.md")]["state"] == "approved"
    assert KEY.format("hd.md") not in data["items"]


def test_止めがあっても5_30_1の_readが承認をそのまま読む(isolated_account):
    """本物の 5.30.1 の `approval_ledger` を git の tag から読んで、新しい版が書いた台帳を読ませる。"""
    source = subprocess.run(["git", "-C", ROOT, "show", "v5.30.1:thth/approval_ledger.py"],
                            capture_output=True, text=True)
    if source.returncode != 0:
        pytest.skip("tag v5.30.1 が無い clone（浅い checkout）。形の写しの試験が代わりに縛る")
    old = types.ModuleType("thth._approval_ledger_5301")
    old.__package__ = "thth"
    exec(compile(source.stdout, "approval_ledger_5.30.1.py", "exec"), old.__dict__)
    path = _ledger_with_every_kind(isolated_account)
    items = old._read(path)
    assert items[KEY.format("ok.md")]["state"] == "approved"
    assert items[KEY.format("rv.md")]["state"] == "revoked"
    assert items[KEY.format("wd.md")]["state"] == "withdrawn"
    assert KEY.format("hd.md") not in items


def test_知らないstateの行があっても台帳ごと倒れず_その行だけdraftに倒す(isolated_account):
    """監査 D1 の付け足し: 次に state を足したとき、古い版で同じ事が起きないように。"""
    name = isolated_account["name"]
    ok = _draft(isolated_account, "ok.md", hours=3)
    assert approve_via_cli(ok).returncode == 0
    legacy = _legacy_approved(isolated_account, "legacy.md", hours=3)
    path = approval_ledger.path_for(name)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    key = KEY.format("legacy.md")
    data["items"][key] = {"file": key, "state": "future_state", "fields": {"status": "approved"},
                          "repo": approval_ledger.repo_root_of(legacy)}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    assert approval_ledger.problem(name) is None
    assert _fm(ok)["status"] == "approved"
    fm = _fm(legacy)
    assert fm["status"] == "draft" and fm.get(approval_ledger.UNKNOWN_ROW_MARK) == "true"
    shown = run_thth(["approvals", name])
    assert shown.returncode == 0 and "読めない行（state: future_state）" in shown.stdout, shown.stdout


# ---- 承認の一段目（監査 L4） ---------------------------------------------------------

def test_ディレクトリの承認の一段目に止めていますと出る(isolated_account):
    held = _draft(isolated_account, "held.md", hours=2)
    _draft(isolated_account, "open.md", hours=3)
    assert _hold(held, until=_day(6), reason="対照").returncode == 0
    first = run_thth(["approve", isolated_account["queue_dir"]])
    assert first.returncode == 1
    assert f"⏸ 止めています: {held}（{_day(6)} まで・対照）" in first.stdout, first.stdout
    assert "止めている原稿が 1 本入っています" in first.stdout
    as_json = json.loads(run_thth(["approve", isolated_account["queue_dir"], "--json"]).stdout)
    by_name = {os.path.basename(f["file"]): f for f in as_json["files"]}
    assert by_name["held.md"]["held"] == {"until": _day(6), "reason": "対照"}
    assert "held" not in by_name["open.md"]


# ---- 断り ---------------------------------------------------------------------------

def test_断り_承認済み_閉じた_出たもの(isolated_account):
    approved = _draft(isolated_account, "ok.md", hours=3)
    assert approve_via_cli(approved).returncode == 0
    result = _hold(approved)
    assert result.returncode == 1
    assert "承認済み" in result.stderr and "thth revoke" in result.stderr and "publish_at" in result.stderr

    closed = _draft(isolated_account, "w.md", hours=3)
    assert run_thth(["withdraw", closed, "--by", "テスト"]).returncode == 0
    result = _hold(closed)
    assert result.returncode == 1 and "--undo" in result.stderr, result.stderr

    posted = write_queue_file(isolated_account["queue_dir"], "p.md", fm_overrides={
        "status": "posted", "post_id": "99", "publish_at": _now().isoformat(), "posted_at": _now().isoformat()})
    result = _hold(posted)
    assert result.returncode == 1 and "post_id" in result.stderr
    assert approval_ledger.load_holds(isolated_account["name"]) == {}


def test_断る本だけ断って他は進める(isolated_account):
    good = _draft(isolated_account, "g.md", hours=3)
    approved = _draft(isolated_account, "x.md", hours=3)
    assert approve_via_cli(approved).returncode == 0
    result = _hold([good, approved], "--json")
    assert result.returncode == 1
    out = json.loads(result.stdout)
    assert [d["path"] for d in out["done"]] == [KEY.format("g.md")]
    assert [r["file"] for r in out["refused"]] == [approved]


def test_断り_過去の日_今日_遠すぎる日_形違い_理由なし_長すぎる理由(isolated_account):
    path = _draft(isolated_account, "d.md", hours=3)
    for until in (_day(-1), _day(0), _day(91), "9999-12-31", "2026/12/02", "tomorrow"):
        result = _hold(path, until=until)
        assert result.returncode == 2, (until, result.stderr)
        assert "--until" in result.stderr
    assert "thth withdraw" in _hold(path, until=_day(91)).stderr
    assert _hold(path, until=_day(90)).returncode == 0
    assert run_thth(["hold", path, "--undo", "--by", "テスト"]).returncode == 0
    result = _hold(path, until=False)
    assert result.returncode == 2 and "--until" in result.stderr
    for reason in (None, "", "   "):
        result = _hold(path, reason=reason)
        assert result.returncode == 2 and "--reason" in result.stderr, (reason, result.stderr)
    long = _hold(path, reason="あ" * 201)
    assert long.returncode == 2 and "200 字" in long.stderr, long.stderr
    assert _hold(path, reason="あ" * 200).returncode == 0
    assert run_thth(["hold", path, "--undo", "--by", "テスト"]).returncode == 0
    assert _hold(path, reason="x\nstatus: approved").returncode == 2
    assert run_thth(["hold", path, "--until", _day(3), "--reason", "r"]).returncode == 2  # --by が無い
    assert approval_ledger.load_holds(isolated_account["name"]) == {}


def test_遠くの道には出さない():
    assert "hold" in remote.UNSUPPORTED


def test_approvalsに止めた行が出る(isolated_account):
    path = _draft(isolated_account, "ap2.md", hours=3)
    assert _hold(path, until=_day(4), reason="対照").returncode == 0
    human = run_thth(["approvals", isolated_account["name"]])
    assert f"止めている {KEY.format('ap2.md')}" in human.stdout and f"{_day(4)} まで（対照）" in human.stdout, human.stdout
    out = json.loads(run_thth(["approvals", isolated_account["name"], "--json"]).stdout)
    assert out["holds"][0]["file"] == KEY.format("ap2.md") and out["holds"][0]["until"] == _day(4)
    assert out["holds_problem"] is None
