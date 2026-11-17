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
    thth register fix <台帳> <種類:番号> --set 欄=値 ... --reason-text <説明> --outlet <出し口|all> --to <先>
                      --as-of <日時> --by <名前> [--how <照らし方>] [--against <種類:番号> ...] [--common …] [--json]
        段 11: 訂正の 1 本の命令（`fix.py`）。外れる 0・入る 0・新しい判断だけで、出し口の宣言が fix_seal を許すときだけ
        封まで進む（rc 0）。それ以外は受け入れの 1 段めで止め、一覧と印を出す（rc 1）
    （段 11）export・accept・verify の --outlet all: 宣言の全部の出し口。--to・--material は根で、出し口ごとに
        <根>/<dest>。verify の --previous・--publish はフォルダで、出し口ごとに thth-seal.<出し口>.json。1 つでも赤なら rc 1。
        export --check は違い（differs・stale・mixed・pending_takes・封と違う）があれば rc 1
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
    thth register used <台帳> <種類:番号> [--json]
        使った先（読むだけ）: どの出し口の・どの世代の材料に・何として載ったか・差し込みで使われたか（封から）と、
        「から作った」で指す記録（来歴の控え）。今の版が最後の世代に載った版と違えば「材料が古い」
    thth register published <台帳> --outlet <出し口> --list <一覧> [--record] [--now <日時>]
                            (--previous <前に配った控えの頭> | --no-previous) [--publish <配る控えの頭の置き場>] [--json]
        出した URL の関門（build のあとの一覧と、出し口ごとの追記だけの控え .thth/published/<出し口>.jsonl を比べる）。
        前に出した URL が一覧に無い・許していない転送・転送先が無い・二重・控えの書き換えは赤（rc 1）。
        --record を付けたときだけ、緑なら控えに足す（付けなければ台帳に書かない）
    thth register key init|show [--json]
        持ち主の鍵（秘密の鍵は ~/.config/thth/ に。値は出さない・出すのは公開鍵と指紋）

