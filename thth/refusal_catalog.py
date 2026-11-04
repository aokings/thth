"""断りの目録（設計「監査と審査の機械化と前向きな断り」§3・段 A）。

原則（`docs/原則_前向きな断り.md`）: 断るときは **なぜ・どうすれば通るか・どうすればもっと良くなるか** の 3 つを出す。
この目録は 1 符丁 1 項目で、その 3 つを 1 か所に持つ。

項目の形（`CATALOG[符丁]`）:

- `kind`: `refusal`（通さない・出さない）か `cannot_say`（数や事実を言えない・読めない）。
- `why`: なぜ（1 文）。
- `fix`: `{"text": 通すには, "command": 打てる命令の型 or None}`。
- `better`: もっと良く（1 行）。利用者向けの断りは必須（masaru 2026-11-03 の決定）。
- `no_better`: better を書かない理由。`internal`（内部の故障系）か `terminal`（先の一手が無い）。数に上限を置き版ごとに下げる。
- `doors`: その符丁が出る口（`cli`・`mcp`・`remote`・`dashboard`・`worker`）。`worker` は thth.me の Worker
  （`callback/src`）が `{error: 符丁}` で断る口。`worker` だけの符丁は Python の口（`thth/refusal.py`）では引かない。
- `since`: 目録に載った時点の版（段 A の初期の項目は、その時点で既にあった符丁として 5.10.0）。
- `lang`: 文の言語（`ja`）。
- `retired`: 退いた符丁の印（消すときは項目を消さずにこれを立てる・互換の柵が読む）。
- `sources`: 文の出どころ・出る所（`fix_dict:<module>.<name>` はその符丁が出る説明の辞書の見方・
  `guard_message`・`dashboard`）。

部の項目には、ほかに `fix_by_view`（`{見方: 文}`・同じ符丁でも説明の辞書ごとに文が違うときの、その見方の文）
を書ける。目録の `fix.text` には `fix` の文（優先順で先に来る見方の文）が入る。

**通す直し方の文の正本は部**（段 A の 2 つ目・5.11.0 のあと）: 各モジュールの説明の辞書（`remote.NEXT`・
`hatena_entry._MESSAGES` など 15）は、目録から作る読むだけの見方（`fix_view`）になった。文を足す・直すときは
部の `fix`（と `fix_by_view`）を書き、`FIX_VIEWS` に符丁を並べる。モジュールに辞書を直に書くと柵が赤になる。
`core.guard_refusal_message` の文は `why` に、import して値を引く（`guard_why`）。ダッシュボードの
`THTH_REASON` の文は、ここに正本として置き（`DASHBOARD_REASON_JA`・目録の `why` に入る）、4 つの HTML の表は
`tools/render_refusals.py` が目録の `dashboard` の口の `why` から生成する（手で直さない・ずれたら試験が赤）。

**Worker の口**（`worker`）: `tools/render_refusals.py` が、`worker` の口を持つ符丁の 3 つ（`door_view("worker")`）を
`callback/src/refusal-catalog.js` に書き出し、Worker の `reply` が断りの答えに `why`・`fix`・`better` を足す。
ほかの口にも出る符丁は、`fix_by_door["worker"]` に Worker の口で通れる文を書く（`text`・`command` のほか、
その口でだけ違う `why`・`better` も書ける）。Worker は穴を埋めないので、Worker の口の文に型の穴は書かない。

**import の向き**: 説明の辞書を持つモジュール（`accounts`・`queuefile` など `core` が import するものを含む）が
この目録を import する。`CATALOG` は `core` を読む（`guard_why`）ので、import の時には作らず、初めて読まれた
時に作る（`__getattr__`）。`fix_view` は部だけを読む。

**穴埋めは静的な型と検査済みの値だけ**（`render`）: `<口座>`・`<主体>`・`<名前>`・`<時刻>` の型の穴に、
形を検査した値だけを入れる。自由文・本文・パスは埋めない。
"""
from __future__ import annotations

import re
from types import MappingProxyType

