"""外の場所に手で貼る束（計画 段 4・叩き台 §10）。

店の情報が載っている外の場所（Google マップ・食べログなど）ごとに、台帳に 1 枚の行（kind `place`）を置く:

```
---
id: place-tabelog
kind: place
status: 本採用
source: owner
confirmed: 推測
review_by: 2027-03-01          # 外の場所の今の文を見直す日
public: false                  # 場所の行そのものは外に出さない
place: tabelog                 # `PLACES` の番号
url: https://…                 # 店の頁（THTH はここに何も送らない・読まない）
admin_by: 店主                 # 管理画面を誰が持つか
synced_on: 2026-12-08          # THTH が書く（貼ったと記録した日）
synced_row: special-x@…        # THTH が書く（どの行のどの中身を貼ったか・, で区切る）
---
```

- **THTH は外の場所の頁を読まず、管理画面にも入らない。** 貼る文を作るだけで、貼るのは持ち主（計画 段 4 の理由:
  食べログ・Retty の規約・ホットペッパーグルメの API の決まり）。この部分は網の部品を import しない。
- 貼る文の材料は柵（`fence.usable`）を通った行だけで、外に出してよい欄だけを使う（`draft.body_lines` と同じ）。
- 字数の目安（`max_chars`）は【推測】（各社の管理画面の欄の字数は確かめていない・計画 §9 の 7）。超えたら
  「収まらない」と言うだけで、文は切らない（切ると事実が欠けるので）。
- 持ち主が外の場所の今の文を写して貼れば、台帳の日付と時刻と比べて違いを見せる（叩き台 §10.1 の A'）。
  どちらが正しいかは機械が決めず、持ち主に聞く。貼られた文は残さない。
"""
from __future__ import annotations

import re

from . import draft, form, kinds

PASTE_KINDS = ("special", "hours", "event")
SYNCED_MAX = 20
COMPARE_MAX = 5000
# 場所ごとの欄と字数の目安（どれも【推測】・本物の店の管理画面で確かめて直す）。
PLACES = {
    "google_maps": {"label": "Google マップ（Google ビジネス プロフィール）", "max_chars": 1500,
                    "field": {"special": "営業時間の「特別営業時間」と、最新情報の投稿", "hours": "営業時間の欄",
                              "event": "最新情報の投稿（イベント）"}},
    "tabelog": {"label": "食べログ", "max_chars": 200,
                "field": {"special": "店舗会員の管理画面のお知らせ・営業時間の欄", "hours": "営業時間の欄",
                          "event": "店舗会員の管理画面のお知らせ"}},
    "hotpepper": {"label": "ホットペッパーグルメ", "max_chars": 200,
                  "field": {"special": "店の管理画面のお知らせ・営業時間の欄", "hours": "営業時間の欄",
                            "event": "店の管理画面のお知らせ"}},
    "retty": {"label": "Retty", "max_chars": 200,
              "field": {"special": "店の管理画面の営業時間の欄", "hours": "営業時間の欄", "event": "店の管理画面のお知らせ"}},
    "gurunavi": {"label": "楽天ぐるなび", "max_chars": 200,
                 "field": {"special": "店の管理画面のお知らせ", "hours": "営業時間の欄", "event": "店の管理画面のお知らせ"}},
    "ikyu": {"label": "一休.comレストラン", "max_chars": 200,
             "field": {"special": "店の管理画面のお知らせ", "hours": "営業時間の欄", "event": "店の管理画面のお知らせ"}},
    "instagram": {"label": "Instagram のプロフィール", "max_chars": 150,
                  "field": {"special": "プロフィールの文", "hours": "プロフィールの文", "event": "プロフィールの文"}},
    "other": {"label": "ほかの場所", "max_chars": 200,
              "field": {"special": "お知らせの欄", "hours": "営業時間の欄", "event": "お知らせの欄"}},
}
_DATE = re.compile(r"(\d{1,2})\s*[月/／]\s*(\d{1,2})")
_TIME = re.compile(r"(\d{1,2})[:：](\d{2})")


def check_place(head: dict, body: str) -> list:
    problems = []
    if head.get("place") not in PLACES:
        problems.append("place_name")
    url = head.get("url")
    if url is not None and not re.fullmatch(r"https://[!-~]{1,2040}", url):
        problems.append("place_url")
    if "synced_on" in head and form.parse_day(head["synced_on"]) is None:
        problems.append("date_form:synced_on")
    if "synced_row" in head and synced(head) is None:
        problems.append("place_synced_row")
    return problems


