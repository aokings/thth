"""`min_interval_hours: 0` と `quiet_hours: null`／`[]` で「制限しない」を選べること
（設計 §3.6 の裁定・masaru 指摘 2026-09-09）。頻度・時刻・本数は編集の判断であって
基盤の礼儀ではない。台帳の形式検査がこれらを弾かないことも併せて確認する。
"""
from __future__ import annotations

import datetime

from tests.conftest import make_queue_text, parse_verified
from thth import accounts as accounts_mod
from thth import queuefile
from thth import select as select_mod

ACCOUNT = "nigamilab-threads"
NOW = datetime.datetime.fromisoformat("2026-09-09T23:30:00+09:00")  # 通常なら静かな時間帯


def write(tmp_path, name, **kwargs):
    path = tmp_path / name
    path.write_text(make_queue_text(**kwargs), encoding="utf-8")
    return str(path)


def test_quiet_hoursがnullなら常にFalse():
    assert select_mod.in_quiet_hours(NOW, None) is False


def test_quiet_hoursが空配列でも常にFalse():
    assert select_mod.in_quiet_hours(NOW, []) is False


def test_quiet_hoursが設定されていれば従来どおり判定する():
    assert select_mod.in_quiet_hours(NOW, ["22:00", "07:00"]) is True
    daytime = datetime.datetime.fromisoformat("2026-09-09T10:00:00+09:00")
    assert select_mod.in_quiet_hours(daytime, ["22:00", "07:00"]) is False


def test_quiet_hours_nullなら深夜でも選ばれる(tmp_path):
    p = write(tmp_path, "a.md", fm_overrides={"publish_at": "2026-09-09T08:00:00+09:00"})
    cfg = {"media": "threads", "hashtags": False, "quiet_hours": None,
           "min_interval_hours": 6, "stale_days": 7}
    result = select_mod.select_one(
        [parse_verified(p)], account_name=ACCOUNT, account_cfg=cfg,
        now=NOW, last_post_at=None, recent_texts=set())
    assert result.chosen is not None
    assert result.chosen.path == p


def test_min_interval_0なら直前に投稿していても選ばれる(tmp_path):
    p = write(tmp_path, "a.md", fm_overrides={"publish_at": "2026-09-09T08:00:00+09:00"})
    cfg = {"media": "threads", "hashtags": False, "quiet_hours": None,
           "min_interval_hours": 0, "stale_days": 7}
    daytime = datetime.datetime.fromisoformat("2026-09-09T10:00:00+09:00")
    last_post_at = daytime - datetime.timedelta(minutes=1)  # 1 分前に投稿済み
    result = select_mod.select_one(
        [parse_verified(p)], account_name=ACCOUNT, account_cfg=cfg,
        now=daytime, last_post_at=last_post_at, recent_texts=set())
    assert result.chosen is not None
    assert result.chosen.path == p


def test_台帳のquiet_hours_nullとmin_interval_0を形式検査は弾かない(tmp_path, monkeypatch):
    """`accounts.load_account()` が `quiet_hours: null`・`min_interval_hours: 0` を
    弾かないこと（必須項目チェックはキーの有無だけを見る）。"""
    import json
    import os

    fake_app_dir = tmp_path / "app"
    accounts_dir = fake_app_dir / "accounts"
    os.makedirs(accounts_dir)
    monkeypatch.setattr(accounts_mod, "APP_DIR", str(fake_app_dir))

    data = {
        "account": "x-threads", "project": "x", "media": "threads", "handle": "x",
        "repo_dir": str(tmp_path), "queue_dir": "queue", "replies_dir": "replies",
        "quiet_hours": None, "min_interval_hours": 0, "collect_days": 14,
        "hashtags": True, "stale_days": 7, "env": str(tmp_path / "none.env"),
        "token": str(tmp_path / "none.token"), "ping": "wrapper", "timeout": 300,
        "dry_run_env": "THTH_DRY_RUN", "production": False,
    }
    with open(accounts_dir / "x-threads.json", "w", encoding="utf-8") as f:
        json.dump(data, f)

    cfg = accounts_mod.load_account("x-threads")
    assert cfg["quiet_hours"] is None
    assert cfg["min_interval_hours"] == 0
