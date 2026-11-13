#!/usr/bin/env python3
"""台帳の器 段 1 を、実物の台帳で一度だけ確かめる台本（各メディアの repo を**読むだけ**。何も書かない）。

    python3 tools/register_vessel_realcheck.py [--developer ~/Developer] [--json]

種類の宣言は、この台本が一時の置き場（repo の外）に書いて当てる（元の repo の `.thth/` には書かない）。
見る物（期待する答え `docs/期待する答え_台帳の器_段1〜3_2026-11-13.md` §6 の数え直しと突き合わせる）:

- 関東: 入試結果の枚と回の行・鍵で決まらない組・中身まで同じ組・欄ごとの年度・範囲が未宣言の定員の値・
  決まった字下げで書き直しても依る所が変わらないか（数-K1・K2・K3）
- こぴちゃ: 主張・根拠・点・来歴の控え・YAML の枚（断り 0 か）・見直す本文・反する根拠を持つ「よい」の語（数-C1・C2・C5）
- MMR: 照合台帳の枚と行・held の理由の種類・出し口の組（数-M3）
- にがみラボ: data/*.csv の改行の混ざりと、読めた行の数（数-N1）
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from thth.register import judgment as J, vessel as V  # noqa: E402


def write_kinds(base: pathlib.Path, kinds: list, constitution: dict | None = None) -> str:
    (base / "kinds").mkdir(parents=True)
    for kind in kinds:
        (base / "kinds" / f"{kind['kind']}.json").write_text(json.dumps(kind, ensure_ascii=False), encoding="utf-8")
    if constitution:
        (base / "constitution.json").write_text(json.dumps(constitution, ensure_ascii=False), encoding="utf-8")
    return str(base)


SRC = {"list": "sources", "field": "field", "split": "/,", "rest": "rounds"}


def kanto(root: str, tmp: pathlib.Path) -> dict:
    base = write_kinds(tmp / "kanto", [
        {"schema": "thth.kind/1", "kind": "admission_result", "place": "admission_results/[0-9]*.json",
         "format": "json", "records": "file", "max_bytes": 400000, "id": {"name": True},
         "units": {"groups": ["years", "*"], "items": ["rounds", "*"],
                   "key": ["group.year", "round", "date", "time_of_day", "gender", "subject_type"],
                   "time": {"group": "year"}, "scope": {"key_fields": ["gender"], "separate": ["capacity"]},
                   "sources": SRC}},
        {"schema": "thth.kind/1", "kind": "school_record", "place": "school_records/[0-9]*.json", "format": "json",
         "records": "file", "max_bytes": 400000, "id": {"name": True},
         "units": {"groups": ["admissions"], "items": ["rounds", "*"], "key": ["round", "date"],
                   "time": {"per_field": "field_years"}}},
        {"schema": "thth.kind/1", "kind": "fees", "place": "fees/[0-9]*.json", "format": "json", "records": "file",
         "max_bytes": 400000, "id": {"name": True},
         "units": {"groups": ["admissions_extra"], "items": ["rounds", "*"], "key": ["group.year", "round", "date"],
                   "time": {"group": "year"},
                   "scope": {"separate": ["capacity"], "fields": {"capacity": "capacity_shared"}}}},
    ])
    v = V.open_vessel(root, base)
    by_kind = collections.Counter(r.kind for r in v.all_records)
    results = [r for r in v.all_records if r.kind == "admission_result"]
    amb = [a for a in V.ambiguous_units(v) if a["record"].startswith("admission_result:")]
    same_content = sum(1 for a in amb if a["same_content"] > 1)
    kind = v.kinds["admission_result"]
    undeclared = 0
    sources_all = 0
    for rec in results:
        for unit in rec.units:
            for value in V.values_of(rec, kind, unit):
                if value.name == "capacity" and value.value is not None and value.scope is None:
                    undeclared += 1
    # 決まった字下げ（indent=1）で書き直した物を読み直し、単位の指し先と依る所が同じかを見る（ファイルには書かない）。
    reformatted_differs = 0
    pointer_changes = 0
    for rec in results:
        path = os.path.join(root, rec.path)
        raw = pathlib.Path(path).read_text(encoding="utf-8")
        again = json.dumps(json.loads(raw), ensure_ascii=False, indent=1) + "\n"
        if again != raw:
            reformatted_differs += 1
            twin = V.Record(rec.kind, rec.id, rec.path, V.bytes_version(again.encode()), json.loads(again), True)
            V._read_units(twin, kind)
            before = sorted((u.pointer, V.unit_depend(kind, u)) for u in rec.units)
            after = sorted((u.pointer, V.unit_depend(kind, u)) for u in twin.units)
            pointer_changes += before != after
    mixed_years = 0
    per_field = 0
    for rec in (r for r in v.all_records if r.kind == "school_record"):
        for unit in rec.units:
            table = unit.item.get("field_years")
            if isinstance(table, dict):
                per_field += 1
                if len(set(map(str, table.values()))) > 1:
                    mixed_years += 1
    return {"records": dict(by_kind), "result_rows": sum(len(r.units) for r in results),
            "ambiguous_groups": len(amb), "ambiguous_units": sum(len(a["units"]) for a in amb),
            "ambiguous_same_content": same_content, "capacity_scope_undeclared": undeclared,
            "reformatted_differs": reformatted_differs, "reformatted_pointer_or_depend_changed": pointer_changes,
            "field_years_rounds": per_field, "field_years_mixed": mixed_years,
            "problems": len(V.problems(v))}


def page_kinds(root: str) -> list:
    """`src/content/` の置き場（ディレクトリ）ごとに 1 つの種類（`page_<置き場>`）。別の置き場の同じ名前
    （`origins/kyoto.yaml` と `producers/kyoto.yaml`）を、1 つの種類の同じ番号に重ねない。"""
    content = pathlib.Path(root, "src", "content")
    out = []
    for directory in sorted(p for p in content.iterdir() if p.is_dir() and not p.name.startswith(".")) \
            if content.is_dir() else []:
        if not any(directory.glob("*.yaml")):
            continue
        name = "page_" + "".join(c if c.isalnum() else "_" for c in directory.name.lower())
        out.append({"schema": "thth.kind/1", "kind": name[:32], "place": f"src/content/{directory.name}/*.yaml",
                    "format": "yaml", "id": {"name": True}, "max_bytes": 262144})
    return out


def kopicha(root: str, tmp: pathlib.Path) -> dict:
    base = write_kinds(tmp / "kopicha", [
        {"schema": "thth.kind/1", "kind": "claim", "place": "data/knowledge/claims.csv", "format": "csv",
         "id": {"field": "id"},
         "questions": [{"name": "says_so", "state": "claim_status", "depends": ["statement_ja", "object_text_ja"]}],
         "pointers": [{"field": "subject_entity_id", "meaning": "about", "to": "point"}]},
        {"schema": "thth.kind/1", "kind": "evidence", "place": "data/knowledge/evidence.csv", "format": "csv",
         "id": {"field": "id"},
         "pointers": [{"field": "claim_id", "meaning": "supports", "meaning_from": "relation", "to": "claim"}]},
        {"schema": "thth.kind/1", "kind": "point", "place": "data/knowledge/entities.csv", "format": "csv",
         "id": {"field": "id"}},
        {"schema": "thth.kind/1", "kind": "article_claim", "place": "data/knowledge/article-claims.csv",
         "format": "csv", "id": {"key": ["file", "claim_id"]},
         "pointers": [{"field": "claim_id", "meaning": "derived_from", "to": "claim",
                       "recorded_version": {"field": "statement_hash", "of": ["statement_ja"], "hash": "sha1:12"}}]},
        {"schema": "thth.kind/1", "kind": "central", "place": "data/knowledge/staging/*/central.csv",
         "format": "csv", "id": {"field": "evidence_id"},
         "pointers": [{"field": "evidence_id", "meaning": "about", "to": "evidence"}],
         "inspects": {"follow": ["evidence_id", "claim_id"], "question": "says_so", "by": "central",
                      "content": {"statement_ja": "statement_ja", "object_text_ja": "object_text_ja",
                                  "claim_status": "claim_status"},
                      "required": ["statement_ja", "claim_status"]}},
        *page_kinds(root),
    ], {"schema": "thth.constitution/1", "words": {"says_so": {"supported": {"conclusion": "good"},
                                                                "contested": {"conclusion": "pending",
                                                                              "reason": "both_sides"}}}})
    v = V.open_vessel(root, base)
    graph = V.links(v)
    probs = V.problems(v)
    yaml_refused = [p for p in probs if any(w.startswith("yaml:") for w in p["words"])]
    pages = [r for r in v.all_records if r.kind.startswith("page_")]
    central_probs = [p for p in probs if "/central.csv" in p["path"]]
    other_probs = [p for p in probs if p not in central_probs and p not in yaml_refused]
    report = J.inspection_report(v)
    found = report["judgments"]
    inspection_states = collections.Counter(J.evaluate(v, j) for j in found)
    contradicted = {e["to"] for e in graph["edges"] if e["meaning"] == "contradicts"}
    supports = {e["to"] for e in graph["edges"] if e["meaning"] == "supports"}
    both = contradicted & supports
    words = collections.Counter(v.records[r].values.get("claim_status") for r in both if r in v.records)
    return {"records": dict(collections.Counter(r.kind for r in v.all_records)),
            "pages_inspected": sum(1 for r in pages if r.inspected), "yaml_refused": len(yaml_refused),
            "yaml_words": sorted({w for p in yaml_refused for w in p["words"]})[:10],
            "provenance_rows": sum(1 for e in graph["edges"] if e["meaning"] == "derived_from"),
            "provenance_review": len(graph["review"]),
            "dangling": len(graph["dangling"]), "dangling_kinds": dict(collections.Counter(
                e["to"].split(":")[0] for e in graph["dangling"])),
            "claims_with_both": len(both), "both_by_status": dict(words),
            "central_inspections": len(found), "central_inspection_states": dict(inspection_states),
            "central_skipped": dict(collections.Counter(x["reason"] for x in report["skipped"])),
            "blank_pointers": len(graph["blank"]),
            # 中央の判断の表は、束ごとに同じ根拠の番号を書き直し、番号の無い行もある（今の台帳の形）。目録の問題とは
            # 分けて数える（行は判断の材料で、記録の番号として指されない）。
            "central_problems": dict(collections.Counter(w.split(":")[0] for p in central_probs for w in p["words"])),
            "problems": len(other_probs),
            "problem_words": dict(collections.Counter(w.split(":")[0] for p in other_probs for w in p["words"])),
            "problem_paths": sorted(p["path"] for p in other_probs)[:10]}


def mmr(root: str, tmp: pathlib.Path) -> dict:
    base = write_kinds(tmp / "mmr", [
        {"schema": "thth.kind/1", "kind": "credit", "place": "site/src/credits/*.json", "format": "json",
         "records": "file", "max_bytes": 2000000, "id": {"name": True},
         "questions": [{"name": "credit_holds", "level": "unit", "state": "status", "reason_kind": "held_kind",
                        "reason_text": "held_reason_ja", "depends": ["seed", "title"]},
                       {"name": "may_publish", "outlet": "artist_page", "state": "public", "depends": []},
                       {"name": "may_publish", "outlet": "song_page", "state": "public_song", "depends": []}],
         "units": {"groups": [], "items": ["rows", "*"], "key": ["seed"]}}])
    v = V.open_vessel(root, base)
    kind = v.kinds["credit"]
    words, reasons, outlets = collections.Counter(), collections.Counter(), collections.Counter()
    for rec in v.all_records:
        obs = V.observations(kind, rec)
        out = {o["question"]: o["word"] for o in obs if o["unit"] is None}
        outlets[(out.get("may_publish@artist_page"), out.get("may_publish@song_page"))] += 1
        for o in obs:
            if o["unit"]:
                words[o["word"]] += 1
                if o["reason_kind"]:
                    reasons[o["reason_kind"]] += 1
    return {"records": len(v.all_records), "rows": sum(len(r.units) for r in v.all_records),
            "ambiguous_groups": len(V.ambiguous_units(v)), "status": dict(words), "held_kind": dict(reasons),
            "outlets": {f"{a}/{b}": n for (a, b), n in outlets.items()}, "problems": len(V.problems(v))}


def nigami(root: str, tmp: pathlib.Path) -> dict:
    base = write_kinds(tmp / "nigami", [
        {"schema": "thth.kind/1", "kind": "table", "place": "data/*.csv", "format": "csv", "id": {"key": ["id"]}}])
    v = V.open_vessel(root, base)
    endings = collections.Counter()
    for path in sorted(pathlib.Path(root, "data").glob("*.csv")):
        data = path.read_bytes()
        crlf = data.count(b"\r\n")
        lf = data.count(b"\n") - crlf
        endings["crlf" if crlf and not lf else "lf" if lf and not crlf else "mixed" if crlf else "none"] += 1
    return {"tables": dict(endings), "rows": len(v.all_records),
            "row_width_problems": sum(1 for r in v.all_records if "row_width" in r.problems),
            "unreadable": sum(1 for r in v.all_records if "not_utf8" in r.problems)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--developer", default=os.path.expanduser("~/Developer"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    dev = pathlib.Path(args.developer)
    targets = {"kanto": (kanto, dev / "asmon" / "research" / "kanto_type"), "kopicha": (kopicha, dev / "kopicha"),
               "mmr": (mmr, dev / "music-research"), "nigami": (nigami, dev / "nigamilab")}
    out = {"yaml_reader": V.yamlread.available()}
    with tempfile.TemporaryDirectory(prefix="thth-vessel-") as tmp:
        for name, (func, root) in targets.items():
            if not root.is_dir():
                out[name] = {"skipped": "フォルダが無い"}
                continue
            try:
                out[name] = func(str(root), pathlib.Path(tmp))
            except Exception as exc:  # 確かめの台本なので、止まった所を報告に残す
                out[name] = {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(out, ensure_ascii=False, indent=None if args.json else 2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
