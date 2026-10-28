"""`thth scenario <project|account> [--json]`・`thth scenario show <project> <id> [--json]`・
`thth scenario lint <project> [--json]`（設計 5.0 §4.1・段 1）。**読むだけ。** 中身は `thth/scenario.py`。"""
from __future__ import annotations

import json
import sys

from . import scenario as scenario_mod

SUBCOMMANDS = ("show", "lint")


def _when(iso):
    return iso[:16].replace("T", " ") if iso else "—"


def _stage_cell(row) -> str:
    if row["name"] == "warm":
        if row.get("replies") is None:
            return "warm —（読めない）"
        return f"warm 返信 {row['replies']}・越えた {row['overdue']}"
    mark = " ✓" if row["satisfied"] else ""
    if row.get("done") is None:
        return f"{row['name']} —（読めない）"
    if "signal" in row and not row["satisfied"]:
        return f"{row['name']} 0（{row['signal']} 待ち）"
    extra = f"＋予定 {row['pending']}" if row.get("pending") else ""
    return f"{row['name']} {row['done']}/{row['need']}{extra}{mark}"


def render(payload, out=print) -> None:
    out(f"{payload['project']}  キャンペーン {len(payload['threads'])}・シリーズ {len(payload['series'])}"
        f"   {_when(payload['generated_at'])}")
    if not payload["threads"] and not payload["series"]:
        out("  宣言がありません（docs/sns/scenarios/<id>.md に thth_scenario: 1 で書く）")
    for row in payload["threads"]:
        stage = row["stage"]
        state = {"active": "", "closed": "  終わり",
                 "stalled": f"  停滞 {row['stalled']['days']} 日" if row["stalled"] and row["stalled"]["days"] is not None
                 else "  停滞"}[row["state"]]
        out(f"キャンペーン {row['id']}  {row['title'] or ''}   {stage['index']}/{stage['of']} {stage['name']}{state}")
        out("   " + "  ".join(_stage_cell(s) for s in row["stages"]))
        if row["next"]:
            out("   次: " + "・".join(n["what"] for n in row["next"]))
        if row["cannot_say"]:
            out("   読めなかったもの: " + "・".join(row["cannot_say"]))
    for row in payload["series"]:
        slots = "・".join(f"{s['day'] or '毎日'} {s['time']}" for s in row["slots"]) or "期日の型"
        stock = row["stock"]
        low = " ← 少ない" if stock["low"] else ""
        slot = row["next_slot"]
        slot_text = f"  次の枠 {_when(slot['at'])}{'' if slot['filled'] else '（空き）'}" if slot else ""
        out(f"シリーズ {row['id']}  {row['title'] or ''}  {slots}   ストック {stock['approved_future']} 本"
            f"（目安 {stock['floor']}）{low}{slot_text}")
        if row["material"] is not None:
            m = row["material"]
            out(f"   素材 あと {m['remaining']} 回（{m['written_at'] or '日付なし'} に窓口が書いた）")
        if row["deadline"] is not None:
            d = row["deadline"]
            out(f"   期限 48 時間以内の未承認 {d['expiring_48h']} 本・期限を過ぎた {d['passed']} 本")
        if row["next"]:
            out("   次: " + "・".join(n["what"] for n in row["next"]))
    untied = payload["untied"]
    out(f"未分類の原稿: SNS {untied['drafts']}・はてなブログ {untied['deliveries']}（thth scenario lint で一覧）")
    if payload["cannot_say"]:
        out("読めなかったもの: " + "・".join(payload["cannot_say"]))


def render_show(payload, out=print) -> None:
    key = payload["kind"]
    render({"project": payload["project"], "generated_at": payload["generated_at"],
            "threads": [payload[key]] if key == "thread" else [],
            "series": [payload[key]] if key == "series" else [],
            "untied": {"drafts": 0, "deliveries": 0}, "cannot_say": payload["cannot_say"]},
           out=lambda line: None if line.startswith("未分類") else out(line))
    body = payload[key]
    if key == "thread" and body["events"]:
        out("出来事:")
        for e in body["events"]:
            out(f"   {e['at']}  {e['name']}  {e['head']}")
    out(f"原稿 {len(payload['items'])} 本:")
    for row in payload["items"]:
        state = row["delivery_state"] or ("posted" if row["posted"] else row["status"])
        out(f"   {_when(row['posted_at'] or row['publish_at'])}  {row['account']}  {state}  {row['file']}")


def render_lint(payload, out=print) -> None:
    out(f"{payload['project']}  宣言 {payload['declarations']}・指摘 {len(payload['problems'])}・"
        f"未分類 {len(payload['untied'])}")
    for p in payload["problems"]:
        out(f"  {p['code']}  {p['file']}  {p['id'] or ''}  {p['detail'] or ''}")
    for row in payload["untied"]:
        out(f"  未分類  {row['file']}  {row['account']}  {row['status']}")
    if payload["cannot_say"]:
        out("読めなかったもの: " + "・".join(payload["cannot_say"]))


def register(sub) -> None:
    p = sub.add_parser(
        "scenario",
        help="キャンペーン（糸）とシリーズ（型）の現在地・次の一手・停滞（読むだけ）。"
             "show <project> <id> で 1 本の詳細、lint <project> で宣言の誤りと未分類の原稿")
    p.add_argument("words", nargs="+", metavar="target",
                   help="<project|account>／show <project> <id>／lint <project>")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd)


def _dumps(payload, **kwargs) -> str:
    """`--json` の文字列。`</` は `<\\/`（型の `<script>` に貼っても途中で閉じない・JSON の値は同じ）。"""
    return json.dumps(payload, ensure_ascii=False, **kwargs).replace("</", "<\\/")


def _fail(code, as_json) -> int:
    print(code, file=sys.stderr)
    if as_json:
        print(_dumps({"schema": scenario_mod.SCHEMA, "cannot_say": [code]}))
    return 2


def cmd(args) -> int:
    words = list(args.words)
    sub = words[0] if words[0] in SUBCOMMANDS and len(words) > 1 else None
    try:
        if sub == "show":
            if len(words) != 3:
                return _fail("usage: thth scenario show <project> <id>", args.json)
            payload, renderer = scenario_mod.show(words[1], words[2]), render_show
        elif sub == "lint":
            if len(words) != 2:
                return _fail("usage: thth scenario lint <project>", args.json)
            payload, renderer = scenario_mod.lint(words[1]), render_lint
        else:
            if len(words) != 1:
                return _fail("usage: thth scenario <project|account>", args.json)
            payload, renderer = scenario_mod.build(words[0]), render
    except ValueError as exc:
        return _fail(str(exc), args.json)
    if args.json:
        print(_dumps(payload, indent=2))
    else:
        renderer(payload)
    return 0
