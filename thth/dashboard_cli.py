"""`thth dashboard [<project> [campaign|series <id>]] [--json] [--full]`——会話の中の 3 階層のダッシュボード
（設計 5.0 §5・段 2）。

**読むだけ。** 媒体の API は呼ばない。何も書かない（栞も runs も）。中身は既存の計算を呼んで束ねるだけで、
二重に計算しない:

- `summary`（`summary_cli.build`）: 公開の本数・反応の和・返していない返信・止まり。前週の数は同じ関数に
  7 日前の `now` を渡して取る。
- `events`（`events_cli.build`）: 要対応（`ongoing: true` の種類と `not_after_passed`）。公開の行はカレンダーの
  過去の日にも使う。
- `schedule`（`report.schedule`）: 予約済み（承認済み・未来・期限内）と下書きの予定。
- 承認待ち（`approve_pending.for_account`）: draft のまま（確定待ち・未依頼）。
- `thth.scenario/1`（段 1・`_load_scenario()` の 1 か所から）: キャンペーン（糸）とシリーズ（型）。

`schema: "thth.dashboard/1"`・`level: "all"|"project"|"campaign"|"series"`。約束（`thth.scenario/1` と同じ）:
鍵は増やしても名前を変えない・消さない。本文は入れない（題・`head`・`next` は 60 字まで）。他人の本文
（はてブのコメント・返信の本文）は入れない。絶対パスを入れない。取れない数は `null` と `cannot_say`。

THTH は決めない: 要対応の並びは `days` の大きい順だけ（`sort_basis: "days_desc"`・同じ日数は種類と
ref の字の順）。重みづけ・おすすめはしない。押すところ（`actions[]`）の指示文は THTH が作り、
`[<project>/<種類>:<id>]` を必ず入れる。戻せないもの（宣言の書き換え・口座を止める・予約の取り消し）は
`irreversible: true`。取り下げ（retract）と公開の承認そのものは置かない（承認待ちの「承認へ」は 1 段目と
承認の頁の URL まで）。

階層 1 の合計は本数だけ。インプレッション・エンゲージメント率は口座をまたいで足さない（`summary`・`after`
の規律）——メディアの中でも口座ごとに並べる（`by_account`）。
"""
from __future__ import annotations

import datetime
import json
import os
import sys

from . import accounts as accounts_mod
from . import jst

SCHEMA = "thth.dashboard/1"
SCENARIO_SCHEMA = "thth.scenario/1"
LEVELS = ("all", "project", "campaign", "series")
TEXT_CHARS = 60
CALENDAR_DAYS = 14
SCHEDULED_DAYS = 14
EVENTS_SINCE = "7d"
SORT_BASIS = "days_desc"
PROMPT_PREFIX = "ダッシュボードから: "
# 他人の本文や絶対パスの入りうる鍵（段 1 の答えを写すときに落とす）。
_DROP_KEYS = frozenset(("body", "text", "comment", "comments", "user", "username", "author_key", "permalink"))
# 要対応に入れる、続いていない出来事の種類（起きた時刻から `EVENTS_SINCE` のあいだ）。
_ATTENTION_POINT_KINDS = ("not_after_passed",)
# 要対応の種類 → 指示文の角括弧の種類（無いものは口座）。
_REF_KIND = {"scenario_stalled": "campaign", "series_low": "series", "not_after_passed": "draft",
             "delivery_held": "delivery", "delivery_behind": "delivery"}
WEEKDAYS_JA = ("月", "火", "水", "木", "金", "土", "日")
# 画面の言葉（型の THTH_MEDIUM・THTH_KIND と同じ）。中の名前・符丁は画面の文に出さない（B1）。
MEDIUM_JA = {"threads": "Threads", "x": "X", "bluesky": "Bluesky", "mastodon": "Mastodon",
             "facebook": "Facebook", "instagram": "Instagram", "hatenablog": "はてなブログ", "hatena_bookmark": "はてブ", "site": "サイト"}
KIND_JA = {"scenario_stalled": "停滞", "series_low": "ストック不足", "untied_drafts": "未分類の下書き",
           "unanswered_replies": "未返信", "schedule_low": "予約切れ間近", "schedule_thin": "予約が薄い",
           "not_after_passed": "期限切れ", "publish_held": "公開を停止中", "account_stopped": "口座が停止",
           "inflight_open": "公開の結果待ち", "delivery_held": "配信の保留", "delivery_behind": "記事の更新待ち",
           "principal_stopped": "はてな ID が停止", "budget_exhausted": "予算切れ",
           "token_data_access_expiring": "データの期限が近い（認可し直す）"}
# 予算の断りの符丁 → 画面の言葉（読むと投稿は別の断り・言葉で分ける）。
BUDGET_JA = {"budget_exhausted": "X の読む予算が今月の上限", "missing_rate": "X の読む予算の為替の設定が無い",
             "post_budget_exhausted": "X の投稿の予算が今月の上限"}
HELD_JA = {"approval_stale": "承認が古い（記事を直した）", "delivery_state_conflict": "配信の記録が食い違っている"}

# 今の THTH で取れない数（設計 §5.3）。0 と混ぜない。
NOT_COLLECTED = {
    "blog_pv": "blog_pv_not_collected",            # はてなブログの数字は取っていない
    "article_clicks": "article_clicks_not_connected",  # 本記事側（GA4）とはつないでいない
}


class DashboardError(ValueError):
    """問いが受け取れない（対象が無い・階層の語が違う・id が無い）。"""


# ------------------------------------------------------------------ 小道具

def clip(text, n: int = TEXT_CHARS):
    """1 行・n 字まで（はみ出したら n-1 字＋「…」）。文字列でなければ None。"""
    if not isinstance(text, str):
        return None
    flat = " ".join(text.split())
    return flat if len(flat) <= n else flat[: n - 1] + "…"


def _relative(path):
    """絶対パスを出さない（repo の相対はそのまま・絶対ならファイル名だけ）。"""
    if not isinstance(path, str) or not path:
        return None
    if path.startswith(("/", "~")) or (len(path) > 1 and path[1] == ":"):
        return os.path.basename(path)
    return path


def _clean(value):
    """段 1 の答えを写すときの掃除: 他人の本文の鍵を落とし、`head`・`title`・`what` を 60 字に、
    `declared_in` などのパスを相対に。"""
    if isinstance(value, dict):
        out = {}
        for key, child in value.items():
            if key in _DROP_KEYS:
                continue
            if key in ("head", "title", "what", "next", "label", "goal_text") and isinstance(child, str):
                out[key] = clip(child)
            elif key in ("declared_in", "file", "path", "queue_file") and isinstance(child, str):
                out[key] = _relative(child)
            else:
                out[key] = _clean(child)
        return out
    if isinstance(value, list):
        return [_clean(child) for child in value]
    return value


def ref_tag(project, kind, ident=None) -> str:
    """指示文の角括弧（`[kopicha/campaign:num-04-sencha]`）。同名で混ざらず、LLM が id を探し直さない。"""
    return f"[{project}]" if kind is None else f"[{project}/{kind}:{ident}]"


def action(label, project, kind, ident, body, *, irreversible=False) -> dict:
    """押すところ 1 つ。指示文は THTH が作る（LLM・型は書き換えない）。"""
    prompt = f"{PROMPT_PREFIX}{ref_tag(project, kind, ident)} {body}"
    if irreversible:
        prompt += "（戻せない操作です。実行の前に 1 度確かめてください）"
    return {"label": label, "prompt": prompt, "irreversible": bool(irreversible)}


