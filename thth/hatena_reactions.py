"""はてなの反応の採取と台帳（設計 4.0 S6 §3・§4）。

**何を取るか**（認証なしの公開の 3 つ・§3.1）: ブックマークの件数（件数取得 API `count/entries`）・
ブックマークの中身（エントリー情報 `entry/jsonlite`・キャッシュ）・はてなスター（`entry.json`）。
ブログのコメントとアクセス数は取らない（公式の API が無い・画面は機械で読まない）。

**いつ取るか**（§4.2）: はてなの口座の `thth run` の末尾で、配信の公開からの経過時間の刻み
（`collect.AGE_MARKS_HOURS`・SNS と同じ）を跨いだ配信だけ 1 回読む。起点は配信の台帳の `history[]` で
最初に `published` になった行の `at`。対象は `published`・`conflict`・`no_follow`（`published_url` がある）、
公開から `collect.metric_window_days()` 日の中だけ。`deleted` は読まない。1 run に 10 配信まで。
要求と要求の間は 2 秒（`SLEEP` は差し替え可）。主体・口座の止めは**掛けない**（読む側）。

**どこに置くか**（§4.3）: `accounts.data_dirs()["hatena_reactions"]/<delivery>.ndjson`（レーンの repo の
`data/hatena/reactions/`・repo が無ければ `state/<口座>/data/hatena/reactions/`）。**追記だけ・冪等。**
書く前に repo を同期し、書いたら commit と push（`collect._collect_entry` と同じ流儀）。push できなければ
commit を取り消して作業ツリーに残す。同期できなければ読まない・書かない（fail-closed）。

**取れなかった指標は書かない**（0 と混ぜない）。出所（ブックマーク・スター）ごとに独立で、取れなかった
出所はその刻みが「済んだ」に数えられず、次の run で同じ刻みをもう一度試す。

**自分の分**: 同じ `owner` の全主体の `hatena_id` が付けたブックマーク・スターは `self` に分け、`others`
（第三者の数）にも `bookmarks[]` にも入れない。`others` はエントリー情報が読めなければ **null**
（`missing` に `bookmark_others: own_unknown`）——推して `total` で埋めない。

承認の指紋・`sent/`・配信の台帳には 1 バイトも書かない（読むだけ）。
"""
from __future__ import annotations

import datetime
import json
import os
import time

from . import accounts as accounts_mod
from . import jst

SCHEMA = "thth.hatena_reaction/1"
DIR_KEY = "hatena_reactions"
TARGET_STATES = ("published", "conflict", "no_follow")
SOURCES = ("bookmark", "star")
MAX_DELIVERIES_PER_RUN = 10
INTERVAL_SECONDS = 2          # `hatena_bookmark.RECONCILE_INTERVAL_SECONDS` と同じ（公式の「数秒空けて」）
ACTION = "hatena_collect"

# 読む口の差し替え（試験）。`callable(kind, arg) -> 値`。kind は `counts`（URL の list → {url: 件数}）・
# `entry`（URL → `entry_info_full` の返り）・`stars`（URL の list → {url: [スター]}）・`count_one`
# （doctor の 1 回・URL の list）。失敗は `FetchError(符丁)` を上げる。None なら本物の adapter。
FETCH = None
SLEEP = time.sleep


class FetchError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


# --------------------------------------------------------------------------
# 台帳
# --------------------------------------------------------------------------

def ledger_dir(cfg, account) -> str:
    return accounts_mod.data_dirs(cfg, account)[DIR_KEY]


def ledger_path(cfg, account, delivery) -> str:
    from . import deliveries
    if not deliveries.is_delivery_id(delivery):
        raise ValueError("invalid_delivery")
    return os.path.join(ledger_dir(cfg, account), f"{delivery}.ndjson")


def read_rows(cfg, account, delivery) -> tuple:
    """`(行の list, 壊れているか)`。壊れた行が 1 行でもあれば中身は使わない（measured と同じ流儀）。"""
    path = ledger_path(cfg, account, delivery)
    if not os.path.exists(path):
        return [], False
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return [], True
    rows = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            return [], True
        if not isinstance(row, dict) or row.get("schema") != SCHEMA or row.get("delivery") != delivery:
            return [], True
        rows.append(row)
    return rows, False


def _append(path, row) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------
# 刻み
# --------------------------------------------------------------------------

def origin_at(record):
    """公開の起点（`history[]` で最初に `published` になった行の `at`）。無ければ None。"""
    for row in record.get("history") or []:
        if row.get("state") == "published":
            return jst.parse(row.get("at"))
    return None


