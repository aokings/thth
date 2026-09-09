"""`thth token set`（発注 T2b）。

Meta 管理画面の「ユーザートークン生成ツール」で発行した長期トークンを、
OAuth の往復（`thth auth`）を経ずに直接受け取って `.token` に保存する経路。

正常系・貼り付けの揺れ（`access_token=` 接頭辞・前後の空白と引用符）・
`me` の失敗・既存 `.token` の保護（`--force`）・秘密が出力に出ないことを確認する。

本物の Threads API（graph.threads.net）には一切触れない。
"""
from __future__ import annotations

import json
import os
import stat

from tests.helpers.fake_oauth_server import fake_oauth_server
from thth import core
from thth import oauth as oauth_mod


def _account_with_token_path(isolated_account_factory, tmp_path, **overrides):
    token_path = str(tmp_path / "account.token")
    account = isolated_account_factory(token=token_path, **overrides)
    account["token_path"] = token_path
    return account


def test_1_正常系は貼り付けたトークンをmeで確認してtokenを書く(tmp_path, monkeypatch, isolated_account_factory):
    account = _account_with_token_path(isolated_account_factory, tmp_path)

    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        lines = []
        rc = oauth_mod.run_token_set(
            account["name"], input_func=lambda: "PASTED-LONG-LIVED-TOKEN\n", log=lines.append)

    assert rc == 0
    with open(account["token_path"], encoding="utf-8") as f:
        token = json.load(f)
    assert token["access_token"] == "PASTED-LONG-LIVED-TOKEN"
    assert token["user_id"] == "999999"
    assert token["username"] == "nigamilab"
    assert token["expires_in"] == oauth_mod.DEFAULT_TOKEN_LIFETIME_SECONDS
    assert token["scopes"] is None
    assert "obtained_at" in token

    mode = stat.S_IMODE(os.stat(account["token_path"]).st_mode)
    assert mode == 0o600

    out = "\n".join(lines)
    assert "999999" in out and "nigamilab" in out
    assert "PASTED-LONG-LIVED-TOKEN" not in out


def test_2_access_token接頭辞つきの貼り付けも通る(tmp_path, monkeypatch, isolated_account_factory):
    account = _account_with_token_path(isolated_account_factory, tmp_path)

    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc = oauth_mod.run_token_set(
            account["name"], input_func=lambda: "access_token=PASTED-LONG-LIVED-TOKEN&foo=bar\n",
            log=lambda _l: None)

    assert rc == 0
    with open(account["token_path"], encoding="utf-8") as f:
        token = json.load(f)
    assert token["access_token"] == "PASTED-LONG-LIVED-TOKEN"


def test_3_前後の空白と引用符を落として通る(tmp_path, monkeypatch, isolated_account_factory):
    account = _account_with_token_path(isolated_account_factory, tmp_path)

    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc = oauth_mod.run_token_set(
            account["name"], input_func=lambda: '  "PASTED-LONG-LIVED-TOKEN"  \n',
            log=lambda _l: None)

    assert rc == 0
    with open(account["token_path"], encoding="utf-8") as f:
        token = json.load(f)
    assert token["access_token"] == "PASTED-LONG-LIVED-TOKEN"


def test_4_meが4xxならexit1でtokenを作らない(tmp_path, monkeypatch, isolated_account_factory):
    account = _account_with_token_path(isolated_account_factory, tmp_path)

    with fake_oauth_server({"me": "4xx"}) as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        lines = []
        rc = oauth_mod.run_token_set(
            account["name"], input_func=lambda: "BAD-TOKEN-VALUE\n", log=lines.append)

    assert rc == 1
    assert not os.path.exists(account["token_path"])
    out = "\n".join(lines)
    assert "使えませんでした" in out
    assert "BAD-TOKEN-VALUE" not in out


def test_5_既存tokenはforce無しでは拒否しforceで上書きする(tmp_path, monkeypatch, isolated_account_factory):
    account = _account_with_token_path(isolated_account_factory, tmp_path)
    with open(account["token_path"], "w", encoding="utf-8") as f:
        json.dump({"access_token": "OLD-TOKEN-VALUE"}, f)
    os.chmod(account["token_path"], 0o600)

    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        lines = []
        rc = oauth_mod.run_token_set(
            account["name"], input_func=lambda: "NEW-TOKEN-VALUE\n", log=lines.append)
        assert rc == 1
        with open(account["token_path"], encoding="utf-8") as f:
            still_old = json.load(f)
        assert still_old["access_token"] == "OLD-TOKEN-VALUE"
        assert any("--force" in line for line in lines)

        rc2 = oauth_mod.run_token_set(
            account["name"], force=True, input_func=lambda: "NEW-TOKEN-VALUE\n", log=lambda _l: None)

    assert rc2 == 0
    with open(account["token_path"], encoding="utf-8") as f:
        updated = json.load(f)
    assert updated["access_token"] == "NEW-TOKEN-VALUE"


