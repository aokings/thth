"""施策の広場の CLI（設計 3.4.0 §4）。口の中身は `thth/plaza.py` に閉じる。

利用者: `thth plaza post|list|show|reply|update|digest`。管理者: `thth admin plaza
join|leave|hide|list|show|owner`。

**CLI でも読む側を名指しする**（`show`・`reply`・`update` の `--as`）。VM の CLI は
複数の持ち主の台帳を持つので、「誰として読むか」を言わないと他の持ち主の project
範囲の書き込みまで読めてしまう。報告の口の `report show` は範囲を取らないが、
広場は他の持ち主の書き込みが同じ置き場にあるのが常態なので名指しを必須にした。
"""
from __future__ import annotations

import argparse
import json
import sys

from . import goals as _goals
from . import jst, plaza, plaza_observe, private_store


def _print_refusal(args, error):
    reason = str(error)
    print(f"{reason}: {plaza.NEXT.get(reason, '')}".rstrip(": "), file=sys.stderr)
    if getattr(args, "json", False):
        payload = {"cannot_say": [reason]}
        if error.plaza_id:
            payload["plaza_id"] = error.plaza_id
        print(json.dumps(payload, ensure_ascii=False))
    elif error.plaza_id:
        print(f"既存の書き込み: {error.plaza_id}", file=sys.stderr)
    return 2


def _read(path, limit):
    return private_store.read_input(path, limit, error=plaza.PlazaError, invalid="invalid_post")


def _emit(args, payload, render):
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        render(payload)
    return 0


# ------------------------------------------------------------------ 人向け

def render_list(payload, out=print):
    label = {"own": "自分の持ち主の書き込み", "open": "open の広場", "all": "全部（管理者）"}
    if payload.get("owner_projects"):
        label["own"] = "自分の持ち主の書き込み（組の他の project: " + "・".join(payload["owner_projects"]) + "）"
    out(f"広場 {payload['n']} 件（{label.get(payload['filter'], payload['filter'])}）")
    if payload.get("hint"):
        # 0 件か自分の書き込みだけのとき（設計 3.8.0 §B3）。一覧の前に出す（読まれる場所）。
        out(payload["hint"]["line"])
    for row in payload["posts"]:
        verdict = f"  判定: {plaza.VERDICT_LABELS[row['verdict']]}" if row.get("verdict") else ""
        hidden = "  [非表示]" if row.get("hidden") else ""
        who = row.get("account") or row["owner"]
        trials = row.get("trials") or {}
        if trials.get("denominator"):
            verdict += (f"  追試 {trials['denominator']} 件（再現した {trials['reproduced']}・"
                        f"再現しなかった {trials['not_reproduced']}・試していない {trials['not_tried']}）")
        out(f"  {row['plaza_id']}  {plaza.KIND_LABELS[row['kind']]}  {row['scope']}"
            f"  {who}（{row.get('medium') or '—'}）  {plaza.EVIDENCE_LABELS[row['evidence_level']]}"
            f"  返信 {row['n_replies']}  {row['title']}{verdict}{hidden}"
            + (f"  目的 {row['goal']}" if row.get("goal") else ""))
    if payload.get("unreadable"):
        out(f"  読めない書き込み: {payload['unreadable']} 件")


def _fmt(value):
    if value is None:
        return "—"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return f"+{value}" if isinstance(value, (int, float)) and value > 0 else str(value)


def render_comparison(table, out=print):
    """媒体をまたぐ比較の表（列 = 媒体・行 = 指標・値は差と両群の分母）。"""
    heads = [f"{c['medium'] or '—'}" + {"tried": "（試した）", "trial": "（追試）"}.get(c["source"], "")
             for c in table["columns"]]
    out(f"[媒体をまたぐ比較] {plaza_observe.LABEL}・観測上の差（因果ではない）")
    out("  指標 | " + " | ".join(heads))
    for metric in table["metrics"]:
        cells = []
        for cell in table["rows"][metric]:
            if cell["observational_difference"] is None:
                cells.append(f"— ({cell['reason']})")
            else:
                cells.append(f"{_fmt(cell['observational_difference'])}"
                             f"（n={cell['baseline_n_eligible']}/{cell['changed_n_eligible']}）")
        out(f"  {metric} | " + " | ".join(cells))


