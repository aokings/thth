#!/usr/bin/env python3
"""台帳の器 段 4 を、各メディアの実物で確かめる台本（各メディアの repo を**読むだけ**。何も書かない）。

    python3 tools/register_stage4_realcheck.py [--developer ~/Developer] [--media <名前>] [--json]
                                               [--work <一時の置き場の親>] [--thth <THTH の根>]

指示 `docs/指示_台帳の器_段4_検査の形と済みの線_2026-11-14.md` §3。メディアの名前は台本に書かない。
`tools/register_decls/*/media.json` を順に読み、同じフォルダの宣言（`kinds/*.json`・`constitution.json`）を
一時の置き場（repo の外）に写して `open_vessel(root, decl_base)` で当てる。

`media.json` の形:

    {"name": "...", "source": "<--developer の下の道>",
     "expected": {"records": {種類: 数}, "dangling": {"<種類>.<指す欄>": 数}, "checks": {検査の id: 今の数},
                  "problems": 数（無ければ 0）, "unrouted": {"<種類>.<指す欄>": 数}（G15 の to_by で先の種類が決まらない指し。
                  書かない呼び名は 0）},
     "ask_lane": ["今の台本と決まりの文が食い違う所（1 行ずつ）"],
     "vessel_gaps": ["器の不足（宣言で言えず、読める所までにした所。1 行ずつ）"]}

指す欄の呼び名は `<種類>.<欄>`（決まった形の指し `template` は `<種類>.{template}`、欄の組 `fields` は
`<種類>.<欄>+<欄>`）。1 つの種類に、同じ先を指す `template` と `fields` を両方書くと、先の無い数を分けられない。宣言した指す欄は全部
`expected.dangling` に書く（書き漏らしは「合わない」）。`to_by`（G15）の指す欄も呼び名は `<種類>.<欄>` で、先の無い数は先の種類を
足して比べる。検査の id はメディアの中で重ならないように付ける
（同じ id が 2 つの種類にあれば数を足して比べる）。

出す物（メディアごと）: 読めた記録の数（種類ごと）・例外・読みの問題・指す欄の「先が無い」の数・輪・来歴の控えの
見直し・検査ごとの違反の数。`expected` と突き合わせて「合う・合わない」。合わないが 1 つでもあれば rc 1。

検査の部品（`thth.register.checks`）が import できない間は、宣言から `checks` を外して当て、外したことを出力に書く
（検査の数は「保留」で、合わないに数えない）。`--thth` で、部品の入った別の THTH の根を使える（下見）。
"""
from __future__ import annotations

import argparse
import collections
import importlib
import json
import os
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve()
DECLS = HERE.parent / "register_decls"


def _load_thth(root: str | None):
    sys.path.insert(0, str(pathlib.Path(root).resolve() if root else HERE.parents[1]))
    vessel = importlib.import_module("thth.register.vessel")
    try:
        checks = importlib.import_module("thth.register.checks")
        if not hasattr(checks, "run"):
            checks = None
    except ImportError:
        checks = None
    return vessel, checks


