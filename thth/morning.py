"""`thth morning`——毎朝の一枚（設計 3.1.0）。**束ねるだけ。**

芯（設計 3.1.0 §0）: **「昨日から何があって、今日なにをすればよいか」を、道具が
事実だけで 1 枚にする。** 空欄を推測で埋めない。本文は作らない——作るのは人と
LLM の会話の側で、ここは材料を並べて終わる。

規律（同 §2・§7）:

  (a) **段ごとに `try` で囲む。** 1 段が落ちても他の 5 段は出る。落ちた段は
      `{"value": null, "cannot_say": "<静的な符丁>"}`（`REASONS` の 6 語だけ）。
      provider の文面・traceback は**この口から外へ出さない**。
  (b) **読むだけ。** SNS 台帳に 1 バイトも書かない（2.10 §1.4）。進むのは栞
      （`handoff_cursor`）だけで、それも `mark=False` なら進めない。
  (c) **数には分母。** 取れないものは `null`——`0` と混ぜない。
  (d) **`--json` に絶対パス・本文全文・新しい個人情報を入れない**（leak probe の
      対象）。本文は `where` と同じ先頭 60 字の表示だけ。
  (e) **「次の一手」は候補の列挙だけ**（masaru 裁定 3.1.0 §7-1）。本文を作らない
      ——`body` という鍵はこの口のどこにも出てこない。
  (f) **監視語は管理者が入れる**（同 §7-2）。語が無い account の第 3 段は
      `no_watch_words` で飛ばす——**推測で語を選ばない。**
"""
from __future__ import annotations

import datetime
import json
import sys

from . import accounts as accounts_mod
from . import adapters as adapters_mod
from . import jst
from . import threads_read_cli as threads_read_cli_mod

# 段が言える「取れなかった」理由（**この 6 語だけ**・設計 3.1.0 §2）。
REASONS = ("provider_timeout", "budget_exhausted", "scope_missing",
           "no_watch_words", "not_supported", "unavailable")

# 本文の見せ方は `where` と同じ（先頭 60 字・保存しない）。
PREVIEW_CHARS = threads_read_cli_mod.TEXT_PREVIEW_CHARS

# 第 1 段で数える未回答の窓（`thth unanswered` の既定と同じ）。
UNANSWERED_SINCE = "7d"
# 第 3 段が 1 語あたりに要求する件数と、絡みに行く先として並べる上限。
WORLD_LIMIT = 25
ENGAGE_TARGETS = 3
# 栞を進めるときの名乗り（`handoff-report --mark-read --by` に相当）。
MARK_BY = "morning"

SECTION_TITLES = (("tool", "道具"), ("unanswered", "返していないもの"),
                  ("yesterday", "昨日の自分"), ("world", "世間"),
                  ("today", "予定"), ("next_steps", "次の一手"))


class MorningError(ValueError):
    """問いが受け取れない（target がどの account／project にも当たらない 等）。"""


# ------------------------------------------------------------------ 小道具

def cell(value=None, cannot_say=None) -> dict:
    """1 つの升目。**値か理由のどちらか一方**（理由は `REASONS` の静的な符丁）。"""
    if cannot_say is not None and cannot_say not in REASONS:
        cannot_say = "unavailable"
    return {"value": None if cannot_say is not None else value,
            "cannot_say": cannot_say}


def _preview(text) -> str:
    """本文を 1 行・先頭 60 字に（`where` と同じ・**保存しない・全文を出さない**）。"""
    return threads_read_cli_mod._one_line(text, PREVIEW_CHARS)


def classify(error) -> str:
    """例外・断りの文面を**静的な符丁 1 語**に写す（文面そのものは外へ出さない）。"""
    if isinstance(error, TimeoutError):
        return "provider_timeout"
    text = str(error)
    lowered = text.lower()
    if "timeout" in lowered or "timed out" in lowered or "タイムアウト" in text:
        return "provider_timeout"
    if ("permission" in lowered or "権限" in text or "乗っていません" in text
            or "標準アクセス" in text or "scope" in lowered):
        return "scope_missing"
    if "未対応" in text or "not supported" in lowered:
        return "not_supported"
    if "budget_exhausted" in lowered:
        return "budget_exhausted"
    return "unavailable"


