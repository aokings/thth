"""`before_you_post`: 投稿する前の問いに、**手元の account の水だけ**で答える
（設計 v2 §1「問いと答えの契約」・§6 v2-1）。

**泉はまだ無い。** これは泉（v2-5）と同じ**契約**を、手元の 4 アカウントの
実データだけで満たす口。`provenance.source` は `"local"` で固定——**泉から
汲んだと言わない。**

**何を渡すか**（設計 v2 §1）: 媒体・語（トピック）・型（`topics.KINDS`）・出す
予定の時刻帯・返信かどうか。**原稿本文は渡さない。**

**何が返るか**: §1 の JSON そのもの（`summary`・`expected`・`comparable`・
`cannot_say`・`one_thing_to_change`・`audience`・`provenance`）。

規約（この module が守るもの・§1 の 1〜4）:

1. **数値は単独で返さない。** `n`・期間（`window_days`）・揃えた条件
   （`aligned_on`）を必ず同梱する。
2. **n が `min_n` に満たない群は中央値を返さない。** 理由（`n=…`）を
   `cannot_say` に出す。**手元の水では、ほぼ全部 `cannot_say` になる**
   ——それが正直な答えで、売るために断定しない。
3. **`one_thing_to_change` は 1 個**（無ければ `null`）。比べられる群が 2 つ
   以上あって、中央値が 2 倍以上違うときだけ。**指図の語（「〜すべき」）は
   使わない**——事実の形で書く。
4. **本文・返信の本文・`username` は、入力にも出力にも入らない。**
   `audience` に出るのは観測の `audience`（自由文）と `observers`（人数）と
   `latest` だけ。
5. **媒体をまたがない**（設計 v2 §2.1）。`medium` で閉じる。

**読むだけ。何も書かない。API も git も触らない。**

数の出どころ（全部 L1・手元の台帳）:

- `branches_24h` … 返信の台帳（`thth/replies.py`）の**根の直下・他人の返信**の
  うち、投稿から 24 時間以内のもの。**24h の刻みが取れていない投稿は `null`**
  （`0` ではない・設計 v1 §3.2.2 規約 12）。**`own` が `None`（身内か判らない）
  の返信は他人に数えない**（`thth/threadshape.py` の `first_reply` と同じ流儀。
  `threadshape.author_reply_effect` の枝は不明を他人側に入れているが、
  **こちらは数えない**——泉の契約は「判らないものを判ったことにしない」が先）。
- `first_reply_min` … `thth/threadshape.py` の投稿ごとの値をそのまま使う。
- `views_24h` … `thth/measured.py` の実測から 24h の刻みの行を拾い、
  `account_report.comparable_views()` に通したものだけ（**実経過 24〜30h の帯**）。
"""
from __future__ import annotations

import datetime
import statistics

from . import account_report as account_report_mod
from . import accounts as accounts_mod
from . import adapters as adapters_mod
from . import jst as jst_mod
from . import measured as measured_mod
from . import queuefile as queuefile_mod
from . import replies as replies_mod
from . import threadshape as threadshape_mod
from . import topics as topics_mod

# **答えの形の版**（設計 v2 §1 `provenance.schema`）。形を変えたら上げる
# ——読み手が黙って古い形のまま通らないように。
SCHEMA = 1

# 出所。**泉ではない**（v2-5 が立つまで `"local"` から動かさない）。
SOURCE_LOCAL = "local"

# 設計 v2 §7 裁定 4。`n >= 20` で中央値を返す・期間の既定は 30 日。
DEFAULT_MIN_N = 20
DEFAULT_WINDOW_DAYS = 30

# 揃えた条件（設計 v2 §1 規約 1）。`marks` の名前ではなく**実経過**で揃える
# （`thth/account_report.py:AGE_BAND_HOURS`）。
ALIGNED_ON = "age_hours=24"
VIEWS_MARK = 24

# 観測の鮮度（設計 v2 §2）。これを過ぎた観測は答えに出すが理由を添える。
OBSERVATION_STALE_DAYS = 90

# 時刻帯の名前（`thth/threadshape.py:HOUR_BANDS` を正とする——ここで書き写すと
# 分ける側と聞く側が黙って割れる）。
HOUR_BAND_NAMES = tuple(name for name, _lo, _hi in threadshape_mod.HOUR_BANDS)

