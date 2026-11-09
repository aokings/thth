"""未承認で時刻が近い原稿の数え上げと、出さないと決めた draft を閉じる口（11/06）。

kopicha-threads の draft 6 本が未承認のまま publish_at を過ぎ、誰も気づかなかった
（依頼 docs/依頼_運用から開発へ_未承認で時刻が近い原稿を機械で知らせる_2026-11-06.md）。
hub が `thth board --json` の `unapproved_soon` を読んで masaru に知らせる。判定は
`report.is_unapproved_soon()` の 1 か所で、board と `thth schedule --unapproved` が共有する。
"""
from __future__ import annotations

import datetime
import json
import os

from tests.conftest import approve_via_cli, run_thth, write_queue_file
from thth import approval_ledger, jst, report

NOW = datetime.datetime(2026, 9, 9, 10, 0, tzinfo=jst.JST)


def _at(delta: datetime.timedelta, base=NOW) -> str:
    return (base + delta).isoformat()


def _draft(account, name, publish_at, **extra):
    fm = {"status": "draft", "approved_sha": None, "publish_at": publish_at}
    fm.update(extra)
    return write_queue_file(account["queue_dir"], name, fm_overrides=fm,
                            body=f"## threads\n\n{name} の本文です。\n")


def _board_cell(name, now=NOW):
    rows = report.board_summary(now=now)["accounts"]
    return next(row for row in rows if row["account"] == name)["unapproved_soon"]


def _cli_rows(args):
    result = run_thth(["schedule", "--unapproved", "--json", *args])
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout), result


# ---- 1 本を未承認で残すと、board と schedule --unapproved の両方に出る -------------

def test_未承認を1本残すとboardのcountとschedule_unapprovedに出る(isolated_account):
    real_now = datetime.datetime.now(jst.JST)
    path = _draft(isolated_account, "late.md", _at(datetime.timedelta(hours=-5), real_now))

    cell = _board_cell(isolated_account["name"], now=real_now)
    assert cell["count"] == 1
    assert cell["window"] == "-3d,+24h"
    assert cell["paths"] == ["docs/sns/queue/late.md"]
    assert cell["first_at"] == _at(datetime.timedelta(hours=-5), real_now)
    assert set(cell) == {"count", "first_at", "window", "paths"}

    rows, _ = _cli_rows([isolated_account["name"]])
    assert [r["file"] for r in rows] == ["late.md"]
    assert rows[0]["path"] == "docs/sns/queue/late.md"
    assert os.path.basename(path) == "late.md"

    human = run_thth(["schedule", "--unapproved", isolated_account["name"]])
    assert human.returncode == 0 and "late.md" in human.stdout, human.stdout


def test_無ければcountは0で常に出る(isolated_account):
    cell = _board_cell(isolated_account["name"])
    assert cell == {"count": 0, "first_at": None, "window": "-3d,+24h", "paths": []}


# ---- 窓の端 -------------------------------------------------------------------

def test_窓の外は出ない_境界は入る(isolated_account):
    hour = datetime.timedelta(hours=1)
    _draft(isolated_account, "old73.md", _at(-73 * hour))
    _draft(isolated_account, "old72.md", _at(-72 * hour))
    _draft(isolated_account, "soon24.md", _at(24 * hour))
    _draft(isolated_account, "far25.md", _at(25 * hour))
    cell = report.unapproved_soon(isolated_account["name"], now=NOW)[isolated_account["name"]]
    assert [r["file"] for r in cell["rows"]] == ["old72.md", "soon24.md"]
    assert cell["first_at"] == _at(-72 * hour)


def test_windowを変えられる_変な形はrc2(isolated_account):
    real_now = datetime.datetime.now(jst.JST)
    _draft(isolated_account, "in3h.md", _at(datetime.timedelta(hours=3), real_now))
    rows, _ = _cli_rows([isolated_account["name"], "--window", "-1h,+2h"])
    assert rows == []
    rows, _ = _cli_rows([isolated_account["name"], "--window", "-1d,+4h"])
    assert [r["file"] for r in rows] == ["in3h.md"]
    for bad in ("3d,+24h", "-3d,24h", "-3w,+1d", "-3d", "", "-3d,+24h,"):
        result = run_thth(["schedule", "--unapproved", "--window", bad])
        assert result.returncode == 2, (bad, result.stdout, result.stderr)
    # --window だけ（--unapproved 無し）も断る。
    assert run_thth(["schedule", "--window", "-3d,+24h"]).returncode == 2


