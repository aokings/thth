"""`thth observe`（別名 `thth morning`）——観測の 1 枚（設計 3.1.0・3.3.0 §F）。**束ねるだけ。**

3.3.0 §F: 「毎朝の一枚」を「観測」にし、セッションの始めと区切りごとに引ける
ようにした。名前は `observe`、`morning` は同じものの別名（既存の skill・セッションを
壊さない）。JSON の `report_type` はどちらで呼んでも `observe`、呼んだ名前は
`invoked_as`。第 2 段は「前回の観測から」（栞の時刻から今まで・栞が無い・7 日より
古いときは前日 JST）。

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

# 第 1 段で数える未回答の窓は台帳の `collect_days`（採集が返信を取りに行く日数・
# 既定 14）。以前は 7 日固定で、145 時間前の行があと 1 日で窓から落ちるところだった
# （3.1.1）。窓の残りがこれを切った行に `window_edge` を立てる。
UNANSWERED_DEFAULT_DAYS = 14
WINDOW_EDGE_HOURS = 24
# 第 3 段が 1 語あたりに要求する件数と、絡みに行く先として並べる上限。
WORLD_LIMIT = 25
ENGAGE_TARGETS = 3
# 第 4 段に名前を並べる時刻超過の原稿の上限（数は `n` で全部言う）。
OVERDUE_LIMIT = 10
# 栞を進めるときの名乗り（`handoff-report --mark-read --by` に相当）。
MARK_BY = "morning"
# 第 2 段の窓を「前回の観測から」にする栞の古さの上限（これより古ければ前日 JST）。
OBSERVE_MAX_DAYS = 7
# 呼び名（`invoked_as`）。どちらも同じ 1 枚（設計 3.3.0 §F）。
INVOKED_AS = ("observe", "morning")

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

def _tool_section(handoff, now=None, admin=False, configs=None, plaza=None):
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
        # 報告の口（設計 3.1.2 §3）。**管理者の 1 枚にだけ**（サーバ型の利用者の
        # credential では出さない——他 project の件数を渡さない）。
        "reports": _reports_cell(now) if admin else None,
        # 指紋の版（設計 3.3.0 A3）。前回読んだ版から変わっていれば、再承認が
        # 要る原稿の本数を account ごとに（変わっていなければ null）。
        "fingerprint_version": tool.get("fingerprint_version"),
        "reapproval_required": _reapproval_cell(handoff),
        # 知らせる先が無い scheduled の account（設計 3.3.0 A4）。止まっても誰にも
        # 届かない状態を黙らない。欠けていなければ空（有無だけ・値は出さない）。
        "notification_routes": _routes(configs or {}),
        # あなたの project の報告（設計 3.3.0 B5）。開いている件数と、この版で閉じた
        # 報告の題（置いた報告が効いたことを見せる）。対象の account と project の
        # 報告だけ（サーバ型の利用者でも自分の範囲だけ）。
        "project_reports": _project_reports(configs or {}),
        # 施策の広場（設計 3.4.0 §5）: 新着 n（自分の持ち主）・open の新着 m（参加して
        # いれば）・最近追試が付いた書き込み 1 件（§9-6）。読める範囲の書き込みだけ。
        "plaza": plaza,
        # 観測の地図（設計 3.5.0 §3）: 伸びた点・強まった線（候補の列挙だけ）。対象の
        # project の地図だけ（世間の層は project の外に出さない）。
        "map": _map_cell(configs or {}, now),
    }


def _project_reports(configs):
    from . import report_inbox
    projects = {cfg.get("project") for cfg in configs.values() if cfg.get("project")}
    return report_inbox.project_summary(report_inbox.Scope(configs, projects))


def _routes(configs):
    from . import notification_route
    try:
        return notification_route.missing(configs)
    except Exception:  # noqa: BLE001 — 経路の判定で 0 段を落とさない
        return [{"account": name, "cannot_say": "unavailable"} for name in sorted(configs)]


def _reapproval_cell(handoff):
    from . import operations_handoff
    needed = {name: (node.get("tool") or {}).get("reapproval_required")
              for name, node in (handoff.get("by_account") or {}).items()}
    needed = {name: value for name, value in needed.items() if value is not None}
    return operations_handoff.reapproval_total(needed) if needed else None


def _reports_cell(now):
    from . import report_inbox
    return report_inbox.morning_summary(now or jst.now_jst())


def _plaza_cell(configs, since, now, exclude_account=None):
    """0 段の「広場」。対象の account（とその project）から読める書き込みだけを数える。"""
    from . import plaza
    viewer = plaza.Viewer({name: cfg.get("project") for name, cfg in configs.items()})
    try:
        summary = plaza.observe_summary(viewer, since=since, now=now,
                                        exclude_account=exclude_account)
        summary["recent_trial"] = plaza.recent_trial(viewer)
    except Exception:  # noqa: BLE001 — 広場の読みで 0 段を落とさない
        return {"project_new": None, "project_denominator": None, "open_new": None,
                "open_denominator": None, "open_reason": None, "owner_new": None,
                "owner_denominator": None, "owner_reason": None, "since": jst.iso(since),
                "recent_trial": None, "cannot_say": "plaza_store_unavailable"}
    return summary


def _map_cell(configs, now):
    """0 段の「地図」。対象の account の project ごとに 1 行（読むだけ）。"""
    from . import map_view
    projects = sorted({cfg.get("project") for cfg in configs.values()
                       if isinstance(cfg.get("project"), str) and cfg.get("project")})
    cells = {}
    for project in projects:
        try:
            cells[project] = map_view.observe_summary(project, now=now)
        except Exception:  # noqa: BLE001 — 地図の読みで 0 段を落とさない
            cells[project] = {"project": project, "n_nodes": None, "n_edges": None,
                              "grown_node": None, "strengthened_edge": None,
                              "cannot_say": "map_store_unavailable"}
    return {"by_project": cells, "cannot_say": None if projects else "no_project"}


def _plaza_steps(configs, since, now):
    from . import plaza
    viewer = plaza.Viewer({name: cfg.get("project") for name, cfg in configs.items()})
    return plaza.observe_steps(viewer, {name: cfg.get("media") for name, cfg in configs.items()},
                               since=since, now=now)


# --------------------------------------------------------------- 第 1 段

def _unanswered_days(cfg):
    """未回答の窓の日数と、その出所（`collect_days` か既定か）。"""
    raw = (cfg or {}).get("collect_days")
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return UNANSWERED_DEFAULT_DAYS, "default"
    if isinstance(raw, bool) or days < 1:
        return UNANSWERED_DEFAULT_DAYS, "default"
    return days, "collect_days"


def _unanswered_rows(name, now, allowed_names=None, cfg=None):
    from . import unanswered as unanswered_mod
    days, source = _unanswered_days(cfg)
    result = unanswered_mod.answer(name, since=f"{days}d", now=now,
                                   **({} if allowed_names is None
                                      else {"allowed_names": tuple(allowed_names)}))
    limit = days * 24

    def edge(age):
        # 窓の残りが WINDOW_EDGE_HOURS を切った行（次の朝には窓の外かもしれない）。
        return age is not None and limit - age < WINDOW_EDGE_HOURS

    rows = [{"post_id": row["post_id"], "reply_id": row["reply_id"],
             "author_key": row.get("author_key"),
             "age_hours": row.get("age_hours"),
             "window_edge": edge(row.get("age_hours")),
             "hours_to_window_edge": (round(limit - row["age_hours"], 1)
                                      if row.get("age_hours") is not None else None),
             "permalink": row.get("permalink"),
             "preview": _preview(row.get("preview"))}
            for row in result["replies"]]
    return {"n": result["n_total"], "denominator": result["n_total"],
            "window": {**result["window"], "days": days, "days_source": source},
            "items": rows,
            "collection_stale_hours": result.get("collection_stale_hours"),
            # 何の時刻から数えたか（設計 3.7.0 §B2）。`window.since` と混ぜないよう
            # `collection_stale_since` と呼ぶ。
            "collection_stale_basis": result.get("collection_stale_basis"),
            "collection_stale_since": result.get("collection_stale_since"),
            "last_fetch_at": result.get("last_fetch_at"),
            "last_fetch_hours": _ago(result.get("last_fetch_at"), now),
            "last_reply_collected_at": result.get("last_reply_collected_at"),
            "last_reply_hours": _ago(result.get("last_reply_collected_at"), now),
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


def observe_window(now, read_at=None):
    """第 2 段の窓（設計 3.3.0 §F）。`(since, until, basis)`。

    前回の栞（`handoff_cursor` の `read_at`）があり、`OBSERVE_MAX_DAYS` 日以内なら
    その時刻から今まで（`since_last_observe`）。無い・古い・未来なら前日 JST
    （`yesterday_jst`・3.1.0 と同じ窓）。
    """
    at = jst.parse(read_at) if isinstance(read_at, str) else None
    if at is not None and at <= now and now - at <= datetime.timedelta(days=OBSERVE_MAX_DAYS):
        return at, now, "since_last_observe"
    start, end = yesterday_window(now)
    return start, end, "yesterday_jst"


def _yesterday_posts(name, now, read_at=None):
    """窓の中に出した投稿ごとの実測（分母つき・24h 前と比べられれば差分）。

    窓は `observe_window()`（前回の観測から・無ければ前日 JST）。
    """
    from . import goals as goals_mod, measured as measured_mod
    start, end, basis = observe_window(now, read_at)
    data = measured_mod.load(name)
    # 投稿の目的（設計 3.6.0 §A2）。公開の時点の記録から。目的ごとの主な物差しを先頭に。
    recorded = goals_mod.recorded_goals(name)
    click_index = None
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
        goal = goals_mod.goal_for(recorded, post["post_id"])
        entry["goal"] = goal
        entry["lead_metrics"] = list(goals_mod.primary_metrics(goal, post.get("medium")))
        # follow は投稿単位の数字が一次資料に無い——日次を投稿に割らない。
        entry["goal_cannot_say"] = goals_mod.PER_POST_CANNOT_SAY.get(goal)
        if goal == "click":
            # click は一意のリンク先なら投稿から 72 時間のクリック（設計 3.7.0 §A1）。
            # 共有・リンク無し・プロフィールのリンクは言えない（割らない）。
            if click_index is None:
                click_index = _click_index(name, data, now)
            entry["click"] = _click_entry(click_index, post, at, now)
            entry["goal_cannot_say"] = entry["click"]["cannot_say"]
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
            "window": {"since": jst.iso(start), "until": jst.iso(end), "basis": basis,
                       "time_field": "posted_at_jst"},
            "posts": posts, "broken": len(data.get("broken") or []),
            "unknown_ownership": len(data.get("posts_unknown_ownership") or [])}


def _click_index(name, data, now):
    """click の材料（`click_attribution.Index` と、24h の views を選ぶための台帳）。"""
    from . import analytics_comparison as comparison, click_attribution, measured as measured_mod
    cfg = accounts_mod.load_account(name)
    posts = [(str(p["post_id"]), comparison._timestamp(p.get("posted_at"))) for p in data["posts"]]
    index = click_attribution.Index.for_account(name, cfg, posts=posts,
                                                account_daily=data.get("account_daily"), now=now)
    # 24h の views は `analytics-report` と同じ選び方（観測の時刻の検査つき）で。
    detailed = measured_mod.load(name, observation_metadata=True)
    return index, {str(p["post_id"]): p for p in detailed["posts"]}


def _click_entry(material, post, at, now):
    """observe の 1 行に添える click の升目（設計 3.7.0 §A1）。"""
    from . import analytics_comparison as comparison
    index, detailed = material
    posted = comparison._timestamp(post.get("posted_at")) or at
    observation, _rejected = comparison._observation(
        detailed.get(str(post["post_id"])), posted, now, 24)
    views = ((observation or {}).get("metrics") or {}).get("views")
    return index.attribute(post["post_id"], posted, views)


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

def _own_author_keys(name, cfg, allowed_names=None):
    """同じ project の**同じ媒体の全 account** の author_key（3.1.1）。

    絡みに行く先に自分（同じ本人の別台帳を含む）を並べないための鍵の集合。
    `where` の行の `author_key` は媒体ごとに式が違う（Mastodon はドメイン付きの
    acct・Bluesky は DID）ので、handle は **adapter 自身の `author_key()`** が
    あればそれで、無ければ境界の `author_key(medium, handle)` で作る。`user_id`
    （token・台帳）があれば境界の式でも足す。`morning <account>`（単体）でも、
    同じ project の他 account を見る。サーバ型（`allowed_names`）では許された
    account の外は読まない。

    戻りは `(keys, {"n": 見た account 数, "unreadable": 読めなかった数})`——
    読めなかった台帳があれば、除き切れていないかもしれないと分母で言う。
    """
    from .adapters import base as adapter_base
    media, project = cfg.get("media"), cfg.get("project")
    keys, seen, unreadable = set(), 0, 0
    for other_name in accounts_mod.list_account_names():
        if allowed_names is not None and other_name not in allowed_names:
            continue
        try:
            other = cfg if other_name == name else accounts_mod.load_account(other_name)
        except (accounts_mod.AccountError, ValueError, TypeError, OSError):
            unreadable += 1
            continue
        if other.get("media") != media or other.get("project") != project:
            continue
        seen += 1
        handle = (other.get("handle") or "").strip().lstrip("@")
        identities = {handle}
        try:
            token = accounts_mod.load_token(other) or {}
            identities.add(str(token.get("user_id") or other.get("user_id") or ""))
            adapter = adapters_mod.make_adapter(other, token)
            method = getattr(adapter, "author_key", None)
            if handle and callable(method):
                keys.add(method(handle))
        except Exception:
            # 鍵が 1 つ作れなくても段は止めない。境界の式の鍵は下で足す。
            unreadable += 1
        for identity in identities:
            keys.add(adapter_base.author_key(media, identity))
    keys.discard(None)
    return keys, {"n": seen, "unreadable": unreadable}


def _world(name, cfg, now, counter, allowed_names=None):
    """監視語ごとの世間（件数・異なり・上位 3 の占有率・直近・絡みに行く先 3 件）。

    絡みに行く先（`targets`）からは自分の投稿を除く（`_own_author_keys()`）。
    件数（`n`・`distinct_authors`）は世間の大きさなので変えない。除いた本数は
    `own_excluded` として升目に残す（分母の規律・3.1.1）。
    """
    from . import where_cli as where_cli_mod
    media = cfg.get("media")
    words = accounts_mod.watch_words(cfg)
    if not words:
        return cell(cannot_say="no_watch_words")
    if "keyword_search" not in adapters_mod.capabilities_for(media):
        return cell(cannot_say="not_supported")
    words = words[:where_cli_mod.MAX_WORDS]

    def call():
        own_keys, own_accounts = _own_author_keys(name, cfg, allowed_names)
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
            others = [post for post in entry["posts"]
                      if post.get("author_key") not in own_keys]
            by_word[word] = cell({
                "n": material["n"], "denominator": material["requested_limit"],
                "distinct_authors": material["authors"]["distinct"],
                "authors_denominator": material["authors"]["with_username"],
                "top_share": material["authors"]["top_share"],
                "top_k": material["authors"]["top_k"],
                "latest_timestamp": material["latest_timestamp"],
                "my_history": {"n": history.get("n"), "reacted": history.get("reacted")}
                               if history else None,
                "own_excluded": len(entry["posts"]) - len(others),
                "targets": [{"post_id": post["post_id"], "permalink": post["permalink"],
                             "timestamp": post["timestamp"],
                             "author_key": post["author_key"],
                             "replies": post["replies"], "has_replies": post["has_replies"],
                             "replied": post["replied"],
                             "my_history_present": bool(history.get("n")),
                             "preview": _preview(post.get("preview"))}
                            for post in others[:ENGAGE_TARGETS]],
            })
        return {"words": words, "by_word": by_word, "own_accounts": own_accounts}

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


def _overdue_rows(name, now):
    """時刻を過ぎたのに出ていない原稿の**名前**（3.1.1）。

    数（`queue.overdue`）だけでは、どの原稿かを知るのに `thth queue` を別に叩く
    必要があった。定義は handoff の数え方と同じ（`approved`・`post_id` 無し・
    `publish_at` から `report.OVERDUE_HOURS` を超えた）。古い順に最大
    `OVERDUE_LIMIT` 件を並べ、`n` に全体の数を出す（分母）。本文は先頭 60 字。
    """
    from . import report as report_mod
    rows = []
    for row in report_mod.schedule(name, now=now):
        at = jst.parse(row.get("publish_at"))
        if row.get("status") != "approved" or at is None:
            continue
        # reply_to_file が未解決の原稿は時刻超過に数えない（設計 3.2.0 §2）。
        # 名前は `_waiting_rows()` が「返信待ち」として出す。
        if row.get("reply_to_unresolved"):
            continue
        elapsed = (now - at).total_seconds() / 3600.0
        if elapsed <= report_mod.OVERDUE_HOURS:
            continue
        rows.append({"file": row["file"], "publish_at": row["publish_at"],
                     "status": row["status"], "elapsed_hours": round(elapsed, 1),
                     "head": _preview(row.get("head"))})
    return {"n": len(rows), "limit": OVERDUE_LIMIT, "items": rows[:OVERDUE_LIMIT],
            "basis": "approved_unposted_over_overdue_hours"}


def _waiting_rows(name, now):
    """reply_to_file の指した原稿を待っている承認済みの原稿（設計 3.2.0 §2・§4）。

    待ちは時刻超過ではない。**名前と待ち先**を出す（`overdue_items` と同じ形）。
    待ち以外の未解決（取り下げ・読めない・消えた）も同じ升目に理由付きで出す
    ——時刻超過から外した原稿を、どこにも出ない形にしない。
    """
    from . import report as report_mod
    rows = []
    for row in report_mod.schedule(name, now=now):
        if row.get("status") != "approved" or not row.get("reply_to_unresolved"):
            continue
        rows.append({"file": row["file"], "publish_at": row["publish_at"],
                     "reply_to_file": row.get("reply_to_file"),
                     "waiting_for": row.get("waiting_for"),
                     "reason": row["reply_to_unresolved"], "past": row.get("past"),
                     "head": _preview(row.get("head"))})
    return {"n": len(rows), "limit": OVERDUE_LIMIT, "items": rows[:OVERDUE_LIMIT],
            "basis": "approved_reply_to_file_unresolved"}


def _held_rows(name, cfg, now):
    """承認済みなのに出られない原稿の**名前**（設計 3.3.0 A1・A2）。

    `overdue_items` と同じ形。**時刻前の approval_stale も名前は出す**（`due`
    が False）——通知はまだしないが、朝のうちに再承認できるように。reply_to_file の
    待ちは入れない（`waiting_items` が出す）。本文は持たない（名前と理由と時刻）。
    `n_due` が `thth run` の `held` に数える本数（分母は `n`）。
    """
    from . import core
    rows = core.held_items_for_account(name, cfg, now=now)
    items = [{"file": row["file"], "reason": row["reason"], "category": row["category"],
              "publish_at": row["publish_at"], "due": row["due"],
              "elapsed_hours": row["elapsed_hours"]} for row in rows]
    return {"n": len(items), "n_due": sum(1 for row in items if row["due"]),
            "limit": OVERDUE_LIMIT, "items": items[:OVERDUE_LIMIT],
            "basis": "approved_needs_review_excluding_waiting"}


def _confirm_rows(name, cfg, now):
    """承認の確定待ち（設計 3.7.0 §B3）。not_approved を確定待ちと未依頼に分ける。

    確定待ちの行は名前・予定・digest・予定までの時間だけ（本文は持たない）。
    `n_due` は publish_at まで `approve_pending.CONFIRM_DUE_HOURS` 時間を切った本数。
    """
    from . import approve_pending
    result = approve_pending.for_account(name, cfg, now=now)
    summary = approve_pending.summary(result)
    items = [{"file": row["file"], "rel": row["rel"], "publish_at": row["publish_at"],
              "digest": row["digest"], "hours_to_publish": row["hours_to_publish"],
              "due": row["due"]} for row in result[approve_pending.AWAITING]]
    return {"n_awaiting_confirm": summary["awaiting_confirm"],
            "n_not_requested": summary["not_requested"],
            "earliest_publish_at": summary["awaiting_confirm_earliest_publish_at"],
            "n_due": summary["confirm_due"], "due_hours": approve_pending.CONFIRM_DUE_HOURS,
            "limit": OVERDUE_LIMIT, "items": items[:OVERDUE_LIMIT],
            "basis": "approve_first_stage_pending"}


def _queue_cell(node):
    """handoff の節から queue の数だけを写す（置き場のパスは写さない）。"""
    if node is None:
        return cell(cannot_say="unavailable")
    counts = (node.get("queue") or {}).get("counts")
    if counts is None:
        return cell(cannot_say="unavailable")
    keys = ("draft", "approved", "approval_needed", "approved_waiting", "overdue",
            "waiting_reply", "malformed", "unattributed_malformed")
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


def _last_notification(name, cfg):
    """最後に SMTP が受領した運用通知（`incident.summary()` と同じ値）。宛先は出さない。"""
    from . import incident
    summary = incident.summary(cfg, accounts_mod.state_dir_for(name))
    if summary.get("outbox") != "ok":
        raise ValueError("outbox_unreadable")
    return summary.get("last_sent")


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

def next_steps(unanswered_entries, world_entries, today_entries, reports=None,
               plaza=None) -> list:
    """**候補の列挙だけ**（masaru 裁定 3.1.0 §7-1）。本文は 1 字も作らない。

    3.7.0 から「確定」（`kind: "confirm_due"`・設計 §B3）: 承認の確定待ちで publish_at まで
    3 時間を切った原稿の名前と確定の命令の 1 行（`--by <名前>` は人が埋める）。
    6 種類だけ: 「返す」（1 段の各行）・「絡む」（3 段の各行）・「出す」
    （4 段で今日の予定が無い account）・「超過」（4 段の時刻超過の各行・3.1.1）・
    「出られない」（4 段の held の各行・3.3.0 A2。`candidate` は approval_stale なら
    `reapprove`（再承認）、それ以外は `inspect`）・「報告」（管理者の 1 枚だけ・
    開いている報告 1 件につき 1 行・3.1.2 §3）。held に名前がある原稿は「超過」に
    重ねない（同じ原稿に 2 つの候補を出さない）。
    3.4.0 から「広場」（`kind: "plaza"`・設計 §5）: 返信の付いた自分の施策
    （`read_replies`）・判定待ちの施策（`verdict`）・判定の付いた施策をまだ試して
    いない媒体（`try_on_medium`）。id と題の先頭 60 字だけで、本文は作らない。
    どの要素にも `body` は無い（「超過」「出られない」も file と理由と時刻だけ、
    「報告」も id と種類と題の先頭 60 字だけで、報告の本文は載せない）。
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
        held = ((entry["value"] or {}).get("held_items") or {}).get("value") or {}
        held_files = {row["file"] for row in held.get("items") or []}
        overdue = ((entry["value"] or {}).get("overdue_items") or {}).get("value") or {}
        for row in overdue.get("items") or []:
            if row["file"] in held_files:
                continue
            steps.append({"kind": "overdue", "account": name, "file": row["file"],
                          "publish_at": row["publish_at"],
                          "elapsed_hours": row["elapsed_hours"]})
        for row in held.get("items") or []:
            steps.append({"kind": "held", "account": name, "file": row["file"],
                          "reason": row["reason"], "publish_at": row["publish_at"],
                          "due": row["due"],
                          "candidate": ("reapprove" if row["category"] == "approval_stale"
                                        else "inspect")})
        # 確定待ちの原稿の publish_at まで 3 時間を切った（設計 3.7.0 §B3）。確定の命令の
        # 1 行を添える（名乗りは人が入れる・本文は作らない）。
        from . import approve_pending
        confirm = ((entry["value"] or {}).get("confirm_items") or {}).get("value") or {}
        for row in confirm.get("items") or []:
            if row["due"]:
                steps.append({"kind": "confirm_due", "account": name, "file": row["file"],
                              "publish_at": row["publish_at"],
                              "hours_to_publish": row["hours_to_publish"],
                              "command": approve_pending.confirm_command(row)})
    for row in reports or []:
        steps.append({"kind": "report", "report_id": row["report_id"],
                      "report_kind": row["kind"], "title": row["title"][:PREVIEW_CHARS]})
    steps.extend(plaza or [])
    return steps


