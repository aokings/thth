"""env・token の事前確認（設計 §3.2・T3a 訂正 2026-09-09）。

以前は env ファイルと token の両方が無ければ `thth run` は exit 2 だった。
**設計が変わって env ファイルは要らなくなった**（アプリ ID・シークレットは Meta
管理画面のトークン生成ツールで発行する運用になり、実際 VM には `<account>.token`
しか無い・`thth.accounts.token_exists()` の docstring 参照）。ここでは
「token だけあれば進む」「token が無ければ exit 2 のまま」の両方を固定する。
"""
from __future__ import annotations

import json
import os

from tests.conftest import run_thth, write_queue_file


def test_env_tokenが無ければrunはexit2で何も投げない(isolated_account):
    write_queue_file(isolated_account["queue_dir"], "a.md")
    # isolated_account の台帳は env・token を存在しないパスにしてある（conftest 既定）。
    result = run_thth(["run", isolated_account["name"]])
    assert result.returncode == 2

    # queue ファイルの front-matter が書き換わっていない（何も投げていない）ことを確認する。
    path = isolated_account["queue_dir"] + "/a.md"
    with open(path, encoding="utf-8") as f:
        text = f.read()
    assert "status: approved" in text
    assert "status: posted" not in text
    for line in text.split("\n"):
        if line.startswith("post_id:"):
            assert line.strip() == "post_id:"


def test_tokenだけあればenvが無くてもrunはexit2で止まらない(isolated_account_factory, tmp_path):
    """env ファイルを置かず token だけ置いた状態（VM の実運用と同じ形）。
    `thth run` は事前確認を通り、先の判断（select・fail-closed 等）まで進む。
    ここでは approved の queue が無いので dry-run で「出すものが無い」exit 0 になる
    （事前確認で exit 2 になっていないことが本題）。"""
    token_path = str(tmp_path / "account.token")
    with open(token_path, "w", encoding="utf-8") as f:
        json.dump({"access_token": "fake-token"}, f)
    account = isolated_account_factory(token=token_path)
    # conftest の既定 env パス（存在しない）を確かに使っている＝env は無い状態。
    assert not os.path.exists(os.path.join(account["repo_dir"], "..", "nonexistent.env"))

    result = run_thth(["run", account["name"]])
    assert result.returncode == 0
    assert "token が無い" not in (result.stdout + result.stderr)
    assert "env" not in (result.stdout + result.stderr)