def _guard(call):
    """1 段（または 1 升目）を呼ぶ。**例外は外に出さない**——理由 1 語に畳む。"""
    try:
        return cell(call())
    except MorningError:
        raise
    except BaseException as error:  # noqa: BLE001 — 段は落ちても morning は続く
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        return cell(cannot_say=classify(error))


def _section(name, title, entries) -> dict:
    """account ごとの升目を 1 段に束ねる。

    **全部が同じ理由で欠けたら、段そのものがその理由**（X の読取予算 0 で
    1〜3 段が丸ごと `budget_exhausted` になる形・設計 3.1.0 §4）。理由が
    混ざっているときは升目のまま並べる——1 本の account の欠測で他を隠さない。
    """
    reasons = {entry["cannot_say"] for entry in entries.values()}
    if entries and len(reasons) == 1 and None not in reasons:
        node = cell(cannot_say=reasons.pop())
    else:
        node = cell({"by_account": entries})
    return {"section": name, "title": title, **node}


# ------------------------------------------------------------- 対象の解決

def targets(target) -> list:
    """target（account 名か project 名）に当たる account 名の一覧。

    **account 名が先**（同じ綴りの project があっても、名指しは名指し）。
    読めない台帳は飛ばす——1 本で全部を止めない。
    """
    if not accounts_mod.name_is_safe(target):
        return []
    names = accounts_mod.list_account_names()
    if target in names:
        return [target]
    found = []
    for name in names:
        try:
            cfg = accounts_mod.load_account(name)
        except (accounts_mod.AccountError, ValueError, TypeError):
            continue
        if cfg.get("project") == target:
            found.append(name)
    return found


# --------------------------------------------------------------- 第 0 段

def _tool_section(handoff):
    """版・前回から変わったか・リリースノート・出せるもの表（設計 §2 の 0 段）。

    **前回との比較は account ごとの栞から来る**（`handoff-report` の
    `by_account[].tool`）。project をまとめるときは、1 本でも「変わった」と
    言う栞があれば変わった——**変化を平均で薄めない。**
    """
    tool = dict(handoff["tool"])
    nodes = [node.get("tool") or {} for node in (handoff.get("by_account") or {}).values()]
    flags = [node.get("changed_since_last_read") for node in nodes]
    changed = (True if any(flag is True for flag in flags)
               else False if any(flag is False for flag in flags) else None)
    previous = {node.get("previous_version") for node in nodes} - {None}
    notes = []
    for node in nodes:
        for name in node.get("release_notes") or []:
            if name not in notes:
                notes.append(name)
    tool["previous_version"] = previous.pop() if len(previous) == 1 else None
    if notes:
        tool["release_notes"] = sorted(notes)
    return {
        "version": tool.get("version"),
        "previous_version": tool.get("previous_version"),
        "changed_since_last_read": changed,
        "release_notes": list(tool.get("release_notes") or []),
        "notes_reason": tool.get("notes_reason"),
        "notes_root_local_hint": tool.get("notes_root_local_hint"),
        # **表そのものは変わったときだけ出す**（設計 §2 の 0 段「出せるもの表に
        # 変化があれば」）。静的な表なので、版が変わらなければ変わらない。
        "capabilities": tool.get("capabilities") if changed else None,
        "capabilities_basis": "static_table_not_a_provider_probe",
    }


# --------------------------------------------------------------- 第 1 段

def _unanswered_rows(name, now, allowed_names=None):
    from . import unanswered as unanswered_mod
    result = unanswered_mod.answer(name, since=UNANSWERED_SINCE, now=now,
                                   **({} if allowed_names is None
                                      else {"allowed_names": tuple(allowed_names)}))
    rows = [{"post_id": row["post_id"], "reply_id": row["reply_id"],
             "author_key": row.get("author_key"),
             "age_hours": row.get("age_hours"),
             "permalink": row.get("permalink"),
             "preview": _preview(row.get("preview"))}
            for row in result["replies"]]
    return {"n": result["n_total"], "denominator": result["n_total"],
            "window": result["window"], "items": rows,
            "collection_stale_hours": result.get("collection_stale_hours"),
            "cannot_say": list(result.get("cannot_say") or [])}