def synced(head: dict) -> dict | None:
    """`synced_row` を `{行: 指紋}` に（無ければ空・形が違えば None）。"""
    raw = head.get("synced_row")
    if not raw:
        return {}
    out = {}
    for part in raw.split(","):
        row_id, sep, digest = part.strip().partition("@")
        if not sep or not form.ID.fullmatch(row_id) or not re.fullmatch(r"[0-9a-f]{16}", digest):
            return None
        out[row_id] = digest
    return out


def synced_text(entries: dict) -> str:
    items = list(entries.items())[-SYNCED_MAX:]
    return ",".join(f"{row_id}@{digest}" for row_id, digest in items)


def lines_for(row: form.Row) -> list:
    """貼る文の行（外に出してよい欄だけ）。"""
    if row.kind == "special":
        return ["【臨時】" + draft.body_lines("special_soon", row)[0]]
    if row.kind == "event":
        return ["【催し】" + " ".join(draft.body_lines("event_soon", row))]
    if row.kind == "hours":
        if row.head.get("effective_from"):
            return draft.body_lines("hours_change", row)
        days, notes, _problems = kinds.hours_lines(row.body)
        out = []
        for day in kinds.WEEKDAYS:
            value = days.get(day)
            out.append(f"{day} 休み" if value == "closed" else f"{day} " + ", ".join(draft._range_text(a, b) for a, b in value))
        return out + [draft.clean(note, 120) for note in notes]
    return []


def place_rows(rows: list) -> list:
    return [row for row in rows if row.kind == "place" and not row.problems
            and row.head.get("status") != form.RETIRED and row.head.get("confirmed") != form.NO]


def bundle(row: form.Row, places: list) -> list:
    """場所ごとの貼る文と、貼ったかの点検。"""
    text = "\n".join(lines_for(row))
    digest = form.fingerprint(row.text)
    out = []
    for place in places:
        info = PLACES[place.head["place"]]
        done = (synced(place.head) or {}).get(row.id) == digest
        out.append({"place": place.id, "label": place.head.get("name") or info["label"],
                    "field": info["field"][row.kind], "url": place.head.get("url"), "admin_by": place.head.get("admin_by"),
                    "text": text, "chars": len(text), "max_chars": info["max_chars"],
                    "fits": len(text) <= info["max_chars"], "pasted": done})
    return out


def unpasted(rows: list, usable_rows: list) -> list:
    """`(場所, 行)` の並び: 柵を通った貼るべき行のうち、その場所に今の中身をまだ貼っていない物。"""
    out = []
    for place in place_rows(rows):
        entries = synced(place.head) or {}
        for row in usable_rows:
            if entries.get(row.id) != form.fingerprint(row.text):
                out.append((place, row))
    return out


def facts_of(row: form.Row) -> dict:
    """台帳の行の日付（月, 日）と時刻（時, 分）の集まり（比べるため）。"""
    text = "\n".join(lines_for(row))
    return {"dates": {(int(m), int(d)) for m, d in _DATE.findall(text)},
            "times": {(int(h), int(mi)) for h, mi in _TIME.findall(text)}, "closed": "休" in text}


def compare(row: form.Row, outside: str) -> dict:
    """外の場所の今の文（持ち主が写して貼った物）と台帳の行の、日付と時刻の違い。どちらが正しいかは決めない。"""
    mine = facts_of(row)
    theirs = {"dates": {(int(m), int(d)) for m, d in _DATE.findall(outside)},
              "times": {(int(h), int(mi)) for h, mi in _TIME.findall(outside)}, "closed": "休" in outside}

    def show(values, kind):
        return sorted(f"{a}月{b}日" if kind == "dates" else f"{a:02d}:{b:02d}" for a, b in values)

    out = {"only_register": {k: show(mine[k] - theirs[k], k) for k in ("dates", "times")},
           "only_outside": {k: show(theirs[k] - mine[k], k) for k in ("dates", "times")}}
    if mine["closed"] and not theirs["closed"]:
        out["only_register"]["closed"] = True
    out["same"] = not any(out["only_register"].values()) and not any(out["only_outside"].values())
    return out