網に出ない。
"""
from __future__ import annotations

import json
import os

from .. import admin_log, jst
from . import route as R, seal as S
from .adopt_cli import root_of
from .errors import RegisterRefused, report_channel


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
    if args.more and (args.baseline or args.carry or args.stop or args.resume):
        raise RegisterRefused("register_args_invalid", "record")   # 並べられるのは足す判断だけ
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
        bodies = R.judge_many(root, [args.record] + list(args.more or []), args.question, by=by, at=at,
                              unit=args.unit, value=args.value, word=args.word, conclusion=args.conclusion,
                              reason_kind=args.reason_kind, reason_text=args.reason_text, how=args.how,
                              until=args.until, against=args.against)
        if len(bodies) > 1:
            # 5.23.0: 記録を並べた judge（同じ問い・同じ理由を 1 回で）
            value = {"judgments": [{"judgment": b["id"], "record": b["target"]["record"]} for b in bodies],
                     "question": args.question, "count": len(bodies)}
            if args.json:
                _print(value)
                return 0
            print(f"判断を {len(bodies)} 件足しました（{args.question}）: " + "・".join(b["target"]["record"] for b in bodies))
            return 0
        body = bodies[0]
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
    for ref in value.get("unchecked") or []:
        print(f"  検めた跡がありません: 共通の {ref}（共通の台帳の側に、今の版への「よい」の判断が無い。採るのは止めません）")
    if value["taken"]:
        print("受け入れる（thth register accept）までは、材料は前の版のままです")
    show_impact(value.get("impact"))
    return 0


def show_impact(found) -> None:
    """段 11 の F: 影響の候補（判断は無効にしていない。判断し直すかは係が決める）。段 12: 出した投稿と確かめ直しの候補。"""
    if not found:
        return
    if found.get("count"):
        print(f"  影響の候補 {found['count']} 件（版の変わった記録 {len(found['changed'])} 件と指す欄でつながった記録。"
              "判断は無効にしていません）:")
        for c in found["candidates"][:20]:
            print(f"    {c['record']}（{c['via']} の {c.get('field') or '?'}・{c['why']}）")
    show_posts(found.get("posts"), found.get("recheck"))


def show_posts(posts, recheck, indent: str = "  ") -> None:
    """段 12 の 2・3（読むだけ）: 直した記録を seeds で指す出した投稿と、事実の指摘から引いた確かめ直しの候補。"""
    if posts and posts.get("count"):
        print(f"{indent}この記録を指す出した投稿 {posts['count']} 件（消す・直す・訂正を出すかは窓口が決めます）:")
        for p in posts["items"][:20]:
            print(f"{indent}  {p['account']}（{p.get('media') or '?'}）{p['post_id']}・{p.get('sent_at') or '?'}"
                  f"・{' '.join(p['seeds'])}")
    if recheck and recheck.get("count"):
        print(f"{indent}確かめ直しの候補 {recheck['count']} 件（読者が事実を指摘した返信の付いた投稿が指す記録）:")
        for r in recheck["items"][:20]:
            print(f"{indent}  {r['record']} ← {r['account']} {r['post_id']} の返信 {r['reply_id']}"
                  f"（印 {r.get('marked_at') or '?'}・{r.get('marked_by') or '?'}）")
    # 投稿と確かめ直しは読む口座が違う（投稿は直した記録があれば全口座・確かめ直しは印のある口座だけ・監査 5.24.0 の
    # 軽 3）ので、両方の言えない物を合わせ、同じ (口座, わけ) は 1 回だけ出す（検収 段 12 の残りの危 1）。
    seen = set()
    for c in [*((posts or {}).get("cannot_say") or []), *((recheck or {}).get("cannot_say") or [])]:
        if c.get("why") in ("register_not_registered", "common_register"):
            continue      # 控えの無い台帳（試しの写しなど）・共通の台帳は口座を持たない
        key = (c.get("account"), c.get("why"))
        if key in seen:
            continue
        seen.add(key)
        print(f"{indent}言えない: {c.get('account') or '口座'}（{c['why']}）")


def _show_changes(value: dict) -> None:
    ch = value.get("changes") or {}
    print(f"  外れる {len(ch.get('leaving', []))} 件・入る {len(ch.get('entering', []))} 件")
    for item in ch.get("leaving", [])[:20]:
        print(f"    外れる {item}")
    for item in ch.get("entering", [])[:20]:
        print(f"    入る   {item}")
    for p in value.get("pending_takes") or []:
        print(f"  採り入れ待ち: {p['record']}（採った版 {p['taken'][:12]}・共通の今の版 {p['now'][:12]}）")


def cmd_fix(args) -> int:
    """段 11 の B: 訂正の 1 本の命令（`fix.py` の頭の注）。封まで進めば rc 0、1 段めで止まれば rc 1。"""
    from . import fix as F
    from .adopt_cli import _pairs
    by = _by(args)
    root = root_of(args.register)
    value = F.fix(root, args.record, _pairs(args.set), outlet=args.outlet, to=args.to, as_of=args.as_of, by=by,
                  at=_at(), reason_text=args.reason_text, how=args.how, against=args.against,
                  common_root=args.common)
    if args.json:
        _print(value)
        return 0 if value["sealed"] else 1
    print(f"直しました: {value['record']}（{value['path']}）。判断を {len(value['judgments'])} 件足しました")
    for n in value["named"]:
        mark = ("判断を足した: " + "・".join(n["questions"])) if n["judged"] else f"足していない（{n['why']}）"
        print(f"  名指し [{n['outlet']}] {n['item']} — {mark}")
    for p in value.get("prior_unjudged") or []:
        print(f"  先に判断の無い直しがありました [{p['outlet']}] {p['item']} — この命令の判断がその直しも覆うので、"
              "封へは進めません（中身を確かめてから accept）")
    show_impact(value.get("impact"))
    if value["sealed"]:
        for name, o in value["outlets"].items():
            print(f"  [{name}] 受け入れて封をしました: 世代 {o['generation']}・{o['files']} ファイル（封は {o['seal']}）")
        return 0
    for name, o in value["outlets"].items():
        print(f"  [{name}] 1 段めで止めました（{'・'.join(o['stopped_because']) or '—'}）: 世代 {o['generation']}・"
              f"新しい判断 {len(o['new_judgments'])} 件")
        _show_changes(o)
    print(f"持ち主に見せ、よいと言われたら thth register accept … --outlet {args.outlet} --confirm {value['digest']} "
          "で封をしてください")
    return 1


def check_found(value: dict) -> list:
    """段 11 の D: `export --check` が見つけた違いの語（空なら同じ）。CI が `--json` の中身を読まずに、終わりの番号で
    止められるように（こぴちゃの報告 #7899）。"""
    found = [word for word in ("differs", "stale", "mixed", "pending_takes", "common_not_in_seal") if value.get(word)]
    if not value.get("same_as_seal", True):
        found.append("not_same_as_seal")
    return found


