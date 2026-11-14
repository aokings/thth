"""台帳の器 段 1 の命令（設計 §13 の `register kinds`・`register check`・`register links` のうち段 1 に要る分）。

    thth register kinds <台帳> [--json]
        種類の宣言・憲法・備え付けの種類の一覧（宣言の無い台帳は、備え付けの今の頭つきの .md だけ）
    thth register check <台帳> [--catalog] [--against <前>] [--json]
        台帳に .thth/kinds/ があれば、器として読む: 宣言の形・記録と内側の単位・YAML の範囲・同じ番号・鍵で決まらない
        単位・指す欄（先が無い・輪）・来歴の控えの見直し・判断（効いている・古い・消えた指し先）と結びの候補
        （.thth/kinds/ が無い台帳は、今の check のまま）。--against <前>（commit か控えの名）は、判断の記録が
        前の時点の記録の続き（前の全バイトが今の頭と同じ）でなければ赤（段 5 §2.2）
    thth register links <台帳> <種類:番号> [--units] [--unit <指し先>] [--key 欄=値 ...] [--json]
        指す・指される・判断・観測。単位を鍵で引いて 2 つ以上に当たれば、黙って 1 つにせずに断る。
        判断ごとに対象の版・依る所の指紋・今の版に効くか・元の記録（inspects の表の行か、誰が・いつ）と、
        今の版がどの取り込みで入ったか（控えの名・日時・集めた者・検めた者）を出す（段 5 §2.3・§2.4）

どれも読むだけで、台帳にも THTH の側にも書かない。網に出ない。
"""
from __future__ import annotations

import json
import sys

from . import checks as C
from . import judgment as J
from . import trail as T
from . import vessel as V
from .errors import RegisterRefused


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, default=str))


def _kind_view(kind: V.Kind) -> dict:
    return {"kind": kind.name, "place": kind.places, "format": kind.format, "records": kind.records,
            "id": kind.id_rule, "max_bytes": kind.max_bytes, "declaration": kind.path,
            "questions": [{"question": q.ident, "level": q.level, "state": q.state,
                           "depends": q.depend_fields() if q.depends is not None else "all",
                           "reason_kind": q.reason_kind} for q in kind.questions],
            "pointers": [{k: v for k, v in p.items() if k != "note"} for p in kind.pointers],
            "units": None if kind.units is None else {
                "key": kind.units["key"], "id": kind.units["id"], "time": kind.units["time"],
                "scope_separate": kind.units["scope"]["separate"], "sources": bool(kind.units["sources"])},
            "checks": C.view(kind)}


def kinds(root: str) -> dict:
    declared = V.has_declarations(root)
    decl = V.load_declarations(root) if declared else {"kinds": {"thth_row": V.builtin_row_kind()},
                                                       "constitution": {}, "ids": {}}
    return {"declared": declared, "kinds": [_kind_view(k) for k in decl["kinds"].values()],
            "constitution": {q: sorted(words) for q, words in decl["constitution"].items()},
            "builtin": ["thth_row"], "yaml_reader": V.yamlread.available(), **_outlets_view(root, decl["kinds"])}


def _outlets_view(root: str, kinds_: dict) -> dict:
    """出し口ごとの読者に見える欄と、内部の語の数（段 4 のつなぎ。出し口の宣言が無ければ空）。"""
    from types import SimpleNamespace
    from . import route as R
    holder = SimpleNamespace(kinds=kinds_)
    outlets = R.load_outlets(root, holder)
    view = [{"outlet": name, "audience": o.audience,
             "reader_fields": {k: list(o.kind_conf(k).reader) for k in o.kinds if o.kind_conf(k).reader}}
            for name, o in sorted(outlets.items())]
    return {"outlets": view, "internal_words": len(R.internal_words(root, holder)) if outlets else 0}


