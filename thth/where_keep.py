"""`thth where … --keep`——語ごとの数を日付つきで残す口（3.18.0・設計 3.7.0 §C3・3.9.0 §E）。

10/12 の「決めたこと 3」（`docs/整理_Meta承認で解放されたものとされていないもの_2026-10-12.md`）:
Meta の許可された用途（10 権限すべてに「集計され、非特定化または匿名化された情報の利用を通じて
アプリを改善する」）の中で、**件数だけ・5 未満は残さない・privacy に書く**を条件に残してよい。

残すのは 2 種類の行だけ:

  - `aggregate`（A2・`--aggregate --keep`）: 語ごとの件数・問いの形の数・lexicon の分類ごとの数。
  - `also_hits`（A3・`--also … --keep`）: 語ごとの「取れた・also に合った」の数（当たり率は読む
    ときに割る）。翌日の配り直し（どの語に何本配るか）の材料。

規律（観測の地図の世間の層 `thth/map_world.py` の (a)〜(h) と同じ作法）:

  (a) **保存行は許可リストの項目だけ**（`AGGREGATE_KEYS`・`HITS_KEYS`）。本文・post_id・
      username・author_key・permalink・時刻の生値は入らない。書く前と読むときに `valid_row()`
      で確かめ、形の違う行は書かない（loud reject）。行は `where` の**出力の数**から作る——
      検索結果の行そのものはこの口に渡らない。
  (b) **5 未満の数は残さない**（0〜4 は null・欠測。発注と privacy の「numbers below 5 are not
      kept」のとおり 0 も値では残さない——地図は 0 件を 0 で残すが、この口は残さない）。件数 n
      （取れた数）が 5 未満の行は内訳も全部 null（理由は `small_sample`・0 件は
      `zero_or_filtered`／`no_posts_fetched`）。`valid_row()` も 0〜4 の値を持つ行を断る（二重に固定）。
  (c) **既定は残さない**。`--keep` を付けたときだけ書く（設計 3.7.0 §C3・3.9.0 §E の
      「表示だけ」を既定のまま残す）。
  (d) 置き場は観測の地図と同じ `state/_map/<project>/where-<YYYY-MM>.ndjson`（0600・git の外・
      project ごと）。**同じ日・同じ媒体・同じ種類・同じ語の行は最後の 1 回で置き換える**
      （同じ投稿を 2 度数えない）。
  (e) 保持は地図と同じ日数（`thth admin map retention`・既定 180 日・日単位）。書くたびに
      古い日を消し、読むときも保持を過ぎた日は見せない。`thth admin map purge` と退出でも消える
      （`map_store.purge()`・`map_world.purge_for_leave()`）。
  (f) **project の中だけ**（読む口は `thth where-log <project>` だけ・広場にも横断の集計にも
      出さない）。
"""
from __future__ import annotations

import datetime
import json
import os
import re

from . import accounts, jst, map_store

FILE = re.compile(r"where-(\d{4})-(\d{2})\.ndjson\Z")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
SMALL_SAMPLE = 5
KINDS = ("aggregate", "also_hits")
MEDIA = ("threads", "bluesky", "mastodon", "x")
SEARCH_TYPES = ("TOP", "RECENT")
# `where` の絞り込み（3.18.0・A5）の符丁。行に残すのは符丁だけ（絞った件数は残さない）。
FILTERS = ("ja", "question", "no_replies")
REASONS = ("small_sample", "zero_or_filtered", "no_posts_fetched")
COMMON_KEYS = frozenset(("date", "medium", "kind", "word", "search_type", "requested",
                         "filters", "reason"))
AGGREGATE_KEYS = COMMON_KEYS | {"n", "ends_with_question_mark", "contains_question_mark",
                                "lexicon"}
HITS_KEYS = COMMON_KEYS | {"also", "n_fetched", "n_matched"}
MAX_WORD_CHARS = 200
MAX_LEXICON = 30
MAX_ALSO = 5
PLACE = "state/_map/<project>/where-<YYYY-MM>.ndjson（VM の私有・git の外）"
STORAGE_NOTE_KEPT = ("語ごとの数だけを残しました（本文・post_id・username は残さない・5 未満は欠測）。"
                     "置き場: " + PLACE)


# ------------------------------------------------------------------ 行の形

def _count(value):
    return value is None or (type(value) is int and value >= 0)


def _small(value):
    """残してはいけない数（0〜4）。残す数は null か 5 以上だけ。"""
    return type(value) is int and value < SMALL_SAMPLE


