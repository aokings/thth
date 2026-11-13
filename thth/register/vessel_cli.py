"""台帳の器 段 1 の命令（設計 §13 の `register kinds`・`register check`・`register links` のうち段 1 に要る分）。

    thth register kinds <台帳> [--json]
        種類の宣言・憲法・備え付けの種類の一覧（宣言の無い台帳は、備え付けの今の頭つきの .md だけ）
    thth register check <台帳> [--catalog] [--json]
        台帳に .thth/kinds/ があれば、器として読む: 宣言の形・記録と内側の単位・YAML の範囲・同じ番号・鍵で決まらない
        単位・指す欄（先が無い・輪）・来歴の控えの見直し・判断（効いている・古い・消えた指し先）と結びの候補
        （.thth/kinds/ が無い台帳は、今の check のまま）
    thth register links <台帳> <種類:番号> [--units] [--unit <指し先>] [--key 欄=値 ...] [--json]
        指す・指される・判断・観測。単位を鍵で引いて 2 つ以上に当たれば、黙って 1 つにせずに断る

どれも読むだけで、台帳にも THTH の側にも書かない。網に出ない。
"""
from __future__ import annotations

import json
import sys

from . import judgment as J
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
                "scope_separate": kind.units["scope"]["separate"], "sources": bool(kind.units["sources"])}}


def kinds(root: str) -> dict:
    declared = V.has_declarations(root)
    decl = V.load_declarations(root) if declared else {"kinds": {"thth_row": V.builtin_row_kind()},
                                                       "constitution": {}, "ids": {}}
    return {"declared": declared, "kinds": [_kind_view(k) for k in decl["kinds"].values()],
            "constitution": {q: sorted(words) for q, words in decl["constitution"].items()},
            "builtin": ["thth_row"], "yaml_reader": V.yamlread.available()}


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
            print(f"  指す欄 {p.get('field') or p.get('template')} → {p['to']}（{p['meaning']}）")
    if not value["yaml_reader"]:
        print("  YAML の中を読む部品（PyYAML）が入っていないので、YAML の記録は版を取るだけで、中は見ていません"
              "（pip install 'thth[yaml]'）")
    return 0


def _health(vessel: V.Vessel, graph: dict) -> dict:
    """反する根拠を持つのに、書いてある語が「よい」にあたる記録の数（語は上書きしない・知らせるだけ）。"""
    contradicted = {e["to"] for e in graph["edges"] if e["meaning"] == "contradicts"}
    count = 0
    for ref in contradicted:
        rec = vessel.records.get(ref)
        if rec is None:
            continue
        for obs in V.observations(vessel.kinds[rec.kind], rec):
            table = vessel.constitution.get(obs["question"]) or {}
            if (table.get(obs["word"]) or {}).get("conclusion") == "good":
                count += 1
                break
    return {"contradicted_good": count}


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
                       "review": graph["review"]},
             "observations": observations, "judgments": J.summary(vessel, judgments),
             "candidates": J.candidates(vessel, judgments), "ids_moves": V.ids_table_moves(vessel),
             "health": _health(vessel, graph)}
    if catalog:
        value["catalog"] = vessel.catalog()
    return value


def cmd_check(args, root: str) -> int:
    value = {"register": args.register, **check(root, catalog=getattr(args, "catalog", False))}
    yaml_problems = [p for p in value["problems"] if any(w.startswith("yaml:") for w in p["words"])]
    other = [p for p in value["problems"] if p not in yaml_problems]
    if not args.json:
        for p in value["problems"]:
            print(f"  {p['path']}: {', '.join(p['words'])}", file=sys.stderr)
    payload = {k: value[k] for k in ("problems", "records", "links")}
    if yaml_problems:
        raise RegisterRefused("register_yaml_refused", f"{len(yaml_problems)} files", **payload)
    if other:
        raise RegisterRefused("register_record_invalid", f"{len(other)} records", **payload)
    if value["links"]["dangling"] or value["links"]["cycles"]:
        raise RegisterRefused("register_link_invalid",
                              f"{len(value['links']['dangling'])} dangling {len(value['links']['cycles'])} cycles",
                              **payload)
    if args.json:
        _print(value)
        return 0
    total = sum(value["records"].values())
    print(f"台帳 {args.register}: 記録 {total} 件（{', '.join(f'{k} {n}' for k, n in sorted(value['records'].items()))}）・"
          f"内側の単位 {value['units']}")
    if value["not_inspected"]:
        print(f"  中は見ていない記録 {value['not_inspected']} 件（版だけ取りました）")
    if value["ambiguous_units"]:
        print(f"  鍵で決まらない単位 {len(value['ambiguous_units'])} 組（鍵と中身の指紋で分けて指しています。"
              "1 つにはしていません）")
    j = value["judgments"]
    print(f"  判断: 効いている {j['effective']}・古い {j['stale']}・消えた指し先 {j['vanished']}")
    if value["candidates"]:
        print(f"  結びの候補 {len(value['candidates'])} 件（器は結びません。結ぶなら人が名指しします）")
    if value["links"]["review"]:
        print(f"  来歴の控えが今の版と違う物 {len(value['links']['review'])} 件（見直す物）")
    if value["health"]["contradicted_good"]:
        print(f"  反する根拠を持つのに「よい」の語の記録 {value['health']['contradicted_good']} 件（語は上書きしていません）")
    return 0


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
    judgments = J.read(root)
    mine = [j for j in judgments if j["target"].get("record") == ref]
    value = {"record": ref, "kind": rec.kind, "path": rec.path, "version": rec.version, "inspected": rec.inspected,
             "problems": rec.problems,
             "observations": V.observations(kind, rec),
             "points_to": [e for e in graph["edges"] if e["from"] == ref],
             "pointed_by": [e for e in graph["edges"] if e["to"] == ref],
             "review": [e for e in graph["review"] if e["from"] == ref or e["to"] == ref],
             "judgments": [{"id": j["id"], "question": j["question"], "unit": j["target"].get("unit"),
                            "value": j["target"].get("value"), "state": J.evaluate(vessel, j),
                            "by": j.get("by"), "path": j.get("path")} for j in mine]}
    if units:
        value["units"] = [{"pointer": u.pointer, "key": u.key, "ambiguous": u.ambiguous} for u in rec.units]
    picked = None
    if key:
        picked = V.find_units(rec, key)
    elif unit:
        picked = V.units_by_pointer(rec).get(unit)
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
    for j in value["judgments"]:
        print(f"  判断 {j['id']} {j['question']}: {j['state']}")
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