# ---- 承認済み・期限切れ・閉じたものは出ない ----------------------------------------

def test_承認済みは出ない(isolated_account):
    path = _draft(isolated_account, "ok.md", _at(datetime.timedelta(hours=2)))
    assert approve_via_cli(path).returncode == 0
    cell = report.unapproved_soon(isolated_account["name"], now=NOW)[isolated_account["name"]]
    assert cell["count"] == 0


def test_出さない期限を過ぎたものは出ない(isolated_account):
    _draft(isolated_account, "expired.md", _at(datetime.timedelta(hours=-10)),
           not_after=_at(datetime.timedelta(hours=-1)))
    _draft(isolated_account, "alive.md", _at(datetime.timedelta(hours=-10)),
           not_after=_at(datetime.timedelta(hours=5)))
    cell = report.unapproved_soon(isolated_account["name"], now=NOW)[isolated_account["name"]]
    assert cell["paths"] == ["docs/sns/queue/alive.md"]


def test_withdrawすると消え_scheduleにもthrowにも出ず_undoで戻る(isolated_account):
    real_now = datetime.datetime.now(jst.JST)
    path = _draft(isolated_account, "drop.md", _at(datetime.timedelta(hours=-2), real_now))
    _draft(isolated_account, "keep.md", _at(datetime.timedelta(hours=-1), real_now))
    with open(path, encoding="utf-8") as f:
        before_text = f.read()

    result = run_thth(["withdraw", path, "--by", "テスト", "--reason", "出さないと決めた", "--json"])
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["count"] == 1 and out["recorded"] == "ledger"
    assert out["done"][0]["path"] == "docs/sns/queue/drop.md"
    # repo には書かない（本文も front-matter もそのまま）。台帳に withdrawn。
    with open(path, encoding="utf-8") as f:
        assert f.read() == before_text
    entry = approval_ledger.load(isolated_account["name"])["docs/sns/queue/drop.md"]
    assert entry["state"] == "withdrawn" and entry["fields"]["withdrawn_reason"] == "出さないと決めた"

    cell = _board_cell(isolated_account["name"], now=real_now)
    assert cell["paths"] == ["docs/sns/queue/keep.md"]
    rows, _ = _cli_rows([isolated_account["name"]])
    assert [r["file"] for r in rows] == ["keep.md"]
    assert "drop.md" not in [r["file"] for r in report.schedule(isolated_account["name"], now=real_now)]
    # 閉じたものはディレクトリの承認に拾われない・throw にも出ない。
    first = run_thth(["approve", isolated_account["queue_dir"]])
    assert "drop.md" not in first.stdout and "keep.md" in first.stdout, first.stdout
    named = run_thth(["approve", path])
    assert named.returncode == 1 and "閉じた原稿" in named.stderr, named.stderr
    from thth import core
    result_throw = core.throw_once(isolated_account["name"])
    assert result_throw.file is None or "drop.md" not in result_throw.file

    # 戻す。
    undo = run_thth(["withdraw", path, "--undo", "--by", "テスト", "--json"])
    assert undo.returncode == 0, undo.stderr
    assert "docs/sns/queue/drop.md" not in approval_ledger.load(isolated_account["name"])
    cell = _board_cell(isolated_account["name"], now=real_now)
    assert cell["count"] == 2


def test_withdrawの断り(isolated_account):
    real_now = datetime.datetime.now(jst.JST)
    posted = write_queue_file(isolated_account["queue_dir"], "posted.md", fm_overrides={
        "status": "posted", "post_id": "99", "publish_at": _at(datetime.timedelta(hours=-3), real_now),
        "posted_at": _at(datetime.timedelta(hours=-3), real_now)})
    result = run_thth(["withdraw", posted, "--by", "テスト"])
    assert result.returncode == 1 and "post_id" in result.stderr, result.stderr

    approved = _draft(isolated_account, "appr.md", _at(datetime.timedelta(hours=3), real_now))
    assert approve_via_cli(approved).returncode == 0
    result = run_thth(["withdraw", approved, "--by", "テスト"])
    assert result.returncode == 1 and "revoke" in result.stderr, result.stderr

    draft = _draft(isolated_account, "d.md", _at(datetime.timedelta(hours=3), real_now))
    for args in (["--by", "a\nstatus: approved"], ["--by", "テスト", "--reason", "x\nstatus: draft"]):
        result = run_thth(["withdraw", draft, *args])
        assert result.returncode == 2, result.stderr
    assert run_thth(["withdraw", draft]).returncode == 2  # --by が無い
    # 閉じていないものの --undo は断る。
    assert run_thth(["withdraw", draft, "--undo", "--by", "テスト"]).returncode == 1
    assert approval_ledger.load(isolated_account["name"]).get("docs/sns/queue/d.md") is None