def _days_since(at, now):
    moment = jst.parse(at) if isinstance(at, str) else None
    if moment is None:
        return None
    return max(0, (now - moment).days)


def _int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


# ------------------------------------------------------------------ 段 1 との継ぎ目（1 か所）

def _scenario_module():
    """段 1 の本物（`thth.scenario`・`build(target, now=)` と `show(target, id, now=)`）。

    入らない（ImportError）ときだけ None——`scenario_not_installed` の道は、段 1 を外した配布のために残す。
    """
    try:
        from . import scenario
    except ImportError:
        return None
    return scenario


def _load_scenario(project, now=None, allowed_names=None):
    """段 1 の `thth scenario <project> --json` の中身（dict）か、読めない理由の符丁。

    戻り値 `(payload, None)` か `(None, 符丁)`。符丁は `scenario_not_installed`（段 1 が無い）・
    `no_declarations`（宣言が読めない・無い）・`scenario_schema_unknown`（版が違う）。
    """
    module = _scenario_module()
    if module is None:
        return None, "scenario_not_installed"
    try:
        payload = module.build(project, now=now, allowed_names=allowed_names)
    except Exception:  # noqa: BLE001 — 宣言が読めなくても一覧の他の部分は出す
        return None, "no_declarations"
    if not isinstance(payload, dict):
        return None, "no_declarations"
    if payload.get("schema") != SCENARIO_SCHEMA:
        return None, "scenario_schema_unknown"
    return payload, None


def _load_scenario_show(project, ident, now=None, allowed_names=None):
    """段 1 の `thth scenario show <project> <id> --json` の中身。読めなければ None。

    `allowed_names`（遠くの道の鍵の範囲）の外の口座の原稿は段 1 が読まない（`_load_scenario` と同じ）。
    """
    module = _scenario_module()
    show = getattr(module, "show", None) if module is not None else None
    if not callable(show):
        return None
    try:
        payload = show(project, ident, now=now, allowed_names=allowed_names)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(payload, dict) or payload.get("schema") != SCENARIO_SCHEMA:
        return None
    return payload


def _scenario_node(project, now=None, allowed_names=None):
    payload, reason = _load_scenario(project, now, allowed_names)
    if payload is None:
        return {"available": False, "cannot_say": reason}, None
    node = {"available": True, "generated_at": payload.get("generated_at"),
            "n_campaigns": len(payload.get("threads") or []),
            "n_campaigns_stalled": sum(1 for t in payload.get("threads") or [] if t.get("state") == "stalled"),
            "n_series": len(payload.get("series") or []),
            "untied": _clean(payload.get("untied")),
            "cannot_say": list(payload.get("cannot_say") or [])}
    return node, payload


# ------------------------------------------------------------------ 出所ごと（既存の計算を呼ぶだけ）

def _accounts_of(project, allowed_names):
    from . import morning
    names = morning.targets(project)
    if allowed_names is not None:
        names = [name for name in names if name in allowed_names]
    return names


def _projects(allowed_names):
    """見える口座を project ごとに（project の無い口座は口座名を project として）。"""
    out: dict = {}
    for name in accounts_mod.list_account_names():
        if allowed_names is not None and name not in allowed_names:
            continue
        try:
            cfg = accounts_mod.load_account(name)
        except (accounts_mod.AccountError, ValueError, TypeError):
            continue
        out.setdefault(cfg.get("project") or name, []).append(name)
    return out


def _mediums(names):
    out = {}
    for name in names:
        try:
            out[name] = accounts_mod.load_account(name).get("media")
        except (accounts_mod.AccountError, ValueError, TypeError):
            out[name] = None
    return out


def _summary(project, now, allowed, cannot_say, label):
    from . import summary_cli
    try:
        return summary_cli.build(project, now=now, allowed_names=allowed)
    except Exception:  # noqa: BLE001 — 1 つの出所で全部を落とさない
        cannot_say.add(f"{label}_unreadable")
        return None


def _events(project, now, allowed, cannot_say):
    from . import events_cli
    try:
        payload = events_cli.build(project, since=EVENTS_SINCE, now=now, allowed_names=allowed)
    except Exception:  # noqa: BLE001
        cannot_say.add("events_unreadable")
        return None
    cannot_say.update(f"events:{code}" for code in payload.get("cannot_say") or [])
    return payload


def _schedule(names, now, cannot_say):
    from . import report
    rows = []
    for name in names:
        try:
            rows += report.schedule(name, now=now, days=max(CALENDAR_DAYS, SCHEDULED_DAYS))
        except Exception:  # noqa: BLE001
            cannot_say.add("schedule_unreadable")
    return rows


def _pending(names, now, cannot_say):
    from . import approve_pending
    rows = []
    for name in names:
        try:
            cfg = accounts_mod.load_account(name)
            result = approve_pending.for_account(name, cfg, now=now)
        except Exception:  # noqa: BLE001
            cannot_say.add("approve_pending_unreadable")
            continue
        for state in (approve_pending.AWAITING, approve_pending.NOT_REQUESTED):
            for row in result[state]:
                rows.append({"account": name, "file": os.path.basename(row["file"]),
                             "publish_at": row.get("publish_at"), "state": state,
                             "medium": cfg.get("media"), "head": _manuscript_head(row["file"], cfg)})
    rows.sort(key=lambda r: (str(r["publish_at"] or ""), r["account"], r["file"]))
    return rows


def _manuscript_head(path, cfg):
    """承認待ちの原稿の題（自分の原稿の先頭 60 字・`report.schedule` と同じ取り方）。読めなければ None。"""
    try:
        from .scenario import manuscript_head
    except ImportError:
        return None
    if not isinstance(path, str):
        return None
    if not os.path.isabs(path):
        try:
            path = os.path.join(accounts_mod.resolved_repo_dir(cfg), cfg.get("queue_dir") or "", path)
        except Exception:  # noqa: BLE001
            return None
    return clip(manuscript_head(path, cfg.get("media")))


def _measured_as_of(names):
    """実測のいちばん新しい採取の時刻（`measured.load` を読むだけ）。無ければ None。"""
    from . import measured
    latest = None
    for name in names:
        try:
            data = measured.load(name)
        except Exception:  # noqa: BLE001
            continue
        for post in data.get("posts") or []:
            for row in post.get("rows") or []:
                at = jst.parse(row.get("collected_at"))
                if at is not None and (latest is None or at > latest):
                    latest = at
    return jst.iso(latest) if latest is not None else None


# ------------------------------------------------------------------ KPI

def _views(row):
    """summary の 1 口座の 7 日の表示の和と、反応の和（無ければ None）。"""
    reactions = ((row or {}).get("reactions") or {}).get("7d") or {}
    views = (reactions.get("sums") or {}).get("views")
    return (views or {}).get("sum"), (views or {}).get("n"), reactions.get("reactions_total")


