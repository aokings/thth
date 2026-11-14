#!/usr/bin/env python3
"""台帳の器 段 5「跡」を、各メディアの実物で確かめる台本（各メディアの repo を**読むだけ**。何も書かない）。

    python3 tools/register_stage5_realcheck.py [--developer ~/Developer] [--work <作業の置き場>] [--only kopicha|mmr|jiangshi]
                                               [--decls <宣言の置き場>]

見る物（指示 `docs/指示_台帳の器_段5_跡と消す求め_2026-11-14.md` §1 の 2）:

- **こぴちゃ**: repo そのものを台帳のフォルダとし、宣言は `tools/register_decls/kopicha/`（台帳の外）。commit
  `8ead0fc^..8ead0fc` と `3398d6d28^..3398d6d28` の差（`adopt.diff`。git は読むだけ）が、期待した記録だけを出すか。
  期待は差とは別に作る: commit の前と後の枚を git から読み、表は Python の `csv` で行ごとに、YAML の枚は 1 枚ごとに
  比べる（番号は宣言の `id` の決まり。鍵の番号は器と同じ `k-<指紋の頭>`）。commit の枚の一覧は `register_adopt_realcheck`
  の `REPLAY`（目録 こぴちゃ K-1）と合うか。古い commit に無い種類は「その時点に無い」と出して先へ進む。
- **MMR の訂正・キョンシーの監査**: 判断から「対象の版」と「元の記録」へたどり、今の版に効くかを 1 例ずつ出す
  （版を示していない検収の記録なら「言えない」）。宣言に検収の記録を持つ表が無く器の形で書けない物は、読まずに
  「器の不足で未確認」と理由を出す（`GAPS`）。
  宣言（`tools/register_decls/mmr/`・`jiangshi/`）が無ければ「宣言待ち」と出す（別の書き手が書いている途中）。
  `--decls` で宣言の置き場を替えられる（別の作業木の書きかけの宣言を、読むだけで当てて試すとき）。

一時の置き場は `--work`（既定 `/Volumes/NexDev/Developer/thth-work/stage5/realcheck`）の下だけに作る。
出力は例ごとの「期待と合う・合わない」と、終わりの rc（合わない物が 1 つでもあれば 1）。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from thth.register import adopt as A, judgment as J, trail as T, vessel as V  # noqa: E402

DECLS = HERE / "tools" / "register_decls"
REPLAY = {  # tools/register_adopt_realcheck.py の REPLAY と同じ（目録 こぴちゃ K-1）
    "8ead0fc": ["data/knowledge/claims.csv", "data/knowledge/evidence.csv", "data/knowledge/article-claims.csv"],
    "3398d6d28": ["src/content/origins/sikkim.yaml", "data/knowledge/entities.csv",
                  "data/knowledge/article-claims.csv"],
}
K1_CLAIM = "claim:c-term-tea-grade-ctc-res-milktea1-24"
MEDIA = {"mmr": "music-research", "jiangshi": "jiangshi-lab"}


def git_show(repo: pathlib.Path, rev: str, path: str) -> bytes | None:
    """期待を作るための読み（差の部品と別に、git を直に読む）。"""
    result = subprocess.run(["/usr/bin/git", "-C", str(repo), "show", f"{rev}:{path}"], capture_output=True,
                            env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "GIT_CONFIG_NOSYSTEM": "1",
                                 "GIT_CONFIG_GLOBAL": "/dev/null"})
    return result.stdout if result.returncode == 0 else None


def changed_paths(repo: pathlib.Path, commit: str) -> list:
    result = subprocess.run(["/usr/bin/git", "-C", str(repo), "diff-tree", "--no-commit-id", "-r", "--name-only",
                             f"{commit}^", commit], capture_output=True, check=True,
                            env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "GIT_CONFIG_NOSYSTEM": "1",
                                 "GIT_CONFIG_GLOBAL": "/dev/null"})
    return sorted(result.stdout.decode("utf-8").split())


def kind_of(decl: pathlib.Path, path: str):
    for f in sorted((decl / "kinds").glob("*.json")):
        kind = json.loads(f.read_text(encoding="utf-8"))
        places = kind["place"] if isinstance(kind["place"], list) else [kind["place"]]
        import fnmatch
        if any(fnmatch.fnmatchcase(path, p) for p in places):
            return kind
    return None


def rows(data: bytes | None, kind: dict) -> dict:
    """表の行を番号 → 行の値の組に（Python の csv で読む）。"""
    if data is None:
        return {}
    text = data.decode("utf-8-sig")
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        rule = kind["id"]
        if "field" in rule:
            rid = row.get(rule["field"])
        else:
            rid = "k-" + V.short(V.fp([row.get(k) for k in rule["key"]]))
        out[rid] = tuple(row.items())
    return out


def expected_for(repo: pathlib.Path, decl: pathlib.Path, commit: str) -> dict:
    added, changed, vanished = set(), set(), set()
    for path in changed_paths(repo, commit):
        kind = kind_of(decl, path)
        if kind is None:
            continue
        before, after = git_show(repo, f"{commit}^", path), git_show(repo, commit, path)
        if kind["format"] == "csv" and kind.get("records") == "rows":
            a, b = rows(before, kind), rows(after, kind)
            added |= {f"{kind['kind']}:{i}" for i in set(b) - set(a)}
            vanished |= {f"{kind['kind']}:{i}" for i in set(a) - set(b)}
            changed |= {f"{kind['kind']}:{i}" for i in set(a) & set(b) if a[i] != b[i]}
        else:
            name = pathlib.PurePosixPath(path).stem
            ref = f"{kind['kind']}:{name}"
            (added if before is None else vanished if after is None else changed).add(ref)
    return {"added": sorted(added), "changed": sorted(changed), "vanished": sorted(vanished)}


def check_kopicha(developer: pathlib.Path) -> list:
    repo = developer / "kopicha"
    decl = DECLS / "kopicha"
    out = []
    for commit, files in REPLAY.items():
        name = f"こぴちゃ {commit}^..{commit}"
        paths = changed_paths(repo, commit)
        try:
            value = A.diff(str(repo), f"{commit}^..{commit}", decl=str(decl))
        except Exception as exc:  # noqa: BLE001 — 台本は止めずに合わないと出す
            out.append({"example": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            continue
        got = {"added": value["records"]["added"],
               "changed": [c["record"] for c in value["records"]["changed"]],
               "vanished": value["records"]["vanished"]}
        want = expected_for(repo, decl, commit)
        checks = {
            "枚の一覧が REPLAY と同じ": sorted(files) == paths,
            "記録が期待と同じ": got == want,
            "赤でない": not value["red"],
            "作業木を変えていない（commit の端）": all(s["side"] == "commit" for s in value["sides"]),
        }
        if commit == "8ead0fc":
            checks["K-1: 主張・根拠・記事と主張の 1 行ずつ"] = (
                K1_CLAIM in got["changed"] and len(got["changed"]) == 3 and not got["added"]
                and sorted(c.split(":")[0] for c in got["changed"]) == ["article_claim", "claim", "evidence"])
        out.append({"example": name, "ok": all(checks.values()), "checks": checks, "got": got, "want": want,
                    "absent_kinds": [s.get("absent_kinds") for s in value["sides"]],
                    "files": value["files"]})
    return out


# 宣言に検収の記録を持つ表（inspects）が書けない理由（器の不足）。宣言を曲げずに報告する。
GAPS = {
    "jiangshi": "原文確認の記録（docs/original-check-log.md）は表でなく、1 回 1 行の箇条書きの md（日付・誰が・根拠の"
                "番号の並び・何のため）。器の形式（csv・json・yaml・頭つきの md・bytes）に、md の行を記録として読む形が"
                "無い。読者監査（audits/reader/*.json）は commit（base）を持つが、確かめた物を「ファイルと行の番号」で指し、"
                "記録の番号で指さないので、指す欄にできない",
}


def check_trace(developer: pathlib.Path, media: str, decls: pathlib.Path = DECLS) -> list:
    """判断（検収の記録）から対象の版と元の記録へ。宣言が無ければ「宣言待ち」。宣言に検収の記録を持つ表が無く、
    器の形で書けないと分かっている物は、読まずに「器の不足で未確認」（MMR は読むだけで 10 分を超える）。"""
    decl = decls / media
    name = f"{media} 判断から版と元の記録へ"
    if not (decl / "kinds").is_dir():
        return [{"example": name, "ok": None, "waiting": f"宣言待ち（{decl} が無い）"}]
    has_inspects = any("inspects" in json.loads(f.read_text(encoding="utf-8"))
                       for f in sorted((decl / "kinds").glob("*.json")))
    if not has_inspects:
        return [{"example": name, "ok": None, "gap": GAPS.get(media, "宣言に検収の記録を持つ表（inspects）が無い")}]
    repo = developer / MEDIA[media]
    vessel = V.open_vessel(str(repo), decl_base=str(decl))
    judgments = J.inspections(vessel)
    if not judgments:
        return [{"example": name, "ok": False, "error": "inspects の表から作れる判断が 0 件"}]
    j = judgments[0]
    if T.shows_version(vessel, j):
        view = T.judgment_view(vessel, j)
        ok = "table" in view["origin"] and view["state"] in ("effective", "stale", "vanished") and view["depend"]
    else:
        view = T.unversioned_view(vessel, j)
        ok = view["table"] is not None and view["row"] is not None and view["state"] == "cannot_say"
    return [{"example": name, "ok": bool(ok), "record": j["target"]["record"], "view": view,
             "count": len(judgments),
             "states": {s: sum(1 for x in judgments if J.evaluate(vessel, x) == s)
                        for s in ("effective", "stale", "vanished")}}]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--developer", default="/Volumes/NexDev/Developer")
    parser.add_argument("--work", default="/Volumes/NexDev/Developer/thth-work/stage5/realcheck")
    parser.add_argument("--only", default=None, choices=["kopicha", "mmr", "jiangshi"])
    parser.add_argument("--decls", default=str(DECLS), help="MMR とキョンシーの宣言の置き場（読むだけ）")
    args = parser.parse_args(argv)
    work = pathlib.Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(work)  # 器の一時の置き場もここ（/tmp に置かない）
    developer = pathlib.Path(args.developer)
    results = []
    if args.only in (None, "kopicha"):
        results += check_kopicha(developer)
    for media in ("mmr", "jiangshi"):
        if args.only in (None, media):
            results += check_trace(developer, media, pathlib.Path(args.decls))
    bad = 0
    for r in results:
        mark = ("器の不足で未確認" if r.get("gap") else "宣言待ち") if r["ok"] is None else \
            ("期待と合う" if r["ok"] else "期待と合わない")
        print(f"[{mark}] {r['example']}")
        for key, val in (r.get("checks") or {}).items():
            print(f"    {'ok' if val else 'NG'} {key}")
        if "got" in r:
            print(f"    記録: 足した {r['got']['added']}")
            print(f"          変わった {r['got']['changed']}")
            print(f"          消えた {r['got']['vanished']}")
            if r["got"] != r["want"]:
                print(f"    期待: {r['want']}")
            print(f"    その時点に無い種類: 前 {r['absent_kinds'][0]}・後 {r['absent_kinds'][1]}")
        if "view" in r:
            v = r["view"]
            if v.get("why") == "version_not_shown":
                print(f"    {r['record']}: 版を示していない検収の記録・元の表 {v['table']} の行 {v['row']}"
                      f"（{v['file']}）・今の版に効くかは言えない")
            else:
                print(f"    {r['record']}: 対象の版 {v['version']}・依る所 {str(v['depend'])[:16]}・{v['state']}・"
                      f"元 {v['origin']}（判断 {r['count']} 件: {r['states']}）")
        for key in ("waiting", "gap", "error"):
            if key in r:
                print(f"    {r[key]}")
        bad += r["ok"] is False
    rc = 1 if bad else 0
    gaps = sum(1 for r in results if r["ok"] is None and r.get("gap"))
    waiting = sum(1 for r in results if r["ok"] is None and not r.get("gap"))
    print(f"rc={rc}（合わない {bad}・器の不足で未確認 {gaps}・宣言待ち {waiting}）")
    return rc


if __name__ == "__main__":
    sys.exit(main())
