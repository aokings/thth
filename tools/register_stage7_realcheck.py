#!/usr/bin/env python3
"""台帳の器 段 7「build の入力が同じ」（設計 §8.3 の 2）を、こぴちゃの実物で確かめる台本（こぴちゃの repo は
**読むだけ**。何も書かない）。

    python3 tools/register_stage7_realcheck.py [--developer ~/Developer] [--work ~/Developer/thth-work/stage7]
                                               [--as-of 2026-11-14T12:00:00+09:00] [--json]

1. こぴちゃの、宣言（`tools/register_decls/kopicha/kinds/*.json`）の置き場に当たるファイルを、repo の外の写し
   （`<work>/run-<日時>/ledger/`。毎回新しいフォルダ。前の回の物は消さない）に取り込み、宣言と出し口の宣言
   （`outlets.json`）を `.thth/` に置く。
2. 試しの鍵を写しの側の一時の置き場（`<run>/config/`）に作る（手元の本物の鍵 `~/.config/thth/` には触らない・値は出さない）。
3. 読者に出る出し口（`site`）に基準の判断（`judge --baseline`）を足し、受け入れて封をする。内の道具が読む出し口
   （`tools`）は判断なしで受け入れる。書き出し先は出し口ごとに `<run>/out/<出し口>/`。
4. 比べる: 内の道具の出し口は、材料の全部が元と sha256 で同じ（足りない・余る物も無い）。読者の出し口は、元と
   同じでなければ、違うファイルごとに「元にあって材料に無い行」と「除いた物の一覧」がぴったり合うかを出し、合わない
   差を 1 つずつ（器の誤り・宣言の誤り・今の決まりとの違い）の候補つきで書く。
5. 公開の直前の確かめ（`verify --no-previous`）が両方の出し口で緑か。

`--stop <種類:番号>` は柵を壊して見るための物: 受け入れる前にその記録を止める。読者の材料から外れ、差が「除いた物の
一覧」とぴったり合えば「合う」（内の道具の出し口は止めた記録も外れるので「違う」になる）。

出力は出し口ごとの「同じ・違う（何件）」と rc。期待のとおりなら rc 0。
"""
from __future__ import annotations

import argparse
import collections
import datetime
import fnmatch
import hashlib
import json
import os
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))
DECLS = HERE.parent / "register_decls" / "kopicha"
AS_OF = "2026-11-14T12:00:00+09:00"


def sha(path) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def import_ledger(source: pathlib.Path, ledger: pathlib.Path) -> dict:
    """宣言の置き場に当たるファイルを写す（隠しの名前・symlink は辿らない。器の読み方と同じ）。`{種類: [道]}`。"""
    kinds = {}
    for path in sorted((DECLS / "kinds").glob("*.json")):
        decl = json.loads(path.read_text(encoding="utf-8"))
        places = decl["place"] if isinstance(decl["place"], list) else [decl["place"]]
        kinds[decl["kind"]] = places
    taken = collections.defaultdict(list)
    for directory, dirs, files in os.walk(source, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "node_modules"
                         and not os.path.islink(os.path.join(directory, d)))
        for name in sorted(files):
            if name.startswith("."):
                continue
            rel = os.path.relpath(os.path.join(directory, name), source).replace(os.sep, "/")
            for kind, places in kinds.items():
                if any(fnmatch.fnmatchcase(rel, p) for p in places):
                    dst = ledger / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(os.path.join(directory, name), dst)
                    taken[kind].append(rel)
                    break
    (ledger / ".thth/kinds").mkdir(parents=True)
    for path in sorted((DECLS / "kinds").glob("*.json")):
        shutil.copyfile(path, ledger / ".thth/kinds" / path.name)
    for name in ("constitution.json", "outlets.json"):
        if (DECLS / name).exists():
            shutil.copyfile(DECLS / name, ledger / ".thth" / name)
    return dict(taken)


def table_rows(data: bytes) -> list:
    from thth.register import vessel as V
    text = data.decode("utf-8")
    return V.split_csv_rows(text[1:] if text.startswith("﻿") else text)