def _impressions(summary, previous):
    """口座ごとのインプレッション（7 日）と前週比・エンゲージメント率。**口座をまたいで足さない。**"""
    out = {}
    for name, row in ((summary or {}).get("by_account") or {}).items():
        views, n, total = _views(row)
        prev_views = _views(((previous or {}).get("by_account") or {}).get(name))[0]
        ratio = None
        if _int(views) and _int(prev_views) and prev_views > 0:
            ratio = round((views - prev_views) / prev_views * 100, 1)
        er = round(total / views * 100, 1) if _int(views) and views > 0 and _int(total) else None
        out[name] = {"medium": row.get("medium"), "impressions": views, "n_posts_measured": n,
                     "impressions_previous": prev_views, "change_pct": ratio,
                     "engagement_rate_pct": er}
    return {"by_account": out, "summed": False,
            "basis": "summary の 7 日の窓・views の和・ER は反応の和 ÷ 表示の和（実測のある投稿）・前週は 7 日前の同じ窓",
            "not_summed_because": "口座をまたいで足さない（summary・after の規律）"}


def _published(summary):
    rows = ((summary or {}).get("by_account") or {}).values()
    values = [((row.get("published") or {}).get("7d") or {}).get("published") for row in rows]
    values = [v for v in values if _int(v)]
    return sum(values) if values else (None if rows else 0)


def _scheduled(schedule, now):
    horizon = now + datetime.timedelta(days=SCHEDULED_DAYS)
    return sum(1 for row in schedule if row["status"] == "approved" and not row["past"]
               and not row.get("expired") and not row.get("waiting_for")
               and (at := jst.parse(row["publish_at"])) is not None and at <= horizon)


# ------------------------------------------------------------------ 要対応

def _attention(project, events, summary, scenario, now, mediums=None, schedule=None):
    mediums = mediums or {}
    threads = {t.get("id"): t for t in (scenario or {}).get("threads") or []}
    series_by_id = {s.get("id"): s for s in (scenario or {}).get("series") or []}
    heads = {(r.get("account"), r.get("file")): r.get("head") for r in schedule or []}
    rows, seen = [], set()
    for event in (events or {}).get("events") or []:
        kind = event.get("kind")
        if not (event.get("ongoing") or kind in _ATTENTION_POINT_KINDS):
            continue
        ref = event.get("ref")
        key = (kind, event.get("account"), ref)
        if key in seen:
            continue
        seen.add(key)
        days = event.get("days") if _int(event.get("days")) else _days_since(event.get("occurred_at"), now)
        title, next_text = _event_text(event, mediums, threads, series_by_id, heads)
        rows.append(_attention_row(project, kind, ref, event.get("account"), clip(title),
                                   event.get("occurred_at"), days, clip(next_text),
                                   "events", event.get("event_id"),
                                   stage=event.get("stage"), next_slot=event.get("next_slot")))
    # events が先（段 1 の `scenario_stalled`・`series_low`）。events に無いものだけ段 1 の答えから写す
    # （events が読めなかったとき・窓の外のとき）。同じ (種類, ref) は 1 度だけ数える。
    if scenario is not None:
        refs = {(row["kind"], row["ref"]) for row in rows}
        for thread in scenario.get("threads") or []:
            if thread.get("state") == "stalled" and ("scenario_stalled", thread.get("id")) not in refs:
                stalled = thread.get("stalled") or {}
                title, next_text = _stalled_text(thread, None)
                rows.append(_attention_row(
                    project, "scenario_stalled", thread.get("id"), None, clip(title),
                    stalled.get("since"), stalled.get("days") if _int(stalled.get("days")) else None,
                    clip(next_text), "scenario", None,
                    stage=(thread.get("stage") or {}).get("name")))
        for series in scenario.get("series") or []:
            if (series.get("stock") or {}).get("low") and ("series_low", series.get("id")) not in refs:
                title, next_text = _series_low_text(series, None)
                rows.append(_attention_row(
                    project, "series_low", series.get("id"), None, clip(title), None, None,
                    clip(next_text), "scenario", None, next_slot=(series.get("next_slot") or {}).get("at")))
        untied = (scenario.get("untied") or {}).get("drafts")
        if _int(untied) and untied > 0:
            rows.append(_attention_row(project, "untied_drafts", "drafts", None, f"未分類の下書き {untied} 件",
                                       None, None, "キャンペーンかシリーズに振り分ける", "scenario", None,
                                       n=untied))
    for name, row in ((summary or {}).get("by_account") or {}).items():
        n = (row.get("unanswered_replies") or {}).get("n")
        if _int(n) and n > 0:
            rows.append(_attention_row(project, "unanswered_replies", name, name,
                                       f"{_medium_ja(mediums.get(name) or row.get('medium'))} の返していない返信 {n} 件",
                                       None, None, None, "summary", None, n=n))
    rows.sort(key=lambda r: (-(r["days"] if r["days"] is not None else -1), r["kind"], str(r["ref"])))
    return rows


def _medium_ja(medium) -> str:
    return MEDIUM_JA.get(medium, "口座") if isinstance(medium, str) else "口座"


def _md_hm(at) -> str:
    moment = jst.parse(at) if isinstance(at, str) else None
    return f"{moment.month}/{moment.day} {moment.strftime('%H:%M')}" if moment is not None else ""


def _label_of(next_items, kind=None):
    """段 1 の `next[]` の画面の言葉（`label`）。`kind` を渡せばその種類を先に。"""
    if not isinstance(next_items, list):
        return None
    rows = [n for n in next_items if isinstance(n, dict)]
    if kind is not None:
        rows = [n for n in rows if n.get("kind") == kind] + [n for n in rows if n.get("kind") != kind]
    for row in rows:
        if isinstance(row.get("label"), str):
            return row["label"]
    return None


def _stalled_text(thread, event):
    """停滞の行: 題は段 1 の `title`（無ければ「キャンペーン」）＋止まっている段の言葉。id は出さない。"""
    thread = thread or {}
    event = event or {}
    name = thread.get("title") or event.get("title") or "名前の無いキャンペーン"
    label = (thread.get("stalled") or {}).get("label") or event.get("label")
    if label is None:
        stage = (thread.get("stage") or {}).get("name") or event.get("stage")
        label = STALLED_FALLBACK.get(stage, "途中の段で止まっている")
    return f"{name} — {label}", _label_of(thread.get("next")) or _label_of(event.get("next"))


STALLED_FALLBACK = {"collect": "リサーチが進んでいない", "diary": "仮説のブログが未公開", "article": "本記事が未公開",
                    "announce": "SNS 告知が未投稿", "warm": "返信の対応が止まっている"}


def _series_low_text(series, event):
    series = series or {}
    event = event or {}
    stock = series.get("stock") or {}
    n = stock.get("approved_future", event.get("approved_future"))
    floor = stock.get("floor", event.get("floor"))
    name = series.get("title") or event.get("title") or "名前の無いシリーズ"
    title = f"{name} — ストックが {n} 回分" + (f"（目安 {floor} 回）" if floor is not None else "")
    next_text = _label_of(series.get("next"), "slot") or _label_of(event.get("next"), "slot")
    slot = (series.get("next_slot") or {}).get("at") or event.get("next_slot")
    if next_text is None and slot:
        next_text = f"次の枠 {_md_hm(slot)} の原稿を用意する"
    return title, next_text