def cmd_export(args) -> int:
    if args.check:
        def run():
            with R.reading():   # 比べるだけ（何も書かない）の export だけ、同じ物を読み直さない
                return _cmd_export(args)
        return _noted("export_check", args, run)
    return _cmd_export(args)


def _cmd_export(args) -> int:
    root = root_of(args.register)
    if R.is_all(args.outlet):
        return _export_all(args, root)
    value = R.export(root, args.outlet, args.to, args.as_of, common_root=args.common, check=args.check)
    found = check_found(value) if args.check else []
    if args.check:
        value["found"] = found
        args._noted_value = value
    if args.json:
        _print({**value, **report_channel("register_material_stale")} if found else value)
        return 1 if found else 0
    _export_lines(value, args.check)
    if found:
        print(f"  違いがあります（{'・'.join(found)}）。終わりの番号は 1 です")
    return 1 if found else 0


def _each(names, run) -> tuple:
    """出し口ごとに回す。断りもその出し口の結果として持ち、ほかの出し口は続ける。`({名前: 結果}, 全部が緑か)`。"""
    out, ok = {}, True
    for name in names:
        try:
            value, good = run(name)
        except RegisterRefused as exc:
            value, good = {"ok": False, "code": exc.code, "detail": exc.detail, **exc.extra}, False
        out[name] = value
        ok = ok and good
    return out, ok


def _all_head(args, names, ok) -> None:
    print(f"出し口の全部（{'・'.join(names)}）: " + ("緑" if ok else "赤があります（終わりの番号は 1）"))


def red_channel(results: dict, default: str = "register_material_stale") -> dict:
    """`--all` の赤（rc 1）の JSON に足す報告の 1 行（5.23.0）。符丁は最初の断りの物か、既定の符丁。"""
    code = next((v["code"] for v in results.values() if v.get("ok") is False and isinstance(v.get("code"), str)
                 and v["code"].startswith("register_")), default)
    return report_channel(code)


def _export_all(args, root: str) -> int:
    """段 11 の C: `export --outlet all --to <根>`。出し口ごとに `<根>/<dest>` へ。1 つでも赤なら全体が赤（rc 1）。"""
    names = R.all_outlets(root)

    def one(name):
        value = R.export(root, name, R.dest_under(root, name, args.to), args.as_of, common_root=args.common,
                         check=args.check)
        found = check_found(value) if args.check else []
        if args.check:
            value["found"] = found
        return value, not found
    results, ok = _each(names, one)
    args._noted_value = {"outlets": results}
    if args.json:
        _print({"outlet": R.ALL, "ok": ok, "outlets": results, **({} if ok else red_channel(results))})
        return 0 if ok else 1
    _all_head(args, names, ok)
    for name, value in results.items():
        print(f"[{name}]")
        if "code" in value and value.get("ok") is False:
            print(f"  断り {value['code']}（{value.get('detail') or ''}）")
            continue
        _export_lines(value, args.check)
        if value.get("found"):
            print(f"  違いがあります（{'・'.join(value['found'])}）")
    return 0 if ok else 1


def _export_lines(value: dict, check: bool) -> None:
    verb = "比べました（書いていません）" if check else "書きました"
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
    if value.get("common_unchecked"):
        print("  共通の台帳を読まずに比べました（共通から採った物は封のときの指紋で照らしています。共通の側で止めた・直した物は --common を渡すと分かります）")
    if value.get("common_not_in_seal"):
        print(f"  封の出力と結べない共通の記録 {len(value['common_not_in_seal'])}（中身を言えないので違いに数えます。"
              "--common を渡すか、受け入れて封をし直します）: " + "・".join(value["common_not_in_seal"][:10]))