def _source_done(row, source) -> bool:
    if source in row:
        return True
    return any(m.get("what") == source and m.get("reason") == "not_supported" for m in row.get("missing") or [])


def complete_marks(rows) -> set:
    """全部の出所（ブックマーク・スター）が済んだ刻み。出所ごとに別の行で済んでいてもよい。"""
    done = {source: set() for source in SOURCES}
    for row in rows:
        for mark in row.get("marks") or []:
            for source in SOURCES:
                if _source_done(row, source):
                    done[source].add(mark)
    out = None
    for marks in done.values():
        out = set(marks) if out is None else out & marks
    return out or set()


def due(record, rows, now) -> list:
    """跨いだのにまだ済んでいない刻み（`collect.due_marks` を使い回す）。"""
    from . import collect
    origin = origin_at(record)
    if origin is None:
        return []
    age = (now - origin).total_seconds() / 3600
    return collect.due_marks(age, [{"marks": sorted(complete_marks(rows))}])


def next_mark(record, rows, now):
    """次の刻みと、その時刻（`(刻み, datetime)`）。もう無ければ None。"""
    from . import collect
    origin = origin_at(record)
    if origin is None:
        return None
    done = complete_marks(rows)
    for mark in collect.AGE_MARKS_HOURS:
        if mark in done:
            continue
        at = origin + datetime.timedelta(hours=mark)
        if at > now:
            return mark, at
    return None


def eligible(record, now) -> bool:
    """採取の対象か（状態・URL・起点・38 日の窓）。"""
    from . import collect
    if record.get("state") not in TARGET_STATES or not record.get("published_url"):
        return False
    origin = origin_at(record)
    if origin is None or origin > now:
        return False
    return now - origin <= datetime.timedelta(days=collect.metric_window_days())


# --------------------------------------------------------------------------
# 自分の ID
# --------------------------------------------------------------------------

def self_ids(cfg) -> set:
    """同じ owner の全主体の `hatena_id`（自分の分を分ける材料・§2）。読めなければ口座の主体の ID だけ。"""
    from . import principals
    out = set()
    name = (cfg or {}).get("principal")
    if not isinstance(name, str):
        return out
    try:
        for _sibling, data in principals.siblings(name):
            if isinstance((data or {}).get("hatena_id"), str):
                out.add(data["hatena_id"])
    except Exception:  # noqa: BLE001 — 主体の台帳が読めなくても自分の ID は持っておく
        try:
            out.add(principals.load_principal(name)["hatena_id"])
        except Exception:  # noqa: BLE001
            pass
    return out


def contact_of(cfg):
    from . import principals
    try:
        return principals.load_principal(cfg["principal"]).get("user_agent_contact")
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# 読む口（差し替え → 本物の adapter）
# --------------------------------------------------------------------------

def _call(kind, arg, contact):
    if FETCH is not None:
        return FETCH(kind, arg)
    from .adapters import hatena_common, hatenabookmark, hatenastar
    try:
        if kind in ("counts", "count_one"):
            return hatenabookmark.bookmark_counts(arg, contact=contact)
        if kind == "entry":
            return hatenabookmark.entry_info_full(arg, contact=contact)
        if kind == "stars":
            return hatenastar.entry_stars(arg, contact=contact)
    except hatena_common.PublicHTTPError as exc:
        raise FetchError(exc.code) from None
    raise ValueError(kind)


class _Pace:
    """要求と要求の間を `INTERVAL_SECONDS` 空ける（最初の 1 回は待たない）。"""

    def __init__(self):
        self.calls = 0

    def __call__(self, kind, arg, contact):
        if self.calls:
            SLEEP(INTERVAL_SECONDS)
        self.calls += 1
        return _call(kind, arg, contact)


def _chunks(values, size):
    return [values[i:i + size] for i in range(0, len(values), size)]


def fetch_counts(urls, *, contact=None, pace=None) -> tuple:
    """件数を 50 件ずつの束で読む。`({url: 件数}, {url: 失敗の符丁})`。"""
    from .adapters import hatenabookmark
    pace = pace or _Pace()
    got, failed = {}, {}
    for chunk in _chunks(list(dict.fromkeys(urls)), hatenabookmark.COUNT_BATCH):
        try:
            answer = pace("counts", chunk, contact)
        except FetchError as exc:
            failed.update({u: exc.code for u in chunk})
            continue
        for u in chunk:
            value = (answer or {}).get(u)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                got[u] = value
            else:
                failed[u] = "response_invalid"
    return got, failed