def cmd_kinds(args, root: str) -> int:
    value = {"register": args.register, **kinds(root)}
    if args.json:
        _print(value)
        return 0
    if not value["declared"]:
        print(f"台帳 {args.register}: 種類の宣言（.thth/kinds/）はありません。備え付けの種類 thth_row（頭つきの .md）で読みます")
    for k in value["kinds"]:
        print(f"{k['kind']}\t{k['format']}\t{k['records']}\t{', '.join(k['place'])}")
        for q in k["questions"]:
            print(f"  問い {q['question']}（{q['level']}）: 状態の欄 {q['state'] or '-'}・依る欄 {q['depends']}")
        for p in k["pointers"]:
            name = p.get("field") or "+".join(p.get("fields") or []) or p.get("template")
            to = p.get("to") or "・".join(sorted(set((p.get("to_by") or {}).get("prefix", {}).values())
                                                 | set((p.get("to_by") or {}).get("map", {}).values())))
            print(f"  指す欄 {name}{'（内側の単位）' if p.get('in_units') else ''} → {to}（{p['meaning']}）")
        for c in k["checks"]:
            print(f"  検査 {c['id']}（{c['check']}・{c['level']}）" + (f": {c['note']}" if c["note"] else ""))
    for o in value["outlets"]:
        if o["reader_fields"]:
            fields = "・".join(f"{k}（{', '.join(f)}）" for k, f in o["reader_fields"].items())
            print(f"出し口 {o['outlet']}（{o['audience']}）: 読者に見える欄 {fields}")
    if value["outlets"]:
        print(f"内部の語 {value['internal_words']} 語（読者に見える欄に出たら check が赤にします）")
    if not value["yaml_reader"]:
        print("  YAML の中を読む部品（PyYAML）が入っていないので、YAML の記録は版を取るだけで、中は見ていません"
              "（pip install 'thth[yaml]'）")
    return 0


def _health(vessel: V.Vessel, graph: dict) -> dict:
    """反する根拠を持つのに、書いてある語が「よい」にあたる記録の数（語は上書きしない・知らせるだけ）。"""
    contradicted = {e["to"] for e in graph["edges"] if e["meaning"] == "contradicts"}
    count = 0
    for ref in contradicted:
        rec = vessel.unique(ref)  # 同じ番号の記録が 2 つ以上なら数えない（先頭を選ばない）
        if rec is None:
            continue
        for obs in V.observations(vessel.kinds[rec.kind], rec):
            table = vessel.constitution.get(obs["question"]) or {}
            if (table.get(obs["word"]) or {}).get("conclusion") == "good":
                count += 1
                break
    return {"contradicted_good": count}


def _erase_unfinished(root: str) -> int:
    """消し終わっていない消す求めの数（段 5 §3.5）。"""
    from . import erase as E
    return E.unfinished(root)


def _all_checks(root: str, vessel: V.Vessel, judgments: list) -> dict:
    """宣言の検査（`checks.run`）に、読者に見える欄の点検（`reader_check.check`）を足す（数えにも入れる）。"""
    from . import reader_check as RC
    out = C.run(vessel, judgments)
    for one in RC.check(root, vessel):
        out["total"] += 1
        out[one["level"]] += one["violations"]
        out["failed"] += 1 if one.get("failed") else 0
        out["list"].append(one)
    return out


def check(root: str, *, catalog: bool = False) -> dict:
    vessel = V.open_vessel(root)
    graph = V.links(vessel)
    judgments = J.read(root)
    records = {}
    for rec in vessel.all_records:
        records[rec.kind] = records.get(rec.kind, 0) + 1
    observations = sum(len(V.observations(vessel.kinds[r.kind], r)) for r in vessel.all_records)
    value = {"vessel": True, "kinds": sorted(vessel.kinds), "records": records,
             "units": sum(len(r.units) for r in vessel.all_records),
             "not_inspected": sum(1 for r in vessel.all_records if not r.inspected and not r.problems),
             "ambiguous_units": V.ambiguous_units(vessel), "problems": V.problems(vessel),
             "links": {"edges": len(graph["edges"]), "dangling": graph["dangling"], "cycles": graph["cycles"],
                       "review": graph["review"], "unrouted": graph["unrouted"]},
             "observations": observations, "judgments": J.summary(vessel, judgments),
             "candidates": J.candidates(vessel, judgments), "ids_moves": V.ids_table_moves(vessel),
             "health": _health(vessel, graph), "checks": _all_checks(root, vessel, judgments),
             "erase_unfinished": _erase_unfinished(root)}
    if catalog:
        value["catalog"] = vessel.catalog()
    return value


