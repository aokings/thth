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
    (["hold", "--account", "a", "-"], "invalid_request"),                                # 標準入力
    (["hold", "--by", "x", "--account", "a", "q.md"], "pass_unsupported"),
    (["hold", "--account", "a", "--until", "2026/12/01", "q.md"], "invalid_request"),     # 値に `/`
    (["hold", "--account", "a", "--confirm", "x", "q.md"], "pass_unsupported"),           # hold に二段は無い
    (["reply", "pending", "a"], "invalid_request"),                                      # id が無い
    (["reply", "hide", "a", "1"], "pass_unsupported"),                                   # 表に無い小さな命令
    (["reply", "unhide", "a", "1"], "pass_unsupported"),
    (["reply", "delete", "a", "1", "--confirm", "x"], "pass_unsupported"),
    (["reply", "--json", "approve", "a", "1"], "pass_unsupported"),                      # 小さな命令は頭に
    (["reply"], "pass_unsupported"),
    (["reply", "approve", "a", "1", "--by", "x"], "pass_unsupported"),                   # 名乗りは鍵から
    (["reply", "approve", "a", "1", "--confirm", "x"], "pass_unsupported"),              # 1 段の命令
    (["reply", "ignore", "a", "1", "--status", "all"], "pass_unsupported"),              # pending の旗
    (["reply", "pending", "a", "1", "--status", "hidden"], "invalid_request"),
    (["reply", "approve", "a", "12x"], "invalid_request"),                               # id は数字
    (["reply", "approve", "a", "1", "2"], "invalid_request"),
    (["reply", "approve", "../a", "1"], "invalid_request"),
    (["reply", "approve", "-a", "1"], "invalid_request"),
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


# ---- 書く命令の残り（第4）: hold・reply pending／approve／ignore --------------------------------------

@pytest.mark.parametrize("argv,want", [
    (["hold", "--account", "a", "--until", "2026-12-01", "--reason", "試しで止める", "docs/q/"],
     ["hold", "--account", "a", "--until", "2026-12-01", "--reason=試しで止める", "docs/q/"]),
    (["hold", "--undo", "--account", "a", "--json", "q/x.md"], ["hold", "--undo", "--account", "a", "--json", "q/x.md"]),
    (["reply", "pending", "a", "1789", "--status", "all", "--json"],
     ["reply", "--status", "all", "--json", "pending", "a", "1789"]),
    (["reply", "approve", "a", "1789", "--reason", "身内"], ["reply", "--reason=身内", "approve", "a", "1789"]),
    (["reply", "ignore", "a", "1789", "--wait", "5"], ["reply", "--wait", "5", "ignore", "a", "1789"]),
])
def test_hold_and_reply_allowed(argv, want):
    assert sp.build_argv(argv) == want


@pytest.mark.parametrize("argv", [
    ["hold", "--account", "a", "--until", "2026-12-01", "--reason", "試し", "q/x.md"],
    ["reply", "pending", "a", "1789", "--status", "all", "--json"],
    ["reply", "approve", "a", "1789", "--reason", "身内"],
    ["reply", "ignore", "a", "1789"],
])
def test_built_argv_is_what_the_cli_parses(argv):
    """VM が打つ argv（旗が小さな命令より前に来る形）を、本物の CLI の parser がそのまま受ける。"""
    from thth import cli
    built = sp.build_argv(argv)
    args = cli.build_parser().parse_args(built)
    if argv[0] == "reply":
        assert (args.action, args.account, args.comment_id) == tuple(argv[1:4])
        assert args.by is None and args.confirm is None
    else:
        assert args.file == ["q/x.md"] and args.until == "2026-12-01" and args.by is None


def test_pass_commands_cover_reply_for_the_mac():
    assert "reply" in sp.PASS_COMMANDS and "hold" in sp.PASS_COMMANDS
    assert set(sp.ALL) <= sp.PASS_COMMANDS


@pytest.mark.parametrize("argv", [
    ["hold", "--account", "gamma-garden", "--until", "2099-12-01", "--reason", "試し", "docs/sns/queue/drop.md"],
    ["reply", "approve", "gamma-garden", "1789"],
    ["reply", "ignore", "gamma-garden", "1789"],
])
def test_hold_and_reply_need_a_writing_key(two_workspaces, monkeypatch, argv):  # noqa: F811
    """読むだけの鍵（`writes` が無い）は、打つ前に断る。"""
    monkeypatch.setattr(sp.subprocess, "run", lambda *a, **k: pytest.fail("読むだけの鍵で打った"))
    ctx = _writer(writes=False)
    with pytest.raises(ReportServiceError, match="writes_not_allowed"):
        sp.execute(ctx, {"operation": "ws_command", "account": "gamma-garden", "argv": argv})


class _Done:
    returncode, stdout, stderr = 0, "", ""


