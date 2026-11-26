"""括りの段 4 の準備（新しい括りを開く前に、本番を変えずに置ける物）の柵。

設計: docs/設計_THTHアカウント（括り）_叩き台_2026-11-26.md §3 の段 4・段 5 の注。
- main でない括りの台帳は、秘密（env・token・consumer）を括りの `secrets/` の外に置けない（`~` も断る）。
  新しく書く台帳は、main なら今までどおり `~/.config/thth/`、ほかの括りなら `$THTH_ROOT/secrets/`。
- timer の単位（`thth@<口座>`。印は `bin/thth-run` が付ける・単位のファイルは配った姿のまま）は、
  印 `THTH_WS_FROM_ACCOUNT=1` があるときだけ
  口座の台帳のある括りに入る。印の無い打ち方は名前で別の括りへ入らない。
"""
from __future__ import annotations

import json
import os

import pytest

from tests.conftest import make_account_json, run_thth
from tests.test_ws_isolation import two_workspaces  # noqa: F401
from thth import accounts


def _garden_account(garden, **kw):
    kw.setdefault("env", "$THTH_ROOT/secrets/gamma-garden.env")
    kw.setdefault("token", "$THTH_ROOT/secrets/gamma-garden.token")
    make_account_json(str(garden / "accounts"), "gamma-garden", repo_dir=str(garden / "repo"),
                      project="gammaproj", **kw)


@pytest.mark.parametrize("token", [
    "~/.config/thth/gamma-garden.token",          # 今の main の置き方
    "$THTH_ROOT/../../accounts/x.token",           # 括りの外へ出る
    "/etc/thth/gamma-garden.token",                # 絶対の道で外
    "$THTH_ROOT/secrets",                          # secrets そのもの
    "secrets/gamma-garden.token",                  # 相対
])
def test_garden_refuses_secrets_outside(two_workspaces, token):  # noqa: F811
    root, garden = two_workspaces
    _garden_account(garden, token=token)
    with accounts.use_workspace("garden"):
        with pytest.raises(accounts.AccountError, match=accounts.SECRET_OUTSIDE):
            accounts.load_account("gamma-garden")


def test_garden_refuses_env_outside(two_workspaces):  # noqa: F811
    root, garden = two_workspaces
    _garden_account(garden, env=str(root / "secrets" / "alpha-main.env"))  # 共通の根（main）の秘密
    with accounts.use_workspace("garden"):
        with pytest.raises(accounts.AccountError, match=accounts.SECRET_OUTSIDE):
            accounts.load_account("gamma-garden")


def test_garden_reads_secrets_inside(two_workspaces):  # noqa: F811
    root, garden = two_workspaces
    _garden_account(garden, env="$THTH_ROOT/secrets/gamma-garden.env", token="$THTH_ROOT/secrets/gamma-garden.token")
    with accounts.use_workspace("garden"):
        cfg = accounts.load_account("gamma-garden")
    assert cfg["token"] == str(garden / "secrets" / "gamma-garden.token")


def test_main_keeps_home_secrets(two_workspaces):  # noqa: F811
    root, garden = two_workspaces
    make_account_json(str(root / "accounts"), "alpha-main", repo_dir=str(root / "repo"), project="alphaproj",
                      token="~/.config/thth/alpha-main.token")
    cfg = accounts.load_account("alpha-main")
    assert cfg["token"] == os.path.expanduser("~/.config/thth/alpha-main.token")


def test_secret_ref_by_workspace(two_workspaces):  # noqa: F811
    assert accounts.secret_ref("a", ".token") == "~/.config/thth/a.token"
    with accounts.use_workspace("garden"):
        assert accounts.secret_ref("a", ".token") == "$THTH_ROOT/secrets/a.token"
        assert not accounts.secret_path_problem(accounts.secret_ref("a", ".env"))


def test_principal_in_garden(two_workspaces):  # noqa: F811
    from thth import principal_cli, principals
    with accounts.use_workspace("garden"):
        data = principal_cli.build_ledger("gamma-writer", hatena_id="gammawriter", blogs=[])
        assert data["token"] == "$THTH_ROOT/secrets/gamma-writer.token"
        assert data["consumer"] == "$THTH_ROOT/secrets/gamma-writer.consumer"
        path = principals.path_for("gamma-writer")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data["token"] = "~/.config/thth/gamma-writer.token"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        with pytest.raises(principals.PrincipalError, match=accounts.SECRET_OUTSIDE):
            principals.load_principal("gamma-writer")


# ---- timer の単位が口座の括りに入る ---------------------------------------------------------------

@pytest.fixture(autouse=True)
def _forget_entered_workspace():
    """`_enter_account_workspace` は os.environ に直に書く（打つ命令の間ずっと効かせるため）。試験の外へ漏らさない。"""
    before = os.environ.get("THTH_WS")
    yield
    if before is None:
        os.environ.pop("THTH_WS", None)
    else:
        os.environ["THTH_WS"] = before


