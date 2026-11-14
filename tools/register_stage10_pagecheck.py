#!/usr/bin/env python3
"""台帳の器 段 10 の最初の確かめ「頁が同じ」（設計 §8.3 の 3）を、こぴちゃの写しで打つ台本。
こぴちゃの repo は**読むだけ**（写しと `node_modules` の複製は `--work` の下に作る）。

    python3 tools/register_stage10_pagecheck.py --material <段 7 の書き出し先 out/site> [--material <out/tools>]
                                                [--kopicha ~/Developer/kopicha] [--work ~/Developer/thth-work/stage10]
                                                [--json]

1. こぴちゃの HEAD を `git archive` で `<run>/src/` に写す（作業木の書きかけは入れない。こぴちゃのレーンが
   途中の変更を置いていても比べが揺れない）。`node_modules` は作業木のものを
   APFS の複製（`cp -cR`）で写す（build が `node_modules/.vite` などに書いても、元に触れない）。
2. 同じ写しで `npm run build` を 2 回（A・B）。`dist/` の全ファイルの sha256 を比べ、もともと揺れるファイルを名前つきで
   書き出す（理由の候補は中の差の頭で示す）。揺れが全ファイルの 5% を超えたら比べにならないので止まって知らせる。
3. 材料（`--material` の下の全ファイル。道は repo の根からの道）を写しに上書きし、もう 1 回 build（C）。
   A と C を比べ、揺れるファイルを除いて全部同じなら「同じ」。違えば違うファイルの一覧。揺れは「型」で除く
   （名前の 7 桁以上の 16 進を伏せた形。pagefind の索引は build ごとに名前の指紋が変わるので、A と B で揺れた型の
   ファイルは C でも除く）。除いた型と枚数は report に書く。
   `--recompare <run>` は build し直さず、置き場の dist-A・dist-B・dist-C を比べ直す。
4. 材料が元と何枚違ったかも書く（段 7 の入力の一致がそのままなら 0）。

出力は `<run>/report.json` と、標準出力の要約（`--json` なら JSON）。期待のとおり（同じ）なら rc 0、違えば 1、
比べにならなければ 2。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

FLUCT_MAX = 0.05
HASHED = re.compile(r"[0-9a-f]{7,}")


def family(path: str) -> str:
    """名前に指紋の入るファイル（pagefind の索引など）は、指紋を伏せた形で「同じ型」とみる。"""
    return HASHED.sub("#", path)


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hashes(root: pathlib.Path) -> dict:
    out = {}
    for directory, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(files):
            full = pathlib.Path(directory) / name
            out[full.relative_to(root).as_posix()] = sha(full)
    return out


def build(src: pathlib.Path, log: pathlib.Path, env: dict) -> float:
    if (src / "dist").exists():
        shutil.move(str(src / "dist"), str(src.parent / f"dist-old-{time.time_ns()}"))
    started = time.monotonic()
    with open(log, "w") as f:
        r = subprocess.run(["npm", "run", "build"], cwd=src, stdout=f, stderr=subprocess.STDOUT, env=env)
    if r.returncode != 0:
        raise SystemExit(f"build が rc {r.returncode}（{log}）")
    return round(time.monotonic() - started, 1)


def head_of_diff(a: pathlib.Path, b: pathlib.Path) -> str:
    x, y = a.read_bytes(), b.read_bytes()
    i = next((k for k in range(min(len(x), len(y))) if x[k] != y[k]), min(len(x), len(y)))
    return f"{len(x)}→{len(y)} バイト・{i} バイト目から: {x[i:i + 60]!r} / {y[i:i + 60]!r}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kopicha", default=os.path.expanduser("~/Developer/kopicha"))
    ap.add_argument("--material", action="append", required=True)
    ap.add_argument("--work", default=os.path.expanduser("~/Developer/thth-work/stage10"))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--recompare", metavar="RUN")
    args = ap.parse_args(argv)
    if args.recompare:
        return compare(pathlib.Path(args.recompare), json.loads((pathlib.Path(args.recompare) / "report.json")
                                                                .read_text()), args.json)
    kopicha = pathlib.Path(args.kopicha).resolve()
    run = pathlib.Path(args.work) / ("page-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    run.mkdir(parents=True)
    src = run / "src"
    head = subprocess.run(["git", "-C", str(kopicha), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(kopicha), "status", "--short"], capture_output=True, text=True).stdout
    src.mkdir()
    arc = subprocess.run(["git", "-C", str(kopicha), "archive", "--format=tar", "HEAD"], capture_output=True, check=True)
    subprocess.run(["tar", "-x", "-C", str(src)], input=arc.stdout, check=True)
    subprocess.run(["cp", "-cR", str(kopicha / "node_modules"), str(src / "node_modules")], check=True)
    env = dict(os.environ, TMPDIR=str(run / "tmp"), ASTRO_TELEMETRY_DISABLED="1")
    (run / "tmp").mkdir()
    report = {"run": str(run), "kopicha_head": head, "kopicha_dirty": dirty.splitlines(), "seconds": {}}

    report["seconds"]["A"] = build(src, run / "build-A.log", env)
    shutil.move(str(src / "dist"), str(run / "dist-A"))
    report["seconds"]["B"] = build(src, run / "build-B.log", env)
    shutil.move(str(src / "dist"), str(run / "dist-B"))
    a, b = tree_hashes(run / "dist-A"), tree_hashes(run / "dist-B")
    fluct = sorted(p for p in set(a) | set(b) if a.get(p) != b.get(p))
    report["files"] = len(a)
    report["fluctuating"] = [{"path": p, "why": head_of_diff(run / "dist-A" / p, run / "dist-B" / p)
                              if p in a and p in b else "片方にだけ在る"} for p in fluct]
    if len(fluct) > FLUCT_MAX * max(len(a), 1):
        report["verdict"] = "比べにならない（揺れが多すぎる）"
        (run / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
        print(json.dumps(report, ensure_ascii=False, indent=1) if args.json else report["verdict"])
        return 2

    replaced = differ = 0
    for m in args.material:
        mroot = pathlib.Path(m)
        for rel, digest in tree_hashes(mroot).items():
            target = src / rel
            if not target.exists() or sha(target) != digest:
                differ += 1
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(mroot / rel, target)
            replaced += 1
    report["material"] = {"files": replaced, "differ_from_source": differ}
    report["seconds"]["C"] = build(src, run / "build-C.log", env)
    shutil.move(str(src / "dist"), str(run / "dist-C"))
    return compare(run, report, args.json)


def compare(run: pathlib.Path, report: dict, as_json: bool) -> int:
    a, b, c = (tree_hashes(run / f"dist-{x}") for x in "ABC")
    fluct = sorted(p for p in set(a) | set(b) if a.get(p) != b.get(p))
    fams = sorted({family(p) for p in fluct})
    skip = lambda p: family(p) in fams
    diffs = sorted(p for p in set(a) | set(c) if not skip(p) and a.get(p) != c.get(p))
    report["fluctuating_families"] = [{"family": f, "files_A": sum(1 for p in a if family(p) == f),
                                       "files_C": sum(1 for p in c if family(p) == f)} for f in fams]
    report["compared"] = sum(1 for p in set(a) | set(c) if not skip(p))
    report["compared_html"] = sum(1 for p in set(a) | set(c) if not skip(p) and p.endswith(".html"))
    report["differ"] = [{"path": p, "why": head_of_diff(run / "dist-A" / p, run / "dist-C" / p)
                         if p in a and p in c else "片方にだけ在る"} for p in diffs[:50]]
    report["differ_count"] = len(diffs)
    report["verdict"] = "同じ" if not diffs else f"違う（{len(diffs)} 枚）"
    (run / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        m = report.get("material", {})
        print(f"頁: {report['verdict']}・dist {len(a)} 枚のうち比べた {report['compared']} 枚（html {report['compared_html']}）・"
              f"揺れの型 {len(fams)}・材料 {m.get('files')} 枚（元と違う {m.get('differ_from_source')}）・"
              f"build 秒 {report.get('seconds')}・こぴちゃ {str(report.get('kopicha_head'))[:9]}")
        for f in report["fluctuating_families"]:
            print(f"  揺れの型: {f['family']}  A {f['files_A']} 枚・C {f['files_C']} 枚")
        for d in report["differ"][:10]:
            print(f"  違う: {d['path']}  {d['why']}")
        print(f"置き場: {run}")
    return 0 if not diffs else 1

if __name__ == "__main__":
    sys.exit(main())