def _mentions_rows(name, cfg, now, counter):
    """自分への言及のうち**まだ返していないもの**（1 account 1 回）。"""
    media = cfg.get("media")
    if "mentions" not in adapters_mod.capabilities_for(media):
        return cell(cannot_say="not_supported")

    def call():
        from . import thread_read as thread_read_mod
        adapter, why = threads_read_cli_mod._adapter_for(name, capability="mentions")
        if adapter is None:
            raise RuntimeError(why or "unavailable")
        counter(media, "mentions")
        rows = adapter.mentions(since=None)
        ledger, queue, unreadable = thread_read_mod._already_replied_index(cfg, name)
        items = []
        for row in rows:
            pid = row.get("message_id")
            replied = thread_read_mod._already_replied_for(
                pid, ledger_by_reply_to=ledger, queue_index=queue,
                self_reply_by_parent={}, ledgers_unreadable=bool(unreadable))
            if replied:
                continue
            at = jst.parse(row.get("timestamp"))
            items.append({"post_id": pid, "author_key": row.get("author_key"),
                          "permalink": row.get("permalink"),
                          "timestamp": row.get("timestamp"),
                          "age_hours": (round((now - at).total_seconds() / 3600, 3)
                                        if at is not None else None),
                          "replied": replied,
                          "preview": _preview(row.get("text"))})
        return {"n": len(items), "denominator": len(rows), "items": items}

    return _guard(call)


# --------------------------------------------------------------- 第 2 段

def yesterday_window(now):
    """昨日（JST）の 00:00 以上・今日の 00:00 未満。"""
    today = jst.to_jst(now).replace(hour=0, minute=0, second=0, microsecond=0)
    return today - datetime.timedelta(days=1), today


def _yesterday_posts(name, now):
    """昨日出した投稿ごとの実測（分母つき・24h 前と比べられれば差分）。"""
    from . import measured as measured_mod
    start, end = yesterday_window(now)
    data = measured_mod.load(name)
    posts = []
    for post in data["posts"]:
        at = jst.parse(post.get("posted_at"))
        if at is None or not (start <= at < end):
            continue
        rows = [row for row in post["rows"] if jst.parse(row.get("collected_at"))]
        rows.sort(key=lambda row: jst.parse(row["collected_at"]))
        latest = rows[-1] if rows else None
        metrics = dict(latest.get("metrics") or {}) if latest else None
        entry = {"post_id": post["post_id"], "topic": post.get("topic"),
                 "posted_at": post.get("posted_at"),
                 "observations": len(rows),
                 "rows_unattributed": post.get("rows_unattributed"),
                 "observed_at": latest.get("collected_at") if latest else None,
                 "age_hours": latest.get("age_hours") if latest else None,
                 "metrics": metrics,
                 "missing": list(latest.get("missing") or []) if latest else None,
                 "delta_24h": None, "delta_basis": None, "cannot_say": []}
        if latest is None:
            entry["cannot_say"].append("no_observation_recorded")
        else:
            previous = _row_24h_before(rows, latest)
            if previous is None:
                entry["cannot_say"].append("no_24h_earlier_observation")
            else:
                before = previous.get("metrics") or {}
                entry["delta_24h"] = {
                    key: (metrics[key] - before[key]
                          if isinstance(metrics.get(key), int) and isinstance(before.get(key), int)
                          else None)
                    for key in sorted(set(metrics) | set(before))}
                entry["delta_basis"] = {"from": previous.get("collected_at"),
                                        "to": latest.get("collected_at")}
        posts.append(entry)
    posts.sort(key=lambda entry: entry["posted_at"] or "")
    return {"n": len(posts), "denominator": len(data["posts"]),
            "window": {"since": jst.iso(start), "until": jst.iso(end), "basis": "posted_at_jst"},
            "posts": posts, "broken": len(data.get("broken") or []),
            "unknown_ownership": len(data.get("posts_unknown_ownership") or [])}


def _row_24h_before(rows, latest):
    """`latest` のおよそ 24 時間前（±2 時間）に採れた行。無ければ None。"""
    target = jst.parse(latest["collected_at"]) - datetime.timedelta(hours=24)
    window = datetime.timedelta(hours=2)
    found = None
    for row in rows:
        at = jst.parse(row["collected_at"])
        if abs(at - target) <= window and (found is None or at > jst.parse(found["collected_at"])):
            found = row
    return found


# --------------------------------------------------------------- 第 3 段