def check_against(args, root: str) -> dict | None:
    """`--against <前>`（段 5 §2.2）: 判断の記録が前の時点の続きでなければ赤（`register_judgments_rewritten`）。続きなら結果を返す（無ければ None）。"""
    name = getattr(args, "against", None)
    if not name:
        return None
    result = T.against(root, name)
    if not result["continues"]:
        if not getattr(args, "json", False):
            print(f"  判断の記録が前の時点（{result['from']} {result['against'][:12]}）の続きではありません: "
                  f"前の {result['line']} 行めが{'ありません' if result['why'] == 'shortened' else '書き換わっています'}",
                  file=sys.stderr)
        raise RegisterRefused("register_judgments_rewritten", f"line {result['line']}",
                              against=result)
    return result


def cmd_check(args, root: str) -> int:
    against = check_against(args, root)
    value = {"register": args.register, **check(root, catalog=getattr(args, "catalog", False))}
    if against is not None:
        value["against"] = against
    yaml_problems = [p for p in value["problems"] if any(w.startswith("yaml:") for w in p["words"])]
    other = [p for p in value["problems"] if p not in yaml_problems]
    if not args.json:
        for p in value["problems"]:
            print(f"  {p['path']}: {', '.join(p['words'])}", file=sys.stderr)
    payload = {k: value[k] for k in ("problems", "records", "links", "checks")}
    if yaml_problems:
        raise RegisterRefused("register_yaml_refused", f"{len(yaml_problems)} files", **payload)
    if other:
        raise RegisterRefused("register_record_invalid", f"{len(other)} records", **payload)
    if value["links"]["dangling"] or value["links"]["cycles"]:
        raise RegisterRefused("register_link_invalid",
                              f"{len(value['links']['dangling'])} dangling {len(value['links']['cycles'])} cycles",
                              **payload)
    failing = C.red(value["checks"])
    if failing:
        if not args.json:
            for c in failing:
                _say_check(c, file=sys.stderr)
        raise RegisterRefused("register_record_invalid", f"checks: {len(failing)}", **payload)
    if args.json:
        _print(value)
        return 0
    if against is not None:
        print(f"判断の記録は前の時点（{against['from']} {against['against'][:12]}）の続きです"
              f"（前 {against['lines_before']} 行・今 {against['lines_now']} 行）")
    total = sum(value["records"].values())
    print(f"台帳 {args.register}: 記録 {total} 件（{', '.join(f'{k} {n}' for k, n in sorted(value['records'].items()))}）・"
          f"内側の単位 {value['units']}")
    if value["not_inspected"]:
        print(f"  中は見ていない記録 {value['not_inspected']} 件（版だけ取りました）")
    if value["ambiguous_units"]:
        print(f"  鍵で決まらない単位 {len(value['ambiguous_units'])} 組（鍵と中身の指紋で分けて指しています。"
              "1 つにはしていません）")
    j = value["judgments"]
    print(f"  判断: 効いている {j['effective']}・古い {j['stale']}・消えた指し先 {j['vanished']}"
          + (f"・よその台帳を指して確かめられない {j['unverifiable']}" if j["unverifiable"] else ""))
    if value["candidates"]:
        print(f"  結びの候補 {len(value['candidates'])} 件（器は結びません。結ぶなら人が名指しします）")
    if value["links"]["unrouted"]:
        print(f"  先の種類が決まらない指し {len(value['links']['unrouted'])} 件（to_by のどれにも当たらない。"
              "先が無い指しとは数えていません）")
    if value["links"]["review"]:
        print(f"  来歴の控えが今の版と違う物 {len(value['links']['review'])} 件（見直す物）")
    ch = value["checks"]
    if ch["total"]:
        print(f"  検査 {ch['total']} 件: 赤 0・知らせる {ch['warn']}・見直しの候補 {ch['review']}")
        for c in ch["list"]:
            if c["violations"] or c.get("unverifiable"):
                _say_check(c)
    if value["erase_unfinished"]:
        print(f"  消し終わっていない消す求め {value['erase_unfinished']} 件（thth register erase status <台帳>）")
    if value["health"]["contradicted_good"]:
        print(f"  反する根拠を持つのに「よい」の語の記録 {value['health']['contradicted_good']} 件（語は上書きしていません）")
    return 0


