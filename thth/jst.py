"""JST（日本標準時）の日付・時刻ユーティリティ。UTC で日付を切らない。

発注 §0-3・設計 §3.6・§7「UTC 日付で前日を上書き」の事故を防ぐため、すべての日付・時刻の
決定はここを通す。呼び出し側は `now_jst()` を 1 回だけ呼び、以後は同じ値を使い回す。

watchtower/watchtower/jst.py を写した（import しない・repo をまたぐ依存を作らない規約）。
"""
from __future__ import annotations

import datetime

JST = datetime.timezone(datetime.timedelta(hours=9))


def now_jst() -> datetime.datetime:
    """現在時刻を JST の aware datetime で返す。"""
    return datetime.datetime.now(tz=datetime.timezone.utc).astimezone(JST)


def to_jst(dt: datetime.datetime) -> datetime.datetime:
    """任意の aware datetime を JST に変換する。naive なら UTC とみなす。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(JST)


def today_str(dt: datetime.datetime | None = None) -> str:
    """JST の日付を YYYY-MM-DD で返す。"""
    dt = to_jst(dt) if dt is not None else now_jst()
    return dt.strftime("%Y-%m-%d")


def month_str(dt: datetime.datetime | None = None) -> str:
    """JST の年月を YYYY-MM で返す（runs-YYYY-MM.ndjson 用）。"""
    dt = to_jst(dt) if dt is not None else now_jst()
    return dt.strftime("%Y-%m")


def iso(dt: datetime.datetime | None = None, *, milliseconds: bool = False) -> str:
    """JST の ISO 8601 時刻（+09:00）を返す。既定は秒精度。"""
    dt = to_jst(dt) if dt is not None else now_jst()
    timespec = "milliseconds" if milliseconds else "seconds"
    return dt.isoformat(timespec=timespec)
