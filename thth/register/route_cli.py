"""台帳の器 段 3 の命令（設計 §13 の `register judge`・`export`・`accept`・`take` と、封の確かめ `verify`・
持ち主の鍵 `key`。`use --outlet` は `cli.py` の `use` を広げた物）。

    thth register judge <台帳> <種類:番号> --question <問い> (--word <語> | --conclusion good|bad|pending)
                        [--unit <指し先>] [--value <欄>] [--reason-kind <種類>] [--reason-text <説明>] [--how <照らし方>]
                        [--until <日>] [--against <種類:番号> ...] --by <名前> [--json]
        判断を 1 件足す（足すだけ）。受け入れる（accept）までは、広げる向きには効かない
    thth register judge <台帳> <種類:番号> --stop|--resume [--outlet <出し口>] [--unit <指し先>] [--until <日>] --by <名前>
        止める（封を待たずに効く）・止めない（広げるので受け入れが要る）
    thth register judge <台帳> --baseline --outlet <出し口> --by <名前>
        基準: 今の版の記録を、その出し口の条件の問いごとに 1 回で受ける（受け入れるまで効かない）
    thth register judge <台帳> --carry <判断> --to <今の版|新しい指し先> --changed-by <直した者> --by <名前>
        結び直す: 前の判断の元の検収を、新しい版へ結ぶ新しい判断（直した者と同じ者は結べない）
    thth register take <台帳> --common <共通の台帳> [--record <種類:番号> ...] --by <名前> [--json]
        共通の記録の今の版を、まとめて採る（受け入れるまで材料は変わらない）
    thth register export <台帳> --outlet <出し口> --to <先> --as-of <日時> [--common <共通の台帳>] [--check] [--json]
        封の中の判断で材料を書く（鍵は使わない・狭める物は封を待たない・一覧に無い旧い材料があれば止まる）。
        読者に出る出し口では、出さない欄を書かず・差し込みを置き換え・伏せ方の粒で外す（出し口の宣言の kinds.<種類>）
    thth register accept <台帳> --outlet <出し口> --to <先> --as-of <日時> --by <名前> [--confirm D] [--common …] [--json]
        受け入れて封をする（二段: 1 回めは変わる物と印を見せる・2 回めに --confirm で材料を書いて封をする）
    thth register verify <台帳> --outlet <出し口> --material <先> [--now <日時>] (--previous <前に配った物> | --no-previous)
                         [--common <共通の台帳>] [--publish <配る物の置き場>] [--json]
        公開の直前の確かめ（CI・手元の配信）。公開鍵は環境の変数 THTH_REGISTER_SEAL_PUBLIC_KEY か、手元の鍵だけ。
        緑なら --publish の置き場に、配る物（封と、配った時の判断の記録の頭）を書く（次の回の --previous）
    thth register key init|show [--json]
        持ち主の鍵（秘密の鍵は ~/.config/thth/ に。値は出さない・出すのは公開鍵と指紋）

網に出ない。
"""
from __future__ import annotations

import json

from .. import admin_log, jst
from . import route as R, seal as S
from .adopt_cli import root_of
from .errors import RegisterRefused


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, default=str))


def _at() -> str:
    return jst.now_jst().isoformat(timespec="seconds")


def _by(args) -> str:
    try:
        return admin_log.actor(getattr(args, "by", None))
    except ValueError:
        raise RegisterRefused("register_args_invalid", "by") from None


WHY_JA = {
    "stopped": "止めている",
    "not_accepted": "判断はあるが、まだ受け入れていない（封の外）",
    "no_judgment": "判断が無い",
    "no_inspection_for_new_version": "新しい版への検収が無い（中身が変わった）",
    "against_changed": "照らした物（根拠など）の版が変わった",
    "expired": "判断の期限が切れた",
    "not_permitted": "この出し口へ出してよいの判断が無いか、だめ",
    "reason_kind_not_allowed": "理由の種類が、留保にしてよい種類に入らない",
    "only_reservation": "事実としては使えない（留保としてだけ使える）",
    "record_problem": "記録の形に問題がある",
    "unit_ambiguous": "内側の単位が 1 つに決まらない",
    "kind_not_in_outlet": "この出し口の種類の記録ではない",
    "no_seal": "この出し口はまだ受け入れて封をしていない",
    "insert_unusable": "差し込みの先が、この出し口で事実として使えない（使えない・留保だけ・止めた・欄が無い・出さない欄）",
    "private_unremovable": "出さない欄を、元の書き方のまま除けない（枚の書き方を、上の段の鍵ごとの形にする）",
    "insert_unplaceable": "差し込みの値を、元の書き方のまま置けない",
    "hidden_with_record": "伏せ方が記録ごとで、同じ記録の内側の単位が使えなくなった",
}


