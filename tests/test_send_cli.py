"""`thth send` CLI 配線の確認（外部レビュー §1b・受け入れ 6）。

`core.send_once()` 自体の分岐（digest 計算・confirm 照合）は `tests/test_send.py` で
確認済み。ここでは argparse の `--confirm` の配線と、subprocess 越し（本物の
`bin/thth`）でも dry-run の digest と `--production --confirm` の一致が機能することを
確認する。本物の Threads API には触れない（`tests/test_fake_api.py` の偽サーバを使う）。
"""
from __future__ import annotations

import re

from tests.conftest import run_thth
from tests.test_fake_api import fake_threads_server


def test_dry_runで出たdigestをconfirmに渡すと本番で送れる(isolated_account_factory, tmp_path):
    account = isolated_account_factory(production=True)
    text_path = tmp_path / "body.txt"
    text_path.write_text("こんにちは\n", encoding="utf-8")

    dry = run_thth(["send", account["name"], "--text-file", str(text_path)])
    assert dry.returncode == 0
    m = re.search(r"digest: (\S+)", dry.stdout)
    assert m is not None
    digest = m.group(1)
    assert len(digest) == 12

    with fake_threads_server({"create": "ok", "publish": "ok"}) as base_url:
        env = {"THTH_THREADS_BASE_URL": base_url, "THTH_THREADS_WAIT_SECONDS": "0"}
        prod = run_thth(
            ["send", account["name"], "--text-file", str(text_path),
             "--production", "--confirm", digest], env=env)
    assert prod.returncode == 0
    assert "投稿しました" in prod.stdout


def test_confirmを渡さない本番は拒否される(isolated_account_factory, tmp_path):
    account = isolated_account_factory(production=True)
    text_path = tmp_path / "body.txt"
    text_path.write_text("こんにちは\n", encoding="utf-8")

    with fake_threads_server({"create": "ok", "publish": "ok"}) as base_url:
        env = {"THTH_THREADS_BASE_URL": base_url, "THTH_THREADS_WAIT_SECONDS": "0"}
        prod = run_thth(
            ["send", account["name"], "--text-file", str(text_path), "--production"], env=env)
    assert prod.returncode == 1
    assert "送信しません" in prod.stdout or "送信しません" in prod.stderr


def test_違うconfirmの本番は拒否される(isolated_account_factory, tmp_path):
    account = isolated_account_factory(production=True)
    text_path = tmp_path / "body.txt"
    text_path.write_text("こんにちは\n", encoding="utf-8")

    with fake_threads_server({"create": "ok", "publish": "ok"}) as base_url:
        env = {"THTH_THREADS_BASE_URL": base_url, "THTH_THREADS_WAIT_SECONDS": "0"}
        prod = run_thth(
            ["send", account["name"], "--text-file", str(text_path),
             "--production", "--confirm", "0" * 12], env=env)
    assert prod.returncode == 1