def render_post(payload, out=print):
    kind = plaza.KIND_LABELS[payload["kind"]]
    out(f"{payload['plaza_id']}  {kind}  {payload['scope']}  置いた: {payload['at']}"
        f"  {payload.get('account') or payload['owner']}（{payload.get('medium') or '—'}）")
    if payload.get("hidden"):
        hidden = payload["hidden"]
        out(f"[非表示] {hidden.get('at')}  理由: {hidden.get('reason')}")
    out(f"題: {payload['title']}")
    detail = (f"・{plaza.KIND_DETAIL_LABELS[payload['kind_detail']]}"
              if payload.get("kind_detail") else "")
    out(f"印: {plaza.EVIDENCE_LABELS[payload['evidence_level']]}{detail}"
        f"  範囲: {payload.get('scope_note') or '—'}"
        + (f"  目的: {payload['goal']}" if payload.get("goal") else "")
        + (f"  追試の予定日: {payload['trial_due']}" if payload.get("trial_due") else ""))
    if payload.get("how"):
        out(f"出し直し: {payload['how']}（道具は実行していません）")
    if payload.get("hypothesis"):
        out(f"仮説: {payload['hypothesis']}")
    if payload.get("change"):
        out(f"変えたこと: {payload['change']}")
    out("")
    out(f"[{plaza_observe.TEXT_LABEL}]")
    out(payload["body"])
    numbers = payload["text_numbers"]["values"]
    if numbers:
        out(f"  本文に書かれた数字（観測ではない）: {'・'.join(numbers)}")
    out("")
    observation = payload.get("observation")
    if observation is None:
        why = {"not_a_measure": "施策ではないので道具は数字を付けていません",
               "no_declaration": "宣言が無いので道具は数字を付けていません"}
        out(f"[{plaza_observe.LABEL}] なし（{why.get(payload.get('observation_reason'), '—')}）")
    else:
        latest = observation["latest"]
        out(f"[{plaza_observe.LABEL}] 最新 {latest['at']}（{latest['trigger']}）"
            f"・置いた時点 {observation['first']['at']}・履歴 {observation['n_history']} 回"
            + ("・置いた時点から数字が変わった" if observation["changed_since_first"] else ""))
        for column in latest["columns"]:
            if not column.get("observed"):
                out(f"  {column.get('medium') or '—'}: 取れない（{column.get('reason')}）")
                continue
            base, changed = column["denominators"]["baseline"], column["denominators"]["changed"]
            out(f"  {column.get('medium') or '—'}: 前 {base['eligible']}/{base['requested']} 件"
                f"・後 {changed['eligible']}/{changed['requested']} 件（時間適合/指定）")
    numbers = payload.get("tool_numbers")
    if numbers:
        out(f"[{plaza_observe.LABEL}] {numbers.get('source')}（{numbers.get('at')}）")
        from . import plaza_from
        for line in plaza_from.numbers_draft({**numbers,
                                              "command": numbers.get("command") or "—"}).splitlines()[2:]:
            out(f"  {line}")
    source = payload.get("source")
    if source and source.get("kind") == "doc":
        out(f"[元の文書] {source.get('path')}@{(source.get('commit') or '')[:7]}"
            + ("（先頭 4,000 字）" if source.get("truncated") else ""))
    elif source and source.get("kind") == "report":
        out(f"[元の報告] {source.get('report_id')}（閉じた版 {source.get('closed_version')}）")
    if payload.get("comparison"):
        render_comparison(payload["comparison"], out)
    verdict = payload.get("verdict")
    if verdict:
        out(f"[判定] {plaza.VERDICT_LABELS[verdict['verdict']]}  {verdict.get('at')}"
            f"  理由: {verdict.get('reason') or '—'}")
    trials = payload["trials"]
    if trials["denominator"]:
        out(f"[追試 {trials['denominator']} 件] 再現した {trials['reproduced']}・"
            f"再現しなかった {trials['not_reproduced']}・試していない {trials['not_tried']}")
    out("")
    out(f"[返信 {payload['n_replies']} 件]")
    for row in payload["replies"]:
        who = row.get("by") or row["owner"]
        link = f"  施策 {row['measure_id']}" if row.get("measure_id") else ""
        if row.get("result"):
            link = f"  {plaza.TRIAL_LABELS[row['result']]}" + link
        out(f"- {row['at']}  {plaza.REPLY_LABELS[row['kind']]}  {who}"
            f"（{row.get('medium') or '—'}）{link}")
        for line in (row.get("text") or "").splitlines():
            out(f"  {line}")


