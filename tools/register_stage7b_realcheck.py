#!/usr/bin/env python3
"""台帳の器 段 7 の後半「関門が今と同じ答え」（指示 `docs/指示_台帳の器_段6と段7_書く口と出口の残り_2026-11-14.md`
段 7 の「済み」の線の 4）を、各メディアの実物で確かめる台本。各メディアの repo は**読むだけ**（台本は書き込みの無い
物だけを動かし、byte code も書かせない）。写しと器の台帳は `<work>/run-<日時>/` の下だけに書く。

    python3 tools/register_stage7b_realcheck.py [--developer /Volumes/NexDev/Developer]
                                                [--work /Volumes/NexDev/Developer/thth-work/stage7b] [--json]

1. 出した URL の関門（こぴちゃ・キョンシーラボ・関東）: 今の台本の決まり（許す転送の番号・二重の扱い・例外・関東の
   一時の転送と Functions の対応表）を出し口の宣言 `published` に写し、今の控え（published-urls.txt）を器の控えに移し、
   build の出力（手元の dist）からメディアの台本と同じ読み方で一覧を作って、器の関門を回す。今の台本も同じ入力で動かし
   （読むだけの台本）、赤の URL の集まりと rc が同じかを見る。違反を 1 つ足した写し（控えに無い頁を 1 行足す）で、
   台本も器も赤になるか。build の出力が無ければ、控えそのものを一覧として使い「build の出力は未確認」と出す。
2. 読者に見える欄（キョンシーの `public_jargon.py`・染めと化学の `audit_site.py` の (v)）: 字のままの語で写せる分を
   `internal_words` と `reader_fields` に写し、台帳の表の写しで `reader_check.check` を回す。今の台本の答え（キョンシーは
   台帳の欄・染めと化学は build の出力の頁）と、違反 0 どうしで合うか。語を 1 つ足した写しで、台本も器も赤か。
   正規表現でしか言えない分は `docs/固有として残す決まり_台帳の器_<メディア>_2026-11-14.md` に書いた（台本は書かない）。

出力はメディアごと・関門ごとの「合う・合わない・未確認」と rc（合わないが 1 つでもあれば 1）。
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import importlib.util
import io
import json
import pathlib
import re
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))
sys.dont_write_bytecode = True          # メディアの台本を import しても __pycache__ を書かない

from thth.register import published as P   # noqa: E402
from thth.register import reader_check as RC   # noqa: E402

NOW = "2026-11-14T12:00:00+09:00"
FAKE = "thth-stage7b-fake"

GATES = {
    "こぴちゃ": {"repo": "kopicha", "script": "scripts/gate_published_urls.py", "dist": "dist",
              "published": "data/published-urls.txt", "redirects": "dist/_redirects", "gone": "data/gone-urls.txt",
              "status": list(range(300, 310)), "temp": [], "with_page": "warn", "skip_200": False, "prefix": "/"},
    "キョンシーラボ": {"repo": "jiangshi-lab", "script": "scripts/gate_published_urls.py", "dist": "site/dist",
                "published": "site/published-urls.txt", "redirects": "site/public/_redirects",
                "gone": "site/gone-urls.txt", "status": list(range(300, 310)), "temp": [], "with_page": "warn",
                "skip_200": False, "prefix": "/"},
    "関東": {"repo": "asmon", "script": "research/kanto_type/published_urls_gate.py", "dist": "docs/kanto_html",
           "published": "research/kanto_type/published-urls.txt", "redirects": "docs/kanto_html/_redirects",
           "gone": "research/kanto_type/gone-urls.txt", "status": [301, 308], "temp": [302, 307],
           "with_page": "red", "skip_200": True, "prefix": "/kanto/"},
}

JIANGSHI_WORDS = ["本束", "この束", "同束", "今回の束", "次の束", "別の束", "台帳", "統合担当", "収集担当", "検証担当",
                  "work_setting", "source_record", "lab_reading", "official_work_info", "journalistic_report",
                  "scholarly_study", "primary_text", "contextual", "supported", "integrator_checked",
                  "human_reviewed", "machine_read", "主張化", "主張にし", "主張に起こ"]
JIANGSHI_FIELDS = {"lead": ("leads.csv", ["title_ja", "lead_text_ja"]),
                   "entity": ("entities.csv", ["description_ja"]),
                   "claim": ("claims.csv", ["object_text_ja", "scope_ja"]),
                   "evidence": ("evidence.csv", ["locator"]), "source": ("sources.csv", ["title"])}
SOMETOKA_WORDS = ["統括", "統合担当", "執筆担当", "照合担当", "統合稿", "未撮影", "**", "台帳", "照合", "入口", "出典表",
                  "needs_verification", "integrator_checked", "human_checked", "abstract_checked",
                  "machine_read", "metadata_only"]
SOMETOKA_FIELDS = {"claim": ("claims.csv", ["object_text_ja", "object_text_en", "scope_ja", "scope_en"]),
                   "evidence": ("evidence.csv", ["locator", "finding_ja", "finding_en", "limitations"]),
                   "source": ("sources.csv", ["title", "authors", "publication"]),
                   "lead": ("leads.csv", ["title_ja", "title_en", "lead_text_ja", "lead_text_en"]),
                   "entity": ("entities.csv", ["canonical_name_ja", "canonical_name_en", "scientific_name",
                                               "description_ja", "description_en"])}


def module(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def mtime(path: pathlib.Path) -> str:
    return datetime.datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="minutes")


def make_ledger(led: pathlib.Path, kinds: dict, outlets: dict, files: dict | None = None) -> pathlib.Path:
    (led / ".thth/kinds").mkdir(parents=True)
    for name, place in kinds.items():
        decl = {"schema": "thth.kind/1", "kind": name, "place": place, "format": "csv", "id": {"field": "id"}}
        (led / f".thth/kinds/{name}.json").write_text(json.dumps(decl, ensure_ascii=False), encoding="utf-8")
    (led / ".thth/outlets.json").write_text(json.dumps(outlets, ensure_ascii=False, indent=1), encoding="utf-8")
    for rel, src in (files or {}).items():
        (led / rel).parent.mkdir(parents=True, exist_ok=True)
        if isinstance(src, bytes):
            (led / rel).write_bytes(src)
        else:
            shutil.copyfile(src, led / rel)
    return led


# ------------------------------------------------------------------ 1. 出した URL の関門

def script_gate(repo: pathlib.Path, g: dict, published: pathlib.Path, norm) -> tuple:
    """今の台本を読むだけで動かす: `(rc, 赤の道の集まり, 出力の要約の行)`。"""
    if g["repo"] == "asmon":
        args = ["--dist", g["dist"], "--published", str(published), "--gone", g["gone"]]
    else:
        args = ["--dist", g["dist"], "--published", str(published), "--gone", g["gone"]]
        if (repo / g["redirects"]).exists():
            args += ["--redirects", g["redirects"]]
    r = subprocess.run([sys.executable, "-B", g["script"], *args], cwd=repo, capture_output=True, text=True)
    bad = set()
    summary = ""
    for line in r.stdout.splitlines():
        s = line.strip()
        if s.startswith("published url gate:"):
            summary = s
        if s.startswith("PUBURL NG "):
            bad.add(norm(s[len("PUBURL NG "):].split(": ", 1)[0]))
        elif s.startswith("- ") and ": " in s:
            bad.add(norm(s[2:].split(": ", 1)[0]))
    return r.returncode, bad, summary


def gate_decl(mod, repo: pathlib.Path, g: dict) -> tuple:
    """今の台本の決まりを出し口の宣言 `published` に写す。`(宣言, 写せなかった物の数え)`。"""
    if g["repo"] == "asmon":
        rules = mod.load_rules(repo / g["dist"])
    else:
        rules = mod.load_redirects(repo / g["redirects"]) if (repo / g["redirects"]).exists() else []
    redirects, skipped = [], {"rewrite_200": 0, "status_not_number": 0}
    for r in rules:
        code = str(r["status"])
        if g["skip_200"] and code == "200":
            skipped["rewrite_200"] += 1          # 関東の台本は 200 の書き換えを転送に数えない
            continue
        if not code.isdigit():
            skipped["status_not_number"] += 1
            continue
        redirects.append({"from": r["from"], "to": r["to"], "status": int(code)})
    gone = mod.load_gone(repo / g["gone"])
    decl = {"normalize": "path", "redirect_status": g["status"], "gone_redirect_status": g["temp"],
            "redirect_with_page": g["with_page"], "redirects": redirects,
            "gone": [{"url": u, "reason_kind": "media_gone"} for u, why in sorted(gone.items()) if why]}
    return decl, skipped


def vessel_gate(led: pathlib.Path, decl: dict, logged: list, listed: list, run: pathlib.Path, tag: str) -> dict:
    outlets = {"schema": "thth.outlets/1",
               "outlets": {"site": {"audience": "reader", "kinds": ["page"], "dest": "site", "published": decl}}}
    make_ledger(led, {"page": "pages/*.csv"}, outlets)
    P.write_seed(str(led), "site", logged, at=NOW)
    list_path = run / f"{tag}.list.txt"
    list_path.write_text("".join(u + "\n" for u in listed), encoding="utf-8")
    # 写しの台帳は前に配った控えの頭を持たない（初回）。published は --previous か --no-previous を言わせる
    return P.published(str(led), "site", str(list_path), NOW, no_previous=True)


def red_urls(v: dict) -> set:
    return {p["url"] for p in v["problems"] if "url" in p}


def check_gate(name: str, g: dict, dev: pathlib.Path, run: pathlib.Path) -> dict:
    repo = dev / g["repo"]
    out = {"media": name, "gate": "出した URL", "repo": str(repo)}
    script = repo / g["script"]
    if not script.exists():
        return dict(out, answer="未確認", why="台本が無い")
    mod = module(script, f"gate_{g['repo'].replace('-', '_')}")
    norm = mod.normalize_path
    published = repo / g["published"]
    logged_raw = mod.load_published(published)
    logged, seen = [], set()
    for path, date in logged_raw:
        if path not in seen:
            seen.add(path)
            logged.append((path, date or None))
    dist = repo / g["dist"]
    if dist.is_dir():
        listed = sorted(mod.dist_paths(dist, g["prefix"]) if g["repo"] == "asmon" else mod.dist_paths(dist))
        out["build"] = f"手元の build の出力（{g['dist']}・{mtime(dist)}）"
    else:
        listed = [p for p, _d in logged]
        out["build"] = "build の出力は未確認（控えそのものを一覧に使った）"
    decl, skipped = gate_decl(mod, repo, g)
    out["declared"] = {"redirects": len(decl["redirects"]), "gone": len(decl["gone"]), "skipped": skipped,
                       "redirect_status": decl["redirect_status"], "gone_redirect_status": decl["gone_redirect_status"],
                       "redirect_with_page": decl["redirect_with_page"]}
    work = run / f"gate-{g['repo']}"
    work.mkdir(parents=True)
    # 今の入力
    rc, script_bad, summary = script_gate(repo, g, published, norm)
    v = vessel_gate(work / "led", decl, logged, listed, work, "now")
    vessel_bad = red_urls(v)
    out["now"] = {"script_rc": rc, "script_red": len(script_bad), "script_summary": summary,
                  "vessel_ok": v["ok"], "vessel_red": len(vessel_bad), "vessel_counts": v["counts"],
                  "vessel_words": sorted({p["word"] for p in v["problems"]}),
                  "only_script": sorted(script_bad - vessel_bad)[:20], "only_vessel": sorted(vessel_bad - script_bad)[:20]}
    same_now = (rc == 0) == v["ok"] and script_bad == vessel_bad
    # 違反を 1 つ足した写し（控えに、頁も転送も例外も無い道を 1 行足す）
    fake = norm(g["prefix"] + FAKE + "/")
    copy = work / "published-urls.violation.txt"
    copy.write_bytes(published.read_bytes() + f"{fake}\t2026-11-14\n".encode("utf-8"))
    rc2, script_bad2, _ = script_gate(repo, g, copy, norm)
    v2 = vessel_gate(work / "led-violation", decl, logged + [(fake, "2026-11-14")], listed, work, "violation")
    out["violation"] = {"added": fake, "script_rc": rc2, "script_red_has_it": fake in script_bad2,
                        "vessel_ok": v2["ok"], "vessel_red_has_it": fake in red_urls(v2)}
    both_red = rc2 != 0 and fake in script_bad2 and not v2["ok"] and fake in red_urls(v2)
    out["answer"] = "合う" if same_now and both_red else "合わない"
    if not dist.is_dir() and out["answer"] == "合う":
        out["answer"] = "未確認"
    return out


# ------------------------------------------------------------------ 2. 読者に見える欄

def reader_ledger(led: pathlib.Path, data: pathlib.Path, fields: dict, words: list, extra_kinds: dict) -> None:
    kinds = {k: f"data/{fn}" for k, (fn, _f) in fields.items()}
    kinds.update({k: f"data/{fn}" for k, fn in extra_kinds.items()})
    outlets = {"schema": "thth.outlets/1", "internal_words": words,
               "outlets": {"site": {"audience": "reader", "dest": "site",
                                    "kinds": {k: {"reader_fields": f} for k, (_fn, f) in fields.items()}}}}
    files = {f"data/{fn}": data / fn for fn in list({fn for fn, _ in fields.values()}) + list(extra_kinds.values())}
    make_ledger(led, kinds, outlets, files)


def loaded_of(data: pathlib.Path, fields: dict, skip) -> dict:
    """載る記録の集まり（今の台本が頁に出さない行を除く。器では出し口の条件の判断が言う物を、ここでは表から作る）。"""
    import csv
    refs = set()
    for kind, (fn, _f) in fields.items():
        with open(data / fn, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if not skip(kind, row):
                    refs.add(f"{kind}:{row['id']}")
    return {"site": refs}


def jiangshi_skip(kind, row) -> bool:
    return kind == "lead" and row.get("verification_status") == "retired"     # public_jargon.py は retired を外す


def sometoka_skip(kind, row) -> bool:
    return kind == "entity" and row.get("search_visibility") == "noindex"     # audit_site.py (ii): 頁を作らない


def vessel_reader(led: pathlib.Path, loaded=None) -> dict:
    got = RC.check(str(led), loaded=loaded)
    words = [c for c in got if c["id"] == RC.WORDS_ID]
    ids = [c for c in got if c["id"] == RC.IDS_ID]
    hits = [(r["record"], r["field"], r["word"]) for c in words for r in c["records"]]
    return {"internal_words": sum(c["violations"] for c in words), "hidden_ids": sum(c["violations"] for c in ids),
            "short_ids_skipped": max([c.get("short_ids_skipped", 0) for c in ids] or [0]),
            "checked": sum(c["checked"] for c in words), "not_loaded": sum(c["not_when"] for c in words),
            "hits": hits[:20],
            "hidden_id_hits": [(r["record"], r["field"], r["found"]) for c in ids for r in c["records"]][:20]}


ID_SHAPE = re.compile(r"[a-z]+-[a-z0-9-]+|w\d{2}[a-z]?")


def vessel_only_ids(script_hits, v: dict) -> list:
    """器の番号の点検が拾い、今の台本の番号の点検（同じ行・同じ欄）が拾わなかった物（「器だけが拾った」）。"""
    script = {(rid, field) for _fn, rid, field, found in script_hits if any(ID_SHAPE.fullmatch(x) for x in found)}
    return [{"record": rec, "field": field, "found": found} for rec, field, found in v["hidden_id_hits"]
            if (rec.split(":", 1)[-1], field) not in script]


def inject_csv(src: pathlib.Path, dst: pathlib.Path, field: str, word: str) -> str:
    """表の写しの、`field` が空でなく引用符を使わない最初の行の値の末尾に語を足す（ほかの行のバイトは変えない）。"""
    import csv
    lines = src.read_bytes().decode("utf-8").splitlines(keepends=True)
    header = next(csv.reader([lines[0]]))
    col = header.index(field)
    for n in range(1, len(lines)):
        if '"' in lines[n]:
            continue
        cells = lines[n].rstrip("\r\n").split(",")
        if len(cells) == len(header) and cells[col]:
            cells[col] += word
            end = lines[n][len(lines[n].rstrip("\r\n")):]
            lines[n] = ",".join(cells) + end
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes("".join(lines).encode("utf-8"))
            return cells[0]
    raise SystemExit(f"語を足せる行が無い: {src}")


def check_jiangshi(dev: pathlib.Path, run: pathlib.Path) -> dict:
    repo = dev / "jiangshi-lab"
    out = {"media": "キョンシーラボ", "gate": "読者に見える欄（public_jargon.py）"}
    mod = module(repo / "scripts/public_jargon.py", "public_jargon_jiangshi")
    hits = mod.scan()
    work = run / "reader-jiangshi"
    reader_ledger(work / "led", repo / "data", JIANGSHI_FIELDS, JIANGSHI_WORDS, {"observation": "observations.csv"})
    v = vessel_reader(work / "led", loaded_of(repo / "data", JIANGSHI_FIELDS, jiangshi_skip))
    retired = sum(1 for row in __import__("csv").DictReader(open(repo / "data/leads.csv", encoding="utf-8"))
                  if row.get("verification_status") == "retired")
    script_ids = sum(1 for _fn, _rid, _f, found in hits if any(ID_SHAPE.fullmatch(x) for x in found))
    out["now"] = {"script_hits": len(hits), "script_id_hits": script_ids, "vessel_hidden_ids": v["hidden_ids"],
                  "vessel": v, "retired_leads_also_checked_by_vessel": retired}
    # 番号の点検の数も比べる。器だけが拾った物（台本の番号の型に無い種類の番号）は「器だけが拾った」と出す
    out["vessel_only"] = vessel_only_ids(hits, v)
    same = (len(hits) == 0) == (v["internal_words"] == 0)
    # 語を 1 つ足した写し: 台本は ROOT を写しに向ける（台本のファイルは変えない）
    root = work / "script-root"
    for fn, _f in JIANGSHI_FIELDS.values():
        (root / "data").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo / "data" / fn, root / "data" / fn)
    rid = inject_csv(repo / "data/claims.csv", root / "data/claims.csv", "object_text_ja", "台帳")
    mod.ROOT = root
    hits2 = mod.scan()
    led2 = work / "led-violation"
    reader_ledger(led2, root / "data", JIANGSHI_FIELDS, JIANGSHI_WORDS, {})
    v2 = vessel_reader(led2, loaded_of(root / "data", JIANGSHI_FIELDS, jiangshi_skip))
    out["violation"] = {"added": f"claim:{rid}.object_text_ja += 台帳", "script_hits": len(hits2),
                        "vessel_internal_words": v2["internal_words"]}
    both_red = len(hits2) > len(hits) and v2["internal_words"] > v["internal_words"]
    out["answer"] = "合う" if same and both_red else "合わない"
    return out


def check_sometoka(dev: pathlib.Path, run: pathlib.Path) -> dict:
    repo = dev / "sometokagaku"
    out = {"media": "染めと化学", "gate": "読者に見える欄（audit_site.py の (v)）"}
    mod = module(repo / "scripts/audit_site.py", "audit_site_sometoka")
    dist = repo / "site/dist"
    if not dist.is_dir():
        return dict(out, answer="未確認", why="build の出力が無い")
    pages = sorted(dist.rglob("*.html"))
    entity_ids = {e["id"] for e in mod.load("entities")}
    bare = mod.source_bare_id_set(mod.load("sources"))
    found = mod.check_internal_terms(dist, pages, entity_ids, bare)
    work = run / "reader-sometoka"
    reader_ledger(work / "led", repo / "data", SOMETOKA_FIELDS, SOMETOKA_WORDS, {"observation": "observations.csv"})
    v = vessel_reader(work / "led", loaded_of(repo / "data", SOMETOKA_FIELDS, sometoka_skip))
    out["build"] = f"手元の build の出力（site/dist・{mtime(dist)}）"
    out["now"] = {"script_hits": len(found), "script_first": found[:3], "vessel_hidden_ids": v["hidden_ids"],
                  "vessel": v}
    # 台本は build の頁を見る（行と欄に戻せない）ので、器の番号の点検が拾った物は全部「器だけが拾った」と出す
    out["vessel_only"] = [{"record": r, "field": f, "found": x} for r, f, x in v["hidden_id_hits"]] if not found else []
    same = (len(found) == 0) == (v["internal_words"] == 0)
    # 語を 1 つ足した写し: 頁は 1 枚を写して本文に語を足す・台帳は表の写しの 1 欄に足す
    cdist = work / "dist-copy"
    page = dist / "index.html"
    (cdist).mkdir(parents=True)
    text = page.read_text(encoding="utf-8")
    (cdist / "index.html").write_text(text.replace("</body>", "<p>統合担当</p></body>", 1), encoding="utf-8")
    found2 = mod.check_internal_terms(cdist, [cdist / "index.html"], entity_ids, bare)
    data2 = work / "data-violation"
    data2.mkdir()
    for fn, _f in SOMETOKA_FIELDS.values():
        shutil.copyfile(repo / "data" / fn, data2 / fn)
    rid = inject_csv(repo / "data/claims.csv", data2 / "claims.csv", "object_text_ja", "統合担当")
    led2 = work / "led-violation"
    reader_ledger(led2, data2, SOMETOKA_FIELDS, SOMETOKA_WORDS, {})
    v2 = vessel_reader(led2, loaded_of(data2, SOMETOKA_FIELDS, sometoka_skip))
    out["violation"] = {"added_page": "index.html の本文に「統合担当」", "script_hits": len(found2),
                        "added_ledger": f"claim:{rid}.object_text_ja += 統合担当",
                        "vessel_internal_words": v2["internal_words"]}
    both_red = any("統合担当" in f for f in found2) and v2["internal_words"] > v["internal_words"]
    out["answer"] = "合う" if same and both_red else "合わない"
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--developer", default="/Volumes/NexDev/Developer")
    ap.add_argument("--work", default="/Volumes/NexDev/Developer/thth-work/stage7b")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    dev = pathlib.Path(args.developer)
    run = pathlib.Path(args.work) / f"run-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    run.mkdir(parents=True)
    results = []
    with contextlib.redirect_stdout(io.StringIO()):
        for name, g in GATES.items():
            results.append(check_gate(name, g, dev, run))
        results.append(check_jiangshi(dev, run))
        results.append(check_sometoka(dev, run))
    rc = 1 if any(r["answer"] == "合わない" for r in results) else 0
    (run / "result.json").write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    if args.json:
        print(json.dumps({"rc": rc, "run": str(run), "results": results}, ensure_ascii=False, indent=1, default=str))
        return rc
    for r in results:
        print(f"{r['media']} / {r['gate']}: {r['answer']}" + (f"（{r.get('build')}）" if r.get("build") else ""))
        if "now" in r:
            print("  今の入力: " + json.dumps(r["now"], ensure_ascii=False, default=str)[:600])
        if r.get("vessel_only"):
            print("  器だけが拾った（番号の点検）: " + json.dumps(r["vessel_only"], ensure_ascii=False)[:600])
        if "violation" in r:
            print("  違反を 1 つ足した写し: " + json.dumps(r["violation"], ensure_ascii=False))
    print(f"写しの置き場 {run}")
    print(f"rc {rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