# 指標の並びと日本語の名前。**`expected` の鍵は §1 の固定名。**
METRICS = (
    ("branches_24h", "24h の枝", "本"),
    ("first_reply_min", "最初の枝まで", "分"),
    ("views_24h", "views（24h）", ""),
)


class AskError(Exception):
    """**問いが受け取れない**（未知の媒体・型・時刻帯・語が空・期間が 0 以下）。

    **黙って空の答えを返さない**（loud reject・作法 §5）。知っている一覧を
    添えて断る——エージェントは説明文と 1 回の失敗で呼び方を直す。
    """


def _reject(message: str) -> None:
    raise AskError(message)


def _num(value):
    """見せる数。**整数で表せるなら整数、それ以外は小数第 1 位まで。**"""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if float(value).is_integer() else round(value, 1)
    return value


def _date_of(value) -> str | None:
    """`2026-09-11T…` から `2026-09-11` だけ。読めなければ `None`。"""
    dt = threadshape_mod.parse_time(value)
    if dt is not None:
        return dt.date().isoformat()
    if isinstance(value, str) and len(value) >= 10 and value[4] == "-" and value[7] == "-":
        return value[:10]
    return None


def _stat(values: list, *, min_n: int) -> dict:
    """`{"median", "p25", "p75", "n"}`。**n < min_n なら中央値を返さない**（規約 2）。

    **`n` は必ず出す**——「言えない」ことと「1 件も無い」ことを読み分けるため。
    """
    n = len(values)
    out = {"median": None, "p25": None, "p75": None, "n": n}
    if n < min_n:
        return out
    ordered = sorted(values)
    q25, _q50, q75 = statistics.quantiles(ordered, n=4, method="inclusive")
    out["median"] = _num(statistics.median(ordered))
    out["p25"] = _num(q25)
    out["p75"] = _num(q75)
    return out


# ---------------------------------------------------------------- 手元の水

def _branches_24h(rows: list, root_id: str, posted_at, *, covered: bool):
    """根の直下の**他人**の返信のうち、投稿から 24 時間以内の数。

    **取れていない刻みは `null`**（`0` ではない）。`posted_at` が読めない・
    24h の取得がまだ無い投稿は「枝が 0 本だった」ではなく「まだ言えない」。
    """
    if posted_at is None or not covered:
        return None
    limit = posted_at + datetime.timedelta(hours=VIEWS_MARK)
    count = 0
    for row in rows:
        if threadshape_mod.ref_id(row.get("replied_to")) != root_id:
            continue
        # **判らないものを他人にしない**（規約 12）。`own` が `None` の返信は
        # 数えない（数えなかったこと自体は `threadshape` の `own_unknown`）。
        if row.get("own") is not False:
            continue
        ts = threadshape_mod.parse_time(row.get("timestamp"))
        if ts is None or ts > limit:
            continue
        count += 1
    return count


def _views_observation(post: dict) -> dict | None:
    """実測の 1 投稿から、24h の刻みの**観測**（数値だけでなく出所と時間条件も）。

    `thth/account_report.py:comparable_views()` に通せる形にして返す。**通す
    のはそちら**——「刻みの名前で揃えたつもり」を防ぐ関門は 1 つに保つ。
    """
    best = None
    for row in post.get("rows") or []:
        if VIEWS_MARK in (row.get("marks") or []):
            best = row
    if best is None:
        return None
    return {
        "views": (best.get("metrics") or {}).get("views"),
        "medium": post.get("medium"),
        "mark": VIEWS_MARK,
        "age_hours": best.get("age_hours"),
        "collected_at": best.get("collected_at"),
        "post_id": post.get("post_id"),
        "source": account_report_mod.LEDGER_SOURCE,
        "topic_source": account_report_mod.DRAFT_TOPIC,
    }


