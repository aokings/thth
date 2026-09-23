"""3.7.0 §B2 `collection_stale_hours` の数え始めを添える。

実装で確かめた定義: 根投稿ごとの「最後に取得が成功した時刻」（`kind: fetch` の行・
返信 0 件の成功も書く・失敗した試行は台帳に残らない）のうち**いちばん古いもの**から。
返信が 1 件以上取れた時刻とは別物なので、両方を添える。
"""
from __future__ import annotations

import json
from pathlib import Path

from tests.test_v210_unanswered import own_sent
from thth import accounts, jst, morning, unanswered

NOW = jst.parse("2026-09-09T10:00:00+09:00")


def _write(cfg, root, rows):
    directory = Path(accounts.data_dirs(accounts.load_account(cfg["name"]), cfg["name"])["replies"])
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{root}.ndjson").write_text("".join(json.dumps(r) + "\n" for r in rows))


def _seed(isolated_account_factory):
    cfg = isolated_account_factory("one", handle="owner")
    own_sent("one", "ROOT1")
    own_sent("one", "ROOT2")
    # ROOT1: 24 時間前に取れた（返信 1 件）。ROOT2: 1 時間前に取れた（返信 0 件の成功）。
    _write(cfg, "ROOT1", [
        {"kind": "reply", "id": "R1", "username": "outside", "text": "x",
         "timestamp": "2026-09-08T09:00:00+09:00", "post_id": "ROOT1",
         "collected_at": "2026-09-08T10:00:00+09:00"},
        {"kind": "fetch", "post_id": "ROOT1", "collected_at": "2026-09-08T10:00:00+09:00"}])
    _write(cfg, "ROOT2", [
        {"kind": "fetch", "post_id": "ROOT2", "collected_at": "2026-09-09T09:00:00+09:00"}])
    return cfg


def test_数え始めはいちばん古い根投稿の最後の取得の成功_返信が取れた時刻は別(isolated_account_factory):
    _seed(isolated_account_factory)
    result = unanswered.answer("one", now=NOW)
    assert result["collection_stale_hours"] == 24
    assert result["collection_stale_basis"] == "oldest_root_last_successful_fetch"
    assert result["collection_stale_since"] == "2026-09-08T10:00:00+09:00"
    assert result["last_fetch_at"] == "2026-09-09T09:00:00+09:00"
    assert result["last_reply_collected_at"] == "2026-09-08T10:00:00+09:00"


def test_observeの返信の段に数え始めと1行(isolated_account_factory):
    cfg = _seed(isolated_account_factory)
    node = morning._unanswered_rows("one", NOW, cfg=accounts.load_account(cfg["name"]))
    assert node["collection_stale_basis"] == "oldest_root_last_successful_fetch"
    assert node["collection_stale_since"] == "2026-09-08T10:00:00+09:00"
    assert node["last_fetch_hours"] == 1.0 and node["last_reply_hours"] == 24.0
    line = morning.fetch_line(node)
    assert line.startswith("返信の取得: 最後に取得できた（0 件を含む）のは、いちばん古い根投稿で 24.0h前")
    assert "いちばん新しい取得は 1.0h 前" in line
    assert "返信が 1 件以上取れたのは 24.0h 前" in line
    assert "失敗した試行は台帳に残らない" in line
    lines = []
    morning._render_unanswered("one", {"medium": "threads",
                                       "replies": {"value": node, "cannot_say": None},
                                       "mentions": {"value": None, "cannot_say": "not_supported"}},
                               lines.append)
    assert any(item.strip() == line for item in lines)


def test_取得の記録が無ければ数え始めはnull(isolated_account_factory):
    isolated_account_factory("one", handle="owner")
    own_sent("one", "ROOT1")
    result = unanswered.answer("one", now=NOW)
    assert result["collection_stale_since"] is None and result["last_fetch_at"] is None
    assert morning.fetch_line({"collection_stale_since": None}) is None