def cmd_judge(args) -> int:
    by = _by(args)
    root = root_of(args.register)
    at = _at()
    if args.baseline:
        if not args.outlet:
            raise RegisterRefused("register_args_invalid", "outlet")
        made = R.baseline(root, args.outlet, by=by, at=at)
        value = {"baseline": args.outlet, "judgments": len(made)}
        _print(value) if args.json else print(f"基準の判断を {len(made)} 件足しました（{args.outlet}）。"
                                              "受け入れる（thth register accept）までは材料に効きません")
        return 0
    if args.carry:
        body = R.carry(root, args.carry, to=args.to, by=by, at=at, changed_by=args.changed_by)
    elif args.stop or args.resume:
        if not args.record:
            raise RegisterRefused("register_args_invalid", "record")
        body = R.stop(root, args.record, by=by, at=at, outlet=args.outlet, lift=args.resume, unit=args.unit,
                      until=args.until, reason_text=args.reason_text)
    else:
        if not args.record or not args.question:
            raise RegisterRefused("register_args_invalid", "record" if not args.record else "question")
        body = R.judge(root, args.record, args.question, by=by, at=at, unit=args.unit, value=args.value,
                       word=args.word, conclusion=args.conclusion, reason_kind=args.reason_kind,
                       reason_text=args.reason_text, how=args.how, until=args.until, against=args.against)
    value = {"judgment": body["id"], "question": body["question"], "target": body["target"],
             "conclusion": (body.get("answer") or {}).get("conclusion"), "word": (body.get("answer") or {}).get("word")}
    if args.json:
        _print(value)
        return 0
    narrowing = value["conclusion"] == "bad"
    print(f"判断を足しました: {value['judgment']}（{value['question']}・{value['target'].get('record')}）。"
          + ("狭める判断なので、封を待たずに次の材料と確かめに効きます" if narrowing else
             "受け入れる（thth register accept）までは、広げる向きには効きません"))
    return 0


def cmd_take(args) -> int:
    by = _by(args)
    root = root_of(args.register)
    value = R.take(root, args.common, args.record, by=by, at=_at())
    if args.json:
        _print(value)
        return 0
    for t in value["taken"]:
        print(f"採りました: 共通の {t['record']}（{(t['from'] or '初めて')[:12]} → {t['to'][:12]}）")
    for ref in value["same"]:
        print(f"変わっていません: 共通の {ref}（もう今の版を採っています）")
    if value["taken"]:
        print("受け入れる（thth register accept）までは、材料は前の版のままです")
    return 0


def _show_changes(value: dict) -> None:
    ch = value.get("changes") or {}
    print(f"  外れる {len(ch.get('leaving', []))} 件・入る {len(ch.get('entering', []))} 件")
    for item in ch.get("leaving", [])[:20]:
        print(f"    外れる {item}")
    for item in ch.get("entering", [])[:20]:
        print(f"    入る   {item}")
    for p in value.get("pending_takes") or []:
        print(f"  採り入れ待ち: {p['record']}（採った版 {p['taken'][:12]}・共通の今の版 {p['now'][:12]}）")


def cmd_export(args) -> int:
    root = root_of(args.register)
    value = R.export(root, args.outlet, args.to, args.as_of, common_root=args.common, check=args.check)
    if args.json:
        _print(value)
        return 0
    verb = "比べました（書いていません）" if args.check else "書きました"
    print(f"材料を{verb}: {value['outlet']}・世代 {value['generation']}・{value['files']} ファイル・"
          f"{value['items']} 件 → {value['to']}")
    _show_changes(value)
    unusable = [e for e in value.get("excluded") or [] if e.get("why") == "insert_unusable"]
    for e in unusable[:20]:
        print(f"  差し込みが使えず外した {e['record']}: "
              + "・".join(f"{i.get('insert', i['field'])}（{i['why']}）" for i in e.get("inserts") or []))
    if value["differs"]:
        print(f"  置いてある材料と違うファイル {len(value['differs'])}: " + "・".join(value["differs"][:10]))
    if not value["same_as_seal"]:
        print("  封のときの材料と違います（狭める物が効いた）。このまま配るには、受け入れて封をし直すか、"
              "出し口の宣言で on_narrow を drop にします")
    if value["stale"]:
        print(f"  一覧に無い旧い材料 {len(value['stale'])}: " + "・".join(value["stale"][:10]))
    return 0