def _population(account_name: str, *, default_medium: str | None) -> list:
    """手元の台帳を 1 投稿 1 行に並べる。**判断はしない。数えるだけ。**

    3 つの台帳を post_id で突き合わせる:

      - `thth/threadshape.py` … 型・時刻帯・最初の枝・24h の刻みが取れているか
      - `thth/measured.py`    … 媒体・返信かどうか・24h の views
      - `thth/replies.py`     … 根の直下の返信（枝を 24 時間で切るため）
    """
    shape = threadshape_mod.load(account_name)
    shape_by_id = {p["post_id"]: p for p in shape["posts"]}

    try:
        measured = measured_mod.load(account_name)
    except accounts_mod.AccountError:
        measured = {"posts": []}
    measured_by_id = {p["post_id"]: p for p in measured.get("posts") or []}

    # **揃った views だけ**（設計 v2 §1 規約 1・`comparable_views` の関門）。
    観測 = [o for o in (_views_observation(p) for p in measured_by_id.values())
            if o is not None]
    使う, _使わない = account_report_mod.comparable_views(観測, mark=VIEWS_MARK)
    views_by_id = {o["post_id"]: o["views"] for o in 使う}

    ledger = replies_mod.load(account_name)
    rows_by_id: dict = {}
    for row in ledger["replies"]:
        pid = row.get("post_id")
        if pid:
            rows_by_id.setdefault(str(pid), []).append(row)

    kind_cache: dict = {}

    def kind_of(topic):
        if not topic:
            return None
        if topic not in kind_cache:
            try:
                kind_cache[topic] = topics_mod.kind_of(topic, account_name)
            except topics_mod.ShelfBroken:
                kind_cache[topic] = None
        return kind_cache[topic]

    out = []
    for post_id in sorted(set(shape_by_id) | set(measured_by_id)):
        s = shape_by_id.get(post_id) or {}
        m = measured_by_id.get(post_id) or {}
        topic = queuefile_mod.normalize_topic(s.get("topic") or m.get("topic"))
        posted_at = threadshape_mod.parse_time(s.get("posted_at") or m.get("posted_at"))
        covered = bool(((s.get("fetch_coverage") or {}).get(str(VIEWS_MARK)) or {})
                       .get("covered"))
        # **返信かどうかは、判る投稿でだけ判る。** 実測の台帳が無い投稿は
        # `None`（「返信ではない」ではない）。
        is_reply = None if not m else bool(m.get("reply_to"))
        out.append({
            "post_id": post_id,
            # 採取時点の媒体が正。無い行（設計 v2 §4.2 より前）は台帳の `media`。
            "medium": m.get("medium") or s.get("medium") or default_medium,
            "topic": topic,
            "kind": kind_of(topic),
            "posted_at": posted_at,
            "hour_band": threadshape_mod.hour_band(posted_at),
            "is_reply": is_reply,
            "branches_24h": _branches_24h(rows_by_id.get(post_id, []), post_id,
                                          posted_at, covered=covered),
            "first_reply_min": s.get("first_reply_min"),
            "views_24h": views_by_id.get(post_id),
        })
    return out


# ---------------------------------------------------------------- 1 つだけ

_HOUR_BAND_LABEL = {name: f"{lo}〜{hi}時"
                    for name, lo, hi in threadshape_mod.HOUR_BANDS}


def _one_thing_to_change(base: list, *, min_n: int):
    """**時刻帯どうしの差**を 1 つだけ（設計 v2 §1 規約 3）。無ければ `None`。

    条件は 3 つ全部:

      - 比べられる時刻帯が **2 つ以上**あり
      - どちらも **n >= min_n** で中央値が出ていて
      - 中央値が **2 倍以上**違う

    **指図はしない。** 「朝に回す」ではなく「深夜は 180 分、朝は 42 分」。
    どちらを選ぶかは人とエージェントの仕事で、この口の仕事ではない。
    """
    bands: dict = {}
    for post in base:
        if post["hour_band"]:
            bands.setdefault(post["hour_band"], []).append(post)

    best = None
    for metric, label, unit in METRICS:
        stats = {}
        for band, posts in bands.items():
            values = [p[metric] for p in posts if isinstance(p[metric], (int, float))]
            if len(values) >= min_n:
                stats[band] = (statistics.median(values), len(values))
        if len(stats) < 2:
            continue
        hi = max(stats.items(), key=lambda kv: kv[1][0])
        lo = min(stats.items(), key=lambda kv: kv[1][0])
        if lo[1][0] <= 0 or hi[1][0] / lo[1][0] < 2:
            continue
        cand = (hi[1][0] / lo[1][0], metric, label, unit, hi, lo)
        if best is None or cand[0] > best[0]:
            best = cand

    if best is None:
        return None
    _ratio, _metric, label, unit, hi, lo = best
    単位 = f" {unit}" if unit else ""
    return (f"{hi[0]}（{_HOUR_BAND_LABEL.get(hi[0], hi[0])}）は{label}中央値 "
            f"{_num(hi[1][0])}{単位}（n={hi[1][1]}）。"
            f"{lo[0]}（{_HOUR_BAND_LABEL.get(lo[0], lo[0])}）は "
            f"{_num(lo[1][0])}{単位}（n={lo[1][1]}）")