def _world(name, cfg, now, counter):
    """監視語ごとの世間（件数・異なり・上位 3 の占有率・直近・絡みに行く先 3 件）。"""
    from . import where_cli as where_cli_mod
    media = cfg.get("media")
    words = accounts_mod.watch_words(cfg)
    if not words:
        return cell(cannot_say="no_watch_words")
    if "keyword_search" not in adapters_mod.capabilities_for(media):
        return cell(cannot_say="not_supported")
    words = words[:where_cli_mod.MAX_WORDS]

    def call():
        for _word in words:
            counter(media, "keyword_search")
            if media in ("bluesky", "mastodon"):
                # `where` はこの 2 媒体でタグの観測も 1 語 1 回叩く。
                counter(media, "tag_search")
        result = where_cli_mod.answer(account_name=name, words=words, now=now,
                                      limit=WORLD_LIMIT)
        node = (result.get("by_account") or {}).get(name)
        if node is None:
            reasons = [line for line in result.get("cannot_say") or []]
            raise RuntimeError(reasons[0] if reasons else "unavailable")
        by_word = {}
        for word in words:
            entry = node["by_word"].get(word)
            if entry is None:
                why = next((line for line in node["cannot_say"]
                            if line.startswith(word + ":")), "unavailable")
                by_word[word] = cell(cannot_say=classify(why))
                continue
            material = entry["material"]
            history = entry.get("my_history") or {}
            by_word[word] = cell({
                "n": material["n"], "denominator": material["requested_limit"],
                "distinct_authors": material["authors"]["distinct"],
                "authors_denominator": material["authors"]["with_username"],
                "top_share": material["authors"]["top_share"],
                "top_k": material["authors"]["top_k"],
                "latest_timestamp": material["latest_timestamp"],
                "my_history": {"n": history.get("n"), "reacted": history.get("reacted")}
                               if history else None,
                "targets": [{"post_id": post["post_id"], "permalink": post["permalink"],
                             "timestamp": post["timestamp"],
                             "author_key": post["author_key"],
                             "replies": post["replies"], "has_replies": post["has_replies"],
                             "replied": post["replied"],
                             "my_history_present": bool(history.get("n")),
                             "preview": _preview(post.get("preview"))}
                            for post in entry["posts"][:ENGAGE_TARGETS]],
            })
        return {"words": words, "by_word": by_word}

    node = _guard(call)
    if node["cannot_say"] is None:
        # **語が全部同じ理由で欠けたら、その account の段がその理由**（段の
        # 束ね方（`_section()`）と同じ物差しを 1 段下でも使う）。
        by_word = node["value"]["by_word"]
        reasons = {entry["cannot_say"] for entry in by_word.values()}
        if by_word and len(reasons) == 1 and None not in reasons:
            return cell(cannot_say=reasons.pop())
    return node


# --------------------------------------------------------------- 第 4 段

def _today_rows(name, now):
    from . import report as report_mod
    start = jst.to_jst(now).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + datetime.timedelta(days=1)
    rows = []
    for row in report_mod.schedule(name):
        at = jst.parse(row.get("publish_at"))
        if at is None or not (start <= at < end):
            continue
        rows.append({"file": row["file"], "publish_at": row["publish_at"],
                     "status": row["status"], "topic": row.get("topic"),
                     "reply_to": row.get("reply_to"), "past": row.get("past"),
                     "head": _preview(row.get("head"))})
    return {"n": len(rows), "items": rows,
            "window": {"since": jst.iso(start), "until": jst.iso(end), "basis": "publish_at_jst"}}


def _queue_cell(node):
    """handoff の節から queue の数だけを写す（置き場のパスは写さない）。"""
    if node is None:
        return cell(cannot_say="unavailable")
    counts = (node.get("queue") or {}).get("counts")
    if counts is None:
        return cell(cannot_say="unavailable")
    keys = ("draft", "approved", "approval_needed", "approved_waiting", "overdue",
            "malformed", "unattributed_malformed")
    return cell({key: counts.get(key) for key in keys})


def _inflight(node):
    if node is None:
        return {"present": None, "since": None, "reason_code": None, "next_action_code": None}
    diagnostic = node.get("inflight")
    if not diagnostic:
        return {"present": False, "since": None, "reason_code": None, "next_action_code": None}
    return {"present": True, "since": diagnostic.get("since"),
            "reason_code": diagnostic.get("reason_code"),
            "next_action_code": diagnostic.get("next_action_code")}


