#!/usr/bin/env python3
"""台帳の器 段 11: にがみラボの向き替えの形を写しで試し、わざと壊す 5 通りを起こす。

にがみラボの HEAD を `git archive` で `<run>/orig` に写し、`<run>/repo` に同じ物と、repo の中の台帳のフォルダ
`register/`（宣言の置き場に当たるファイルと `.thth/` の宣言）を作る。器（`--thth` の thth）で
控える → 鍵 → 基準の判断 → site と tools の受け入れ（書き出し先は repo の根）→ 確かめ → `export --check` →
repo の根が元と同じか、を打つ。そのあと repo の写しを 5 つ作り、1 つずつ壊して器が止めるかを見る
（考察 `docs/考察_台帳の器の手間と利点_2026-11-15.md` §3 の 5 通り）。

にがみラボの repo には書かない（読むのは `git archive` だけ）。

    python3 tools/register_stage11_nigami_shape.py [--src ~/Developer/nigamilab] [--work ~/Developer/thth-work/stage11]
        [--thth ~/.local/share/thth-register-venv/bin/thth]
"""
from __future__ import annotations

import argparse
import csv
import datetime
import fnmatch
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

DECLS = pathlib.Path(__file__).resolve().parent / "register_decls" / "nigami"
NAME = "nigami"
BY = "stage11-shape"
CHECKER = "stage11-checker"
LEDGER = "register"
JST = datetime.timezone(datetime.timedelta(hours=9))


def now() -> str:
    return datetime.datetime.now(JST).isoformat(timespec="seconds")


class Run:
    def __init__(self, thth, env, cwd):
        self.thth, self.env, self.cwd, self.steps = thth, env, cwd, []

    def __call__(self, label, args, *, expect=None):
        t = time.monotonic()
        r = subprocess.run([self.thth, "register", *args], capture_output=True, text=True, env=self.env, cwd=self.cwd)
        out = "\n".join(x for x in r.stdout.splitlines() if not x.startswith("report_channel:")).strip()
        try:
            data = json.loads(out) if out else None
        except json.JSONDecodeError:
            data = None
        self.steps.append({"label": label, "rc": r.returncode, "seconds": round(time.monotonic() - t, 2),
                           "argv": ["thth", "register", *args]})
        if expect is not None and r.returncode not in (expect if isinstance(expect, tuple) else (expect,)):
            raise SystemExit(f"{label}: rc {r.returncode}（期待 {expect}）\n{out[-2000:]}\n{r.stderr[-2000:]}")
        return r.returncode, data, out, r.stderr


def places():
    kinds = {}
    for path in sorted((DECLS / "kinds").glob("*.json")):
        decl = json.loads(path.read_text(encoding="utf-8"))
        kinds[decl["kind"]] = decl["place"] if isinstance(decl["place"], list) else [decl["place"]]
    return kinds


def build_ledger(repo: pathlib.Path) -> dict:
    ledger = repo / LEDGER
    taken = {}
    for directory, dirs, files in os.walk(repo, followlinks=False):
        rel_dir = os.path.relpath(directory, repo)
        if rel_dir == LEDGER or rel_dir.startswith(LEDGER + os.sep):
            dirs[:] = []
            continue
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "node_modules"
                         and not os.path.islink(os.path.join(directory, d)))
        for name in sorted(files):
            if name.startswith("."):
                continue
            rel = os.path.relpath(os.path.join(directory, name), repo).replace(os.sep, "/")
            for kind, ps in places().items():
                if any(fnmatch.fnmatchcase(rel, p) for p in ps):
                    dst = ledger / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(os.path.join(directory, name), dst)
                    taken.setdefault(kind, []).append(rel)
                    break
    (ledger / ".thth" / "kinds").mkdir(parents=True)
    for path in sorted((DECLS / "kinds").glob("*.json")):
        shutil.copyfile(path, ledger / ".thth" / "kinds" / path.name)
    for name in ("constitution.json", "outlets.json"):
        shutil.copyfile(DECLS / name, ledger / ".thth" / name)
    return taken


def diff_root(orig: pathlib.Path, repo: pathlib.Path) -> list:
    r = subprocess.run(["diff", "-rq", "-x", LEDGER, str(orig), str(repo)], capture_output=True, text=True)
    return [x for x in r.stdout.splitlines() if x.strip()]