def media_list(only: str | None) -> list:
    out = []
    for path in sorted(DECLS.glob("*/media.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if only and only not in (data.get("name"), path.parent.name):
            continue
        out.append((path.parent, data))
    return out


def copy_decls(src: pathlib.Path, dst: pathlib.Path, keep_checks: bool) -> dict:
    """宣言を一時の置き場に写す。部品が無ければ `checks` を外す。外した数を返す。"""
    (dst / "kinds").mkdir(parents=True)
    stripped = 0
    declared = 0
    for path in sorted((src / "kinds").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        declared += len(data.get("checks", []))
        if not keep_checks and "checks" in data:
            stripped += len(data.pop("checks"))
        (dst / "kinds" / path.name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    con = src / "constitution.json"
    if con.exists():
        (dst / "constitution.json").write_bytes(con.read_bytes())
    return {"declared": declared, "stripped": stripped}


def pointer_name(kind: str, spec: dict) -> str:
    if spec.get("field"):
        return f"{kind}.{spec['field']}"
    if spec.get("fields"):
        return f"{kind}." + "+".join(spec["fields"])
    return f"{kind}.{{{spec['template']}}}"


def pointer_targets(spec: dict) -> list:
    """指す欄の先の種類（`to` か、G15 の `to_by` の頭・map の値の全部）。"""
    if "to" in spec:
        return [spec["to"]]
    route = spec.get("to_by") or {}
    return sorted(set((route.get("prefix") or route.get("map") or {}).values()))


def measure(V, C, root: str, base: str) -> dict:
    v = V.open_vessel(root, base)
    graph = V.links(v)
    records = collections.Counter(r.kind for r in v.all_records)
    dangling = {pointer_name(k.name, p): 0 for k in v.kinds.values() for p in k.pointers}
    by_field = {}
    for k in v.kinds.values():
        for p in k.pointers:
            for to in pointer_targets(p):
                by_field[(k.name, p.get("field"), to)] = pointer_name(k.name, p)
    unrouted = collections.Counter()
    unrouted_examples = collections.defaultdict(list)
    for u in graph.get("unrouted", []):
        kind = u["from"].split(":", 1)[0]
        name = f"{kind}.{u['field']}" if u.get("field") else f"{kind}.?"
        unrouted[name] += 1
        if len(unrouted_examples[name]) < 3:
            unrouted_examples[name].append(f"{u['from']} → {u['target']}")
    examples = collections.defaultdict(list)
    for e in graph["dangling"]:
        kind = e["from"].split(":", 1)[0]
        to = e["to"].split(":", 1)[0]
        name = by_field.get((kind, e.get("field"), to), f"{kind}.?")
        dangling[name] = dangling.get(name, 0) + 1
        if len(examples[name]) < 3:
            examples[name].append(f"{e['from']} → {e['to']}")
    probs = V.problems(v)
    out = {"records": dict(sorted(records.items())),
           "not_inspected": sum(1 for r in v.all_records if not r.inspected and not r.problems),
           "problems": len(probs),
           "problem_words": dict(collections.Counter(w.split(":")[0] for p in probs for w in p["words"])),
           "problem_examples": [f"{p['path']}: {','.join(p['words'])}" for p in probs[:5]],
           "dangling": dict(sorted(dangling.items())), "dangling_examples": dict(examples),
           "unrouted": dict(sorted(unrouted.items())), "unrouted_examples": dict(unrouted_examples),
           "blank_in_lists": len(graph["blank"]), "cycles": len(graph["cycles"]),
           "provenance_review": len(graph["review"]), "ambiguous": len(graph["ambiguous"])}
    if C is not None:
        result = C.run(v, [])
        counts, detail = {}, {}
        for one in result["list"]:
            counts[one["id"]] = counts.get(one["id"], 0) + one.get("violations", 0)
            detail[one["id"]] = {"kind": one["kind"], "level": one["level"], "failed": bool(one.get("failed")),
                                 "unverifiable": one.get("unverifiable", 0), "unresolved": one.get("unresolved", 0),
                                 "examples": [r["record"] for r in one.get("records", [])[:3]]}
        out["checks"] = counts
        out["checks_detail"] = detail
        out["checks_failed"] = result.get("failed", 0)
    return out


def compare(expected: dict, got: dict, checks_run: bool) -> list:
    """合わない所の並び（`[見出し, 名前, 期待, 今]`）。"""
    bad = []
    for name in sorted(set(expected.get("records", {})) | set(got["records"])):
        want, now = expected.get("records", {}).get(name), got["records"].get(name, 0)
        if want != now:
            bad.append(["records", name, want, now])
    for name in sorted(set(expected.get("dangling", {})) | set(got["dangling"])):
        want, now = expected.get("dangling", {}).get(name), got["dangling"].get(name)
        if want != now:
            bad.append(["dangling", name, want, now])
    for name in sorted(set(expected.get("unrouted", {})) | set(got.get("unrouted", {}))):
        want, now = expected.get("unrouted", {}).get(name, 0), got.get("unrouted", {}).get(name, 0)
        if want != now:  # 先の種類が決まらない指し（G15）。書かなければ 0
            bad.append(["unrouted", name, want, now])
    if expected.get("problems", 0) != got["problems"]:
        bad.append(["problems", "-", expected.get("problems", 0), got["problems"]])
    if got["cycles"]:
        bad.append(["cycles", "-", 0, got["cycles"]])
    if checks_run:
        for name in sorted(set(expected.get("checks", {})) | set(got["checks"])):
            want, now = expected.get("checks", {}).get(name), got["checks"].get(name)
            if want != now:
                bad.append(["checks", name, want, now])
        if got.get("checks_failed"):
            bad.append(["checks_failed", "-", 0, got["checks_failed"]])
    return bad


def run_one(V, C, folder: pathlib.Path, media: dict, developer: pathlib.Path, work: pathlib.Path) -> dict:
    root = developer / media["source"]
    out = {"name": media["name"], "source": str(root), "checks_part": C is not None}
    if not root.is_dir():
        out.update(ok=False, error="フォルダが無い")
        return out
    with tempfile.TemporaryDirectory(prefix=f"stage4-{folder.name}-", dir=str(work)) as tmp:
        base = pathlib.Path(tmp) / "decl"
        out["checks_declared"] = copy_decls(folder, base, keep_checks=C is not None)
        try:
            got = measure(V, C, str(root), str(base))
        except Exception as exc:  # 確かめの台本なので、止まった所を報告に残す（例外は 0 であるべき）
            out.update(ok=False, exception=f"{type(exc).__name__}: {str(exc)[:300]}")
            return out
    out.update(got)
    out["mismatch"] = compare(media.get("expected", {}), got, C is not None)
    out["ok"] = not out["mismatch"]
    out["ask_lane"] = media.get("ask_lane", [])
    out["vessel_gaps"] = media.get("vessel_gaps", [])
    return out


def say(one: dict) -> None:
    print(f"== {one['name']}（{one['source']}）: {'合う' if one['ok'] else '合わない'}")
    if "error" in one or "exception" in one:
        print(f"  止まった: {one.get('error') or one.get('exception')}")
        return
    cd = one["checks_declared"]
    if one["checks_part"]:
        print(f"  検査の部品あり: 宣言の検査 {cd['declared']} 件を当てた")
    else:
        print(f"  検査の部品が無いので、宣言の検査 {cd['stripped']} 件を外して当てた（検査の数は保留）")
    total = sum(one["records"].values())
    print(f"  記録 {total} 件: " + "・".join(f"{k} {n}" for k, n in one["records"].items()))
    print(f"  例外 0・読みの問題 {one['problems']}" + (f" {one['problem_words']}" if one["problems"] else "")
          + f"・中を見ていない {one['not_inspected']}・輪 {one['cycles']}・来歴の見直し {one['provenance_review']}")
    hit = {k: n for k, n in one["dangling"].items() if n}
    print(f"  指す欄 {len(one['dangling'])} 本・先が無い: " + ("・".join(f"{k} {n}" for k, n in hit.items()) or "0")
          + "・先の種類が決まらない: " + ("・".join(f"{k} {n}" for k, n in one.get("unrouted", {}).items()) or "0"))
    if one["checks_part"]:
        hit = {k: n for k, n in one["checks"].items() if n}
        print(f"  検査 {len(one['checks'])} 件・違反のある物: " + ("・".join(f"{k} {n}" for k, n in hit.items()) or "0"))
    for section, name, want, now in one["mismatch"]:
        print(f"  合わない {section} {name}: 期待 {want}・今 {now}")
    for line in one.get("vessel_gaps", []):
        print(f"  器の不足: {line}")
    for line in one.get("ask_lane", []):
        print(f"  レーンに聞く: {line}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--developer", default=os.path.expanduser("~/Developer"))
    parser.add_argument("--media", help="1 つだけ（media.json の name か、フォルダの名前）")
    parser.add_argument("--work", help="一時の置き場を作る親（repo の外。既定は OS の一時の置き場）")
    parser.add_argument("--thth", help="THTH の根（既定はこの台本の repo）。検査の部品の入った作業木で下見するとき")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    V, C = _load_thth(args.thth)
    work = pathlib.Path(args.work) if args.work else pathlib.Path(tempfile.gettempdir())
    work.mkdir(parents=True, exist_ok=True)
    targets = media_list(args.media)
    if not targets:
        print("media.json が見つからない", file=sys.stderr)
        return 2
    results = [run_one(V, C, folder, media, pathlib.Path(args.developer), work) for folder, media in targets]
    if args.json:
        print(json.dumps({"yaml_reader": V.yamlread.available(), "checks_part": C is not None, "media": results},
                         ensure_ascii=False, indent=2, default=str))
    else:
        if not V.yamlread.available():
            print("YAML の中を読む部品（PyYAML）が無いので、YAML の記録は中を見ていない")
        for one in results:
            say(one)
    return 0 if all(one["ok"] for one in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