_REASON_JA = {"duplicate": "重なる", "blank": "空", "not_blank": "空でない", "none_filled": "どれも空",
              "more_than_one": "2 つ以上埋まっている", "not_text": "字でない", "word_not_allowed": "語の中に無い",
              "shape": "形に合わない", "forbidden_pair": "禁じた組を指している", "too_few": "数が足りない",
              "too_many": "数が多すぎる", "not_judged_good": "効いている「よい」の判断が足りない",
              "column_missing": "要る欄が無い", "column_not_allowed": "決めた欄の外の欄がある",
              "column_forbidden": "あってはならない欄がある", "no_prefix": "区切りの字が無い",
              "prefix_not_allowed": "頭の語が一覧に無い"}


def _say_check(c: dict, file=None) -> None:
    """人が読む検査の結果: 呼び名と注（なぜ）と、当たった記録と理由（どこを直せば通るか）。"""
    level = {"error": "赤", "warn": "知らせる", "review": "見直しの候補"}[c["level"]]
    head = f"  検査 {c['kind']}.{c['id']}（{c['check']}・{level}）"
    if c.get("failed"):
        print(head + ": 回す途中で止まりました（宣言か記録の形を確かめてください）", file=file)
        return
    head += f": {c['violations']} 件"
    if c.get("unverifiable"):
        head += f"・よその台帳を指して確かめられない {c['unverifiable']}"
    print(head + (f" — {c['note']}" if c["note"] else ""), file=file)
    for r in c["records"][:5]:
        where = r.get("field") or ",".join(r.get("fields") or []) or ""
        print(f"    {r['record']}: {_REASON_JA.get(r['reason'], r['reason'])}" + (f"（{where}）" if where else ""),
              file=file)
    rest = c["violations"] - min(5, len(c["records"]))
    if rest > 0:
        print(f"    ほか {rest} 件（--json の checks に 50 件まで）", file=file)


_STATE_JA = {"effective": "効く", "stale": "古い", "vanished": "消えた指し先", "unverifiable": "よその台帳"}


def _parse_key(items) -> dict:
    out = {}
    for item in items or []:
        name, sep, val = item.partition("=")
        if not sep or not V.FIELD.fullmatch(name.replace("group.", "")):
            raise RegisterRefused("register_args_invalid", "key")
        out[name] = val
    return out