def cmd_accept(args) -> int:
    _by(args)   # 誰が受け入れたかは、受け入れた判断の行（by）と台帳の git に残る
    root = root_of(args.register)
    if args.confirm is None:
        plan = R.accept_plan(root, args.outlet, args.to, args.as_of, common_root=args.common)
        value = {"stage": 1, "outlet": plan["outlet"], "generation": plan["body"]["generation"],
                 "digest": plan["digest"], "new_judgments": plan["new_judgments"], "changes": plan["changes"],
                 "pending_takes": plan["result"]["pending_takes"], "stale": plan["stale"],
                 "files": len(plan["body"]["outputs"]), "items": len(plan["body"]["items"])}
        if args.json:
            _print(value)
            return 1
        print(f"受け入れの 1 段めです（{plan['outlet']}・世代 {value['generation']}）。新しい判断 "
              f"{len(plan['new_judgments'])} 件:")
        for j in plan["new_judgments"][:40]:
            print(f"  {j['id']}\t{j['question']}\t{(j['target'] or {}).get('record')}\t"
                  f"{j['word'] or j['conclusion']}\t{j['by']}")
        _show_changes(value)
        print(f"持ち主に見せ、よいと言われたら --confirm {plan['digest']} を付けて打ち直してください")
        return 1
    value = R.accept(root, args.outlet, args.to, args.as_of, confirm=args.confirm, at=_at(), common_root=args.common)
    if args.json:
        _print(value)
        return 0
    print(f"受け入れて封をしました: {value['outlet']}・世代 {value['generation']}・判断 {value['judgments']} 件・"
          f"{value['files']} ファイル（封は {value['seal']}）")
    _show_changes(value)
    return 0


PROBLEM_JA = {
    "no_seal": "封がありません",
    "seal_shape": "封の形が崩れています",
    "public_key": "確かめに使う公開鍵がありません（環境の変数 THTH_REGISTER_SEAL_PUBLIC_KEY）",
    "signature": "封の署名が、持ち主の公開鍵で確かめられません",
    "judgments_rewritten": "判断の記録の、受け入れた所までが書き換わっています",
    "previous_unknown": "前に配った世代の封を読んでいません（--previous か、初めてなら --no-previous）",
    "previous_signature": "前に配った世代の封を、持ち主の公開鍵で確かめられません",
    "previous_other_outlet": "前に配った世代の封が、別の出し口の物です",
    "previous_not_deployed": "前に配った物に、配った時の判断の記録の頭がありません（封だけの写しは読みません）",
    "seal_other_outlet": "封が、別の出し口に向けて署名された物です",
    "generation_back": "世代の番号が、前に配った世代より戻っています",
    "generation_reused": "前に配った世代と同じ番号で、中身が違う封です",
    "judgments_not_continued": "判断の記録が、前に配った世代の続きになっていません（行が消えたか書き換わった）",
    "decl_changed": "種類・憲法・出し口の宣言が、封のときから変わっています",
    "material_mismatch": "置いてある材料が、封の出力と合いません",
    "narrowed_after_seal": "封のあとで使えなくなった物が、材料に載っています（止めた・期限が切れた・中身が変わった）",
    "widened_after_seal": "封の中の判断では使えない物が、使える側に出ています",
    "stale_material": "一覧に無い旧い材料が、書き出し先にあります（. で始まるフォルダの中も見ます。見ない物は出し口の宣言の ignore）",
    "inputs_changed": "材料に載せた記録か差し込んだ先の版が、封のときから変わっています（受け入れて封をし直す）",
}


def cmd_verify(args) -> int:
    root = root_of(args.register)
    now = args.now or _at()
    value = R.verify(root, args.outlet, args.material, now, previous_path=args.previous,
                     no_previous=args.no_previous, common_root=args.common, publish=args.publish)
    if not args.json:
        if value["ok"]:
            print(f"確かめました: {args.outlet}・世代 {value.get('generation')}（公開鍵: {value.get('key')}）")
            if value.get("published"):
                print(f"配る物を置きました: {value['published']}（次の回の --previous）")
        for p in value["problems"]:
            print(f"  赤: {PROBLEM_JA.get(p['word'], p['word'])}"
                  + (f"（{'・'.join(str(i.get('item', i)) if isinstance(i, dict) else str(i) for i in (p.get('items') or p.get('files') or []))[:300]}）"
                     if p.get("items") or p.get("files") else ""))
        for w in value["warnings"]:
            print(f"  知らせ: {w['word']}")
    elif value["ok"]:
        _print(value)
    if not value["ok"]:
        raise RegisterRefused("register_seal_unverified", value["problems"][0]["word"],
                              problems=value["problems"], warnings=value["warnings"], key=value.get("key"),
                              generation=value.get("generation"))
    return 0