def test_20260909_token_setが標準出力に秘密を一切出さない(tmp_path, monkeypatch, isolated_account_factory, capsys):
    """事故防止テスト: 仕込んだトークン文字列が標準出力・ログのどこにも現れないこと。"""
    secret_token = "SUPER-SECRET-PASTED-TOKEN-9999"
    account = _account_with_token_path(isolated_account_factory, tmp_path)

    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        lines = []
        rc = oauth_mod.run_token_set(
            account["name"], input_func=lambda: secret_token + "\n", log=lines.append)

    assert rc == 0
    out = "\n".join(lines)
    assert secret_token not in out

    capsys.readouterr()  # 直前の出力を捨てる
    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        token_path2 = str(tmp_path / "account2.token")
        account2 = isolated_account_factory("nigamilab-threads-2", token=token_path2,
                                             repo_dir=account["repo_dir"])
        oauth_mod.run_token_set(account2["name"], input_func=lambda: secret_token + "\n")  # log=print(既定)
    captured = capsys.readouterr()
    assert secret_token not in captured.out
    assert secret_token not in captured.err


def test_6_coreはtokenのuser_idを台帳より優先する(tmp_path, monkeypatch, isolated_account_factory):
    """統括の検収 T2a 指摘（`docs/検収_T2a_2026-09-09.md`）: `.token` の `user_id` を
    優先し、台帳側は空でも動くことを確認する。"""
    from thth import accounts as accounts_mod

    account = _account_with_token_path(isolated_account_factory, tmp_path, user_id="")
    with open(account["token_path"], "w", encoding="utf-8") as f:
        json.dump({"access_token": "SOME-TOKEN", "user_id": "888888"}, f)
    os.chmod(account["token_path"], 0o600)

    account_cfg = accounts_mod.load_account(account["name"])
    token = accounts_mod.load_token(account_cfg)
    adapter = core._default_adapter_factory(account_cfg, token)

    assert adapter.user_id == "888888"


def test_7_CLI経由でパイプで渡しても動く_stdinフラグ(tmp_path, monkeypatch, isolated_account_factory):
    """`echo TOKEN | bin/thth token set <account> --stdin` を subprocess で呼ぶ
    （`run_thth` は標準入力を渡せないので、ここだけ直接 subprocess.run を使う）。"""
    import subprocess
    import sys

    account = _account_with_token_path(isolated_account_factory, tmp_path)
    secret_token = "PIPED-SECRET-TOKEN-VALUE"
    bin_thth = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "thth")

    with fake_oauth_server() as base_url:
        full_env = dict(os.environ)  # isolated_account_factory が THTH_APP_DIR 等を既に setenv 済み
        full_env["THTH_THREADS_BASE_URL"] = base_url
        proc = subprocess.run(
            [sys.executable, bin_thth, "token", "set", account["name"], "--stdin"],
            input=secret_token + "\n", capture_output=True, text=True, env=full_env,
        )

    assert proc.returncode == 0, proc.stderr
    assert secret_token not in proc.stdout
    assert secret_token not in proc.stderr
    assert os.path.exists(account["token_path"])
    with open(account["token_path"], encoding="utf-8") as f:
        token = json.load(f)
    assert token["access_token"] == secret_token


def test_20260909_handle_mismatch_would_post_as_the_wrong_account(
        tmp_path, monkeypatch, isolated_account_factory):
    """台帳の handle と食い違うトークンは保存しない（masaru の指摘 2026-09-09）。

    Meta 側にも「選択中のテスタープロフィールと一致しません」という検査があるが、
    それが見るのは「管理画面で押した行」と「ブラウザでログイン中のアカウント」の
    一致だけ。**正しく発行したトークンを別のアカウントの枠に貼る**取り違え
    （nigamilab のトークンを kopicha-threads に入れる等）は通ってしまう。
    通すとそのアカウントの queue の本文が別のアカウントから出る（取り消せない）。
    """
    # 偽 me は username=nigamilab を返す。台帳の handle をわざと別にする。
    account = _account_with_token_path(
        isolated_account_factory, tmp_path, handle="kopi_chaba")

    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        lines = []
        rc = oauth_mod.run_token_set(
            account["name"], input_func=lambda: "PASTED-TOKEN", log=lines.append)

    assert rc == 1
    assert not os.path.exists(account["token_path"]), "食い違ったのに .token を書いてしまった"
    out = "\n".join(lines)
    assert "kopi_chaba" in out and "nigamilab" in out
    assert "PASTED-TOKEN" not in out


def test_20260909_handle_が一致すれば通る(tmp_path, monkeypatch, isolated_account_factory):
    account = _account_with_token_path(
        isolated_account_factory, tmp_path, handle="nigamilab")
    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc = oauth_mod.run_token_set(
            account["name"], input_func=lambda: "PASTED-TOKEN", log=lambda *_: None)
    assert rc == 0
    assert os.path.exists(account["token_path"])