def kept(value):
    """5 未満（0〜4）は欠測（null）。5 以上だけを値で残す。"""
    if type(value) is not int or value < SMALL_SAMPLE:
        return None
    return value


def valid_row(row) -> bool:
    if not isinstance(row, dict):
        return False
    kind = row.get("kind")
    if kind == "aggregate":
        if set(row) != AGGREGATE_KEYS:
            return False
        counts = [row["n"], row["ends_with_question_mark"], row["contains_question_mark"]]
        lexicon = row["lexicon"]
        if lexicon is not None:
            if (not isinstance(lexicon, dict) or len(lexicon) > MAX_LEXICON
                    or not all(isinstance(k, str) and k for k in lexicon)):
                return False
            counts.extend(lexicon.values())
    elif kind == "also_hits":
        if set(row) != HITS_KEYS:
            return False
        also = row["also"]
        if (not isinstance(also, list) or not 1 <= len(also) <= MAX_ALSO
                or not all(isinstance(w, str) and w for w in also)):
            return False
        counts = [row["n_fetched"], row["n_matched"]]
    else:
        return False
    if not all(_count(value) and not _small(value) for value in counts):
        return False
    if not isinstance(row["date"], str) or not DATE.match(row["date"]):
        return False
    if row["medium"] not in MEDIA or row["search_type"] not in SEARCH_TYPES:
        return False
    if not isinstance(row["word"], str) or not row["word"] or len(row["word"]) > MAX_WORD_CHARS:
        return False
    if type(row["requested"]) is not int or not 1 <= row["requested"] <= 100:
        return False
    filters = row["filters"]
    if not isinstance(filters, list) or any(f not in FILTERS for f in filters):
        return False
    return row["reason"] is None or row["reason"] in REASONS


def _base(date, medium, kind, word, *, search_type, requested, filters):
    return {"date": date, "medium": medium, "kind": kind, "word": word,
            "search_type": search_type, "requested": requested,
            "filters": sorted(filters), "reason": None}


def aggregate_row(date, medium, word, agg, *, search_type, requested, filters):
    """`where_cli.aggregate()` の**出力の数**から 1 行。本文・名前・id は受け取らない。"""
    row = _base(date, medium, "aggregate", word, search_type=search_type,
                requested=requested, filters=filters)
    row.update(n=None, ends_with_question_mark=None, contains_question_mark=None, lexicon=None)
    n = agg.get("n")
    lexicon = agg.get("lexicon")
    if type(n) is not int or n < 0:
        return None
    if n == 0:
        row["reason"] = "zero_or_filtered"
        if lexicon is not None:
            row["lexicon"] = {name: None for name in lexicon}
        return row
    if _small(n):
        row["reason"] = "small_sample"
        if lexicon is not None:
            row["lexicon"] = {name: None for name in lexicon}
        return row
    q = agg.get("question_forms") or {}
    row.update(n=n, ends_with_question_mark=kept(q.get("ends_with_question_mark")),
               contains_question_mark=kept(q.get("contains_question_mark")))
    if lexicon is not None:
        row["lexicon"] = {name: kept((cell or {}).get("n_posts")) for name, cell in lexicon.items()}
    return row


def hits_row(date, medium, hit, also, *, search_type, requested, filters):
    """`where_suggest.also_hits()` の 1 行（数だけ）から 1 行。"""
    row = _base(date, medium, "also_hits", hit["word"], search_type=search_type,
                requested=requested, filters=filters)
    row.update(also=list(also), n_fetched=None, n_matched=None)
    fetched, matched = hit.get("n_fetched"), hit.get("n_matched")
    if type(fetched) is not int or fetched < 0:
        return None
    if fetched == 0:
        row["reason"] = "no_posts_fetched"
        return row
    if _small(fetched):
        row["reason"] = "small_sample"
        return row
    row.update(n_fetched=fetched, n_matched=kept(matched))
    if row["n_matched"] is None:
        row["reason"] = "small_sample"
    return row


def rows_for_node(node, *, date, search_type, filters, also) -> list:
    """`where` の 1 account の節（出力）から残す行。`by_word` と `also_hits` の数だけを読む。"""
    medium = node.get("medium")
    out = []
    if node.get("mode") == "aggregate":
        for word, entry in node["by_word"].items():
            row = aggregate_row(date, medium, word, entry["aggregate"], search_type=search_type,
                                requested=entry["requested_limit"], filters=filters)
            if row is not None:
                out.append(row)
    requested = {word: (entry.get("requested_limit")
                        or (entry.get("material") or {}).get("requested_limit"))
                 for word, entry in node["by_word"].items()}
    for hit in node.get("also_hits") or []:
        row = hits_row(date, medium, hit, also, search_type=search_type,
                       requested=requested.get(hit["word"]) or 1, filters=filters)
        if row is not None:
            out.append(row)
    return [row for row in out if valid_row(row)]