def _budget(media_set):
    """X の読取（推定 USD）と投稿（本数）の残り。X が居なければこの段に出さない。"""
    if "x" not in media_set:
        return cell(cannot_say="not_supported")

    def call():
        from . import budget_x, budget_x_posts
        read = budget_x.report()
        posts = budget_x_posts.report()
        return {"x_read": {"month_utc": read["month_utc"], "cap_usd": read["cap_usd"],
                           "spent_estimate_usd": read["spent_estimate_usd"],
                           "held_usd": read["held_usd"],
                           "remaining_usd": read["remaining_usd"],
                           "read_refusal": read["read_refusal"],
                           "cannot_say": list(read["cannot_say"])},
                "x_posts": {"month_utc": posts["month_utc"], "monthly": posts["monthly"],
                            "accounts": posts["accounts"],
                            "post_refusal": posts["post_refusal"],
                            "cannot_say": list(posts["cannot_say"])}}

    return _guard(call)


def read_refusal(media):
    """その媒体の読み取りが予算で止まっているか（静的な符丁か None）。"""
    if media != "x":
        return None
    try:
        from . import budget_x
        reason = budget_x.report()["read_refusal"]
    except (OSError, ValueError, TypeError, KeyError):
        return "unavailable"
    if reason is None:
        return None
    return "budget_exhausted" if reason == "budget_exhausted" else "unavailable"


# --------------------------------------------------------------- 第 5 段

def next_steps(unanswered_entries, world_entries, today_entries) -> list:
    """**候補の列挙だけ**（masaru 裁定 3.1.0 §7-1）。本文は 1 字も作らない。

    3 種類だけ: 「返す」（1 段の各行）・「絡む」（3 段の各行）・「出す」
    （4 段で今日の予定が無い account）。どの要素にも `body` は無い。
    """
    steps = []
    for name, entry in unanswered_entries.items():
        value = entry["value"] or {}
        replies = (value.get("replies") or {}).get("value") or {}
        for row in replies.get("items") or []:
            steps.append({"kind": "reply", "account": name, "post_id": row["post_id"],
                          "reply_id": row["reply_id"], "age_hours": row.get("age_hours"),
                          "permalink": row.get("permalink")})
        mentions = (value.get("mentions") or {}).get("value") or {}
        for row in mentions.get("items") or []:
            steps.append({"kind": "reply", "account": name, "post_id": row["post_id"],
                          "reply_id": None, "age_hours": row.get("age_hours"),
                          "permalink": row.get("permalink")})
    for name, entry in world_entries.items():
        value = entry["value"] or {}
        for word, word_cell in (value.get("by_word") or {}).items():
            for row in ((word_cell.get("value") or {}).get("targets") or []):
                steps.append({"kind": "engage", "account": name, "word": word,
                              "post_id": row["post_id"], "permalink": row.get("permalink"),
                              "replied": row.get("replied"),
                              "my_history_present": row.get("my_history_present")})
    for name, entry in today_entries.items():
        today = (entry["value"] or {}).get("today") or {}
        planned = today.get("value")
        if planned is not None and planned["n"] == 0:
            steps.append({"kind": "post", "account": name})
    return steps


# ------------------------------------------------------------------ 組み立て