def links_of(root: str, ref: str, *, unit=None, key=None, units=False) -> dict:
    vessel = V.open_vessel(root)
    rec = vessel.get(ref)
    kind = vessel.kinds[rec.kind]
    graph = V.links(vessel)
    # 台帳に書いた判断と、検収の記録を持つ表（inspects）から作った判断（元の記録は元のまま・器は索引だけ）。
    judgments = J.read(root) + J.inspections(vessel)
    mine = [j for j in judgments if j["target"].get("record") == ref]
    value = {"record": ref, "kind": rec.kind, "path": rec.path, "version": rec.version, "inspected": rec.inspected,
             "problems": rec.problems,
             "observations": V.observations(kind, rec),
             "points_to": [e for e in graph["edges"] if e["from"] == ref],
             "pointed_by": [e for e in graph["edges"] if e["to"] == ref],
             "review": [e for e in graph["review"] if e["from"] == ref or e["to"] == ref],
             "judgments": [T.judgment_view(vessel, j) for j in mine if T.shows_version(vessel, j)],
             # 対象の版を示していない検収の記録（判断に数えない・効くかは言えない）
             "unversioned": [T.unversioned_view(vessel, j) for j in mine if not T.shows_version(vessel, j)],
             "entered": T.entered(root, rec)}
    if units:
        value["units"] = [{"pointer": u.pointer, "key": u.key, "ambiguous": u.ambiguous} for u in rec.units]
    picked = None
    if key:
        picked = V.find_units(rec, key)
    elif unit:
        picked = V.unit_at(rec, unit)
        if picked is None:
            raise RegisterRefused("register_record_missing", "unit")
    if picked is not None:
        value["unit"] = {"pointer": picked.pointer, "key": picked.key, "ambiguous": picked.ambiguous,
                         "values": [{"name": v.name, "pointer": v.pointer, "time": v.time, "scope_kind": v.scope_kind,
                                     "scope": v.scope, "sources": len(v.sources), "depend": V.short(v.depend())}
                                    for v in V.values_of(rec, kind, picked)]}
    return value


def cmd_links(args, root: str) -> int:
    value = {"register": args.register, **links_of(root, args.record, unit=args.unit, key=_parse_key(args.key),
                                                   units=args.units)}
    if args.json:
        _print(value)
        return 0
    print(f"{value['record']}（{value['path']}・版 {V.short(value['version'])}"
          f"{'' if value['inspected'] else '・中は見ていない'}）")
    for e in value["points_to"]:
        print(f"  指す → {e['to']}（{e['meaning']}）")
    for e in value["pointed_by"]:
        print(f"  指される ← {e['from']}（{e['meaning']}）")
    for o in value["observations"]:
        print(f"  観測 {o['question']}: {o['word']}（書いてある語・判断ではない）")
    e = value["entered"]
    if e.get("kept"):
        from .adopt_cli import intake_line
        print(f"  この版が入った所: {intake_line(e)}")
    else:
        print("  この版が入った所: 控えに無い（控えのあとに直に書かれたか、控えの無い台帳）")
    for j in value["judgments"]:
        o = j["origin"]
        where = (f"元の表 {o['table']} の行 {o['row']}" + (f"（{o['file']}）" if o.get("file") else "")
                 if "table" in o else f"命令で足した（{o.get('by')}・{o.get('at')}）")
        seen = f"版 {V.short(j['version'])}" if j["version"] else "確かめた中身"
        depend = V.short(j["depend"]) if isinstance(j["depend"], str) else "-"
        print(f"  判断 {j['id']} {j['question']}: {_STATE_JA.get(j['state'], j['state'])}（{seen}・依る所 {depend}・{where}）")
    for u in value["unversioned"]:
        print(f"  版を示していない検収の記録: 元の表 {u['table']} の行 {u['row']}"
              + (f"（{u['file']}）" if u.get("file") else "") + f" {u['question']}: 今の版に効くかは言えない")
    for e in value["review"]:
        print(f"  見直す: {e['from']} の控え {e['recorded']} が、今の {e['to']} と違います（今 {e['now']}）")
    if "unit" in value:
        u = value["unit"]
        print(f"  単位 {u['pointer']}{'（鍵で決まらない）' if u['ambiguous'] else ''}")
        for v in u["values"]:
            scope = v["scope"] if v["scope"] is not None else "範囲が未宣言"
            print(f"    {v['name']} @{v['time']}・範囲 {scope}・出典 {v['sources']}")
    for u in value.get("units", []):
        print(f"  {u['pointer']}{'（鍵で決まらない）' if u['ambiguous'] else ''}\t{json.dumps(u['key'], ensure_ascii=False)}")
    return 0