def fetch_stars(urls, *, contact=None, pace=None) -> tuple:
    """スターを 50 件ずつの束で読む。`({url: [スター]}, {url: 失敗の符丁})`。https で通らない間は全部 `not_supported`。"""
    from .adapters import hatenastar
    if not hatenastar.SUPPORTED:
        return {}, {u: "not_supported" for u in urls}
    pace = pace or _Pace()
    got, failed = {}, {}
    for chunk in _chunks(list(dict.fromkeys(urls)), hatenastar.BATCH):
        try:
            answer = pace("stars", chunk, contact)
        except FetchError as exc:
            failed.update({u: exc.code for u in chunk})
            continue
        for u in chunk:
            rows = (answer or {}).get(u)
            if isinstance(rows, list):
                got[u] = rows
            else:
                failed[u] = "response_invalid"
    return got, failed


def _bookmark_part(total, entry, own) -> tuple:
    """`(bookmark の欄, missing の行の list)`。"""
    part = {"total": total, "cached": True}
    if not isinstance(entry, dict):
        part["others"] = None
        return part, [{"what": "bookmark_others", "reason": "own_unknown",
                       **({"detail": entry} if isinstance(entry, str) else {})}]
    marks = entry.get("bookmarks") or []
    selfs = sorted({b["user"] for b in marks if b.get("user") in own})
    third = [{"user": b["user"], "timestamp": b.get("timestamp"), "comment": b.get("comment") or "",
              "tags": list(b.get("tags") or [])} for b in marks if b.get("user") not in own]
    others = total - len(selfs)
    missing = []
    if others < 0:
        # 件数（キャッシュでない）より自分の分（キャッシュ）が多い——引けない。推して埋めない。
        others = None
        missing.append({"what": "bookmark_others", "reason": "own_unknown", "detail": "count_below_self"})
    part.update({"others": others, "self": selfs,
                 "comments_n": sum(1 for b in third if b["comment"].strip()), "bookmarks": third})
    return part, missing


def _star_part(stars, own) -> dict:
    others, mine, by_color = 0, 0, {}
    for star in stars:
        count = star.get("count") or 1
        if star.get("name") in own:
            mine += count
            continue
        others += count
        color = star.get("color") or "yellow"
        by_color[color] = by_color.get(color, 0) + count
    return {"others": others, "self": mine, "by_color": by_color}


def observe(items, *, own, contact=None, pace=None) -> dict:
    """配信ごとに今の値を読む。`items` は `(配信の台帳)` の list。`{delivery: {bookmark?, star?, missing}}`。

    HTTP: 件数 1 回（50 件ずつ）＋ スター 1 回（50 件ずつ）＋ エントリー情報を配信ごとに 1 回。
    """
    pace = pace or _Pace()
    urls = [record["published_url"] for record in items]
    counts, count_failed = fetch_counts(urls, contact=contact, pace=pace)
    stars, star_failed = fetch_stars(urls, contact=contact, pace=pace)
    out = {}
    for record in items:
        url = record["published_url"]
        parts, missing = {}, []
        if url in counts:
            try:
                entry = pace("entry", url, contact)
            except FetchError as exc:
                entry = exc.code
            parts["bookmark"], more = _bookmark_part(counts[url], entry, own)
            missing += more
        else:
            missing.append({"what": "bookmark", "reason": f"not_fetched:{count_failed.get(url, 'unknown')}"})
        if url in stars:
            parts["star"] = _star_part(stars[url], own)
        elif star_failed.get(url) == "not_supported":
            missing.append({"what": "star", "reason": "not_supported"})
        else:
            missing.append({"what": "star", "reason": f"not_fetched:{star_failed.get(url, 'unknown')}"})
        out[record["delivery"]] = {**parts, "missing": missing}
    return out


def make_row(record, account, observed, *, now, marks) -> dict:
    origin = origin_at(record)
    age = (now - origin).total_seconds() / 3600 if origin is not None else None
    row = {"schema": SCHEMA, "delivery": record["delivery"], "account": account,
           "article": record.get("article"), "url": record.get("published_url"),
           "published_at": jst.iso(origin) if origin is not None else None,
           "collected_at": jst.iso(now), "age_hours": round(age, 2) if age is not None else None,
           "marks": list(marks)}
    for source in SOURCES:
        if source in observed:
            row[source] = observed[source]
    row["missing"] = list(observed.get("missing") or [])
    return row


# --------------------------------------------------------------------------
# 採取（run の末尾・`thth hatena collect`）
# --------------------------------------------------------------------------