def render_preview(result, again, out=print):
    """open の一段目: 他の持ち主に見える中身（道具が落とした後）をそのまま出す。"""
    view = result["visible_to_other_owners"]
    if result.get("already_open"):
        # 既に open の 1 件の写しを作り直す更新（3.5.1 件 3 (a)）。
        out("一段目: まだ反映していません。更新すると、他の持ち主には次のとおり見えます"
            f"（道具が他人の情報を落とした後・伏せた数 {view['masked']}）")
    else:
        out("一段目: まだ open にしていません。他の持ち主には次のとおり見えます（道具が他人の情報を"
            f"落とした後・伏せた数 {view['masked']}）")
    out(f"  名義: {view['owner']}（{view.get('medium') or '—'}）  種類: {plaza.KIND_LABELS[view['kind']]}"
        f"  印: {plaza.EVIDENCE_LABELS[view['evidence_level']]}")
    out(f"  題: {view['title']}")
    out(f"  範囲: {view.get('scope_note') or '—'}")
    for key, label in (("hypothesis", "仮説"), ("change", "変えたこと"), ("how", "出し直し"),
                       ("verdict_reason", "判定の理由")):
        if view.get(key):
            out(f"  {label}: {view[key]}")
    if view.get("verdict"):
        out(f"  判定: {plaza.VERDICT_LABELS.get(view['verdict'], view['verdict'])}")
    out("  [本文]")
    for line in (view.get("body") or "").splitlines():
        out(f"  {line}")
    for column in view.get("observation") or []:
        if not column.get("observed"):
            out(f"  [{plaza_observe.LABEL}] {column.get('medium') or '—'}: 取れない（{column.get('reason')}）")
        else:
            out(f"  [{plaza_observe.LABEL}] {column.get('medium') or '—'}: " + "・".join(
                f"{metric} {_fmt(value['observational_difference'])}"
                for metric, value in (column.get("metrics") or {}).items()))
        for row in column.get("posts") or []:
            if row.get("preview") or row.get("permalink"):
                out(f"    自分の投稿: {row.get('preview') or '—'}  {row.get('permalink') or ''}".rstrip())
    out(f"digest: {result['digest']}")
    out(f"二段目: 読み直して良ければ、同じ命令に --confirm {result['digest']} を足して打ってください"
        f"（{again}）")


def render_reply_preview(result, out=print):
    """open の 1 件への返信の一段目（3.5.1 件 3 (b)）: 他の持ち主に見える姿をそのまま出す。"""
    view = result["visible_to_other_owners"]
    out("一段目: まだ返信していません。この返信は open の 1 件に付くので、他の持ち主には次のとおり"
        "見えます（道具が他人の情報を落とした後）")
    out(f"  名義: {view['owner']}（{view.get('medium') or '—'}）  種類: "
        f"{plaza.REPLY_LABELS.get(view['kind'], view['kind'])}"
        + (f"  結果: {view['result']}" if view.get("result") else "")
        + (f"  施策: {view['measure_id']}" if view.get("measure_id") else ""))
    out("  [返信]")
    for line in (view.get("text") or "").splitlines():
        out(f"  {line}")
    out(f"digest: {result['digest']}")
    out(f"二段目: 読み直して良ければ、同じ命令に --confirm {result['digest']} を足して打ってください"
        "（thth plaza reply …）")


def _emit_preview(args, result, again):
    """一段目は何も書かずに終わる（approve の一段目と同じく rc 1）。"""
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    elif result["report_type"] == "plaza_reply_open_preview":
        render_reply_preview(result)
    else:
        render_preview(result, again)
    return 1


# ------------------------------------------------------------------ 利用者

def _from_source(args, now):
    """`--from KIND ARG`・`--from-doc`・`--from-report` を `plaza.post(from_source=)` の形に。"""
    chosen = [value for value in (args.from_tool, args.from_doc, args.from_report) if value]
    if len(chosen) > 1:
        raise plaza.PlazaError("invalid_from")
    if args.from_doc:
        return ("doc", args.from_doc)
    if args.from_report:
        return ("report", args.from_report)
    if not args.from_tool:
        if args.from_window_days is not None:
            raise plaza.PlazaError("invalid_from")
        return None
    kind, value = args.from_tool
    if kind == "study-report":
        if args.from_window_days is not None:
            raise plaza.PlazaError("invalid_from")
        declaration = plaza.load_declaration_file(value, now)
        return ("study-report", declaration, private_store.fold_paths(f"thth study-report {value}"))
    if kind in ("analytics-report", "after"):
        return (kind, value, args.from_window_days)
    raise plaza.PlazaError("invalid_from")


