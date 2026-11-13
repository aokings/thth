"""`thth register …` の命令（台帳の機械・計画 `docs/計画_実店舗_2026-11-13.md` 段 1〜4）。

    thth register add <台帳> --path <フォルダ> --project <project> --kind media|store --by <名前>
                                       台帳のフォルダを THTH に控える（控えは 0600・台帳そのものは預からない）
    thth register check <台帳> [--json]
                                       全部の行の形を点検する（頭の 7 つの欄・印・見直し日・秘密らしい値・種類ごとの形）
    thth register show <台帳> [--json]
                                       行ごとに「中身の材料に使えるか」と、使えないならその符丁
    thth register use <台帳> <行> [--json]
                                       柵: その行を材料に使えるか。使えれば外に出してよい欄と本文、使えなければ断る
    thth register confirm <台帳> <行> --by <名前> [--confirm D] [--json]
                                       本人が認めた（営業の時間と臨時は二段確認）
    thth register deny <台帳> <行> --by <名前> [--json]
                                       本人が違うと言った（行は消さない・同じ推測を二度聞かない）
    thth register remove <台帳> --by <名前> [--json]
                                       THTH の控えと認めた記録を消す（台帳のフォルダは消さない）
    thth register triggers <台帳> [--json]
                                       暦と台帳から、今出す頃のきっかけ（臨時の休み・催し・営業の時間の変わり・新しい品・季節の品）
    thth register draft <台帳> --to <口座> (--row <行> | --trigger <種類>) [--json]
                                       きっかけから queue の下書き（承認されていない形・出さない期限つき）を 1 本か上限 3 本
    thth register holes <台帳> [--json]
                                       穴の一覧（推測の行・見直し日を過ぎた行・欠けた行・季節の休み・品の思い）。読むだけ
    thth register ask <台帳> --by <名前> [--json]
                                       今聞く穴を上限まで選び、問いの記録に「聞いた」を足す（聞く言葉は持ち主の LLM が書く）
    thth register answer <台帳> <問い> --result yes|no|other|skip --by <名前> [--asked 文] [--said 文] [--row 行] [--json]
                                       答えを問いの記録に足す。推測の行に no なら「違うと言った」、yes なら認める
                                       （営業の時間と臨時は答えだけでは認めない・二段の confirm を通す）
    thth register questions <台帳> [--json]
                                       問いの数・答えの数・答えから増えた行の数
    thth register paste <台帳> <行> [--json]
                                       外の場所（Google マップ・食べログなど）ごとに貼る文と、貼ったかの点検表（網に出ない）
    thth register pasted <台帳> <場所の行> --for <行> --by <名前> [--json]
                                       持ち主が貼ったと記録する（場所の行の頭の synced_on・synced_row だけを書く）
    thth register compare <台帳> <場所の行> --for <行> [--json] < 外の今の文
                                       持ち主が写して貼った外の場所の今の文と、台帳の日付と時刻を比べる（貼った文は残さない）

記録（`admin_log`）の動きの名前は `register_` で始める。名前・時刻・行の番号だけで、台帳の本文は入れない。
**網に出ない。** 台帳には頭の決まった欄（`form.MANAGED`）しか書かない。
"""
from __future__ import annotations

import json
import os
import shutil
import sys

from .. import admin_log, draft_origin, jst, redact, refusal, refusals
from . import ACTION_PREFIX, COMMAND, confirm as confirm_mod, draft as draft_mod, fence, form, kinds, ledger
from . import holes as holes_mod, places as places_mod, questions as questions_mod, triggers as triggers_mod
from .errors import RegisterRefused

# 詳細の語のうち、目録の `by_detail` に通し方があるもの。
DETAIL_WORDS = frozenset(("register_name", "project", "by", "row", "confirm", "row_or_trigger", "question",
                          "answer_text"))


