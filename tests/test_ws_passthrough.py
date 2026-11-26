"""括りの段 3 の柵: 読むだけの命令を括りの中に通す通り道（VM の受け口）。

設計: docs/設計_THTHアカウント（括り）_叩き台_2026-11-26.md §5.2。
- 開くのは括りとレーンが書かれた鍵だけ。口座の鍵（招待で入った外の人の鍵を含む）は無い口と同じに断る。
- 表に無い命令・旗（書く・目印を付ける・道を取る）は断る。morning・observe は必ず --no-mark で打つ。
- 括り garden の鍵で打った board は garden の口座だけを返し、main の口座を出さない。
"""
from __future__ import annotations

import json
import os

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


# ---- 書く命令（1 つずつ・設計 §5.2）: approve・revoke・withdraw ----------------------------------

DIGEST = "ab" * 32


def _writer(account="gamma-garden", project="gammaproj", workspace="garden", lane="gamma-desk", **kw):
    kw.setdefault("writes", True)
    kw.setdefault("actor", "gamma")
    kw.setdefault("credential_digest", DIGEST)
    return ReportContext({account: project}, workspace=workspace, lane=lane, **kw)


@pytest.mark.parametrize("argv,want", [
    (["approve", "--account", "a", "docs/q/x.md"], ["approve", "--account", "a", "docs/q/x.md"]),
    (["approve", "--account", "a", "--confirm", "0123abcd", "docs/q/"], ["approve", "--account", "a", "--confirm",
                                                                         "0123abcd", "docs/q/"]),
    (["revoke", "--account", "a", "--reason", "-止める 理由", "x.md"], ["revoke", "--account", "a",
                                                                       "--reason=-止める 理由", "x.md"]),
    (["withdraw", "--undo", "--account", "a", "q/日本語.md"], ["withdraw", "--undo", "--account", "a", "q/日本語.md"]),
    (["approvals", "--json", "kopicha"], ["approvals", "--json", "kopicha"]),
])
def test_write_allowed(argv, want):
    assert sp.build_argv(argv) == want


@pytest.mark.parametrize("argv,code", [
    (["approve", "--by", "x", "--account", "a", "q.md"], "pass_unsupported"),           # 名乗りは鍵から
    (["approve", "--confirm-file", "f", "--account", "a"], "pass_unsupported"),          # 手元のファイルを取る
    (["approve", "--account", "a", "/srv/thth/accounts/a.json"], "invalid_request"),     # 絶対パス
    (["approve", "--account", "a", "../other/q.md"], "invalid_request"),                 # repo の外
    (["revoke", "--account", "a", "q/../../x.md"], "invalid_request"),
    (["withdraw", "--account", "a", "-"], "invalid_request"),                            # 標準入力
    (["withdraw", "--account", "a", "./q.md"], "invalid_request"),
    (["revoke", "--account", "a", "--reason", "1行目\n2行目", "q.md"], "invalid_request"),  # 改行
    (["approve", "--account", "a"], "invalid_request"),                                  # 原稿が無い
    (["hold", "--account", "a", "q.md"], "pass_unsupported"),                            # 審査のあと
    (["reply", "pending", "a"], "pass_unsupported"),
])
def test_write_refused(argv, code):
    with pytest.raises(sp.PassRefused, match=code):
        sp.build_argv(argv)


def test_write_needs_a_writing_key(two_workspaces):  # noqa: F811
    ctx = ReportContext({"gamma-garden": "gammaproj"}, workspace="garden", lane="gamma-desk",
                        credential_digest=DIGEST)  # writes が無い
    with pytest.raises(ReportServiceError, match="writes_not_allowed"):
        sp.execute(ctx, {"operation": "ws_command", "account": "gamma-garden",
                         "argv": ["withdraw", "--account", "gamma-garden", "docs/sns/queue"]})


