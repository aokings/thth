"""括りの段 3 の柵: 読むだけの命令を括りの中に通す通り道（VM の受け口）。

設計: docs/設計_THTHアカウント（括り）_叩き台_2026-11-26.md §5.2。
- 開くのは括りとレーンが書かれた鍵だけ。口座の鍵（招待で入った外の人の鍵を含む）は無い口と同じに断る。
- 表に無い命令・旗（書く・目印を付ける・道を取る）は断る。morning・observe は必ず --no-mark で打つ。
- 括り garden の鍵で打った board は garden の口座だけを返し、main の口座を出さない。
"""
from __future__ import annotations

import json

import pytest

from thth import command_scope, server_passthrough as sp
from thth.report_service import ReportContext, ReportServiceError
from tests.test_ws_isolation import two_workspaces  # noqa: F401  （同じ 2 つの括り）


def test_every_pass_command_is_a_workspace_command():
    for name in sp.PASS:
        assert command_scope.scope_of(name) == command_scope.WS, name


@pytest.mark.parametrize("argv,want", [
    (["board", "--json"], ["board", "--json"]),
    (["morning", "kopicha"], ["morning", "--no-mark", "kopicha"]),
    (["observe", "--json", "kopicha"], ["observe", "--json", "--no-mark", "kopicha"]),
    (["schedule", "--unapproved", "--window", "-3d,+24h"], ["schedule", "--unapproved", "--window", "-3d,+24h"]),
    (["analytics-report", "--by=topic"], ["analytics-report", "--by", "topic"]),
])
def test_allowed(argv, want):
    assert sp.build_argv(argv) == want


@pytest.mark.parametrize("argv,code", [
    (["send", "x"], "pass_unsupported"),                      # 書く命令
    (["admin", "ws", "list"], "pass_unsupported"),            # 管理者だけ
    (["morning", "--mark", "kopicha"], "pass_unsupported"),   # 表に無い旗
    (["handoff-report", "--mark-read"], "pass_unsupported"),  # 目印を付ける旗
    (["schedule", "--text", "hello"], "pass_unsupported"),    # 予定を書く旗
    (["unanswered", "--refresh", "a"], "pass_unsupported"),
    (["study-report", "/etc/passwd"], "pass_unsupported"),    # 手元のファイルを取る命令
    (["analytics-report", "--project", "../x"], "invalid_request"),  # 道
    (["analytics-report", "--by", "nope"], "invalid_request"),
    (["morning"], "invalid_request"),                         # 名前の数
    (["board", "x"], "invalid_request"),
    ([], "invalid_request"),
    ("board", "invalid_request"),
])
def test_refused(argv, code):
    with pytest.raises(sp.PassRefused, match=code):
        sp.build_argv(argv)


def test_account_key_cannot_enter():
    for ctx in (ReportContext({"gamma-garden": "gammaproj"}),
                ReportContext({"gamma-garden": "gammaproj"}, workspace="garden")):
        with pytest.raises(ReportServiceError, match="unsupported_operation"):
            sp.execute(ctx, {"operation": "ws_command", "account": "gamma-garden", "argv": ["board"]})


def test_lane_needs_workspace():
    with pytest.raises(ReportServiceError, match="invalid_context"):
        ReportContext({"a": "p"}, lane="asmon-kanto")


def test_board_through_the_pass_stays_inside(two_workspaces):  # noqa: F811
    ctx = ReportContext({"gamma-garden": "gammaproj"}, workspace="garden", lane="gamma-desk")
    out = sp.execute(ctx, {"operation": "ws_command", "account": "gamma-garden", "argv": ["board", "--json"]})
    assert out["rc"] == 0, out["stderr"]
    names = [row["account"] for row in json.loads(out["stdout"])["accounts"]]
    assert names == ["gamma-garden"]
    assert "alpha-main" not in out["stdout"] + out["stderr"]
    main = ReportContext({"alpha-main": "alphaproj"}, workspace="main", lane="alpha-desk")
    out = sp.execute(main, {"operation": "ws_command", "account": "alpha-main", "argv": ["board", "--json"]})
    assert [row["account"] for row in json.loads(out["stdout"])["accounts"]] == ["alpha-main"]
