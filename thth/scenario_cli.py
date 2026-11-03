"""`thth scenario <project|account> [--json]`・`thth scenario show <project> <id> [--json]`・
`thth scenario lint <project> [--json]`（設計 5.0 §4.1・段 1）。**読むだけ。** 中身は `thth/scenario.py`。

5.7.0（設計 効果測定と連動 §3 段 1・段 3）:
- 出力の 1 行目は skill `thth-flow` の 1 行（`--json` は `skill` の欄）。`thth scenario skill [--print]` で場所と全文。
- `thth scenario site-import <project> --file <json>`: hub の `hub.site_visits/1` を受け取り、
  `state/<project>/site_visits/` に控える（中身は `thth/site_visits.py`）。書くのはこの控えだけ。
"""
from __future__ import annotations

import json
import sys

from . import scenario as scenario_mod

SUBCOMMANDS = ("show", "lint", "site-import")


def _when(iso):
    return iso[:16].replace("T", " ") if iso else "—"


def _stage_name(row) -> str:
    """段の名と日本語の名（`promote（告知）`）。日本語の名の無い段は名だけ。"""
    label = row.get("label")
    return f"{row['name']}（{label}）" if label and not label.endswith("段目") else row["name"]


def _stage_cell(row) -> str:
    name = _stage_name(row)
    if row["name"] == "warm":
        if row.get("replies") is None:
            return f"{name} —（読めない）"
        return f"{name} 返信 {row['replies']}・越えた {row['overdue']}"
    mark = " ✓" if row["satisfied"] else ""
    if row.get("done") is None:
        return f"{name} —（読めない）"
    if "signal" in row and not row["satisfied"]:
        return f"{name} 0（{row['signal']} 待ち）"
    extra = f"＋予定 {row['pending']}" if row.get("pending") and row.get("medium") != "ads" else (
        f"＋止めたまま {row['pending']}" if row.get("pending") else "")
    own = f"・自分の記事 {row['by_target']['own']}" if row.get("target") is None and (row.get("by_target") or {}).get("own") else ""
    return f"{name} {row['done']}/{row['need']}{extra}{own}{mark}"


def skill_line(payload) -> str:
    return "skill: " + ((payload.get("skill") or {}).get("text") or "thth-flow")


def visits_lines(block) -> list:
    """「本記事への訪問（GA4・hub 経由・as_of）」の数行（show と dashboard campaign で同じ）。"""
    if not block:
        return []
    as_of = _when(block.get("as_of"))
    head = f"{block.get('label') or '本記事への訪問'}・{as_of} 時点"
    if block.get("sessions") is not None:
        engaged = block.get("engaged_sessions")
        lines = [f"{head}: {block['sessions']}" + (f"（エンゲージ {engaged}）" if engaged is not None else "")]
    else:
        lines = [f"{head}: —（{block.get('cannot_say')}）"]
    for site in block.get("sites") or []:
        window = site.get("window") or {}
        span = f"{window.get('from')}〜{window.get('to')}"
        if site.get("cannot_say") == "site_visits_unavailable":
            lines.append(f"   {site['site']} {span}: —（取れなかった・{site.get('unknown_reason')}）")
            continue
        media = "・".join(f"{k} {v}" for k, v in sorted((site.get("by_medium") or {}).items()))
        count = site.get("sessions")
        lines.append(f"   {site['site']} {span}: {'—' if count is None else count}"
                     + (f"（{media}）" if media else "") + (f"（{site['cannot_say']}）" if site.get("cannot_say") else ""))
        newer = site.get("newer_unavailable")
        if newer:
            nw = newer.get("window") or {}
            lines.append(f"      新しい窓 {nw.get('from')}〜{nw.get('to')} は取れなかった（{newer.get('unknown_reason')}）。"
                         "上は前の窓の数")
        for row in site.get("campaigns") or []:
            lines.append(f"      {row.get('file')}  {row.get('source') or '?'}/{row.get('medium') or '?'}  "
                         f"{'—' if row.get('sessions') is None else row['sessions']}")
        untied = site.get("untied_campaigns")
        if untied:
            lines.append(f"      結ばれない campaign {untied['campaigns']}（訪問 "
                         f"{'—' if untied['sessions'] is None else untied['sessions']}）")
    return lines