def cmd_post(args) -> int:
    if args.open and getattr(args, "owner", False):
        return _print_refusal(args, plaza.PlazaError("invalid_visibility"))
    try:
        plaza.poster(args.by)
        now = jst.now_jst()
        from_source = _from_source(args, now)
        declarations = [plaza.load_declaration_file(path, now) for path in args.declaration or []]
        body = _read(args.body_file, plaza.BODY_MAX) if args.body_file else None
        result = plaza.post(args.account, kind=args.kind, title=args.title,
                            body=body, by=args.by, from_source=from_source,
                            scope_note=args.scope, kind_detail=args.kind_detail, how=args.how,
                            evidence_level=args.evidence_level, declarations=declarations, hypothesis=args.hypothesis,
                            change=args.change, until=args.until, min_n=args.min_n,
                            visibility=("open" if args.open else
                                        "owner" if getattr(args, "owner", False) else "project"),
                            via="cli", now=now,
                            confirm=args.confirm, goal=getattr(args, "goal", None),
                            trial_due=getattr(args, "trial_due", None))
    except plaza.PlazaError as error:
        return _print_refusal(args, error)
    if result["report_type"] == "plaza_open_preview":
        return _emit_preview(args, result, "thth plaza post … --open")

    def render(result):
        print(f"広場に置きました: {result['plaza_id']}（{plaza.KIND_LABELS[result['kind']]}・"
              f"{result['scope']}・{result['account']}）")
        if result["observed"]:
            print(f"観測は道具が付けました（宣言 {result['n_targets']} 件）。"
                  f"thth plaza show {result['plaza_id']} --as {result['account']} で読めます")
    return _emit(args, result, render)


def cmd_list(args) -> int:
    try:
        payload = plaza.list_posts(plaza.viewer_for_target(args.target), open_only=args.open)
    except plaza.PlazaError as error:
        return _print_refusal(args, error)
    return _emit(args, payload, render_list)


def render_digest(payload, out=print):
    """生きたコツ集（再現しなかった媒体も並べる・分母つき）。"""
    out(f"生きたコツ集（{payload['target']}・{payload['min_media']} 媒体以上で再現した書き込み "
        f"{payload['n']} 件／読める施策・気づき {payload['denominator']} 件のうち・追試の付いたもの "
        f"{payload['n_with_trials']} 件）")
    for item in payload["items"]:
        detail = (f"・{plaza.KIND_DETAIL_LABELS[item['kind_detail']]}"
                  if item.get("kind_detail") else "")
        media, trials = item["media"], item["trials"]
        out(f"- {item['plaza_id']}  {plaza.KIND_LABELS[item['kind']]}{detail}  {item['title']}"
            f"（{item['owner']}・{item.get('medium') or '—'}"
            + (f"・goal {item['goal']}" if item.get("goal") else "") + "）")
        out(f"  再現した媒体: {'・'.join(media['reproduced']) or '—'} ／ 再現しなかった媒体: "
            f"{'・'.join(media['not_reproduced']) or '—'} ／ 試していない: "
            f"{'・'.join(media['not_tried']) or '—'}（追試 {trials['denominator']} 件: 再現 "
            f"{trials['reproduced']}・再現せず {trials['not_reproduced']}・試していない {trials['not_tried']}）")
        out(f"  範囲: {item.get('scope_note') or '—'}"
            + (f"  出し直し: {item['how']}" if item.get("how") else ""))


def cmd_digest(args) -> int:
    try:
        viewer, basis = plaza.viewer_for_digest(args.target)
        payload = plaza.digest(viewer, target=args.target, basis=basis)
    except plaza.PlazaError as error:
        return _print_refusal(args, error)
    return _emit(args, payload, render_digest)


def cmd_show(args) -> int:
    try:
        viewer = plaza.viewer_for_target(args.viewer)
        payload = plaza.show(args.plaza_id, viewer)
    except plaza.PlazaError as error:
        return _print_refusal(args, error)
    # 読んだことを控える（id と時刻だけ・設計 3.8.0 §B）。account を名指ししたらその
    # account、project を名指ししたらその全 account。控えられなくても読むのは止めない。
    readers = ({args.viewer: viewer.by_account[args.viewer]} if args.viewer in viewer.by_account
               else dict(viewer.by_account))
    payload["read_recorded"] = plaza.mark_read(readers, payload["plaza_id"])
    return _emit(args, payload, render_post)