def build(target, *, now=None, mark=True, allowed_names=None):
    """毎朝の一枚（設計 3.1.0 §2 の 6 段）。**読むだけ・栞だけ進める。**

    `allowed_names` はサーバ型の credential が許した account（招待された側の
    MCP）。**渡されたら、その外の account は 1 本も読まない。**
    """
    from . import handoff_cursor, operations_handoff
    now = now if now is not None else jst.now_jst()
    names = targets(target)
    kind = "account" if names == [target] else "project"
    if allowed_names is not None:
        names = [name for name in names if name in allowed_names]
    if not names:
        raise MorningError("target_unknown")

    configs, top_cannot_say = {}, []
    for name in names:
        try:
            configs[name] = accounts_mod.load_account(name)
        except (accounts_mod.AccountError, ValueError, TypeError):
            top_cannot_say.append("account_unreadable")
    names = [name for name in names if name in configs]
    if not names:
        raise MorningError("target_unknown")

    calls: dict = {}

    def counter(medium, endpoint):
        calls.setdefault(medium or "unknown", {}).setdefault(endpoint, 0)
        calls[medium or "unknown"][endpoint] += 1

    scope = ({} if allowed_names is None
             else {"trusted_names": tuple(names), "allowed_names": tuple(allowed_names)})
    handoff_cell = _guard(lambda: operations_handoff.answer(
        target if kind == "account" else None,
        project=None if kind == "account" else target,
        now=now, since_last_read=True, **scope))
    handoff = handoff_cell["value"]
    nodes = (handoff or {}).get("by_account") or {}

    refusals = {name: read_refusal(cfg.get("media")) for name, cfg in configs.items()}

    # 第 1 段: 返していないもの。
    unanswered_entries = {}
    for name in names:
        cfg = configs[name]
        if refusals[name]:
            unanswered_entries[name] = cell(cannot_say=refusals[name])
            continue
        unanswered_entries[name] = _guard(lambda name=name, cfg=cfg: {
            "medium": cfg.get("media"),
            "replies": _guard(lambda: _unanswered_rows(name, now, allowed_names)),
            "mentions": _mentions_rows(name, cfg, now, counter)})

    # 第 2 段: 昨日の自分。
    yesterday_entries = {}
    for name in names:
        cfg = configs[name]
        if refusals[name]:
            yesterday_entries[name] = cell(cannot_say=refusals[name])
            continue
        yesterday_entries[name] = _guard(lambda name=name, cfg=cfg: {
            "medium": cfg.get("media"), **_yesterday_posts(name, now)})

    # 第 3 段: 世間。
    world_entries = {}
    for name in names:
        cfg = configs[name]
        if refusals[name]:
            world_entries[name] = cell(cannot_say=refusals[name])
            continue
        node = _world(name, cfg, now, counter)
        world_entries[name] = (cell({"medium": cfg.get("media"), **node["value"]})
                               if node["cannot_say"] is None else node)

    # 第 4 段: 予定（予算は account をまたがないので段の直下に置く）。
    today_entries = {}
    for name in names:
        cfg = configs[name]
        node = nodes.get(name)
        today_entries[name] = cell({
            "medium": cfg.get("media"),
            "today": _guard(lambda name=name: _today_rows(name, now)),
            "queue": _queue_cell(node),
            "inflight": _inflight(node),
            "changes_since_last_read": ((node or {}).get("changes_since") or {}).get("changes"),
        })
    budget_cell = _budget({cfg.get("media") for cfg in configs.values()})

    def _steps():
        steps = next_steps(unanswered_entries, world_entries, today_entries)
        return {"steps": steps, "n": len(steps)}

    sections = [
        {"section": "tool", "title": "道具",
         **(_guard(lambda: _tool_section(handoff)) if handoff is not None
            else cell(cannot_say=handoff_cell["cannot_say"]))},
        _section("unanswered", "返していないもの", unanswered_entries),
        _section("yesterday", "昨日の自分", yesterday_entries),
        _section("world", "世間", world_entries),
        {"section": "today", "title": "予定",
         **cell({"by_account": today_entries, "budget": budget_cell})},
        {"section": "next_steps", "title": "次の一手", **_guard(_steps)},
    ]

    marked = []
    if mark and handoff is not None:
        for name, node in nodes.items():
            try:
                handoff_cursor.write(name, node, MARK_BY, now)
                marked.append(name)
            except (accounts_mod.AccountError, OSError, ValueError, TypeError):
                top_cannot_say.append("cursor_not_advanced")

    return {"schema_version": 1, "report_type": "morning",
            "generated_at": jst.iso(now), "target": target, "target_kind": kind,
            "accounts": names, "sections": sections, "calls": calls,
            "marked": sorted(marked), "marked_by": MARK_BY if marked else None,
            "cannot_say": sorted(set(top_cannot_say)),
            "limitations": [
                "読むだけ。SNS 台帳には書かない（進むのは栞だけ）",
                "本文は先頭 60 字の表示だけ。全文も絶対パスも返さない",
                "次の一手は候補の列挙。本文は作らない",
                "監視語は管理者が入れた語だけ。道具は語を選ばない",
                "取れなかった段は null と静的な理由。0 件と混ぜない"]}


# ------------------------------------------------------------------ 人向け