def render(payload, out=print) -> None:
    if payload.get("skill"):
        out(skill_line(payload))
    out(f"{payload['project']}  キャンペーン {len(payload['threads'])}・シリーズ {len(payload['series'])}"
        f"   {_when(payload['generated_at'])}")
    if not payload["threads"] and not payload["series"]:
        out("  宣言がありません（docs/sns/scenarios/<id>.md に thth_scenario: 1 で書く）")
    for row in payload["threads"]:
        stage = row["stage"]
        state = {"active": "", "closed": "  終わり",
                 "stalled": f"  停滞 {row['stalled']['days']} 日" if row["stalled"] and row["stalled"]["days"] is not None
                 else "  停滞"}[row["state"]]
        current = next((s for s in row["stages"] if s["name"] == stage["name"]), {"name": stage["name"]})
        out(f"キャンペーン {row['id']}  {row['title'] or ''}   {stage['index']}/{stage['of']} "
            f"{_stage_name(current) if stage['name'] else ''}{state}")
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
    render({"project": payload["project"], "generated_at": payload["generated_at"], "skill": payload.get("skill"),
            "threads": [payload[key]] if key == "thread" else [],
            "series": [payload[key]] if key == "series" else [],
            "untied": {"drafts": 0, "deliveries": 0}, "cannot_say": payload["cannot_say"]},
           out=lambda line: None if line.startswith("未分類") else out(line))
    body = payload[key]
    if key == "thread" and body["events"]:
        out("出来事:")
        for e in body["events"]:
            out(f"   {e['at']}  {e['name']}  {e['head']}")
    for line in visits_lines(payload.get("site_visits")):
        out(line)
    out(f"原稿 {len(payload['items'])} 本:")
    for row in payload["items"]:
        state = row["delivery_state"] or ("posted" if row["posted"] else row["status"])
        out(f"   {_when(row['posted_at'] or row['publish_at'])}  {row['account']}  {state}  {row['file']}")
    if key == "thread":
        for stage in body["stages"]:
            for item in stage.get("items") or []:
                if item.get("kind") == "ad":
                    budget = item.get("budget_minor")
                    out(f"   広告 {item['campaign_id']}  {item['status']}  {item.get('file') or '—'}  予算 "
                        f"{'—' if budget is None else budget} {item.get('currency') or ''}（使った額は thth ads insights）")


def render_import(payload, out=print) -> None:
    window = payload["window"]
    out(skill_line({"skill": payload.get("skill")}))
    out(f"{payload['project']}  {payload['site']} {window['from']}〜{window['to']} を控えた"
        f"（{'上書き' if payload['replaced'] else '新規'}・{payload['stored']}）")
    if payload["rows_total"] is None:
        out(f"  取れなかった窓（{payload['unknown_reason']}）。数は —（0 と混ぜない）")
        return
    tied, untied = payload["tied"], payload["untied_campaigns"]
    out(f"  行 {payload['rows_total']}・糸に結んだ campaign {tied['campaigns']}（糸 {tied['threads']}）・"
        f"結ばれない campaign {untied['campaigns']}（訪問 {'—' if untied['sessions'] is None else untied['sessions']}）")
    reasons = "・".join(f"{k} {v}" for k, v in untied["by_reason"].items() if v)
    if reasons:
        out(f"  結ばれない理由: {reasons}")


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
                   help="<project|account>／show <project> <id>／lint <project>／"
                        "site-import <project> --file <json>／skill [--print]")
    p.add_argument("--file", default=None, help="site-import: hub の hub.site_visits/1 の JSON（1 サイト 1 ファイル）")
    p.add_argument("--print", dest="print_skill", action="store_true", help="skill: skill thth-flow の全文を出す")
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


def _skill(args) -> int:
    from . import flow_skill
    if args.print_skill:
        text = flow_skill.read_text()
        if text is None:
            return _fail("skill_missing", args.json)
        if args.json:
            print(_dumps(dict(flow_skill.skill_ref(), content=text)))
        else:
            print(text, end="" if text.endswith("\n") else "\n")
        return 0
    ref = flow_skill.skill_ref()
    if args.json:
        print(_dumps(ref))
    else:
        print(ref["text"])
        print(flow_skill.skill_path() or ref["path"])
    return 0


def _site_import(words, args) -> int:
    from . import flow_skill, site_visits
    if len(words) != 2 or not args.file:
        return _fail("usage: thth scenario site-import <project> --file <json>", args.json)
    try:
        payload = site_visits.import_file(words[1], args.file)
    except site_visits.SiteVisitsError as exc:
        print(f"{exc.code}" + (f": {exc.detail}" if exc.detail else ""), file=sys.stderr)
        if args.json:
            print(_dumps({"schema": site_visits.IMPORT_SCHEMA, "ok": False, "code": exc.code,
                          "cannot_say": [exc.code]}))
        return 2
    payload["skill"] = flow_skill.skill_ref()
    if args.json:
        print(_dumps(payload, indent=2))
    else:
        render_import(payload)
    return 0


def cmd(args) -> int:
    words = list(args.words)
    if words == ["skill"]:
        return _skill(args)
    if words[0] == "site-import":
        return _site_import(words, args)
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