def _event_text(event, mediums, threads, series_by_id, heads):
    """events の 1 行を画面の言葉に（題, 次の一手）。events の `summary`（符丁まじりの長文）は使わない。"""
    kind = event.get("kind")
    ref = event.get("ref")
    medium = _medium_ja(mediums.get(event.get("account")))
    if kind == "scenario_stalled":
        return _stalled_text(threads.get(ref), event)
    if kind == "series_low":
        return _series_low_text(series_by_id.get(ref), event)
    if kind == "schedule_low":
        hours = event.get("stock_hours")
        if event.get("last_publish_at"):
            return f"{medium} の予約の在庫が薄い（あと {hours} 時間分）", "承認済みの予約を足す"
        return f"{medium} の承認済みの予約が無い", "承認済みの予約を足す"
    if kind == "schedule_thin":
        thin = next((t for t in event.get("thin_days") or [] if isinstance(t, dict)), None)
        day = jst.parse(thin["date"] + "T00:00:00+09:00") if thin and isinstance(thin.get("date"), str) else None
        hours = [h for h in event.get("free_hours") or [] if _int(h)]
        title = (f"{medium} の {day.month}/{day.day} の予約が薄い（{thin.get('count')} 本）" if day is not None
                 else f"{medium} の予約が薄い日がある")
        return title, ("空いた時間: " + "・".join(f"{h} 時台" for h in hours)) if hours else "予約を足す"
    if kind == "account_stopped":
        return f"{medium} の口座を止めている", "理由を見て、戻すか決める"
    if kind == "publish_held":
        return f"{medium} の自動の公開を止めている", "理由を見て、戻すか決める"
    if kind == "inflight_open":
        return f"{medium} の公開の結果待ち", "結果を確かめる"
    if kind == "principal_stopped":
        return "はてな ID の書き込みを止めている", "理由を見て、戻すか決める"
    if kind == "budget_exhausted":
        return BUDGET_JA.get(ref, "X の予算で断っている"), "来月まで待つか、上限を見直す"
    if kind == "delivery_held":
        reason = event.get("reason")
        why = HELD_JA.get(reason) or ("配信の記録を確かめる" if isinstance(reason, str)
                                      and reason.startswith("delivery_state_") else "出せない")
        return f"はてなブログの原稿 — {why}", "中身と理由を確かめる"
    if kind == "delivery_behind":
        return "はてなブログの記事 — 正本を直したが記事は前のまま", "更新するか決める"
    if kind == "not_after_passed":
        head = heads.get((event.get("account"), ref))
        return f"{head or medium + ' の原稿'} — 出さない期限を過ぎた", "出し直すか決める"
    return f"{medium} の{KIND_JA.get(kind, 'お知らせ')}", None


def _attention_row(project, kind, ref, account, title, since, days, next_text, source, event_id, **extra):
    row = {"kind": kind, "ref": ref, "account": account, "title": title, "since": since, "days": days,
           "next": next_text, "source": source, "event_id": event_id}
    row.update({k: v for k, v in extra.items() if v is not None})
    row["actions"] = _attention_actions(project, kind, ref, account, extra)
    return row


def _attention_actions(project, kind, ref, account, extra):
    if kind == "scenario_stalled":
        acts = [action("開く", project, "campaign", ref, "キャンペーンのダッシュボードを開いてください"
                       f"（thth dashboard {project} campaign {ref} --json）。")]
        if extra.get("stage") == "article":
            acts.insert(0, action("公開した", project, "campaign", ref,
                                  "本記事を公開した。URL は私が伝える（無ければ書かない）。"
                                  "糸のファイルに出来事を 1 行足して、次のステップを見せてください。"))
        return acts
    if kind == "series_low":
        at = extra.get("next_slot")
        return [action("下書き", project, "series", ref,
                       f"次の枠{'（' + at + '）' if at else ''}に入れる下書きを作ってください。承認は私がします。"),
                action("開く", project, "series", ref,
                       f"シリーズのダッシュボードを開いてください（thth dashboard {project} series {ref} --json）。")]
    if kind == "untied_drafts":
        return [action("振り分け", project, None, None,
                       "未分類の下書きを thth scenario lint で一覧にして、どのキャンペーンかシリーズに入れるか"
                       "案を見せてください。札を書くのは私が決めてから。")]
    if kind == "unanswered_replies":
        return [action("未返信を見る", project, "account", ref,
                       f"返していない返信を見せてください（thth unanswered {ref}）。")]
    if kind in ("schedule_low", "schedule_thin"):
        return [action("空きの下書き", project, "account", account,
                       "予約の薄い日と空いた時間を見せて、入れる下書きを作ってください。承認は私がします。")]
    if kind == "not_after_passed":
        return [action("中身を見る", project, "draft", ref,
                       "期限を過ぎて出なかった原稿の中身と状態を見せてください。出し直すかは私が決めます。")]
    tag_kind = _REF_KIND.get(kind, "account")
    ident = ref if tag_kind != "account" else account
    return [action("理由を見る", project, tag_kind, ident,
                   f"{kind} の理由と次の一歩を見せてください（thth events {project} --json の該当の行）。")]


# ------------------------------------------------------------------ キャンペーン・シリーズの行

def _campaign_row(project, thread):
    row = _clean({key: thread.get(key) for key in
                  ("id", "title", "goal_text", "declared_in", "state", "stage", "stages", "next",
                   "last_movement_at", "stalled", "cannot_say")})
    ident = thread.get("id")
    row["actions"] = [action("開く", project, "campaign", ident,
                             f"キャンペーンのダッシュボードを開いてください（thth dashboard {project} campaign "
                             f"{ident} --json）。")]
    return row


def _series_row(project, series):
    row = _clean({key: series.get(key) for key in
                  ("id", "title", "accounts", "slots", "stock", "next_slot", "empty_slots_14d", "material",
                   "deadline", "cannot_say")})
    ident = series.get("id")
    row["actions"] = [action("開く", project, "series", ident,
                             f"シリーズのダッシュボードを開いてください（thth dashboard {project} series {ident} "
                             "--json）。")]
    return row


def _pending_rows(project, pending):
    out = []
    for row in pending:
        out.append(dict(row, actions=[action(
            "承認へ", project, "draft", row["file"],
            f"（口座 {row['account']}）承認の 1 段目（thth approve）を出して、承認の頁の URL を貼ってください。"
            "確定は私が承認の頁で押します。")]))
    return out


# ------------------------------------------------------------------ カレンダー