# ---------------------------------------------------------------- 答える

def before_you_post(account: str, *, medium: str | None = None, topic: str,
                    kind: str | None = None, hour_band: str | None = None,
                    is_reply: bool = False,
                    window_days: int = DEFAULT_WINDOW_DAYS,
                    min_n: int = DEFAULT_MIN_N, now=None) -> dict:
    """設計 v2 §1 の答えを 1 つ返す。**読むだけ。**

    `medium` を省くと台帳の `media`（**媒体をまたがない**ための既定）。
    `kind` は語から決まる（`topics.kind_of()`）ので、渡した型がその語の型と
    違えば群は空になる——**黙って型を無視して答えない。**

    `AskError`（問いが受け取れない）と `accounts.AccountError`（台帳が無い・
    壊れている）を投げる。**空の答えでごまかさない。**
    """
    account_cfg = accounts_mod.load_account(account)
    medium = medium or account_cfg.get("media")

    if not isinstance(medium, str) or medium not in adapters_mod.REGISTRY:
        _reject(f"知らない媒体です: {medium!r}。"
                f"知っている媒体: {'・'.join(sorted(adapters_mod.REGISTRY))}")
    topic = queuefile_mod.normalize_topic(topic)
    if not topic:
        _reject("語（--topic）が空です。**語の無い問いには答えません**"
                "——比べる群が決まりません")
    if kind is not None and kind not in topics_mod.KINDS:
        _reject(f"知らない型です: {kind!r}。"
                f"知っている型: {'・'.join(topics_mod.KINDS)}")
    if hour_band is not None and hour_band not in HOUR_BAND_NAMES:
        _reject(f"知らない時刻帯です: {hour_band!r}。"
                f"知っている時刻帯: {'・'.join(HOUR_BAND_NAMES)}")
    if not isinstance(window_days, int) or window_days <= 0:
        _reject(f"期間（--window-days）は 1 以上の整数です: {window_days!r}")
    if not isinstance(min_n, int) or min_n < 1:
        _reject(f"閾値（--min-n）は 1 以上の整数です: {min_n!r}")

    now = now if now is not None else jst_mod.now_jst()
    since = now - datetime.timedelta(days=window_days)

    population = _population(account, default_medium=account_cfg.get("media"))

    # **捨てない・数える**（設計 v1 §3.2.2）。除いた理由ごとに件数を持つ。
    除いた = {"medium": 0, "window": 0, "posted_at": 0, "reply_unknown": 0}
    base = []
    for post in population:
        # **媒体で閉じる**（設計 v2 §2.1）。Threads の 24 時間と Bluesky の
        # 24 時間は別の数。
        if post["medium"] != medium:
            除いた["medium"] += 1
            continue
        if post["posted_at"] is None:
            除いた["posted_at"] += 1
            continue
        if post["posted_at"] < since:
            除いた["window"] += 1
            continue
        if post["topic"] != topic:
            continue
        if kind is not None and post["kind"] != kind:
            continue
        if post["is_reply"] is None:
            # **判らないものを「返信ではない」にしない**（規約 12）。
            除いた["reply_unknown"] += 1
            continue
        if post["is_reply"] != bool(is_reply):
            continue
        base.append(post)

    # 時刻帯は最後に切る——`one_thing_to_change` は**帯どうしの差**なので、
    # 帯で絞る前の群が要る。
    group = [p for p in base if hour_band is None or p["hour_band"] == hour_band]

    cannot_say: list = []
    expected: dict = {}
    for metric, label, _unit in METRICS:
        values = [p[metric] for p in group if isinstance(p[metric], (int, float))]
        stat = _stat(values, min_n=min_n)
        expected[metric] = stat
        if stat["median"] is None:
            cannot_say.append(f"{label}: n={stat['n']}（{min_n} 未満）")

    if 除いた["medium"]:
        cannot_say.append(f"媒体違いで数えなかった投稿 {除いた['medium']} 件"
                          f"（{medium} 以外。媒体をまたいで比べません）")
    if 除いた["window"]:
        cannot_say.append(f"期間外で数えなかった投稿 {除いた['window']} 件"
                          f"（直近 {window_days} 日の外）")
    if 除いた["posted_at"]:
        cannot_say.append(f"`posted_at` が読めず数えなかった投稿 {除いた['posted_at']} 件")
    if 除いた["reply_unknown"]:
        cannot_say.append(f"返信かどうかが判らず数えなかった投稿 "
                          f"{除いた['reply_unknown']} 件（実測の台帳がありません）")

    # ---- 観測（誰がいるか）。**名前は出さない。人数と自由文と日付だけ。**
    try:
        観測 = topics_mod.observation(topic)
    except topics_mod.ShelfBroken as e:
        観測 = []
        cannot_say.append(f"観測の棚が読めません（{e.detail}）")

    observers = len(観測)
    audience: list = []
    latest_date = None
    if 観測:
        newest = 観測[0]
        latest_date = _date_of(newest.get("checked_at"))
        audience.append({
            "topic": topic,
            # **自由文の `audience` だけ。** 観測者の名前（account・by）は出さない。
            "who": (newest.get("audience") or None),
            "observers": observers,
            "latest": latest_date,
        })
        if latest_date:
            古い = threadshape_mod.parse_time(newest.get("checked_at"))
            if 古い is not None and (now - 古い).days > OBSERVATION_STALE_DAYS:
                cannot_say.append(
                    f"観測が古い（最終 {latest_date}・{OBSERVATION_STALE_DAYS} 日超）")

    one_thing = _one_thing_to_change(base, min_n=min_n)

    # `updated` は**手元の水がいつまでのものか**。実測の投稿と観測の新しいほう。
    posted_dates = [p["posted_at"].date().isoformat() for p in base]
    updated = max([d for d in posted_dates + [latest_date] if d], default=None)

    return {
        "summary": _summary(topic=topic, kind=kind, hour_band=hour_band,
                            medium=medium, window_days=window_days,
                            min_n=min_n, n=len(group), expected=expected,
                            is_reply=bool(is_reply)),
        "expected": expected,
        "comparable": {"n": len(group), "window_days": window_days,
                        "aligned_on": ALIGNED_ON, "medium": medium},
        "cannot_say": cannot_say,
        "one_thing_to_change": one_thing,
        "audience": audience,
        "provenance": {"source": SOURCE_LOCAL, "observers": observers,
                        "updated": updated, "schema": SCHEMA},
    }