def _refuse(args, exc: RegisterRefused) -> int:
    reason = {"reason": exc.detail} if exc.detail in DETAIL_WORDS else {}
    found = refusal.parts(exc.code, **reason) or {}
    line = f"{exc.code}: {found.get('why') or exc.code}"
    if exc.detail:
        line += f"（{exc.detail}）"
    payload = {"ok": False, "code": exc.code, "detail": exc.detail, **exc.extra}
    return refusal.emit(args, exc.code, line=redact.redact(line), payload=payload, **reason)


def _run(handler):
    def call(args) -> int:
        try:
            return handler(args)
        except RegisterRefused as exc:
            return _refuse(args, exc)
    return call


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False))


def _by(args) -> str:
    try:
        return admin_log.actor(getattr(args, "by", None))
    except ValueError:
        raise RegisterRefused("register_args_invalid", "by") from None


def _record(event: str, name: str, by: str, diff: dict) -> None:
    try:
        admin_log.append(ACTION_PREFIX + event, name, {"media": "register"}, by=by, diff=diff)
    except (OSError, ValueError, admin_log.AdminLogError):
        raise RegisterRefused("register_log_unavailable") from None


def _log_ready() -> None:
    try:
        with admin_log.transaction():
            pass
    except (OSError, ValueError, admin_log.AdminLogError):
        raise RegisterRefused("register_log_unavailable") from None


def _today():
    return jst.now_jst().date()


def open_register(name: str):
    """`(控え, 台帳のフォルダ, 読んだ結果)`。"""
    cfg = ledger.load(name)
    root = ledger.root_of(cfg)
    return cfg, root, form.scan(root)


def find_row(scanned: dict, row_id: str) -> form.Row:
    if not isinstance(row_id, str) or not form.ID.fullmatch(row_id):
        raise RegisterRefused("register_args_invalid", "row")
    row = form.find(scanned["rows"], row_id)
    if row is None:
        raise RegisterRefused("register_row_missing", row_id)
    return row


def _row_line(row: form.Row) -> list:
    return [f"  {row.path}: {word} — {form.problem_text(word)}" for word in row.problems]


# ------------------------------------------------------------------ 命令

def cmd_add(args) -> int:
    by = _by(args)
    name = ledger.check_name(args.register)
    if not isinstance(args.project, str) or not ledger.accounts.name_is_safe(args.project):
        raise RegisterRefused("register_args_invalid", "project")
    path = ledger.check_path(args.path)
    _log_ready()
    written = ledger.write_new(name, ledger.build(name, project=args.project, path=path, kind=args.kind))
    _record("added", name, by, {"project": [None, args.project], "kind": [None, args.kind]})
    scanned = form.scan(path)
    value = {"register": name, "ledger": written, "rows": len(scanned["rows"]), "problems": len(scanned["problems"])}
    if args.json:
        _print(value)
        return 0
    print(f"台帳 {name} を控えました（{written}）。行 {value['rows']} 枚・形の問題 {value['problems']} 件")
    print(f"  次: thth {COMMAND} check {name} で形を点検します")
    return 0


def cmd_check(args) -> int:
    cfg, _root, scanned = open_register(args.register)
    value = {"register": args.register, "kind": cfg["kind"], "rows": len(scanned["rows"]),
             "skipped": scanned["skipped"], "problems": scanned["problems"]}
    if scanned["problems"]:
        lines = []
        for row in scanned["rows"]:
            lines += _row_line(row)
        if not args.json:
            for line in lines:
                print(line, file=sys.stderr)
        raise RegisterRefused("register_row_invalid", f"{len(scanned['problems'])} rows",
                              problems=scanned["problems"], rows=len(scanned["rows"]))
    if args.json:
        _print(value)
        return 0
    print(f"台帳 {args.register}: 行 {value['rows']} 枚の形は合っています（頭の無い .md {value['skipped']} 枚は読み物として数えただけ）")
    return 0