def _calendar(project, schedule, events, scenario, mediums, now):
    today = now.date()
    start = today - datetime.timedelta(days=today.weekday())   # 今週の月曜から 2 週
    days = [start + datetime.timedelta(days=i) for i in range(CALENDAR_DAYS)]
    by_day = {day.isoformat(): [] for day in days}
    for row in schedule:
        at = jst.parse(row["publish_at"])
        if at is None or at.date().isoformat() not in by_day or row.get("expired"):
            continue
        status = "scheduled" if row["status"] == "approved" else "draft"
        if row["past"] and status == "scheduled":
            status = "overdue"
        by_day[at.date().isoformat()].append(_calendar_item(
            project, at, row["account"], mediums.get(row["account"]), status, clip(row.get("head")),
            row["file"]))
    for event in (events or {}).get("events") or []:
        if event.get("kind") != "published":
            continue
        at = jst.parse(event.get("occurred_at"))
        if at is None or at.date().isoformat() not in by_day:
            continue
        title = event.get("summary") or ""
        title = title[len("公開: "):] if title.startswith("公開: ") else title
        by_day[at.date().isoformat()].append(_calendar_item(
            project, at, event.get("account"), mediums.get(event.get("account")), "published", clip(title),
            event.get("ref")))
    # 橙（gap）はシリーズの空き枠（`empty_slots`・古い答えなら `next_slot`）と予約が薄い日（`schedule_thin`）
    # だけ。予定の無い日は無地（`empty: true`）。
    gap_days = set()
    for series in (scenario or {}).get("series") or []:
        empties = series.get("empty_slots")
        if not isinstance(empties, list):
            slot = series.get("next_slot") or {}
            empties = [slot] if slot.get("filled") is False else []
        for slot in empties:
            at = jst.parse(slot.get("at")) if isinstance(slot, dict) and isinstance(slot.get("at"), str) else None
            if at is None or at.date().isoformat() not in by_day:
                continue
            filled_by = [a for a in slot.get("filled_by") or [] if isinstance(a, str)]
            name = series.get("title") or "シリーズ"
            head = f"空き枠: {name}" + (
                f"（{'・'.join(_medium_ja(mediums.get(a)) for a in filled_by)} だけ）" if filled_by else "")
            gap_days.add(at.date().isoformat())
            by_day[at.date().isoformat()].append({
                "time": at.strftime("%H:%M"), "at": jst.iso(at), "account": None, "medium": None,
                "status": "gap", "head": clip(head), "series": series.get("id"), "filled_by": filled_by,
                "actions": [action("下書き", project, "series", series.get("id"),
                                   f"{jst.iso(at)} の枠に入れる下書きを作ってください。承認は私がします。")]})
    for event in (events or {}).get("events") or []:
        if event.get("kind") == "schedule_thin":
            for thin in event.get("thin_days") or []:
                if isinstance(thin, dict) and thin.get("date") in by_day:
                    gap_days.add(thin["date"])
    out = []
    for day in days:
        key = day.isoformat()
        items = sorted(by_day[key], key=lambda item: (item["time"], item["status"]))
        future = day >= today
        empty = future and not any(item["status"] in ("scheduled", "published") for item in items)
        out.append({"date": key, "weekday": WEEKDAYS_JA[day.weekday()], "today": day == today,
                    "past": day < today, "gap": key in gap_days, "empty": empty, "items": items})
    return out


def _calendar_item(project, at, account, medium, status, head, ref):
    if status == "published":
        acts = [action("成果を見る", project, "account", account,
                       f"{at.strftime('%m/%d %H:%M')} に出した投稿（{ref}）の成果を見せてください（thth measured）。")]
    else:
        acts = [action("中身を見る", project, "draft", ref,
                       f"（口座 {account}）{at.strftime('%m/%d %H:%M')} の予定の中身と状態を見せてください。")]
    return {"time": at.strftime("%H:%M"), "at": jst.iso(at), "account": account, "medium": medium,
            "status": status, "head": head, "ref": ref, "actions": acts}


# ------------------------------------------------------------------ 階層ごと

def _project_parts(project, names, now, allowed, cannot_say, scenario_allowed=None):
    """`scenario_allowed` は段 1 に渡す範囲（遠くの道の鍵の口座・手元なら None で project 全体）。"""
    summary = _summary(project, now, allowed, cannot_say, "summary")
    previous = _summary(project, now - datetime.timedelta(days=7), allowed, cannot_say, "summary_previous")
    events = _events(project, now, allowed, cannot_say)
    schedule = _schedule(names, now, cannot_say)
    pending = _pending(names, now, cannot_say)
    scenario_node, scenario = _scenario_node(project, now, scenario_allowed)
    if summary:
        cannot_say.update(f"summary:{code}" for code in summary.get("cannot_say") or [])
    return summary, previous, events, schedule, pending, scenario_node, scenario


def _as_of(now, names, events, scenario):
    return {"generated_at": jst.iso(now), "measured": _measured_as_of(names),
            "schedule": jst.iso(now), "events": (events or {}).get("generated_at"),
            "scenario": (scenario or {}).get("generated_at")}


def _kpi(attention, pending, schedule, summary, previous, now):
    published = _published(summary)
    published_prev = _published(previous)
    by_kind: dict = {}
    for row in attention:
        by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1
    return {"attention": {"n": len(attention), "by_kind": by_kind},
            "pending_approval": {"n": len(pending),
                                 "awaiting_confirm": sum(1 for r in pending if r["state"] == "awaiting_confirm"),
                                 "not_requested": sum(1 for r in pending if r["state"] == "not_requested")},
            "scheduled_14d": {"n": _scheduled(schedule, now), "days": SCHEDULED_DAYS},
            "published_7d": {"n": published, "previous": published_prev,
                             "delta": (published - published_prev)
                             if _int(published) and _int(published_prev) else None},
            "impressions_7d": _impressions(summary, previous)}


def build_project(project, *, now=None, allowed_names=None, full=False) -> dict:
    now = now if now is not None else jst.now_jst()
    names = _accounts_of(project, allowed_names)
    if not names:
        raise DashboardError("target_unknown")
    cannot_say: set = set()
    summary, previous, events, schedule, pending, scenario_node, scenario = _project_parts(
        project, names, now, allowed_names, cannot_say, allowed_names)
    mediums = _mediums(names)
    attention = _attention(project, events, summary, scenario, now, mediums, schedule)
    payload = _head("project", now, project)
    payload.update({
        "accounts": names, "mediums": mediums,
        "as_of": _as_of(now, names, events, scenario),
        "kpi": _kpi(attention, pending, schedule, summary, previous, now),
        "attention": attention, "sort_basis": SORT_BASIS,
        "pending_approval": _pending_rows(project, pending),
        "scenario": scenario_node,
        "campaigns": [_campaign_row(project, t) for t in (scenario or {}).get("threads") or []],
        "series": [_series_row(project, s) for s in (scenario or {}).get("series") or []],
        "calendar": _calendar(project, schedule, events, scenario, mediums, now),
        "actions": [action("次のキャンペーン候補", project, None, None,
                           "反応の多かった問いや告知から、次のキャンペーンの候補を並べてください。選ぶのは私です。")],
        "cannot_say": sorted(cannot_say),
        "limitations": _limitations()})
    if full and scenario is not None:
        payload["campaign_details"] = [
            _campaign_detail(project, t, events, now, allowed_names) for t in scenario.get("threads") or []]
        payload["series_details"] = [_series_detail(project, s, now, allowed_names)
                                     for s in scenario.get("series") or []]
    return payload