def _capture(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"], seen["env"] = argv, kw["env"]
        return _Done()

    monkeypatch.setattr(sp.subprocess, "run", fake_run)
    return seen


def test_reply_pending_is_read_with_a_read_only_key(two_workspaces, monkeypatch):  # noqa: F811
    seen = _capture(monkeypatch)
    out = sp.execute(_writer(writes=False), {"operation": "ws_command", "account": "gamma-garden",
                                             "argv": ["reply", "pending", "gamma-garden", "1789", "--json"]})
    assert out["rc"] == 0 and out["workspace"] == "garden"
    assert seen["argv"][-5:] == ["reply", "--json", "pending", "gamma-garden", "1789"]
    assert seen["env"]["THTH_WS"] == "garden" and seen["env"]["THTH_ACTOR"] == "lane:gamma-desk"


@pytest.mark.parametrize("argv", [
    ["reply", "pending", "alpha-main", "1789"],
    ["reply", "approve", "alpha-main", "1789"],
    ["reply", "ignore", "alpha-main", "1789"],
    ["hold", "--account", "alpha-main", "--until", "2099-12-01", "--reason", "試し", "docs"],
])
@pytest.mark.parametrize("ctx_kw", [
    {},                                                    # 鍵の口座でない
    {"account": "alpha-main", "project": "alphaproj"},     # 鍵に書いても、ほかの括り（main）の口座
])
def test_hold_and_reply_stay_on_the_keys_workspace(two_workspaces, monkeypatch, argv, ctx_kw):  # noqa: F811
    monkeypatch.setattr(sp.subprocess, "run", lambda *a, **k: pytest.fail("ほかの括りの口座で打った"))
    with pytest.raises(ReportServiceError, match="pass_target_invalid"):
        sp.execute(_writer(**ctx_kw), {"operation": "ws_command", "account": "gamma-garden", "argv": argv})


def test_reply_approve_runs_with_the_forced_actor(two_workspaces, monkeypatch):  # noqa: F811
    seen = _capture(monkeypatch)
    monkeypatch.setenv("THTH_ACTOR", "someone-else")
    sp.execute(_writer(), {"operation": "ws_command", "account": "gamma-garden",
                           "argv": ["reply", "approve", "gamma-garden", "1789", "--reason", "身内の返信"]})
    assert seen["argv"][-5:] == ["reply", "--reason=身内の返信", "approve", "gamma-garden", "1789"]
    assert seen["env"]["THTH_WS"] == "garden"
    assert seen["env"]["THTH_ACTOR"] == f"gamma（lane:gamma-desk・key:{DIGEST[:12]}）"


def test_hold_through_the_pass_records_the_lane_and_key(garden_draft):
    """garden の中で hold を実際に打ち、止めの名乗りに鍵の持ち主・レーン・鍵の id が残る（main は触らない）。"""
    import datetime
    from thth import accounts, approval_ledger
    root, garden, repo, path = garden_draft
    # 打つのは別の process（試験の時計の差し替えが効かない）なので、本物の今日から数える。
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    until = (today + datetime.timedelta(days=10)).strftime("%Y-%m-%d")
    out = sp.execute(_writer(), {"operation": "ws_command", "account": "gamma-garden",
                                 "argv": ["hold", "--account", "gamma-garden", "--until", until,
                                          "--reason", "試しで止める", "--json", "docs/sns/queue/drop.md"]})
    assert out["rc"] == 0, out["stdout"] + out["stderr"]
    with accounts.use_workspace("garden"):
        row = approval_ledger.load_holds("gamma-garden")["docs/sns/queue/drop.md"]
        assert approval_ledger.holds_path_for("gamma-garden").startswith(str(garden))
    assert row["by"] == f"gamma（lane:gamma-desk・key:{DIGEST[:12]}）", row
    assert row["reason"] == "試しで止める" and row["until"] == until
    assert not (root / "state" / "alpha-main" / "holds.json").exists()


@pytest.mark.parametrize("action", ["pending", "approve", "ignore"])
def test_reply_only_on_the_keys_accounts(garden_draft, monkeypatch, action):
    """同じ括りの口座でも、鍵に書いていない口座の返信は読まない・書かない。"""
    from tests.conftest import make_account_json
    root, garden, repo, path = garden_draft
    make_account_json(str(garden / "accounts"), "delta-garden", repo_dir=str(repo), project="deltaproj",
                      env="$THTH_ROOT/secrets/delta-garden.env", token="$THTH_ROOT/secrets/delta-garden.token")
    monkeypatch.setattr(sp.subprocess, "run", lambda *a, **k: pytest.fail("鍵の口座でない口座で打った"))
    with pytest.raises(ReportServiceError, match="pass_target_invalid"):
        sp.execute(_writer(), {"operation": "ws_command", "account": "gamma-garden",
                               "argv": ["reply", action, "delta-garden", "1789"]})
