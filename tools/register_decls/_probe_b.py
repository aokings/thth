#!/usr/bin/env python3
"""書き手 b の小さな確かめ（段 4 の宣言を実物に当てる。各メディアの repo は**読むだけ**）。

    python3 tools/register_decls/_probe_b.py jiangshi [sometokagaku mmr kanto] [--developer ~/Developer]

- 宣言（`tools/register_decls/<メディア>/`）をそのまま `open_vessel(root, decl_base)` で当て、`checks.run` で検査を回す。
  読めた記録の数・読みの問題・指す欄の「先が無い」の数・検査ごとの違反の数を、`media.json` の `expected` と突き合わせる。
- 欄の名前が実物に在るかの点検（`lint`）と、`--tentative` でこの台本の仮の数え（部品が届く前に使った物）を並べる。
- `strip_checks` は部品が届く前の道（検査を外した写しを作業の置き場に書く）。今は使わない。
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import re
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from thth.register import checks as C, vessel as V  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
WORK = "/Volumes/NexDev/Developer/thth-work/stage4/run-b/probe"

ALL_KEYS = {"check", "id", "level", "when", "note"}
PARTS = {
    "unique": {"fields", "fold", "skip_blank"},
    "required": {"fields"},
    "empty": {"fields"},
    "one_of": {"fields", "mode"},
    "words": {"field", "in", "allow_blank"},
    "shape": {"field", "shape", "prefix", "allow_blank"},
    "forbid_pair": {"via", "target"},
    "count": {"from", "where", "distinct", "min", "max"},
    "judged": {"question", "min", "how"},
}
SHAPES = {
    "slug": re.compile(r"[a-z0-9]+(-[a-z0-9]+)*\Z"),
    "slug_loose": re.compile(r"[a-z0-9][a-z0-9-]*\Z"),
    "date": re.compile(r"\d{4}-\d{2}-\d{2}\Z"),
    "date_partial": re.compile(r"\d{4}(-\d{2}(-\d{2})?)?\Z"),
    "lang": re.compile(r"[a-z]{2,3}\Z"),
    "http_url": re.compile(r"https?://"),
    "https_url": re.compile(r"https://"),
    "positive_int": re.compile(r"[1-9][0-9]*\Z"),
    "sha256": re.compile(r"[0-9a-f]{64}\Z"),
}


def strip_checks(media_dir: pathlib.Path, dest: pathlib.Path) -> dict:
    (dest / "kinds").mkdir(parents=True)
    decls = {}
    for path in sorted((media_dir / "kinds").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        decls[data["kind"]] = data
        bare = {k: v for k, v in data.items() if k != "checks"}
        (dest / "kinds" / path.name).write_text(json.dumps(bare, ensure_ascii=False), encoding="utf-8")
    cpath = media_dir / "constitution.json"
    if cpath.exists():
        (dest / "constitution.json").write_text(cpath.read_text(encoding="utf-8"), encoding="utf-8")
    return decls


# ------------------------------------------------------------------ 形の点検

def lint(decls: dict, fields_seen: dict) -> list:
    out = []
    for kname, decl in decls.items():
        pointers = {p.get("field") for p in decl.get("pointers", [])}
        known = fields_seen.get(kname)

        def field_ok(f, where, kn=kname):
            ks = fields_seen.get(kn)
            if ks is not None and f not in ks:
                out.append(f"{kn}.{where}: 欄 {f} が実物に無い")

        ids = set()
        for chk in decl.get("checks", []):
            name, cid = chk.get("check"), chk.get("id")
            tag = f"{kname}:{cid}"
            if name not in PARTS:
                out.append(f"{tag}: 部品の名前 {name}")
                continue
            extra = set(chk) - ALL_KEYS - PARTS[name]
            if extra:
                out.append(f"{tag}: 知らない欄 {sorted(extra)}")
            if not isinstance(cid, str) or not V.NAME.fullmatch(cid) or cid in ids:
                out.append(f"{tag}: id の形か重なり")
            ids.add(cid)
            for f in (chk.get("when") or {}):
                field_ok(f, f"{cid}.when")
            for f in chk.get("fields", []):
                field_ok(f, cid)
            if "field" in chk:
                field_ok(chk["field"], cid)
            if name == "shape" and chk.get("shape") not in SHAPES:
                out.append(f"{tag}: 形の名前 {chk.get('shape')}")
            if name == "forbid_pair":
                if chk.get("via") not in pointers:
                    out.append(f"{tag}: via {chk.get('via')} は宣言済みの指す欄でない")
                else:
                    to = next(p["to"] for p in decl["pointers"] if p.get("field") == chk["via"])
                    for f in chk.get("target", {}):
                        field_ok(f, f"{cid}.target", to)
            if name == "count":
                src = chk.get("from") or {}
                if src.get("units"):
                    if not decl.get("units"):
                        out.append(f"{tag}: units の無い種類で from.units")
                else:
                    other = decls.get(src.get("kind"))
                    if other is None or src.get("via") not in {p.get("field") for p in other.get("pointers", [])}:
                        out.append(f"{tag}: from {src} が宣言済みの指す欄でない")
                    else:
                        for f in list(chk.get("where") or {}) + ([chk["distinct"]] if chk.get("distinct") else []):
                            field_ok(f, f"{cid}.where", src["kind"])
        if known is None and decl.get("checks"):
            out.append(f"{kname}: 読めた行が 0 か中を読めない種類で、検査の欄を実物と比べられない（知らせだけ）")
    return out


# ------------------------------------------------------------------ 仮の数え（部品の代わりではない）

def _s(v):
    return v.strip() if isinstance(v, str) else v


def _blank(v):
    return v is None or (isinstance(v, str) and not v.strip()) or v == [] or v == {}


def match(values: dict, cond: dict | None) -> bool:
    for f, test in (cond or {}).items():
        v = values.get(f)
        if "blank" in test:
            if _blank(v) != test["blank"]:
                return False
        elif "in" in test:
            if not isinstance(v, (str, int, float, bool)) or str(_s(_word(v))) not in test["in"]:
                return False
        elif "not_in" in test:
            if isinstance(v, (str, int, float, bool)) and str(_s(_word(v))) in test["not_in"]:
                return False
    return True


def _word(v):
    return V.word_of(v)


def tentative(v: V.Vessel, decls: dict) -> dict:
    recs = collections.defaultdict(list)
    for r in v.all_records:
        if r.values is not None and r.id is not None:
            recs[r.kind].append(r)
    by_id = {(r.kind, r.id): r for rs in recs.values() for r in rs}
    # 指してくる行の索引: (先の種類, via の種類, via の欄) → 先の番号 → 行
    back = collections.defaultdict(lambda: collections.defaultdict(list))
    for kname, decl in decls.items():
        for p in decl.get("pointers", []):
            for r in recs.get(kname, []):
                for t in V.pointer_targets(r, p):
                    back[(kname, p["field"])][t].append(r)
    counts = {}
    for kname, decl in decls.items():
        ptrs = {p.get("field"): p for p in decl.get("pointers", [])}
        for chk in decl.get("checks", []):
            n = 0
            name = chk["check"]
            if name == "unique":
                seen = collections.Counter()
                for r in recs.get(kname, []):
                    key = tuple(_s(_word(r.values.get(f))) or "" for f in chk["fields"])
                    if chk.get("fold") == "lower":
                        key = tuple(k.lower() for k in key)
                    if chk.get("skip_blank", True) and all(k == "" for k in key):
                        continue
                    seen[key] += 1
                n = sum(c for c in seen.values() if c > 1)
                counts[chk["id"]] = n
                continue
            for r in recs.get(kname, []):
                vals = r.values
                if not match(vals, chk.get("when")):
                    continue
                bad = False
                if name == "required":
                    bad = any(_blank(vals.get(f)) for f in chk["fields"])
                elif name == "empty":
                    bad = any(not _blank(vals.get(f)) for f in chk["fields"])
                elif name == "one_of":
                    k = sum(not _blank(vals.get(f)) for f in chk["fields"])
                    bad = k == 0 if chk.get("mode", "at_least_one") == "at_least_one" else k != 1
                elif name == "words":
                    x = vals.get(chk["field"])
                    if _blank(x):
                        bad = not chk.get("allow_blank", False)
                    else:
                        bad = str(_s(_word(x))) not in chk["in"]
                elif name == "shape":
                    x = vals.get(chk["field"])
                    if _blank(x):
                        bad = not chk.get("allow_blank", True)
                    else:
                        x = str(_s(_word(x)))
                        bad = not SHAPES[chk["shape"]].match(x) or not x.startswith(chk.get("prefix", ""))
                elif name == "forbid_pair":
                    p = ptrs[chk["via"]]
                    bad = any(match(t.values, chk["target"]) for t in
                              (by_id.get((p["to"], tid)) for tid in V.pointer_targets(r, p)) if t is not None)
                elif name == "count":
                    src = chk["from"]
                    if src.get("units"):
                        rows = [u.item for u in r.units]
                    else:
                        rows = [x.values for x in back[(src["kind"], src["via"])].get(r.id, [])]
                    rows = [x for x in rows if match(x, chk.get("where"))]
                    if chk.get("distinct"):
                        k = len({str(_s(_word(x.get(chk["distinct"])))) for x in rows
                                 if not _blank(x.get(chk["distinct"]))})
                    else:
                        k = len(rows)
                    bad = (k < chk.get("min", 0)) or ("max" in chk and k > chk["max"])
                elif name == "judged":
                    bad = None
                if bad:
                    n += 1
            counts[chk["id"]] = n
    return counts


def probe(media: str, developer: pathlib.Path, tentative_too: bool = False) -> dict:
    """宣言を検査つきでそのまま当てる（`open_vessel(root, decl_base)` と `checks.run`）。"""
    media_dir = HERE / media
    card = json.loads((media_dir / "media.json").read_text(encoding="utf-8"))
    root = developer / card["source"]
    decls = {}
    for path in sorted((media_dir / "kinds").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        decls[data["kind"]] = data
    started = time.monotonic()
    v = V.open_vessel(str(root), str(media_dir))
    read_s = time.monotonic() - started
    exp = card["expected"]
    by_kind = collections.Counter(r.kind for r in v.all_records)
    no_id = collections.Counter(r.kind for r in v.all_records if r.id is None)
    words = collections.Counter((p["kind"], w.split(":")[0]) for p in V.problems(v) for w in p["words"])
    graph = V.links(v)
    dangling = collections.Counter(f"{e['from'].split(':')[0]}.{e['field']}" for e in graph["dangling"])
    fields_seen = {}
    for r in v.all_records:
        if r.values is not None:
            fields_seen.setdefault(r.kind, set()).update(r.values)
    rec_cmp = {k: [by_kind.get(k, 0), n, by_kind.get(k, 0) == n] for k, n in exp["records"].items()}
    extra_kinds = sorted(set(by_kind) - set(exp["records"]))
    dang_cmp = {k: [dangling.get(k, 0), n, dangling.get(k, 0) == n] for k, n in exp["dangling"].items()}
    started = time.monotonic()
    result = C.run(v)
    check_s = time.monotonic() - started
    got = {c["id"]: c for c in result["list"]}
    chk_cmp = {k: [got[k]["violations"] if k in got else None, n, k in got and got[k]["violations"] == n]
               for k, n in exp["checks"].items()}
    side = {k: {w: c.get(w) for w in ("unverifiable", "unresolved", "not_inspected", "failed") if c.get(w)}
            for k, c in got.items()}
    out = {
        "media": media,
        "records_ok": all(x[2] for x in rec_cmp.values()) and not extra_kinds,
        "records": {k: x for k, x in rec_cmp.items() if not x[2]} or "全部合う",
        "records_total": sum(by_kind.values()), "extra_kinds": extra_kinds,
        "no_id": dict(no_id), "problems": {f"{k}:{w}": n for (k, w), n in sorted(words.items())},
        "dangling_ok": all(x[2] for x in dang_cmp.values()),
        "dangling": {k: x for k, x in dang_cmp.items() if not x[2]} or "全部合う",
        "dangling_undeclared": {k: n for k, n in dangling.items() if k not in exp["dangling"]},
        "blank_pointers": len(graph["blank"]), "ambiguous": len(graph["ambiguous"]),
        "cycles": len(graph["cycles"]), "units": sum(len(r.units) for r in v.all_records),
        "lint": lint(decls, fields_seen),
        "checks_total": result["total"], "checks_expected": len(exp["checks"]),
        "checks_unknown": sorted(set(got) - set(exp["checks"])),
        "checks_ok": all(x[2] for x in chk_cmp.values()),
        "checks": {k: x for k, x in chk_cmp.items() if not x[2]} or "全部合う",
        "checks_side": {k: s for k, s in side.items() if s},
        "red": len(C.red(result)), "warn": result["warn"], "failed": result["failed"],
        "seconds": {"read": round(read_s, 2), "checks": round(check_s, 2)},
    }
    if tentative_too:
        t = tentative(v, decls)
        out["tentative_differs"] = {k: [t.get(k), got[k]["violations"]] for k in got
                                    if t.get(k) != got[k]["violations"]}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("media", nargs="+")
    ap.add_argument("--developer", default=os.path.expanduser("~/Developer"))
    ap.add_argument("--tentative", action="store_true", help="この台本の仮の数えも並べる（部品との食い違いを見る）")
    args = ap.parse_args(argv)
    rc = 0
    for media in args.media:
        try:
            out = probe(media, pathlib.Path(args.developer), args.tentative)
        except Exception as exc:  # 確かめの台本なので、止まった所を出す
            out = {"media": media, "error": f"{type(exc).__name__}: {exc}"}
            rc = 1
        print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