def build_all(*, now=None, allowed_names=None) -> dict:
    now = now if now is not None else jst.now_jst()
    projects = _projects(allowed_names)
    cards, cannot_say = [], set()
    totals = {"attention": 0, "pending_approval": 0, "scheduled_14d": 0, "published_7d": 0}
    # 数えられなかったメディア（鍵 → project の列）。黙って飛ばさない（0 と混ぜない）。
    totals_cannot_say: dict = {}
    for project, names in sorted(projects.items()):
        local: set = set()
        allowed = names if allowed_names is None else [n for n in names if n in allowed_names]
        summary, previous, events, schedule, pending, scenario_node, scenario = _project_parts(
            project, names, now, allowed, local, allowed_names)
        mediums = _mediums(names)
        attention = _attention(project, events, summary, scenario, now, mediums, schedule)
        kpi = _kpi(attention, pending, schedule, summary, previous, now)
        calendar = _calendar(project, schedule, events, scenario, mediums, now)
        week = [{"date": d["date"], "weekday": d["weekday"], "gap": d["gap"], "empty": d["empty"]}
                for d in calendar if not d["past"]][:7]
        unreadable = {"published_7d": summary is None, "attention": events is None,
                      "pending_approval": "approve_pending_unreadable" in local,
                      "scheduled_14d": "schedule_unreadable" in local}
        for key in totals:
            value = kpi[key]["n"]
            if _int(value) and not unreadable[key]:
                totals[key] += value
            else:
                totals_cannot_say.setdefault(key, []).append(project)
        cards.append({
            "project": project, "accounts": names, "mediums": _mediums(names),
            "status": "attention" if attention else "ok",
            "kpi": {"published_7d": kpi["published_7d"], "impressions_7d": kpi["impressions_7d"],
                    "attention": kpi["attention"]["n"], "pending_approval": kpi["pending_approval"]["n"]},
            "scenario": scenario_node, "week": week,
            "first_attention": ({k: v for k, v in attention[0].items() if k != "actions"}
                                if attention else None),
            "as_of": _as_of(now, names, events, scenario),
            "cannot_say": sorted(local),
            "actions": [action("開く", project, None, None,
                               f"このメディアのダッシュボードを開いてください（thth dashboard {project} --json）。")]})
        cannot_say.update(f"{project}:{code}" for code in local)
    payload = _head("all", now, None)
    payload.update({
        "totals": totals, "totals_cannot_say": totals_cannot_say,
        "totals_basis": "本数だけを足す。インプレッション・エンゲージメント率はメディアのカードで口座ごとに見る",
        "n_projects": len(cards),
        "n_projects_attention": sum(1 for c in cards if c["status"] == "attention"),
        "projects": cards, "sort_basis": "project_name",
        "cannot_say": sorted(cannot_say), "limitations": _limitations()})
    return payload


# ------------------------------------------------------------------ 階層 3 の成果（既存の台帳を読むだけ）

def _measured_index(names, cannot_say):
    """口座ごとの `{post_id: いちばん新しい実測の行}`（`measured.load` を読むだけ・`summary` と同じ選び方）。"""
    from . import measured
    out = {}
    for name in sorted(set(names)):
        try:
            posts = measured.load(name)["posts"]
        except Exception:  # noqa: BLE001
            cannot_say.add("measured_unreadable")
            continue
        index = {}
        for post in posts:
            rows = [row for row in post.get("rows") or [] if jst.parse(row.get("collected_at"))]
            if rows and post.get("post_id"):
                rows.sort(key=lambda row: jst.parse(row["collected_at"]))
                index[post["post_id"]] = rows[-1]
        out[name] = index
    return out


def _bookmarks_by_delivery(names, cannot_say):
    """第三者のはてブの数を配信 id ごとに（`events` の `bookmark_received` と同じ出所・同じ数え方:
    利用者ごとに 1 回・全期間）。本文（コメント）と user は見ない。"""
    from . import events_cli
    counts = {}
    for name in sorted(set(names)):
        try:
            cfg = accounts_mod.load_account(name)
            rows, broken = events_cli.reaction_events(name, cfg)
        except Exception:  # noqa: BLE001
            cannot_say.add("reactions_unreadable")
            continue
        if broken:
            cannot_say.add("reactions_unreadable")
        for row in rows:
            if row.get("kind") == "bookmark_received" and isinstance(row.get("ref"), str):
                counts[row["ref"]] = counts.get(row["ref"], 0) + 1
    return counts


def _item_status(item):
    if item.get("posted"):
        return "posted"
    return item.get("delivery_state") or item.get("status")


def _episode(item, measured_index, bookmarks, readable):
    """回 1 つの成果。取れない数は null と理由（0 と混ぜない）。"""
    from . import summary_cli
    account, medium = item.get("account"), item.get("medium")
    post_ids = [p for p in item.get("post_ids") or [] if isinstance(p, str)]
    row = {"ref": item.get("file"), "head": clip(item.get("head")), "account": account, "medium": medium,
           "status": _item_status(item),
           "at": item.get("posted_at") or item.get("publish_at"), "delivery": item.get("delivery"),
           "post_id": post_ids[0] if post_ids else None,
           "impressions": None, "reactions_total": None, "engagement_rate_pct": None,
           "bookmarks_received": None, "clicks": None, "collected_at": None, "cannot_say": {}}
    why = row["cannot_say"]
    if medium == "hatenablog":
        why["impressions"] = NOT_COLLECTED["blog_pv"]
        delivery = item.get("delivery")
        if not delivery:
            why["bookmarks_received"] = "no_deliveries"
        elif "reactions" not in readable:
            why["bookmarks_received"] = "reactions_unreadable"
        else:
            row["bookmarks_received"] = bookmarks.get(delivery, 0)
    elif not item.get("posted"):
        why["impressions"] = "not_published_yet"
    elif account not in measured_index:
        why["impressions"] = "measured_unreadable"
    else:
        latest = measured_index[account].get(row["post_id"]) if row["post_id"] else None
        if latest is None:
            why["impressions"] = "not_measured_yet"
        else:
            metrics = latest.get("metrics") or {}
            views = metrics.get("views")
            totals = [metrics[k] for k in summary_cli.TOTAL_KEYS if _int(metrics.get(k))]
            row["impressions"] = views if _int(views) else None
            row["reactions_total"] = sum(totals) if totals else None
            row["collected_at"] = latest.get("collected_at")
            if _int(views) and views > 0 and totals:
                row["engagement_rate_pct"] = round(sum(totals) / views * 100, 1)
            if row["impressions"] is None:
                why["impressions"] = "not_measured_yet"
    why.setdefault("clicks", NOT_COLLECTED["article_clicks"])
    return row


def _episodes(items):
    """回ごとの成果の列（`show` の `items[]` から）。"""
    cannot_say: set = set()
    sns = [i.get("account") for i in items if i.get("medium") not in ("hatenablog", None) and i.get("posted")]
    blogs = [i.get("account") for i in items if i.get("medium") == "hatenablog" and i.get("delivery")]
    measured_index = _measured_index(sns, cannot_say)
    bookmarks = _bookmarks_by_delivery(blogs, cannot_say)
    readable = set() if "reactions_unreadable" in cannot_say else {"reactions"}
    return [_episode(i, measured_index, bookmarks, readable) for i in items if isinstance(i, dict)], cannot_say


def _by_account(episodes, key):
    out = {}
    for row in episodes:
        if _int(row.get(key)):
            slot = out.setdefault(row["account"], {"sum": 0, "n": 0})
            slot["sum"] += row[key]
            slot["n"] += 1
    return out


def _impressions_outcome(episodes):
    """インプレッションの和。**口座をまたいで足さない**（2 口座以上なら null と `accounts_not_summed`）。"""
    by_account = _by_account(episodes, "impressions")
    if len(by_account) == 1:
        only = next(iter(by_account.values()))
        return only["sum"], None, by_account
    if by_account:
        return None, "accounts_not_summed", by_account
    if any(r["medium"] != "hatenablog" and r["status"] == "posted" for r in episodes):
        return None, "not_measured_yet", by_account
    return None, "not_published_yet", by_account