def cmd_show(args) -> int:
    _cfg, _root, scanned = open_register(args.register)
    records = ledger.read_confirmed(args.register)
    today = _today()
    rows = [{"id": row.id, "path": row.path, "kind": row.kind, "confirmed": row.head.get("confirmed"),
             "usable": fence.state(row, records, today) is None, "code": fence.state(row, records, today)}
            for row in scanned["rows"]]
    if args.json:
        _print({"register": args.register, "rows": rows})
        return 0
    for row in rows:
        mark = "使える" if row["usable"] else row["code"]
        print(f"{row['id'] or '(番号なし)'}\t{row['kind']}\t{row['confirmed']}\t{mark}\t{row['path']}")
    return 0


def cmd_use(args) -> int:
    _cfg, _root, scanned = open_register(args.register)
    row = fence.usable(find_row(scanned, args.row), ledger.read_confirmed(args.register), _today())
    value = {"register": args.register, "row": row.id, "kind": row.kind, "fields": form.outward(row),
             "body": row.body.strip(), "digest": form.fingerprint(row.text)}
    if args.json:
        _print(value)
        return 0
    print(f"行 {row.id}（{row.kind}）は材料に使えます。外に出してよい欄:")
    for key, val in value["fields"].items():
        print(f"  {key}: {val}")
    if value["body"]:
        print("本文:")
        print(value["body"])
    return 0


def cmd_confirm(args) -> int:
    by = _by(args)
    _cfg, root, scanned = open_register(args.register)
    row = find_row(scanned, args.row)
    if row.problems:
        raise RegisterRefused("register_row_secret" if "secret" in row.problems else "register_row_invalid", row.id)
    if row.head["confirmed"] == form.YES and (ledger.read_confirmed(args.register).get(row.id) or {}).get(
            "digest") == form.fingerprint(row.text):
        value = {"register": args.register, "row": row.id, "confirmed": form.YES, "already": True}
        _print(value) if args.json else print(f"行 {row.id} は既に本人が認めた行です（中身も認めたときのままです）")
        return 0
    digest = confirm_mod.digest_for(args.register, row)
    if kinds.two_stage(row.kind) and args.confirm != digest:
        if args.confirm is not None:
            raise RegisterRefused("register_confirm_mismatch", "confirm", digest=digest)
        refusals.mark_first_stage()
        value = {"register": args.register, "row": row.id, "kind": row.kind, "stage": 1, "digest": digest,
                 "text": row.text}
        if args.json:
            _print(value)
            return 1
        print(f"行 {row.id}（{row.kind}）は二段で認める種類です。次の全文を持ち主に見せ、合っていると言われたら、")
        print(f"thth {COMMAND} confirm {args.register} {row.id} --by {by} --confirm {digest} で確定してください。")
        print("-----")
        print(row.text)
        print("-----")
        print(f"digest: {digest}")
        return 1
    _log_ready()
    done = confirm_mod.confirm(args.register, root, row, by=by)
    _record("confirmed", args.register, by, {"row": [None, row.id]})
    if args.json:
        _print({"register": args.register, **done})
        return 0
    print(f"行 {row.id} を「本人が認めた」にしました（{done['confirmed_at']}）。中身を変えると、認めたは外れます")
    return 0


def cmd_deny(args) -> int:
    by = _by(args)
    _cfg, root, scanned = open_register(args.register)
    row = find_row(scanned, args.row)
    if row.problems:
        raise RegisterRefused("register_row_secret" if "secret" in row.problems else "register_row_invalid", row.id)
    _log_ready()
    done = confirm_mod.deny(args.register, root, row, by=by)
    _record("denied", args.register, by, {"row": [None, row.id]})
    if args.json:
        _print({"register": args.register, **done})
        return 0
    print(f"行 {row.id} を「本人が違うと言った」にしました。行は残すので、同じことを二度聞きません")
    return 0


