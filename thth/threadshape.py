"""スレッドの**形**を返信の台帳から計算する（設計 v2 §2「スレッドの形」・§6 v2-0）。

**なぜ要るか。** masaru の直感——「Threads はスレッドがどう伸び、どう枝を産むかを
気にしているに違いない」——を、泉（v2-5）を作る前に**手元の実データで先に確かめる**
ための口。`thth/collect.py` の `/conversation` は既に `replied_to`・`root_post`・
`timestamp`・`username` を全階層で採っている（設計 v2 §2 の表・L1）。**枝・深さ・
参加者・最初の返信までの分は、ここから計算するだけ**で、新しく採る必要はない。

**読むだけ。何も書かない。API も git も触らない。**

規約（この module が守るもの）:

- **取れていない刻みは `null`。`0` ではない**（設計 v1 §3.2.2・規約 12）。「1 件も
  返信が来なかった」と「その刻みでまだ採っていない」は別のこと。刻みが取れて
  いるかは、返信の台帳の `kind: "fetch"` の行（`marks`・`age_hours`）から決める。
- **n を必ず添える**（設計 v2 §1 規約 1）。平均・中央値を単独で出さない。
- **n < 3 は「言えない」**（設計 v2 §1 規約 2）。要約は `cannot_say` に理由ごと出す。
- **媒体をまたいで集計しない**（設計 v2 §2.1）。この口は 1 account だけを扱い、
  出力に `medium` を刻む。
- **指図をしない。** 「〜すべき」は 1 行も出さない。事実と分母だけ。
- **判らないものを「違う」にしない**（規約 12・`thth/replies.py` と同じ流儀）。
  `own` が `None`（身内か判らない）の返信は「他人」に数えない。数えなかったこと
  自体は `own_unknown` に出す。

**因果ではない。** `author_reply_effect` は「作者が返した枝」と「返さなかった枝」の
**相関**であって、返したから伸びたという証拠ではない（枝が伸びていたから作者が
返した、という向きも同じだけありうる）。名前に `effect` と入っているが、出すのは
2 群の平均と n だけで、差の検定も推奨も出さない。
"""
from __future__ import annotations

import datetime
import statistics

from . import accounts as accounts_mod
from . import collect as collect_mod
from . import measured as measured_mod
from . import replies as replies_mod
from . import topics as topics_mod

# 採集の刻み。**`thth/collect.py` の `AGE_MARKS_HOURS` を正とする**——ここで
# 数字を書き写すと、採る側と読む側が黙って割れる。
MARKS = tuple(collect_mod.AGE_MARKS_HOURS)

# **n がここに満たない群は「言えない」**（設計 v2 §1 規約 2）。§1 の閾値
# （中央値は n>=20）とは別の、v2-0 の手元 4 アカウント用の下限。
MIN_N = 3

# `posted_at` の JST 時台を 4 区分にする。**境界は左閉右開**（`lo <= h < hi`）。
# `深夜` だけ日をまたぐ（22 時〜翌 5 時）。
HOUR_BANDS = (
    ("朝", 5, 11),
    ("昼", 11, 17),
    ("夕", 17, 22),
    ("深夜", 22, 5),
)


def hour_band(dt: datetime.datetime | None) -> str | None:
    """JST の時台を 4 区分のどれかに落とす。読めなければ `None`（推測で埋めない）。"""
    if dt is None:
        return None
    h = dt.hour
    for name, lo, hi in HOUR_BANDS:
        if lo < hi:
            if lo <= h < hi:
                return name
        elif h >= lo or h < hi:  # 日をまたぐ帯
            return name
    return None