from . import (refusal_catalog_part1, refusal_catalog_part2, refusal_catalog_part3, refusal_catalog_part4,
               refusal_catalog_part5, refusal_catalog_part6)

# 項目を書いた部（1〜3: 設計 §1 の 8 つの口・集合・JSON の欄・説明の辞書。4〜5: 広告の口 OpsRefused と
# 認可の口 FlowError。6: Worker の口）。
PARTS = (refusal_catalog_part1, refusal_catalog_part2, refusal_catalog_part3, refusal_catalog_part4,
         refusal_catalog_part5, refusal_catalog_part6)

LANG = "ja"
INITIAL_SINCE = "5.10.0"
KINDS = ("refusal", "cannot_say")
NO_BETTER = ("internal", "terminal")
DOORS = ("cli", "mcp", "remote", "dashboard", "worker")
# Python の口で引かない口（その口だけの符丁は `thth/refusal.py` が何も足さない）。
FOREIGN_DOORS = ("worker",)

# 説明の辞書の見方（`fix_view`）。各モジュールの元の名前（`remote.NEXT` など）は、ここに並べた符丁を
# 部の文から引いた読むだけの写像になる（呼び出し側は無改修）。並びは元の辞書の鍵の順（`lint.REASONS` が
# `queuefile.REPLY_TO_FILE_MESSAGES` の順に読む）。見方の並びは、同じ符丁が複数の見方にあるときの優先順で、
# 部の `fix` には先に来る見方の文を書き、文が違う見方は部の `fix_by_view` に書く。
# `accounts._DUMMY_NEXT` の `handle`・`instance` は欄の名前（符丁の形でない）で目録に載らないので、
# accounts.py に残す。
FIX_VIEWS = {
    "remote.NEXT": (
        "not_logged_in", "invalid_key", "key_expired", "remote_config_invalid", "remote_unavailable",
        "remote_pending", "remote_unsupported", "rate_limited", "too_many_requests", "daily_limit", "retract_limit",
        "quiet_hours", "account_stopped", "settings_loosen_requires_owner", "scope_unavailable",
        "writes_not_allowed", "invalid_draft", "publication_unconfirmed", "confirm_mismatch", "inflight_changed",
        "decision_required", "media_inflight_held", "media_inflight_unsupported",
    ),
    "invites.NEXT": (
        "invite_media_unsupported", "invalid_instance", "invalid_project", "invalid_label", "invalid_expires",
        "invite_project_in_owner_group", "invite_account_exists", "invite_not_found", "invite_already_used",
        "invite_not_open", "invite_tty_required", "invite_registration_rejected", "invite_registration_unknown",
        "invite_revoke_unknown", "invite_remote_created_audit_unconfirmed", "invite_remote_created_delivery_failed",
        "admin_draft_invite_account_only", "admin_draft_credential_missing", "admin_draft_invalid_draft",
    ),
    "report_inbox.NEXT": (
        "by_required", "invalid_account", "account_unavailable", "invalid_kind", "invalid_report",
        "report_too_long", "secret_detected", "report_rate_limited", "duplicate_report", "report_not_found",
        "report_already_closed", "invalid_close_reason", "invalid_version", "invalid_status",
        "report_store_unavailable", "report_log_unavailable", "invalid_export_target", "report_closed",
        "no_last_refusal", "invalid_since",
    ),
    "map_store.NEXT": (
        "by_required", "invalid_project", "project_unknown", "invalid_node", "node_handle", "node_url",
        "node_person_like", "secret_detected", "node_exists", "node_not_found", "node_limit", "invalid_edge",
        "edge_exists", "edge_not_found", "edge_cycle", "edge_limit", "invalid_retention", "invalid_range",
        "invalid_since", "map_store_unavailable", "map_log_unavailable", "scope_unavailable",
    ),
    "plaza.NEXT": (
        "by_required", "invalid_account", "account_unavailable", "invalid_kind", "invalid_post", "post_too_long",
        "secret_detected", "plaza_rate_limited", "duplicate_post", "plaza_not_found", "plaza_store_unavailable",
        "plaza_log_unavailable", "plaza_not_joined", "invalid_reply_kind", "reason_required",
        "measure_link_required", "measure_link_invalid", "not_a_measure", "not_owner", "invalid_verdict",
        "invalid_declaration", "declaration_out_of_scope", "plaza_project_required", "invalid_visibility",
        "invalid_owner", "plaza_owner_conflict", "plaza_owner_unregistered", "plaza_owner_not_found",
        "redaction_unavailable", "invalid_until", "invalid_trial_due", "nothing_to_update", "plaza_hidden",
        "already_hidden", "not_hidden", "invalid_min_n", "invalid_goal", "invalid_from", "from_unavailable",
        "from_out_of_scope", "from_scenario_not_found", "from_is_finding", "invalid_from_doc",
        "from_doc_outside_repo", "from_doc_not_committed", "from_report_not_found", "from_report_open",
        "from_report_no_reply", "invalid_kind_detail", "scope_required", "how_required", "invalid_how",
        "observed_is_tool_only", "invalid_evidence_level", "invalid_trial_result", "open_digest_mismatch",
        "open_requires_cli", "third_party_handle",
    ),
    "hatena_entry._MESSAGES": (
        "article_required", "article_source_missing", "invalid_article_source", "title_required", "invalid_op",
        "delivery_required", "blog_id_in_manuscript", "categories_invalid", "invalid_form", "delivery_unknown",
        "image_missing", "image_too_large", "image_type_unsupported", "too_many_images", "images_too_large",
        "relative_link_unresolvable", "delivery_state_wanted", "delivery_state_inflight", "delivery_state_unknown",
        "delivery_state_published", "delivery_state_conflict", "delivery_state_no_follow", "update_no_change",
        "delivery_state_deleted", "article_not_committed",
    ),
    "bookmark_cli._MESSAGES": (
        "use_delivery", "bookmark_other_lane_article", "invalid_url", "bookmark_not_found",
        "bookmark_already_deleted", "bookmark_edit_missing_remote", "bookmark_edit_no_change",
        "bookmark_result_unknown", "approval_stale", "invalid_op", "bookmark_precheck_failed",
        "bookmark_record_failed", "hatena_auth_rejected", "hatena_rejected_item",
    ),
    "bookmark_cli._BATCH_MESSAGES": (
        "batch_empty", "batch_too_large", "batch_exists", "batch_not_found", "batch_unreadable",
        "batch_text_invalid", "batch_not_cancellable", "schedule_unavailable", "unsupported_operation",
    ),
    "announce._MESSAGES": (
        "invalid_announces", "announces_not_found", "announces_other_lane", "announces_not_allowed",
        "article_mismatch", "article_url_placeholder_twice", "article_url_without_announces",
    ),
    "queuefile.REPLY_TO_FILE_MESSAGES": (
        "reply_to_conflict", "reply_to_file_invalid", "reply_to_file_self", "reply_to_file_missing",
        "reply_to_file_account_mismatch", "reply_to_file_bundle_unsupported", "reply_to_file_unreadable",
    ),
    "inflight_cli._WRITEBACK_NEXT": (
        "text_mismatch_before_writeback", "repo_sync_failed", "writeback_push_failed", "text_mismatch_after_rebase",
        "unusable_post_id",
    ),
    "accounts._DUMMY_NEXT": (
        "redirect_uri", "blog_id", "hatena_id", "link_base",
    ),
    "admin_diagnose.ISOLATION_HINTS": (
        "root_mismatch", "private_root_required", "account_registry_mismatch",
    ),
    "dashboard_cli.ADS_NEXT": (
        "ads_stop_unconfirmed", "ads_active_outside_thth", "ads_no_end_time", "ads_account_status",
        "ads_spend_over_estimate", "ads_spend_cap_near", "ads_budget_low", "ads_spend_cap_unset",
        "ads_token_expiring", "ads_read_failed",
    ),
    "cli.VALUE_ERROR_NEXT": (
        "managed_git_store_unsafe_mode",
    ),
}

