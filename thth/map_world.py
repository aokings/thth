"""観測の地図の世間の層と共起（設計 3.5.0 §1 の層 1・§2・§8）。**既定で無効。**

`thth map collect <project>`（1 日 1 回・timer 用）が、点ごとに媒体の検索を 1 回だけ
呼び、**その場で集計して**日ごとの行を積む。管理者が `THTH_MAP_WORLD=1` を入れたとき
だけ媒体を叩く（`map_view.world_enabled()`）。無効のあいだは何も叩かず
`world_layer_disabled` と言う。

規律（設計 §2・照合 §6 の 11 項目）:

  (a) **保存行は許可リストの項目だけ**（`NODE_KEYS`・`EDGE_KEYS`）。本文・post_id・
      username・author_key・permalink・時刻の生値は入らない。書く前と読むときに
      `valid_row()` で確かめ、項目が 1 つでも多い行は書かない（loud reject）。
  (b) **小標本は null と `small_sample`**（n・distinct・co が 5 未満）。n=1・distinct=1・
      top3_share=1.0 の行は語と日付で特定の 1 投稿・1 人を指すため（照合 §1・§6-1）。
      `valid_row()` も 1〜4 の値を持つ行を断る（二重に固定する）。
  (c) **search_type・limit・期間を固定して行に記録**（`RECENT`・25 件・24 時間）。
      0 件は `zero_or_filtered`（Threads は語によって空の配列を返す・照合 §6-3）。
  (d) **点ごとに検索 1 回**。共起は、点 A の検索結果（メモリの中）に点 B の語が含まれるかを
      数えて捨てる——**別の検索を増やさない**。本文は数え終えたら手放す。
  (e) **1 日 1 回**（同じ日・同じ媒体の行が既にあれば叩かない・`already_collected_today`）。
  (f) **Threads は API だけ**（アダプタの `keyword_search`・Web の自動取得はしない・
      照合 §6-10）。Mastodon の件数は「そのサーバで検索に出た数」と表示する（§6-11）。
  (g) **世間の層は project の中だけ**（広場の open・他の持ち主・横断集計に出さない・§6-7）。
      読む口は `map_view.show()` だけで、project の外からは読めない。
  (h) 置き場は `state/_map/<project>/<YYYY-MM>.ndjson`（0600・git の外）。
"""
from __future__ import annotations

import datetime
import json
import os
import re

from . import accounts, adapters, jst, map_store, threads_read_cli
from .adapters import base as adapter_base

# 世間の層を集める媒体（X は読みの従量課金があるので入れない）。
WORLD_MEDIA = ("threads", "bluesky", "mastodon")
# 固定する問い方（照合 §6-3: search_type・limit・期間を固定して記録）。
SEARCH_TYPE = "RECENT"
LIMIT = 25
WINDOW_HOURS = 24
# 小標本の閾値（照合 §6-1）。n・distinct・co がこれ未満なら null・`small_sample`。
SMALL_SAMPLE = 5
# 直近の投稿までの時間の丸め（時刻の生値は残さない）。
AGE_BUCKETS = ("lt_1h", "1h_6h", "6h_24h", "ge_24h")
ROW_REASONS = ("small_sample", "zero_or_filtered", "provider_timeout", "scope_missing",
               "budget_exhausted", "not_supported", "unavailable")
# 保存行の許可リスト（設計 §2 の 1 行の形＋照合 §6-3 の問い方と理由）。
NODE_KEYS = frozenset(("date", "medium", "node", "n", "requested", "distinct", "with_username",
                       "top3_share", "latest_age_bucket", "search_type", "window_hours", "reason"))
EDGE_KEYS = frozenset(("date", "medium", "edge", "co", "denominator", "search_type",
                       "window_hours", "reason"))
MONTH_FILE = re.compile(r"(\d{4})-(\d{2})\.ndjson\Z")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")

# 媒体ごとの件数の読み方（照合 §6-11）。
LABELS = {
    "threads": f"Threads の検索（{SEARCH_TYPE}・先頭 {LIMIT} 件・直近 {WINDOW_HOURS} 時間）に出た数",
    "bluesky": f"Bluesky の検索（{SEARCH_TYPE}・先頭 {LIMIT} 件・直近 {WINDOW_HOURS} 時間）に出た数",
    "mastodon": f"そのサーバで検索に出た数（先頭 {LIMIT} 件・直近 {WINDOW_HOURS} 時間。"
                "全文検索はサーバの設定次第）",
}


# ------------------------------------------------------------------ 行の形

def _count(value):
    return value is None or (type(value) is int and value >= 0)