def _campaign_detail(project, thread, events, now, allowed_names=None):
    ident = thread.get("id")
    show = _load_scenario_show(project, ident, now, allowed_names)
    cannot_say = set()
    deliverables, episodes = [], []
    if show is not None:
        for stage in (show.get("thread") or {}).get("stages") or []:
            for item in stage.get("items") or []:
                if not isinstance(item, dict):
                    continue
                kind = item.get("kind")
                # 題: 原稿は自分の原稿の先頭 60 字・出来事は窓口の書いた行・はてブは対象の題が台帳に無いので
                # 「ブックマーク」（第三者のコメント・user は出さない）。
                head = item.get("head") if kind in ("event", "draft") else (
                    "ブックマーク" if kind == "bookmark" else None)
                deliverables.append(_clean({
                    "stage": stage.get("name"), "kind": kind,
                    "status": _item_status(item) if kind == "draft" else (item.get("status") or "published"),
                    "head": head,
                    "at": item.get("posted_at") or item.get("publish_at") or item.get("at"),
                    "ref": item.get("file") or item.get("name"), "account": item.get("account"),
                    "medium": item.get("medium"), "delivery": item.get("delivery")}))
        episodes, unreadable = _episodes(show.get("items") or [])
        cannot_say |= unreadable
    else:
        cannot_say.add("scenario_show_unavailable")
    impressions, why_impressions, by_account = _impressions_outcome(episodes)
    blogs = [r for r in episodes if r["medium"] == "hatenablog" and r["delivery"]]
    bookmarks = [r["bookmarks_received"] for r in blogs if _int(r["bookmarks_received"])]
    outcomes = {
        "impressions": impressions, "impressions_by_account": by_account,
        "bookmarks_received": sum(bookmarks) if bookmarks else None,
        "blog_pv": None, "article_clicks": None,
        "per_episode": episodes if show is not None else None,
        "cannot_say": dict(NOT_COLLECTED)}
    if show is None:
        outcomes["cannot_say"].update(impressions="needs_scenario_show", bookmarks_received="needs_scenario_show",
                                      per_episode="needs_scenario_show")
    else:
        if why_impressions:
            outcomes["cannot_say"]["impressions"] = why_impressions
        if not bookmarks:
            outcomes["cannot_say"]["bookmarks_received"] = (
                "reactions_unreadable" if "reactions_unreadable" in cannot_say else "no_deliveries")
        else:
            # 1 つでも読めない配信があれば和は言えない（0 と混ぜない）。
            if len(bookmarks) != len(blogs):
                outcomes["bookmarks_received"] = None
                outcomes["cannot_say"]["bookmarks_received"] = "reactions_unreadable"
            outcomes["bookmarks_received_basis"] = (
                "events の bookmark_received と同じ出所（反応の台帳・利用者ごとに 1 回・全期間）を配信 id ごとに数えた和")
    row = _campaign_row(project, thread)
    row.update({
        "steps": row.get("stages"),
        "history": _clean(thread.get("events") or []),
        "deliverables": deliverables,
        "outcomes": outcomes,
        "deliveries": list((show or {}).get("deliveries") or []),
        "cannot_say": sorted(set(row.get("cannot_say") or []) | cannot_say)})
    row["actions"] = _campaign_actions(project, thread)
    return row


def _campaign_actions(project, thread):
    ident = thread.get("id")
    stage = (thread.get("stage") or {}).get("name")
    acts = []
    if stage == "article":
        acts.append(action("公開した", project, "campaign", ident,
                           "本記事を公開した。URL は私が伝える（無ければ書かない）。"
                           "糸のファイルに出来事を 1 行足して、次のステップを見せてください。"))
        acts.append(action("記事の構成案", project, "campaign", ident,
                           "ここまでのブックマークとブログから、本記事の構成案を作ってください。"))
    if stage in ("article", "announce"):
        acts.append(action("告知を先に下書き", project, "campaign", ident,
                           "SNS 告知の下書きを先に作ってください（出すのは記事の公開と承認のあと）。"))
    if stage == "collect":
        acts.append(action("リサーチを進める", project, "campaign", ident,
                           "足りないブックマークの候補を並べてください。付けるかは私が決めます。"))
    if stage == "diary":
        acts.append(action("仮説の下書き", project, "campaign", ident,
                           "ブックマークから仮説のブログの下書きを作ってください。承認は私がします。"))
    if stage == "warm":
        acts.append(action("返信を見る", project, "campaign", ident,
                           "告知への返信と、返していないものを見せてください。"))
    acts.append(action("成果を見る", project, "campaign", ident,
                       "このキャンペーンの投稿の成果（インプレッション・反応・クリック）を見せてください。"))
    if thread.get("state") != "closed":
        acts.append(action("一時停止", project, "campaign", ident,
                           "このキャンペーンを一時停止にしてください。理由は私が言います。糸のファイルを書き換えます。",
                           irreversible=True))
    return acts


def _series_detail(project, series, now=None, allowed_names=None):
    ident = series.get("id")
    show = _load_scenario_show(project, ident, now, allowed_names)
    cannot_say = set()
    items, episodes = [], []
    if show is not None:
        for item in show.get("items") or []:
            if isinstance(item, dict):
                items.append(_clean({"status": _item_status(item), "head": item.get("head"), "ref": item.get("file"),
                                     "at": item.get("posted_at") or item.get("publish_at"),
                                     "account": item.get("account"), "medium": item.get("medium"),
                                     "delivery": item.get("delivery"),
                                     "future_approved": item.get("future_approved")}))
        posted = [i for i in show.get("items") or [] if isinstance(i, dict) and i.get("posted")]
        episodes, unreadable = _episodes(posted)
        cannot_say |= unreadable
    else:
        cannot_say.add("scenario_show_unavailable")
    by_account = _by_account(episodes, "impressions")
    average, why_average = None, None
    if show is None:
        why_average = "needs_scenario_show"
    elif len(by_account) == 1:
        only = next(iter(by_account.values()))
        average = round(only["sum"] / only["n"], 1)
    elif by_account:
        why_average = "accounts_not_summed"
    else:
        why_average = "not_measured_yet" if episodes else "not_published_yet"
    outcomes = {"per_episode": episodes if show is not None else None,
                "average_impressions": average,
                "average_impressions_by_account": {
                    name: {"average": round(v["sum"] / v["n"], 1), "n": v["n"]} for name, v in by_account.items()},
                "cannot_say": {}}
    if show is None:
        outcomes["cannot_say"]["per_episode"] = "needs_scenario_show"
    if why_average:
        outcomes["cannot_say"]["average_impressions"] = why_average
    row = _series_row(project, series)
    row.update({
        "items": items,
        "slots_upcoming": _clean(((show or {}).get("series") or {}).get("slots_upcoming") or []),
        "outcomes": outcomes,
        "cannot_say": sorted(set(row.get("cannot_say") or []) | cannot_say)})
    slot = (series.get("next_slot") or {}).get("at")
    row["actions"] = [
        action("次の枠の下書き", project, "series", ident,
               f"次の枠{'（' + slot + '）' if slot else ''}の下書きを作ってください。承認は私がします。"),
        action("空き枠を埋める", project, "series", ident,
               "空き枠の分の下書きを素材の候補から作ってください。承認は私がします。"),
        action("反応の良い回の共通点", project, "series", ident,
               "このシリーズの回ごとの成果を見せて、反応の多かった回の共通点を並べてください。"),
        action("枠を変える", project, "series", ident,
               "配信枠を変えたいです。型のファイルを書き換えます。", irreversible=True)]
    return row