@pytest.mark.parametrize("ctx_kw,account,path", [
    ({}, "alpha-main", "docs"),                                       # 鍵の口座でない
    ({"account": "alpha-main", "project": "alphaproj"}, "alpha-main", "docs"),  # 鍵に書いても括りの外
    ({}, "gamma-garden", "no/such.md"),                               # repo に無い
    ({}, "gamma-garden", "."),
])
def test_write_target_stays_inside(two_workspaces, ctx_kw, account, path):  # noqa: F811
    ctx = _writer(**ctx_kw)
    with pytest.raises(ReportServiceError, match="pass_target_invalid|invalid_request"):
        sp.execute(ctx, {"operation": "ws_command", "account": account,
                         "argv": ["withdraw", "--account", account, path]})


def test_write_needs_account_flag(two_workspaces):  # noqa: F811
    with pytest.raises(ReportServiceError, match="invalid_request"):
        sp.execute(_writer(), {"operation": "ws_command", "account": "gamma-garden",
                               "argv": ["withdraw", "docs/sns/queue/a.md"]})


@pytest.fixture
def garden_draft(two_workspaces, tmp_path):  # noqa: F811
    from tests.conftest import init_real_repo, make_account_json, write_queue_file
    root, garden = two_workspaces
    repo = init_real_repo(tmp_path, "gamma-repo")
    make_account_json(str(garden / "accounts"), "gamma-garden", repo_dir=str(repo), project="gammaproj",
                      env="$THTH_ROOT/secrets/gamma-garden.env", token="$THTH_ROOT/secrets/gamma-garden.token")
    path = write_queue_file(os.path.join(str(repo), "docs/sns/queue"), "drop.md",
                            fm_overrides={"status": "draft", "approved_sha": None, "account": "gamma-garden"},
                            body="## threads\n\ngarden の本文です。\n")
    return root, garden, repo, path


def test_withdraw_through_the_pass_records_the_lane_and_key(garden_draft, monkeypatch):
    from thth import accounts, approval_ledger
    root, garden, repo, path = garden_draft
    out = sp.execute(_writer(), {"operation": "ws_command", "account": "gamma-garden",
                                 "argv": ["withdraw", "--account", "gamma-garden", "--reason", "出さない",
                                          "--json", "docs/sns/queue/drop.md"]})
    assert out["rc"] == 0, out["stdout"] + out["stderr"]
    with accounts.use_workspace("garden"):
        entry = approval_ledger.load("gamma-garden")["docs/sns/queue/drop.md"]
    assert entry["state"] == "withdrawn"
    assert entry["fields"]["withdrawn_by"] == f"gamma（lane:gamma-desk・key:{DIGEST[:12]}）"
    assert not (root / "state" / "alpha-main" / "approvals.json").exists()


def test_write_runs_with_absolute_path_and_forced_actor(garden_draft, monkeypatch):
    root, garden, repo, path = garden_draft
    seen = {}

    class Done:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(argv, **kw):
        seen["argv"], seen["env"] = argv, kw["env"]
        return Done()

    monkeypatch.setattr(sp.subprocess, "run", fake_run)
    monkeypatch.setenv("THTH_ACTOR", "someone-else")
    sp.execute(_writer(), {"operation": "ws_command", "account": "gamma-garden",
                           "argv": ["revoke", "--account", "gamma-garden", "docs/sns/queue/drop.md"]})
    assert seen["argv"][-1] == os.path.realpath(path)
    assert seen["env"]["THTH_WS"] == "garden"
    assert seen["env"]["THTH_ACTOR"] == f"gamma（lane:gamma-desk・key:{DIGEST[:12]}）"


def test_write_only_on_the_keys_accounts(garden_draft):
    """同じ括りの口座でも、鍵に書いていない口座には書かない（読むのは括り全体・書くのは鍵の口座だけ）。"""
    from tests.conftest import make_account_json
    root, garden, repo, path = garden_draft
    make_account_json(str(garden / "accounts"), "delta-garden", repo_dir=str(repo), project="deltaproj",
                      env="$THTH_ROOT/secrets/delta-garden.env", token="$THTH_ROOT/secrets/delta-garden.token")
    with pytest.raises(ReportServiceError, match="pass_target_invalid"):
        sp.execute(_writer(), {"operation": "ws_command", "account": "gamma-garden",
                               "argv": ["withdraw", "--account", "delta-garden", "docs/sns/queue/drop.md"]})
