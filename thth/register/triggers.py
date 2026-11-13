"""暦と台帳から出るきっかけ（計画 段 2・叩き台 §6.1）。中身（投稿の文）とは分けて持つ（構想 §9 の 2）。

| きっかけ | 行 | いつ出るか（`window`） | 出さない期限（`not_after`） |
|---|---|---|---|
| `special_soon` 臨時の休み・時間の変更が近い | `special` | `date` の `SPECIAL_LEAD_DAYS` 日前から最後の日まで | 最後の日（`end_date` か `date`）の 23:59 |
| `event_soon` 催しが近い | `event` | `starts` の `EVENT_LEAD_DAYS` 日前から終わりまで | `ends` |
| `hours_change` 営業の時間が変わる | `hours`（`effective_from` のある行） | 変わる日の `HOURS_LEAD_DAYS` 日前から変わる日まで | 変わる日の `HOURS_AFTER_DAYS` 日後の 23:59 |
| `item_new` 店頭に新しい品 | `item`（`since` のある行） | 並んだ日から `ITEM_NEW_DAYS` 日の間 | 並んだ日の `ITEM_NEW_DAYS` 日後の 23:59 |
| `season_start` 季節の品の始まり | `item`（`season_from` のある行） | 始まりの `SEASON_LEAD_DAYS` 日前から始まりの日まで | 終わりの日（`season_to`・無ければ始まりの `ITEM_NEW_DAYS` 日後）の 23:59 |

日数は叩き台の案（決め打ちにせず、本物の店で測って直す・計画 段 3 の「次へ進む条件」と同じ考え）。
「今日やっています」のような、事実の変化が無い日のきっかけは作らない（計画 段 2 の入らない物）。
"""
from __future__ import annotations

import datetime

from .. import jst
from . import fence, form, kinds

SPECIAL_LEAD_DAYS = 7
EVENT_LEAD_DAYS = 14
HOURS_LEAD_DAYS = 14
HOURS_AFTER_DAYS = 7
ITEM_NEW_DAYS = 7
SEASON_LEAD_DAYS = 7
TRIGGERS = ("special_soon", "event_soon", "hours_change", "item_new", "season_start")


def _end_of(day: datetime.date) -> datetime.datetime:
    return datetime.datetime.combine(day, datetime.time(23, 59), tzinfo=jst.JST)


def _at(minute: datetime.datetime) -> datetime.datetime:
    return minute.replace(tzinfo=jst.JST)


def window(row: form.Row, now: datetime.datetime):
    """`(きっかけ, 出さない期限, 状態)`。状態は due（今出す頃）・early（まだ早い）・past（過ぎた）。無ければ None。"""
    head, today = row.head, jst.to_jst(now).date()
    if row.kind == "special":
        start = kinds.parse_day(head.get("date"))
        if start is None:
            return None
        end = kinds.parse_day(head.get("end_date")) or start
        return _state("special_soon", start - datetime.timedelta(days=SPECIAL_LEAD_DAYS), _end_of(end), now)
    if row.kind == "event":
        starts, ends = kinds.parse_minute(head.get("starts")), kinds.parse_minute(head.get("ends"))
        if starts is None or ends is None:
            return None
        return _state("event_soon", starts.date() - datetime.timedelta(days=EVENT_LEAD_DAYS), _at(ends), now)
    if row.kind == "hours" and head.get("effective_from"):
        start = kinds.parse_day(head["effective_from"])
        if start is None:
            return None
        return _state("hours_change", start - datetime.timedelta(days=HOURS_LEAD_DAYS),
                      _end_of(start + datetime.timedelta(days=HOURS_AFTER_DAYS)), now)
    if row.kind == "item":
        season = kinds.parse_day(head.get("season_from"))
        if season is not None:
            end = kinds.parse_day(head.get("season_to")) or season + datetime.timedelta(days=ITEM_NEW_DAYS)
            found = _state("season_start", season - datetime.timedelta(days=SEASON_LEAD_DAYS), _end_of(end), now)
            # 始まりの日を過ぎたら「始まりました」とは言わない（並んでいる間でも・きっかけは始まりだけ）。
            if found[2] == "due" and today > season:
                return ("season_start", found[1], "past")
            return found
        since = kinds.parse_day(head.get("since"))
        if since is not None:
            return _state("item_new", since, _end_of(since + datetime.timedelta(days=ITEM_NEW_DAYS)), now)
    return None


def _state(trigger: str, opens: datetime.date, not_after: datetime.datetime, now: datetime.datetime):
    if now > not_after:
        return trigger, not_after, "past"
    if jst.to_jst(now).date() < opens:
        return trigger, not_after, "early"
    return trigger, not_after, "due"


def find(rows: list, records: dict, now: datetime.datetime) -> list:
    """今出す頃のきっかけ（`due`）を、行ごとに 1 つ。使えない行も並べ、`code` に柵の符丁を入れる。"""
    out = []
    for row in rows:
        if row.problems or row.head.get("status") == form.RETIRED:
            continue
        found = window(row, now)
        if found is None or found[2] != "due":
            continue
        trigger, not_after, _state_word = found
        out.append({"trigger": trigger, "row": row.id, "kind": row.kind, "not_after": jst.iso(not_after),
                    "code": fence.state(row, records, jst.to_jst(now).date())})
    return out