def _find(scenario, key, ident):
    for row in (scenario or {}).get(key) or []:
        if row.get("id") == ident:
            return row
    return None


def build_detail(project, level, ident, *, now=None, allowed_names=None) -> dict:
    now = now if now is not None else jst.now_jst()
    names = _accounts_of(project, allowed_names)
    if not names:
        raise DashboardError("target_unknown")
    cannot_say: set = set()
    events = _events(project, now, allowed_names, cannot_say)
    scenario_node, scenario = _scenario_node(project, now, allowed_names)
    payload = _head(level, now, project)
    payload.update({"accounts": names, "mediums": _mediums(names), "id": ident, "scenario": scenario_node,
                    "as_of": _as_of(now, names, events, scenario)})
    if scenario is None:
        payload.update({level: None, "cannot_say": sorted(cannot_say | {scenario_node["cannot_say"]}),
                        "limitations": _limitations()})
        return payload
    if level == "campaign":
        thread = _find(scenario, "threads", ident)
        if thread is None:
            raise DashboardError("campaign_unknown")
        payload["campaign"] = _campaign_detail(project, thread, events, now, allowed_names)
    else:
        series = _find(scenario, "series", ident)
        if series is None:
            raise DashboardError("series_unknown")
        payload["series"] = _series_detail(project, series, now, allowed_names)
    payload["actions"] = [action("メディアに戻る", project, None, None,
                                 f"このメディアのダッシュボードを開いてください（thth dashboard {project} --json）。")]
    payload.update({"cannot_say": sorted(cannot_say), "limitations": _limitations()})
    return payload


def _head(level, now, project):
    return {"schema": SCHEMA, "report_type": "dashboard", "level": level, "generated_at": jst.iso(now),
            "project": project}


def _limitations():
    return ["読むだけ。媒体の API は呼ばず、THTH の記録と段 1 の宣言の読みだけを束ねる",
            "要対応の並びは days の大きい順だけ（THTH は重みづけ・おすすめをしない）",
            "インプレッション・エンゲージメント率は口座をまたいで足さない",
            "他人の本文（はてブのコメント・返信の本文）は入れない。題・head・next は 60 字まで",
            "取れない数は null と cannot_say（0 と混ぜない）"]


def dumps(payload, **kwargs) -> str:
    """`--json` の文字列。`</` を `<\\/` に（型の `<script>` に貼っても途中で閉じない・JSON としては同じ値）。"""
    return json.dumps(payload, ensure_ascii=False, **kwargs).replace("</", "<\\/")


def build(project=None, level=None, ident=None, *, now=None, allowed_names=None, full=False) -> dict:
    """入口（CLI・遠くの道の両方から）。"""
    if project is None:
        if level is not None or ident is not None:
            raise DashboardError("invalid_level")
        return build_all(now=now, allowed_names=allowed_names)
    if level is None:
        if ident is not None:
            raise DashboardError("invalid_level")
        return build_project(project, now=now, allowed_names=allowed_names, full=full)
    if level not in ("campaign", "series") or not isinstance(ident, str) or not ident:
        raise DashboardError("invalid_level")
    return build_detail(project, level, ident, now=now, allowed_names=allowed_names)


# ------------------------------------------------------------------ CLI（端末の表示）

def _n(value):
    return "—" if value is None else str(value)


def render(payload, out=print) -> None:
    level = payload.get("level")
    out(f"ダッシュボード  {payload.get('project') or '全メディア'}（{level}）  {payload.get('generated_at')}")
    if level == "all":
        t = payload.get("totals") or {}
        out(f"  要対応 {_n(t.get('attention'))}・承認待ち {_n(t.get('pending_approval'))}・"
            f"予約済み 14 日 {_n(t.get('scheduled_14d'))}・公開済み 7 日 {_n(t.get('published_7d'))}")
        for card in payload.get("projects") or []:
            first = card.get("first_attention") or {}
            out(f"  {card['project']}  要対応 {card['kpi']['attention']}・公開 7 日 "
                f"{_n(card['kpi']['published_7d']['n'])}"
                + (f"  いちばん日数の多い要対応: {first.get('title')}" if first else ""))
    elif level == "project":
        k = payload.get("kpi") or {}
        out(f"  要対応 {k['attention']['n']}・承認待ち {k['pending_approval']['n']}・予約済み 14 日 "
            f"{k['scheduled_14d']['n']}・公開済み 7 日 {_n(k['published_7d']['n'])}"
            f"（前週 {_n(k['published_7d']['previous'])}）")
        for name, row in (k.get("impressions_7d") or {}).get("by_account", {}).items():
            out(f"  {name}: インプレッション {_n(row['impressions'])}（前週比 {_n(row['change_pct'])}%）・"
                f"ER {_n(row['engagement_rate_pct'])}%")
        for row in payload.get("attention") or []:
            out(f"  要対応 {KIND_JA.get(row['kind'], row['kind'])}"
                + (f"（{row['days']} 日）" if row.get("days") is not None else "") + f": {row.get('title') or ''}"
                + (f" → {row['next']}" if row.get("next") else ""))
        scenario = payload.get("scenario") or {}
        if not scenario.get("available"):
            out(f"  キャンペーン・シリーズ: —（{scenario.get('cannot_say')}）")
        for row in payload.get("campaigns") or []:
            out(f"  キャンペーン {row['id']} {row.get('title') or ''}  {row.get('state')}")
        for row in payload.get("series") or []:
            stock = row.get("stock") or {}
            out(f"  シリーズ {row['id']} {row.get('title') or ''}  ストック {_n(stock.get('approved_future'))}"
                f"／目安 {_n(stock.get('floor'))}")
    else:
        node = payload.get(level) or {}
        if not node:
            out(f"  —（{(payload.get('scenario') or {}).get('cannot_say')}）")
        else:
            out(f"  {node.get('id')} {node.get('title') or ''}")
            for act in node.get("actions") or []:
                out(f"  押すと: {act['prompt']}")
    if payload.get("cannot_say"):
        out("  読めなかったもの: " + "・".join(str(c) for c in payload["cannot_say"]))


def register(sub) -> None:
    p = sub.add_parser("dashboard",
                       help="会話の中のダッシュボードの中身（全メディア・メディア・キャンペーン／シリーズの 3 階層・読むだけ）")
    p.add_argument("account", metavar="project", nargs="?", default=None,
                   help="project 名（省略で全メディア）")
    p.add_argument("level", nargs="?", default=None, choices=("campaign", "series"),
                   help="campaign か series（id と一緒に）")
    p.add_argument("ident", metavar="id", nargs="?", default=None)
    p.add_argument("--json", action="store_true")
    p.add_argument("--full", action="store_true", help="メディアの階層で、キャンペーン・シリーズの詳細まで 1 回で")
    p.set_defaults(func=cmd)


def cmd(args) -> int:
    try:
        payload = build(args.account, args.level, args.ident, full=bool(args.full))
    except DashboardError as exc:
        print(str(exc), file=sys.stderr)
        if args.json:
            print(dumps({"schema": SCHEMA, "cannot_say": [str(exc)]}))
        return 2
    if args.json:
        print(dumps(payload, indent=2))
        return 0
    render(payload)
    return 0