def _summary(*, topic, kind, hour_band, medium, window_days, min_n, n,
             expected, is_reply) -> str:
    """要約 1 行。**引用できる形**（設計 v2 §1 規約 5）——語・条件・n・期間つき。

    **指図はしない。** 言えることだけ並べ、言えないときは言えないと書く。
    """
    条件 = [topic]
    if kind:
        条件.append(f"{kind}型")
    条件.append(hour_band if hour_band else "時刻帯すべて")
    条件.append("返信" if is_reply else "返信ではない投稿")
    頭 = f"{'・'.join(条件)}（{medium}・直近 {window_days} 日・n={n}）: "

    言えた = []
    b, f, v = (expected["branches_24h"], expected["first_reply_min"],
               expected["views_24h"])
    if b["median"] is not None:
        言えた.append(f"24h で枝 中央値 {b['median']} 本（n={b['n']}）")
    if f["median"] is not None:
        言えた.append(f"最初の枝は中央値 {f['median']} 分（n={f['n']}）")
    if v["median"] is not None:
        言えた.append(f"24h の views 中央値 {v['median']}（n={v['n']}）")
    if not 言えた:
        return (頭 + f"言える中央値がありません（{min_n} 件から答えます。"
                     "理由は cannot_say）")
    return 頭 + "、".join(言えた)
