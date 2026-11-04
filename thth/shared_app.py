"""共有アプリの柵（設計「監査と審査の機械化と前向きな断り」§2 の (3)・§7 の段 E）。

2 つだけ持つ。数の既定は masaru 2026-11-04 の決定（変えない）。

1. **新しいテナントの慣らし**: 入ってから最初の `RAMP_DAYS`（14）日は、1 日の公開の上限
   （`post.daily`・`entry.daily`＝台帳の `daily_max_posts`）を**台帳の値の半分**にして安全装置が読む。
   台帳は書き換えない。半分は切り上げで 1 未満にしない（台帳が 0 なら 0 のまま・緩めない）。止めはしない。
   半分の数で断るとき（台帳の数なら通るとき）だけ、符丁 `ramp_daily_limit` で断り、文に「慣らしの期間・
   あと何日」を出す。台帳の数でも当たるなら今までどおり `daily_limit`。
   - **テナント**は招待で入った口座（台帳の `provenance.created_via == "invite"`）。運営者が `thth account add`
     で作る口座（持ち主の自分のレーン）には掛けない。
   - **起点**は台帳の `provenance.created_at`（招待を受け入れて口座の台帳ができた時刻・JST の日付）。起点が
     読めない口座と、`RAMP_SINCE` より前に入った口座には掛けない（既存の利用者を急に絞らない）。
2. **全テナントの合計**: 媒体ごとのアプリ全体の上限に対して、全口座の使った量の合計が `ALERT_RATIO`（8 割）に
   達したら、**管理者の** observe に 1 行出すだけ（止めない・外に送らない）。止めるのは今までどおり口座ごとの柵と
   媒体の断り。8 割の下では何も出さない。行に出すのは媒体・何の数・合計と上限と割合だけで、口座の名前・本文は
   出さない。上限の数が台帳に無い媒体は合計を出さず「上限が分からない」と言う（`thth media app-usage`）。

**読むだけ**（合計は `state/<口座>/sent/` と X の予算の台帳を読む）。数を新しく決めない: アプリ全体の数は
媒体の台帳（`thth/media_traits/`）の項目の文にあるものと、管理者が決めた予算の台帳だけを使う。
"""
from __future__ import annotations

import datetime

from . import accounts, jst

# ---------------------------------------------------------------- 慣らし

RAMP_DAYS = 14
# これより前に入った口座には慣らしを掛けない（入れた日・既存の利用者を急に絞らない）。
RAMP_SINCE = datetime.date(2026, 11, 4)
# 慣らしで半分にする行（台帳の `daily_max_posts`）。返信（`reply.daily`）・削除・反応には掛けない。
RAMP_RULES = ("post.daily", "entry.daily")
RAMP_CODE = "ramp_daily_limit"