def first_row(path: pathlib.Path):
    text = path.read_text(encoding="utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    return rows


def words(data) -> list:
    if not isinstance(data, dict):
        return []
    out = set()
    for p in data.get("problems", []) or []:
        if isinstance(p, dict):
            out.add(p.get("word") or p.get("code") or p.get("kind") or json.dumps(p, ensure_ascii=False)[:60])
    return sorted(str(x) for x in out)


def names(data) -> list:
    """赤に名指しされた記録（problems の record・records）。"""
    if not isinstance(data, dict):
        return []
    out = set()
    for p in data.get("problems", []) or []:
        if isinstance(p, dict):
            for k in ("record", "id"):
                if p.get(k):
                    out.add(str(p[k]))
            for x in p.get("records", []) or []:
                out.add(str(x))
            for x in p.get("items", []) or []:
                if isinstance(x, dict) and x.get("item"):
                    out.add(str(x["item"]).split("=")[0])
    return sorted(out)


def check_summary(data) -> dict:
    if not isinstance(data, dict):
        return {}
    keep = {}
    for k in ("same_as_seal", "differs", "stale", "mixed", "pending_takes", "ok"):
        if k in data:
            v = data[k]
            keep[k] = len(v) if isinstance(v, list) else v
    return keep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.expanduser("~/Developer/nigamilab"))
    ap.add_argument("--work", default=os.path.expanduser("~/Developer/thth-work/stage11"))
    ap.add_argument("--thth", default=os.path.expanduser("~/.local/share/thth-register-venv/bin/thth"))
    ap.add_argument("--extra-ignore", action="append", default=[],
                    help="写しの宣言だけに足す ignore（器の穴の回り道を試すとき。正本の宣言は変えない）")
    args = ap.parse_args(argv)
    run = pathlib.Path(args.work) / ("nigami-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    for d in ("orig", "config", "home", "root"):
        (run / d).mkdir(parents=True)
    head = subprocess.run(["git", "-C", args.src, "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()
    arch = subprocess.run(["git", "-C", args.src, "archive", "HEAD"], capture_output=True, check=True).stdout
    subprocess.run(["tar", "-x", "-C", str(run / "orig")], input=arch, check=True)
    subprocess.run(["cp", "-cR", str(run / "orig"), str(run / "repo")], check=True)
    repo = run / "repo"
    taken = build_ledger(repo)
    if args.extra_ignore:
        op = repo / LEDGER / ".thth" / "outlets.json"
        od = json.loads(op.read_text(encoding="utf-8"))
        for o in od["outlets"].values():
            o["ignore"] = list(o.get("ignore", [])) + args.extra_ignore
        op.write_text(json.dumps(od, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    env = dict(os.environ, XDG_CONFIG_HOME=str(run / "config"), HOME=str(run / "home"), THTH_ROOT=str(run / "root"))
    env.pop("THTH_REGISTER_SEAL_PUBLIC_KEY", None)
    R = Run(args.thth, env, str(repo))
    report = {"run": str(run), "nigamilab": head, "thth": subprocess.run([args.thth, "--version"], capture_output=True,
                                                                          text=True).stdout.strip(),
              "ledger_files": sum(len(v) for v in taken.values()), "kinds_taken": len(taken),
              "extra_ignore": args.extra_ignore}

    R("控える", ["add", NAME, "--path", str(repo / LEDGER), "--project", "nigamilab", "--kind", "media", "--by", BY],
      expect=0)
    R("鍵", ["key", "init", "--json"], expect=0)
    rc, data, out, err = R("形の点検", ["check", NAME, "--json"])
    report["check_before"] = {"rc": rc, "code": (data or {}).get("code"),
                              "dangling": len(((data or {}).get("links") or {}).get("dangling", []) or [])}
    rc, data, out, err = R("基準の判断（site）", ["judge", NAME, "--baseline", "--outlet", "site", "--by", BY, "--json"])
    report["baseline"] = {"rc": rc, "head": (out or err)[:300]}
    if rc != 0:
        report["stopped"] = "baseline"
        (run / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 2

    def accept(outlet, base):
        as_of = now()
        rc, plan, out, err = R(f"受け入れ 1 段め {outlet}", ["accept", NAME, "--outlet", outlet, "--to", str(base),
                                                         "--as-of", as_of, "--by", BY, "--json"], expect=(0, 1))
        digest = (plan or {}).get("digest")
        if not digest:
            raise SystemExit(f"accept {outlet}: digest が無い\n{out[-2000:]}\n{err[-2000:]}")
        R(f"受け入れ 2 段め {outlet}", ["accept", NAME, "--outlet", outlet, "--to", str(base), "--as-of", as_of,
                                     "--by", BY, "--confirm", digest, "--json"], expect=0)
        return plan

    def verify(label, outlet, base, name=NAME):
        rc, data, out, err = R(label, ["verify", name, "--outlet", outlet, "--material", str(base), "--now", now(),
                                       "--no-previous", "--json"])
        return {"rc": rc, "ok": bool(isinstance(data, dict) and data.get("ok")), "words": words(data),
                "named": names(data)[:20], "named_count": len(names(data)), "head": (out or err)[:300]}

    def xcheck(label, outlet, base, name=NAME):
        rc, data, out, err = R(label, ["export", name, "--outlet", outlet, "--to", str(base), "--as-of", now(),
                                       "--check", "--json"])
        return {"rc": rc, **check_summary(data), "head": (out or err)[:300]}

    plans = {o: accept(o, repo) for o in ("site", "tools")}
    report["plans"] = {o: {k: (len(v) if isinstance(v, list) else v) for k, v in (p or {}).items()
                           if k in ("drops", "adds", "dropped", "added", "outputs", "new_judgments")}
                       for o, p in plans.items()}
    report["shape"] = {o: {"verify": verify(f"確かめ {o}", o, repo), "export_check": xcheck(f"export --check {o}", o, repo)}
                       for o in ("site", "tools")}
    report["root_diff"] = diff_root(run / "orig", repo)[:20]
    report["root_same"] = not report["root_diff"]

    # -- わざと壊す 5 通り（repo の写しを 1 つずつ壊す。台帳の控えも写しに向け直す）
    breaks = {}

    def copy(tag):
        dst = run / f"break-{tag}"
        subprocess.run(["cp", "-cR", str(repo), str(dst)], check=True)
        R(f"控える {tag}", ["add", f"{NAME}-b{tag}", "--path", str(dst / LEDGER), "--project", "nigamilab",
                           "--kind", "media", "--by", BY], expect=0)
        return dst, f"{NAME}-b{tag}"

    claims = first_row(repo / LEDGER / "data" / "claims.csv")
    evidence = first_row(repo / LEDGER / "data" / "evidence.csv")
    c0 = claims[0]
    e0 = next(e for e in evidence if e.get("claim_id"))
    cid_field = "id" if "id" in c0 else list(c0.keys())[0]
    eid_field = "id" if "id" in e0 else list(e0.keys())[0]

    # 1 根に直に書く
    b, nm = copy("1")
    with open(b / "data" / "claims.csv", "ab") as f:
        f.write(b"\n")
    breaks["1_root_direct_write"] = {"verify": verify("壊す 1 確かめ", "site", b, nm),
                                     "export_check": xcheck("壊す 1 export --check", "site", b, nm)}
    # 2 判断の無い変更（台帳の中を直して、判断も受け入れもせずに出す）
    b, nm = copy("2")
    text_field = next((k for k in ("object_text", "statement_ja", "scope_text", "notes") if c0.get(k)),
                      next(k for k in c0 if k != cid_field and c0[k]))
    rc, data, out, err = R("壊す 2 書く", ["row", "set", nm, f"claim:{c0[cid_field]}", "--set",
                                         f"{text_field}={c0[text_field]}（壊す 2）", "--by", BY, "--json"])
    breaks["2_unjudged_change"] = {"row_set_rc": rc, "field": text_field,
                                   "verify": verify("壊す 2 確かめ", "site", b, nm)}
    # 3 古い材料（読者が型で取るフォルダに、一覧に無い問いを置く）
    b, nm = copy("3")
    qdirs = sorted(p for p in (b / "data" / "questions").iterdir() if p.is_dir())
    stale = b / "data" / "questions" / "zz-stale-stage11"
    shutil.copytree(qdirs[0], stale)
    breaks["3_stale_material"] = {"export_check": xcheck("壊す 3 export --check", "site", b, nm),
                                  "verify": verify("壊す 3 確かめ", "site", b, nm)}
    # 4 指す先を消す（根拠の行の出典を、無い番号に）
    b, nm = copy("4")
    src_field = next((k for k in e0 if k in ("source_id", "source")), None)
    rc, data, out, err = R("壊す 4 書く", ["row", "set", nm, f"evidence:{e0[eid_field]}", "--set",
                                         f"{src_field}=src-does-not-exist-stage11", "--by", BY, "--json"])
    rc_c, data_c, out_c, err_c = R("壊す 4 形の点検", ["check", nm, "--json"])
    breaks["4_dangling"] = {"row_set_rc": rc, "field": src_field,
                            "check": {"rc": rc_c, "code": (data_c or {}).get("code"),
                                      "dangling": len(((data_c or {}).get("links") or {}).get("dangling", []) or []),
                                      "dangling_before": report["check_before"]["dangling"]},
                            "verify": verify("壊す 4 確かめ", "site", b, nm)}
    # 5 根拠だけ変える（それに依る主張が名指しされるか）
    b, nm = copy("5")
    find_field = next((k for k in e0 if k.startswith("finding") or k.startswith("summary")), None)
    rc, data, out, err = R("壊す 5 書く", ["row", "set", nm, f"evidence:{e0[eid_field]}", "--set",
                                         f"{find_field}={e0[find_field]}（壊す 5）", "--by", BY, "--json"])
    v = verify("壊す 5 確かめ", "site", b, nm)
    breaks["5_evidence_only"] = {"row_set_rc": rc, "field": find_field, "evidence": e0[eid_field],
                                 "claim_of_evidence": e0.get("claim_id"),
                                 "claim_named": any(str(e0.get("claim_id")) in n for n in v["named"]),
                                 "verify": v}
    report["breaks"] = breaks
    report["steps"] = R.steps
    (run / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    short = {k: report[k] for k in ("run", "nigamilab", "thth", "ledger_files", "extra_ignore", "root_same", "check_before",
                                    "plans")}
    short["shape"] = {o: {"verify_ok": s["verify"]["ok"], "export_check": {k: v for k, v in s["export_check"].items()
                                                                          if k != "head"}}
                      for o, s in report["shape"].items()}
    short["breaks"] = {k: {kk: (vv if kk not in ("verify", "export_check") else
                                {x: y for x, y in vv.items() if x in ("rc", "ok", "words", "named", "stale",
                                                                      "differs", "mixed", "same_as_seal")})
                           for kk, vv in v.items()} for k, v in breaks.items()}
    print(json.dumps(short, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