def _accept_all(args, root: str) -> int:
    """段 11 の C: `accept --outlet all --to <根>`。1 段めは出し口ごとの一覧と、全部を合わせた印。2 段めは同じ印のとき
    だけ、出し口ごとに受け入れて封をする（読者の出し口が先）。印が違えば何も書かない。"""
    names = R.all_outlets(root)
    plans = {name: R.accept_plan(root, name, R.dest_under(root, name, args.to), args.as_of, common_root=args.common)
             for name in names}
    combined = R.all_digest({name: plan["digest"] for name, plan in plans.items()})
    if args.confirm is None:
        value = {"stage": 1, "outlet": R.ALL, "digest": combined,
                 "outlets": {name: _stage1(plan) for name, plan in plans.items()}}
        if args.json:
            _print(value)
            return 1
        print(f"受け入れの 1 段めです（出し口の全部: {'・'.join(names)}）")
        for name, plan in plans.items():
            _stage1_lines(_stage1(plan), plan)
        print(f"持ち主に見せ、よいと言われたら --confirm {combined} を付けて打ち直してください")
        return 1
    if args.confirm != combined:
        raise RegisterRefused("register_confirm_mismatch", "confirm", digest=combined)
    at = _at()
    # 全部の出し口の書く前の確かめと署名を先に済ませ、1 つでも断れば何も書かない・何も封をしない（5.23.0）
    prepared, ready = _each(names, lambda name: (R.accept_prepare(root, name, plans[name]["to"], args.as_of,
                                                                  confirm=plans[name]["digest"], at=at,
                                                                  common_root=args.common), True))
    if ready:
        results, ok = _each(names, lambda name: (R.accept_commit(root, prepared[name]), True))
    else:
        # 断った出し口はその断り、断らなかった出し口は「封をしていない」（ほかの出し口が断ったので）
        results, ok = {name: (value if isinstance(value, dict) and value.get("ok") is False
                              else {"ok": False, "sealed": False, "why": "other_outlet_refused"})
                       for name, value in prepared.items()}, False
    if args.json:
        _print({"outlet": R.ALL, "ok": ok, "outlets": results, **({} if ok else red_channel(results))})
        return 0 if ok else 1
    _all_head(args, names, ok)
    for name, value in results.items():
        if value.get("why") == "other_outlet_refused":
            print(f"[{name}] ほかの出し口が断ったので、書かず封もしていません")
            continue
        if value.get("ok") is False:
            print(f"[{name}] 断り {value['code']}（{value.get('detail') or ''}）")
            continue
        print(f"[{name}] 受け入れて封をしました: 世代 {value['generation']}・判断 {value['judgments']} 件・"
              f"{value['files']} ファイル")
        _show_changes(value)
    return 0 if ok else 1


def _stage1(plan: dict) -> dict:
    return {"stage": 1, "outlet": plan["outlet"], "generation": plan["body"]["generation"],
            "digest": plan["digest"], "new_judgments": plan["new_judgments"], "changes": plan["changes"],
            "pending_takes": plan["result"]["pending_takes"], "stale": plan["stale"],
            "files": len(plan["body"]["outputs"]), "items": len(plan["body"]["items"])}


def _stage1_lines(value: dict, plan: dict) -> None:
    print(f"[{plan['outlet']}] 世代 {value['generation']}・新しい判断 {len(plan['new_judgments'])} 件:")
    for j in plan["new_judgments"][:40]:
        print(f"  {j['id']}\t{j['question']}\t{(j['target'] or {}).get('record')}\t"
              f"{j['word'] or j['conclusion']}\t{j['by']}")
    _show_changes(value)
    if value["stale"]:
        print(f"  一覧に無い旧い材料 {len(value['stale'])}: " + "・".join(value["stale"][:10]))


