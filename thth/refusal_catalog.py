"""断りの目録（設計「監査と審査の機械化と前向きな断り」§3・段 A）。

原則（`docs/原則_前向きな断り.md`）: 断るときは **なぜ・どうすれば通るか・どうすればもっと良くなるか** の 3 つを出す。
この目録は 1 符丁 1 項目で、その 3 つを 1 か所に持つ。

項目の形（`CATALOG[符丁]`）:

- `kind`: `refusal`（通さない・出さない）か `cannot_say`（数や事実を言えない・読めない）。
- `why`: なぜ（1 文）。
- `fix`: `{"text": 通すには, "command": 打てる命令の型 or None}`。
- `better`: もっと良く（1 行）。利用者向けの断りは必須（masaru 2026-11-03 の決定）。
- `no_better`: better を書かない理由。`internal`（内部の故障系）か `terminal`（先の一手が無い）。数に上限を置き版ごとに下げる。
- `doors`: その符丁が出る口（`cli`・`mcp`・`remote`・`dashboard`）。
- `since`: 目録に載った時点の版（段 A の初期の項目は、その時点で既にあった符丁として 5.10.0）。
- `lang`: 文の言語（`ja`）。
- `retired`: 退いた符丁の印（消すときは項目を消さずにこれを立てる・互換の柵が読む）。
- `sources`: 既存の文をどこから引いたか（`fix_dict:<module>.<name>`・`guard_message`・`dashboard`）。

**既存の文は読み取って使う**（写さない）: 各モジュールの説明の辞書（`NEXT`・`_MESSAGES` など）の文は
`fix.text` に、`core.guard_refusal_message` の文は `why` に、import して値を引く。元の辞書は書き換えない
（目録の見方に置き換えるのは次の段）。ダッシュボードの `THTH_REASON` は Python の包みに入らない HTML に
しか無いので、ここに正本として置き（`DASHBOARD_REASON_JA`）、4 つの HTML と一致することを試験で縛る
（次の段で HTML をここから生成する）。

**穴埋めは静的な型と検査済みの値だけ**（`render`）: `<口座>`・`<主体>`・`<名前>`・`<時刻>` の型の穴に、
形を検査した値だけを入れる。自由文・本文・パスは埋めない。
"""
from __future__ import annotations

import importlib
import re

from . import (refusal_catalog_part1, refusal_catalog_part2, refusal_catalog_part3, refusal_catalog_part4,
               refusal_catalog_part5)

# 項目を書いた部（1〜3: 設計 §1 の 8 つの口・集合・JSON の欄・説明の辞書。4〜5: 広告の口 OpsRefused と
# 認可の口 FlowError）。
PARTS = (refusal_catalog_part1, refusal_catalog_part2, refusal_catalog_part3, refusal_catalog_part4,
         refusal_catalog_part5)

LANG = "ja"
INITIAL_SINCE = "5.10.0"
KINDS = ("refusal", "cannot_say")
NO_BETTER = ("internal", "terminal")
DOORS = ("cli", "mcp", "remote", "dashboard")

# 既存の説明の辞書（読む順＝複数の辞書に同じ符丁があるときの優先順）。値は文（str）。
FIX_SOURCES = (
    ("thth.remote", "NEXT"),
    ("thth.invites", "NEXT"),
    ("thth.report_inbox", "NEXT"),
    ("thth.map_store", "NEXT"),
    ("thth.plaza", "NEXT"),
    ("thth.hatena_entry", "_MESSAGES"),
    ("thth.bookmark_cli", "_MESSAGES"),
    ("thth.bookmark_cli", "_BATCH_MESSAGES"),
    ("thth.announce", "_MESSAGES"),
    ("thth.queuefile", "REPLY_TO_FILE_MESSAGES"),
    ("thth.inflight_cli", "_WRITEBACK_NEXT"),
    ("thth.accounts", "_DUMMY_NEXT"),
    ("thth.admin_diagnose", "ISOLATION_HINTS"),
    ("thth.dashboard_cli", "ADS_NEXT"),
    ("thth.cli", "VALUE_ERROR_NEXT"),
)

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


