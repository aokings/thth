#!/usr/bin/env python3
"""台帳の器 段 10 の 10-2「訂正の下見」（`docs/指示_台帳の器_段10_こぴちゃの向き替え_2026-11-14.md`）。

段 7 の影の台帳（`register_stage7_realcheck.py` の回の置き場）を `--work` の下へ写し、こぴちゃの `8ead0fc` と同じ型の
訂正（主張の数値を 1 つ直す・根拠の記述を直す・記事の控えの指紋を付け直す）を、**器の命令で**打つ。
打った命令・終わりの番号・かかった秒・人の判断が要る所を数えて `<run>/rehearse.json` に書く。
こぴちゃの repo には触らない（読むのは段 7 の写しだけ）。

    python3 tools/register_stage10_rehearse.py --from <段 7 の回の置き場> [--work ~/Developer/thth-work/stage10]

直す中身は下見のための作りもの（主張 c-term-tea-grade-ctc-res-milktea1-24 の「17.1%」を「17.2%」に）。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time

CLAIM = "claim:c-term-tea-grade-ctc-res-milktea1-24"
EVIDENCE = "evidence:e-term-tea-grade-ctc-res-milktea1-24"
ARTICLE_CLAIM = "article_claim:k-abb9be8f90152577"
OLD, NEW = "17.1%", "17.2%"
NAME = "kopicha-media"
BY = "stage10-rehearse"
CHECKER = "stage10-checker"


class Log:
    def __init__(self, env):
        self.env, self.steps = env, []

    def run(self, label, args, *, who="LLM", decision=False, expect_rc=0):
        started = time.monotonic()
        r = subprocess.run([sys.executable, "-m", "thth", "register", *args], capture_output=True, text=True,
                           env=self.env, cwd=pathlib.Path(__file__).resolve().parent.parent)
        out = "\n".join(x for x in r.stdout.strip().splitlines() if not x.startswith("report_channel:"))
        try:
            data = json.loads(out) if out else None
        except (json.JSONDecodeError, IndexError):
            data = None
        step = {"label": label, "who": who, "decision": decision, "argv": ["thth", "register", *args],
                "rc": r.returncode, "seconds": round(time.monotonic() - started, 2),
                "out_head": (out or r.stderr.strip())[:400]}
        self.steps.append(step)
        if r.returncode != expect_rc:
            raise SystemExit(f"{label}: rc {r.returncode}（期待 {expect_rc}）\n{out[-1500:]}\n{r.stderr[-1500:]}")
        return data


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", required=True)
    ap.add_argument("--work", default=os.path.expanduser("~/Developer/thth-work/stage10"))
    args = ap.parse_args(argv)
    src = pathlib.Path(args.src)
    run = pathlib.Path(args.work) / ("rehearse-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    run.mkdir(parents=True)
    for d in ("config", "home", "root", "ledger", "out"):
        subprocess.run(["cp", "-cR", str(src / d), str(run / d)], check=True)
    env = dict(os.environ, XDG_CONFIG_HOME=str(run / "config"), HOME=str(run / "home"), THTH_ROOT=str(run / "root"))
    env.pop("THTH_REGISTER_SEAL_PUBLIC_KEY", None)
    log = Log(env)
    log.run("(準備) 影の台帳を控える", ["add", NAME, "--path", str(run / "ledger"), "--project", "kopicha",
                                  "--kind", "media", "--by", BY], who="準備")

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    os.environ.update({k: env[k] for k in ("XDG_CONFIG_HOME", "HOME", "THTH_ROOT")})
    from thth.register import vessel as V
    v = V.open_vessel(str(run / "ledger"))
    claim, evid = v.unique(CLAIM).values, v.unique(EVIDENCE).values
    new_statement = claim["statement_ja"].replace(OLD, NEW)
    new_hash = hashlib.sha1(new_statement.encode("utf-8")).hexdigest()[:12]
    now = lambda: datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec="seconds")

    # -- 訂正（今の道ではここが CSV 3 枚の手での直し）
    log.run("直す: 主張", ["row", "set", NAME, CLAIM, "--set", f"statement_ja={new_statement}",
                         "--set", f"object_text_ja={claim['object_text_ja'].replace(OLD, NEW)}", "--by", BY, "--json"])
    log.run("直す: 根拠", ["row", "set", NAME, EVIDENCE, "--set",
                         f"finding_ja={evid['finding_ja'].replace('水浸出物' + OLD, '水浸出物' + NEW)}", "--by", BY,
                         "--json"])
    log.run("直す: 記事の控えの指紋", ["row", "set", NAME, ARTICLE_CLAIM, "--set", f"statement_hash={new_hash}",
                                "--by", BY, "--json"])
    # -- 器の道で増える所
    out_site, out_tools = run / "out" / "site", run / "out" / "tools"
    red = log.run("確かめ: site が赤になる", ["verify", NAME, "--outlet", "site", "--material", str(out_site),
                                         "--now", now(), "--no-previous", "--json"], expect_rc=2)
    log.run("判断: 主張を出してよい", ["judge", NAME, CLAIM, "--question", "may_publish@site", "--conclusion", "good",
                                 "--how", "原典の要旨と照らした", "--reason-text", "下見の訂正", "--by", CHECKER, "--json"],
            who="検める者", decision=True)
    log.run("判断: 根拠を出してよい", ["judge", NAME, EVIDENCE, "--question", "may_publish@site", "--conclusion", "good",
                                 "--how", "原典の要旨と照らした", "--reason-text", "下見の訂正", "--by", CHECKER, "--json"],
            who="検める者", decision=True)
    results = {}
    for outlet, to in (("site", out_site), ("tools", out_tools)):
        as_of = now()
        plan = log.run(f"受け入れ 1 回め（見せる）: {outlet}", ["accept", NAME, "--outlet", outlet, "--to", str(to),
                                                       "--as-of", as_of, "--by", BY, "--json"], who="受け入れる者",
                   expect_rc=1)
        digest = (plan or {}).get("digest")
        log.run(f"受け入れ 2 回め（封）: {outlet}", ["accept", NAME, "--outlet", outlet, "--to", str(to), "--as-of", as_of,
                                                 "--by", BY, "--confirm", str(digest), "--json"],
                who="受け入れる者", decision=True)
        results[outlet] = log.run(f"確かめ: {outlet} が緑（CI と同じ）", ["verify", NAME, "--outlet", outlet,
                                                               "--material", str(to), "--now", now(),
                                                               "--no-previous", "--json"])
    report = {"run": str(run), "from": str(src), "correction": {"claim": CLAIM, "old": OLD, "new": NEW,
                                                                "statement_hash": new_hash},
              "red_words": sorted({p.get("word") for p in (red or {}).get("problems", [])}),
              "steps": log.steps,
              "counts": {"commands": sum(1 for s in log.steps if s["who"] != "準備"),
                         "write": 3,
                         "added_by_vessel": sum(1 for s in log.steps if s["who"] != "準備") - 3,
                         "decisions": sum(1 for s in log.steps if s["decision"]),
                         "seconds": round(sum(s["seconds"] for s in log.steps if s["who"] != "準備"), 1)},
              "green": {k: bool(v and v.get("ok")) for k, v in results.items()}}
    (run / "rehearse.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    for s in log.steps:
        print(f"{s['rc']:>2} {s['seconds']:>6}s  [{s['who']}{'・判断' if s['decision'] else ''}] {s['label']}")
    print(json.dumps({k: report[k] for k in ("counts", "green", "red_words")}, ensure_ascii=False))
    print(f"置き場: {run}")
    return 0 if all(report["green"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
