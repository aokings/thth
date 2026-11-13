#!/usr/bin/env python3
"""台帳の器 段 2 を、7 つのメディアの実物で一度だけ確かめる台本（各メディアの repo を**読むだけ**。何も書かない）。

    python3 tools/register_adopt_realcheck.py [--developer ~/Developer] [--shadow ~/Documents/THTH/shadow] [--only 名前]

影の台帳と戻した写しは、repo の外の `<shadow>/<日時>/<メディア>/{register,restored}` に作る（毎回新しい日時の
フォルダ。前の回の物は消さない）。見る物（期待する答え `docs/期待する答え_台帳の器_段1〜3_2026-11-13.md` §3）:

- (a) 7 つとも、取り込み → 戻す → 数・名前・sha256 が全部同じ。元の範囲の物の sha256 と変えた時刻が、前と後で同じ
  （元に書いていない）。範囲は目録 7 枚の §1 とレーンの答え。
- (b) 影の台帳の上で、今の道具の書き方で 1 回直す: こぴちゃの表（読んで書き戻す）と YAML の 1 枚・関東の年度の
  置き換え（`json.dumps(indent=1)` で 1 枚を丸ごと）。触っていない原物は 1 バイトも変わらず、効かなくなる判断は
  依る所が変わった物だけ。
- (c) MMR の `research_status` を書き換える（`json.dumps(indent=2)`）: 曲の頁の判断は効いたまま。`title` なら外れる。
- (d) こぴちゃの影で、表 4 枚の入れ替えを 2 枚めのあとで止める: 読み手には前の姿・続けると後の姿。
- 変えた跡: MMR の影（git の外）で、控えどうしの差。

判断は試すための物で、どこにも書かない（`judgment.make` の返す値を `evaluate` で読むだけ）。
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime
import io
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from thth.register import adopt as A, judgment as J, keep as K, vessel as V  # noqa: E402

BY = "register_adopt_realcheck"
AT = "2026-11-14T00:00:00+09:00"

PLANS = {
    "kopicha": ("kopicha", ["src/content/**", "data/knowledge/*.csv", "data/sister-links.csv", "data/article-links.csv",
                            "data/hatena/bookmark-list.csv"], []),
    "nigami": ("nigamilab", ["data/*.csv", "data/questions/**", "data/i18n/en/*.csv", "schema/*"], []),
    "sometokagaku": ("sometokagaku", ["data/*.csv", "site/src/content/articles/*.md", "site/src/content/pillars/*.md",
                                      "site/src/content/pillars-en/*.md", "site/src/lib/entity-page-rules.ts",
                                      "site/photos.yaml", "site/owner-read.txt"], []),
    "jiangshi": ("jiangshi-lab", ["data/*.csv", "lab.yaml", "drafts/article-*.md", "images/files/*",
                                  "audits/reader/*.json"], []),
    "mmr": ("music-research", ["data/*.csv", "data/dossiers/**", "queue.json", "ledger.json", "content/**",
                               "site/src/credits/*.json", "site/src/credit-versions/versions.json", "lab.yaml"], []),
    "kanto": ("asmon/research", [
        "kanto_type/*/[0-9]*.json", "kanto_type/boshu_henkou/2027/[0-9]*.json",
        "kanto_type/submission_docs/2027/[0-9]*.json", "kanto_type/knowledge/insights/*.yaml",
        "kanto_type/school_master/_targets.csv", "kanto_type/hensachi/school_hensachi.csv",
        "kanto_type/admission_results.csv", "kanto_type/admission_results_summary.csv", "kanto_type/school_master.csv",
        "kanto_type/features_school_subject.csv", "kanto_type/types_school_subject.csv",
        "kanto_type/human_review_ledger.csv", "kanto_type/high_school_admissions.csv",
        "kanto_type/shinro_disclosure.csv", "kanto_type/round_alias.csv", "results/kanto_type/full/*.csv"], []),
    "media_hub": ("media-hub", ["registry/concepts.csv", "registry/README.md", "media.yaml"], []),
}


def write_decl(base: pathlib.Path, kinds: list) -> str:
    (base / "kinds").mkdir(parents=True, exist_ok=True)
    for kind in kinds:
        (base / "kinds" / f"{kind['kind']}.json").write_text(json.dumps(kind, ensure_ascii=False), encoding="utf-8")
    return str(base)


def mtimes(source: str, plan: dict) -> dict:
    select = A.selector(plan)
    return {rel: os.stat(path).st_mtime_ns for rel, path, _l in K.walk(source, A.tops(plan)) if select(rel)}


def roundtrip(name: str, source: str, plan: dict, place: pathlib.Path, decl: str | None = None) -> dict:
    before = A.source_catalog(source, plan)
    times = mtimes(source, plan)
    value = A.adopt(source, str(place / "register"), plan=plan, by=BY, at=AT, decl=decl)
    restored = A.restore(str(place / "register"), str(place / "restored"))
    got = K.dir_catalog(str(place / "restored"))
    after = A.source_catalog(source, plan)
    exp = {e["path"]: e["sha256"] for e in before}
    out = {e["path"]: e["sha256"] for e in got}
    endings = collections.Counter()
    for e in before:
        if e["path"].endswith(".csv"):
            data = (pathlib.Path(source) / e["path"]).read_bytes()
            crlf = data.count(b"\r\n")
            lf = data.count(b"\n") - crlf
            endings["crlf" if crlf and not lf else "lf" if lf and not crlf else "mixed" if crlf else "none"] += 1
    return {"files": len(before), "bytes": sum(e["size"] for e in before), "source_fp": V.short(K.catalog_fp(before)),
            "register_fp": V.short(value["fp"]), "restored_fp": V.short(restored["fp"]),
            "same": K.catalog_fp(before) == value["fp"] == restored["fp"] and exp == out,
            "missing": sorted(set(exp) - set(out))[:5], "extra": sorted(set(out) - set(exp))[:5],
            "differ": sorted(p for p in set(exp) & set(out) if exp[p] != out[p])[:5],
            "source_unchanged": K.catalog_fp(after) == K.catalog_fp(before) and mtimes(source, plan) == times,
            "over_64k": sum(1 for e in before if e["size"] > 64 * 1024), "max_bytes": max((e["size"] for e in before), default=0),
            "csv_endings": dict(endings), "executable": sum(1 for e in before if e["x"])}


def shas(root: pathlib.Path) -> dict:
    return {e["path"]: e["sha256"] for e in K.dir_catalog(str(root))}


def changed_files(a: dict, b: dict) -> list:
    return sorted(p for p in set(a) | set(b) if a.get(p) != b.get(p))


# ------------------------------------------------------------------ (b) と (d): こぴちゃ

KOPICHA_KINDS = [
    {"schema": "thth.kind/1", "kind": "claim", "place": "data/knowledge/claims.csv", "format": "csv",
     "id": {"field": "id"}, "questions": [{"name": "says_so", "state": "claim_status",
                                            "depends": ["statement_ja", "object_text_ja"]}]},
    {"schema": "thth.kind/1", "kind": "page", "place": "src/content/origins/*.yaml", "format": "yaml",
     "id": {"name": True}, "max_bytes": 262144},
]


def csv_roundtrip(path: pathlib.Path) -> bool:
    rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8"), newline="")))
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return buf.getvalue().encode("utf-8") == path.read_bytes()


def kopicha_edits(reg: pathlib.Path) -> dict:
    out = {}
    tables = ["claims.csv", "evidence.csv", "sources.csv", "leads.csv", "article-claims.csv"]
    out["python_csv_roundtrip_same"] = {t: csv_roundtrip(reg / "data/knowledge" / t) for t in tables}
    v = V.open_vessel(str(reg))
    claims = [r for r in v.all_records if r.kind == "claim" and r.id and not r.problems]
    judged = [J.make(v, {"record": r.ref}, "says_so", by="v", at=AT, conclusion="good") for r in claims]
    pages = [r for r in v.all_records if r.kind == "page" and r.id]
    judged_pages = [J.make(v, {"record": r.ref}, "reads_right", by="v", at=AT, conclusion="good") for r in pages]
    before = shas(reg)
    # 表: 束の取り込みの道（読んで、1 行を直し、丸ごと書き直す）。
    path = reg / "data/knowledge/claims.csv"
    rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8"), newline="")))
    target = claims[len(claims) // 2].id
    col = rows[0].index("statement_ja")
    for row in rows[1:]:
        if row and row[0] == target:
            row[col] = row[col] + "（直し）"
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    path.write_bytes(buf.getvalue().encode("utf-8"))
    # YAML: 1 枚を書き換える。
    page = pages[len(pages) // 2]
    ypath = reg / page.path
    ypath.write_bytes(ypath.read_bytes() + "# 直し（影の台帳で試した 1 行）\n".encode())
    after = shas(reg)
    v2 = V.open_vessel(str(reg))
    states = collections.Counter(J.evaluate(v2, j) for j in judged)
    page_states = collections.Counter(J.evaluate(v2, j) for j in judged_pages)
    versions_before = {r.ref: r.version for r in claims}
    rows_moved = sum(1 for r in v2.all_records if r.kind == "claim" and r.ref in versions_before
                     and versions_before[r.ref] != r.version)
    out["csv_and_yaml"] = {"files_changed": changed_files(before, after), "claim_rows_changed": rows_moved,
                           "claim_judgments": len(judged), "claim_states": dict(states),
                           "stale_is_target": [j["target"]["record"] for j in judged
                                               if J.evaluate(v2, j) != "effective"] == [f"claim:{target}"],
                           "page_judgments": len(judged_pages), "page_states": dict(page_states)}
    return out


class Stop(Exception):
    pass


def kopicha_stop(reg: pathlib.Path) -> dict:
    """表 4 枚の入れ替えを 2 枚めのあとで止め、読み手の姿を見てから続ける。"""
    four = ["data/knowledge/claims.csv", "data/knowledge/evidence.csv", "data/knowledge/sources.csv",
            "data/knowledge/leads.csv"]
    # 中身を変える（各表の最後に、見出しの数だけ欄を持つ 1 行を足す。今の表はどれも LF で終わる）。
    desired = {}
    for rel in four:
        data = (reg / rel).read_bytes()
        header = data.split(b"\n", 1)[0]
        desired[rel] = data + ("x-shadow-" + rel.split("/")[-1][:-4]).encode() + b"," * header.count(b",") + b"\n"
    before_fp = K.catalog_fp(K.view(str(reg)))
    real = K._replace
    count = [0]

    def stop(a, b):
        if count[0] == 2:
            raise Stop()
        count[0] += 1
        real(a, b)
    K._replace = stop
    try:
        K.run(str(reg), desired, what="merge", by="a", at=AT)
    except Stop:
        pass
    finally:
        K._replace = real
    mixed_fp = K.catalog_fp(K.dir_catalog(str(reg)))
    seen_stopped = K.catalog_fp(K.view(str(reg)))
    result = K.run(str(reg), desired, what="merge", by="b", at=AT)
    after_fp = K.catalog_fp(K.view(str(reg)))
    return {"before": V.short(before_fp), "mixed_on_disk": V.short(mixed_fp), "reader_while_stopped": V.short(seen_stopped),
            "reader_sees_before": seen_stopped == before_fp, "mixed_differs": mixed_fp not in (before_fp, after_fp),
            "continued_by_other": result.get("continued"), "after": V.short(after_fp),
            "after_is_bundle": all((reg / rel).read_bytes() == desired[rel] for rel in four)}


# ------------------------------------------------------------------ (b) 関東

KANTO_KIND = {"schema": "thth.kind/1", "kind": "admission_result",
              "place": "kanto_type/admission_results/[0-9]*.json", "format": "json", "records": "file",
              "max_bytes": 400000, "id": {"name": True},
              "units": {"groups": ["years", "*"], "items": ["rounds", "*"],
                        "key": ["group.year", "round", "date", "time_of_day", "gender", "subject_type"],
                        "time": {"group": "year"}, "scope": {"key_fields": ["gender"], "separate": ["capacity"]},
                        "sources": {"list": "sources", "field": "field", "split": "/,", "rest": "rounds"}}}


def kanto_edit(reg: pathlib.Path) -> dict:
    v = V.open_vessel(str(reg))
    kind = v.kinds["admission_result"]
    pick = None
    for rec in sorted((r for r in v.all_records if r.values), key=lambda r: int(r.id) if r.id.isdigit() else 0):
        raw = (reg / rec.path).read_text(encoding="utf-8")
        years = rec.values.get("years") or []
        if len(years) < 2 or any(u.ambiguous for u in rec.units):
            continue
        if json.dumps(rec.values, ensure_ascii=False, indent=1) + "\n" == raw:
            continue  # 道具の書き方とバイトで合わない 121 枚の 1 つを選ぶ（書き直しで字下げも変わる枚）
        newest = max(y.get("year") for y in years if isinstance(y.get("year"), int))
        rounds = [y for y in years if y.get("year") == newest][0].get("rounds") or []
        if rounds and isinstance(rounds[0].get("applicants"), int):
            pick = (rec, newest)
            break
    if pick is None:
        return {"skipped": "選べる枚が無い"}
    rec, year = pick
    judged = []
    for unit in rec.units:
        for value in V.values_of(rec, kind, unit):
            judged.append(J.make(v, {"record": rec.ref, "unit": unit.pointer, "value": value.name}, "reads_right",
                                 by="v", at=AT, conclusion="good"))
    before = shas(reg)
    path = reg / rec.path
    data = json.loads(path.read_text(encoding="utf-8"))
    for y in data["years"]:
        if y.get("year") == year:
            y["rounds"][0]["applicants"] += 1
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    after = shas(reg)
    v2 = V.open_vessel(str(reg))
    stale = [j for j in judged if J.evaluate(v2, j) != "effective"]
    return {"school": rec.id, "year": year, "files_changed": changed_files(before, after),
            "value_judgments": len(judged), "stale": len(stale),
            "stale_values": sorted({j["target"]["value"] for j in stale}),
            "other_year_values_effective": all(J.evaluate(v2, j) == "effective" for j in judged
                                               if str(j["target"].get("time")) != str(year))}


# ------------------------------------------------------------------ (c) と変えた跡: MMR

SONG_PAGE = ["seed_id", "research_order", "title", "artists", "album", "duration", "liked_added_date",
             "liked_added_display", "date_basis"]
MMR_KINDS = [
    {"schema": "thth.kind/1", "kind": "song", "place": "queue.json", "format": "json", "records": "rows",
     "max_bytes": 5000000, "id": {"field": "seed_id"}, "questions": [{"name": "song_page", "depends": SONG_PAGE}]},
    {"schema": "thth.kind/1", "kind": "credit", "place": "site/src/credits/*.json", "format": "json",
     "records": "file", "max_bytes": 2000000, "id": {"name": True},
     "questions": [{"name": "credit_holds", "level": "unit", "state": "status", "reason_kind": "held_kind",
                    "depends": ["seed", "title"]}],
     "units": {"groups": [], "items": ["rows", "*"], "key": ["seed"]}},
]


def mmr_edit(reg: pathlib.Path) -> dict:
    path = reg / "queue.json"
    raw = path.read_bytes()
    items = json.loads(raw)
    dumped = json.dumps(items, ensure_ascii=False, indent=2).encode("utf-8")
    tail = b"" if dumped == raw else b"\n" if dumped + b"\n" == raw else None
    v = V.open_vessel(str(reg))
    songs = [r for r in v.all_records if r.kind == "song" and r.id]
    judged = [J.make(v, {"record": r.ref}, "song_page", by="v", at=AT, conclusion="good") for r in songs]
    credit_units = [(r, u) for r in v.all_records if r.kind == "credit" and r.id for u in r.units if not u.ambiguous]
    judged_credit = [J.make(v, {"record": r.ref, "unit": u.pointer}, "credit_holds", by="v", at=AT,
                            conclusion="good") for r, u in credit_units]
    seed = "seed-liked-0443" if any(r.id == "seed-liked-0443" for r in songs) else songs[0].id
    versions = {r.ref: r.version for r in songs}
    before = shas(reg)
    first_fp = K.save_snapshot(str(reg), K.view(str(reg)), what="keep", by=BY, at=AT)

    def write(change):
        data = json.loads(path.read_bytes())
        for item in data:
            if item["seed_id"] == seed:
                item.update(change)
        path.write_bytes(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8") + (tail or b""))
    old_status = next(i.get("research_status") for i in items if i["seed_id"] == seed)
    write({"research_status": "audited" if old_status != "audited" else "merged"})
    after = shas(reg)
    v2 = V.open_vessel(str(reg))
    moved = sorted(r.ref for r in v2.all_records if r.kind == "song" and versions.get(r.ref) != r.version)
    status_states = collections.Counter(J.evaluate(v2, j) for j in judged + judged_credit)
    second_fp = K.save_snapshot(str(reg), K.view(str(reg)), what="keep", by=BY, at=AT)
    old_title = next(i.get("title") for i in items if i["seed_id"] == seed)
    write({"title": old_title + "（影の直し）"})
    v3 = V.open_vessel(str(reg))
    title_stale = [j["target"]["record"] for j in judged + judged_credit if J.evaluate(v3, j) != "effective"]
    diff = A.diff(str(reg), f"{first_fp[:16]}..{second_fp[:16]}")
    return {"indent2_same_bytes": tail is not None, "songs": len(songs), "credit_units": len(credit_units),
            "seed": seed, "files_changed": changed_files(before, after), "song_records_moved": moved,
            "after_research_status": dict(status_states), "after_title_stale": title_stale,
            "diff_between_keeps": {"changed": [c["record"] for c in diff["records"]["changed"]],
                                   "added": len(diff["records"]["added"]), "vanished": len(diff["records"]["vanished"]),
                                   "red": diff["red"]}}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--developer", default=os.path.expanduser("~/Developer"))
    parser.add_argument("--shadow", default=os.path.expanduser("~/Documents/THTH/shadow"))
    parser.add_argument("--only", action="append", default=None)
    args = parser.parse_args(argv)
    dev = pathlib.Path(args.developer)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    base = pathlib.Path(args.shadow) / stamp
    base.mkdir(parents=True)
    out = {"shadow": str(base), "yaml_reader": V.yamlread.available()}
    for name, (folder, include, exclude) in PLANS.items():
        if args.only and name not in args.only:
            continue
        source = dev / folder
        if not source.is_dir():
            out[name] = {"skipped": "フォルダが無い"}
            continue
        plan = A.parse_plan({"schema": A.PLAN_SCHEMA, "include": include, "exclude": exclude})
        place = base / name
        decl = None
        if name == "kopicha":
            decl = write_decl(base / "_decl" / name, KOPICHA_KINDS)
        elif name == "mmr":
            decl = write_decl(base / "_decl" / name, MMR_KINDS)
        elif name == "kanto":
            decl = write_decl(base / "_decl" / name, [KANTO_KIND])
        try:
            result = {"a": roundtrip(name, str(source), plan, place, decl)}
            reg = place / "register"
            if name == "kopicha":
                result["b"] = kopicha_edits(reg)
                result["d"] = kopicha_stop(reg)
            elif name == "kanto":
                result["b"] = kanto_edit(reg)
            elif name == "mmr":
                result["c"] = mmr_edit(reg)
            out[name] = result
        except Exception as exc:  # 確かめの台本なので、止まった所を報告に残す
            out[name] = {"error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps({name: out[name]}, ensure_ascii=False, default=str), flush=True)
    (base / "result.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str) + "\n",
                                      encoding="utf-8")
    print(f"結果: {base / 'result.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