def fix_texts() -> dict[str, list[tuple[str, str]]]:
    """既存の説明の辞書の文を符丁ごとに `[(出どころ, 文), …]`（優先順）で返す。"""
    found: dict[str, list[tuple[str, str]]] = {}
    for module_name, name in FIX_SOURCES:
        table = getattr(importlib.import_module(module_name), name)
        for code, text in table.items():
            found.setdefault(code, []).append((f"{module_name.split('.')[-1]}.{name}", str(text)))
    return found


def guard_why(code: str) -> str | None:
    """`core.guard_refusal_message` の文から、なぜの部分を引く（分岐に無い符丁は None）。"""
    from . import core
    line = core.guard_refusal_message(_GUARD_ACCOUNT, _Refused(code))
    if _GUARD_MARK not in line:
        return None
    why = line.split(_GUARD_MARK, 1)[1]
    if why.endswith(_GUARD_TAIL):
        why = why[: -len(_GUARD_TAIL)]
    if why == code:  # 分岐に無い符丁は符丁そのものが返る
        return None
    return why


def _build() -> dict[str, dict]:
    raw: dict[str, dict] = {}
    for part in PARTS:
        for code, entry in part.ENTRIES.items():
            if code in raw:
                raise ValueError(f"duplicate_catalog_entry: {code}")
            raw[code] = entry
    fixes = fix_texts()
    catalog: dict[str, dict] = {}
    for code, entry in raw.items():
        sources = []
        why = entry.get("why")
        if why is None and code in DASHBOARD_REASON_JA:
            why = DASHBOARD_REASON_JA[code]
            sources.append("dashboard")
        if why is None:
            why = guard_why(code)
            if why is not None:
                sources.append("guard_message")
        fix_text = entry.get("fix")
        if code in fixes:
            # 既存の説明の辞書がある符丁は、その文を使う（食い違わせない・置き換えは次の段）。
            origin, fix_text = fixes[code][0]
            sources.append(f"fix_dict:{origin}")
        catalog[code] = {
            "kind": entry["kind"],
            "why": why or "",
            "fix": {"text": fix_text or "", "command": entry.get("command")},
            "better": entry.get("better") or "",
            "no_better": entry.get("no_better"),
            "doors": tuple(entry.get("doors") or ()),
            "since": entry.get("since", INITIAL_SINCE),
            "lang": LANG,
            "retired": RETIRED.get(code),
            "sources": tuple(sources),
        }
    return catalog


CATALOG: dict[str, dict] = _build()


def get(code: str) -> dict | None:
    return CATALOG.get(code)


# ---- 穴埋め（静的な型に検査済みの値だけ） ----

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?)?\Z")
# 穴の名前 → 型の字面と値の検査。
SLOTS = {
    "account": ("<口座>", _NAME),
    "principal": ("<主体>", _NAME),
    "by": ("<名前>", _NAME),
    "at": ("<時刻>", _TIME),
}


def render(code: str, **values) -> dict:
    """符丁の 3 つを、検査を通った値だけ穴に入れて返す。通らない値・知らない穴は入れない（型のまま）。

    返す形: `{"code", "why", "fix", "command", "better"}`。符丁が目録に無ければ KeyError。
    """
    entry = CATALOG[code]
    filled = {}
    for key, value in values.items():
        slot = SLOTS.get(key)
        if slot is None or not isinstance(value, str) or not slot[1].fullmatch(value):
            continue
        filled[slot[0]] = value

    def fill(text):
        if not text:
            return text
        for placeholder, value in filled.items():
            text = text.replace(placeholder, value)
        return text

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
    out = {"entries": len(CATALOG), "kind": {}, "why": 0, "fix": 0, "fix_command": 0, "better": 0,
           "no_better": {}}
    for entry in CATALOG.values():
        out["kind"][entry["kind"]] = out["kind"].get(entry["kind"], 0) + 1
        out["why"] += bool(entry["why"])
        out["fix"] += bool(entry["fix"]["text"])
        out["fix_command"] += bool(entry["fix"]["command"])
        out["better"] += bool(entry["better"])
        if entry["no_better"]:
            out["no_better"][entry["no_better"]] = out["no_better"].get(entry["no_better"], 0) + 1
    return out
