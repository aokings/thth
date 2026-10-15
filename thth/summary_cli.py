"""`thth summary <account|project> [--json]`——口座の数の要約（4.0 の柱 1・計画 §3.3）。

**読むだけ。** 媒体の API は呼ばない。何も書かない。`schema: "thth.summary/1"`。

口座ごとに（`by_account.<口座>`）:

- `published`: 直近 24h・7d に公開した本数（`sent/` の `sent_at`・THTH を通したものだけ）と取り下げた本数。
- `reactions`: 直近 24h・7d に公開した投稿の、いちばん新しい実測の和（指標ごとの `sum` と、その
  指標を持っていた投稿の数 `n`）。`reactions_total` は views を除いた和（likes・replies・
  reposts・quotes・shares のうちあるもの）。実測が 1 つも無ければ null（0 と混ぜない）。
- `unanswered_replies`: 返していない返信の数（`thth unanswered` と同じ数え方・窓は台帳の
  `collect_days`）。
- `followers`: 口座日次の最新の `followers_count` と、その前日の値との差（前日が無ければ null）。
- `stopped`: 止まっているか（安全装置・`publish_hold`・inflight・X の予算）と、その符丁。

升目ごとに `try` で囲む。読めなかった升目は null にし、`cannot_say` に `<升目>_unreadable`。
合計や順位は作らない（口座をまたいで足さない）。
"""
from __future__ import annotations

import datetime
import json
import sys

from . import accounts as accounts_mod
from . import jst

SUMMARY_SCHEMA = "thth.summary/1"
WINDOWS = (("24h", datetime.timedelta(hours=24)), ("7d", datetime.timedelta(days=7)))
REACTION_KEYS = ("views", "likes", "replies", "reposts", "quotes", "shares")
# `reactions_total` に足す指標（views は「見られた」で反応ではないので足さない）。
TOTAL_KEYS = ("likes", "replies", "reposts", "quotes", "shares")


class SummaryError(ValueError):
    """問いが受け取れない（対象が無い）。"""


def _int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _published(name, now):
    from . import sent as sent_mod
    rows = sent_mod.records(accounts_mod.state_dir_for(name))
    out = {}
    for label, span in WINDOWS:
        floor = now - span
        sent_at = [jst.parse(row.get("sent_at")) for row in rows]
        retracted = [jst.parse(row.get("retracted_at")) for row in rows]
        out[label] = {"published": sum(1 for at in sent_at if at is not None and floor <= at <= now),
                      "retracted": sum(1 for at in retracted if at is not None and floor <= at <= now)}
    return {**out, "basis": "sent_at（THTH を通して出したもの）"}


def _reactions(name, now):
    from . import measured
    posts = measured.load(name)["posts"]
    out = {}
    for label, span in WINDOWS:
        floor = now - span
        chosen = [post for post in posts
                  if (at := jst.parse(post.get("posted_at"))) is not None and floor <= at <= now]
        latest = []
        for post in chosen:
            rows = [row for row in post.get("rows") or [] if jst.parse(row.get("collected_at"))]
            if rows:
                rows.sort(key=lambda row: jst.parse(row["collected_at"]))
                latest.append(rows[-1].get("metrics") or {})
        sums = {}
        for key in REACTION_KEYS:
            values = [metrics[key] for metrics in latest if _int(metrics.get(key))]
            if values:
                sums[key] = {"sum": sum(values), "n": len(values)}
        total = [sums[key]["sum"] for key in TOTAL_KEYS if key in sums]
        out[label] = {"posts": len(chosen), "measured": len(latest), "sums": sums,
                      "reactions_total": sum(total) if total else None}
    return {**out, "basis": "posted_at の窓に出した投稿の、いちばん新しい実測の和"}


def _unanswered(name, cfg, now, allowed_names):
    from . import morning, unanswered
    days, _ = morning._unanswered_days(cfg)
    result = unanswered.answer(name, since=f"{days}d", now=now,
                               **({} if allowed_names is None else {"allowed_names": tuple(allowed_names)}))
    return {"n": result["n_total"], "window_days": days,
            "cannot_say": sorted(result.get("cannot_say") or [])}


def _followers(name):
    from . import measured
    daily = {}
    for row in measured.load(name).get("account_daily") or []:
        value = (row.get("metrics") or {}).get("followers_count") if isinstance(row, dict) else None
        if isinstance(row, dict) and isinstance(row.get("date"), str) and _int(value):
            daily[row["date"]] = value
    if not daily:
        return {"latest": None, "date": None, "previous": None, "previous_date": None, "delta": None}
    date = max(daily)
    before = (datetime.date.fromisoformat(date) - datetime.timedelta(days=1)).isoformat()
    previous = daily.get(before)
    return {"latest": daily[date], "date": date, "previous": previous, "previous_date": before,
            "delta": daily[date] - previous if previous is not None else None}