def render(payload, out=print) -> None:
    """人向けの 1 枚（縦に 6 段・数には分母・取れないものは理由 1 語）。"""
    out(f"毎朝の一枚  {payload['target']}（{payload['target_kind']}）  "
        f"{payload['generated_at']}")
    out("account: " + "・".join(payload["accounts"]))
    for section in payload["sections"]:
        out("")
        if section["cannot_say"] is not None:
            out(f"[{section['title']}] 言えない: {section['cannot_say']}")
            continue
        out(f"[{section['title']}]")
        _render_section(section, out)
    if payload["calls"]:
        out("")
        out("叩いた回数: " + "・".join(
            f"{medium} {endpoint}={count}"
            for medium, endpoints in sorted(payload["calls"].items())
            for endpoint, count in sorted(endpoints.items())))
    if payload["marked"]:
        out(f"栞を進めました（by {payload['marked_by']}）: " + "・".join(payload["marked"]))
    else:
        out("栞は進めていません")
    if payload["cannot_say"]:
        out("言えない: " + "・".join(payload["cannot_say"]))


def _render_section(section, out) -> None:
    value = section["value"]
    name = section["section"]
    if name == "tool":
        out(f"  版 {value['version']}  前回から変わった: "
            f"{_yes(value['changed_since_last_read'])}"
            f"（前回 {value['previous_version'] or '—'}）")
        if value["release_notes"]:
            out("  リリースノート: " + "・".join(value["release_notes"]))
        if value["capabilities"] is not None:
            out("  出せるもの表: 版が変わったので読み直してください"
                f"（{value['capabilities_basis']}）")
        if value["notes_reason"]:
            out(f"  ノート: {value['notes_reason']}")
        return
    if name == "next_steps":
        out(f"  候補 {value['n']} 件（本文は作りません）")
        for step in value["steps"]:
            if step["kind"] == "reply":
                out(f"  返す  {step['account']}  {step['post_id']}"
                    f"  {_hours(step.get('age_hours'))}  {step.get('permalink') or '—'}")
            elif step["kind"] == "engage":
                out(f"  絡む  {step['account']}  語「{step['word']}」"
                    f"  {step.get('permalink') or step['post_id']}")
            else:
                out(f"  出す  {step['account']}（今日の予定がありません）")
        return
    for account, entry in (value.get("by_account") or {}).items():
        if entry["cannot_say"] is not None:
            out(f"  {account}: 言えない: {entry['cannot_say']}")
            continue
        node = entry["value"]
        if name == "unanswered":
            _render_unanswered(account, node, out)
        elif name == "yesterday":
            _render_yesterday(account, node, out)
        elif name == "world":
            _render_world(account, node, out)
        else:
            _render_today(account, node, out)
    if name == "today":
        budget = value.get("budget") or {}
        if budget.get("cannot_say") is not None:
            out(f"  予算: 言えない: {budget['cannot_say']}")
        else:
            read = budget["value"]["x_read"]
            posts = budget["value"]["x_posts"]
            out(f"  予算 X 読取: 残り {read['remaining_usd']} / 上限 {read['cap_usd']} USD"
                f"（{read['month_utc']}・推定・{read['read_refusal'] or '止めていません'}）")
            out(f"  予算 X 投稿: 上限 {posts['monthly']} 本/月"
                f"（{posts['month_utc']}・{posts['post_refusal'] or '止めていません'}）")


def _render_unanswered(account, node, out) -> None:
    replies, mentions = node["replies"], node["mentions"]
    if replies["cannot_say"] is not None:
        out(f"  {account}: 返信: 言えない: {replies['cannot_say']}")
    else:
        rows = replies["value"]
        out(f"  {account}（{node['medium']}）: 未回答の返信 {rows['n']} 件"
            f"（窓 {rows['window']['since'] or '—'} 以降）")
        for row in rows["items"]:
            out(f"    {_hours(row['age_hours'])}  {row['post_id']} ← {row['reply_id']}"
                f"  {row['preview']}")
    if mentions["cannot_say"] is not None:
        out(f"  {account}: 言及: 言えない: {mentions['cannot_say']}")
        return
    rows = mentions["value"]
    out(f"  {account}: まだ返していない言及 {rows['n']}/{rows['denominator']} 件")
    for row in rows["items"]:
        out(f"    {_hours(row['age_hours'])}  {row['post_id']}"
            f"  {row.get('permalink') or '—'}  {row['preview']}")