def remove_state(name: str) -> list:
    """控えと認めた記録を消す（台帳のフォルダは消さない）。消した物の並び。"""
    done = []
    state = ledger.state_dir_for(name)
    if os.path.isdir(state) and not os.path.islink(state):
        shutil.rmtree(state)
        done.append("state")
    try:
        os.unlink(ledger.path_for(name))
        done.append("ledger")
    except FileNotFoundError:
        pass
    return done


def cmd_remove(args) -> int:
    by = _by(args)
    ledger.load(args.register)
    _log_ready()
    done = remove_state(args.register)
    _record("removed", args.register, by, {"deleted": [[], done]})
    if args.json:
        _print({"register": args.register, "deleted": done})
        return 0
    print(f"台帳 {args.register} の控えと認めた記録を消しました（台帳のフォルダには触っていません）")
    return 0


# ------------------------------------------------------------------ きっかけと下書き（段 2）

def cmd_triggers(args) -> int:
    _cfg, _root, scanned = open_register(args.register)
    found = triggers_mod.find(scanned["rows"], ledger.read_confirmed(args.register), jst.now_jst())
    if args.json:
        _print({"register": args.register, "triggers": found})
        return 0
    if not found:
        print(f"台帳 {args.register}: 今出す頃のきっかけはありません")
        return 0
    for one in found:
        mark = "下書きにできる" if one["code"] is None else f"使えない（{one['code']}）"
        print(f"{one['trigger']}\t{one['row']}\t出さない期限 {one['not_after']}\t{mark}")
    return 0


def _pick(args, scanned, records, now) -> list:
    if (args.row is None) == (args.trigger is None):
        raise RegisterRefused("register_args_invalid", "row_or_trigger")
    if args.row is not None:
        row = find_row(scanned, args.row)
        fence.usable(row, records, now.date())
        found = triggers_mod.window(row, now)
        if found is None or found[2] == "early":
            raise RegisterRefused("register_draft_nothing", row.id)
        if found[2] == "past":
            raise RegisterRefused("register_draft_expired", row.id)
        return [(found[0], row, found[1])]
    picked = []
    for one in triggers_mod.find(scanned["rows"], records, now):
        if one["trigger"] != args.trigger or one["code"] is not None:
            continue
        row = form.find(scanned["rows"], one["row"])
        picked.append((one["trigger"], row, triggers_mod.window(row, now)[1]))
    if not picked:
        raise RegisterRefused("register_draft_nothing")
    return picked[:draft_mod.MAX_DRAFTS]


def cmd_draft(args) -> int:
    by = admin_log.actor(getattr(args, "by", None) or os.environ.get("THTH_ACTOR") or f"thth {COMMAND} draft")
    cfg, root, scanned = open_register(args.register)
    now = jst.now_jst()
    records = ledger.read_confirmed(args.register)
    account = draft_mod.load_account(args.to)
    queue = draft_mod.queue_dir(account)
    picked = _pick(args, scanned, records, now)
    for trigger, row, _until in picked:
        if draft_mod.same_exists(queue, args.register, row, trigger):
            raise RegisterRefused("register_draft_exists", row.id)
    _log_ready()
    commit = draft_mod.commit_of(root)
    written = []
    for trigger, row, until in picked:
        text = draft_mod.text_for(args.register, args.to, account["media"], trigger, row, commit=commit, now=now,
                                  not_after=until)
        name = draft_mod.file_name(args.register, row.id, trigger, now)
        if (queue / name).exists():
            raise RegisterRefused("register_draft_exists", row.id)  # 控えを書く前に（あるファイルの控えを書き換えない）
        # 書く前に THTH の側に出所を控える（頭の行が落とされても、承認の確定の段が控えから読み直す・監査 5.19.0 の 1）。
        draft_origin.record(ledger.state_dir_for(args.register), queue / name,
                            {**draft_mod.head(text), "not_after": jst.iso(until)})
        written.append({"row": row.id, "trigger": trigger, "not_after": jst.iso(until),
                        "path": draft_mod.write(queue, name, text)})
    _record("drafted", args.register, by, {"to": [None, args.to], "rows": [[], [w["row"] for w in written]],
                                           "triggers": [[], [w["trigger"] for w in written]]})
    notes = ["status は draft です（承認されていません）。本文は素の 1〜3 行なので、出す前に整えて thth approve に通してください",
             "出さない期限（not_after）を頭に入れました。過ぎると承認も予約も通りません",
             "住所・電話・休みの理由は、持ち主が public_fields に書いた物のほかは入れていません"]
    if account.get("project") != cfg["project"]:
        notes.append(f"注意: 口座 {args.to} の project（{account.get('project')}）が台帳の project（{cfg['project']}）と"
                     "違います。出す先が合っているか確かめてください")
    value = {"register": args.register, "account": args.to, "drafts": written, "commit": commit, "notes": notes}
    if args.json:
        _print(value)
        return 0
    for one in written:
        print(f"下書きを書きました: {one['path']}（{one['trigger']}・行 {one['row']}・出さない期限 {one['not_after']}）")
    for line in notes:
        print(f"  {line}")
    return 0


