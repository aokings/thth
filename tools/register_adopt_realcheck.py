#!/usr/bin/env python3
"""台帳の器 段 2 を、7 つのメディアの実物で一度だけ確かめる台本（各メディアの repo を**読むだけ**。何も書かない）。

    python3 tools/register_adopt_realcheck.py [--developer ~/Developer] [--shadow ~/Documents/THTH/shadow] [--only 名前]

影の台帳と戻した写しは、repo の外の `<shadow>/<日時>/<メディア>/{register,restored}` に作る（毎回新しい日時の
フォルダ。前の回の物は消さない）。見る物（期待する答え `docs/期待する答え_台帳の器_段1〜3_2026-11-13.md` §3）:

- (a) 7 つとも、取り込み → 戻す → 数・名前・sha256 が全部同じ。元の範囲の物の sha256 と変えた時刻が、前と後で同じ
  （元に書いていない）。範囲は目録 7 枚の §1 とレーンの答え。
- 範囲: 目録の数（`EXPECTED`）と、元を glob で数え直した数を比べ、glob で見つかった枚の和と台帳の枚を比べる
  （範囲の中の欠けと、範囲の外の混入の両方。範囲の JSON の選び方を使わずに確かめる）。(a) の往復の比べも、
  戻した物をこの一覧と元のバイトの sha256 に比べる。
- (b) こぴちゃの 8ead0fc と 3398d6d28 の再演（`--replay <フォルダ>` に、各 commit の親の木と後の 3 枚を置く）:
  親の木を取り込んでから 3 枚を当て、3 枚だけが変わり、外れた判断が期待の集合と等しいかを見る。`--replay` が無いときは、代わりに表を読んで
  書き戻す道と YAML の 1 枚で確かめる（`b_substitute`。再演ではない）。関東の年度の置き換え
  （`json.dumps(indent=1)` で 1 枚を丸ごと）。触っていない原物は 1 バイトも変わらず、効かなくなる判断は
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
import hashlib
import io
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from thth.register import adopt as A, judgment as J, keep as K, vessel as V  # noqa: E402

BY = "register_adopt_realcheck"
AT = "2026-11-14T00:00:00+09:00"

# 取り込む物の範囲。目録 7 枚（docs/目録_台帳の器_*_2026-11-13.md）の §1「移す物」と、§5「検収の記録」
# （判断の元の記録として置く物）から作る。
PLANS = {
    "kopicha": ("kopicha", [
        "src/content/**", "data/knowledge/*.csv", "data/sister-links.csv", "data/article-links.csv",
        "data/hatena/bookmark-list.csv",
        # §5 検収の記録（束の作業の場 staging/ のうち、検めの判定・検めに渡した中身・中央の判断・取り込みの印）
        "data/knowledge/staging/*/verify_v.csv", "data/knowledge/staging/*/verify_t.csv",
        "data/knowledge/staging/*/verify_input.csv", "data/knowledge/staging/*/verify_input_t.csv",
        "data/knowledge/staging/*/central.csv", "data/knowledge/staging/*/.merged",
        "docs/knowledge/*_結果_*.md"], []),
    "nigami": ("nigamilab", [
        "data/*.csv", "data/questions/**", "data/i18n/en/*.csv", "schema/*",
        # §5 検収の記録
        "data/staging/**/VERIFY.md", "data/staging/**/verify.json", "data/staging/**/verify_r*.json",
        "data/staging/**/AUDIT.md", "data/staging/**/*.merged"],
        # 束の作業の場の道具の置き場（symlink。検収の記録は無い）
        ["data/staging/**/node_modules/**"]),
    "sometokagaku": ("sometokagaku", [
        "data/*.csv", "site/src/content/articles/*.md", "site/src/content/pillars/*.md",
        "site/src/content/pillars-en/*.md", "site/src/lib/entity-page-rules.ts", "site/photos.yaml",
        "site/owner-read.txt",
        # §5 検収の記録
        "staging/*/verify.json", "staging/*/recheck.json", "staging/*/.merged", "docs/original-check-log.md"], []),
    "jiangshi": ("jiangshi-lab", [
        "data/*.csv", "lab.yaml", "drafts/article-*.md", "images/files/*",
        # §5 検収の記録
        "audits/reader/*.json", "docs/original-check-log.md", "staging/*/verify.json", "staging/*/recheck.json"], []),
    "mmr": ("music-research", [
        "data/*.csv", "data/dossiers/**", "queue.json", "ledger.json", "content/**",
        "site/src/credits/*.json", "site/src/credit-versions/versions.json", "lab.yaml",
        # §5 検収の記録（git の外）
        "staging/credits/judg/*"], []),
    "kanto": ("asmon/research", [
        "kanto_type/*/[0-9]*.json", "kanto_type/boshu_henkou/2027/[0-9]*.json",
        "kanto_type/submission_docs/2027/[0-9]*.json", "kanto_type/knowledge/insights/*.yaml",
        "kanto_type/school_master/_targets.csv", "kanto_type/hensachi/school_hensachi.csv",
        "kanto_type/admission_results.csv", "kanto_type/admission_results_summary.csv", "kanto_type/school_master.csv",
        "kanto_type/features_school_subject.csv", "kanto_type/types_school_subject.csv",
        "kanto_type/human_review_ledger.csv", "kanto_type/high_school_admissions.csv",
        "kanto_type/shinro_disclosure.csv", "kanto_type/round_alias.csv", "results/kanto_type/full/*.csv",
        # §5 検収の記録
        "kanto_type/admission_results/_audit_*.csv", "kanto_type/admission_results/_audit_20261004/*",
        "kanto_type/admission_results/_audit_2025/*", "kanto_type/admission_results/_audit_2024/*",
        "kanto_type/events/_patrol_verified.json"], []),
    "media_hub": ("media-hub", ["registry/concepts.csv", "registry/README.md", "media.yaml"], []),
}

# 期待値: 目録に書かれた数（選び方と別に確かめる）。数え直しは、範囲の JSON（PLANS）でなく、ここの型で元を
# glob し（器の選び方を使わない）、目録の数と比べ、見つかった枚が全部台帳に入ったかを見る。目録の数と今の数が
# 違えば「動いた」として差を言う（目録は 11/13 の数）。None は目録に数が無い（未確認・名前だけ）。
EXPECTED = {
    "kopicha": {"source": "docs/目録_台帳の器_こぴちゃ_2026-11-13.md", "groups": [
        ("src/content/**/*", 296, "§1.1"), ("data/knowledge/*.csv", 11, "§1.1"),
        ("data/knowledge/staging/*/verify_v.csv", 227, "§5 検証 V"),
        ("data/knowledge/staging/*/verify_t.csv", 225, "§5 検証 T"),
        ("data/knowledge/staging/*/verify_input.csv", 228, "§5 検めに渡した中身"),
        ("data/knowledge/staging/*/verify_input_t.csv", 77, "§5 検めに渡した中身 T"),
        ("data/knowledge/staging/*/central.csv", 226, "§5 中央の判断"),
        ("data/knowledge/staging/*/.merged", 215, "§5 束の取り込みの印"),
        ("docs/knowledge/*_結果_*.md", None, "§5 結果の書（10 枚ほど・名前だけ）"),
        ("data/sister-links.csv", 1, "期待する答え §3 (a)・問い 3"), ("data/article-links.csv", 1, "問い 3"),
        ("data/hatena/bookmark-list.csv", 1, "問い 3")]},
    "nigami": {"source": "docs/目録_台帳の器_にがみラボ_2026-11-13.md", "groups": [
        ("data/*.csv", 33, "§1.1"), ("data/questions/**/*", 174, "§1.2"), ("data/i18n/en/*.csv", 4, "期待する答え §3 (a)"),
        ("schema/*", 2, "期待する答え §3 (a)"),
        ("data/staging/**/VERIFY.md", 109, "§5 束の検め"), ("data/staging/**/verify.json", 8, "§5 ほかの検め"),
        ("data/staging/**/verify_r*.json", 2, "§5 ほかの検め"), ("data/staging/**/AUDIT.md", 3, "§5 ほかの検め"),
        ("data/staging/**/*.merged", 2, "§5 束の取り込みの印")],
        "skip_parts": ["node_modules"]},
    "sometokagaku": {"source": "docs/目録_台帳の器_染めと化学_2026-11-13.md", "groups": [
        ("data/*.csv", 8, "§1.1"), ("site/src/content/articles/*.md", 33, "§1.2"),
        ("site/src/content/pillars/*.md", 8, "§1.2"), ("site/src/content/pillars-en/*.md", 8, "§1.2"),
        ("site/src/lib/entity-page-rules.ts", 1, "§1.2"), ("site/owner-read.txt", 1, "§1.2・§5"),
        ("site/photos.yaml", 1, "§1.2"),
        ("staging/*/verify.json", 83, "§5 独立の検証"), ("staging/*/recheck.json", 82, "§5 3 度目の読み"),
        ("staging/*/.merged", 18, "§5 取り込みの印"), ("docs/original-check-log.md", 1, "§5 原文確認の記録")]},
    "jiangshi": {"source": "docs/目録_台帳の器_キョンシーラボ_2026-11-13.md", "groups": [
        ("data/*.csv", 11, "§1.1"), ("lab.yaml", 1, "§1.1"), ("drafts/article-*.md", 38, "§1.1"),
        ("images/files/*", 26, "§1.1"), ("audits/reader/*.json", 56, "§5 読者監査（結果 54・state・scope）"),
        ("docs/original-check-log.md", 1, "§5 原文確認の記録"),
        ("staging/*/verify.json", None, "§5 束の検め（未確認）"), ("staging/*/recheck.json", None, "§5 束の検め（未確認）")]},
    "mmr": {"source": "docs/目録_台帳の器_MMR_2026-11-13.md", "groups": [
        ("data/*.csv", 15, "§0・§1.1"), ("queue.json", 1, "§1.1"), ("ledger.json", 1, "§1.1"),
        ("content/index.yaml", 1, "§1.1"), ("site/src/credits/*.json", 789, "§1.1"),
        ("site/src/credit-versions/versions.json", 1, "§1.1"), ("lab.yaml", 1, "§1.1"),
        ("content/reviews/**/*", 414, "§5 記事の監査"), ("staging/credits/judg/*", 794, "§5 照合台帳の判定"),
        ("content/**/*", None, "§1.1 記事の本文ほか（全部の数は目録に無い）"),
        ("data/dossiers/**/*", None, "§1.1 調べの書（数は目録に無い）")]},
    "kanto": {"source": "docs/目録_台帳の器_関東_2026-11-13.md", "groups": [
        ("kanto_type/admission_results/[0-9]*.json", 265, "§1.1"), ("kanto_type/school_records/[0-9]*.json", 265, "§1.1"),
        ("kanto_type/school_master/[0-9]*.json", 265, "§1.1"), ("kanto_type/events/[0-9]*.json", 264, "§1.1"),
        ("kanto_type/procedures/[0-9]*.json", 46, "§1.1"), ("kanto_type/boshu_henkou/2027/[0-9]*.json", 163, "§1.1"),
        ("kanto_type/knowledge/insights/*.yaml", 229, "§1.1"),
        ("kanto_type/admission_results/_audit_*.csv", 8, "§5 入試結果の監査（項目ごと）"),
        ("kanto_type/admission_results/_audit_20261004/*", 238, "§5 入試結果の監査（2026 年度）"),
        ("kanto_type/admission_results/_audit_2025/*", 265, "§5 入試結果の監査（2025 年度）"),
        ("kanto_type/admission_results/_audit_2024/*", 207, "§5 入試結果の監査（2024 年度）"),
        ("kanto_type/human_review_ledger.csv", 1, "§5 型の人の検収"),
        ("kanto_type/events/_patrol_verified.json", 1, "§5 巡回の印"),
        ("kanto_type/*/[0-9]*.json", None, "§1.1 面ごとの 1 校 1 枚（上の 5 つの面のほかも）"),
        ("kanto_type/submission_docs/2027/[0-9]*.json", None, "§1.1"),
        ("kanto_type/school_master/_targets.csv", 1, "§1.1"), ("kanto_type/hensachi/school_hensachi.csv", 1, "§1.1"),
        ("kanto_type/admission_results.csv", 1, "§1.1"), ("kanto_type/admission_results_summary.csv", 1, "§1.1"),
        ("kanto_type/school_master.csv", 1, "§1.1"), ("kanto_type/features_school_subject.csv", 1, "§1.1"),
        ("kanto_type/types_school_subject.csv", 1, "§1.1"), ("kanto_type/high_school_admissions.csv", 1, "§1.1"),
        ("kanto_type/shinro_disclosure.csv", 1, "§1.1"), ("kanto_type/round_alias.csv", 1, "§1.1"),
        ("results/kanto_type/full/*.csv", None, "§1.1 結合した表")]},
    "media_hub": {"source": "docs/目録_台帳の器_media-hub_2026-11-13.md", "groups": [
        ("registry/concepts.csv", 1, "§1.1"), ("registry/README.md", 1, "§1.1"), ("media.yaml", 1, "期待する答え §3 (a)")]},
}


def _glob(source: str, pattern: str, skip_parts=()) -> list:
    import glob as globmod
    out = []
    for p in globmod.glob(pattern, root_dir=source, recursive=True, include_hidden=True):
        rel = p.replace(os.sep, "/")
        path = os.path.join(source, p)
        if os.path.isfile(path) and not os.path.islink(path) and not set(rel.split("/")) & set(skip_parts):
            out.append(rel)
    return sorted(out)


def expected_files(name: str, source: str) -> list:
    """範囲に入るはずの枚の名前の一覧。範囲の JSON（PLANS）と器の選び方を使わず、目録の型（EXPECTED）で元を glob した物の和。"""
    skip = EXPECTED[name].get("skip_parts", ())
    found = set()
    for pattern, _count, _where in EXPECTED[name]["groups"]:
        found.update(_glob(source, pattern, skip))
    return sorted(found)


def scope_check(name: str, source: str, register: str) -> dict:
    """目録の数と、範囲に入れた物を、器の選び方と別に比べる。範囲の中の欠け（missing）と、範囲の外の混入（extra・
    目録の型のどれにも当たらない台帳の枚）の両方を見る。"""
    inside = {e["path"] for e in K.dir_catalog(register)}
    skip = EXPECTED[name].get("skip_parts", ())
    groups = []
    for pattern, expected, where in EXPECTED[name]["groups"]:
        found = _glob(source, pattern, skip)
        missing = [p for p in found if p not in inside]
        groups.append({"glob": pattern, "where": where, "inventory": expected, "now": len(found),
                       "moved": None if expected is None else len(found) - expected,
                       "not_adopted": len(missing), "not_adopted_head": missing[:3]})
    want = set(expected_files(name, source))
    missing, extra = sorted(want - inside), sorted(inside - want)
    return {"inventory": EXPECTED[name]["source"], "groups": groups, "expected": len(want), "register": len(inside),
            "missing": missing[:20], "missing_count": len(missing), "extra": extra[:20], "extra_count": len(extra),
            "all_adopted": not missing and not extra}


def write_decl(base: pathlib.Path, kinds: list) -> str:
    (base / "kinds").mkdir(parents=True, exist_ok=True)
    for kind in kinds:
        (base / "kinds" / f"{kind['kind']}.json").write_text(json.dumps(kind, ensure_ascii=False), encoding="utf-8")
    return str(base)


def mtimes(source: str, plan: dict) -> dict:
    select = A.selector(plan)
    return {rel: os.stat(path).st_mtime_ns for rel, path, _l in K.walk(source, A.tops(plan)) if select(rel)}


def _place_of(rel: str) -> str:
    """種類の宣言の place に書ける字（英数と `_.-/`）だけの型。ほかの字は 1 字の `?` にする。"""
    return "".join(c if (c.isascii() and (c.isalnum() or c in "_.-/")) else "?" for c in rel)


def large_decl(base: pathlib.Path, source: str, plan: dict, kinds: list) -> tuple:
    """取り込みは、どの種類の宣言にも当たらない 64 KB を超える枚を断る（期待する答え §3 (a)）。実物の確かめでは、
    そういう枚を名指しした種類（中身を読まない bytes・フォルダごとに 1 つ）を、メディアの宣言に足して取り込む。
    レーンが本当の種類を書くまでの代わり。返すのは `(宣言の置き場, 足した枚の数)`。"""
    probe = write_decl(base / "probe", kinds)
    declared = V.load_declarations(source, probe)["kinds"].values()
    groups = {}
    for e in A.source_catalog(source, plan):
        if e["size"] > V.FILE_MAX_DEFAULT and not any(V._matches(k, e["path"]) for k in declared):
            groups.setdefault(os.path.dirname(e["path"]), []).append(e)
    extra = []
    for i, (_folder, entries) in enumerate(sorted(groups.items()), 1):
        extra.append({"schema": "thth.kind/1", "kind": f"large_{i}", "place": [_place_of(e["path"]) for e in entries],
                      "format": "bytes", "id": {"name": True},
                      "max_bytes": min(V.TABLE_MAX_DEFAULT, max(e["size"] for e in entries))})
    return write_decl(base / "decl", kinds + extra), sum(len(v) for v in groups.values())


def roundtrip(name: str, source: str, plan: dict, place: pathlib.Path, decl: str | None = None) -> dict:
    before = A.source_catalog(source, plan)
    times = mtimes(source, plan)
    value = A.adopt(source, str(place / "register"), plan=plan, by=BY, at=AT, decl=decl)
    restored = A.restore(str(place / "register"), str(place / "restored"))
    got = K.dir_catalog(str(place / "restored"))
    after = A.source_catalog(source, plan)
    out = {e["path"]: e["sha256"] for e in got}
    # 比べる相手は、器の選び方と別に作った名前の一覧（目録の型で glob）と、元のバイトから計った sha256。
    exp = {rel: hashlib.sha256((pathlib.Path(source) / rel).read_bytes()).hexdigest()
           for rel in expected_files(name, source)}
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
            "independent_files": len(exp),
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
    {"schema": "thth.kind/1", "kind": "evidence", "place": "data/knowledge/evidence.csv", "format": "csv",
     "id": {"field": "id"}},
    {"schema": "thth.kind/1", "kind": "entity", "place": "data/knowledge/entities.csv", "format": "csv",
     "id": {"field": "id"}},
    {"schema": "thth.kind/1", "kind": "article_claim", "place": "data/knowledge/article-claims.csv", "format": "csv",
     "id": {"key": ["file", "claim_id"]}},
]

# 試験 2 (b) の再演（期待する答え §3 (b)）: こぴちゃの 2 つの commit が変えた 3 枚ずつ。
REPLAY = {
    "8ead0fc": ["data/knowledge/claims.csv", "data/knowledge/evidence.csv", "data/knowledge/article-claims.csv"],
    "3398d6d28": ["src/content/origins/sikkim.yaml", "data/knowledge/entities.csv",
                  "data/knowledge/article-claims.csv"],
}


REPLAY_EXPORT = ("mkdir -p {dir}/{c}/parent && git -C <kopicha> archive {c}^ | tar -x -C {dir}/{c}/parent; "
                 "for p in <3 枚>; do mkdir -p {dir}/{c}/after/$(dirname $p) && "
                 "git -C <kopicha> show {c}:$p > {dir}/{c}/after/$p; done")


def _depends(kind_name: str, question: str):
    for kind in KOPICHA_KINDS:
        if kind["kind"] == kind_name:
            for q in kind.get("questions", []):
                if q["name"] == question:
                    return q.get("depends")
    return None


def kopicha_replay(base: pathlib.Path, replay: pathlib.Path) -> dict:
    """試験 2 (b) の再演。commit ごとに、`<replay>/<commit>/parent/` の親 commit の木（`git archive <commit>^` を
    展開した物）を、範囲（PLANS の kopicha）で新しい影の台帳に取り込み、全部の記録に判断を作ってから、今の道具と
    同じく `<replay>/<commit>/after/` の 3 枚を台帳に直に書く。見る物:
    - 当てる前の台帳が親の木（範囲の中の全部）と同じか（basis_is_parent）
    - 変わった枚がその 3 枚だけで、ほかの枚が親の木と同じか（files_changed・others_same_as_parent）
    - 外れた判断の集合が、期待の集合と等しいか（stale_as_expected）。期待は判断の評価と別に作る: 版の動いた記録の
      うち、問いの依る欄が無い物か、依る欄の値が動いた物。"""
    out = {}
    folder, include, exclude = PLANS["kopicha"]
    plan = A.parse_plan({"schema": A.PLAN_SCHEMA, "include": include, "exclude": exclude})
    for commit, paths in REPLAY.items():
        parent, after_dir = replay / commit / "parent", replay / commit / "after"
        if not parent.is_dir() or not all((after_dir / p).is_file() for p in paths):
            out[commit] = {"skipped": f"{parent} か {after_dir} の 3 枚が無い",
                           "export": REPLAY_EXPORT.format(dir=replay, c=commit)}
            continue
        place = base / f"replay-{commit}"
        decl, _large = large_decl(place, str(parent), plan, KOPICHA_KINDS)
        reg = place / "register"
        adopted = A.adopt(str(parent), str(reg), plan=plan, by=BY, at=AT, decl=decl)
        parent_shas = {e["path"]: e["sha256"] for e in A.source_catalog(str(parent), plan)}
        before = shas(reg)
        v = V.open_vessel(str(reg))
        records = {r.ref: r for r in v.all_records if r.id and not r.problems}
        judged = []
        for ref, r in records.items():
            question = "says_so" if r.kind == "claim" else "reads_right"
            judged.append((ref, question, J.make(v, {"record": ref}, question, by="v", at=AT, conclusion="good")))
        for p in paths:  # 今の道具と同じく、台帳のファイルに直に書く
            (reg / p).write_bytes((after_dir / p).read_bytes())
        after = shas(reg)
        v2 = V.open_vessel(str(reg))
        now = {r.ref: r for r in v2.all_records if r.id}
        moved = sorted(ref for ref, r in records.items() if ref in now and now[ref].version != r.version)
        added = sorted(ref for ref in now if ref not in records)
        stale = sorted(ref for ref, _q, j in judged if J.evaluate(v2, j) != "effective")
        expected = []
        for ref, question, _j in judged:
            if ref not in now:
                expected.append(ref)  # 消えた記録の判断も外れる
                continue
            if ref not in moved:
                continue
            depends = _depends(ref.split(":")[0], question)
            if depends is None or any((records[ref].values or {}).get(f) != (now[ref].values or {}).get(f)
                                      for f in depends):
                expected.append(ref)
        expected = sorted(set(expected))
        lines = {}
        for p in paths:
            old = (parent / p).read_bytes().splitlines(keepends=True)
            new = (after_dir / p).read_bytes().splitlines(keepends=True)
            lines[p] = {"added": len(set(new) - set(old)), "removed": len(set(old) - set(new))}
        out[commit] = {"register": str(reg), "files": adopted["files"],
                       "basis_is_parent": adopted["same"] and before == parent_shas,
                       "files_changed": changed_files(before, after),
                       "others_same_as_parent": all(after[p] == parent_shas[p] for p in parent_shas if p not in paths),
                       "line_changes": lines, "records_moved": moved, "records_added": added,
                       "judgments": len(judged), "stale": stale, "expected_stale": expected,
                       "stale_as_expected": stale == expected}
    return out


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
    first_fp = K.save_snapshot(str(reg), K.view(str(reg)), what="keep", by=BY, at=AT)["snapshot"]

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
    second_fp = K.save_snapshot(str(reg), K.view(str(reg)), what="keep", by=BY, at=AT)["snapshot"]
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
    parser.add_argument("--replay", default=None,
                        help="こぴちゃの 8ead0fc と 3398d6d28 の、親 commit の木（parent/）と後の 3 枚（after/）を置いたフォルダ")
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
        kinds = {"kopicha": KOPICHA_KINDS, "mmr": MMR_KINDS, "kanto": [KANTO_KIND]}.get(name, [])
        try:
            decl, large = large_decl(base / "_decl" / name, str(source), plan, kinds)
            result = {"a": roundtrip(name, str(source), plan, place, decl), "large_declared": large}
            reg = place / "register"
            result["scope"] = scope_check(name, str(source), str(reg))
            if name == "kopicha":
                if args.replay:
                    result["b_replay"] = kopicha_replay(place, pathlib.Path(args.replay))
                else:
                    result["b_replay"] = {"skipped": "--replay が無い（再演の 3 枚ずつが要る）"}
                result["b_substitute"] = kopicha_edits(reg)  # 再演の代わりではない（表を読んで書き戻す道の確かめ）
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
