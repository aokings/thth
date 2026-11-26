"""括りの段 1 の柵（主役）: 括り A の中で命令を打つと、括り B の置き場を 1 つも開かない。

設計: docs/設計_THTHアカウント（括り）_叩き台_2026-11-26.md §6.1・§6.5。
Python の監査の口（sys.addaudithook）で、命令の間に開いた・並べた道を全部拾う。
- main（根）の口座 `alpha-main` と、括り garden の口座 `gamma-garden` を置く。
- garden の中で打つ: 根の accounts/・state/<alpha-main>/ を開かない・出力に alpha-main が出ない。
- main の中で打つ: ws/garden/ の下を開かない・出力に gamma-garden が出ない。
"""
from __future__ import annotations

import os
import sys

import pytest

from tests.conftest import make_account_json

_SEEN: list[str] = []
_ON = [False]


def _hook(event, args):
    if not _ON[0]:
        return
    if event in ("open", "os.listdir", "os.scandir") and args:
        path = args[0]
        if isinstance(path, (str, bytes, os.PathLike)):
            try:
                _SEEN.append(os.fsdecode(os.fspath(path)))
            except TypeError:
                pass


sys.addaudithook(_hook)

# 括りの全部の口座を歩く命令（どれも括りの中を実際に読むことを test_commands_read_inside で確かめる）。
COMMANDS = [
    ["account"],
    ["board", "--json"],
    ["schedule"],
    ["queue"],
]
# 口座を名指しする命令。ほかの括りの口座を名指ししたら「無い名前」と同じ答えになる。
NAMED = [
    ["doctor", "{acct}"],
    ["approvals", "{acct}"],
    ["queue", "{acct}"],
    ["posts", "{acct}"],
    ["schedule", "{acct}"],
]


@pytest.fixture
def two_workspaces(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    monkeypatch.setenv("THTH_ROOT", str(root))
    monkeypatch.delenv("THTH_WS", raising=False)
    monkeypatch.delenv("THTH_ACCOUNTS_DIR", raising=False)
    import thth.accounts as accounts_mod
    monkeypatch.setattr(accounts_mod, "APP_DIR", str(tmp_path / "app"))
    monkeypatch.setenv("THTH_APP_DIR", str(tmp_path / "app"))
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    (root / "accounts").mkdir()
    make_account_json(str(root / "accounts"), "alpha-main", repo_dir=str(repo_a), project="alphaproj")
    from thth import ws_admin
    ws_admin.add("garden", lead="gamma")
    garden = root / "ws" / "garden"
    (garden / "accounts").mkdir()
    make_account_json(str(garden / "accounts"), "gamma-garden", repo_dir=str(repo_b), project="gammaproj",
                      env="$THTH_ROOT/secrets/gamma-garden.env", token="$THTH_ROOT/secrets/gamma-garden.token")
    for name, base in (("alpha-main", root), ("gamma-garden", garden)):
        (base / "state" / name).mkdir(parents=True)
    return root, garden


def _run(argv, capsys):
    from thth import cli
    _SEEN.clear()
    _ON[0] = True
    try:
        try:
            cli.main(argv)
        except SystemExit:
            pass
        except Exception:  # 命令が断っても、何を開いたかは見る
            pass
    finally:
        _ON[0] = False
    out = capsys.readouterr()
    return list(_SEEN), out.out + out.err


def _under(path, base):
    path = os.path.realpath(path)
    base = os.path.realpath(base)
    return path == base or path.startswith(base + os.sep)


@pytest.mark.parametrize("argv", COMMANDS, ids=[" ".join(c) for c in COMMANDS])
def test_garden_does_not_touch_main(argv, two_workspaces, monkeypatch, capsys):
    root, garden = two_workspaces
    monkeypatch.setenv("THTH_WS", "garden")
    seen, text = _run(argv, capsys)
    leaked = [p for p in seen if _under(p, root / "accounts") or _under(p, root / "state" / "alpha-main")
              or "alpha-main" in p or _under(p, root / "repos")]
    assert not leaked, f"{argv}: garden の中から main の置き場を開いた: {leaked[:5]}"
    assert "alpha-main" not in text and "alphaproj" not in text, argv


@pytest.mark.parametrize("argv", COMMANDS, ids=[" ".join(c) for c in COMMANDS])
def test_main_does_not_touch_garden(argv, two_workspaces, capsys):
    root, garden = two_workspaces
    seen, text = _run(argv, capsys)
    leaked = [p for p in seen if _under(p, root / "ws") or "gamma-garden" in p]
    assert not leaked, f"{argv}: main の中から garden の置き場を開いた: {leaked[:5]}"
    assert "gamma-garden" not in text and "gammaproj" not in text, argv


def test_garden_sees_its_own_account(two_workspaces, monkeypatch, capsys):
    monkeypatch.setenv("THTH_WS", "garden")
    seen, text = _run(["account"], capsys)
    assert "gamma-garden" in text


def test_unknown_workspace_is_refused(two_workspaces, monkeypatch):
    from thth import accounts
    monkeypatch.setenv("THTH_WS", "nowhere")
    with pytest.raises(accounts.WorkspaceError):
        accounts.ws_root()


def test_workspace_of_account(two_workspaces):
    from thth import accounts
    assert accounts.workspace_of_account("alpha-main") == "main"
    assert accounts.workspace_of_account("gamma-garden") == "garden"
    assert accounts.workspace_of_account("none") is None


@pytest.mark.parametrize("argv", COMMANDS, ids=[" ".join(c) for c in COMMANDS])
def test_commands_read_inside(argv, two_workspaces, monkeypatch, capsys):
    # 柵が空回りしていない（命令が早く落ちて何も開いていない、でない）ことを見る。
    root, garden = two_workspaces
    monkeypatch.setenv("THTH_WS", "garden")
    seen, text = _run(argv, capsys)
    assert any(_under(p, garden) for p in seen), argv


def _named(argv, name):
    return [a.replace("{acct}", name) for a in argv]


@pytest.mark.parametrize("argv", NAMED, ids=[" ".join(c) for c in NAMED])
def test_naming_other_workspace_account_is_like_unknown(argv, two_workspaces, monkeypatch, capsys):
    root, garden = two_workspaces
    monkeypatch.setenv("THTH_WS", "garden")
    seen, other = _run(_named(argv, "alpha-main"), capsys)
    leaked = [p for p in seen if _under(p, root / "accounts") or _under(p, root / "state" / "alpha-main")]
    assert not leaked, leaked[:5]
    _, unknown = _run(_named(argv, "zeta-none"), capsys)
    assert other.replace("alpha-main", "X") == unknown.replace("zeta-none", "X"), (other, unknown)
    assert "alphaproj" not in other
    # 自分の括りの口座は読める（柵の空回りでない）
    seen, own = _run(_named(argv, "gamma-garden"), capsys)
    assert any(_under(p, garden) for p in seen), argv


@pytest.mark.parametrize("argv", NAMED, ids=[" ".join(c) for c in NAMED])
def test_main_naming_garden_account_is_like_unknown(argv, two_workspaces, capsys):
    root, garden = two_workspaces
    seen, other = _run(_named(argv, "gamma-garden"), capsys)
    assert not [p for p in seen if _under(p, root / "ws")]
    _, unknown = _run(_named(argv, "zeta-none"), capsys)
    assert other.replace("gamma-garden", "X") == unknown.replace("zeta-none", "X"), (other, unknown)
