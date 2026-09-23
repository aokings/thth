"""週の表（設計 3.7.0 §A3・`thth analytics-report <account> --weekly-goals [--weeks 6]`）。

**観察の表**。週ごとに、目的ごとの投稿の本数と followers の増分を並べるだけで、
「目的の配分が followers を動かした」とは言わない（因果ではない・推奨もしない）。
読むだけ。台帳を書かない。

- 週は**月曜始まり（JST）**。いまの週も途中まで出す（`partial: true`）。
- 本数: 実測の台帳（`measured`）の投稿を `posted_at`（JST）の週に数える。目的は
  公開の時点の記録（`goals.recorded_goals()`）——記録に goal の欄が無い投稿は
  `unrecorded`（3.6.0 と同じ）。
- followers の増分: 週初の時点（前週の最終日＝日曜の `followers_count`）と週末
  （その週の最終日、途中の週は閉じた最後の日＝昨日）の差。account 日次は閉じた
  日の終わりの数なので、前週の日曜の値が「週初の時点」。**どちらかが欠ければ
  null と理由**（近い日で埋めない）。
"""
from __future__ import annotations

import datetime
import json
import sys

from . import accounts, jst

DEFAULT_WEEKS = 6
MAX_WEEKS = 52
WEEK_BASIS = "月曜始まり（JST）。本数は posted_at の週・followers は前週の日曜の値と週の最終日の値の差"
NOTE = "観察の表（因果ではない）"


def _daily_followers(account_daily) -> dict:
    out = {}
    for row in account_daily or []:
        if not isinstance(row, dict) or not isinstance(row.get("date"), str):
            continue
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        value = metrics.get("followers_count")
        if isinstance(value, int) and not isinstance(value, bool):
            out[row["date"]] = value
    return out


def answer(account_name, *, weeks=DEFAULT_WEEKS, now=None) -> dict:
    """週の表の payload（CLI の Markdown と JSON が同じものから作る）。"""
    from . import after_cli, goals, measured
    if not isinstance(account_name, str) or not account_name.strip():
        raise after_cli.AfterError("account は空でない文字列です")
    if type(weeks) is not int or not 1 <= weeks <= MAX_WEEKS:
        raise after_cli.AfterError(f"weeks は 1〜{MAX_WEEKS} の整数です")
    now = jst.to_jst(now if now is not None else jst.now_jst()).replace(microsecond=0)
    accounts.load_account(account_name)          # 無い・読めない台帳は AccountError
    loaded = measured.load(account_name)
    recorded = goals.recorded_goals(account_name)
    followers = _daily_followers(loaded.get("account_daily"))
    today = now.date()
    this_monday = today - datetime.timedelta(days=today.weekday())
    counts = {}
    for post in loaded["posts"]:
        at = jst.parse(post.get("posted_at"))
        if at is None:
            continue
        day = jst.to_jst(at).date()
        monday = day - datetime.timedelta(days=day.weekday())
        label = goals.goal_for(recorded, post["post_id"])
        counts.setdefault(monday, {}).setdefault(label, 0)
        counts[monday][label] += 1
    rows = []
    for back in range(weeks - 1, -1, -1):
        monday = this_monday - datetime.timedelta(weeks=back)
        sunday = monday + datetime.timedelta(days=6)
        partial = sunday >= today
        # 閉じた日だけが記録される（採取は前日ぶん）。途中の週の終わりは昨日。
        end_day = min(sunday, today - datetime.timedelta(days=1))
        start_day = monday - datetime.timedelta(days=1)
        start = followers.get(start_day.isoformat())
        end = followers.get(end_day.isoformat()) if end_day >= monday else None
        reason = None
        if end_day < monday:
            reason = "week_not_closed"
        elif start is None:
            reason = "followers_missing_start"
        elif end is None:
            reason = "followers_missing_end"
        posts = {label: 0 for label in goals.LAYERS}
        for label, n in (counts.get(monday) or {}).items():
            posts[label] = posts.get(label, 0) + n
        rows.append({"week_start": monday.isoformat(), "week_end": sunday.isoformat(),
                     "partial": partial, "posts": posts, "n_posts": sum(posts.values()),
                     "followers": {"start": start, "start_date": start_day.isoformat(),
                                   "end": end, "end_date": end_day.isoformat(),
                                   "delta": end - start if reason is None else None,
                                   "reason": reason}})
    return {"schema_version": 1, "report_type": "weekly_goals", "account": account_name,
            "generated_at": jst.iso(now), "weeks_requested": weeks,
            "week_basis": WEEK_BASIS, "goal_source": "recorded_at_publish",
            "observational_difference": True, "causal": False, "note": NOTE,
            "population": "measured_posts",
            "weeks": rows,
            "limitations": ["目的ごとの本数と followers の増分を並べるだけ。因果・推奨は出さない",
                            "本数は実測の台帳にある投稿（根と返信を含む）。SNS 上の全投稿ではない",
                            "followers の欠測は null（近い日で埋めない）"]}


def render_markdown(payload) -> str:
    from .analytics_report import _markdown_text
    from . import goals
    lines = [f"# 週の表 {_markdown_text(payload['account'])}", "",
             f"{payload['note']}。{payload['week_basis']}。", ""]
    labels = list(goals.LAYERS)
    lines.append("| 週の始まり | " + " | ".join(labels) + " | 計 | followers の増分 |")
    lines.append("|" + "---|" * (len(labels) + 3))
    for row in payload["weeks"]:
        f = row["followers"]
        delta = (f"{f['delta']:+d}（{f['start_date']} {f['start']} → {f['end_date']} {f['end']}）"
                 if f["delta"] is not None else f"null（{f['reason']}）")
        mark = "（途中）" if row["partial"] else ""
        lines.append(f"| {row['week_start']}{mark} | "
                     + " | ".join(str(row["posts"].get(label, 0)) for label in labels)
                     + f" | {row['n_posts']} | {delta} |")
    lines += ["", "## 制約", ""] + ["- " + item for item in payload["limitations"]]
    return "\n".join(lines) + "\n"


def cmd(args) -> int:
    from . import after_cli
    if args.compare_previous or args.by or args.project:
        print("--weekly-goals は --compare-previous・--by・--project と一緒には使えません"
              "（1 account の週の表です）", file=sys.stderr)
        return 2
    if not args.account:
        print("--weekly-goals には account が要ります", file=sys.stderr)
        return 2
    try:
        payload = answer(args.account, weeks=args.weeks)
    except accounts.AccountError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (after_cli.AfterError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        sys.stdout.write(render_markdown(payload))
    return 0