def _stopped(name, cfg):
    from . import guard, inflight as inflight_mod, publish_hold
    try:
        row = guard.stopped(name)
    except Exception:  # noqa: BLE001 — 読めない印は止まっている側
        row = {"reason": "guard_state_unreadable"}
    guard_reason = (row or {}).get("reason")
    try:
        row = publish_hold.held(name)
    except Exception:  # noqa: BLE001
        row = {"reason": publish_hold.UNREADABLE}
    hold_reason = (row or {}).get("reason")
    inflight = inflight_mod.read(accounts_mod.state_dir_for(name)) is not None
    budget = None
    if cfg.get("media") == "x":
        from . import budget_x, budget_x_posts
        budget = budget_x.report().get("read_refusal") or budget_x_posts.report().get("post_refusal")
    return {"guard": guard_reason, "publish_hold": hold_reason, "inflight": inflight,
            "budget": budget, "any": bool(guard_reason or hold_reason or inflight or budget)}


def _cell(name, call, cannot_say):
    try:
        return call()
    except Exception:  # noqa: BLE001 — 1 升目で全部を落とさない
        cannot_say.add(f"{name}_unreadable")
        return None


def build(target, *, now=None, allowed_names=None) -> dict:
    """口座ごとの数の要約。**読むだけ。** `allowed_names` が渡されたら、その外は読まない。"""
    from . import morning
    now = now if now is not None else jst.now_jst()
    names = morning.targets(target)
    kind = "account" if names == [target] else "project"
    if allowed_names is not None:
        names = [name for name in names if name in allowed_names]
    if not names:
        raise SummaryError("target_unknown")
    by_account, cannot_say = {}, set()
    for name in names:
        try:
            cfg = accounts_mod.load_account(name)
        except (accounts_mod.AccountError, ValueError, TypeError):
            cannot_say.add("account_unreadable")
            continue
        by_account[name] = {
            "medium": cfg.get("media"),
            "published": _cell("published", lambda: _published(name, now), cannot_say),
            "reactions": _cell("reactions", lambda: _reactions(name, now), cannot_say),
            "unanswered_replies": _cell("unanswered", lambda: _unanswered(name, cfg, now, allowed_names),
                                        cannot_say),
            "followers": _cell("followers", lambda: _followers(name), cannot_say),
            "stopped": _cell("stopped", lambda: _stopped(name, cfg), cannot_say),
        }
    return {"schema": SUMMARY_SCHEMA, "report_type": "summary", "generated_at": jst.iso(now),
            "target": target, "target_kind": kind, "accounts": list(by_account),
            "by_account": by_account, "cannot_say": sorted(cannot_say),
            "limitations": [
                "読むだけ。媒体の API は呼ばず、THTH の記録だけから数える",
                "公開の本数は THTH を通したもの（sent/）だけ。手で出した投稿は入らない",
                "口座をまたいで足さない・順位を作らない",
                "取れていない数は null（0 と混ぜない）"]}


# ------------------------------------------------------------------ CLI

def _n(value):
    return "—" if value is None else str(value)


def render(payload, out=print) -> None:
    out(f"数の要約  {payload['target']}（{payload['target_kind']}）  {payload['generated_at']}")
    for name, row in payload["by_account"].items():
        out(f"{name}（{row['medium']}）")
        pub, rea = row.get("published") or {}, row.get("reactions") or {}
        for label, _ in WINDOWS:
            p, r = pub.get(label) or {}, rea.get(label) or {}
            out(f"  {label}: 公開 {_n(p.get('published'))} 本・取り下げ {_n(p.get('retracted'))} 本・"
                f"反応の和 {_n(r.get('reactions_total'))}（実測 {_n(r.get('measured'))}/{_n(r.get('posts'))} 本）")
        un = row.get("unanswered_replies") or {}
        fo = row.get("followers") or {}
        out(f"  返していない返信: {_n(un.get('n'))}  フォロワー: {_n(fo.get('latest'))}"
            f"（{_n(fo.get('date'))}・前日差 {_n(fo.get('delta'))}）")
        st = row.get("stopped") or {}
        reasons = [str(v) for v in (st.get("guard"), st.get("publish_hold"), st.get("budget")) if v]
        if st.get("inflight"):
            reasons.append("inflight")
        out("  止まり: " + ("・".join(reasons) if reasons else "なし"))
    if payload["cannot_say"]:
        out("読めなかったもの: " + "・".join(payload["cannot_say"]))


def register(sub) -> None:
    p = sub.add_parser("summary", help="口座の数の要約（公開数・反応・返していない返信・フォロワー・止まり・読むだけ）")
    p.add_argument("account", metavar="target", help="account 名か project 名")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd)


def cmd(args) -> int:
    try:
        payload = build(args.account)
    except SummaryError as exc:
        print(str(exc), file=sys.stderr)
        if args.json:
            print(json.dumps({"schema": SUMMARY_SCHEMA, "cannot_say": [str(exc)]}, ensure_ascii=False))
        return 2
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    render(payload)
    return 0