# ------------------------------------------------------------------ 聞いて埋める（段 3）

def cmd_holes(args) -> int:
    cfg, root, scanned = open_register(args.register)
    now = jst.now_jst()
    found = holes_mod.find(cfg["kind"], scanned["rows"], now.date(), ledger.read_confirmed(args.register), now)
    if args.json:
        _print({"register": args.register, "holes": found})
        return 0
    if not found:
        print(f"台帳 {args.register}: 穴はありません")
    for hole in found:
        print(f"{hole['hole']}\t{hole['hint']}")
    return 0


def cmd_ask(args) -> int:
    by = _by(args)
    cfg, root, scanned = open_register(args.register)
    now = jst.now_jst()
    log = questions_mod.read(root)
    found = holes_mod.find(cfg["kind"], scanned["rows"], now.date(), ledger.read_confirmed(args.register), now)
    picked = questions_mod.pick(found, log, now.date(), per_day=holes_mod.ASK_MAX_PER_DAY,
                                gap_days=holes_mod.ASK_GAP_DAYS)
    asked = []
    if picked:
        _log_ready()
        for hole in picked:
            asked.append({"type": "asked", "id": questions_mod.new_id(now), "hole": hole["hole"], "row": hole["row"],
                          "at": jst.iso(now), "by": by})
        questions_mod.append(root, asked)
        _record("asked", args.register, by, {"holes": [[], [a["hole"] for a in asked]]})
    questions = [dict(hole, question=a["id"]) for hole, a in zip(picked, asked)]
    value = {"register": args.register, "questions": questions, "holes": len(found),
             "limit": {"per_day": holes_mod.ASK_MAX_PER_DAY, "gap_days": holes_mod.ASK_GAP_DAYS}}
    if args.json:
        _print(value)
        return 0
    if not questions:
        print(f"台帳 {args.register}: 今聞くことはありません（穴 {len(found)} 件・1 日 {holes_mod.ASK_MAX_PER_DAY} 問まで・"
              f"同じ穴は {holes_mod.ASK_GAP_DAYS} 日空けます）")
        return 0
    for one in questions:
        print(f"{one['question']}\t{one['hole']}\t{one['hint']}")
    print(f"  答えを聞いたら thth {COMMAND} answer {args.register} <問い> --result yes|no|other|skip --by {by} で記録します")
    return 0