# ------------------------------------------------------------------ 置き場

def _encode(rows):
    for row in rows:
        if not valid_row(row):
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


def _names(directory):
    try:
        names = os.listdir(directory)
    except OSError:
        raise map_store.MapError("map_store_unavailable") from None
    return sorted(name for name in names if FILE.match(name))


def _key(row):
    return (row["date"], row["medium"], row["kind"], row["word"])


def _floor(project, now):
    days = map_store.load_config(project)["retention_days"]
    return (jst.to_jst(now).date() - datetime.timedelta(days=days)).isoformat()


def write(project, rows, *, now=None) -> dict:
    """行を足す（同じ日・媒体・種類・語は置き換え）。保持を過ぎた日はここで消す。"""
    now = now or jst.now_jst()
    floor = _floor(project, now)
    project_store = map_store.store(project)
    by_month = {}
    for row in rows:
        by_month.setdefault(row["date"][:7], []).append(row)
    pruned = 0
    with project_store.locked() as directory:
        for month, members in sorted(by_month.items()):
            name = f"where-{month}.ndjson"
            existing, broken = _decode(project_store.read_raw(directory, name))
            fresh = {_key(row) for row in members}
            keep = [row for row in existing if _key(row) not in fresh]
            project_store.write_raw(directory, name, _encode(keep + members))
            pruned += broken
        for name in _names(directory):
            existing, broken = _decode(project_store.read_raw(directory, name))
            kept_rows = [row for row in existing if row["date"] >= floor]
            dropped = len(existing) - len(kept_rows) + broken
            if not dropped:
                continue
            pruned += dropped
            if kept_rows:
                project_store.write_raw(directory, name, _encode(kept_rows))
            else:
                project_store.remove_name(directory, name)
    return {"rows": len(rows), "pruned": pruned, "project": project, "place": PLACE,
            "retention_days": map_store.load_config(project)["retention_days"]}


def read(project, *, since_date=None, until_date=None):
    project_store = map_store.store(project)
    directory = project_store.open()
    if directory is None:
        return [], 0
    try:
        rows, broken = [], 0
        for name in _names(directory):
            month = name[6:13]
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


def rewrite(project, keep) -> int:
    """`keep(row)` が真の行だけに書き直す（削除の依頼・退出）。消した行の数。"""
    project_store = map_store.store(project)
    directory = project_store.open()
    if directory is None:
        return 0
    os.close(directory)
    removed = 0
    with project_store.locked() as directory:
        for name in _names(directory):
            rows, broken = _decode(project_store.read_raw(directory, name))
            kept_rows = [row for row in rows if keep(row)]
            dropped = len(rows) - len(kept_rows) + broken
            if not dropped:
                continue
            removed += dropped
            if kept_rows:
                project_store.write_raw(directory, name, _encode(kept_rows))
            else:
                project_store.remove_name(directory, name)
    return removed


def project_of(account_name) -> str | None:
    try:
        project = accounts.load_account(account_name).get("project")
    except accounts.AccountError:
        return None
    return project if isinstance(project, str) and accounts.name_is_safe(project) else None


# ------------------------------------------------------------------ 読む（where-log）

def _allocate(rates: dict, budget: int) -> dict:
    """当たり率に比例して `budget` 本を割る（最大剰余）。率が無い・0 の語は 0 本。"""
    usable = {word: rate for word, rate in rates.items() if rate}
    total = sum(usable.values())
    out = {word: 0 for word in rates}
    if not total:
        return out
    shares = {word: budget * rate / total for word, rate in usable.items()}
    for word, share in shares.items():
        out[word] = int(share)
    rest = budget - sum(out.values())
    for word in sorted(shares, key=lambda w: (-(shares[w] - int(shares[w])), w))[:rest]:
        out[word] += 1
    return out


ALLOCATION_RULE = ("当たり率（期間の also に合った数の和 ÷ 取れた数の和・両方が残っている日だけ）に"
                   "比例して割った数（最大剰余）。率が出せない語・0 の語は 0 本。計算だけで、"
                   "どの語に何本配るかを選ぶのは呼ぶ側")