def cmd_accept(args) -> int:
    _by(args)   # 誰が受け入れたかは、受け入れた判断の行（by）と台帳の git に残る
    root = root_of(args.register)
    if R.is_all(args.outlet):
        return _accept_all(args, root)
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
    "common_pointer_missing": "common:<番号> で指す先が共通の台帳にありません（番号か指す欄の種類の取り違え）",
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


def _omitted_note(entry: dict) -> str:
    """載せきれなかった数を文の出力にも出す（JSON は `<key>_total`・`<key>_omitted`。苦味 第11 #8258）。"""
    for key in ("items", "files"):
        total, omitted = entry.get(f"{key}_total"), entry.get(f"{key}_omitted")
        if isinstance(total, int) and isinstance(omitted, int) and omitted > 0:
            return (f"　全部で {total} 件・出したのは {total - omitted} 件。残り {omitted} 件はここに出ていません"
                    "（直して verify をやり直すと次の分が出ます）")
    return ""


def _verify_all(args, root: str, now: str) -> int:
    """段 11 の C: `verify --outlet all --material <根>`。材料は `<根>/<dest>`。`--previous`・`--publish` はフォルダで、
    出し口ごとに `<フォルダ>/thth-seal.<出し口>.json`。1 つでも赤なら全体が赤（rc 1）。"""
    names = R.all_outlets(root)

    def seal_file(folder, name):
        return None if folder is None else os.path.join(folder, f"thth-seal.{name}.json")

    def one(name):
        value = R.verify(root, name, R.dest_under(root, name, args.material), now,
                         previous_path=seal_file(args.previous, name), no_previous=args.no_previous,
                         common_root=args.common, publish=seal_file(args.publish, name))
        return value, value["ok"]
    results, ok = _each(names, one)
    args._noted_value = {"outlets": results}
    if args.json:
        _print({"outlet": R.ALL, "ok": ok, "outlets": results,
                **({} if ok else red_channel(results, "register_seal_unverified"))})
        return 0 if ok else 1
    _all_head(args, names, ok)
    for name, value in results.items():
        if "code" in value and value.get("ok") is False and "problems" not in value:
            print(f"[{name}] 断り {value['code']}（{value.get('detail') or ''}）")
            continue
        print(f"[{name}] " + (f"緑・世代 {value.get('generation')}" if value["ok"] else "赤"))
        for p in value["problems"]:
            print(f"  赤: {PROBLEM_JA.get(p['word'], p['word'])}" + _omitted_note(p))
        for w in value["warnings"]:
            print(f"  知らせ: {w['word']}" + _omitted_note(w))
        show_impact(value.get("impact"))
    return 0 if ok else 1


def _noted(command: str, args, run) -> int:
    """確かめの結果を記録に 1 行（`runs.py`・書けなくても rc を変えない・5.23.0）。"""
    import time
    from . import runs
    started, value = time.monotonic(), {}
    try:
        rc = run()
    except RegisterRefused as exc:
        runs.note(command, args.register, args.outlet, {"code": exc.code, **exc.extra}, False,
                  time.monotonic() - started)
        raise
    runs.note(command, args.register, args.outlet, getattr(args, "_noted_value", None) or value, rc == 0,
              time.monotonic() - started)
    return rc


def cmd_verify(args) -> int:
    def run():
        with R.reading():
            return _cmd_verify(args)
    return _noted("verify", args, run)


def _cmd_verify(args) -> int:
    root = root_of(args.register)
    now = args.now or _at()
    if R.is_all(args.outlet):
        return _verify_all(args, root, now)
    value = R.verify(root, args.outlet, args.material, now, previous_path=args.previous,
                     no_previous=args.no_previous, common_root=args.common, publish=args.publish)
    args._noted_value = value
    if not args.json:
        if value["ok"]:
            print(f"確かめました: {args.outlet}・世代 {value.get('generation')}（公開鍵: {value.get('key')}）")
            if value.get("published"):
                print(f"配る物を置きました: {value['published']}（次の回の --previous）")
        for p in value["problems"]:
            print(f"  赤: {PROBLEM_JA.get(p['word'], p['word'])}"
                  + (f"（{'・'.join(str(i.get('item', i)) if isinstance(i, dict) else str(i) for i in (p.get('items') or p.get('files') or []))[:300]}）"
                     if p.get("items") or p.get("files") else "")
                  + _omitted_note(p))
        for w in value["warnings"]:
            print(f"  知らせ: {w['word']}" + _omitted_note(w))
        show_impact(value.get("impact"))
    elif value["ok"]:
        _print(value)
    if not value["ok"]:
        raise RegisterRefused("register_seal_unverified", value["problems"][0]["word"],
                              problems=value["problems"], warnings=value["warnings"], key=value.get("key"),
                              generation=value.get("generation"), impact=value.get("impact"))
    return 0