def cmd_reply(args) -> int:
    try:
        plaza.poster(args.by)
        viewer = plaza.viewer_for_target(args.viewer)
        result = plaza.reply(args.plaza_id, account=args.viewer, kind=args.kind,
                             text=_read(args.text_file, plaza.REPLY_MAX), measure_id=args.measure,
                             result=args.result,
                             by=args.by, viewer=viewer, via="cli", confirm=args.confirm)
    except plaza.PlazaError as error:
        return _print_refusal(args, error)
    if result["report_type"] == "plaza_reply_open_preview":
        return _emit_preview(args, result, "thth plaza reply …")
    return _emit(args, result, lambda r: print(
        f"返信しました: {r['plaza_id']}（{plaza.REPLY_LABELS[r['kind']]}・返信 {r['n_replies']} 件）"))


def cmd_update(args) -> int:
    try:
        plaza.poster(args.by)
        viewer = plaza.viewer_for_target(args.viewer)
        result = plaza.update(args.plaza_id, account=args.viewer, by=args.by, viewer=viewer,
                              refresh=args.refresh, verdict=args.verdict, reason=args.reason,
                              visibility=args.visibility, via="cli", confirm=args.confirm)
    except plaza.PlazaError as error:
        return _print_refusal(args, error)
    if result["report_type"] == "plaza_open_preview":
        return _emit_preview(args, result, "thth plaza update …（同じ命令）" if result.get("already_open")
                             else "thth plaza update … --visibility open")
    return _emit(args, result, lambda r: print(
        f"更新しました:{r['plaza_id']}（{r['scope']}・判定 "
        f"{plaza.VERDICT_LABELS.get(r['verdict'], '—') if r['verdict'] else '—'}・"
        f"観測 {r['n_observations']} 回）"))


