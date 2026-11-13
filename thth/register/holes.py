"""穴の一覧と、聞いて埋める拍子（計画 段 3・叩き台 §4.3）。

穴（機械が見つける）:

| 穴の番号 | 何が足りないか | どの台帳 |
|---|---|---|
| `guess:<行>` | 推測のままの行（本人に聞いて認めてもらう） | どれも |
| `review:<行>` | 見直し日を過ぎた行 | どれも |
| `store:missing` | 店の行が無い | 店（`--kind store`） |
| `hours:missing` | 営業の時間の行が無い | 店 |
| `season:<季節>:<年>` | 季節の休み（年末年始・お盆・大型連休）の行が、その `SEASON_LEAD_DAYS` 日前を過ぎても無い | 店 |
| `voice:<品>` | 品に思いの 1 枚（`about: <品の番号>` の voice の行）が無い | どれも |
| `place:<場所>` | 外の場所の行の見直し日を過ぎた（段 4） | どれも |
| `unpasted:<場所>:<行>` | 柵を通った臨時・営業の時間・催しの今の中身を、その外の場所にまだ貼っていない（段 4） | どれも |

「本人が違うと言った」行と退役した行は穴にしない（同じことを二度聞かない）。

拍子（`ask`）: 1 日に `ASK_MAX_PER_DAY` 問まで・同じ穴は `ASK_GAP_DAYS` 日空ける。数は叩き台の案で、決め打ちにしない
（計画 段 3「上限の数を実績で決め直す」・忙しい店主には週 1 回にまとめる形も試す）。
聞く言葉は持ち主の LLM が書く。THTH が渡すのは穴の番号と、何を聞くかの手がかり（`hint`）だけ。
THTH は持ち主に直接知らせを送らない（聞くのは持ち主の LLM との会話の中だけ）。
"""
from __future__ import annotations

import datetime

from . import form, kinds

ASK_MAX_PER_DAY = 3
ASK_GAP_DAYS = 7
SEASON_LEAD_DAYS = 21
# (番号, 名前, 始まり (月, 日), 終わり (月, 日)・年をまたぐなら翌年)。
SEASONS = (("year_end", "年末年始", (12, 29), (1, 3)), ("obon", "お盆", (8, 13), (8, 16)),
           ("golden_week", "大型連休", (5, 3), (5, 5)))
# 手がかりの文（名前に HINTS・MESSAGES を使わない: 断りの説明の辞書として数えられるので）。
HOLE_TEXT = {
    "guess": "この行の中身が合っているかを、推しを添えて、はい・いいえで答えられる形で聞いてください",
    "review": "この行が今も合っているかを聞き、合っていれば review_by を先の日に直して認め直してください",
    "store": "店の名前と、外に出してよい連絡先（店のサイト・地図の URL）を聞いてください",
    "hours": "曜日ごとの営業の時間と定休日を聞いてください（「月 10:00-18:00」「水 休み」の形にします）",
    "season": "この季節に休みや時間の変更があるかを聞いてください（いつもどおりなら、それも 1 枚の行にできます）",
    "voice": "この品を置いた思い（なぜ選んだか・誰に届けたいか）を聞いてください",
    "place": "この外の場所の今の文を持ち主に写してもらい、thth register compare で台帳と比べてください",
    "unpasted": "この外の場所に、台帳の今の中身をまだ貼っていません。thth register paste で貼る文を出し、貼ったら thth register pasted で記録します",
}


def _season_range(code: str, year: int):
    for number, name, start, end in SEASONS:
        if number == code:
            first = datetime.date(year, *start)
            last = datetime.date(year + (1 if end < start else 0), *end)
            return name, first, last
    raise KeyError(code)


def _special_overlaps(rows: list, first: datetime.date, last: datetime.date) -> bool:
    for row in rows:
        if row.kind != "special" or row.problems or row.head.get("confirmed") == form.NO:
            continue
        start = kinds.parse_day(row.head.get("date"))
        end = kinds.parse_day(row.head.get("end_date")) or start
        if start and end and start <= last and end >= first:
            return True
    return False


def find(register_kind: str, rows: list, today: datetime.date, records: dict | None = None, now=None) -> list:
    """穴の並び（`{"hole", "row", "kind", "hint"}`）。並びは聞く順（推測 → 見直し → 欠け → 季節 → 思い）。"""
    live = [row for row in rows if not row.problems and row.head.get("status") != form.RETIRED]
    holes = []
    for row in live:
        if row.head.get("confirmed") == form.GUESS:
            holes.append({"hole": f"guess:{row.id}", "row": row.id, "kind": "guess"})
    for row in live:
        if row.head.get("confirmed") == form.YES and form.parse_day(row.head["review_by"]) < today:
            holes.append({"hole": f"review:{row.id}", "row": row.id, "kind": "review"})
    if register_kind == "store":
        present = {row.kind for row in rows if row.head.get("confirmed") != form.NO}
        for kind in ("store", "hours"):
            if kind not in present:
                holes.append({"hole": f"{kind}:missing", "row": None, "kind": kind})
        for year in (today.year, today.year + 1):
            for number, name, _start, _end in SEASONS:
                _name, first, last = _season_range(number, year)
                if first - datetime.timedelta(days=SEASON_LEAD_DAYS) <= today < first \
                        and not _special_overlaps(rows, first, last):
                    holes.append({"hole": f"season:{number}:{year}", "row": None, "kind": "season", "season": name,
                                  "from": first.isoformat(), "to": last.isoformat()})
    about = {row.head.get("about") for row in rows if row.kind == "voice" and row.head.get("confirmed") != form.NO}
    for row in live:
        if row.kind == "item" and row.id not in about:
            holes.append({"hole": f"voice:{row.id}", "row": row.id, "kind": "voice"})
    # 段 4: 見直し日を過ぎた外の場所（外の今の文と合っているかを持ち主の目で見る）と、まだ貼っていない変わり。
    from . import places
    for place in places.place_rows(rows):
        if form.parse_day(place.head["review_by"]) < today:
            holes.append({"hole": f"place:{place.id}", "row": place.id, "kind": "place"})
    if records is not None:
        for place, row in places.unpasted(rows, paste_rows(rows, records, now or _noon(today))):
            holes.append({"hole": f"unpasted:{place.id}:{row.id}", "row": row.id, "place": place.id, "kind": "unpasted"})
    for hole in holes:
        hole["hint"] = HOLE_TEXT[hole["kind"]]
    return holes


def _noon(day):
    from .. import jst
    return datetime.datetime.combine(day, datetime.time(12, 0), tzinfo=jst.JST)


def paste_rows(rows: list, records: dict, now) -> list:
    """外の場所に貼るべき今の行: 柵を通った臨時・営業の時間・催しのうち、きっかけの頃のもの（変わる日の無い営業の時間はいつも）。"""
    from . import fence, places, triggers
    out = []
    for row in rows:
        if row.kind not in places.PASTE_KINDS or fence.state(row, records, now.date()) is not None:
            continue
        found = triggers.window(row, now)
        if (found is None and row.kind == "hours") or (found is not None and found[2] == "due"):
            out.append(row)
    return out