# ------------------------------------------------------------------ 組み立て

def build(target, *, now=None, mark=True, allowed_names=None, invoked_as="observe"):
    """観測の 1 枚（設計 3.1.0 §2 の 6 段・3.3.0 §F）。**読むだけ・栞だけ進める。**

    `invoked_as` は呼んだ名前（`observe` か別名の `morning`）。中身は同じ。

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
            "replies": _guard(lambda: _unanswered_rows(name, now, allowed_names, cfg)),
            "mentions": _mentions_rows(name, cfg, now, counter)})

    # 第 2 段: 前回の観測から（栞が無い・古ければ昨日の自分）。
    if invoked_as not in INVOKED_AS:
        invoked_as = "observe"
    read_ats = {name: ((nodes.get(name) or {}).get("changes_since") or {}).get("read_at")
                for name in names}
    bases = {name: observe_window(now, read_ats[name])[2] for name in names}
    yesterday_entries = {}
    for name in names:
        cfg = configs[name]
        if refusals[name]:
            yesterday_entries[name] = cell(cannot_say=refusals[name])
            continue
        yesterday_entries[name] = _guard(lambda name=name, cfg=cfg: {
            "medium": cfg.get("media"), **_yesterday_posts(name, now, read_ats[name])})

    # 第 3 段: 世間。
    world_entries = {}
    for name in names:
        cfg = configs[name]
        if refusals[name]:
            world_entries[name] = cell(cannot_say=refusals[name])
            continue
        node = _world(name, cfg, now, counter, allowed_names)
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
            "overdue_items": _guard(lambda name=name: _overdue_rows(name, now)),
            "waiting_items": _guard(lambda name=name: _waiting_rows(name, now)),
            "held_items": _guard(lambda name=name, cfg=cfg: _held_rows(name, cfg, now)),
            # 承認の確定待ち（設計 3.7.0 §B3）。
            "confirm_items": _guard(lambda name=name, cfg=cfg: _confirm_rows(name, cfg, now)),
            "queue": _queue_cell(node),
            "inflight": _inflight(node),
            # 最後に送った運用通知（設計 3.3.1 §5）。届いたかを人が受信箱と照合する。
            "last_notification": _guard(lambda cfg=cfg, name=name: _last_notification(name, cfg)),
            "changes_since_last_read": ((node or {}).get("changes_since") or {}).get("changes"),
        })
    budget_cell = _budget({cfg.get("media") for cfg in configs.values()})
    # 広場の新着は第 2 段と同じ窓の始まり（前回の観測から・無ければ前日 JST）。account
    # ごとに窓が違えば、いちばん古い始まりから（見落とすより重ねて見せる）。
    plaza_since = min(observe_window(now, read_ats[name])[0] for name in names)
    plaza_cell = _plaza_cell(configs, plaza_since, now,
                             exclude_account=target if kind == "account" else None)

    def _steps():
        # 報告は**管理者の 1 枚だけ**（サーバ型の利用者に他 project の報告を並べない）。
        reports = None
        if allowed_names is None:
            from . import report_inbox
            reports = report_inbox.list_reports(scope=None, status="open")["reports"]
        try:
            plaza_steps = _plaza_steps(configs, plaza_since, now)
        except Exception:  # noqa: BLE001 — 広場の読みで次の一手を落とさない
            plaza_steps = []
        steps = next_steps(unanswered_entries, world_entries, today_entries, reports,
                           plaza=plaza_steps)
        return {"steps": steps, "n": len(steps)}

    sections = [
        {"section": "tool", "title": "道具",
         **(_guard(lambda: _tool_section(handoff, now, admin=allowed_names is None,
                                         configs=configs, plaza=plaza_cell)) if handoff is not None
            else cell(cannot_say=handoff_cell["cannot_say"]))},
        _section("unanswered", "返していないもの", unanswered_entries),
        # 段の id は `yesterday` のまま（JSON の契約）。題は窓で変わる。
        _section("yesterday", ("昨日の自分" if all(b == "yesterday_jst" for b in bases.values())
                               else "前回の観測から"), yesterday_entries),
        _section("world", "世間", world_entries),
        {"section": "today", "title": "予定",
         **cell({"by_account": today_entries, "budget": budget_cell})},
        {"section": "next_steps", "title": "次の一手", **_guard(_steps)},
    ]

    marked = []
    if mark and handoff is not None:
        for name, node in nodes.items():
            try:
                handoff_cursor.write(name, node, invoked_as, now)
                marked.append(name)
            except (accounts_mod.AccountError, OSError, ValueError, TypeError):
                top_cannot_say.append("cursor_not_advanced")

    return {"schema_version": 1, "report_type": "observe", "invoked_as": invoked_as,
            "generated_at": jst.iso(now), "target": target, "target_kind": kind,
            "accounts": names, "sections": sections, "calls": calls,
            "marked": sorted(marked), "marked_by": invoked_as if marked else None,
            "cannot_say": sorted(set(top_cannot_say)),
            "limitations": [
                "読むだけ。SNS 台帳には書かない（進むのは栞だけ）",
                "本文は先頭 60 字の表示だけ。全文も絶対パスも返さない",
                "次の一手は候補の列挙。本文は作らない",
                "監視語は管理者が入れた語だけ。道具は語を選ばない",
                "取れなかった段は null と静的な理由。0 件と混ぜない",
                "施策を試したら広場へ（thth plaza post・thth_plaza_post）。次を決める前に他の媒体の施策を読む",
                "地図は人が足した点だけ（thth map show・thth_map_show）。伸びた点・強まった線は候補の列挙",
                # 報告の口の 1 行は末尾に置く（設計 3.1.2 §3.5・試験が末尾を見る）。
                "不具合・要望・つまずきは report の口へ（thth_report_file）"]}


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
        mine = value.get("project_reports")
        if mine is not None:
            if mine["cannot_say"] is not None:
                out(f"  あなたの project の報告: 言えない: {mine['cannot_say']}")
            else:
                titles = "・".join(row["title"] for row in mine["closed_this_version"])
                out(f"  あなたの project の報告: 開いている {mine['open']}"
                    f"・この版（{mine['version']}）で閉じた {mine['m']}"
                    + (f"（{titles}）" if titles else ""))
        _render_plaza(value.get("plaza"), out)
        _render_map(value.get("map"), out)
        from . import notification_route
        for row in value.get("notification_routes") or []:
            text = (notification_route.line(row["cannot_say"])
                    if row["cannot_say"] != "unavailable" else "unavailable")
            out(f"  知らせる先: {row['account']}: {text}")
        needed = value.get("reapproval_required")
        if needed is not None:
            per = "・".join(f"{name} {n if n is not None else '言えない'}"
                           for name, n in needed["by_account"].items())
            out(f"  この版で再承認が要る原稿: "
                f"{needed['n'] if needed['n'] is not None else '言えない'} 本（{per}）"
                f"——承認の指紋の計算が変わりました（指紋の版 {needed['fingerprint_version']}）")
        reports = value.get("reports")
        if reports is not None:
            if reports["cannot_say"] is not None:
                out(f"  報告: 言えない: {reports['cannot_say']}")
            else:
                out(f"  報告: 開いている {reports['open']} 件（新規 {reports['new']} 件・"
                    f"直近 {reports['new_window_hours']} 時間）"
                    + (f"  → {reports['next']}" if reports["next"] else ""))
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
            elif step["kind"] == "overdue":
                out(f"  超過  {step['account']}  {step['file']}  {step['publish_at']}"
                    f"（{step['elapsed_hours']}h）")
            elif step["kind"] == "held":
                verb = "再承認" if step["candidate"] == "reapprove" else "確かめる"
                out(f"  {verb}  {step['account']}  {step['file']}  {step['reason']}"
                    f"（予定 {step['publish_at'] or '—'}）")
            elif step["kind"] == "confirm_due":
                out(f"  確定  {step['account']}  {step['file']}（予定 {step['publish_at']}・"
                    f"あと {step['hours_to_publish']}h・確定するまで出ません）  {step['command']}")
            elif step["kind"] == "report":
                out(f"  報告  {step['report_id']}  {step['report_kind']}  {step['title']}")
            elif step["kind"] == "plaza":
                verb = {"read_replies": f"広場の返信を読む（新しい返信 {step.get('n_new_replies')} 件）",
                        "verdict": "広場の施策を判定する",
                        "try_on_medium": f"広場の施策を {step.get('medium') or '—'} で試す"
                                         f"（まだ試していない媒体・判定 {step.get('verdict')}）"}
                out(f"  {verb[step['candidate']]}  {step['account']}  {step['plaza_id']}"
                    f"  {step['title']}")
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


def _render_plaza(node, out) -> None:
    """0 段の広場の 1〜2 行（数には分母・不参加は理由）。"""
    if node is None:
        return
    if node.get("cannot_say") is not None:
        out(f"  広場: 言えない: {node['cannot_say']}")
        return
    opened = (f"open の新着 {node['open_new']}（open {node['open_denominator']} 件のうち）"
              if node["open_new"] is not None else "open: 参加していません")
    owner = (f"・同じ持ち主の組の新着 {node['owner_new']}（組 {node['owner_denominator']} 件のうち）"
             if node.get("owner_new") is not None else "")
    out(f"  広場: 新着 {node['project_new']}（project {node['project_denominator']} 件のうち）"
        f"{owner}・{opened}（{node['since']} から）")
    trial = node.get("recent_trial")
    if trial:
        counts = trial["trials"]
        out(f"  広場の最近の追試: {trial['plaza_id']}  {trial['title']}"
            f"（再現した {counts['reproduced']}・再現しなかった {counts['not_reproduced']}・"
            f"試していない {counts['not_tried']}／{counts['denominator']} 件）")


def _render_map(node, out) -> None:
    """0 段の地図の 1 行（伸びた点・強まった線・候補の列挙だけ）。"""
    if not node or not node.get("by_project"):
        return
    from . import map_view
    for cell in node["by_project"].values():
        out("  " + map_view.observe_text(cell))


def _ago(stamp, now):
    at = jst.parse(stamp) if isinstance(stamp, str) else None
    if at is None or now is None:
        return None
    return round((now - at).total_seconds() / 3600, 1)


def fetch_line(rows) -> str | None:
    """返信の取得の 1 行（設計 3.7.0 §B2）。数え始めの時刻を言う（無ければ None）。

    `collection_stale_hours` は根投稿ごとの最後の取得の成功（0 件の成功を含む）の
    うち最も古いものから。返信が 1 件以上取れた時刻は別に言う。失敗した試行は台帳に
    残らないので「試した」時刻は言えない。
    """
    if rows.get("collection_stale_since") is None:
        return None
    stale = rows.get("collection_stale_hours")
    newest, reply = rows.get("last_fetch_hours"), rows.get("last_reply_hours")
    return (f"返信の取得: 最後に取得できた（0 件を含む）のは、いちばん古い根投稿で "
            f"{_hours(stale)}（{rows['collection_stale_since']}）"
            + (f"・いちばん新しい取得は {newest}h 前" if newest is not None else "")
            + (f"・返信が 1 件以上取れたのは {reply}h 前" if reply is not None
               else "・返信が取れた記録はありません")
            + "（失敗した試行は台帳に残らないので数えていません）")


def _render_unanswered(account, node, out) -> None:
    replies, mentions = node["replies"], node["mentions"]
    if replies["cannot_say"] is not None:
        out(f"  {account}: 返信: 言えない: {replies['cannot_say']}")
    else:
        rows = replies["value"]
        out(f"  {account}（{node['medium']}）: 未回答の返信 {rows['n']} 件"
            f"（窓 {rows['window'].get('days') or '—'} 日・{rows['window']['since'] or '—'} 以降）")
        for row in rows["items"]:
            warn = (f"  ⚠ 窓まで {row['hours_to_window_edge']}h"
                    if row.get("window_edge") else "")
            out(f"    {_hours(row['age_hours'])}  {row['post_id']} ← {row['reply_id']}"
                f"{warn}  {row['preview']}")
        line = fetch_line(rows)
        if line:
            out(f"    {line}")
    if mentions["cannot_say"] is not None:
        out(f"  {account}: 言及: 言えない: {mentions['cannot_say']}")
        return
    rows = mentions["value"]
    out(f"  {account}: まだ返していない言及 {rows['n']}/{rows['denominator']} 件")
    for row in rows["items"]:
        out(f"    {_hours(row['age_hours'])}  {row['post_id']}"
            f"  {row.get('permalink') or '—'}  {row['preview']}")


def _render_yesterday(account, node, out) -> None:
    label = ("前回の観測からの投稿" if node["window"].get("basis") == "since_last_observe"
             else "昨日の投稿")
    out(f"  {account}（{node['medium']}）: {label} {node['n']}/{node['denominator']} 本"
        f"（{node['window']['since']}〜{node['window']['until']}）")
    for post in node["posts"]:
        metrics = post["metrics"] or {}
        # 目的の主な物差しを先頭に（設計 3.6.0 §A2）。目的が無ければ従前の並び。
        lead = [key for key in post.get("lead_metrics") or [] if key in metrics]
        keys = lead + [key for key in sorted(metrics) if key not in lead]
        numbers = "・".join(f"{key}={metrics[key]}" for key in keys) or "—"
        goal = post.get("goal")
        # 目的なし（none）と記録なし（unrecorded・3.6.0 より前）は印を付けない（JSON には出る）。
        tag = f"[{goal}] " if goal and goal not in ("none", "unrecorded") else ""
        out(f"    {post['post_id']}  {tag}{numbers}（採取 {post['observations']} 回）")
        click = post.get("click")
        if click is not None and click.get("basis"):
            if click["clicks_72h"] is None:
                out(f"      click: 一意のリンク先（{click['basis']}）・72h のクリックはまだ言えません"
                    f"（{click['clicks_missing']}・{click['window']}＝投稿日を含む 3 暦日）")
            else:
                rate = (f"クリック率 {click['click_rate']}（{click['rate_basis']}＝"
                        f"{click['clicks_72h']}/{click['views_24h']}）" if click["click_rate"] is not None
                        else f"クリック率は言えません（{click['rate_missing']}）")
                out(f"      click: 72h のクリック {click['clicks_72h']}（{click['basis']}・"
                    f"{click['window']}＝投稿日を含む 3 暦日）・{rate}")
        elif click is not None:
            out(f"      言えない: {click['cannot_say']}（共有・リンク無し・プロフィールのリンクは"
                "投稿単位のクリックを出しません）")
        elif post.get("goal_cannot_say"):
            out(f"      言えない: {post['goal_cannot_say']}（日次の数を投稿に割りません）")
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
            f"  直近 {value['latest_timestamp'] or '—'}"
            f"  自分を除外 {value['own_excluded']}")
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
            f"・時刻超過 {counts['overdue']}・返信待ち {counts.get('waiting_reply')}"
            f"・型外 {counts['malformed']}")
    overdue = node.get("overdue_items") or {}
    if overdue.get("cannot_say") is not None:
        out(f"    時刻超過の名前: 言えない: {overdue['cannot_say']}")
    elif overdue.get("value"):
        value = overdue["value"]
        for row in value["items"]:
            out(f"    時刻超過: {row['file']} {row['publish_at']}（{row['elapsed_hours']}h）")
        if value["n"] > len(value["items"]):
            out(f"    時刻超過: ほか {value['n'] - len(value['items'])} 本（全 {value['n']} 本）")
    waiting = node.get("waiting_items") or {}
    if waiting.get("cannot_say") is not None:
        out(f"    返信待ちの名前: 言えない: {waiting['cannot_say']}")
    elif waiting.get("value"):
        value = waiting["value"]
        for row in value["items"]:
            if row["waiting_for"]:
                out(f"    返信待ち: {row['file']} → {row['waiting_for']} が出たら返信します"
                    f"（予定 {row['publish_at']}）")
            else:
                out(f"    返信先を解決できません: {row['file']}（{row['reason']}）")
        if value["n"] > len(value["items"]):
            out(f"    返信待ち: ほか {value['n'] - len(value['items'])} 本（全 {value['n']} 本）")
    held = node.get("held_items") or {}
    if held.get("cannot_say") is not None:
        out(f"    出られない原稿の名前: 言えない: {held['cannot_say']}")
    elif held.get("value"):
        value = held["value"]
        for row in value["items"]:
            when = (f"{row['elapsed_hours']}h 超過" if row["elapsed_hours"] is not None
                    else "時刻前" if not row["due"] else "時刻不明")
            out(f"    出られない: {row['file']}（{row['reason']}・予定 {row['publish_at'] or '—'}"
                f"・{when}）")
        if value["n"] > len(value["items"]):
            out(f"    出られない: ほか {value['n'] - len(value['items'])} 本（全 {value['n']} 本）")
    confirm = node.get("confirm_items") or {}
    if confirm.get("cannot_say") is not None:
        out(f"    確定待ち: 言えない: {confirm['cannot_say']}")
    elif confirm.get("value") and confirm["value"]["n_awaiting_confirm"]:
        value = confirm["value"]
        out(f"    確定待ち {value['n_awaiting_confirm']} 本（最短の publish_at "
            f"{value['earliest_publish_at'] or '—'}・1 段目がまだの下書き {value['n_not_requested']} 本）"
            "——確定するまで出ません")
        for row in value["items"]:
            mark = "  ⚠ 3 時間を切りました" if row["due"] else ""
            out(f"    確定待ち: {row['file']}（予定 {row['publish_at'] or '—'}）{mark}")
    inflight = node["inflight"]
    if inflight["present"]:
        out(f"    inflight: {inflight['reason_code']}（{inflight['since']}）"
            f" 次 {inflight['next_action_code']}")
    sent = node.get("last_notification")
    if sent is not None:
        from . import incident
        if sent.get("cannot_say") is not None:
            out(f"    最後に送った運用通知: 言えない: {sent['cannot_say']}")
        else:
            out("    最後に送った運用通知: " + incident.last_sent_line(sent.get("value")))


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
    for name, text in (
            ("observe", "観測——前回の観測から何があって、いまなにをすればよいか"
                        "（セッションの始めと区切りごとに・読むだけ・栞だけ進める）"),
            ("morning", "observe の別名（毎朝の一枚・同じ 1 枚を返す）")):
        parser = sub.add_parser(name, help=text)
        parser.add_argument("target", help="project 名か account 名")
        parser.add_argument("--json", action="store_true")
        parser.add_argument("--no-mark", action="store_true", dest="no_mark",
                            help="栞（handoff cursor）を進めない")
        parser.set_defaults(func=cmd_morning, invoked_as=name)


def cmd_morning(args) -> int:
    try:
        payload = build(args.target, mark=not getattr(args, "no_mark", False),
                        invoked_as=getattr(args, "invoked_as", "morning"))
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