# ダッシュボードの「言えない」の理由（`skills/thth/dashboard/*.html` の `THTH_REASON` と同じ文）。
DASHBOARD_REASON_JA = {
    "referrers_unavailable": "参照元を取れなかった窓",
    "no_referrers": "参照元が無い",
    "no_threads_posts": "糸に Threads の投稿が無い",
    "no_attributable_post": "クリックを言える投稿が無い",
    "per_post_clicks_unavailable": "この媒体は投稿ごとのクリックを取れない",
    "post_id_missing": "投稿の id が無い",
    "posted_at_missing": "投稿の時刻が無い",
    "clicks_unavailable": "クリックを言えない",
    "url_shared_72h": "同じリンク先を前後 72 時間に使う投稿がある",
    "no_link": "リンクが無い",
    "profile_link": "プロフィールのリンクと同じリンク先",
    "link_unrecorded": "リンク先が記録に無い",
    "nearby_link_unrecorded": "近くにリンク先が記録に無い投稿がある",
    "window_open": "72 時間の窓がまだ閉じていない",
    "daily_missing": "日次の数が無い日がある",
    "clicks_by_url_unavailable": "リンク先ごとのクリックが読めない",
    "clicks_by_url_not_collected_yet": "リンク先ごとのクリックをまだ採っていない",
    "scenario_not_installed": "シナリオ（段 1）が未導入",
    "no_declarations": "宣言が読めない",
    "scenario_schema_unknown": "シナリオの版が違う",
    "blog_pv_not_collected": "ブログ PV は取っていない",
    "article_clicks_not_connected": "記事側の計測とつないでいない",
    "needs_scenario_show": "詳細が読めない",
    "posts_unlisted": "投稿の一覧が無い",
    "deliveries_unlisted": "配信の一覧が無い",
    "scenario_show_unavailable": "詳細が読めない",
    "accounts_not_summed": "口座をまたいで足さない（口座ごとに見る）",
    "not_measured_yet": "実測がまだ無い",
    "not_published_yet": "まだ公開していない",
    "no_deliveries": "配信がまだ無い",
    "measured_unreadable": "実測の記録が読めない",
    "reactions_unreadable": "反応の記録が読めない",
    "account_unreadable": "口座の台帳が読めない",
    "repo_unreadable": "原稿の置き場が読めない",
    "scenarios_unreadable": "キャンペーン・シリーズの宣言が読めない",
    "queue_unreadable": "原稿の置き場が読めない",
    "sent_unreadable": "送った記録が読めない",
    "deliveries_unreadable": "配信の記録が読めない",
    "replies_unreadable": "返信の記録が読めない",
    "inbox_unreadable": "通知の記録が読めない",
    "source_unreadable": "記事の正本が読めない",
    "inflight_unreadable": "公開の途中の記録が読めない",
    "budget_unreadable": "X の予算の記録が読めない",
    "hatena_account_missing": "はてなの口座がつながっていない",
    "series_account_unknown": "シリーズの口座に知らない名前がある",
    "series_account_out_of_scope": "この鍵では見えない口座がある（その分は数えない）",
    "scenario_unreadable": "キャンペーン・シリーズが読めない",
    "events_unreadable": "出来事が読めない",
    "summary_unreadable": "直近 7 日の集計が読めない",
    "summary_previous_unreadable": "前週の集計が読めない",
    "schedule_unreadable": "予定が読めない",
    "approve_pending_unreadable": "承認待ちが読めない",
    "published_unreadable": "公開の数が読めない",
    "followers_unreadable": "フォロワーの数が読めない",
    "stopped_unreadable": "止まりの印が読めない",
    "unanswered_unreadable": "未返信が読めない",
    "full_trimmed": "詳細は大きすぎて省いた",
    "target_unknown": "対象が見つからない",
    "ads_unreadable": "広告の口座を読めない",
    "webhook_unreadable": "Webhook の記録を読めない",
    "site_visits_not_imported": "サイトの訪問をまだ受け取っていない",
    "site_visits_unavailable": "その窓の訪問は取れなかった",
    "site_visits_unreadable": "訪問の控えが読めない",
    "no_tied_campaign": "このキャンペーンの原稿の印（UTM）の訪問が無い",
    "windows_differ": "サイトごとに窓が違うので足さない",
    "sessions_partly_unknown": "一部の訪問の数が無い",
    "outside_period": "訪問の窓が糸の期間に重ならない",
    "ads_account_missing": "広告の口座が無い",
    "ads_spend_not_recorded": "使った額は thth ads insights で見る",
}