def cmd_answer(args) -> int:
    by = _by(args)
    qid = questions_mod.check_id(args.question)
    asked_text, said = questions_mod.check_text(args.asked), questions_mod.check_text(args.said)
    _cfg, root, scanned = open_register(args.register)
    log = questions_mod.read(root)
    found = questions_mod.state(log).get(qid)
    if found is None:
        raise RegisterRefused("register_question_missing", qid)
    if found["answered"] is not None:
        raise RegisterRefused("register_question_answered", qid)
    new_row = None
    if args.row is not None:
        new_row = find_row(scanned, args.row).id
    hole, row_id = found["asked"].get("hole") or "", found["asked"].get("row")
    _log_ready()
    notes, effect = [], None
    if hole.startswith("guess:") and row_id and args.result in ("yes", "no"):
        row = find_row(scanned, row_id)
        if args.result == "no":
            confirm_mod.deny(args.register, root, row, by=by)
            effect = "denied"
        elif kinds.two_stage(row.kind):
            # 営業の時間と臨時は、会話の一言だけでは認めない（計画 段 3 の柵）。
            notes.append(f"行 {row.id}（{row.kind}）は二段で認める種類なので、答えだけでは認めていません。"
                         f"全文を持ち主に見せて thth {COMMAND} confirm {args.register} {row.id} --by {by} で確定してください")
            effect = "needs_confirm"
        elif not row.problems:
            confirm_mod.confirm(args.register, root, row, by=by)
            effect = "confirmed"
    questions_mod.append(root, [{"type": "answered", "id": qid, "result": args.result, "at": jst.iso(), "by": by,
                                 "asked": asked_text, "said": said, "new_row": new_row}])
    _record("answered", args.register, by, {"question": [None, qid], "result": [None, args.result],
                                            "effect": [None, effect]})
    value = {"register": args.register, "question": qid, "result": args.result, "effect": effect, "notes": notes}
    if args.json:
        _print(value)
        return 0
    print(f"問い {qid} の答え（{args.result}）を記録しました" + (f"（{effect}）" if effect else ""))
    for line in notes:
        print(f"  {line}")
    return 0


def cmd_questions(args) -> int:
    _cfg, root, _scanned = open_register(args.register)
    value = {"register": args.register, **questions_mod.counts(questions_mod.read(root))}
    if args.json:
        _print(value)
        return 0
    print(f"台帳 {args.register}: 聞いた {value['asked']}・答えた {value['answered']}・飛ばした {value['skipped']}・"
          f"答えから増えた行 {value['rows_from_answers']}・まだ答えの無い問い {value['open']}")
    return 0


# ------------------------------------------------------------------ 外の場所に手で貼る束（段 4）

def _paste_row(row_id, scanned, records):
    row = find_row(scanned, row_id)
    if row.kind not in places_mod.PASTE_KINDS:
        raise RegisterRefused("register_paste_kind", row.id)
    return fence.usable(row, records, _today())


def _place_row(scanned, place_id):
    place = find_row(scanned, place_id)
    if place.kind != "place":
        raise RegisterRefused("register_place_not_place", place.id)
    if place.problems:
        raise RegisterRefused("register_row_invalid", place.id)
    return place


def cmd_paste(args) -> int:
    _cfg, _root, scanned = open_register(args.register)
    row = _paste_row(args.row, scanned, ledger.read_confirmed(args.register))
    places = places_mod.place_rows(scanned["rows"])
    if not places:
        raise RegisterRefused("register_paste_nothing")
    found = places_mod.bundle(row, places)
    value = {"register": args.register, "row": row.id, "kind": row.kind, "places": found,
             "notes": ["THTH は外の場所の頁を読まず、管理画面にも入りません。貼るのは持ち主です",
                       "字数の目安は確かめていない数です（収まらないときは文を短くして貼ってください）"]}
    if args.json:
        _print(value)
        return 0
    for one in found:
        mark = "貼った" if one["pasted"] else "まだ"
        fits = "" if one["fits"] else f"（目安 {one['max_chars']} 字を超えています・{one['chars']} 字）"
        print(f"[{mark}] {one['label']}（{one['place']}）: {one['field']}{fits}")
        print("-----")
        print(one["text"])
        print("-----")
    print(f"  貼ったら thth {COMMAND} pasted {args.register} <場所の行> --for {row.id} --by <名前> で記録します")
    for line in value["notes"]:
        print(f"  {line}")
    return 0