def explain_table(R, V, vessel, rel: str, src: bytes, got: bytes, excluded: list) -> dict:
    """表の 1 枚の差: 元にあって材料に無い行・材料にあって元に無い行と、除いた物の一覧が合うか。"""
    a, b = table_rows(src), table_rows(got)
    missing = [r for r in a[1:] if r not in b]
    extra = [r for r in b[1:] if r not in a]
    by_version = {rec.version: rec for rec in vessel.all_records if rec.path == rel}
    missing_refs = sorted(by_version[V.bytes_version(r.encode("utf-8"))].ref
                          if V.bytes_version(r.encode("utf-8")) in by_version else f"(行) {r[:40]!r}"
                          for r in missing if r.strip("\r\n"))
    listed = sorted(e["record"] for e in excluded
                    if (vessel.unique(e["record"]) or V.Record("", None, "", "", None, False)).path == rel
                    or e["record"] == rel)
    return {"missing": len(missing), "extra": len(extra), "header_same": a[:1] == b[:1],
            "matches_excluded": missing_refs == listed and not extra,
            "unexplained_missing": sorted(set(missing_refs) - set(listed))[:20],
            "listed_but_present": sorted(set(listed) - set(missing_refs))[:20]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--developer", default="~/Developer")
    ap.add_argument("--work", default="~/Developer/thth-work/stage7")
    ap.add_argument("--as-of", default=AS_OF)
    ap.add_argument("--stop", action="append", default=[], metavar="種類:番号",
                    help="柵を壊して見る: 受け入れる前にその記録を止める（読者の材料から外れ、差が除いた物の一覧と合うか）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    source = pathlib.Path(args.developer).expanduser() / "kopicha"
    run = pathlib.Path(args.work).expanduser() / ("run-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    run.mkdir(parents=True)
    # 試しの鍵と THTH の根は、この回の置き場だけ（手元の本物の鍵には触らない）
    os.environ["XDG_CONFIG_HOME"] = str(run / "config")
    os.environ["HOME"] = str(run / "home")
    os.environ["THTH_ROOT"] = str(run / "root")
    for sub in ("config", "home", "root"):
        (run / sub).mkdir()
    os.environ.pop("THTH_REGISTER_SEAL_PUBLIC_KEY", None)
    from thth.register import route as R, seal as S, vessel as V

    S.init_key()
    ledger = run / "ledger"
    taken = import_ledger(source, ledger)
    vessel = V.open_vessel(str(ledger))
    outlets = R.load_outlets(str(ledger), vessel)
    report = {"run": str(run), "as_of": args.as_of, "source_head": None, "outlets": {}, "ok": True}
    try:
        import subprocess
        report["source_head"] = subprocess.run(["git", "-C", str(source), "rev-parse", "--short", "HEAD"],
                                               capture_output=True, text=True, timeout=30).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        report["source_head"] = None
    reader = [o for o in outlets.values() if o.audience == "reader"]
    for o in reader:
        R.baseline(str(ledger), o.name, by="register_stage7_realcheck", at=args.as_of)
    for ref in args.stop:
        R.stop(str(ledger), ref, by="register_stage7_realcheck", at=args.as_of)
    report["stopped"] = list(args.stop)
    for name, outlet in outlets.items():
        to = run / "out" / name
        plan = R.accept_plan(str(ledger), name, str(to), args.as_of)
        R.accept(str(ledger), name, str(to), args.as_of, confirm=plan["digest"], at=args.as_of)
        outputs = plan["body"]["outputs"]
        excluded = plan["result"]["excluded"]
        files_of_kinds = sorted({rel for k in outlet.kinds for rel in taken.get(k, [])})
        same, differ, details = [], [], {}
        for rel in files_of_kinds:
            got = to / rel
            if not got.is_file():
                differ.append(rel)
                gone = sorted(e["record"] for e in excluded
                              if (vessel.unique(e["record"]) is not None and vessel.unique(e["record"]).path == rel)
                              or e["record"] == rel)
                details[rel] = {"missing_file": True, "excluded": gone, "matches_excluded": bool(gone)}
                continue
            if sha(source / rel) == sha(got):
                same.append(rel)
            else:
                differ.append(rel)
                if rel.endswith(".csv"):
                    details[rel] = explain_table(R, V, vessel, rel, (source / rel).read_bytes(), got.read_bytes(),
                                                 excluded)
        extra = sorted(set(outputs) - set(files_of_kinds))
        verdict = R.verify(str(ledger), name, str(to), args.as_of, no_previous=True)
        entry = {"audience": outlet.audience, "kinds": outlet.kinds, "files": len(files_of_kinds),
                 "same": len(same), "differ": len(differ), "differ_files": differ, "details": details,
                 "outputs_not_in_source": extra, "items": len(plan["body"]["items"]),
                 "excluded": dict(collections.Counter(e["why"] for e in excluded)),
                 "verify_ok": verdict["ok"], "verify_problems": [p["word"] for p in verdict["problems"]]}
        if outlet.audience == "internal":
            ok = not differ and not extra and verdict["ok"]
        else:
            ok = verdict["ok"] and not extra and all(d.get("matches_excluded") for d in details.values())
        entry["ok"] = ok
        report["ok"] &= ok
        report["outlets"][name] = entry
    rc = 0 if report["ok"] else 1
    report["rc"] = rc
    (run / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return rc
    print(f"こぴちゃ（{report['source_head']}）・判定の日時 {args.as_of}・置き場 {run}")
    print(f"取り込んだ記録 {len(vessel.all_records)}（読みの問題 {sum(1 for r in vessel.all_records if r.problems)}）")
    for name, e in report["outlets"].items():
        who = "内の道具" if e["audience"] == "internal" else "読者"
        print(f"- {name}（{who}・{len(e['kinds'])} 種類）: ファイル {e['files']}・同じ {e['same']}・違う {e['differ']}・"
              f"載せた {e['items']}・除いた {e['excluded'] or 0}・確かめ {'緑' if e['verify_ok'] else '赤'}"
              f"{'' if e['verify_ok'] else ' ' + '・'.join(e['verify_problems'])}"
              f" → {'合う' if e['ok'] else '合わない'}")
        for rel, d in e["details"].items():
            print(f"    {rel}: {d}")
        if e["outputs_not_in_source"]:
            print(f"    元に無い材料: {e['outputs_not_in_source'][:10]}")
    print(f"rc={rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
