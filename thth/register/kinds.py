"""行の種類ごとの決まり（段 1 は土台だけ・段 2 で実店舗の種類を足した）。

どの種類にも当てはまる決まりは `form.py`。ここは種類ごとの差だけを持つ:

- `two_stage`: 認める操作を二段確認にする（間違えたときの害が大きい種類・計画 段 2: 営業の時間と臨時）。
- `private_by_default`: 既定で外に出さない頭の欄（持ち主が `public_fields` に書いたときだけ出す）。
  店の住所と電話・臨時の休みの理由（店と家が同じ場所だと、家が空く知らせになる・計画 §6 の 2）。
- `check`: 種類ごとの形の点検（`(head, body) -> [問題の語]`）。

表に無い種類（自前のメディアの `claim`・`school` など）は、どの種類にも共通の決まりだけで読む。

実店舗の種類（叩き台 §4.1 の形を元に）:

| 種類 | 1 枚 | 頭の欄 |
|---|---|---|
| `store` | 店.md | `name`（要る）・`address`・`phone`（既定で出さない）・`map_url`・`site_url` |
| `hours` | 営業の時間.md | `effective_from`（この日から・任意）。本文に曜日ごとの行 `月 10:00-18:00`・`水 休み`・`金 18:00-翌02:00`・`※ ` で始まる注 |
| `special` | 臨時/<日>_<名>.md | `date`（要る）・`end_date`・`closed`（true か false）・`open`・`close`（closed が false のとき）・`reason`（既定で出さない） |
| `event` | 催し/<日>_<名>.md | `name`・`starts`・`ends`（`YYYY-MM-DD HH:MM`・要る）・`place`・`fee`・`booking`・`organizer_url` |
| `item` | 品/<名>.md | `name`（要る）・`price`・`since`（店頭に並んだ日）・`season_from`・`season_to` |
| `voice` | 思い.md | 本文だけ |
"""
from __future__ import annotations

import datetime
import re

WEEKDAYS = ("月", "火", "水", "木", "金", "土", "日")
DAY_WORDS = {"月": "mon", "火": "tue", "水": "wed", "木": "thu", "金": "fri", "土": "sat", "日": "sun", "祝": "hol"}
_TIME = re.compile(r"([01][0-9]|2[0-3]):([0-5][0-9])\Z")
_RANGE = re.compile(r"([0-9]{2}:[0-9]{2})-(翌)?([0-9]{2}:[0-9]{2})\Z")
_DAY_LINE = re.compile(r"(月|火|水|木|金|土|日|祝)\s+(.+)\Z")
_MINUTE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} ([01][0-9]|2[0-3]):[0-5][0-9]\Z")
_PRICE = re.compile(r"[0-9]{1,9}\Z")
CLOSED_WORD = "休み"
NOTE_MARK = "※"


def parse_day(value) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(value) if isinstance(value, str) and len(value) == 10 else None
    except ValueError:
        return None


def parse_minute(value) -> datetime.datetime | None:
    """`YYYY-MM-DD HH:MM`（JST の時刻として読む）。形が違えば None。"""
    if not isinstance(value, str) or not _MINUTE.fullmatch(value):
        return None
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def parse_time(value) -> int | None:
    """`HH:MM` を 0 時からの分に。"""
    matched = _TIME.fullmatch(value or "")
    return int(matched.group(1)) * 60 + int(matched.group(2)) if matched else None


def parse_ranges(text: str):
    """`10:00-18:00, 19:00-翌01:00` を `[(開ける分, 閉める分)]` に（翌は +1440）。形が違えば None。"""
    out = []
    for part in text.split(","):
        matched = _RANGE.fullmatch(part.strip())
        if not matched:
            return None
        start, end = parse_time(matched.group(1)), parse_time(matched.group(3))
        if start is None or end is None:
            return None
        if matched.group(2):
            end += 1440
        if end <= start:
            return None
        out.append((start, end))
    return out