def cmd_seeds(args) -> int:
    """5.23.0: 原稿の頭の `seeds:` を台帳と突き合わせて数える（読むだけ・`seeds.py`）。"""
    from . import seeds as SD
    value = SD.count(root_of(args.register), os.path.abspath(os.path.expanduser(args.queue)))
    if args.json:
        _print(value)
        return 0
    print(f"原稿 {value['drafts']} 本のうち、番号を書いた原稿 {value['seeded']} 本"
          f"（{'・'.join(f'{k} {n}' for k, n in value['by_kind'].items()) or '—'}）")
    print(f"  台帳に無い番号 {value['missing_count']}" + "".join(f"\n    {m['draft']}: {m['seed']}" for m in value["missing"]))
    for kind, item in value["never_seeded"].items():
        print(f"  まだ種になっていない {kind} {item['count']}")
    return 0


def cmd_used(args) -> int:
    if args.record.startswith(R.COMMON + ":"):
        value = R.used_common(root_of(args.register), args.record, args.common)
        if args.json:
            _print(value)
            return 0
        print(f"{value['record']}（今の共通の版 {(value['version'] or '共通の台帳を --common で渡すと出ます')[:12]}）")
        for row in value["outlets"]:
            if "generation" not in row:
                print(f"  {row['outlet']}: 封の形が崩れていて読めません")
                continue
            mark = "" if row["stale"] is None else ("・封のあと共通の版が進んだ" if row["stale"] else "・今の版と同じ")
            print(f"  {row['outlet']}: 世代 {row['generation']}・封の版 {row['version'][:12]}{mark}")
        print(f"  指すこの台帳の記録 {len(value['pointed_by'])}: " + "・".join(value["pointed_by"][:20]))
        print(f"  そこから 1 段 {value['reached_count']}: " + "・".join(c["record"] for c in value["reached"][:20]))
        return 0
    value = R.used(root_of(args.register), args.record)
    if args.json:
        _print(value)
        return 0
    print(f"{value['record']}（今の版 {value['version'][:12]}）")
    if not value["outlets"]:
        print("  どの出し口の封にも載っていません")
    for row in value["outlets"]:
        if "generation" not in row:
            print(f"  {row['outlet']}: 封の形が崩れていて読めません")
            continue
        what = [x["as"] + (f"（{x['unit']}）" if x.get("unit") else "") for x in row["loaded"]]
        what += [f"{i['record']} へ差し込み（{', '.join(i['fields'])}）" for i in row["inserted_into"]]
        if row["read_only"]:
            what.append("入力として読んだが載らなかった")
        if row["unconfirmed"]:
            what.append("差し込んだ側が封のあとに変わり、差し込みは確かめられない")
        print(f"  {row['outlet']} 世代 {row['generation']}（{row['as_of']}・封 {row['seal']}）: {'・'.join(what)}"
              + ("　材料が古い（今の版と違う）" if row["stale"] else ""))
    for d in value["derived_by"]:
        print(f"  から作った: {d['record']}（{d['field']}）" + ("　来歴の控えが今の版と違う" if d.get("review") else ""))
    return 0