def test_取り消してから閉じ_undoで取り消しの記録に戻る(isolated_account):
    real_now = datetime.datetime.now(jst.JST)
    path = _draft(isolated_account, "r.md", _at(datetime.timedelta(hours=3), real_now))
    assert approve_via_cli(path).returncode == 0
    assert run_thth(["revoke", path, "--by", "テスト"]).returncode == 0
    assert run_thth(["withdraw", path, "--by", "テスト"]).returncode == 0
    assert approval_ledger.load(isolated_account["name"])["docs/sns/queue/r.md"]["state"] == "withdrawn"
    assert run_thth(["withdraw", path, "--undo", "--by", "テスト"]).returncode == 0
    entry = approval_ledger.load(isolated_account["name"])["docs/sns/queue/r.md"]
    assert entry["state"] == "revoked"
    assert [h["event"] for h in entry["history"]][-3:] == ["revoked", "withdrawn", "reopened"]


# ---- 数えられない口座 ------------------------------------------------------------

def test_読めない口座はcannot_sayだけ(isolated_account):
    broken = os.path.join(isolated_account["accounts_dir"], "broken.json")
    with open(broken, "w", encoding="utf-8") as f:
        f.write("{ not json")
    cell = _board_cell("broken")
    assert set(cell) == {"cannot_say"} and cell["cannot_say"]
    # 読める口座は数える。
    assert _board_cell(isolated_account["name"])["count"] == 0
    result = run_thth(["schedule", "--unapproved", "--json"])
    assert result.returncode == 0
    assert "broken" in result.stderr


def test_予約を使わない口座は置き場が壊れていても0で数える(isolated_account):
    """運用 #7293（11/09）: 同席専用（scheduled: false）で repo_dir が名だけの口座が、毎回「数えられません」と出た。"""
    path = os.path.join(isolated_account["accounts_dir"], "attended.json")
    with open(os.path.join(isolated_account["accounts_dir"], isolated_account["name"] + ".json"),
              encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.update({"account": "attended", "scheduled": False,
                "repo_dir": os.path.join(isolated_account["accounts_dir"], "no-such-repo")})
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    assert _board_cell("attended") == {"count": 0, "first_at": None, "window": "-3d,+24h", "paths": []}
    result = run_thth(["schedule", "--unapproved", "--json"])
    assert result.returncode == 0 and "attended" not in result.stderr, result.stderr
    # 予約を使う口座の壊れた置き場は、今までどおり数えられないと言う。
    cfg.update({"account": "scheduled-broken", "scheduled": True})
    with open(os.path.join(isolated_account["accounts_dir"], "scheduled-broken.json"), "w",
              encoding="utf-8") as f:
        json.dump(cfg, f)
    assert "repo_unavailable" in _board_cell("scheduled-broken")["cannot_say"]


def test_承認の台帳が読めない口座はcannot_say(isolated_account):
    _draft(isolated_account, "x.md", _at(datetime.timedelta(hours=1)))
    ledger = approval_ledger.path_for(isolated_account["name"])
    os.makedirs(os.path.dirname(ledger), exist_ok=True)
    with open(ledger, "w", encoding="utf-8") as f:
        f.write("{broken")
    cell = report.board_unapproved_cell(
        report.unapproved_soon(isolated_account["name"], now=NOW)[isolated_account["name"]])
    assert set(cell) == {"cannot_say"} and "approval_ledger_unreadable" in cell["cannot_say"]


# ---- 遠くの道には出さない ----------------------------------------------------------

def test_遠くの道ではunapprovedを断る():
    from thth import remote
    import argparse
    import pytest
    args = argparse.Namespace(unapproved=True, window=None, json=False)
    with pytest.raises(remote.RemoteError):
        remote._schedule(args, "someone")