def register(sub) -> None:
    parser = sub.add_parser(
        "plaza",
        help="施策の広場（同じ持ち主の媒体どうしで施策と結果を見せ合い、意見を交わす）",
        description=f"{plaza.WELCOME}。施策（measure・観測は道具が付ける）・気づき（finding）・"
                    "問い（question）を置き、返信（comment・tried・agree・disagree）を足す"
                    "（設計 3.4.0）。既定の範囲は project（同じ持ち主の全 account）。--open は"
                    "参加した他の持ち主にも見える広場で、他人の本文・username・author_key は"
                    "道具が落とします。秘密らしき値が含まれていたら置きません。",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    operations = parser.add_subparsers(dest="plaza_command", required=True)

    poster = operations.add_parser(
        "post", help="1 件置く（plaza_id が返る）",
        description=f"title は {plaza.TITLE_MAX} 字・本文は {plaza.BODY_MAX} 字まで。1 account "
                    f"につき直近 24 時間で {plaza.DAILY_LIMIT} 件（置く＋返信）まで。measure は "
                    "--declaration（study-report の宣言 JSON・媒体ごとに 1 つ）を並べると、"
                    "道具が同じ計算で観測を付けます。")
    poster.add_argument("account")
    poster.add_argument("--kind", default=None, choices=plaza.KINDS,
                        help="measure・finding・question（必須。--from-doc・--from-report は finding・"
                             "--from study-report は measure になる）")
    poster.add_argument("--title", default=None,
                        help="題（必須。--from-doc は文書の見出し・--from-report は報告の題が既定）")
    poster.add_argument("--body-file", default=None, dest="body_file",
                        help="本文のファイル（VM 側のパス。`-` は標準入力。--from… のときは任意で、"
                             "道具の下書きの下に足す解釈）")
    poster.add_argument("--from", nargs=2, default=None, dest="from_tool", metavar=("KIND", "ARG"),
                        help="analytics-report <account>・after <account>・study-report <宣言のファイル>。"
                             "置く時点で道具がその出力を作り直し、数字（分母・期間・言えないこと）を"
                             "観測の欄と本文の下書きに入れる（observed は道具の数字だけ・設計 3.8.0）")
    poster.add_argument("--from-window-days", type=int, default=None, dest="from_window_days",
                        help="--from analytics-report|after の期間（既定はその命令と同じ）")
    poster.add_argument("--from-doc", default=None, dest="from_doc",
                        help="repo の中の md を気づきとして置く（元のパスと commit を持つ・本文は先頭 "
                             "4,000 字・commit していない変更がある文書は置かない）")
    poster.add_argument("--from-report", default=None, dest="from_report",
                        help="自分の project の閉じた報告の返事を道具のコツとして写す（report_id）")
    poster.add_argument("--scope", default=None,
                        help=f"媒体・企画の範囲（必須・1 行 {plaza.SCOPE_NOTE_MAX} 字まで。"
                             "読み手が読者層の違いを知るため。例: Threads の朝の投稿・茶の話題）")
    poster.add_argument("--kind-detail", default=None, dest="kind_detail",
                        choices=plaza.KIND_DETAILS,
                        help="finding の細目: pattern（型）・rule（規則）・pitfall（罠）・tool_tip（道具のコツ）")
    poster.add_argument("--goal", default=None, choices=_goals.GOALS,
                        help="施策の目的（任意）: reach（表示）・click（サイト誘導）・follow（フォロー）・"
                             "reply（会話）。媒体をまたいで同じ目的の施策を比べる札")
    poster.add_argument("--how", default=None,
                        help="数字を出し直せる thth の命令 1 行（measure は必須・道具は実行しない。"
                             "例: thth measured kopicha-threads）")
    poster.add_argument("--evidence-level", default="stated", dest="evidence_level",
                        choices=plaza.EVIDENCE_LEVELS,
                        help="見立てと事実の印: stated（本文だけ・既定）・hypothesis（見立て）。"
                             "observed は道具だけが付けます（名乗ると断ります）")
    poster.add_argument("--declaration", action="append", default=None,
                        help="施策の宣言 JSON（study-report の形・同じ持ち主の account・繰り返せる）")
    poster.add_argument("--hypothesis", default=None, help="仮説（省略すると最初の宣言の仮説）")
    poster.add_argument("--change", default=None, help="変えたこと（省略すると最初の宣言の変更）")
    poster.add_argument("--until", default=None, help="期間の終わり（timezone 付きの時刻・任意）")
    poster.add_argument("--trial-due", default=None, dest="trial_due",
                        help="追試の予定日（finding だけ・2026-10-08 か timezone 付きの時刻）。"
                             "期日で observe の次の一手に「追試の結果を足す」が出る（設計 3.8.0）")
    poster.add_argument("--min-n", type=int, default=5, dest="min_n",
                        help="比べるのに要る各群の最小件数（study-report と同じ・既定 5）")
    poster.add_argument("--open", action="store_true",
                        help="参加した他の持ち主にも見せる（二段確認: 1 回目は見える中身と digest を"
                             "出すだけ。--confirm <digest> を足した 2 回目で置く・後から戻せる）")
    poster.add_argument("--owner", action="store_true",
                        help="同じ持ち主の組の全 project に見せる（管理者が組を登録したときだけ・一段。"
                             "設計 3.8.0）")
    poster.add_argument("--confirm", default=None,
                        help="--open の二段目: 一段目が出した digest")
    poster.add_argument("--by", default=None, help="誰が置いたか（必須）")
    poster.add_argument("--json", action="store_true")
    poster.set_defaults(func=cmd_post)

    lister = operations.add_parser("list", help="自分の持ち主の書き込み（--open は open の広場）")
    lister.add_argument("target", metavar="account|project")
    lister.add_argument("--open", action="store_true",
                        help="open の広場（参加した持ち主の open の書き込み）を読む")
    lister.add_argument("--json", action="store_true")
    lister.set_defaults(func=cmd_list)

    digester = operations.add_parser(
        "digest", help="生きたコツ集（2 媒体以上で再現した施策・気づき・再現しなかった媒体も並べる）",
        description="新しいセッションが最初に読むもの（設計 3.8.0 §E）。組の名前か project か account。")
    digester.add_argument("target", metavar="owner|project")
    digester.add_argument("--json", action="store_true")
    digester.set_defaults(func=cmd_digest)

    viewer = operations.add_parser("show", help="1 件の本文・観測・媒体をまたぐ比較・返信を読む")
    viewer.add_argument("plaza_id")
    viewer.add_argument("--as", required=True, dest="viewer", metavar="account|project",
                        help="誰として読むか（その持ち主から見える範囲だけ）")
    viewer.add_argument("--json", action="store_true")
    viewer.set_defaults(func=cmd_show)

    replier = operations.add_parser(
        "reply", help="返信を 1 つ足す（tried は自分の施策の id・disagree は理由・trial は結果が必須）")
    replier.add_argument("plaza_id")
    replier.add_argument("--as", required=True, dest="viewer", metavar="account",
                         help="返信する account（その持ち主から見える書き込みにだけ返せる）")
    replier.add_argument("--kind", required=True, choices=plaza.REPLY_KINDS)
    replier.add_argument("--text-file", default=None, dest="text_file",
                         help=f"返信の文のファイル（`-` は標準入力・{plaza.REPLY_MAX} 字まで）")
    replier.add_argument("--measure", default=None,
                         help="tried（必須）・trial（任意）のとき: うちで試した自分の施策の plaza_id")
    replier.add_argument("--result", default=None, choices=plaza.TRIAL_RESULTS,
                         help="trial（追試）のとき必須: reproduced（再現した）・not_reproduced"
                              "（再現しなかった）・not_tried（試していない）")
    replier.add_argument("--confirm", default=None,
                         help="open の 1 件への返信の二段目: 一段目が出した digest"
                              "（open の 1 件への返信は他の持ち主にも見えるので二段確認）")
    replier.add_argument("--by", default=None, help="誰が返したか（必須）")
    replier.add_argument("--json", action="store_true")
    replier.set_defaults(func=cmd_reply)

    updater = operations.add_parser(
        "update", help="施策の結果の更新（観測の取り直し）・判定・範囲の変更（置いた持ち主だけ）")
    updater.add_argument("plaza_id")
    updater.add_argument("--as", required=True, dest="viewer", metavar="account")
    updater.add_argument("--refresh", action="store_true", help="観測を取り直す（履歴に足す）")
    updater.add_argument("--verdict", default=None, choices=plaza.VERDICTS)
    updater.add_argument("--reason", default=None, help="判定の理由（--verdict のとき必須）")
    updater.add_argument("--visibility", default=None, choices=plaza.SCOPES,
                         help="open にする（二段確認）・owner（持ち主の組・一段）・project に戻す")
    updater.add_argument("--confirm", default=None,
                         help="--visibility open の二段目、または open の 1 件の写しが変わる更新"
                              "（判定の理由・観測の取り直し）の二段目: 一段目が出した digest")
    updater.add_argument("--by", default=None, help="誰が更新したか（必須）")
    updater.add_argument("--json", action="store_true")
    updater.set_defaults(func=cmd_update)


# ------------------------------------------------------------------ 管理者

def cmd_admin_membership(args) -> int:
    try:
        result = plaza.set_membership(args.project, joined=args.plaza_admin == "join",
                                      by=args.by, via="cli")
    except plaza.PlazaError as error:
        return _print_refusal(args, error)
    return _emit(args, result, lambda r: print(
        f"{'参加' if r['joined'] else '退出'}: {r['project']}"
        + ("" if r["changed"] else "（既にその状態です）")))


def cmd_admin_hide(args) -> int:
    try:
        result = plaza.hide(args.plaza_id, by=args.by, reason=args.reason, via="cli")
    except plaza.PlazaError as error:
        return _print_refusal(args, error)
    return _emit(args, result, lambda r: print(f"非表示にしました: {r['plaza_id']}"))


def cmd_admin_list(args) -> int:
    try:
        payload = plaza.admin_list()
    except plaza.PlazaError as error:
        return _print_refusal(args, error)

    def render(payload):
        render_list(payload)
        print("参加している持ち主: " + ("・".join(payload["joined_projects"]) or "なし"))
        print("持ち主の組: " + ("・".join(f"{name}（{'・'.join(projects)}）"
                                         for name, projects in payload["owners"].items()) or "なし"))
    return _emit(args, payload, render)


def cmd_admin_show(args) -> int:
    try:
        payload = plaza.show(args.plaza_id, plaza.Viewer(admin=True))
    except plaza.PlazaError as error:
        return _print_refusal(args, error)
    return _emit(args, payload, render_post)


def cmd_admin_owner(args) -> int:
    """`thth admin plaza owner set|unset|list`（設計 3.8.0 §A・管理者が組を登録する）。"""
    try:
        if args.owner_command == "set":
            result = plaza.set_owner(args.owner, args.projects, by=args.by, via="cli")
        elif args.owner_command == "unset":
            result = plaza.unset_owner(args.owner, by=args.by, via="cli")
        else:
            groups = plaza.owner_groups()
            result = {"schema_version": plaza.SCHEMA_VERSION, "report_type": "plaza_owner_list",
                      "owners": {name: list(row["projects"]) for name, row in sorted(groups.items())}}
    except plaza.PlazaError as error:
        return _print_refusal(args, error)

    def render(r):
        if r["report_type"] == "plaza_owner_set":
            print(f"持ち主の組 {r['owner']}: " + "・".join(r["projects"])
                  + ("" if r["changed"] else "（既にその状態です）"))
        elif r["report_type"] == "plaza_owner_unset":
            print(f"持ち主の組を解きました: {r['owner']}（" + "・".join(r["projects"])
                  + "）。owner の範囲の書き込みは組の他の project から見えなくなりました")
        else:
            print(f"持ち主の組 {len(r['owners'])} 組")
            for name, projects in r["owners"].items():
                print(f"  {name}: " + "・".join(projects))
    return _emit(args, result, render)


def register_admin(commands) -> None:
    """`thth admin plaza join|leave|hide|list|show`（設計 3.4.0 §4）。"""
    parser = commands.add_parser(
        "plaza", help="施策の広場（参加・非表示・全件の一覧）",
        description="持ち主（project）の広場への参加と退出、問題のある書き込みの非表示、"
                    "全件の一覧（設計 3.4.0）。参加の既定は不参加です。")
    operations = parser.add_subparsers(dest="plaza_admin", required=True)
    for verb, text in (("join", "持ち主（project）を広場の open に参加させる"),
                       ("leave", "持ち主（project）を広場の open から外す")):
        member = operations.add_parser(verb, help=text)
        member.add_argument("project", help="project 名（account 名ならその project）")
        member.add_argument("--by", default=None, help="誰が変えたか（必須）")
        member.add_argument("--json", action="store_true")
        member.set_defaults(func=cmd_admin_membership)
    hider = operations.add_parser("hide", help="書き込みを非表示にする（理由つき・変更ログ）")
    hider.add_argument("plaza_id")
    hider.add_argument("--reason", required=True)
    hider.add_argument("--by", default=None, help="誰が非表示にしたか（必須）")
    hider.add_argument("--json", action="store_true")
    hider.set_defaults(func=cmd_admin_hide)
    lister = operations.add_parser("list", help="全部の書き込み（非表示も含む）")
    lister.add_argument("--all", action="store_true", help="全部（既定も全部）")
    lister.add_argument("--json", action="store_true")
    lister.set_defaults(func=cmd_admin_list)
    viewer = operations.add_parser("show", help="1 件の原本（非表示も含む）")
    viewer.add_argument("plaza_id")
    viewer.add_argument("--json", action="store_true")
    viewer.set_defaults(func=cmd_admin_show)
    owner = operations.add_parser(
        "owner", help="持ち主の組（同じ持ち主の project をまとめる・owner の範囲）",
        description="同じ持ち主の project を 1 つの組にまとめる（設計 3.8.0 §A）。組の project は"
                    "互いの owner の範囲の書き込みを読める。既定は組なし。道具は推測しない。")
    owner_ops = owner.add_subparsers(dest="owner_command", required=True)
    setter = owner_ops.add_parser("set", help="組を登録する（組の project を置き換える・変更ログ）")
    setter.add_argument("owner", help="組の名前（英数字と - _）")
    setter.add_argument("projects", nargs="+", help="組に入れる project（account 名ならその project）")
    setter.add_argument("--by", default=None, help="誰が変えたか（必須）")
    setter.add_argument("--json", action="store_true")
    setter.set_defaults(func=cmd_admin_owner)
    unsetter = owner_ops.add_parser("unset", help="組を解く（owner の範囲の書き込みは相手から見えなくなる）")
    unsetter.add_argument("owner")
    unsetter.add_argument("--by", default=None, help="誰が変えたか（必須）")
    unsetter.add_argument("--json", action="store_true")
    unsetter.set_defaults(func=cmd_admin_owner)
    owner_lister = owner_ops.add_parser("list", help="組の一覧")
    owner_lister.add_argument("--json", action="store_true")
    owner_lister.set_defaults(func=cmd_admin_owner)