def cmd_pasted(args) -> int:
    by = _by(args)
    _cfg, root, scanned = open_register(args.register)
    row = _paste_row(args.for_row, scanned, ledger.read_confirmed(args.register))
    place = _place_row(scanned, args.place)
    entries = dict(places_mod.synced(place.head) or {})
    entries.pop(row.id, None)
    entries[row.id] = form.fingerprint(row.text)
    today = jst.today_str()
    _log_ready()
    confirm_mod.write_row(root, place, confirm_mod.with_head(place.text, {"synced_on": today,
                                                                      "synced_row": places_mod.synced_text(entries)}))
    _record("pasted", args.register, by, {"place": [None, place.id], "row": [None, row.id]})
    value = {"register": args.register, "place": place.id, "row": row.id, "synced_on": today}
    if args.json:
        _print(value)
        return 0
    print(f"外の場所 {place.id} に行 {row.id} の今の中身を貼ったと記録しました（{today}）")
    return 0


def cmd_compare(args) -> int:
    _cfg, _root, scanned = open_register(args.register)
    row = _paste_row(args.for_row, scanned, ledger.read_confirmed(args.register))
    place = _place_row(scanned, args.place)
    outside = sys.stdin.read(places_mod.COMPARE_MAX + 1)
    if not outside.strip() or len(outside) > places_mod.COMPARE_MAX:
        raise RegisterRefused("register_compare_empty")
    found = places_mod.compare(row, outside)
    value = {"register": args.register, "place": place.id, "row": row.id, **found,
             "notes": ["どちらが正しいかは THTH が決めません。違いがあれば持ち主に聞いてください。貼られた文は残していません"]}
    if args.json:
        _print(value)
        return 0
    if found["same"]:
        print(f"外の場所 {place.id} の文の日付と時刻は、台帳の行 {row.id} と合っています")
    else:
        print(f"外の場所 {place.id} の文と台帳の行 {row.id} に違いがあります:")
        for side, label in (("only_register", "台帳にあって外に無い"), ("only_outside", "外にあって台帳に無い")):
            for key, values in found[side].items():
                if values:
                    print(f"  {label}: {key} {values}")
    print(f"  {value['notes'][0]}")
    return 0


# ------------------------------------------------------------------ 退出

def purge_for_leave(account: str, project, *, by: str) -> list:
    """その project の最後の口座が抜けるとき、その project の台帳の控えと記録を消す（台帳のフォルダは消さない）。"""
    from .. import accounts
    if not accounts.name_is_safe(project or ""):
        return []
    names = ledger.of_project(project)
    if not names:
        return []
    for other in accounts.list_account_names():
        if other == account:
            continue
        try:
            if accounts.load_account(other).get("project") == project:
                return []
        except accounts.AccountError:
            return []
    removed = []
    for name in names:
        done = remove_state(name)
        admin_log.append(ACTION_PREFIX + "removed", name, {"media": "register"}, by=by,
                         diff={"deleted": [[], done], "via": [None, "leave"]})
        removed.append(name)
    return removed


# ------------------------------------------------------------------ parser

PARENT_HELP = "台帳の機械（形の点検・推測を材料に使わせない柵・認める操作）"