@pytest.mark.parametrize("argv,mark,want", [
    (["run", "gamma-garden"], "1", "garden"),
    (["collect", "gamma-garden"], "1", "garden"),
    (["run", "alpha-main"], "1", None),            # main の口座は今までどおり（THTH_WS を書かない）
    (["run", "gamma-garden"], None, None),         # 印が無ければ名前で入らない
    (["board"], "1", None),                        # timer の命令でない
])
def test_enter_account_workspace(two_workspaces, monkeypatch, argv, mark, want):  # noqa: F811
    from thth import cli
    monkeypatch.delenv("THTH_WS", raising=False)
    if mark:
        monkeypatch.setenv(cli.FROM_ACCOUNT_ENV, mark)
    else:
        monkeypatch.delenv(cli.FROM_ACCOUNT_ENV, raising=False)
    assert cli._enter_account_workspace(argv) is None
    assert os.environ.get("THTH_WS") == want


def test_explicit_workspace_wins(two_workspaces, monkeypatch):  # noqa: F811
    from thth import cli
    monkeypatch.setenv("THTH_WS", "main")
    monkeypatch.setenv(cli.FROM_ACCOUNT_ENV, "1")
    cli._enter_account_workspace(["run", "gamma-garden"])
    assert os.environ["THTH_WS"] == "main"


def test_same_name_in_two_workspaces_is_refused(two_workspaces, monkeypatch, capsys):  # noqa: F811
    from thth import cli
    root, garden = two_workspaces
    make_account_json(str(garden / "accounts"), "alpha-main", repo_dir=str(garden / "r"), project="alphaproj")
    monkeypatch.delenv("THTH_WS", raising=False)
    monkeypatch.setenv(cli.FROM_ACCOUNT_ENV, "1")
    assert cli._enter_account_workspace(["run", "alpha-main"]) == 2
    assert "account_in_two_workspaces" in capsys.readouterr().err
    assert "THTH_WS" not in os.environ


def test_timer_run_reaches_the_garden_account(two_workspaces):  # noqa: F811
    """印つきの `thth run <garden の口座>` は garden の台帳を読む（印が無ければ main で探して見つからない）。"""
    root, garden = two_workspaces
    env = {k: v for k, v in os.environ.items() if k != "THTH_WS"}
    with_mark = run_thth(["run", "gamma-garden"], env={**env, "THTH_WS_FROM_ACCOUNT": "1"})
    without = run_thth(["run", "gamma-garden"], env=env)
    # 印つき: garden の台帳を読み、token が無いので投げずに止まる（台帳は見つかっている）。
    assert "token が無いので実行しません: gamma-garden" in with_mark.stderr, with_mark.stderr
    assert "台帳が無い" not in with_mark.stderr
    # 印なし: main で探して見つからない（名前で別の括りへ入らない）。
    assert "台帳が無い: gamma-garden" in without.stderr, without.stderr
    assert str(root / "accounts" / "gamma-garden.json") in without.stderr

def test_thth_run_carries_the_mark():
    """`thth@<口座>` は `bin/thth-run` を呼ぶ。単位のファイルは配った姿のまま（c812 の基準）で、印はラッパが付ける。
    `thth-collect@<口座>` の単位に印を足すのは段 4（単位を置き直す日）。それまで collect は main の口座だけ。"""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "bin", "thth-run"), encoding="utf-8") as f:
        assert "export THTH_WS_FROM_ACCOUNT=1" in f.read()


def test_tilde_is_refused_even_if_home_is_inside(two_workspaces, monkeypatch):  # noqa: F811
    """`~` は打つ人の HOME で意味が変わる。たまたま括りの中を指していても、`~` の綴りの台帳は読まない。"""
    root, garden = two_workspaces
    monkeypatch.setenv("HOME", str(garden / "secrets"))
    _garden_account(garden, token="~/gamma-garden.token")
    with accounts.use_workspace("garden"):
        with pytest.raises(accounts.AccountError, match=accounts.SECRET_OUTSIDE):
            accounts.load_account("gamma-garden")


# ---- 口座の名前は全部の括りを通して 1 つ（`thth account add`） --------------------------------------

def test_account_add_refuses_a_name_in_another_workspace(two_workspaces):  # noqa: F811
    root, garden = two_workspaces
    env = {k: v for k, v in os.environ.items() if k != "THTH_WS"}
    for ws, name in (("main", "gamma-garden"), ("garden", "alpha-main")):
        r = run_thth(["account", "add", name, "--media", "threads", "--project", "tryit", "--by", "test", "--force"],
                     env={**env, "THTH_WS": ws})
        assert r.returncode == 2, r.stdout + r.stderr
        assert "account_name_in_other_workspace" in r.stdout + r.stderr
    assert not (root / "accounts" / "gamma-garden.json").exists()
    assert not (garden / "accounts" / "alpha-main.json").exists()


def test_account_add_in_garden_writes_secrets_inside(two_workspaces):  # noqa: F811
    root, garden = two_workspaces
    env = {k: v for k, v in os.environ.items() if k != "THTH_WS"}
    r = run_thth(["account", "add", "delta-garden", "--media", "threads", "--project", "tryit", "--by", "test"],
                 env={**env, "THTH_WS": "garden"})
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads((garden / "accounts" / "delta-garden.json").read_text())
    assert data["token"] == "$THTH_ROOT/secrets/delta-garden.token"
    assert data["env"] == "$THTH_ROOT/secrets/delta-garden.env"