def collect_once(account, cfg, *, now, log=print, delivery=None) -> dict:
    """跨いだ配信を読んで台帳に 1 行ずつ足す。`{"looked", "touched", "errors", "waiting", "broken"}`。

    ロック・同期・commit は呼び手（`_entry`）。ここは HTTP と追記だけ。
    """
    from . import deliveries
    result = {"looked": 0, "touched": [], "errors": [], "waiting": [], "broken": []}
    records, broken = deliveries.list_for(account)
    if broken:
        result["errors"].append("delivery_unreadable")
    candidates = []
    for record in records:
        if delivery is not None and record["delivery"] != delivery:
            continue
        if not eligible(record, now):
            continue
        rows, is_broken = read_rows(cfg, account, record["delivery"])
        if is_broken:
            result["broken"].append(record["delivery"])
            result["errors"].append(f"reactions_unreadable:{record['delivery']}")
            continue
        marks = due(record, rows, now)
        if not marks:
            nxt = next_mark(record, rows, now)
            result["waiting"].append({"delivery": record["delivery"], "next": nxt})
            continue
        candidates.append((origin_at(record), record, marks))
    candidates.sort(key=lambda c: (c[0], c[1]["delivery"]))
    chosen = candidates[:MAX_DELIVERIES_PER_RUN]
    if not chosen:
        return result
    observed = observe([c[1] for c in chosen], own=self_ids(cfg), contact=contact_of(cfg))
    for _origin, record, marks in chosen:
        result["looked"] += 1
        got = observed.get(record["delivery"]) or {"missing": []}
        row = make_row(record, account, got, now=now, marks=marks)
        if not any(source in row for source in SOURCES) and all(
                m.get("reason") != "not_supported" for m in row["missing"]):
            # 何も取れなかった——書かない（次の run が同じ刻みをもう一度試す）。
            result["errors"].append(f"{record['delivery']}:" + "・".join(m["reason"] for m in row["missing"]))
            continue
        path = ledger_path(cfg, account, record["delivery"])
        _append(path, row)
        result["touched"].append(path)
        for m in row["missing"]:
            if m["reason"].startswith("not_fetched:"):
                result["errors"].append(f"{record['delivery']}:{m['what']}:{m['reason']}")
    return result


def _undo_and_log(repo_dir, log, err) -> None:
    from . import collect
    undone = collect._undo_local_commit(repo_dir)
    log(f"反応の採取を push できませんでした: {err}")
    log("送れていない採取はファイルに残しました" + ("（commit は取り消しました）。" if undone else
                                             "。**commit を取り消せませんでした。**") + " 次の実行で送り直します。")


def _entry(account, *, now, log, info, wait=0, delivery=None) -> int:
    from . import lock as lock_mod, writeback
    try:
        cfg = accounts_mod.load_account(account)
    except accounts_mod.AccountError as e:
        log(str(e))
        info["skipped"] = "no_account"
        return 2
    info["cfg"] = cfg
    state = accounts_mod.repo_state(cfg)
    if state == accounts_mod.REPO_BROKEN:
        log(f"repo を同期できないので反応を採りません: {accounts_mod.repo_problem(cfg)}")
        info["skipped"] = "repo_broken"
        return 2
    if state == accounts_mod.REPO_NONE:
        account_lock = lock_mod.AccountLock(accounts_mod.account_lock_path_for(account))
        try:
            lock_mod.acquire(account_lock, wait)
        except lock_mod.LockBusy:
            log(f"別の実行が使っているので反応の採取を見送ります: {account}")
            info["skipped"] = "locked"
            return 0
        try:
            result = collect_once(account, cfg, now=now, log=log, delivery=delivery)
        finally:
            account_lock.release()
        info["result"] = result
        if result["touched"]:
            log(f"反応を採りました: {len(result['touched'])} 配信（repo が無いので state に置きました）")
        for problem in result["errors"]:
            log(f"採れなかったもの: {problem}")
        return 1 if result["errors"] else 0
    repo_dir = cfg.get("repo_dir")
    repo_lock = lock_mod.AccountLock(accounts_mod.repo_lock_path_for(repo_dir))
    try:
        lock_mod.acquire(repo_lock, wait)
    except lock_mod.LockBusy:
        log(f"repo を別の実行が使っているので反応の採取を見送ります: {repo_dir}")
        info["skipped"] = "locked"
        return 0
    try:
        synced, sync_err, _sha = writeback.sync_repo(repo_dir)
        if not synced:
            log(f"repo を同期できないので反応を採りません: {sync_err}")
            info["skipped"] = "not_synced"
            return 2
        result = collect_once(account, cfg, now=now, log=log, delivery=delivery)
        info["result"] = result
        if result["touched"]:
            rel = [os.path.relpath(os.path.realpath(p), os.path.realpath(repo_dir)) for p in result["touched"]]
            pushed, push_err = writeback.commit_and_push(
                repo_dir, rel_path=rel, message=f"反応の採取: {len(rel)} 配信（{account}）")
            if not pushed:
                _undo_and_log(repo_dir, log, push_err)
                info["skipped"] = "not_pushed"
                return 1
            log(f"反応を採りました: {len(rel)} 配信（{account}）")
        for problem in result["errors"]:
            log(f"採れなかったもの: {problem}")
    finally:
        repo_lock.release()
    return 1 if result["errors"] else 0