def hours_lines(body: str):
    """営業の時間の本文を `({曜: 'closed' か [(開, 閉)]}, [注], [問題の語])` に。"""
    days, notes, problems = {}, [], []
    for number, raw in enumerate(body.split("\n"), start=1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith(NOTE_MARK):
            notes.append(line)
            continue
        matched = _DAY_LINE.fullmatch(line)
        if not matched:
            problems.append(f"hours_line:{number}")
            continue
        day, rest = matched.group(1), matched.group(2).strip()
        if day in days:
            problems.append(f"hours_day_twice:{DAY_WORDS[day]}")
            continue
        if rest == CLOSED_WORD:
            days[day] = "closed"
            continue
        ranges = parse_ranges(rest)
        if ranges is None:
            problems.append(f"hours_time:{DAY_WORDS[day]}")
            continue
        days[day] = ranges
    for day in WEEKDAYS:
        if day not in days and not any(p.endswith(":" + DAY_WORDS[day]) for p in problems):
            problems.append(f"hours_day_missing:{DAY_WORDS[day]}")
    return days, notes, problems


def _check_store(head, body):
    return [] if head.get("name") else ["missing:name"]


def _check_hours(head, body):
    problems = [] if "effective_from" not in head or parse_day(head["effective_from"]) else ["date_form:effective_from"]
    return problems + hours_lines(body)[2]


def _check_special(head, body):
    problems = []
    start = parse_day(head.get("date"))
    if start is None:
        problems.append("date_form:date")
    if "end_date" in head:
        end = parse_day(head["end_date"])
        if end is None:
            problems.append("date_form:end_date")
        elif start is not None and end < start:
            problems.append("special_range")
    closed = head.get("closed")
    if closed not in ("true", "false"):
        problems.append("special_closed")
    elif closed == "false":
        opened, shut = parse_time(head.get("open")), parse_time(head.get("close"))
        if opened is None or shut is None or shut <= opened:
            problems.append("special_time")
    return problems


def _check_event(head, body):
    problems = [] if head.get("name") else ["missing:name"]
    starts, ends = parse_minute(head.get("starts")), parse_minute(head.get("ends"))
    if starts is None:
        problems.append("minute_form:starts")
    if ends is None:
        problems.append("minute_form:ends")
    if starts is not None and ends is not None and ends < starts:
        problems.append("event_range")
    return problems


def _check_item(head, body):
    problems = [] if head.get("name") else ["missing:name"]
    for key in ("since", "season_from", "season_to"):
        if key in head and parse_day(head[key]) is None:
            problems.append(f"date_form:{key}")
    if "price" in head and not _PRICE.fullmatch(head["price"]):
        problems.append("item_price")
    start, end = parse_day(head.get("season_from")), parse_day(head.get("season_to"))
    if start and end and end < start:
        problems.append("item_season")
    return problems


RULES: dict[str, dict] = {
    "store": {"private_by_default": ("address", "phone"), "check": _check_store},
    "hours": {"two_stage": True, "check": _check_hours},
    "special": {"two_stage": True, "private_by_default": ("reason",), "check": _check_special},
    "event": {"check": _check_event},
    "item": {"check": _check_item},
    "voice": {},
}

# 点検の語ごとの直し方（`form.PROBLEM_TEXT` に足す・語は `:` の前で引く）。
PROBLEM_TEXT = {
    "hours_line": "営業の時間の本文の行は「月 10:00-18:00」「水 休み」の形か、※ で始まる注にしてください",
    "hours_day_twice": "同じ曜日の行が 2 つあります。1 行にまとめてください（時間を分けるなら 11:00-14:00, 17:00-22:00）",
    "hours_time": "時刻は 10:00-18:00 の形にし、閉める時刻を開ける時刻より後にしてください。日をまたぐなら 18:00-翌02:00",
    "hours_day_missing": "曜日の行が抜けています。月から日まで 7 行そろえ、休みの日は「水 休み」と書いてください",
    "minute_form": "時刻の欄は YYYY-MM-DD HH:MM にしてください（例 2026-12-14 13:00）",
    "special_range": "end_date は date と同じ日か、それより後にしてください",
    "special_closed": "closed は true（終日休み）か false（時間を変えて開ける）にしてください",
    "special_time": "closed: false のときは open と close を HH:MM で書き、close を open より後にしてください",
    "event_range": "ends は starts より後にしてください",
    "item_price": "price は円の数字だけにしてください（例 1200）",
    "item_season": "season_to は season_from と同じ日か、それより後にしてください",
}


def rule(kind: str) -> dict:
    return RULES.get(kind) or {}


def two_stage(kind: str) -> bool:
    return bool(rule(kind).get("two_stage"))


def private_by_default(kind: str) -> frozenset:
    return frozenset(rule(kind).get("private_by_default") or ())


def problems(kind: str, head: dict, body: str) -> list:
    check = rule(kind).get("check")
    return list(check(head, body)) if check else []