# 退いた符丁（消した符丁はここに残し、`retired` の印を立てる）。段 A の時点では無い。
RETIRED: dict[str, str] = {}

_GUARD_ACCOUNT = "<口座>"
_GUARD_TAIL = f"。数は thth account set {_GUARD_ACCOUNT} <名前> <値> --by <名前> で変えられます"
_GUARD_MARK = "（安全装置）: "


class _Refused(Exception):
    """`guard_refusal_message` に渡す形だけの断り（符丁だけ・reason と next_at は無し）。"""

    reason = None
    next_at = None
    assessment = None


def _raw_entries() -> dict[str, dict]:
    """部の項目を 1 つの辞書に（同じ符丁が 2 つの部にあれば止める）。部は文だけなので import の時に読んでよい。"""
    raw: dict[str, dict] = {}
    for part in PARTS:
        for code, entry in part.ENTRIES.items():
            if code in raw:
                raise ValueError(f"duplicate_catalog_entry: {code}")
            raw[code] = entry
    return raw


_RAW = _raw_entries()


def view_text(view: str, code: str) -> str:
    """見方 `view`（`remote.NEXT` など）での符丁の文: 部の `fix_by_view` にその見方があればそれ、無ければ `fix`。"""
    entry = _RAW[code]
    text = (entry.get("fix_by_view") or {}).get(view, entry.get("fix"))
    if not isinstance(text, str) or not text:
        raise ValueError(f"fix_view_text_missing: {view} {code}")
    return text


