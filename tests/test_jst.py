"""test_20260828_utc_date_overwrote_previous_day（発注 §5）: `publish_at`・`posted_at`
は JST（`+09:00`）で扱われ、UTC の日付で前日を上書きしないこと。"""
from __future__ import annotations

import datetime

from thth import jst, queuefile


def test_20260828_utc_date_overwrote_previous_day():
    # UTC 2026-08-28T15:30 は JST では 2026-08-29T00:30（日付が 1 日進む）。
    # UTC の日付だけを見ると 08-28 のまま扱ってしまい、runs-YYYY-MM や board の
    # 「今日」判定が前日のものとして扱われる（事故の型）。
    dt_utc = datetime.datetime(2026, 8, 28, 15, 30, tzinfo=datetime.timezone.utc)
    assert jst.today_str(dt_utc) == "2026-08-29"
    assert jst.month_str(dt_utc) == "2026-08"

    dt_utc_month_boundary = datetime.datetime(2026, 8, 31, 15, 30, tzinfo=datetime.timezone.utc)
    assert jst.today_str(dt_utc_month_boundary) == "2026-09-01"
    assert jst.month_str(dt_utc_month_boundary) == "2026-09"


def test_isoはplus0900のオフセットを持つ():
    dt_utc = datetime.datetime(2026, 8, 28, 15, 30, tzinfo=datetime.timezone.utc)
    text = jst.iso(dt_utc)
    assert text.endswith("+09:00")
    assert text.startswith("2026-08-29T00:30:00")


def test_publish_atはplus0900必須でパースされたらjstを保つ():
    dt = queuefile.parse_publish_at("2026-09-09T08:00:00+09:00")
    assert dt.utcoffset() == datetime.timedelta(hours=9)
    # naive（オフセット無し）の文字列は publish_at として使わない規約（lint が弾く）が、
    # ここでは parse 自体が例外にならず naive datetime を返すことだけ確認する
    # （「+09:00 必須」のチェックは queuefile.parse_publish_at の外側・lint/select 側の責務）。
    naive = queuefile.parse_publish_at("2026-09-09T08:00:00")
    assert naive.tzinfo is None
