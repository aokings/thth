"""段 6（設計 §8 R11）: 名指しの印 `ws/.name-required` のある置き場では、括りを名指ししない打ち方を断る。

- 印が無ければ今までどおり（名指し無しは main）。
- 印があっても、`THTH_WS` があれば通る。常駐と管理者の道具（`cli.NO_NAME_COMMANDS`）と括りなしの命令は通る。
- timer の単位は main の口座でも `THTH_WS` を付ける（印を置いても timer が止まらない）。
- 柵: systemd の単位が打つ命令は、どれも印のある置き場で断られない。
"""
from __future__ import annotations

import json
import re

import pytest

from thth import accounts, cli, command_scope, refusal_catalog, systemd_gen


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("THTH_ROOT", str(tmp_path))
    monkeypatch.delenv(accounts.WS_ENV, raising=False)
    monkeypatch.delenv(cli.FROM_ACCOUNT_ENV, raising=False)
    (tmp_path / "accounts").mkdir()
    return tmp_path


def _mark(root):
    (root / "ws").mkdir(exist_ok=True)
    (root / "ws" / accounts.NAME_REQUIRED_MARK).write_text("")


def test_印が無ければ名指し無しでも通る(root):
    assert accounts.workspace_name_required() is False
    assert cli._require_workspace_name(["board"]) is None
    assert accounts.current_workspace() == accounts.MAIN_WS


def test_印があれば括りの中の命令を名指し無しでは断る(root, capsys):
    _mark(root)
    assert accounts.workspace_name_required() is True
    assert cli._require_workspace_name(["board", "kopicha-threads"]) == 2
    err = capsys.readouterr().err
    assert "workspace_name_required" in err and "THTH_WS=main" in err
    # 管理者だけの札の命令も、括りの中で動く物は名指しが要る（黙って main に倒さない）。
    assert cli._require_workspace_name(["refresh", "kopicha-threads"]) == 2
    assert cli._require_workspace_name(["account", "add", "x"]) == 2


def test_印があっても名指しがあれば通る(root, monkeypatch):
    _mark(root)
    monkeypatch.setenv(accounts.WS_ENV, "main")
    assert cli._require_workspace_name(["board"]) is None


def test_json_の断りは機械が読める形(root, capsys):
    _mark(root)
    assert cli._require_workspace_name(["board", "--json"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "workspace_name_required" and out["next"]


@pytest.mark.parametrize("argv", [
    ["maintain"], ["worker", "--credentials", "x"], ["approval-worker"], ["admin", "ws", "list"],
    ["admin", "meta", "keepalive"], ["systemd", "--maintain"], ["login", "--lane", "x"], ["lint", "f"],
    [], ["--version"], ["no-such-command"],
])
def test_常駐と管理者の道具と括りなしは名指しが要らない(root, argv):
    _mark(root)
    assert cli._require_workspace_name(argv) is None


def test_名指し無しで通す命令は管理者の札か括りなしだけ():
    for name in cli.NO_NAME_COMMANDS:
        assert command_scope.scope_of(name) == command_scope.ADMIN, name


def test_timer_は_main_の口座でも名指しを付ける(root, monkeypatch):
    monkeypatch.setenv(cli.FROM_ACCOUNT_ENV, "1")
    assert cli._enter_account_workspace(["run", "nowhere-account"]) is None
    import os
    assert os.environ[accounts.WS_ENV] == accounts.MAIN_WS


def test_断りの文は目録にある():
    assert "workspace_name_required" in refusal_catalog.fix_view("ws_admin.NEXT")


def _units():
    """systemd の単位（雛形と、作り手が出す物）の ExecStart と Environment。"""
    from pathlib import Path
    texts = [Path(__file__).resolve().parents[1].joinpath("systemd", "thth@.service").read_text(),
             systemd_gen.render_collect_service(), systemd_gen.render_maintain_service(),
             systemd_gen.render_meta_keepalive_service(), systemd_gen.render_map_service(),
             systemd_gen.render_worker_service("/srv/thth/private/report-credentials.json")]
    for text in texts:
        exec_line = re.search(r"^ExecStart=(\S+)(.*)$", text, re.M)
        yield text, exec_line.group(1), exec_line.group(2).split()


def test_柵_単位の命令は印のある置き場で断られない(root, monkeypatch):
    """thth@（thth-run）・thth-collect@・thth-map@ は印 THTH_WS_FROM_ACCOUNT で名指しを付け、ほかは通す命令。"""
    _mark(root)
    seen = 0
    for text, exe, args in _units():
        monkeypatch.delenv(accounts.WS_ENV, raising=False)
        argv = [a.replace("%i", "some-account") for a in args]
        if exe.endswith("/thth-run"):
            argv = ["run", *argv]  # bin/thth-run は印を付けて `thth run` を打つ
            monkeypatch.setenv(cli.FROM_ACCOUNT_ENV, "1")
        elif "Environment=THTH_WS_FROM_ACCOUNT=1" in text:
            monkeypatch.setenv(cli.FROM_ACCOUNT_ENV, "1")
        else:
            monkeypatch.delenv(cli.FROM_ACCOUNT_ENV, raising=False)
        assert cli._enter_account_workspace(argv) is None, text
        assert cli._require_workspace_name(argv) is None, text
        seen += 1
    assert seen == 6


def test_thth_run_は印を付ける():
    from pathlib import Path
    text = Path(__file__).resolve().parents[1].joinpath("bin", "thth-run").read_text()
    assert "export THTH_WS_FROM_ACCOUNT=1" in text


def test_入口の命令から断る(root, capsys):
    """`thth` の入口（`cli._entry`）が、命令を読む前に断る。"""
    _mark(root)
    assert cli._entry(["board"]) == 2
    assert "workspace_name_required" in capsys.readouterr().err