def _parse_time(value) -> datetime.datetime | None:
    """台帳の時刻を aware datetime にする。**読めなければ `None`**（0 にしない）。

    形が 2 通りある: `posted_at` は `2026-09-12T08:08:26+09:00`（THTH が書く）、
    返信の `timestamp` は `2026-09-11T23:32:48+0000`（Threads API の生の形）。
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    for parse in (datetime.datetime.fromisoformat,
                  lambda s: datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z"),
                  lambda s: datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%f%z")):
        try:
            dt = parse(text)
        except ValueError:
            continue
        # **naive を「いまの機械の時間帯」で補わない。** 時間帯が無い記録は
        # UTC とみなす（`thth/jst.py:to_jst()` と同じ約束）。
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=datetime.timezone.utc)
    return None


def _ref_id(value):
    """`replied_to` / `root_post` の中の id。

    **媒体で形が 2 通りある**（`thth/adapters/base.py:109`）。Threads は API の
    生の行なので `{"id": "..."}`、Bluesky・Mastodon は境界の `Message` を写した
    ものなので文字列そのもの。どちらも通す。
    """
    if isinstance(value, dict):
        v = value.get("id")
        return str(v) if v not in (None, "") else None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _fetch_covered(fetches: list) -> dict:
    """刻みごとに「その刻みまでの返信を、実際に見た取得があるか」を決める。

    **`0` と `null` を分けるのはここ**（規約 12）。返り値は
    `{mark: {"covered": bool, "covered_by": ..., "collected_at": ..., ...}}`。

    2 通りの根拠を認める。**どちらで通したかを必ず出す**（黙って通さない）:

    - `by_marks` … その取得が「この刻みとして」採られている（`marks` に入っている）。
      これが本筋。
    - `by_age`  … `marks` には入っていないが、投稿から刻み以上経ってから採った
      取得（`--refresh` 等）がある。その時点の会話を見ているので、**その刻みまでに
      届いていた返信は、その取得で見えている**。

    `marks_collapsed`（`thth/measured.py:_mark_collapsed()` と同じ意味）は、
    1 回の取得に 2 つ以上の刻みが同居していたという印——**過去のその時点を
    復元したわけではない**（25 時間後の 1 回で 1h・6h・24h が付いても、測ったのは
    1 点）。返信の累計は「その刻みまでに届いていた分」を `timestamp` から数え直す
    ので値そのものは狂わないが、**削除された返信は見えない**ので印を残す。
    """
    out = {}
    for mark in MARKS:
        best = None
        for row in fetches:
            marks = row.get("marks") or []
            age = row.get("age_hours")
            by_marks = mark in marks
            by_age = isinstance(age, (int, float)) and age >= mark
            if not (by_marks or by_age):
                continue
            cand = {
                "covered": True,
                "covered_by": "marks" if by_marks else "age",
                "collected_at": row.get("collected_at"),
                "age_hours": age,
                "marks_collapsed": len(marks) >= 2,
                "trigger": row.get("trigger"),
            }
            # **いちばん早くその刻みを満たした取得**を採る（`marks` の根拠を
            # 優先する。同じ根拠なら `collected_at` が早いほう）。
            if best is None:
                best = cand
            elif best["covered_by"] != "marks" and by_marks:
                best = cand
            elif best["covered_by"] == cand["covered_by"] and \
                    (cand["collected_at"] or "") < (best["collected_at"] or ""):
                best = cand
        out[mark] = best or {
            "covered": False, "covered_by": None, "collected_at": None,
            "age_hours": None, "marks_collapsed": False, "trigger": None,
            "reason": f"{mark}h の刻みの取得がまだありません",
        }
    return out


def _median(values: list):
    """中央値。**空なら `None`**（0 で埋めない）。"""
    return statistics.median(values) if values else None


def _stat(values: list) -> dict:
    """`{"median": ..., "n": ...}`。**n < MIN_N なら中央値は `None`**（規約 2）。

    **n は必ず出す**——「言えない」ことと「群が空である」ことを読み分けられる
    ようにするため。
    """
    n = len(values)
    return {"median": _median(values) if n >= MIN_N else None, "n": n}


def _mean_with_n(values: list) -> dict:
    """`{"mean": ..., "n": ...}`。**n < MIN_N なら平均は `None`**（規約 2）。"""
    n = len(values)
    return {"mean": (sum(values) / n) if n >= MIN_N else None, "n": n}


def _tree(root_id: str, rows: list) -> dict:
    """返信の親子関係を組む。**根に繋がらない行を、根の直下に寄せない。**

    戻り値:
      - `nodes`: `{reply_id: {"row":..., "parent":..., "depth":..., "branch":...}}`
        `depth` は根からの階層（直下が 1）。**根まで辿り着けない行は
        `depth: None`・`branch: None`**（`orphans` に名前が出る）。
      - `orphans`: 親が台帳に無い（頁の境界・削除・採り漏れ）行の id
      - `duplicate_ids`: 同じ id が 2 行以上あった場合の id（**先の行を採る**）
    """
    nodes, orphans, duplicates = {}, [], []
    for row in rows:
        rid = collect_mod.reply_row_id(row)
        if not rid:
            continue
        rid = str(rid)
        if rid in nodes:
            duplicates.append(rid)
            continue
        nodes[rid] = {"row": row, "parent": _ref_id(row.get("replied_to")),
                       "depth": None, "branch": None}

    for rid in nodes:
        # 根まで歩いて階層を数える。**辿れなければ `None` のまま**（推測しない）。
        seen, cur, chain = set(), rid, []
        while True:
            if cur is None or cur in seen:
                # 親が無い／循環している。**根に繋がらないので孤児。**
                orphans.append(rid)
                break
            if cur == root_id:
                # `chain` は「根に近いほう」から積み上がっていないので、
                # 直下の枝は最後に積んだもの。
                for depth, node_id in enumerate(reversed(chain), start=1):
                    nodes[node_id]["depth"] = depth
                    nodes[node_id]["branch"] = chain[-1]
                break
            if cur not in nodes:
                orphans.append(rid)
                break
            seen.add(cur)
            chain.append(cur)
            cur = nodes[cur]["parent"]

    return {"nodes": nodes, "orphans": sorted(set(orphans)),
            "duplicate_ids": sorted(set(duplicates))}


def _participants(rows_other: list) -> dict:
    """他人の返信者の異なり数と、いちばん多い 1 人の占有率。

    **分母を必ず出す**（設計 v2 §1 規約 1）。`username` が無い行は数えられない
    ので、異なり数にも占有率にも入れず `username_missing` に出す。
    """
    counts: dict = {}
    missing = 0
    for row in rows_other:
        name = row.get("username")
        if not isinstance(name, str) or not name.strip():
            missing += 1
            continue
        key = name.strip().lstrip("@").lower()
        counts[key] = counts.get(key, 0) + 1
    total = sum(counts.values())
    top = max(counts.values()) if counts else 0
    return {
        "count": len(counts),
        "denominator": total,
        "top_replies": top,
        "top_share": (top / total) if total else None,
        "username_missing": missing,
    }


def _author_reply_effect(root_id: str, tree: dict) -> dict:
    """作者が返した枝と、返さなかった枝の「その後の返信数」。

    **定義をここに書き切る**（数字だけ見て別の意味に読まれないように）:

    - **枝** … 根に直接ぶら下がる返信のうち、**作者以外**が書いたもの
      （作者自身の連投は枝に数えない。`self_branches` に数だけ出す）。
    - **その後の返信数** … その枝の下に付いた**他人**の返信の数。作者が返した枝
      では**作者の最初の返信より後**（`timestamp` で比較）のものだけ、返さなかった
      枝では下に付いた他人の返信すべて。
    - **時刻が読めない行は数えない**（`unclassified` に出す）。

    **これは相関で、因果ではない。** 差の検定も推奨も出さない。
    """
    nodes = tree["nodes"]
    branches: dict = {}
    self_branches = 0
    for rid, node in nodes.items():
        if node["depth"] != 1:
            continue
        if node["row"].get("own") is True:
            self_branches += 1
            continue
        branches[rid] = {"author_first": None, "others": []}

    unclassified = 0
    for rid, node in nodes.items():
        b = node["branch"]
        if b is None or b not in branches or rid == b:
            continue
        ts = _parse_time(node["row"].get("timestamp"))
        if ts is None:
            unclassified += 1
            continue
        if node["row"].get("own") is True:
            cur = branches[b]["author_first"]
            if cur is None or ts < cur:
                branches[b]["author_first"] = ts
        elif node["row"].get("own") is False:
            branches[b]["others"].append(ts)

    replied, not_replied = [], []
    for b in branches.values():
        if b["author_first"] is not None:
            replied.append(sum(1 for t in b["others"] if t > b["author_first"]))
        else:
            not_replied.append(len(b["others"]))

    return {
        "replied": _mean_with_n(replied),
        "not_replied": _mean_with_n(not_replied),
        "self_branches": self_branches,
        "unclassified_replies": unclassified,
        "definition": ("枝＝根の直下の他人の返信。その後の返信数＝その枝の下の"
                        "他人の返信（作者が返した枝は、作者の最初の返信より後の分だけ）。"
                        "相関であって因果ではありません"),
        "min_n": MIN_N,
    }


def _growth(posted_at, rows_all: list, coverage: dict) -> dict:
    """刻みごとの**累計**返信数。**取れていない刻みは `null`**（`0` ではない）。

    数え方: `timestamp` が「投稿から刻み以内」の返信の数。作者の返信も含む
    （`replies_total` と同じ分母。作者を除いた数は `others` に別に出す）。
    """
    out = {}
    for mark in MARKS:
        cover = coverage[mark]
        if posted_at is None:
            out[str(mark)] = {
                "replies": None, "others": None, "covered": cover["covered"],
                "reason": "`posted_at` が読めないので刻みを当てられません",
            }
            continue
        if not cover["covered"]:
            out[str(mark)] = {
                "replies": None, "others": None, "covered": False,
                "covered_by": None,
                "reason": cover.get("reason") or f"{mark}h の刻みの取得がまだありません",
            }
            continue
        limit = posted_at + datetime.timedelta(hours=mark)
        within, others, unreadable = 0, 0, 0
        for row in rows_all:
            ts = _parse_time(row.get("timestamp"))
            if ts is None:
                unreadable += 1
                continue
            if ts <= limit:
                within += 1
                if row.get("own") is False:
                    others += 1
        out[str(mark)] = {
            "replies": within,
            "others": others,
            "covered": True,
            "covered_by": cover["covered_by"],
            "collected_at": cover["collected_at"],
            "collected_age_hours": cover["age_hours"],
            # **1 回の取得に刻みが同居していた**（過去のその時点を復元したわけ
            # ではない・`thth/measured.py:_mark_collapsed()` と同じ印）。
            "marks_collapsed": cover["marks_collapsed"],
            "timestamp_unreadable": unreadable,
        }
    return out


def _views_at(measured_post: dict | None) -> dict:
    """実測の台帳から、同じ刻みの views（設計 v1 §3.2.2）。

    **`age_hours` を必ず添える**——`marks` は「どの刻みとして採ったか」であって
    経過時間ではない（`thth/measured.py:load()` の注意書きと同じ）。
    """
    out = {str(m): {"views": None, "age_hours": None, "collected_at": None,
                     "reason": "実測の台帳にこの刻みの行がありません"} for m in MARKS}
    if not measured_post:
        for m in MARKS:
            out[str(m)]["reason"] = "この投稿の実測の台帳がありません"
        return out
    for row in measured_post.get("rows") or []:
        metrics = row.get("metrics") or {}
        for mark in row.get("marks") or []:
            if mark not in MARKS:
                continue
            slot = out[str(mark)]
            if slot["collected_at"] is not None:
                continue  # 同じ刻みが 2 行あれば先の行を採る
            views = metrics.get("views")
            slot.update({
                "views": views if isinstance(views, (int, float)) else None,
                "age_hours": row.get("age_hours"),
                "collected_at": row.get("collected_at"),
                "marks_collapsed": row.get("marks_collapsed", False),
                "reason": None if isinstance(views, (int, float))
                          else "この行に views がありません",
            })
    return out


def _post_shape(post_id: str, rows: list, fetches: list, *, measured_post,
                 medium: str | None, account_name: str) -> dict:
    """1 投稿の形。**この関数は数えるだけで、良し悪しを言わない。**"""
    tree = _tree(post_id, rows)
    nodes = tree["nodes"]

    posted_at_raw = (measured_post or {}).get("posted_at")
    posted_at = _parse_time(posted_at_raw)

    others = [r for r in rows if r.get("own") is False]
    own = [r for r in rows if r.get("own") is True]
    unknown = [r for r in rows if r.get("own") is None]

    branches = sum(1 for n in nodes.values() if n["depth"] == 1)
    depths = [n["depth"] for n in nodes.values() if n["depth"] is not None]

    # 最初の**他人**の返信までの分。**判らないものを他人にしない**（規約 12）
    # ——`own` が `None` の返信は使わない。使わなかった数は出す。
    other_times = sorted(t for t in (_parse_time(r.get("timestamp")) for r in others)
                          if t is not None)
    unknown_times = sorted(t for t in (_parse_time(r.get("timestamp")) for r in unknown)
                            if t is not None)
    first_reply_min = None
    first_reply_reason = None
    if posted_at is None:
        first_reply_reason = "`posted_at` が読めません"
    elif not other_times:
        first_reply_reason = ("他人と判った返信がまだありません"
                              if not unknown_times
                              else "他人と判った返信がありません（身内か判らない返信のみ）")
    else:
        first_reply_min = (other_times[0] - posted_at).total_seconds() / 60.0
    # **早いほうに「判らない返信」があったことを黙らない。**
    earlier_unknown = bool(
        posted_at is not None and other_times and unknown_times
        and unknown_times[0] < other_times[0])

    topic = (measured_post or {}).get("topic")
    try:
        kind = topics_mod.kind_of(topic, account_name) if topic else None
    except topics_mod.ShelfBroken:
        kind = None

    coverage = _fetch_covered(fetches)
    return {
        "post_id": post_id,
        "medium": medium,
        "topic": topic,
        "kind": kind,
        # **どの記録から採った投稿か**（設計 v2.0.1 §3）。`"queue"` は書き戻された
        # front-matter、`"sent"` は `state/<account>/sent/`（同席の様態）。
        # 実測の台帳が無ければ `None`——**判らないものを `queue` と言わない。**
        "source": ((measured_post or {}).get("source")
                    or next((f.get("source") for f in fetches if f.get("source")),
                            None)),
        "posted_at": posted_at_raw,
        "hour_band": hour_band(posted_at),
        "branches": branches,
        "depth": max(depths) if depths else 0,
        "replies_total": len(rows),
        "author_replies": len(own),
        "other_replies": len(others),
        "own_unknown": len(unknown),
        "participants": _participants(others),
        "first_reply_min": first_reply_min,
        "first_reply": {
            "minutes": first_reply_min,
            "reason": first_reply_reason,
            # 他人と判った最初の返信より前に「身内か判らない返信」があった。
            "unknown_reply_was_earlier": earlier_unknown,
        },
        "author_reply_effect": _author_reply_effect(post_id, tree),
        "growth": _growth(posted_at, rows, coverage),
        "views_at": _views_at(measured_post),
        "fetch_coverage": {str(m): coverage[m] for m in MARKS},
        "fetches": len(fetches),
        # **根に繋がらなかった行を、根の直下に寄せていない**ことを見せる。
        "orphan_replies": tree["orphans"],
        "duplicate_reply_ids": tree["duplicate_ids"],
        "measured": bool(measured_post),
    }


_SUMMARY_METRICS = ("branches", "depth", "replies_total", "first_reply_min")


def _group_summary(label: str, posts: list) -> dict:
    """1 群の中央値。**n < MIN_N の群は中央値を出さない**（規約 2）。"""
    n = len(posts)
    out = {"n": n, "posts": [p["post_id"] for p in posts], "metrics": {}}
    if n < MIN_N:
        out["cannot_say"] = f"{label}: n={n}（{MIN_N} 未満）"
        return out
    for metric in _SUMMARY_METRICS:
        values = [p[metric] for p in posts if isinstance(p.get(metric), (int, float))]
        stat = _stat(values)
        # **指標ごとに n が違う。** `first_reply_min` は返信 0 件の投稿で `None`
        # になるので、群の n だけを見ると分母を取り違える。
        if stat["median"] is None:
            stat["cannot_say"] = f"{label} の {metric}: n={stat['n']}（{MIN_N} 未満）"
        out["metrics"][metric] = stat
    return out


def _summarize(posts: list, *, medium: str | None) -> dict:
    """型ごと・時刻帯ごとの中央値と n。**媒体をまたがない**（設計 v2 §2.1）。"""
    by_kind: dict = {}
    by_band: dict = {}
    for post in posts:
        by_kind.setdefault(post["kind"] or "（型なし）", []).append(post)
        by_band.setdefault(post["hour_band"] or "（時刻不明）", []).append(post)

    kinds = {k: _group_summary(f"型 `{k}`", v) for k, v in sorted(by_kind.items())}
    # 時刻帯は**出た順ではなく決めた順**に並べる（読む人が同じ並びを期待する）。
    band_order = [b[0] for b in HOUR_BANDS] + ["（時刻不明）"]
    bands = {b: _group_summary(f"時刻帯 `{b}`", by_band[b])
             for b in band_order if b in by_band}

    cannot_say = []
    for group in list(kinds.values()) + list(bands.values()):
        if "cannot_say" in group:
            cannot_say.append(group["cannot_say"])
        for stat in group["metrics"].values():
            if "cannot_say" in stat:
                cannot_say.append(stat["cannot_say"])

    return {
        # **媒体で閉じていることを出力に刻む**（設計 v2 §2.1）。
        "medium": medium,
        "posts": len(posts),
        "min_n": MIN_N,
        "by_kind": kinds,
        "by_hour_band": bands,
        "cannot_say": cannot_say,
        "hour_bands": {name: f"{lo}時〜{hi}時" for name, lo, hi in HOUR_BANDS},
    }


def load(account_name: str, *, post_id: str | None = None) -> dict:
    """1 account のスレッドの形。**読むだけ。**

    分母と除外の理由を全部持って返す（設計 v2 §1 規約 1）:

      - `posts` … 返信の台帳（`data/sns/replies/<post_id>.ndjson`）に記録が
        ある投稿ごとの形。**台帳が無い投稿はここに入れない**——返信 0 件と
        「まだ採っていない」を混ぜないため（`posts_without_reply_ledger` に
        post_id だけ出す）。
      - `summary` … 型ごと・時刻帯ごとの中央値と n（`cannot_say` 付き）。
      - `broken` … 読めなかった返信の台帳（壊れと不存在を混ぜない）。
      - `unreadable_accounts` … handle の突き合わせのために読めなかった
        account（非空なら「他人」の判定が不完全で `own_unknown` に落ちている）。
      - `measured_broken` … 読めなかった実測の台帳。
    """
    account_cfg = accounts_mod.load_account(account_name)
    medium = account_cfg.get("media")

    ledger = replies_mod.load(account_name, post_id=post_id)

    # 実測は account 単位でしか読めない（`thth/measured.py` は 1 account を
    # まとめて返す）。**読めなくても形の計算は止めない**——views が出ないだけ。
    try:
        measured = measured_mod.load(account_name)
    except accounts_mod.AccountError:
        measured = {"posts": [], "broken": [], "posts_unknown_ownership": []}
    measured_by_id = {p["post_id"]: p for p in measured.get("posts") or []}

    rows_by_post: dict = {}
    fetches_by_post: dict = {}
    for row in ledger["replies"]:
        pid = row.get("post_id")
        if pid:
            rows_by_post.setdefault(str(pid), []).append(row)
    for row in ledger["fetches"]:
        pid = row.get("post_id")
        if pid:
            fetches_by_post.setdefault(str(pid), []).append(row)

    post_ids = sorted(set(rows_by_post) | set(fetches_by_post))
    if post_id:
        post_ids = [p for p in post_ids if p == str(post_id)]

    posts = [
        _post_shape(pid, rows_by_post.get(pid, []), fetches_by_post.get(pid, []),
                    measured_post=measured_by_id.get(pid), medium=medium,
                    account_name=account_name)
        for pid in post_ids
    ]

    # **実測はあるが返信の台帳が無い投稿**（採集の版が上がる前の投稿など）。
    # 返信 0 件として summary の分母に入れると、「0 件だった」と「まだ採って
    # いない」が混ざる。**名前だけ出して数には入れない。**
    without_ledger = sorted(set(measured_by_id) - set(post_ids))

    return {
        "account": account_name,
        "medium": medium,
        "marks": list(MARKS),
        "posts": posts,
        "summary": _summarize(posts, medium=medium),
        "posts_without_reply_ledger": without_ledger,
        "broken": ledger["broken"],
        "unreadable_accounts": ledger["unreadable_accounts"],
        "measured_broken": measured.get("broken") or [],
        "measured_unknown_ownership": measured.get("posts_unknown_ownership") or [],
    }


# ---------------------------------------------------------------- 公開名
#
# `thth/ask.py`（設計 v2 §1 の答えの口）が**同じ読み方**を使うための名前。
# 時刻の読み方と `replied_to` の中の id の取り出し方は、ここと 2 通りあっては
# ならない——**同じ規則を二度書くと、黙って割れる。**
parse_time = _parse_time
ref_id = _ref_id