def _render_yesterday(account, node, out) -> None:
    out(f"  {account}（{node['medium']}）: 昨日の投稿 {node['n']}/{node['denominator']} 本"
        f"（{node['window']['since']}〜{node['window']['until']}）")
    for post in node["posts"]:
        metrics = post["metrics"] or {}
        numbers = "・".join(f"{key}={metrics[key]}" for key in sorted(metrics)) or "—"
        out(f"    {post['post_id']}  {numbers}（採取 {post['observations']} 回）")
        if post["delta_24h"]:
            delta = "・".join(f"{key}{_signed(v)}" for key, v in sorted(post["delta_24h"].items())
                              if v is not None)
            out(f"      24h 前との差: {delta or '—'}")
        for reason in post["cannot_say"]:
            out(f"      言えない: {reason}")


def _render_world(account, node, out) -> None:
    out(f"  {account}（{node['medium']}）: 監視語 {len(node['words'])} 語")
    for word, word_cell in node["by_word"].items():
        if word_cell["cannot_say"] is not None:
            out(f"    語「{word}」: 言えない: {word_cell['cannot_say']}")
            continue
        value = word_cell["value"]
        out(f"    語「{word}」  件数 {value['n']}/{value['denominator']}"
            f"  異なり {value['distinct_authors']}/{value['authors_denominator']}"
            f"  上位{value['top_k']}占有 {_ratio(value['top_share'])}"
            f"  直近 {value['latest_timestamp'] or '—'}")
        for row in value["targets"]:
            out(f"      {row.get('permalink') or row['post_id']}"
                f"  返信 {_count(row['replies'], row['has_replies'])}"
                f"  自分の履歴 {_yes(row['my_history_present'])}  {row['preview']}")


def _render_today(account, node, out) -> None:
    today = node["today"]
    if today["cannot_say"] is not None:
        out(f"  {account}: 今日の予定: 言えない: {today['cannot_say']}")
    else:
        out(f"  {account}（{node['medium']}）: 今日出る予定 {today['value']['n']} 本")
        for row in today["value"]["items"]:
            out(f"    {row['publish_at'][:16]}  {row['status']}  {row['file']}  {row['head']}")
    queue = node["queue"]
    if queue["cannot_say"] is not None:
        out(f"  {account}: queue: 言えない: {queue['cannot_say']}")
    else:
        counts = queue["value"]
        out(f"    承認待ちの下書き {counts['approval_needed']}"
            f"・承認済みで待ち {counts['approved_waiting']}"
            f"・時刻超過 {counts['overdue']}・型外 {counts['malformed']}")
    inflight = node["inflight"]
    if inflight["present"]:
        out(f"    inflight: {inflight['reason_code']}（{inflight['since']}）"
            f" 次 {inflight['next_action_code']}")


def _yes(value):
    return "—" if value is None else ("はい" if value else "いいえ")


def _hours(value):
    return "—" if value is None else f"{value:.1f}h前"


def _signed(value):
    return f"{value:+d}" if isinstance(value, int) else "—"


def _ratio(value):
    return "—" if value is None else f"{value * 100:.0f}%"


def _count(number, present):
    return str(number) if isinstance(number, int) else ("有" if present else "無" if present is False else "—")


# ---------------------------------------------------------------------- CLI

def register(sub) -> None:
    parser = sub.add_parser(
        "morning",
        help="毎朝の一枚——昨日から何があって、今日なにをすればよいか"
             "（設計 3.1.0・読むだけ・栞だけ進める）")
    parser.add_argument("target", help="project 名か account 名")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-mark", action="store_true", dest="no_mark",
                        help="栞（handoff cursor）を進めない")
    parser.set_defaults(func=cmd_morning)


def cmd_morning(args) -> int:
    try:
        payload = build(args.target, mark=not getattr(args, "no_mark", False))
    except MorningError as exc:
        reason = str(exc)
        print(reason, file=sys.stderr)
        if getattr(args, "json", False):
            print(json.dumps({"cannot_say": [reason]}, ensure_ascii=False))
        return 2
    except (accounts_mod.AccountError, OSError, ValueError, TypeError):
        print("morning_unavailable", file=sys.stderr)
        if getattr(args, "json", False):
            print(json.dumps({"cannot_say": ["morning_unavailable"]}, ensure_ascii=False))
        return 2
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    render(payload)
    return 0
