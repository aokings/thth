"""`thth admin appreview`（審査の台本と提出の前の点検・口の中身は `thth/appreview.py`）。

- `script`: 権限・機能 → THTH の口 → 審査員が打つ命令 → 録画の区間、の表（計画 §1 の形）。`--tsv` は
  `tools/appreview_rec.sh --segments` が読む区間の並び。
- `check`: 30 日以内の成功の呼び出し・口・privacy の語を見て、足りないものを赤で出す。全部通れば黙って rc 0。

**CLI だけ・MCP には出さない**（管理者の口）。読むだけ・網に出ない。
"""
from __future__ import annotations

import json
import sys

from . import appreview, refusal
from . import permission_watch as watch


def _print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _cell(values) -> str:
    return "<br>".join(f"`{value}`" for value in values) if values else "—"


def _render_script(view) -> None:
    rows = view["items"]
    for media in (*appreview.MEDIA_ORDER, "feature"):
        group = [row for row in rows if row["media"] == media]
        if not group:
            continue
        print(f"### {appreview.MEDIA_LABEL[media]}\n")
        print("| 名前 | 種別 | THTH の口 | 審査員が打つ命令 | 録画の区間 |")
        print("|---|---|---|---|---|")
        for row in group:
            kind = {"read": "読む", "write": "書く", "feature": "機能"}.get(row["kind"], "口なし（計画 §1.10）")
            doors = _cell(row["doors"]) if row["doors"] else "**口が無い**"
            segs = "、".join(row["segments"]) if row["segments"] else ("要らない" if not row["recording"] else "—")
            if row["real_first"]:
                segs += "（本物で 1 回打ってから）"
            print(f"| `{row['label']}` | {kind} | {doors} | {_cell(row['commands'])} | {segs} |")
        print()
    print("### 録画の区間（撮る順・bash tools/appreview_rec.sh --segments）\n")
    for segment in view["segments"]:
        mark = f"  ※本物で 1 回打ってから: {segment['real_first']}" if segment["real_first"] else ""
        items = "・".join(segment["items"]) or "審査員の流れ"
        print(f"- {segment['id']}（{items}）: {segment['banner']}{mark}")
    counts = view["counts"]
    print(f"\n権限 {counts['permissions']}・機能 {counts['features']}・区間 {counts['segments']}"
          f"（本物で 1 回打ってから {counts['real_first']}）・口が無い {counts['no_door']}")


def cmd_script(args) -> int:
    if args.tsv:
        sys.stdout.write(appreview.tsv())
        return 0
    view = appreview.script_view()
    if args.json:
        _print_json(view)
    else:
        _render_script(view)
    return 0


def _explain(problem) -> dict:
    found = refusal.parts(problem["code"]) or {}
    return {**problem, "why": found.get("why"), "fix": found.get("fix"), "better": found.get("better")}


def cmd_check(args) -> int:
    result = appreview.evaluate(watch.build(), appreview.privacy_text(args.privacy))
    result["problems"] = [_explain(problem) for problem in result["problems"]]
    if args.json:
        _print_json(result)
    elif not result["ok"]:
        for problem in result["problems"]:
            unrecorded = set(problem.get("unrecorded") or ())   # 打っても記録に残らない権限には印を付ける
            names = [name + "（記録に残らない）" if name in unrecorded else name for name in problem["names"]]
            hit = f" {len(problem['names'])} 件（" + "・".join(names) + "）" if names else ""
            print(f"{problem['code']}: 赤{hit}", file=sys.stderr)
            for prefix, key in ((refusal.WHY, "why"), (refusal.FIX, "fix"), (refusal.BETTER, "better")):
                if problem[key]:
                    print(f"  {prefix}{problem[key]}", file=sys.stderr)
    return 0 if result["ok"] else 2


def register_admin(commands) -> None:
    parser = commands.add_parser("appreview", help="Meta の審査の台本と提出の前の点検（読むだけ）")
    operations = parser.add_subparsers(dest="appreview_operation", required=True)
    p = operations.add_parser("script", help="権限・機能 → THTH の口 → 審査員が打つ命令 → 録画の区間の表")
    p.add_argument("--tsv", action="store_true", help="tools/appreview_rec.sh --segments が読む区間の並びを出す")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_script)
    p = operations.add_parser("check", help="30 日以内の呼び出し・口・privacy の語が足りない権限を赤で出す（通れば黙る）")
    p.add_argument("--privacy", default=None, help="privacy の頁のファイル（既定は tools/build_site.py が作る頁）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_check)
