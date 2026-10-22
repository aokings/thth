"""はてなブックマークのまとめて承認（設計 4.0 S4 §20・4.1）。

1 日分の札（URL か配信 id・コメント・タグ）を JSON Lines の 1 ファイルに並べ、1 段目で全件の一覧と 1 つの
digest を見せ、2 段目で口座の state に順番待ち（`bookmark_batches/<bb_…>.json`）として置く。はてなの口座の
`thth run`（timer）が 7〜23 時の間に 1 回に 1〜2 件ずつ、間を散らして付ける。付けるときは 1 件ずつ
`hatena_bookmark.prepare`（読み直し）→ 指紋の照合 → `hatena_bookmark.execute_locked`（安全装置・owner の
ロック・柵 1〜3・1 回だけ POST・`sent/bm:`）を通す。結果不明は打ち直さない（`thth bookmark reconcile`）。

手元の口（`thth bookmark batch|batch-status|batch-cancel`）と遠くの道（`bookmark_batch_*`）が同じ関数を通る。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import random
import re

from . import accounts as accounts_mod
from . import jst

VERSION = "thth-hatena-bookmark-batch-1"
DIRECTORY = "bookmark_batches"
PACE_FILE = "pace.json"
MAX_ITEMS = 100
MAX_TEXT_BYTES = 256 * 1024
TTL_DAYS = 7
# 終わったまとめ（待ちも照合待ちの結果不明も無い）のファイルを、期限からこの日数のあとに消す（監査 中-3）。
RETAIN_DAYS = 7
# 終わったまとめの札で状態の口に残す欄（ほかの細かい欄は畳む・監査 中-3）。
FOLDED_ITEM_KEYS = ("n", "url", "state", "reason", "detail", "done_at", "permalink", "result")
# 付ける時間帯（JST の時・[始め, 終わり)）。夜中に機械的に並ぶ形を避ける（§20.6）。
ACTIVE_HOURS = (7, 23)
# 1 回の run で付ける上限（遅れていなければ 1 件）。
PER_RUN_MAX = 2
TICK_MINUTES = 10
MIN_GAP_MINUTES = 9
MAX_GAP_MINUTES = 120
RATE_LIMITED_MINUTES = 60
# はてなに打って止まった（認可の断り・打つ前の確かめの GET が通らないなど）とき、次に試すまでの分（4.2.2・S4 §24）。
# 10 分ごとに同じ札を打ち直し続けない（kopicha 10/18 に 57 回）。人が `batch-resume` で解けば待たない。
BLOCKED_HOLD_MINUTES = 60
# 乱数（試験で差し替える）。0 以上 1 未満。
RANDOM = random.random
ITEM_KEYS = frozenset(("url", "delivery", "comment", "tags", "op"))
# 札の `op`（4.2・設計 S4 §21.6）。無ければ追加。
ITEM_OPS = ("add", "edit")
BATCH_ID = re.compile(r"bb_[0-9a-f]{16}\Z")
STATES = ("pending", "placed", "unknown", "refused", "cancelled", "expired")

# 断りの符丁（遠くの道の `SAFE_ERRORS` に足す・静的な語だけ）。
REASONS = frozenset((
    "batch_empty", "batch_too_large", "batch_exists", "batch_not_found", "batch_unreadable",
    "batch_text_invalid", "batch_not_cancellable", "invalid_json", "invalid_item", "unknown_key",
    "duplicate_in_batch", "already_queued", "approval_stale"))

# 付けるときの断りの分け方（§20.8）。
_FINAL_GUARD = frozenset(("bookmark_once", "bookmark_exists_remote"))
_DEFER_TOMORROW = frozenset(("bookmark_host_limit", "self_bookmark_limit"))
_STOP_TODAY = frozenset(("bookmark_limit",))
_FINAL_BOOKMARK = frozenset(("invalid_url", "use_delivery", "bookmark_other_lane_article", "bookmark_target_not_own",
                             "comment_too_long", "comment_required", "too_many_tags", "invalid_request", "invalid_op",
                             "unsupported_operation", "approval_stale",
                             # 直し（S4 §21.6）: はてなに無い・台帳に直せる行が無い・消した・中身が同じ。
                             "bookmark_edit_missing_remote", "bookmark_not_found", "bookmark_already_deleted",
                             "bookmark_edit_no_change",
                             # 4.2.2（S4 §24）: token は生きているのにその札だけ断られた。
                             "hatena_rejected_item"))
# はてなに打たずに止まる符丁（adapter を作れない・照合を待つ）。止まっても次の刻みで見直してよい（HTTP を打たない）。
# これ以外の BookmarkError の止まりは、はてなに打って止まったので `BLOCKED_HOLD_MINUTES` 待つ（4.2.2・S4 §24）。
_NO_HTTP_BLOCK = frozenset(("token_missing", "token_invalid", "consumer_missing", "consumer_invalid",
                            "auth_unsupported", "bookmark_result_unknown"))
# 直しの札が照合を待つ（付けた・前の直しの結果がまだ分からない）。打たずに止める（S4 §21.6）。
_WAIT_RECONCILE = frozenset(("bookmark_result_unknown",))


class BatchError(ValueError):
    def __init__(self, code, *, detail=None):
        super().__init__(code)
        self.code = code
        self.detail = detail


# --------------------------------------------------------------------------
# ファイルの形（§20.2）
# --------------------------------------------------------------------------

def parse_text(text) -> list:
    """札の並びを読む。`[{"n", "line", "raw" | None, "problem" | None}]`。

    JSON Lines（空行と `#` で始まる行は飛ばす）か、全体が `[` で始まる JSON の配列。形の合わない行は
    `problem` を付けて返す（その 1 行だけ除き、ほかは進める・`--confirm-file` と同じ）。
    """
    if not isinstance(text, str):
        raise BatchError("batch_text_invalid")
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise BatchError("batch_too_large")
    stripped = text.lstrip("﻿").strip()
    entries = []
    if stripped.startswith("["):
        try:
            values = json.loads(stripped)
        except ValueError:
            raise BatchError("batch_text_invalid") from None
        if not isinstance(values, list):
            raise BatchError("batch_text_invalid")
        for index, value in enumerate(values, start=1):
            entries.append((index, value, None))
    else:
        for number, line in enumerate(text.lstrip("﻿").splitlines(), start=1):
            body = line.strip()
            if not body or body.startswith("#"):
                continue
            try:
                entries.append((number, json.loads(body), None))
            except ValueError:
                entries.append((number, None, "invalid_json"))
    if len(entries) > MAX_ITEMS:
        raise BatchError("batch_too_large", detail=len(entries))
    out = []
    for n, (line, value, problem) in enumerate(entries, start=1):
        if problem is None:
            problem = _item_problem(value)
        hint = value.get("url") or value.get("delivery") if isinstance(value, dict) else None
        out.append({"n": n, "line": line, "raw": value if problem is None else None, "problem": problem,
                    "hint": hint if isinstance(hint, str) else None})
    return out


def _item_problem(value):
    if not isinstance(value, dict):
        return "invalid_item"
    if set(value) - ITEM_KEYS:
        return "unknown_key"
    if value.get("op") is not None and value.get("op") not in ITEM_OPS:
        return "invalid_item"
    if value.get("op") == "edit" and value.get("comment") is None and value.get("tags") is None:
        return "invalid_item"
    has_url = isinstance(value.get("url"), str) and value["url"].strip() != ""
    has_delivery = isinstance(value.get("delivery"), str) and value["delivery"].strip() != ""
    if has_url == has_delivery:
        return "invalid_item"
    for key in ("url", "delivery", "comment"):
        if value.get(key) is not None and not isinstance(value[key], str):
            return "invalid_item"
    tags = value.get("tags")
    if tags is not None and not isinstance(tags, str) and not (
            isinstance(tags, list) and all(isinstance(t, str) for t in tags)):
        return "invalid_item"
    return None


def lint_problems(path) -> list:
    """`thth lint <札のファイル .jsonl>`（S4 §22）。札ごとの形とタグの長さを 1 行ずつ（error）。HTTP は打たない。"""
    from . import hatena_bookmark
    try:
        entries = parse_text(read_source(path))
    except BatchError as exc:
        return [f"{exc.code}: 札のファイルを読めません"]
    out = []
    for entry in entries:
        where = f"札 {entry['n']}（{entry['line']} 行目）"
        if entry["problem"]:
            out.append(f"{entry['problem']}: {where}")
            continue
        try:
            tags = hatena_bookmark.parse_tags(entry["raw"].get("tags"))
        except hatena_bookmark.BookmarkError:
            out.append(f"invalid_item: {where}")
            continue
        found = hatena_bookmark.long_tags(tags)
        if found:
            out.append(f"bookmark_tag_too_long: {where}: {hatena_bookmark.tag_too_long_text(found)}")
        # 4.2.2（S4 §24.1）: 本文の予告（断らない・`warning:`）。追加の札だけ（直しの札は省いた値が台帳にある）。
        if entry["raw"].get("op") != "edit":
            for w in hatena_bookmark.body_warnings(entry["raw"].get("comment"), tags):
                out.append(f"warning: {w['code']}: {where}: {w['text']}")
    return out


def read_source(source, *, stdin=None) -> str:
    """ファイル（`-` なら標準入力）を読む。読めなければ `BatchError("batch_text_invalid")`。"""
    import sys
    if source == "-":
        return (stdin or sys.stdin).read()
    try:
        with open(source, encoding="utf-8") as stream:
            return stream.read(MAX_TEXT_BYTES + 1)
    except (OSError, UnicodeDecodeError):
        raise BatchError("batch_text_invalid", detail=source) from None


# --------------------------------------------------------------------------
# 指紋（§20.3）
# --------------------------------------------------------------------------

def compute_sha(account, principal, item_shas) -> str:
    lines = [VERSION, f"account={account}", f"principal={principal}", f"n={len(item_shas)}", *item_shas]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def batch_id_of(sha) -> str:
    return "bb_" + sha[:16]


# --------------------------------------------------------------------------
# 置き場
# --------------------------------------------------------------------------

def _dir(account) -> str:
    if not accounts_mod.name_is_safe(account):
        raise BatchError("invalid_request")
    return os.path.join(accounts_mod.state_dir_for(account), DIRECTORY)


def _write_json(path, data) -> None:
    from .secrets_fs import atomic_write_json
    atomic_write_json(path, data)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise BatchError("batch_unreadable") from None


def load_batches(account) -> list:
    """口座のまとめ（承認の古い順）。読めないファイルは `batch_unreadable` の印だけを返す。"""
    directory = _dir(account)
    try:
        names = sorted(os.listdir(directory))
    except FileNotFoundError:
        return []
    out = []
    for name in names:
        if not (name.startswith("bb_") and name.endswith(".json")):
            continue
        try:
            data = _read_json(os.path.join(directory, name))
        except BatchError:
            out.append({"batch": name[:-5], "unreadable": True, "items": []})
            continue
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            # まとめの id はファイル名が正（中の `batch` と違えば名前に揃える）。`_check_integrity` が
            # ファイル名 `bb_<sha16>` と中の approved_sha を照らす。
            data["batch"] = name[:-5]
            out.append(data)
    out.sort(key=lambda b: (b.get("approved_at") or "", b.get("batch") or ""))
    return out


def _save_batch(account, batch) -> None:
    _write_json(os.path.join(_dir(account), batch["batch"] + ".json"), batch)


def _read_pace(account) -> dict:
    try:
        data = _read_json(os.path.join(_dir(account), PACE_FILE))
    except BatchError:
        data = None
    return data if isinstance(data, dict) else {}


def _save_pace(account, pace) -> None:
    _write_json(os.path.join(_dir(account), PACE_FILE), pace)


def has_pending(account) -> bool:
    """待ちの札があるか（ロックを取らずに読む・timer の早道）。"""
    try:
        return any(item.get("state") == "pending" for batch in load_batches(account)
                   for item in batch.get("items") or [])
    except BatchError:
        return False


def _queued_canonicals(principal) -> set:
    """同じ owner の全主体の全口座で、順番待ち（pending）の札の正規化 URL。"""
    from . import principals
    out = set()
    try:
        group = principals.siblings(principal)
    except Exception:  # noqa: BLE001 — 予告のためだけ（付けるときは柵 1 が断る）
        group = [(principal, None)]
    for sibling, _cfg in group:
        try:
            names = [name for name, _raw in principals.accounts_of(sibling)]
        except Exception:  # noqa: BLE001
            continue
        for name in names:
            try:
                batches = load_batches(name)
            except BatchError:
                continue
            for batch in batches:
                for item in batch.get("items") or []:
                    if item.get("state") == "pending" and item.get("url_canonical"):
                        out.add(item["url_canonical"])
    return out


# --------------------------------------------------------------------------
# 1 段目（§20.4・何も書かない）
# --------------------------------------------------------------------------

def _window(now):
    start = now.replace(hour=ACTIVE_HOURS[0], minute=0, second=0, microsecond=0)
    if ACTIVE_HOURS[1] >= 24:
        end = now.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(days=1)
    else:
        end = now.replace(hour=ACTIVE_HOURS[1], minute=0, second=0, microsecond=0)
    return start, end


def _next_window_start(now):
    start, _end = _window(now)
    if now < start:
        return start
    return start + datetime.timedelta(days=1)


def _budget(principal, cfg_principal=None, *, now=None) -> dict | None:
    """主体の今日の残り枠（全体・自分の記事）と、ホストごとの今日の数・日の枠。読めなければ None。"""
    from . import guard, principals
    try:
        principal_cfg = cfg_principal or principals.load_principal(principal)
        whole = guard.principal_summary(principal, principal_cfg, now=now)
        limits = whole["limits"]
        counts = guard.bookmark_counts(principal, now=now)
        hosts = {}
        today = (jst.to_jst(now) if now is not None else jst.now_jst()).date()
        for row in guard.principal_bookmark_rows(principal):
            times = [row.get("sent_at")] + [a.get("sent_at") for a in (row.get("attempts") or [])
                                             if isinstance(a, dict)]
            n = sum(1 for raw in times if (at := jst.parse(raw)) is not None and jst.to_jst(at).date() == today)
            host = guard._bookmark_host(row)  # noqa: SLF001 — 数え方を guard と同じにする
            if n and host:
                hosts[host] = hosts.get(host, 0) + n
        return {"total": max(0, limits["daily_max_bookmarks"] - whole["bookmarks_today"]),
                "own": max(0, limits["daily_max_self_bookmarks"] - counts["own_today"]),
                "host_limit": limits["bookmark_same_host_daily"], "hosts_today": hosts,
                "daily_total": limits["daily_max_bookmarks"], "daily_own": limits["daily_max_self_bookmarks"]}
    except Exception:  # noqa: BLE001 — 見込みは見せるためだけ（決めるのは guard.check）
        return None


def _forecast(items, budget, *, now) -> dict:
    """含める札を、日の枠（全体・自分の記事・同じホスト）の上で日に割り振った見込み（決めるのは付けるとき）。"""
    if budget is None:
        return {"today": None, "later": None, "days": None, "after_expiry": None}
    _start, end = _window(now)
    usable_today = now < end
    total = budget["total"] if usable_today else 0
    own = budget["own"] if usable_today else 0
    hosts = dict(budget["hosts_today"]) if usable_today else {}
    day = 0
    left = list(items)
    days = {}
    while left and day <= TTL_DAYS + 1:
        rest = []
        for item in left:
            host = item["host"]
            if total <= 0 or (item["target"] == "own" and own <= 0) or hosts.get(host, 0) >= budget["host_limit"]:
                rest.append(item)
                continue
            total -= 1
            if item["target"] == "own":
                own -= 1
            hosts[host] = hosts.get(host, 0) + 1
            days[item["n"]] = day
        if len(rest) == len(left) and day > 0:
            break
        left = rest
        day += 1
        total, own, hosts = budget["daily_total"], budget["daily_own"], {}
    for item in items:
        item["day"] = days.get(item["n"])
    placed_days = [d for d in days.values()]
    return {"today": sum(1 for d in placed_days if d == 0),
            "later": sum(1 for d in placed_days if d > 0),
            "days": (max(placed_days) + 1) if placed_days else 0,
            "after_expiry": sum(1 for item in items if item.get("day") is None or item["day"] >= TTL_DAYS)}


def prepare(account_name: str, cfg: dict, text: str, *, now=None) -> dict:
    """1 段目の材料（何も書かない・JSON に書ける形）。まとめ全体の断りは `BatchError`。"""
    from . import guard, hatena_bookmark, principals
    if not principals.uses_principal(cfg):
        raise BatchError("unsupported_operation")
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    entries = parse_text(text)
    principal = cfg["principal"]
    queued = _queued_canonicals(principal)
    seen = set()
    rows = []
    for entry in entries:
        row = {"n": entry["n"], "line": entry["line"], "include": False, "reason": entry["problem"], "detail": None,
               "url": None, "delivery": None, "target": None, "article": None, "host": None,
               "comment": None, "tags": [], "approved_sha": None, "url_canonical": None, "day": None}
        rows.append(row)
        raw = entry["raw"]
        if raw is None:
            row["url"] = entry.get("hint")
            continue
        row["url"] = raw.get("url")
        row["delivery"] = raw.get("delivery")
        row["comment"] = raw.get("comment")
        edit = raw.get("op") == "edit"
        try:
            if edit:
                # 直しの札（S4 §21.6）: GET は打たない（50 件の GET を続けない）。台帳の今の値と直したあとの値。
                p = hatena_bookmark.prepare_edit(account_name, cfg, delivery=raw.get("delivery"), url=raw.get("url"),
                                                 comment=raw.get("comment"), tags=raw.get("tags"),
                                                 check_remote=False)
            else:
                p = hatena_bookmark.prepare(account_name, cfg, delivery=raw.get("delivery"), url=raw.get("url"),
                                            comment=raw.get("comment") or "", tags=raw.get("tags"), op="add",
                                            check_remote=False)
        except hatena_bookmark.BookmarkError as exc:
            row["reason"] = exc.code
            if exc.code == "bookmark_tag_too_long" and exc.tags:
                # S4 §22: どのタグが何バイトか（予告して除く・黙って切らない）。
                row["detail"] = "・".join(f"{tag} {size} バイト" for tag, size in exc.tags) + \
                    f"（{hatena_bookmark.TAG_MAX_BYTES} バイト以内に）"
            if edit:
                row["op"] = "edit"
            continue
        row.update({"url": p["url"], "delivery": p["delivery"], "target": p["target"], "article": p["article"],
                    "comment": p["comment"], "tags": p["tags"], "approved_sha": p["approved_sha"],
                    "url_canonical": p["url_canonical"], "host": hatena_bookmark.host_of(p["url_canonical"]),
                    "blog_id": p.get("blog_id")})
        if edit:
            row.update({"op": "edit", "base_comment": p["base_comment"], "base_tags": p["base_tags"]})
        if p.get("warnings"):
            # 4.2.2（S4 §24.1）: 本文の予告（断らない・指紋に入れない）。符丁だけを札に添える。
            row["warnings"] = [w["code"] for w in p["warnings"]]
        if p.get("ledger"):
            row["reason"], row["detail"] = "bookmark_once", p["ledger"]
            continue
        if p["url_canonical"] in seen:
            row["reason"] = "duplicate_in_batch"
            continue
        if p["url_canonical"] in queued:
            row["reason"] = "already_queued"
            continue
        seen.add(p["url_canonical"])
        row["include"] = True
    included = [row for row in rows if row["include"]]
    budget = _budget(principal, now=now)
    # 直しの札は日の枠を使わない（S4 §21.4）: 見込みは付ける札だけで数え、直しは「今日」。
    forecast = _forecast([row for row in included if row.get("op") != "edit"], budget, now=now)
    for row in included:
        if row.get("op") == "edit":
            row["day"] = 0
    sha = compute_sha(account_name, principal, [row["approved_sha"] for row in included])
    try:
        owner = principals.owner_of(principal)
    except Exception:  # noqa: BLE001
        owner = principal
    try:
        lane_allowed = cfg.get("project") in (
            (principals.load_principal(principal).get("bookmark") or {}).get("lanes") or [])
    except Exception:  # noqa: BLE001 — 予告のためだけ
        lane_allowed = None
    halted = None
    try:
        if principals.stopped(principal) is not None:
            halted = "principal_stopped"
        elif guard.stopped(account_name) is not None:
            halted = "account_stopped"
    except Exception:  # noqa: BLE001
        halted = None
    hosts = {}
    for row in included:
        if row.get("op") != "edit":
            hosts[row["host"]] = hosts.get(row["host"], 0) + 1
    return {"account": account_name, "principal": principal, "owner": owner, "project": cfg.get("project"),
            "items": rows, "n": len(rows), "included": len(included), "excluded": len(rows) - len(included),
            "approved_sha": sha, "digest": sha[:_digest_length()], "batch": batch_id_of(sha),
            "remaining_today": ({k: budget[k] for k in ("total", "own", "host_limit")} if budget else None),
            "hosts": [{"host": h, "n": n, "today": (budget or {}).get("hosts_today", {}).get(h, 0)}
                      for h, n in sorted(hosts.items(), key=lambda kv: (-kv[1], kv[0]))],
            "forecast": forecast, "lane_allowed": lane_allowed, "stopped": halted,
            "scheduled": cfg.get("scheduled", True) is not False, "ttl_days": TTL_DAYS,
            "active_hours": list(ACTIVE_HOURS)}


def _digest_length() -> int:
    from . import approval
    return approval.APPROVE_DIGEST_LENGTH


def _short(text, width=40) -> str:
    text = str(text or "")
    return text if len(text) <= width else text[:width - 1] + "…"


def lines(p: dict) -> list:
    """1 段目の表示（手元・遠くの道で同じ）。"""
    rem = p.get("remaining_today")
    edits = sum(1 for row in p["items"] if row["include"] and row.get("op") == "edit")
    out = [f"ブックマークのまとめて承認  {p['account']}（主体 {p['principal']}・owner {p.get('owner')}）",
           f"札 {p['n']} 件: 付ける {p['included'] - edits}・除く {p['excluded']}"
           + (f"・直す {edits}" if edits else "")]
    for row in p["items"]:
        if row["include"] and row.get("op") == "edit":
            out.append(f"  {row['n']:>3}. [直す]  {row.get('url') or '?'}")
            before = "・".join(row.get("base_tags") or []) or "（なし）"
            after = "・".join(row.get("tags") or []) or "（なし）"
            out.append(f"       コメント: {row.get('base_comment') or '（なし）'} → {row.get('comment') or '（なし）'}")
            out.append(f"       タグ: {before} → {after}")
            continue
        if row["include"]:
            when = "付ける" if row.get("day") == 0 else ("翌日以降" if row.get("day") is not None else "期限までに付かない見込み")
        else:
            when = f"除く: {row['reason']}" + (f"（{row['detail']}）" if row.get("detail") else "")
        own = f"  自分の記事 {row['delivery']}" if row.get("target") == "own" else ""
        tags = "・".join(row.get("tags") or []) or "（なし）"
        out.append(f"  {row['n']:>3}. [{when}]{own}  {row.get('url') or '?'}")
        if row["include"]:
            # コメントは**全文**で見せる（承認の指紋は全文から作る・上限は 100 字。切って見せない）。
            out.append(f"       コメント: {row.get('comment') or ''}  タグ: {tags}")
            if row.get("target") == "own":
                out.append("       （自分の記事です。運営者であることが分かる文にしてください。例: 自分の記事。…）")
            if row.get("warnings"):
                out.append("       予告（断りません）: " + "・".join(row["warnings"])
                           + "（はてなに 401 で断られた札と同じ形・断られたらその札だけ外して進めます）")
    if p["hosts"]:
        limit = rem["host_limit"] if rem else "?"
        out.append("ホストごと（付ける札・今日もう付けた数・日の枠 " + str(limit) + "）: "
                   + "、".join(f"{h['host']} {h['n']}（今日 {h['today']}）" for h in p["hosts"][:12])
                   + ("…" if len(p["hosts"]) > 12 else ""))
    if rem:
        out.append(f"主体の今日の残り枠: 全体 {rem['total']}・自分の記事 {rem['own']}")
    fc = p.get("forecast") or {}
    if fc.get("today") is not None:
        out.append(f"見込み: 今日 {fc['today']} 件・翌日以降 {fc['later']} 件・付け終わるまで {fc['days']} 日"
                   f"（期限 {p['ttl_days']} 日。期限までに付かない見込み {fc['after_expiry']} 件）")
    start, end = p.get("active_hours") or ACTIVE_HOURS
    out.append(f"付け方: timer が {start}〜{end} 時に 1 回 1 件ずつ間を散らして付けます（日の枠・同じホスト・"
               "連投の窓の中で。同じホストの枠を超える分は翌日へ回します）")
    out.append("柵: 台帳（柵 1）は今見ました。公開のエントリー情報（柵 2）とこの主体の GET（柵 3）は付ける直前に"
               " 1 件ずつ見て、当たれば断って理由を残します（batch-status で見られます）")
    if p.get("lane_allowed") is False:
        from .hatena_bookmark import lane_refusal_line
        out.append(lane_refusal_line(p["principal"], p.get("project")))
    if p.get("stopped"):
        out.append(f"→ 2 段目で断ります（{p['stopped']}）: 止めている間は札を積みません")
    if not p.get("scheduled", True):
        out.append("→ 2 段目で断ります（schedule_unavailable）: timer に載っていない口座（scheduled: false）です")
    out.append(f"digest: {p['digest']}")
    return out


# --------------------------------------------------------------------------
# 2 段目（§20.5・口座のロックの中で呼ぶ）
# --------------------------------------------------------------------------

def approve_locked(account_name: str, cfg: dict, p: dict, *, actor, via, credential=None, now=None,
                   approval=None) -> dict:
    """1 段目の材料 `p`（ロックの中で作り直したもの）を順番待ちに置く。

    `approval`（4.5.3）: hub の承認の番号（CLI の `--approval`）。あればまとめのファイルに残す
    （無ければ鍵ごと書かない——4.5.2 までのまとめと同じ形）。
    """
    from . import guard, principals
    if not p["included"]:
        raise BatchError("batch_empty")
    if cfg.get("scheduled", True) is False:
        raise BatchError("schedule_unavailable")
    if principals.stopped(p["principal"]) is not None:
        raise guard.GuardRefused("principal_stopped")
    if guard.stopped(account_name) is not None:
        raise guard.GuardRefused("account_stopped")
    if p.get("lane_allowed") is False:
        raise guard.GuardRefused("bookmark_not_allowed_for_lane")
    if any(b.get("approved_sha") == p["approved_sha"] for b in load_batches(account_name)):
        raise BatchError("batch_exists")
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    items = []
    for row in p["items"]:
        if not row["include"]:
            continue
        entry = {"n": row["n"], "line": row["line"], "url": row["url"], "url_canonical": row["url_canonical"],
                 "delivery": row["delivery"], "target": row["target"], "article": row["article"],
                 "comment": row["comment"], "tags": list(row["tags"]), "approved_sha": row["approved_sha"],
                 "state": "pending", "not_before": None, "wait": None, "tries": 0, "last_tried_at": None,
                 "reason": None, "detail": None, "done_at": None, "post_id": None, "permalink": None}
        if row.get("op") == "edit":
            # 直しの札（S4 §21.6）。追加の札の形は 1 バイトも変えない。
            entry.update({"op": "edit", "base_comment": row["base_comment"], "base_tags": list(row["base_tags"])})
        items.append(entry)
    batch = {"version": 1, "batch": p["batch"], "approved_sha": p["approved_sha"], "digest": p["digest"],
             "account": account_name, "principal": p["principal"], "owner": p.get("owner"),
             "approved_by": actor, "approved_at": jst.iso(now), "via": via, "credential": credential,
             "expires_at": jst.iso(now + datetime.timedelta(days=TTL_DAYS)),
             "cancelled_at": None, "cancelled_by": None,
             "excluded": [{"n": r["n"], "line": r["line"], "url": r.get("url"), "reason": r["reason"],
                           "detail": r.get("detail")} for r in p["items"] if not r["include"]],
             "items": items}
    if approval:
        batch["approval"] = approval
    _save_batch(account_name, batch)
    try:
        from . import admin_log
        admin_log.append("bookmark_batch_approved", account_name, cfg, by=actor or "thth", via=via or "cli",
                         diff={"items": [0, len(items)]})
    except Exception:  # noqa: BLE001 — 正本はまとめのファイル。変更ログに残せなくても札は変わらない
        pass
    return {"account": account_name, "status": "queued", "batch": batch["batch"], "digest": batch["digest"],
            "queued": len(items), "excluded": len(batch["excluded"]), "expires_at": batch["expires_at"],
            "approved_by": actor, **({"approval": approval} if approval else {})}


def cancel_locked(account_name: str, batch_id: str, *, by, item=None, now=None) -> dict:
    """待ちの札を取り消す（口座のロックの中・1 段・止める向き）。付けたものは消さない。"""
    if not isinstance(batch_id, str) or not BATCH_ID.match(batch_id):
        raise BatchError("batch_not_found")
    batch = _read_json(os.path.join(_dir(account_name), batch_id + ".json"))
    if not isinstance(batch, dict):
        raise BatchError("batch_not_found")
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    at = jst.iso(now)
    targets = [it for it in batch["items"] if item is None or it.get("n") == item]
    if item is not None and not targets:
        raise BatchError("batch_not_found")
    cancelled = 0
    for it in targets:
        if it.get("state") == "pending":
            it.update({"state": "cancelled", "done_at": at, "reason": "cancelled", "detail": by})
            cancelled += 1
    if not cancelled and item is not None:
        raise BatchError("batch_not_cancellable")
    if item is None:
        batch["cancelled_at"], batch["cancelled_by"] = at, by
    _save_batch(account_name, batch)
    return {"account": account_name, "batch": batch_id, "status": "cancelled", "cancelled": cancelled,
            "item": item, "by": by}


# --------------------------------------------------------------------------
# 読む（§20.9）
# --------------------------------------------------------------------------

def _sent_result(account, post_id):
    from . import sent as sent_mod
    if not post_id:
        return None
    try:
        row = sent_mod.read(accounts_mod.state_dir_for(account), post_id)
    except Exception:  # noqa: BLE001
        return None
    return (row or {}).get("result")


def _edit_result(principal, item):
    """直しの札の今の結果（監査 中-3）: 主体の全口座の `sent/bm:` の行を引き、結果不明の直しがあれば `unknown`。

    直しは `sent/bm:` の `result`（付けたときの結果・`confirmed` のまま）に出ないので、`attempts[]` を見る。
    行が無い・読めなければ None。消した行（`retracted_at`）は照合から外すので `retracted`。
    """
    from . import hatena_bookmark, sent as sent_mod
    if not principal or not item.get("url_canonical"):
        return None
    try:
        found = hatena_bookmark._ledger_row(principal, item["url_canonical"])  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return None
    if found is None:
        return None
    row = found[1]
    if row.get("retracted_at"):
        return "retracted"
    return "unknown" if sent_mod.pending_bookmark_edit(row) is not None else row.get("result")


def _item_result(account_name, batch, item):
    """札の今の結果（付ける札は `sent/bm:` の `result`・直しの札は `_edit_result`）。"""
    if item.get("op") == "edit":
        return _edit_result(batch.get("principal"), item)
    return _sent_result(account_name, item.get("post_id"))


def finished(account_name, batch, *, now=None) -> bool:
    """終わったまとめか: 読めて、待ち（期限前の pending）が無く、結果不明の札が照合を済ませている（`sent/` の
    `result` が unknown でない）。終わったまとめは状態の口で畳み、期限から `RETAIN_DAYS` 日のあとに消す。"""
    if batch.get("unreadable"):
        return False
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    expires = jst.parse(batch.get("expires_at"))
    for it in batch.get("items") or []:
        state = it.get("state")
        if state == "pending" and not (expires is not None and now > expires):
            return False
        if state == "unknown" and _item_result(account_name, batch, it) in (None, "unknown"):
            return False
    return True


def prunable(account_name, *, now=None) -> list:
    """消してよいまとめの id（終わっていて、期限から `RETAIN_DAYS` 日を過ぎた）。読むだけ・ロックは要らない。"""
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    out = []
    try:
        batches = load_batches(account_name)
    except BatchError:
        return out
    for b in batches:
        expires = jst.parse(b.get("expires_at"))
        if expires is None or now <= expires + datetime.timedelta(days=RETAIN_DAYS):
            continue
        if finished(account_name, b, now=now) and BATCH_ID.match(str(b.get("batch") or "")):
            out.append(b["batch"])
    return out


def prune_locked(account_name, *, now=None, log=None) -> list:
    """終わったまとめのファイルを消す（口座のロックの中・監査 中-3）。消した id の list。

    消すのは THTH が書いた順番待ちの写しだけ（付けたブックマークの記録は `sent/bm:` が正本で、触らない）。
    """
    removed = []
    for batch_id in prunable(account_name, now=now):
        try:
            os.unlink(os.path.join(_dir(account_name), batch_id + ".json"))
        except FileNotFoundError:
            continue
        removed.append(batch_id)
    if removed and log is not None:
        log(f"ブックマークのまとめ: 終わったまとめ {len(removed)} 本のファイルを片づけました"
            f"（期限から {RETAIN_DAYS} 日・付けた記録は sent/ に残っています）")
    return removed


def attention(account_name, *, now=None) -> dict | None:
    """人の手が要るまとめ（`thth morning`・`thth board` の 1 行・監査 低-6）。無ければ None。

    - `blocked`: 待ちの札があるのに timer が止まっている（`pace.json` の `blocked`・主体／口座の止め・
      本番でない・はてなの断りなど）。
    - `unknown`: 結果不明で照合していない札の数（`thth bookmark reconcile` を待つ）。
    """
    try:
        batches = load_batches(account_name)
    except BatchError:
        return None
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    pending = unknown = unreadable = 0
    for b in batches:
        if b.get("unreadable"):
            unreadable += 1
            continue
        expires = jst.parse(b.get("expires_at"))
        for it in b.get("items") or []:
            if it.get("state") == "pending" and not (expires is not None and now > expires):
                pending += 1
            elif it.get("state") == "unknown" and _item_result(account_name, b, it) in (None, "unknown"):
                unknown += 1
    blocked = _read_pace(account_name).get("blocked") if pending else None
    if not (blocked or unknown or unreadable):
        return None
    return {"pending": pending, "blocked": blocked, "unknown": unknown, "unreadable": unreadable}


def attention_line(account_name, value) -> str | None:
    """`attention` の人向けの 1 行（無ければ None）。"""
    if not value:
        return None
    parts = []
    blocked = value.get("blocked")
    if blocked:
        parts.append(f"止まっています（{blocked.get('code')}"
                     + (f"・{blocked.get('reason')}" if blocked.get("reason") else "")
                     + f"・待ち {value['pending']} 件・{_hm(blocked.get('at'))} から"
                     + (f"・{_hm(blocked['until'])} まで打ちません" if blocked.get("until") else "") + "）")
    if value.get("unknown"):
        parts.append(f"結果不明 {value['unknown']} 件 → thth bookmark reconcile {account_name} --by <名前>")
    if value.get("unreadable"):
        parts.append(f"読めないまとめ {value['unreadable']} 本")
    return "ブックマークのまとめ: " + "・".join(parts) + f"（thth bookmark batch-status {account_name}）"


def _fold(item) -> dict:
    return {key: item.get(key) for key in FOLDED_ITEM_KEYS}


def status(account_name: str, *, batch=None, now=None) -> dict:
    """まとめごとの数・札の状態と理由・次に付ける時刻・止まっている理由（読むだけ）。

    終わったまとめ（`finished`）は、`--batch` で名指ししない限り、札を `FOLDED_ITEM_KEYS` だけに畳む
    （`folded: true`・監査 中-3・遠くの道の答えを小さく）。除いた札（`excluded`）は今の形のまま。
    """
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    batches = load_batches(account_name)
    named = batch is not None
    if batch is not None:
        batches = [b for b in batches if b.get("batch") == batch]
        if not batches:
            raise BatchError("batch_not_found")
    out = []
    pending = 0
    for b in batches:
        if b.get("unreadable"):
            out.append({"batch": b["batch"], "unreadable": True})
            continue
        counts = {state: 0 for state in STATES}
        items = []
        expires = jst.parse(b.get("expires_at"))
        for it in b["items"]:
            state = it.get("state")
            if state == "pending" and expires is not None and now > expires:
                state = "expired"
            counts[state] = counts.get(state, 0) + 1
            pending += state == "pending"
            items.append({"n": it.get("n"), "url": it.get("url"), "target": it.get("target"),
                          "delivery": it.get("delivery"), "state": state, "reason": it.get("reason"),
                          "detail": it.get("detail"), "wait": it.get("wait"), "not_before": it.get("not_before"),
                          "tries": it.get("tries"), "done_at": it.get("done_at"), "post_id": it.get("post_id"),
                          "permalink": it.get("permalink"), "result": _item_result(account_name, b, it),
                          **({"op": "edit"} if it.get("op") == "edit" else {})})
        row = {"batch": b["batch"], "digest": b.get("digest"), "approved_by": b.get("approved_by"),
               "approved_at": b.get("approved_at"), "expires_at": b.get("expires_at"),
               "cancelled_at": b.get("cancelled_at"), "counts": counts, "items": items,
               "excluded": b.get("excluded") or []}
        if not named and finished(account_name, b, now=now):
            # 除いた札（`excluded`）は今の答えの形のまま残す（1 段目で見せた理由・数は 100 まで）。
            row.update({"folded": True, "items": [_fold(it) for it in items]})
        out.append(row)
    pace = _read_pace(account_name)
    return {"account": account_name, "pending": pending, "next_at": pace.get("next_at"),
            "blocked": pace.get("blocked"), "last_run_at": pace.get("last_run_at"), "batches": out}


def status_lines(s: dict) -> list:
    out = [f"{s['account']}: ブックマークのまとめ {len(s['batches'])} 本・待ち {s['pending']} 件"
           + (f"・次に付けるのは {_hm(s['next_at'])} 以降" if s.get("next_at") and s["pending"] else "")]
    blocked = s.get("blocked")
    if blocked and s["pending"]:
        out.append(f"  止まっています: {blocked.get('code')}"
                   + (f"（{blocked.get('reason')}）" if blocked.get("reason") else "") + f"  {_hm(blocked.get('at'))}")
        if blocked.get("until"):
            out.append(f"  {_hm(blocked['until'])} まではてなに打ちません。直したら今すぐ: "
                       f"thth bookmark batch-resume {s['account']} --by <名前>")
    for b in s["batches"]:
        if b.get("unreadable"):
            out.append(f"  {b['batch']}: 読めません")
            continue
        c = b["counts"]
        if b.get("folded"):
            out.append(f"  {b['batch']}（{_hm(b['approved_at'])} {b['approved_by']} 承認・終わり）: "
                       f"付けた {c['placed']}・結果不明 {c['unknown']}・断った {c['refused']}・"
                       f"取り消し {c['cancelled']}・期限切れ {c['expired']}"
                       f"（畳んでいます。細目は --batch {b['batch']}）")
            for it in b["items"]:
                if it["state"] in ("refused", "unknown"):
                    why = (it.get("reason") or "") + (f"（{it['detail']}）" if it.get("detail") else "")
                    out.append(f"    {it['n']:>3}. {it['state']}  {why}  {it.get('url')}")
            continue
        out.append(f"  {b['batch']}（{_hm(b['approved_at'])} {b['approved_by']} 承認・期限 {_hm(b['expires_at'])}）: "
                   f"付けた {c['placed']}・待ち {c['pending']}・結果不明 {c['unknown']}・断った {c['refused']}・"
                   f"取り消し {c['cancelled']}・期限切れ {c['expired']}")
        for it in b["items"]:
            if it["state"] == "placed":
                continue
            why = it.get("reason") or it.get("wait") or ""
            if it.get("detail") and it["state"] != "cancelled":
                why += f"（{it['detail']}）"
            if it["state"] == "pending" and it.get("not_before"):
                why = (why + " " if why else "") + f"{_hm(it['not_before'])} から"
            extra = f"  {why}" if why else ""
            if it["state"] == "unknown":
                extra += "  → thth bookmark reconcile " + s["account"]
            out.append(f"    {it['n']:>3}. {it['state']}{extra}  {it.get('url')}")
    return out


def _hm(value) -> str:
    when = jst.parse(value) if value else None
    return jst.to_jst(when).strftime("%m/%d %H:%M") if when else "?"


# --------------------------------------------------------------------------
# timer（§20.6〜§20.8）
# --------------------------------------------------------------------------

def _gap_minutes(now, left_today) -> float:
    _start, end = _window(now)
    minutes_left = max(0.0, (end - now).total_seconds() / 60)
    mean = minutes_left / max(1, left_today)
    return min(MAX_GAP_MINUTES, max(MIN_GAP_MINUTES, mean * (0.6 + 0.8 * RANDOM())))


def _candidates(batches, now):
    out = []
    for b in batches:
        if b.get("unreadable"):
            continue
        for it in b["items"]:
            if it.get("state") != "pending":
                continue
            nb = jst.parse(it.get("not_before")) if it.get("not_before") else None
            if nb is not None and now < nb:
                continue
            out.append((b, it))
    return out


def _expire(batches, now) -> list:
    changed = []
    for b in batches:
        expires = jst.parse(b.get("expires_at"))
        if b.get("unreadable") or expires is None or now <= expires:
            continue
        for it in b["items"]:
            if it.get("state") == "pending":
                it.update({"state": "expired", "done_at": jst.iso(now), "reason": "expired"})
                if b not in changed:
                    changed.append(b)
    return changed


def _check_integrity(batch) -> bool:
    # ファイル名 `bb_<sha16>` と中の approved_sha を照らす（別の名前に置き換えられたまとめを付けない）。
    if batch.get("batch") != batch_id_of(str(batch.get("approved_sha") or "")):
        return False
    shas = [it.get("approved_sha") or "" for it in batch["items"]]
    return compute_sha(batch.get("account"), batch.get("principal"), shas) == batch.get("approved_sha")


def run_due(account_name: str, cfg: dict, *, log=print, now=None, wait=0, adapter_factory=None) -> dict | None:
    """timer の 1 回分（`thth run` の末尾）。待ちが無ければ None（ロックも取らない）。"""
    from . import core, lock as lock_mod, principals
    if not principals.uses_principal(cfg):
        return None
    if not has_pending(account_name):
        # 待ちが無ければ付けない。終わったまとめの片づけだけ（消すものがあるときだけロックを取る・中-3）。
        if prunable(account_name, now=now):
            state_dir = accounts_mod.state_dir_for(account_name)
            try:
                with core._account_locks(account_name, cfg, state_dir, wait=wait):  # noqa: SLF001
                    prune_locked(account_name, now=now, log=log)
            except lock_mod.LockBusy:
                pass
        return None
    state_dir = accounts_mod.state_dir_for(account_name)
    try:
        with core._account_locks(account_name, cfg, state_dir, wait=wait):  # noqa: SLF001 — 同じ順のロック
            return _run_locked(account_name, cfg, log=log, now=now, adapter_factory=adapter_factory)
    except lock_mod.LockBusy:
        log("ブックマークのまとめ: ロックが取れないので次の刻みで付けます")
        return {"status": "busy", "placed": 0}


def _run_locked(account_name, cfg, *, log, now, adapter_factory):
    from . import hatena_bookmark
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    cfg = accounts_mod.load_account(account_name)   # ロックの中で読み直す
    prune_locked(account_name, now=now, log=log)    # 終わったまとめの片づけ（中-3）
    batches = [b for b in load_batches(account_name)]
    dirty = {b["batch"]: b for b in _expire(batches, now)}
    pace = _read_pace(account_name)
    pace["last_run_at"] = jst.iso(now)
    summary = {"status": "idle", "placed": 0, "refused": 0, "unknown": 0, "deferred": 0}

    def finish(status_word, *, next_at=None, blocked=None):
        if next_at is not None:
            pace["next_at"] = jst.iso(next_at)
        pace["blocked"] = blocked
        for b in dirty.values():
            _save_batch(account_name, b)
        _save_pace(account_name, pace)
        summary["status"] = status_word
        if blocked:
            summary["blocked"] = blocked
        return summary

    start, end = _window(now)
    if not (start <= now < end):
        return finish("outside_hours", blocked=pace.get("blocked"))
    nxt = jst.parse(pace.get("next_at")) if pace.get("next_at") else None
    if nxt is not None and now < nxt:
        return finish("waiting", blocked=pace.get("blocked"))
    # 段 3 の監査 低-3（段 4 で入れた）: 結果不明の札が照合を待っている間は次の札へ進まない
    # （はてなが 5xx を返している間に打ち続けない）。解くのは `thth bookmark reconcile`。
    waiting = unresolved_unknown(account_name, batches)
    if waiting:
        return finish("blocked", blocked={"code": "bookmark_unknown", "reason": "reconcile", "at": jst.iso(now),
                                          "n": waiting})
    if cfg.get("production") is not True:
        return finish("blocked", blocked={"code": "production_disabled", "reason": None, "at": jst.iso(now)})
    candidates = _candidates(batches, now)
    if not candidates:
        return finish("idle")
    budget = _budget(cfg["principal"], now=now)
    left_today = len(candidates) if budget is None else min(len(candidates), budget["total"])
    ticks_left = max(1, int((end - now).total_seconds() // (TICK_MINUTES * 60)))
    per_run = PER_RUN_MAX if left_today > ticks_left else 1
    hosts_today = dict((budget or {}).get("hosts_today") or {})
    own_left = (budget or {}).get("own")
    tomorrow = _next_window_start(now)
    placed = 0
    attempts = 0
    for batch, item in candidates:
        # 1 回の run で HTTP を打つ札は予算 + 2 件まで（柵 2・3 に断られ続けても叩き続けない）。
        if placed >= per_run or attempts >= per_run + 2:
            break
        host = hatena_bookmark.host_of(item["url_canonical"]) if item.get("url_canonical") else None
        edit = item.get("op") == "edit"
        # 日の枠で先に回す（最後に決めるのは guard.check）。直しの札は日の枠を使わない（S4 §21.4）。
        if not edit and budget is not None and host and hosts_today.get(host, 0) >= budget["host_limit"]:
            _defer(item, "bookmark_host_limit", tomorrow, now)
            dirty[batch["batch"]] = batch
            summary["deferred"] += 1
            continue
        if (not edit and budget is not None and item.get("target") == "own" and own_left is not None
                and own_left <= 0):
            _defer(item, "self_bookmark_limit", tomorrow, now)
            dirty[batch["batch"]] = batch
            summary["deferred"] += 1
            continue
        dirty[batch["batch"]] = batch
        attempts += 1
        item["tries"] = int(item.get("tries") or 0) + 1
        item["last_tried_at"] = jst.iso(now)
        outcome = _place(account_name, cfg, batch, item, now=now, log=log, adapter_factory=adapter_factory)
        kind = outcome[0]
        if kind == "placed":
            placed += 1
            summary["placed"] += 1
            if edit:
                summary["edited"] = summary.get("edited", 0) + 1
                continue
            if host:
                hosts_today[host] = hosts_today.get(host, 0) + 1
            if item.get("target") == "own" and own_left is not None:
                own_left -= 1
            continue
        if kind == "refused":
            summary["refused"] += 1
            continue
        if kind == "defer":
            summary["deferred"] += 1
            _defer(item, outcome[1], tomorrow, now)
            continue
        if kind == "unknown":
            summary["unknown"] += 1
            # 照合まで止める（低-3）。記録を書けなかった札（post_id 無し）は照合できないので止めない。
            hold = ({"code": "bookmark_unknown", "reason": "reconcile", "at": jst.iso(now), "n": 1}
                    if unresolved_unknown(account_name, [batch]) else None)
            return finish("unknown", next_at=now + datetime.timedelta(minutes=MIN_GAP_MINUTES), blocked=hold)
        if kind == "stop_today":
            item["wait"] = outcome[1]
            return finish("limit", next_at=tomorrow)
        if kind == "wait":
            item["wait"] = outcome[1]
            return finish("waiting", next_at=outcome[2])
        if kind == "blocked_http":
            # はてなに打って止まった（4.2.2・S4 §24）: `BLOCKED_HOLD_MINUTES` は次の run も打たない。
            item["wait"] = outcome[1]
            until = now + datetime.timedelta(minutes=BLOCKED_HOLD_MINUTES)
            log(f"ブックマークのまとめ: 止まりました（{outcome[1]}）。{_hm(jst.iso(until))} まではてなに打ちません"
                f"（直したら thth bookmark batch-resume {account_name} --by <名前>）")
            return finish("blocked", next_at=until,
                          blocked={"code": outcome[1], "reason": outcome[2], "at": jst.iso(now),
                                   "until": jst.iso(until)})
        # blocked
        item["wait"] = outcome[1]
        return finish("blocked", blocked={"code": outcome[1], "reason": outcome[2], "at": jst.iso(now)})
    remaining = len([1 for _b, it in _candidates(batches, now) if it.get("state") == "pending"])
    if budget is not None:
        remaining = min(remaining, max(0, budget["total"] - placed))
    if placed:
        gap = _gap_minutes(now, max(1, remaining))
        log(f"ブックマークのまとめ: {placed} 件付けました（待ち {remaining} 件・次は {gap:.0f} 分あと以降）")
        return finish("placed", next_at=now + datetime.timedelta(minutes=gap))
    return finish("idle")


def unresolved_unknown(account_name, batches) -> int:
    """照合を待つ結果不明の札の数（付ける札は `sent/bm:` の `result: unknown`・直しの札は結果不明の直し）。"""
    from . import sent as sent_mod
    n = 0
    for b in batches:
        if b.get("unreadable"):
            continue
        for it in b.get("items") or []:
            if it.get("state") != "unknown" or not it.get("post_id"):
                continue
            if it.get("op") == "edit":
                # 直しの記録は行を持つ口座（holder）にある。主体の全口座から引く（監査 中-2）。
                n += _edit_result(b.get("principal"), it) == "unknown"
                continue
            try:
                row = sent_mod.read(accounts_mod.state_dir_for(account_name), it["post_id"])
            except Exception:  # noqa: BLE001 — 読めなければ止める側
                n += 1
                continue
            if row is None:
                continue
            n += row.get("result") == "unknown"
    return n


def _item_detail(exc):
    """札の断りの詳細（4.2.2 で足した符丁だけ・ほかの符丁の詳細は今どおり None）。"""
    code = getattr(exc, "code", None)
    if code in ("hatena_rejected_item", "hatena_auth_rejected") and getattr(exc, "status", None):
        return f"HTTP {exc.status}"
    return None


def resume_locked(account_name: str, *, by, now=None) -> dict:
    """止まったまとめを今すぐ見直させる（口座のロックの中・1 段・4.2.2・S4 §24）。

    `pace.json` の `blocked` と `next_at` を外すだけ。札・承認の指紋・`sent/` は触らない。次の `thth run` が
    今どおりの柵（安全装置・柵 1〜3）を通して付ける（まだ直っていなければ、また止まる）。
    """
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    pace = _read_pace(account_name)
    blocked = pace.get("blocked")
    pace["blocked"] = None
    pace["next_at"] = None
    pace["resumed_at"], pace["resumed_by"] = jst.iso(now), by
    _save_pace(account_name, pace)
    try:
        from . import admin_log
        admin_log.append("bookmark_batch_resumed", account_name, accounts_mod.load_account(account_name),
                         by=by or "thth", via="cli", diff={"blocked": [(blocked or {}).get("code"), None]})
    except Exception:  # noqa: BLE001 — 正本は pace.json。変更ログに残せなくても解いたことは変わらない
        pass
    return {"account": account_name, "status": "resumed", "was_blocked": blocked, "by": by,
            "pending": sum(1 for b in load_batches(account_name) if not b.get("unreadable")
                           for it in b.get("items") or [] if it.get("state") == "pending")}


def _defer(item, code, tomorrow, now):
    item["wait"] = code
    item["not_before"] = jst.iso(tomorrow)


def _own_article_pending(account_name, item) -> bool:
    """自分の記事の札で、配信の台帳が読めて、消えて（deleted）おらず、published でもない（更新中など）。"""
    from . import deliveries as deliveries_mod
    if not item.get("delivery"):
        return False
    try:
        record = deliveries_mod.read(account_name, item["delivery"])
    except deliveries_mod.DeliveryError:
        return False
    return record.get("state") not in ("deleted", "published")


def _place(account_name, cfg, batch, item, *, now, log, adapter_factory):
    """1 件を付ける。`(種類, 符丁, 詳細|時刻)`。種類は placed・refused・defer・unknown・stop_today・wait・blocked。"""
    from . import guard, hatena_bookmark
    at = jst.iso(now)

    def refuse(code, detail=None):
        item.update({"state": "refused", "reason": code, "detail": detail, "done_at": at, "wait": None})
        log(f"ブックマークのまとめ: 断りました（{code}{'・' + detail if detail else ''}）: {item.get('url')}")
        return ("refused", code, detail)

    if not _check_integrity(batch):
        return refuse("approval_stale", "batch")
    if item.get("op") == "edit":
        return _place_edit(account_name, cfg, batch, item, now=now, log=log, adapter_factory=adapter_factory,
                           refuse=refuse)
    try:
        p = hatena_bookmark.prepare(account_name, cfg, delivery=item.get("delivery"),
                                    url=None if item.get("delivery") else item.get("url"),
                                    comment=item.get("comment") or "", tags=list(item.get("tags") or []),
                                    op="add", check_remote=False)
    except hatena_bookmark.BookmarkError as exc:
        if exc.code == "bookmark_target_not_own" and _own_article_pending(account_name, item):
            # 自分の記事の配信が published でない（更新中など）: 最終の断りにせず翌日へ回す。
            return ("defer", exc.code, None)
        return refuse(exc.code, _item_detail(exc))
    if p["approved_sha"] != item.get("approved_sha"):
        return refuse("approval_stale")
    logs = []
    try:
        done = hatena_bookmark.execute_locked(account_name, cfg, p, actor=batch.get("approved_by") or "thth",
                                              via="timer", adapter_factory=adapter_factory, log=logs.append)
    except guard.GuardRefused as exc:
        code = str(exc)
        detail = getattr(exc, "reason", None)
        if code in _FINAL_GUARD:
            return refuse(code, detail)
        if code in _DEFER_TOMORROW:
            return ("defer", code, None)
        if code in _STOP_TODAY:
            return ("stop_today", code, None)
        next_at = jst.parse(getattr(exc, "next_at", None)) if getattr(exc, "next_at", None) else None
        if next_at is not None:
            return ("wait", code, next_at)
        return ("blocked", code, detail)
    except hatena_bookmark.BookmarkError as exc:
        code = exc.code
        if code == "bookmark_target_not_own" and _own_article_pending(account_name, item):
            # 自分の記事の配信が published でない（更新中など）: 最終の断りにせず翌日へ回す。
            return ("defer", code, None)
        if code in _FINAL_BOOKMARK or (code.startswith("hatena_http_4")
                                       and code not in ("hatena_http_401", "hatena_http_403", "hatena_http_429")):
            return refuse(code, _item_detail(exc))
        if code == "bookmark_record_failed":
            item.update({"state": "unknown", "reason": code, "done_at": at})
            for line in logs:
                log(line)
            return ("unknown", code, None)
        if code in ("provider_rate_limited", "hatena_http_429"):
            # はてなの 429 も「止まる」でなく「待つ」（provider_rate_limited と同じ分だけ）。
            return ("wait", code, now + datetime.timedelta(minutes=RATE_LIMITED_MINUTES))
        return ("blocked" if code in _NO_HTTP_BLOCK else "blocked_http", code, _item_detail(exc))
    for line in logs:
        log(line)
    if done.get("status") == "unknown":
        item.update({"state": "unknown", "reason": done.get("error") or "unknown", "done_at": at,
                     "post_id": done.get("post_id"), "wait": None})
        log(f"ブックマークのまとめ: 結果が分かりません（{done.get('error')}）: {item.get('url')}。"
            f"打ち直しません。thth bookmark reconcile {account_name} で確かめてください")
        return ("unknown", done.get("error"), None)
    item.update({"state": "placed", "reason": None, "detail": None, "done_at": at, "wait": None,
                 "post_id": done.get("post_id"), "permalink": done.get("permalink")})
    return ("placed", None, None)


def _place_edit(account_name, cfg, batch, item, *, now, log, adapter_factory, refuse):
    """直しの札 1 件（S4 §21.6）。読み直し → 指紋の照合 → `edit_locked`。断りの分け方は付ける札と同じ表に直しの符丁。"""
    from . import guard, hatena_bookmark
    at = jst.iso(now)
    try:
        p = hatena_bookmark.prepare_edit(account_name, cfg, delivery=item.get("delivery"),
                                         url=None if item.get("delivery") else item.get("url"),
                                         comment=item.get("comment") or "", tags=list(item.get("tags") or []),
                                         check_remote=False)
    except hatena_bookmark.BookmarkError as exc:
        if exc.code in _WAIT_RECONCILE:
            return ("blocked", exc.code, None)
        return refuse(exc.code, _item_detail(exc))
    if p["approved_sha"] != item.get("approved_sha"):
        return refuse("approval_stale")
    logs = []
    try:
        done = hatena_bookmark.edit_locked(account_name, cfg, p, actor=batch.get("approved_by") or "thth",
                                           via="timer", adapter_factory=adapter_factory, log=logs.append)
    except guard.GuardRefused as exc:
        code = str(exc)
        next_at = jst.parse(getattr(exc, "next_at", None)) if getattr(exc, "next_at", None) else None
        if next_at is not None:
            return ("wait", code, next_at)
        return ("blocked", code, getattr(exc, "reason", None))
    except hatena_bookmark.BookmarkError as exc:
        code = exc.code
        if code in _FINAL_BOOKMARK or (code.startswith("hatena_http_4")
                                       and code not in ("hatena_http_401", "hatena_http_403", "hatena_http_429")):
            return refuse(code, _item_detail(exc))
        if code in _WAIT_RECONCILE:
            return ("blocked", code, None)
        if code == "bookmark_record_failed":
            item.update({"state": "unknown", "reason": code, "done_at": at})
            for line in logs:
                log(line)
            return ("unknown", code, None)
        if code in ("provider_rate_limited", "hatena_http_429"):
            return ("wait", code, now + datetime.timedelta(minutes=RATE_LIMITED_MINUTES))
        return ("blocked" if code in _NO_HTTP_BLOCK else "blocked_http", code, _item_detail(exc))
    for line in logs:
        log(line)
    if done.get("status") == "unknown":
        item.update({"state": "unknown", "reason": done.get("error") or "unknown", "done_at": at,
                     "post_id": done.get("post_id"), "wait": None})
        log(f"ブックマークのまとめ: 直しの結果が分かりません（{done.get('error')}）: {item.get('url')}。"
            f"打ち直しません。thth bookmark reconcile {done.get('holder') or account_name} で確かめてください")
        return ("unknown", done.get("error"), None)
    item.update({"state": "placed", "reason": None, "detail": None, "done_at": at, "wait": None,
                 "post_id": done.get("post_id"), "permalink": done.get("permalink")})
    log(f"ブックマークのまとめ: 直しました: {item.get('url')}")
    return ("placed", None, None)