def _small(value):
    """小標本（1〜4）か。0 と null は小標本ではない（0 件は `zero_or_filtered`）。"""
    return type(value) is int and 0 < value < SMALL_SAMPLE


def valid_row(row) -> bool:
    """保存行の形（許可リストの項目だけ・小標本の値は持たない）。"""
    if not isinstance(row, dict):
        return False
    keys = set(row)
    if "node" in row:
        if keys != NODE_KEYS or not isinstance(row["node"], str) or not row["node"]:
            return False
        if not all(_count(row[key]) for key in ("n", "distinct", "with_username")):
            return False
        if row["requested"] != LIMIT:
            return False
        share = row["top3_share"]
        if share is not None and (isinstance(share, bool) or not isinstance(share, (int, float))
                                  or not 0 <= share <= 1):
            return False
        if row["latest_age_bucket"] is not None and row["latest_age_bucket"] not in AGE_BUCKETS:
            return False
        if _small(row["n"]) or _small(row["distinct"]):
            return False
        if (row["distinct"] is None) != (share is None):
            return False
    elif "edge" in row:
        if keys != EDGE_KEYS:
            return False
        edge = row["edge"]
        if (not isinstance(edge, list) or len(edge) != 2
                or not all(isinstance(word, str) and word for word in edge) or edge[0] == edge[1]):
            return False
        if not _count(row["co"]) or not _count(row["denominator"]) or _small(row["co"]):
            return False
        if row["denominator"] is None or row["denominator"] < SMALL_SAMPLE:
            return False
    else:
        return False
    if not isinstance(row["date"], str) or not DATE.match(row["date"]):
        return False
    if row["medium"] not in WORLD_MEDIA:
        return False
    if row["search_type"] != SEARCH_TYPE or row["window_hours"] != WINDOW_HOURS:
        return False
    return row["reason"] is None or row["reason"] in ROW_REASONS


def _age_bucket(latest, now):
    at = jst.parse(latest) if isinstance(latest, str) else None
    if at is None:
        return None
    hours = (now - at).total_seconds() / 3600
    if hours < 1:
        return "lt_1h"
    if hours < 6:
        return "1h_6h"
    if hours < 24:
        return "6h_24h"
    return "ge_24h"


def _base(date, medium):
    return {"date": date, "medium": medium, "search_type": SEARCH_TYPE,
            "window_hours": WINDOW_HOURS}


def node_row(date, medium, word, rows, now, *, reason=None):
    """点 1 つ・1 日・1 媒体の行（許可リストの項目だけ）。`rows` は窓の中の検索結果。

    計算は `where` と同じ口（`threads_read_cli.search_material()`: 件数・異なり・
    上位 3 の占有率）。**本文・投稿者・時刻そのものは受け取った dict から写さない。**
    """
    row = {**_base(date, medium), "node": word, "n": None, "requested": LIMIT, "distinct": None,
           "with_username": None, "top3_share": None, "latest_age_bucket": None,
           "reason": reason}
    if reason is not None:
        return row
    material = threads_read_cli.search_material(rows, q=word, search_type=SEARCH_TYPE, limit=LIMIT)
    n = material["n"]
    if n == 0:
        row.update(n=0, reason="zero_or_filtered")
        return row
    if n < SMALL_SAMPLE:
        row["reason"] = "small_sample"
        return row
    authors = material["authors"]
    row.update(n=n, with_username=authors["with_username"],
               latest_age_bucket=_age_bucket(material["latest_timestamp"], now))
    if authors["distinct"] < SMALL_SAMPLE or authors["top_share"] is None:
        row["reason"] = "small_sample"
    else:
        row.update(distinct=authors["distinct"], top3_share=round(authors["top_share"], 4))
    return row


def _contains(text, word):
    return isinstance(text, str) and map_store.node_key(word) in map_store.node_key(text)


def edge_rows(date, medium, word, words, rows):
    """点 A（`word`）の検索結果の中で、点 B の語を含む投稿の本数（**数えて捨てる**）。

    別の検索はしない。A の n が小標本なら線は作らない。co が 0 の線は行を作らない
    （A の行が読めれば 0 と読める）。co が 1〜4 は null・`small_sample`。
    """
    n = len(rows)
    if n < SMALL_SAMPLE:
        return []
    out = []
    for other in words:
        if other == word:
            continue
        co = sum(1 for row in rows if _contains(row.get("text"), other))
        if co == 0:
            continue
        out.append({**_base(date, medium), "edge": [word, other],
                    "co": None if co < SMALL_SAMPLE else co, "denominator": n,
                    "reason": "small_sample" if co < SMALL_SAMPLE else None})
    return out


