"""select の 11 条件（発注 §3・受け入れ 5）。1 条件 1 テスト。順序が意味を持つ点を確認する。"""
from __future__ import annotations

import datetime

from tests.conftest import make_queue_text
from thth import queuefile, select as select_mod

ACCOUNT = "nigamilab-threads"
NOW = datetime.datetime.fromisoformat("2026-09-09T10:00:00+09:00")


def account_cfg(**overrides):
    cfg = {
        "media": "threads",
        "hashtags": False,
        "quiet_hours": ["22:00", "07:00"],
        "min_interval_hours": 6,
        "stale_days": 7,
    }
    cfg.update(overrides)
    return cfg


def write(tmp_path, name, **kwargs):
    path = tmp_path / name
    path.write_text(make_queue_text(**kwargs), encoding="utf-8")
    return str(path)


def parse_all(paths):
    return [queuefile.parse(p) for p in paths]


def select(files, **kwargs):
    kwargs.setdefault("account_name", ACCOUNT)
    kwargs.setdefault("account_cfg", account_cfg())
    kwargs.setdefault("now", NOW)
    kwargs.setdefault("last_post_at", None)
    kwargs.setdefault("recent_texts", set())
    return select_mod.select_one(files, **kwargs)


# 1. post_id が入っている → status より先に落とす（approved に戻されても出ない・§3.5）
def test_1_post_idがあれば承認済みでも出ない(tmp_path):
    p = write(tmp_path, "a.md", fm_overrides={
        "status": "approved", "post_id": "9999999",
        "publish_at": "2026-09-09T08:00:00+09:00"})
    result = select(parse_all([p]))
    assert result.chosen is None
    assert any(r.reason == "post_id_present" for r in result.rejections)


# 2. thth: が無い・front-matter が壊れている → 型外（失敗に数えない）
def test_2_front_matter破損は型外(tmp_path):
    p = write(tmp_path, "a.md", no_front_matter=True, body="ただの文章\n")
    result = select(parse_all([p]))
    assert result.chosen is None
    assert p in result.type_mismatch
    assert result.rejections == []


# 3. account が対象と違う → 落とす
def test_3_accountが違うと落ちる(tmp_path):
    p = write(tmp_path, "a.md", fm_overrides={"account": "other-account"})
    result = select(parse_all([p]))
    assert result.chosen is None
    assert any(r.reason == "account_mismatch" for r in result.rejections)


# 4. status != approved → 落とす
def test_4_draftは出ない(tmp_path):
    p = write(tmp_path, "a.md", fm_overrides={"status": "draft"})
    result = select(parse_all([p]))
    assert result.chosen is None
    assert any(r.reason == "not_approved" for r in result.rejections)


# 5a. publish_at が未来 → 落とす
def test_5a_publish_atが未来なら落ちる(tmp_path):
    p = write(tmp_path, "a.md", fm_overrides={"publish_at": "2026-09-09T23:00:00+09:00"})
    result = select(parse_all([p]))
    assert result.chosen is None
    assert any(r.reason == "future" for r in result.rejections)


# 5b. publish_at に +09:00 が無い → 型外＋要確認
def test_5b_publish_atにtz無しは型外かつ要確認(tmp_path):
    p = write(tmp_path, "a.md", fm_overrides={"publish_at": "2026-09-09T08:00:00"})
    result = select(parse_all([p]))
    assert result.chosen is None
    assert p in result.type_mismatch
    assert p in result.needs_review


# 6. 静かな時間帯 → 全部落とす
def test_6_静かな時間帯は全部落ちる(tmp_path):
    p = write(tmp_path, "a.md", fm_overrides={"publish_at": "2026-09-09T08:00:00+09:00"})
    quiet_now = datetime.datetime.fromisoformat("2026-09-09T23:30:00+09:00")
    result = select(parse_all([p]), now=quiet_now)
    assert result.chosen is None
    assert any(r.reason == "quiet_hours" for r in result.rejections)


# 7. 前回投稿から min_interval_hours 未満 → 全部落とす
def test_7_最短間隔未満は全部落ちる(tmp_path):
    p = write(tmp_path, "a.md", fm_overrides={"publish_at": "2026-09-09T08:00:00+09:00"})
    last_post_at = NOW - datetime.timedelta(hours=1)
    result = select(parse_all([p]), last_post_at=last_post_at)
    assert result.chosen is None
    assert any(r.reason == "min_interval" for r in result.rejections)


# 8. 媒体の節が無い／文字数超過
def test_8a_媒体の節が無いと落ちる(tmp_path):
    p = write(tmp_path, "a.md", body="節が無い本文\n")
    result = select(parse_all([p]))
    assert result.chosen is None
    assert any(r.reason == "no_section" for r in result.rejections)


def test_8b_文字数超過はtoo_longで落ちる(tmp_path):
    body = "## threads\n\n" + ("あ" * 500) + "\n"
    p = write(tmp_path, "a.md", body=body)
    result = select(parse_all([p]))
    assert result.chosen is None
    assert any(r.reason == "too_long(501)" for r in result.rejections)


# 9. hashtags: false なのに `#` 語がある → 落とす
def test_9_hashtagがあると落ちる(tmp_path):
    p = write(tmp_path, "a.md", body="## threads\n\n本文 #タグ です\n")
    result = select(parse_all([p]))
    assert result.chosen is None
    assert any(r.reason == "hashtag" for r in result.rejections)


# 10. publish_at から stale_days 超 → 落として要確認
def test_10_stale_daysを超えると要確認(tmp_path):
    p = write(tmp_path, "a.md", fm_overrides={"publish_at": "2026-08-01T08:00:00+09:00"})
    result = select(parse_all([p]))
    assert result.chosen is None
    assert any(r.reason == "stale" for r in result.rejections)
    assert p in result.needs_review


# 11. 直近 30 日の投稿済み本文と完全一致 → 落とす
def test_11_直近30日の重複本文は落ちる(tmp_path):
    body = "## threads\n\n重複する本文です。\n"
    p = write(tmp_path, "a.md", body=body)
    recent = {"重複する本文です。"}
    result = select(parse_all([p]), recent_texts=recent)
    assert result.chosen is None
    assert any(r.reason == "duplicate_text" for r in result.rejections)


# 正常系: 全部通れば選ばれる。同時刻ならファイル名順。
def test_正常系は選ばれ同時刻ならファイル名順(tmp_path):
    p_b = write(tmp_path, "b.md", fm_overrides={"publish_at": "2026-09-09T08:00:00+09:00"})
    p_a = write(tmp_path, "a.md", fm_overrides={"publish_at": "2026-09-09T08:00:00+09:00"})
    result = select(parse_all([p_b, p_a]))
    assert result.chosen is not None
    assert result.chosen.path == p_a


def test_複数approvedがあっても1件だけ選ぶ(tmp_path):
    p_early = write(tmp_path, "early.md", fm_overrides={"publish_at": "2026-09-09T07:00:00+09:00"})
    p_late = write(tmp_path, "late.md", fm_overrides={"publish_at": "2026-09-09T09:00:00+09:00"})
    result = select(parse_all([p_early, p_late]))
    assert result.chosen is not None
    assert result.chosen.path == p_early