def fix_view(view: str):
    """説明の辞書の見方（読むだけ）。`FIX_VIEWS[view]` の順に、符丁 → 部の文。

    各モジュールの元の名前（`NEXT = refusal_catalog.fix_view("remote.NEXT")`）に置く。書き足しは型の上でも
    断る（`MappingProxyType`）。文を足すときは部と `FIX_VIEWS` を書く。"""
    return MappingProxyType({code: view_text(view, code) for code in FIX_VIEWS[view]})


def views_of(code: str) -> tuple[str, ...]:
    """その符丁が出る見方（`FIX_VIEWS` の順）。"""
    return tuple(view for view, codes in FIX_VIEWS.items() if code in codes)


def fix_texts() -> dict[str, list[tuple[str, str]]]:
    """見方の文を符丁ごとに `[(見方, 文), …]`（優先順）で返す（柵と棚卸しが読む）。"""
    found: dict[str, list[tuple[str, str]]] = {}
    for view, codes in FIX_VIEWS.items():
        for code in codes:
            found.setdefault(code, []).append((view, view_text(view, code)))
    return found


# 安全装置の断りの静的な詳細（`GuardRefused.reason`）のうち、なぜの文を変えるもの（`guard_why(code, reason)`）。
# 止めた理由（`guard.STOP_REASONS`）・返信だけの日の上限・ブックマークの連投の窓。どれもコードに書いた静的な語。
GUARD_DETAIL_WORDS = ("burst", "owner", "reply_daily", "bookmark_burst")