def ramp_count(count):
    """慣らしの上限: 台帳の値の半分を切り上げ、1 未満にしない。台帳の値より緩めない（0 は 0）。"""
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        return count
    return min(count, max(1, -(-count // 2)))


def ramp_start(cfg) -> datetime.date | None:
    """慣らしの起点（JST の日付）。招待で入った口座で、台帳に入った時刻が読め、`RAMP_SINCE` 以後のときだけ。"""
    if not accounts.is_invite_account(cfg):
        return None
    created = jst.parse((cfg.get("provenance") or {}).get("created_at"))
    if created is None:
        return None
    start = jst.to_jst(created).date()
    return start if start >= RAMP_SINCE else None


def ramp_for(cfg, day) -> dict | None:
    """`day`（JST の日付）が慣らしの期間なら `{"started", "until", "days_left"}`、そうでなければ None。

    `until` は台帳の数に戻る日。`days_left` はその日を含めた残りの日数（入った日は 14・最後の日は 1）。"""
    start = ramp_start(cfg)
    if start is None or not isinstance(day, datetime.date):
        return None
    index = (day - start).days
    if not 0 <= index < RAMP_DAYS:
        return None
    return {"started": start.isoformat(), "until": (start + datetime.timedelta(days=RAMP_DAYS)).isoformat(),
            "days_left": RAMP_DAYS - index}


def apply_ramp(rules, cfg, day) -> dict:
    """行の写し（`limits_policy.rules_for` の結果）の 1 日の公開の上限を慣らしの数にする（台帳は触らない）。

    返すのは `{行の id: {"ledger_count", "count", "days_left", "until", "started"}}`（掛けた行だけ・無ければ {}）。"""
    ramp = ramp_for(cfg, day)
    if ramp is None:
        return {}
    out = {}
    for rule in rules:
        if rule.get("id") not in RAMP_RULES:
            continue
        ledger = rule.get("count")
        lowered = ramp_count(ledger)
        if lowered == ledger:
            continue  # 台帳が 0・1（半分にしても変わらない）
        rule["count"] = lowered
        out[rule["id"]] = {"ledger_count": ledger, "count": lowered, **ramp}
    return out


def mark_ramp(result, ramps) -> dict:
    """見立ての結論が慣らしの数で当たった（台帳の数なら通る）なら、符丁を `ramp_daily_limit` にして `ramp` を添える。"""
    if not ramps or not isinstance(result, dict) or result.get("outcome") != "refuse":
        return result
    ramp = ramps.get(result.get("rule"))
    used = result.get("used")
    if ramp is None or not isinstance(used, int) or used + 1 > ramp["ledger_count"]:
        return result
    result["code"] = RAMP_CODE
    result["ramp"] = dict(ramp)
    return result


def ramp_line(ramp) -> str:
    """断りの文の補い（「慣らしの期間・あと何日」）。読めなければ空。"""
    if not isinstance(ramp, dict) or not isinstance(ramp.get("days_left"), int):
        return ""
    until = ramp.get("until")
    back = ""
    try:
        day = datetime.date.fromisoformat(until)
        back = f"・{day.month}/{day.day} から台帳の {ramp['ledger_count']} 件に戻ります"
    except (TypeError, ValueError, KeyError):
        pass
    return f"（慣らしの期間・あと {ramp['days_left']} 日・今日の上限 {ramp.get('count')} 件{back}）"


# ---------------------------------------------------------------- 全テナントの合計

ALERT_RATIO = 0.8

# 媒体ごとのアプリ全体の上限（数の出所は媒体の台帳の項目の文か、管理者が決めた予算の台帳）。
# `trait`・`statement` は台帳の文との結び（`tests/test_shared_app.py` が台帳の文に数があることを縛る）。
APP_LIMITS = (
    {"media": "x", "what": "posts_24h", "label": "X の投稿（POST /2/tweets・24 時間）", "count": 10000,
     "window_hours": 24, "trait": "x.api_post_rate", "statement": "app 10,000/24h"},
    {"media": "x", "what": "read_budget_month", "label": "X の読み取りの月の予算（推定 USD・全口座で 1 本）",
     "count": None, "source": "budget_x"},
)
# アプリ全体の数が台帳に無い媒体（口座ごとの数しか無い・数に人数が掛かる・数が無い）。合計は出さない。
UNKNOWN = {
    "threads": "台帳の数は口座ごと（投稿 250/24h・返信 1,000/24h）だけ。アプリ全体の数は無い",
    "instagram": "台帳の数は口座ごと（公開 100/24h）だけ。アプリ全体の数は無い",
    "facebook": "台帳の数はページごと（4800 × 関わった人の数/24h）で、アプリ全体の数は無い",
    "bluesky": "台帳の数は口座ごと（PDS の点数）と IP ごとだけ。アプリ全体の数は無い",
    "mastodon": "台帳の数は口座ごと（サーバごと）だけ。アプリ全体の数は無い",
    "hatenablog": "台帳に回数の上限の記述が無い",
    "hatenabookmark": "台帳に回数の上限の数が無い",
}


def _x_accounts():
    """X の口座の名前と、読めなかった口座があったか。"""
    names, unreadable = [], False
    for name in accounts.list_account_names():
        try:
            cfg = accounts.load_account(name)
        except Exception:  # noqa: BLE001 — 読めない台帳は数えず「一部読めない」に
            unreadable = True
            continue
        if cfg.get("media") == "x":
            names.append(name)
    return names, unreadable


def _x_posts(now, hours):
    from . import guard
    start = now - datetime.timedelta(hours=hours)
    names, partial = _x_accounts()
    used = 0
    for name in names:
        try:
            rows = guard._action_rows(name)
        except Exception:  # noqa: BLE001
            partial = True
            continue
        used += sum(1 for _what, action, at, _blog, _key in rows
                    if action in ("post", "reply") and start < at <= now)
    return used, partial, len(names)


def _x_read_budget():
    from decimal import Decimal
    from . import budget_x
    report = budget_x.report()
    cap = report.get("cap_usd")
    if cap is None or Decimal(cap) <= 0:
        return None
    return (Decimal(report["spent_estimate_usd"]) + Decimal(report["held_usd"])), Decimal(cap)


def usage(*, now=None) -> list[dict]:
    """媒体ごとのアプリ全体の数（読むだけ）。行: `{"media", "what", "label", "used", "count", "ratio", "partial",
    "cannot_say"}`。上限が分からない媒体は `cannot_say: "app_limit_unknown"` と理由の文（`reason`）だけ。"""
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    rows = []
    for limit in APP_LIMITS:
        row = {"media": limit["media"], "what": limit["what"], "label": limit["label"], "used": None,
               "count": limit.get("count"), "ratio": None, "partial": False, "cannot_say": None}
        try:
            if limit["what"] == "posts_24h":
                used, partial, accounts_n = _x_posts(now, limit["window_hours"])
                row.update(used=used, partial=partial, accounts=accounts_n)
            elif limit["what"] == "read_budget_month":
                found = _x_read_budget()
                if found is None:
                    row["cannot_say"] = "budget_unset"
                else:
                    used, cap = found
                    row.update(used=float(used), count=float(cap))
        except Exception:  # noqa: BLE001 — 合計の読みで観測を落とさない
            row["cannot_say"] = "unavailable"
        if row["cannot_say"] is None and row["count"]:
            row["ratio"] = row["used"] / row["count"]
        rows.append(row)
    for media, reason in UNKNOWN.items():
        rows.append({"media": media, "what": None, "label": None, "used": None, "count": None, "ratio": None,
                     "partial": False, "cannot_say": "app_limit_unknown", "reason": reason})
    return rows


def _amount(row, value):
    if row["what"] == "read_budget_month":
        return f"{value:,.2f} USD"
    return f"{int(value):,}"


def alert_lines(*, now=None) -> list[str]:
    """合計が 8 割に達した数の行（管理者の observe の 1 行ずつ）。8 割の下・読めない・上限が分からない数は出さない。"""
    out = []
    for row in usage(now=now):
        ratio = row.get("ratio")
        if ratio is None or ratio < ALERT_RATIO:
            continue
        part = "・一部の口座を読めず、これより多いかもしれません" if row.get("partial") else ""
        out.append(f"共有アプリの合計: {row['label']} {_amount(row, row['used'])}／{_amount(row, row['count'])}"
                   f"（{int(ratio * 100)}%・8 割を超えました。止めてはいません{part}）")
    return out


def render(rows, out=print) -> None:
    """`thth media app-usage` の人向けの表（上限が分からない媒体も言う）。"""
    for row in rows:
        if row["cannot_say"] == "app_limit_unknown":
            out(f"{row['media']}: 上限が分からない（{row['reason']}）")
        elif row["cannot_say"] is not None:
            out(f"{row['media']} {row['label']}: 言えない: {row['cannot_say']}")
        else:
            part = "（一部の口座を読めず、これより多いかもしれません）" if row.get("partial") else ""
            out(f"{row['media']} {row['label']}: {_amount(row, row['used'])}／{_amount(row, row['count'])}"
                f"（{int(row['ratio'] * 100)}%）{part}")