# ------------------------------------------------------------------ 置き場

def _encode(rows):
    for row in rows:
        if not valid_row(row):
            # 許可リストの外の項目・小標本の値を持つ行は書かない（loud reject）。
            raise map_store.MapError("map_store_unavailable")
    return "".join(json.dumps(row, ensure_ascii=False, allow_nan=False, sort_keys=True) + "\n"
                   for row in rows).encode("utf-8")


def _decode(data):
    rows, broken = [], 0
    for line in (data or b"").decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):
            broken += 1
            continue
        if valid_row(row):
            rows.append(row)
        else:
            broken += 1
    return rows, broken


def _month_names(directory):
    try:
        names = os.listdir(directory)
    except OSError:
        raise map_store.MapError("map_store_unavailable") from None
    return sorted(name for name in names if MONTH_FILE.match(name))


def append_rows(project, rows):
    """行を月ごとの ndjson に足す（排他の中・一時ファイル → rename）。"""
    project_store = map_store.store(project)
    by_month = {}
    for row in rows:
        by_month.setdefault(row["date"][:7], []).append(row)
    with project_store.locked() as directory:
        for month, members in sorted(by_month.items()):
            name = f"{month}.ndjson"
            existing = project_store.read_raw(directory, name) or b""
            project_store.write_raw(directory, name, existing + _encode(members))
    return len(rows)


def read_rows(project, *, since_date=None, until_date=None):
    """保存行（読むだけ）。`(rows, broken)`。置き場が無ければ 0 行。"""
    project_store = map_store.store(project)
    directory = project_store.open()
    if directory is None:
        return [], 0
    try:
        rows, broken = [], 0
        for name in _month_names(directory):
            month = name[:7]
            if since_date and month < since_date[:7] or until_date and month > until_date[:7]:
                continue
            got, bad = _decode(project_store.read_raw(directory, name))
            broken += bad
            rows.extend(row for row in got
                        if (not since_date or row["date"] >= since_date)
                        and (not until_date or row["date"] <= until_date))
        return rows, broken
    finally:
        os.close(directory)


def rewrite(project, keep):
    """月ごとの ndjson を `keep(row)` が真の行だけに書き直す。空になった月は消す。

    読めない行も落とす（形の分からない行を持ち続けない）。戻り値は消した行の数。
    """
    project_store = map_store.store(project)
    directory = project_store.open()
    if directory is None:
        return 0
    os.close(directory)
    removed = 0
    with project_store.locked() as directory:
        for name in _month_names(directory):
            rows, broken = _decode(project_store.read_raw(directory, name))
            kept = [row for row in rows if keep(row)]
            dropped = len(rows) - len(kept) + broken
            if not dropped:
                continue
            removed += dropped
            if kept:
                project_store.write_raw(directory, name, _encode(kept))
            else:
                project_store.remove_name(directory, name)
    return removed


def forget_node(project, word):
    """点を消したとき、その点の行と、その点に掛かる共起の行を消す（持ち続けない）。"""
    return rewrite(project, lambda row: row.get("node") != word
                   and word not in (row.get("edge") or ()))


# ------------------------------------------------------------------ 集める

def _reason(error):
    """例外を行の理由（静的な符丁）に写す。文面は外へ出さない。"""
    if isinstance(error, adapter_base.PermissionMissing):
        return "scope_missing"
    from . import morning
    reason = morning.classify(error)
    return reason if reason in ROW_REASONS else "unavailable"


def _adapter(name, cfg):
    """where と同じ入口（能力・token・アダプタ）。使えなければ `(None, 理由)`。"""
    media = cfg.get("media")
    if media not in WORLD_MEDIA or "keyword_search" not in adapters.capabilities_for(media):
        return None, "not_supported"
    try:
        token = accounts.load_token(cfg)
        if not adapters.adapter_class(media).has_token(token):
            return None, "scope_missing"
        return adapters.make_adapter(cfg, token), None
    except (accounts.AccountError, adapter_base.AdapterError, OSError, ValueError, TypeError):
        return None, "unavailable"


def _in_window(rows, now):
    floor = now - datetime.timedelta(hours=WINDOW_HOURS)
    kept = []
    for row in rows or []:
        at = jst.parse(row.get("timestamp")) if isinstance(row, dict) else None
        if at is not None and floor <= at <= now:
            kept.append(row)
    return kept