def _record_run(account, *, rc, trigger, now, info) -> None:
    """runs に `action: hatena_collect` を 1 行（§4.5・`collect._record_collect_run` と同じ形）。"""
    from . import redact as redact_mod, runs as runs_mod
    if info.get("skipped") == "no_account":
        return
    result = info.get("result") or {}
    errors = list(result.get("errors") or [])
    try:
        runs_mod.append_run(accounts_mod.state_dir_for(account), {
            "account": account, "run_id": f"{ACTION}-" + jst.iso(now), "mode": "collect", "action": ACTION,
            "file": None, "post_id": None,
            # 見に行った配信の数（None は「そこまで行かなかった」・0 と混ぜない）。
            "collected": result.get("looked") if "result" in info else None,
            "refreshed": False, "quota": None, "status": "ok" if rc == 0 else "error",
            "error": (redact_mod.redact("・".join(errors)[:220]) if errors
                      else (info.get("skipped") if rc else None)),
            "trigger": trigger}, jst.month_str(now))
    except (OSError, accounts_mod.AccountError):
        pass


def run_collect(account, *, now=None, log=print, trigger=None, wait=0, delivery=None) -> int:
    """`thth run`（はてなの口座）の末尾・`thth hatena collect` の入口。runs に 1 行残す。"""
    now = now if now is not None else jst.now_jst()
    info: dict = {}
    rc = _entry(account, now=now, log=log, info=info, wait=wait, delivery=delivery)
    _record_run(account, rc=rc, trigger=trigger, now=now, info=info)
    info.pop("cfg", None)
    return rc


def last_run(account) -> dict | None:
    """最後の `hatena_collect` の runs の行（今月と先月の 2 本だけ読む）。無ければ None。"""
    state_dir = accounts_mod.state_dir_for(account)
    now = jst.now_jst()
    months = [jst.month_str(now), jst.month_str(now.replace(day=1) - datetime.timedelta(days=1))]
    for month in months:
        path = os.path.join(state_dir, f"runs-{month}.ndjson")
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("action") == ACTION:
                return row
    return None


# --------------------------------------------------------------------------
# 読む側の道具（束ね・deliveries・events・morning・doctor が使う）
# --------------------------------------------------------------------------

def by_mark(rows) -> dict:
    """`{刻み: {出所: 行}}`——同じ刻みに出所ごとの新しい行を（取り直した行が後ろ）。"""
    out = {}
    for row in sorted(rows, key=lambda r: r.get("collected_at") or ""):
        for mark in row.get("marks") or []:
            slot = out.setdefault(mark, {})
            for source in SOURCES:
                if source in row:
                    slot[source] = row
            slot.setdefault("_any", row)
            slot["_last"] = row
    return out


def latest_row(rows):
    """いちばん新しい行（`collected_at`）。無ければ None。"""
    rows = [r for r in rows if r.get("collected_at")]
    return max(rows, key=lambda r: r["collected_at"]) if rows else None


def summary_of(cfg, account, delivery) -> dict | None:
    """`thth deliveries` の 1 行に添える要約（台帳のいちばん新しい行から・読むだけ）。無ければ None。"""
    rows, broken = read_rows(cfg, account, delivery)
    if broken:
        return {"unreadable": True}
    row = latest_row(rows)
    if row is None:
        return None
    bookmark, star = row.get("bookmark") or {}, row.get("star") or {}
    return {"collected_at": row.get("collected_at"), "marks": row.get("marks"), "age_hours": row.get("age_hours"),
            "bookmark_total": bookmark.get("total"), "bookmark_others": bookmark.get("others"),
            "star_others": star.get("others"), "missing": [m.get("what") for m in row.get("missing") or []]}
