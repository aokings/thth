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

import json
import os

from . import jst

# 最後に取り直した時刻の置き場（VM の state・`state/<口座>/reply_follow.json`）。
# **利用者の repo には書かない**（masaru 10/21「THTH の記録は VM の台帳に持ち、利用者の
# repo には積まない」）。新しい返信が無い取り直しは repo の台帳に 1 行も足さない。
STATE_FILE = "reply_follow.json"

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


def candidate(*, now, posted_at, reply_rows: list, fetch_rows: list, own_reply_at, cfg: dict,
              followed_at=None):
    """取り直す候補なら並べ替えの鍵（いちばん新しい動きの時刻）を返す。違えば None。

    `own_reply_at` は `sent/` の記録から見た、この投稿へ自分が返した最後の時刻（無ければ None）。
    `followed_at` は VM の state（`reply_follow.json`）に持つ、最後に取り直した時刻。間隔は
    これと台帳の取得の行の新しいほうから数える。
    """
    import datetime
    live = datetime.timedelta(hours=cfg["live_hours"])
    activity = [t for t in (latest_reply_at(reply_rows), own_reply_at) if t is not None]
    latest = max(activity) if activity else None
    if now - posted_at > live and (latest is None or now - latest > live):
        return None
    last = max([t for t in (last_fetch_at(fetch_rows), followed_at) if t is not None], default=None)
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


def state_path(state_dir: str) -> str:
    return os.path.join(state_dir, STATE_FILE)


def read_state(state_dir: str, *, log=None) -> dict:
    """post_id → 最後に取り直した時刻（aware datetime）。無い・壊れている・読めないなら空。

    壊れていても採取は止めない（読めなければ空として扱い、log に 1 行）。空なら台帳の
    取得の行だけで間隔を数えるので、取りすぎるのは高々 1 回。
    """
    path = state_path(state_dir)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        if log:
            log(f"返信の取り直しの記録を読めないので空として扱います: {path}（{e.__class__.__name__}）")
        return {}
    last = data.get("last") if isinstance(data, dict) else None
    if not isinstance(last, dict):
        if log:
            log(f"返信の取り直しの記録の形が違うので空として扱います: {path}")
        return {}
    out = {}
    for pid, raw in last.items():
        at = jst.parse(raw) if isinstance(raw, str) else None
        if at is not None:
            out[str(pid)] = at
    return out


def write_state(state_dir: str, state: dict, *, now, live_hours: int, log=None) -> None:
    """最後に取り直した時刻を書く（tmp に書いて置き換え）。窓の外の古い行は落とす。

    書けなくても採取は止めない（log に 1 行・次の回は台帳の取得の行で数える）。
    """
    import datetime
    keep = now - datetime.timedelta(hours=max(live_hours, 1) * 2)
    body = {"last": {pid: jst.iso(at) for pid, at in sorted(state.items()) if at >= keep}}
    path = state_path(state_dir)
    tmp = path + ".tmp"
    try:
        os.makedirs(state_dir, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except OSError as e:
        if log:
            log(f"返信の取り直しの記録を書けませんでした（採取は続けます）: {path}（{e.__class__.__name__}）")