def summary(target, *, since="14d", medium=None, budget=None, now=None) -> dict:
    """`thth where-log <project>`: 残した行を語ごとにまとめる（数だけ）。"""
    from . import read_window
    project, _configs = map_store.project_accounts(target)
    now = now or jst.now_jst()
    try:
        start = read_window.cutoff(since, now=now)
    except ValueError:
        raise map_store.MapError("invalid_since") from None
    since_date = max(jst.to_jst(start).date().isoformat(), _floor(project, now))
    rows, broken = read(project, since_date=since_date,
                        until_date=jst.to_jst(now).date().isoformat())
    if medium is not None:
        rows = [row for row in rows if row["medium"] == medium]
    hits, aggs = {}, {}
    for row in sorted(rows, key=lambda r: r["date"]):
        cell_key = (row["medium"], row["word"])
        if row["kind"] == "also_hits":
            cell = hits.setdefault(cell_key, {"medium": row["medium"], "word": row["word"],
                                              "days": [], "n_fetched_sum": 0, "n_matched_sum": 0,
                                              "days_counted": 0})
            cell["days"].append({k: row[k] for k in ("date", "also", "n_fetched", "n_matched",
                                                     "requested", "filters", "reason")})
            if row["n_fetched"] is not None and row["n_matched"] is not None:
                cell["n_fetched_sum"] += row["n_fetched"]
                cell["n_matched_sum"] += row["n_matched"]
                cell["days_counted"] += 1
        else:
            cell = aggs.setdefault(cell_key, {"medium": row["medium"], "word": row["word"],
                                              "days": []})
            cell["days"].append({k: row[k] for k in ("date", "n", "ends_with_question_mark",
                                                     "contains_question_mark", "lexicon",
                                                     "requested", "filters", "reason")})
    hit_rows = []
    for cell in hits.values():
        fetched = cell["n_fetched_sum"]
        cell["hit_rate"] = round(cell["n_matched_sum"] / fetched, 4) if fetched else None
        cell["days_total"] = len(cell["days"])
        hit_rows.append(cell)
    hit_rows.sort(key=lambda c: (c["medium"], c["hit_rate"] is None, -(c["hit_rate"] or 0),
                                 c["word"]))
    out = {"report_type": "where_kept", "project": project, "since": since_date,
           "until": jst.to_jst(now).date().isoformat(), "medium": medium,
           "also_hits": hit_rows,
           "aggregate": sorted(aggs.values(), key=lambda c: (c["medium"], c["word"])),
           "small_sample_rule": "5 未満の数は残していません（null・欠測）",
           "broken_rows": broken, "allocation": None}
    if budget is not None:
        if medium is None or type(budget) is not int or budget < 1:
            raise ValueError("--budget は 1 以上の整数で、--medium と一緒に使います")
        rates = {cell["word"]: cell["hit_rate"] for cell in hit_rows}
        out["allocation"] = {"budget": budget, "medium": medium, "rule": ALLOCATION_RULE,
                             "by_word": _allocate(rates, budget)}
    return out


def render(result) -> None:
    print(f"where-log  project {result['project']}  {result['since']}〜{result['until']}"
          + (f"  媒体 {result['medium']}" if result["medium"] else ""))
    if not result["also_hits"] and not result["aggregate"]:
        print("  残した行がありません（where … --keep で残します）")
    if result["also_hits"]:
        print("  also の当たり率（期間の和・高い順）:")
    for cell in result["also_hits"]:
        rate = f"{cell['hit_rate']:.0%}" if cell["hit_rate"] is not None else "—"
        print(f"    [{cell['medium']}] 語「{cell['word']}」 取れた {cell['n_fetched_sum']}・合った "
              f"{cell['n_matched_sum']}・当たり率 {rate}（数えた日 {cell['days_counted']}/"
              f"{cell['days_total']}）")
    if result["aggregate"]:
        print("  集計（日ごと）:")
    for cell in result["aggregate"]:
        days = "・".join(f"{d['date'][5:]} n={d['n'] if d['n'] is not None else '—'}"
                        f" 問い={d['contains_question_mark'] if d['contains_question_mark'] is not None else '—'}"
                        for d in cell["days"])
        print(f"    [{cell['medium']}] 語「{cell['word']}」 {days}")
    allocation = result.get("allocation")
    if allocation:
        print(f"  配り直し（{allocation['medium']}・{allocation['budget']} 本）: "
              + "・".join(f"{w} {n}" for w, n in allocation["by_word"].items()))
        print(f"    {allocation['rule']}")
    if result["broken_rows"]:
        print(f"  言えない: 読めない行 {result['broken_rows']} 行")
    print(f"  注記: {result['small_sample_rule']}")