def guard_why(code: str, reason: str | None = None, account: str = _GUARD_ACCOUNT) -> str | None:
    """`core.guard_refusal_message` の文から、なぜの部分を引く（分岐に無い符丁は None）。

    `reason` は `GUARD_DETAIL_WORDS` の語だけ渡す（それ以外は無いものとして引く）。目録の `why` は reason 無しの文。
    `account` は口座の穴の形（`SLOTS["account"]`）を通った名前だけ（通らなければ型のまま）。安全装置の 1 行と
    同じ字面にするため、穴埋めでなく安全装置の文に口座を渡して作る。"""
    from . import core
    if account != _GUARD_ACCOUNT and not (isinstance(account, str) and SLOTS["account"][1].fullmatch(account)):
        account = _GUARD_ACCOUNT
    refused = _Refused(code)
    if reason in GUARD_DETAIL_WORDS:
        refused.reason = reason
    line = core.guard_refusal_message(account, refused)
    if _GUARD_MARK not in line:
        return None
    why = line.split(_GUARD_MARK, 1)[1]
    tail = _GUARD_TAIL.replace(_GUARD_ACCOUNT, account)
    if why.endswith(tail):
        why = why[: -len(tail)]
    if why == code:  # 分岐に無い符丁は符丁そのものが返る
        return None
    return why


def _build() -> dict[str, dict]:
    catalog: dict[str, dict] = {}
    for code, entry in _RAW.items():
        sources = []
        why = entry.get("why")
        if why is None and code in DASHBOARD_REASON_JA:
            why = DASHBOARD_REASON_JA[code]
            sources.append("dashboard")
        if why is None:
            why = guard_why(code)
            if why is not None:
                sources.append("guard_message")
        # 説明の辞書の見方に出る符丁の印（文は部の `fix`・見方ごとの違いは `fix_by_view`）。
        sources.extend(f"fix_dict:{view}" for view in views_of(code))
        catalog[code] = {
            "kind": entry["kind"],
            "why": why or "",
            "fix": {"text": entry.get("fix") or "", "command": entry.get("command")},
            "better": entry.get("better") or "",
            "no_better": entry.get("no_better"),
            "doors": tuple(entry.get("doors") or ()),
            "since": entry.get("since", INITIAL_SINCE),
            "lang": LANG,
            "retired": RETIRED.get(code),
            "sources": tuple(sources),
            # 口ごとの通す道（`{口: {"text", "command"}}`・段 B の直し）と、安全装置の詳細ごとの文。
            "fix_by_door": {door: dict(value) for door, value in (entry.get("fix_by_door") or {}).items()},
            "by_detail": {word: dict(value) for word, value in (entry.get("by_detail") or {}).items()},
        }
    return catalog


def __getattr__(name: str):
    # `CATALOG` は初めて読まれた時に作る（`guard_why` が `core` を読むので、`core` が import する
    # `accounts`・`queuefile` がこの目録を import する時に作ると、半ばの `core` を読んでしまう）。
    if name == "CATALOG":
        catalog = _build()
        globals()["CATALOG"] = catalog
        return catalog
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")



def _catalog() -> dict[str, dict]:
    return globals().get("CATALOG") or __getattr__("CATALOG")


def get(code: str) -> dict | None:
    return _catalog().get(code)


def door_parts(code: str, door: str) -> dict | None:
    """その口での 3 つ（`why`・`fix`・`better`）。穴は埋めない（型のまま）。口に出ない・退いた符丁は None。

    `fix_by_door[door]` があれば、その `text`・`command`（と書いてあれば `why`・`better`）を使う。
    fix には打てる命令を（）で添える（`refusal.parts` と同じ形）。"""
    entry = _catalog().get(code)
    if entry is None or entry.get("retired") or door not in entry["doors"]:
        return None
    by = entry["fix_by_door"].get(door) or {}
    text = by.get("text", entry["fix"]["text"]) or ""
    command = by["command"] if "command" in by else entry["fix"]["command"]
    if command and command not in text:
        text = f"{text}（{command}）"
    return {"why": by.get("why", entry["why"]) or "", "fix": text,
            "better": by.get("better", entry["better"]) or None}