def register(sub) -> None:
    import argparse
    parent = sub.add_parser(COMMAND, help=PARENT_HELP, description=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter)
    verbs = parent.add_subparsers(dest="register_command", required=True)

    p = verbs.add_parser("add", help="台帳のフォルダを控える")
    p.add_argument("register", metavar="台帳")
    p.add_argument("--path", required=True, metavar="フォルダ")
    p.add_argument("--project", required=True)
    p.add_argument("--kind", required=True, choices=ledger.KINDS)
    p.add_argument("--by", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_run(cmd_add))

    for verb, help_text, func in (("check", "全部の行の形を点検する", cmd_check),
                                  ("show", "行ごとに材料に使えるか", cmd_show)):
        p = verbs.add_parser(verb, help=help_text)
        p.add_argument("register", metavar="台帳")
        p.add_argument("--json", action="store_true")
        p.set_defaults(func=_run(func))

    p = verbs.add_parser("use", help="柵: その行を材料に使えるか")
    p.add_argument("register", metavar="台帳")
    p.add_argument("row", metavar="行")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_run(cmd_use))

    p = verbs.add_parser("confirm", help="本人が認めた（営業の時間と臨時は二段確認）")
    p.add_argument("register", metavar="台帳")
    p.add_argument("row", metavar="行")
    p.add_argument("--by", required=True)
    p.add_argument("--confirm", default=None, metavar="D")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_run(cmd_confirm))

    p = verbs.add_parser("deny", help="本人が違うと言った（行は消さない）")
    p.add_argument("register", metavar="台帳")
    p.add_argument("row", metavar="行")
    p.add_argument("--by", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_run(cmd_deny))

    p = verbs.add_parser("remove", help="THTH の控えと認めた記録を消す（台帳のフォルダは消さない）")
    p.add_argument("register", metavar="台帳")
    p.add_argument("--by", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_run(cmd_remove))

    p = verbs.add_parser("triggers", help="今出す頃のきっかけ（臨時・催し・営業の時間の変わり・新しい品・季節の品）")
    p.add_argument("register", metavar="台帳")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_run(cmd_triggers))

    p = verbs.add_parser("draft", help="きっかけから queue の下書き（承認されていない形・出さない期限つき）")
    p.add_argument("register", metavar="台帳")
    p.add_argument("--to", required=True, metavar="口座")
    p.add_argument("--row", default=None, metavar="行")
    p.add_argument("--trigger", default=None, choices=triggers_mod.TRIGGERS)
    p.add_argument("--by", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_run(cmd_draft))

    p = verbs.add_parser("holes", help="穴の一覧（読むだけ）")
    p.add_argument("register", metavar="台帳")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_run(cmd_holes))

    p = verbs.add_parser("ask", help="今聞く穴を上限まで選び、問いの記録に足す")
    p.add_argument("register", metavar="台帳")
    p.add_argument("--by", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_run(cmd_ask))

    p = verbs.add_parser("answer", help="答えを問いの記録に足す（推測の行に no なら違うと言った・yes なら認める）")
    p.add_argument("register", metavar="台帳")
    p.add_argument("question", metavar="問い")
    p.add_argument("--result", required=True, choices=questions_mod.RESULTS)
    p.add_argument("--by", required=True)
    p.add_argument("--asked", default=None, metavar="文", help="聞いた言葉（1 行・200 字まで・任意）")
    p.add_argument("--said", default=None, metavar="文", help="答えの要約（1 行・200 字まで・任意）")
    p.add_argument("--row", default=None, metavar="行", help="答えから足した行の番号（任意）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_run(cmd_answer))

    p = verbs.add_parser("questions", help="問いの数・答えの数・答えから増えた行の数")
    p.add_argument("register", metavar="台帳")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_run(cmd_questions))

    p = verbs.add_parser("paste", help="外の場所ごとに貼る文と、貼ったかの点検表（網に出ない）")
    p.add_argument("register", metavar="台帳")
    p.add_argument("row", metavar="行")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_run(cmd_paste))

    p = verbs.add_parser("pasted", help="持ち主が貼ったと記録する（場所の行の頭の synced_on・synced_row だけを書く）")
    p.add_argument("register", metavar="台帳")
    p.add_argument("place", metavar="場所の行")
    p.add_argument("--for", dest="for_row", required=True, metavar="行")
    p.add_argument("--by", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_run(cmd_pasted))

    p = verbs.add_parser("compare", help="持ち主が写した外の場所の今の文（標準入力）と台帳の日付と時刻を比べる")
    p.add_argument("register", metavar="台帳")
    p.add_argument("place", metavar="場所の行")
    p.add_argument("--for", dest="for_row", required=True, metavar="行")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_run(cmd_compare))
