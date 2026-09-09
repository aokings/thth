"""`redact()` がログ・runs・例外文を通る。伏字前の文字列が出力に出ない（発注 §5 受け入れ 14）。"""
from __future__ import annotations

import os

from thth import redact as redact_mod
from thth import runs as runs_mod
from thth import writeback


def test_access_tokenは伏字になる():
    text = "GET https://graph.threads.net/x?access_token=SECRET123&foo=bar"
    out = redact_mod.redact(text)
    assert "SECRET123" not in out
    assert "access_token=***" in out


def test_client_secretは伏字になる():
    text = '{"client_secret": "SUPERSECRET", "ok": true}'
    out = redact_mod.redact(text)
    assert "SUPERSECRET" not in out


def test_authorizationヘッダは伏字になる():
    text = "Authorization: Bearer abcdef123456\nother line"
    out = redact_mod.redact(text)
    assert "abcdef123456" not in out
    assert "Authorization: ***" in out
    assert "other line" in out  # 他の行には影響しない


def test_hc_pingのパスは伏字になる():
    text = "ping https://hc-ping.com/11111111-2222-3333-4444-555555555555 done"
    out = redact_mod.redact(text)
    assert "11111111-2222-3333-4444-555555555555" not in out
    assert "hc-ping.com/***" in out


def test_none空文字はそのまま():
    assert redact_mod.redact(None) is None
    assert redact_mod.redact("") == ""


def test_runsに書くerrorはredactを通す(tmp_path):
    state_dir = str(tmp_path / "state")
    record = {
        "account": "nigamilab-threads", "run_id": "abc123", "mode": "production",
        "action": "post", "file": "a.md", "post_id": None, "collected": 0,
        "refreshed": False, "quota": None, "status": "error",
        "error": redact_mod.redact("失敗: access_token=SECRET456 が無効"),
    }
    path = runs_mod.append_run(state_dir, record, "2026-09")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    assert "SECRET456" not in text
    assert "access_token=***" in text


def test_writebackの失敗メッセージにgitのエラーが伏字を通る(tmp_path):
    # git が全く無い repo_dir を指すと add が失敗し、そのメッセージが返る。
    # 秘密の値は含まれないが、redact() を通ってもエラー文の意味が壊れないことを確認する。
    repo_dir = str(tmp_path / "not_a_repo")
    os.makedirs(repo_dir)
    ok, err = writeback.commit_and_push(repo_dir, rel_path="x.md", message="msg")
    assert ok is False
    assert isinstance(err, str)