def cmd_key(args) -> int:
    if args.key_command == "init":
        value = S.init_key()
    else:
        value = S.public_info()
    value = dict(value, place=S.key_path())
    if args.json:
        _print(value)
        return 0
    if args.key_command == "init":
        print(f"持ち主の鍵を作りました（秘密の鍵は {value['place']}・値は出しません）。")
    print("公開鍵（CI の変数 THTH_REGISTER_SEAL_PUBLIC_KEY に置く物）:")
    print(value["public_key_base64"])
    print(f"指紋 {value['fingerprint'][:16]}")
    return 0


def register_parsers(verbs, run) -> None:
    p = verbs.add_parser("judge", help="判断を足す（止める・基準・結び直すも）")
    p.add_argument("register", metavar="台帳")
    p.add_argument("record", nargs="?", default=None, metavar="種類:番号")
    p.add_argument("--question", default=None)
    p.add_argument("--unit", default=None, metavar="指し先")
    p.add_argument("--value", default=None, metavar="欄")
    p.add_argument("--word", default=None)
    p.add_argument("--conclusion", default=None, choices=("good", "bad", "pending"))
    p.add_argument("--reason-kind", default=None)
    p.add_argument("--reason-text", default=None)
    p.add_argument("--how", default=None)
    p.add_argument("--until", default=None, metavar="日")
    p.add_argument("--against", action="append", default=None, metavar="種類:番号")
    which = p.add_mutually_exclusive_group()
    which.add_argument("--stop", action="store_true")
    which.add_argument("--resume", action="store_true")
    which.add_argument("--baseline", action="store_true")
    which.add_argument("--carry", default=None, metavar="判断")
    p.add_argument("--outlet", default=None)
    p.add_argument("--to", default=None, metavar="版か指し先")
    p.add_argument("--changed-by", default=None)
    p.add_argument("--by", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run(cmd_judge))

    p = verbs.add_parser("take", help="共通の記録の今の版を、まとめて採る")
    p.add_argument("register", metavar="台帳")
    p.add_argument("--common", required=True, metavar="共通の台帳")
    p.add_argument("--record", action="append", default=None, metavar="種類:番号")
    p.add_argument("--by", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run(cmd_take))

    for verb, help_text in (("export", "封の中の判断で材料を書く（--check は比べるだけ）"),
                            ("accept", "受け入れて封をする（二段）")):
        p = verbs.add_parser(verb, help=help_text)
        p.add_argument("register", metavar="台帳")
        p.add_argument("--outlet", required=True)
        p.add_argument("--to", required=True, metavar="先")
        p.add_argument("--as-of", required=True, metavar="日時")
        p.add_argument("--common", default=None, metavar="共通の台帳")
        if verb == "export":
            p.add_argument("--check", action="store_true")
            p.set_defaults(func=run(cmd_export))
        else:
            p.add_argument("--by", required=True)
            p.add_argument("--confirm", default=None, metavar="D")
            p.set_defaults(func=run(cmd_accept))
        p.add_argument("--json", action="store_true")

    p = verbs.add_parser("verify", help="公開の直前に、封と公開鍵で確かめる（CI）")
    p.add_argument("register", metavar="台帳")
    p.add_argument("--outlet", required=True)
    p.add_argument("--material", required=True, metavar="先")
    p.add_argument("--now", default=None, metavar="日時")
    prev = p.add_mutually_exclusive_group()
    prev.add_argument("--previous", default=None, metavar="前の封")
    prev.add_argument("--no-previous", action="store_true")
    p.add_argument("--common", default=None, metavar="共通の台帳")
    p.add_argument("--publish", default=None, metavar="配る物の置き場")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run(cmd_verify))

    p = verbs.add_parser("key", help="持ち主の封の鍵（作る・公開鍵を見る）")
    keys = p.add_subparsers(dest="key_command", required=True)
    for verb in ("init", "show"):
        q = keys.add_parser(verb)
        q.add_argument("--json", action="store_true")
        q.set_defaults(func=run(cmd_key))
