"""会話が続いている投稿の返信を、刻みとは別に取り直す（4.4.0・設計は
`docs/設計_4.4.0_会話の続く投稿の取り直し_2026-10-21.md`）。

**なぜ要るか**（kopicha 10/21）。返信は投稿からの経過時間の刻み（`collect.REPLY_MARKS_HOURS`
＝1・6・24・72・168 時間）でしか採っていなかった。刻みの間に付いた返信は次の刻みまで台帳に
入らず、台帳の「返していない返信の数」を読んで担当を起こす h2d は、それを知りようがない
（10/21 朝 kopicha-threads で約 2 時間半遅れた）。

**何をするか**。`collect_once()`（`thth run` の採取）の中で、**会話が続いている投稿**だけ、
前の取得からおおむね `every_minutes`（既定 20 分）たったら会話を取り直す。性質は
`thth replies --refresh` と同じ——**刻みを見ない・刻みを進めない（`marks: []`）・数（insights）を
取らない**。新しく見つけた返信は台帳に入り、unanswered の数に効く。

会話が続いている＝投稿から `live_hours`（既定 48 時間）以内、または直近 `live_hours` に
返信（他人の返信か自分の返信）が付いた投稿。

台帳の任意項目 `reply_follow`（口座ごと）:

    "reply_follow": {"enabled": true, "every_minutes": 20, "live_hours": 48, "max_posts": 10}

どの鍵も省略できる。**X は既定で外す**（`enabled` を明示で true にしたときだけ）——X の枝の
読み取りは 1 本 0.005 USD の従量で、20 分おきに読むと 1 本の投稿の 48 時間で 144 回になる。
回数の上限も厳しい（設計 2026-10-08）。
"""
from __future__ import annotations

from . import jst

DEFAULTS = {"every_minutes": 20, "live_hours": 48, "max_posts": 10}
# 受け取る範囲（形の違う値は loader が断る・黙って既定に倒さない）。
RANGES = {"every_minutes": (5, 1440), "live_hours": (1, 720), "max_posts": (0, 50)}
KEYS = frozenset({"enabled", *DEFAULTS})
# 既定で外す媒体（他人の投稿の読み取りが有償・回数の上限が厳しい）。
OFF_BY_DEFAULT_MEDIA = frozenset({"x"})
TRIGGER = "follow"


def valid(value) -> bool:
    """台帳の `reply_follow` として受け取れる形か。"""
    if not isinstance(value, dict) or not set(value) <= KEYS:
        return False
    if "enabled" in value and not isinstance(value["enabled"], bool):
        return False
    for key, (low, high) in RANGES.items():
        if key in value:
            v = value[key]
            if type(v) is not int or not low <= v <= high:
                return False
    return True


def settings(account_cfg: dict) -> dict:
    """口座の設定（既定を埋めたもの）。`enabled` は媒体で既定が変わる。"""
    raw = (account_cfg or {}).get("reply_follow")
    raw = raw if isinstance(raw, dict) else {}
    media = (account_cfg or {}).get("media")
    out = dict(DEFAULTS)
    out["enabled"] = media not in OFF_BY_DEFAULT_MEDIA
    for key in KEYS:
        if key in raw:
            out[key] = raw[key]
    if out["max_posts"] <= 0:
        out["enabled"] = False
    return out


def last_fetch_at(fetch_rows: list):
    """台帳の取得の記録（`kind: fetch`）のうち最後の時刻。刻み・refresh・follow を問わない。"""
    latest = None
    for row in fetch_rows:
        at = jst.parse(row.get("collected_at")) if row.get("collected_at") else None
        if at is not None and (latest is None or at > latest):
            latest = at
    return latest


def latest_reply_at(reply_rows: list):
    """台帳の返信のうち、いちばん新しい返信の時刻（媒体の `timestamp`・無ければ取り込んだ時刻）。"""
    latest = None
    for row in reply_rows:
        raw = row.get("timestamp") or row.get("created_at") or row.get("collected_at")
        at = jst.parse(raw) if isinstance(raw, str) and raw else None
        if at is not None and (latest is None or at > latest):
            latest = at
    return latest


def candidate(*, now, posted_at, reply_rows: list, fetch_rows: list, own_reply_at, cfg: dict):
    """取り直す候補なら並べ替えの鍵（いちばん新しい動きの時刻）を返す。違えば None。

    `own_reply_at` は `sent/` の記録から見た、この投稿へ自分が返した最後の時刻（無ければ None）。
    """
    import datetime
    live = datetime.timedelta(hours=cfg["live_hours"])
    activity = [t for t in (latest_reply_at(reply_rows), own_reply_at) if t is not None]
    latest = max(activity) if activity else None
    if now - posted_at > live and (latest is None or now - latest > live):
        return None
    last = last_fetch_at(fetch_rows)
    if last is not None and now - last < datetime.timedelta(minutes=cfg["every_minutes"]):
        return None
    return max([posted_at, *activity])


def own_reply_times(sent_rows: list) -> dict:
    """`sent/` の記録から、返信先の投稿 id → 自分が返した最後の時刻。"""
    out = {}
    for row in sent_rows:
        target, at = row.get("reply_to"), row.get("sent_at")
        if not target or not isinstance(at, str):
            continue
        when = jst.parse(at)
        if when is None:
            continue
        key = str(target)
        if key not in out or when > out[key]:
            out[key] = when
    return out


def pick(candidates: list, cfg: dict) -> list:
    """候補 `(鍵の時刻, 中身)` を新しい動きの順に並べ、上限の本数だけ返す。"""
    ordered = sorted(candidates, key=lambda c: c[0], reverse=True)
    return [c[1] for c in ordered[:cfg["max_posts"]]]