def collect(target, *, now=None):
    """`thth map collect <project>`（timer 用）。**既定で無効**（媒体を叩かない）。

    戻り値は数だけ（本文・語ごとの結果の中身は返さない）。
    """
    from . import map_view
    project, configs = map_store.project_accounts(target)
    now = now or jst.now_jst()
    date = jst.to_jst(now).date().isoformat()
    result = {"schema_version": map_store.SCHEMA_VERSION, "report_type": "map_collected",
              "project": project, "date": date, "world_enabled": map_view.world_enabled(),
              "searches": 0, "rows_written": 0, "media": {}, "cannot_say": []}
    if not map_view.world_enabled():
        result["cannot_say"].append("world_layer_disabled")
        return result
    words = [row["word"] for row in map_store.load_config(project)["nodes"]]
    if not words:
        result["cannot_say"].append("no_map_nodes")
        return result
    existing, _broken = read_rows(project, since_date=date, until_date=date)
    done = {row["medium"] for row in existing}
    chosen = {}
    for name in sorted(configs):
        medium = configs[name].get("media")
        if medium in WORLD_MEDIA and medium not in chosen:
            chosen[medium] = name
    for medium, name in sorted(chosen.items()):
        if medium in done:
            result["media"][medium] = {"status": "already_collected_today", "searches": 0}
            continue
        adapter, why = _adapter(name, configs[name])
        if adapter is None:
            result["media"][medium] = {"status": why, "searches": 0}
            continue
        rows_out, searches = [], 0
        for word in words:
            # **点ごとに検索 1 回**。結果はこの繰り返しの中だけで使って手放す。
            try:
                searches += 1
                found = _in_window(adapter.keyword_search(word, search_type=SEARCH_TYPE,
                                                          limit=LIMIT), now)
            except Exception as error:  # noqa: BLE001 — 1 語の失敗で他の語を止めない
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    raise
                rows_out.append(node_row(date, medium, word, [], now, reason=_reason(error)))
                continue
            row = node_row(date, medium, word, found, now)
            rows_out.append(row)
            if row["distinct"] is not None:
                # 異なり投稿者が小標本の点からは線を作らない（数人の文の中身を数えることになる）。
                rows_out.extend(edge_rows(date, medium, word, words, found))
            del found
        written = append_rows(project, rows_out)
        result["searches"] += searches
        result["rows_written"] += written
        result["media"][medium] = {"status": "collected", "searches": searches,
                                   "rows": written}
    return result


# ------------------------------------------------------------------ 見る

def _ratio(row):
    if row.get("co") is None or not row.get("denominator"):
        return None
    return row["co"] / row["denominator"]


def view(project, words, *, since, now, edge_words=None):
    """点ごとの世間の層と共起の線（`map_view.show()` からだけ呼ぶ）。

    `edge_words` は線の両端として認める点（既定は `words`。`--node` のときは全部の点）。
    """
    rows, broken = read_rows(project, since_date=jst.to_jst(since).date().isoformat(),
                             until_date=jst.to_jst(now).date().isoformat())
    current = set(words)
    ends = set(edge_words if edge_words is not None else words)
    per_node = {word: {} for word in words}
    edges = {}
    for row in sorted(rows, key=lambda r: r["date"]):
        if "node" in row:
            if row["node"] not in current:
                continue
            cell = per_node[row["node"]].setdefault(
                row["medium"], {"label": LABELS[row["medium"]], "days": [], "latest": None})
            day = {key: row[key] for key in ("date", "n", "requested", "distinct", "with_username",
                                             "top3_share", "latest_age_bucket", "reason")}
            cell["days"].append(day)
            cell["latest"] = day
        elif row["edge"][0] in ends and row["edge"][1] in ends:
            edges.setdefault((row["edge"][0], row["edge"][1], row["medium"]), []).append(row)
    cells = {}
    for word in words:
        if per_node[word]:
            cells[word] = {"cannot_say": None, "by_medium": per_node[word]}
        else:
            cells[word] = {"cannot_say": "world_layer_empty", "by_medium": None}
    co = []
    for (a, b, medium), series in edges.items():
        latest = series[-1]
        before = [ratio for ratio in (_ratio(row) for row in series[:-1]) if ratio is not None]
        ratio = _ratio(latest)
        co.append({"edge": [a, b], "medium": medium, "date": latest["date"], "co": latest["co"],
                   "denominator": latest["denominator"], "ratio": ratio,
                   "change": (ratio - sum(before) / len(before)) if ratio is not None and before
                   else None,
                   "reason": latest["reason"]})
    co.sort(key=lambda e: (e["ratio"] is None, -(e["ratio"] or 0), e["edge"], e["medium"]))
    return cells, co, broken