def door_view(door: str) -> dict[str, dict]:
    """その口に出る符丁の 3 つを、符丁の順で（Worker の書き出しが読む）。"""
    return {code: door_parts(code, door) for code in sorted(_catalog()) if door_parts(code, door) is not None}


def dashboard_view() -> dict[str, str]:
    """ダッシュボードの `THTH_REASON`（`dashboard` の口の符丁 → 目録の `why`）。

    並びは `DASHBOARD_REASON_JA` の順（今の HTML の並び）、そこに無い符丁は後ろに符丁の順で。"""
    catalog = _catalog()
    codes = [code for code, entry in catalog.items() if "dashboard" in entry["doors"] and not entry.get("retired")]
    order = {code: index for index, code in enumerate(DASHBOARD_REASON_JA)}
    codes.sort(key=lambda code: (order.get(code, len(order)), code))
    return {code: catalog[code]["why"] for code in codes}


# ---- 穴埋め（静的な型に検査済みの値だけ） ----

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_TIME_FORM = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:[+-]\d{2}:\d{2}|Z)\Z")


class _Moment:
    """時刻の穴の検査（打てる命令に入る値）: `T` 区切りで日時とタイムゾーンがそろい、実在する時刻だけ。

    日付だけ・タイムゾーン無し・存在しない日付（13 月 45 日・99 時）・空白区切りは通さない。"""

    @staticmethod
    def fullmatch(value):
        if not isinstance(value, str) or not _TIME_FORM.fullmatch(value):
            return None
        import datetime
        try:
            parsed = datetime.datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else None


_TIME = _Moment()
# 穴の名前 → 型の字面と値の検査。
SLOTS = {
    "account": ("<口座>", _NAME),
    "principal": ("<主体>", _NAME),
    "by": ("<名前>", _NAME),
    "at": ("<時刻>", _TIME),
}


def fill_slots(text, **values):
    """型の穴（`SLOTS`）に、形の検査を通った値だけを入れる。通らない値・知らない穴は型のまま。"""
    if not text:
        return text
    for key, value in values.items():
        slot = SLOTS.get(key)
        if slot is None or not isinstance(value, str) or not slot[1].fullmatch(value):
            continue
        text = text.replace(slot[0], value)
    return text


def render(code: str, **values) -> dict:
    """符丁の 3 つを、検査を通った値だけ穴に入れて返す。通らない値・知らない穴は入れない（型のまま）。

    返す形: `{"code", "why", "fix", "command", "better"}`。符丁が目録に無ければ KeyError。
    """
    entry = _catalog()[code]

    def fill(text):
        return fill_slots(text, **values)

    return {"code": code, "why": fill(entry["why"]), "fix": fill(entry["fix"]["text"]),
            "command": fill(entry["fix"]["command"]), "better": fill(entry["better"])}


def lines(code: str, **values) -> list[str]:
    """CLI の形の行（1 行目は `符丁: なぜ`・続けて `通すには:`・`もっと良く:`）。"""
    parts = render(code, **values)
    out = [f"{code}: {parts['why']}"]
    fix = parts["fix"]
    if parts["command"] and parts["command"] not in fix:
        fix = f"{fix}（{parts['command']}）"
    out.append(f"通すには: {fix}")
    if parts["better"]:
        out.append(f"もっと良く: {parts['better']}")
    return out


def counts() -> dict:
    """棚卸しの数（kind ごと・fix・better・no_better）。"""
    catalog = _catalog()
    out = {"entries": len(catalog), "kind": {}, "why": 0, "fix": 0, "fix_command": 0, "better": 0,
           "no_better": {}}
    for entry in catalog.values():
        out["kind"][entry["kind"]] = out["kind"].get(entry["kind"], 0) + 1
        out["why"] += bool(entry["why"])
        out["fix"] += bool(entry["fix"]["text"])
        out["fix_command"] += bool(entry["fix"]["command"])
        out["better"] += bool(entry["better"])
        if entry["no_better"]:
            out["no_better"][entry["no_better"]] = out["no_better"].get(entry["no_better"], 0) + 1
    return out