def cmd_published(args) -> int:
    from . import published as P
    if args.previous is None and not args.no_previous:
        # 前に配った控えの頭が無いと、控えの行を消して頭も書き直した控えを見抜けない（verify と同じ流儀で言わせる）
        raise RegisterRefused("register_args_invalid", "previous")
    value = P.published(root_of(args.register), args.outlet, args.list, args.now or P.now_default(),
                        record=args.record, previous=args.previous, publish=args.publish,
                        no_previous=args.no_previous)
    if args.json:
        _print(value)
        return 0 if value["ok"] else 1
    c = value["counts"]
    print(f"出した URL の関門 {value['outlet']}: 一覧 {c['listed']}・控え {c['logged']}・転送 {c['redirected']}・"
          f"消した {c['gone']}・新しい {c['new']}・赤 {c['problems']}・知らせ {c['warnings']}")
    for w in value["warnings"]:
        print(f"  知らせ {w['word']}: {w.get('url', '')}")
    for p in value["problems"]:
        print(f"  赤 {p['word']}: {p.get('url', p.get('why', ''))} — {P.FIX.get(p['word'], '')}")
    if value["more"]:
        print(f"  ほか {value['more']} 件（--json で）")
    if value["ok"] and args.record:
        print(f"  控えに {value['recorded']} 行足しました")
    elif value["ok"] and c["new"]:
        print("  新しい URL は控えに足していません（--record で足す）")
    return 0 if value["ok"] else 1


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
    p = verbs.add_parser("judge", help="判断を足す（止める・基準・結び直すも・記録を並べると同じ判断を 1 回で）")
    p.add_argument("register", metavar="台帳")
    p.add_argument("record", nargs="?", default=None, metavar="種類:番号")
    p.add_argument("more", nargs="*", default=None, metavar="種類:番号",
                   help="同じ問いと理由で判断する記録を続けて並べる（5.23.0・止める・結び直すには使えない）")
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

    p = verbs.add_parser("fix", help="訂正の 1 本の命令（書く・名指し・判断・受け入れの 1 段め。許せば封まで）")
    p.add_argument("register", metavar="台帳")
    p.add_argument("record", metavar="種類:番号")
    p.add_argument("--set", action="append", default=None, metavar="欄=値")
    p.add_argument("--reason-text", required=True)
    p.add_argument("--how", default=None)
    p.add_argument("--against", action="append", default=None, metavar="種類:番号")
    p.add_argument("--outlet", required=True, metavar="出し口|all")
    p.add_argument("--to", required=True, metavar="先")
    p.add_argument("--as-of", required=True, metavar="日時")
    p.add_argument("--common", default=None, metavar="共通の台帳")
    p.add_argument("--by", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run(cmd_fix))

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

    p = verbs.add_parser("seeds", help="原稿の頭の seeds: を台帳と突き合わせて数える（読むだけ・lint にも承認にも入れない）")
    p.add_argument("register", metavar="台帳")
    p.add_argument("--queue", required=True, metavar="原稿の置き場")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run(cmd_seeds))

    p = verbs.add_parser("used", help="使った先: どの出し口の・どの世代の材料に・何として載ったか（読むだけ・common:<番号> も）")
    p.add_argument("register", metavar="台帳")
    p.add_argument("record", metavar="種類:番号")
    p.add_argument("--common", default=None, metavar="共通の台帳", help="common:<番号> のとき、今の共通の版と比べる")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run(cmd_used))

    p = verbs.add_parser("published", help="出した URL の関門（--record のときだけ控えに足す）")
    p.add_argument("register", metavar="台帳")
    p.add_argument("--outlet", required=True)
    p.add_argument("--list", required=True, metavar="一覧")
    p.add_argument("--record", action="store_true")
    p.add_argument("--now", default=None, metavar="日時")
    prev = p.add_mutually_exclusive_group()
    prev.add_argument("--previous", default=None, metavar="前に配った控えの頭")
    prev.add_argument("--no-previous", action="store_true", help="前に配った控えの頭が無い（初回だけ）")
    p.add_argument("--publish", default=None, metavar="配る控えの頭の置き場")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run(cmd_published))

    p = verbs.add_parser("key", help="持ち主の封の鍵（作る・公開鍵を見る）")
    keys = p.add_subparsers(dest="key_command", required=True)
    for verb in ("init", "show"):
        q = keys.add_parser(verb)
        q.add_argument("--json", action="store_true")
        q.set_defaults(func=run(cmd_key))
