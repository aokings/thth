#!/usr/bin/env python3
"""台帳の器 段 8 の健康を、各メディアの実物で確かめる台本（各メディアの repo を**読むだけ**。何も書かない）。

    python3 tools/register_stage8_realcheck.py [--developer ~/Developer] [--media <名前>] [--json]
                                               --work <一時の置き場の親（repo の外）>

指示 `docs/指示_台帳の器_段8_健康と複数のメディア_2026-11-14.md` §2。当て方は `tools/register_stage4_realcheck.py` と
同じ（`tools/register_decls/<メディア>/` の宣言を一時の置き場に写して `decl_base` で当てる）。

出す物（メディアごと）: 例外の数・かかった秒・群ごとの数（数えられた項目の和）・数えられない項目・主な項目の数。
数の正しさは「段 4 の確かめの数と食い違わない」所まで見る:
- 先の無い指しの数 = 段 4 の `measure` の `dangling` の和 = `media.json` の `expected.dangling` の和
- 検査の違反の数 = 別に回した `checks.run` の error・warn・review の和 = `expected.checks` の和
- 1 件も当たらない検査 = 別に回した `checks.run` で `checked` が 0 の検査（id の組が同じ）
食い違いが 1 つでもあれば rc 1。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))

from thth.register import checks as C  # noqa: E402
from thth.register import health as H  # noqa: E402
from thth.register import vessel as V  # noqa: E402


def _stage4():
    spec = importlib.util.spec_from_file_location("register_stage4_realcheck", HERE.parent / "register_stage4_realcheck.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _key_items(value: dict) -> dict:
    g = value["groups"]
    pick = {
        "thin": g["net"]["thin"], "dangling": g["net"]["dangling"], "stale": g["judgment"]["stale"],
        "observed_only": g["judgment"]["observed_only"], "undeclared_values": g["judgment"]["undeclared_values"],
        "provenance_review": g["judgment"]["provenance_review"], "review_passed": g["deadline"]["review_passed"],
        "unmatched_checks": g["rules"]["unmatched_checks"], "failed_checks": g["rules"]["failed_checks"],
        "unverifiable_pointers": g["rules"]["unverifiable_pointers"], "violations": g["rules"]["violations"],
    }
    return {k: v["count"] for k, v in pick.items()}


def run_one(S4, folder: pathlib.Path, media: dict, developer: pathlib.Path, work: pathlib.Path) -> dict:
    root = developer / media["source"]
    out = {"name": media["name"], "source": str(root)}
    if not root.is_dir():
        out.update(ok=False, error="フォルダが無い")
        return out
    with tempfile.TemporaryDirectory(prefix=f"stage8-{folder.name}-", dir=str(work)) as tmp:
        base = pathlib.Path(tmp) / "decl"
        S4.copy_decls(folder, base, keep_checks=True)
        started = time.monotonic()
        try:
            value = H.health(str(root), decl_base=str(base), name=media["name"])
        except Exception as exc:  # 確かめの台本なので、止まった所を報告に残す（例外は 0 であるべき）
            out.update(ok=False, exception=f"{type(exc).__name__}: {str(exc)[:300]}",
                       seconds=round(time.monotonic() - started, 2))
            return out
        out["seconds"] = round(time.monotonic() - started, 2)
        # 段 4 の確かめと、別の読みで同じ数を作る（比べる相手）
        stage4 = S4.measure(V, C, str(root), str(base))
        vessel = V.open_vessel(str(root), str(base))
        result = C.run(vessel, [])
    line = H.summary_line(value)
    out["records"] = value["records"]
    out["judgments"] = value["judgments"]
    out["inspections"] = value["inspections"]
    out["groups"] = line["groups"]
    out["uncountable"] = line["uncountable"]
    out["items"] = _key_items(value)
    unmatched = value["groups"]["rules"]["unmatched_checks"]
    out["unmatched_checks"] = [{"kind": u["kind"], "id": u["id"], "when": u["when"]} for u in unmatched["examples"]]
    mismatch = []
    expected = media.get("expected", {})
    dangling = value["groups"]["net"]["dangling"]["count"]
    if dangling != sum(stage4["dangling"].values()):
        mismatch.append(["dangling", "measure", sum(stage4["dangling"].values()), dangling])
    if dangling != sum(expected.get("dangling", {}).values()):
        mismatch.append(["dangling", "expected", sum(expected.get("dangling", {}).values()), dangling])
    violations = value["groups"]["rules"]["violations"]["count"]
    total = result["error"] + result["warn"] + result["review"]
    if violations != total:
        mismatch.append(["violations", "checks.run", total, violations])
    if violations != sum(expected.get("checks", {}).values()):
        mismatch.append(["violations", "expected", sum(expected.get("checks", {}).values()), violations])
    zero = sorted((c["kind"], c["id"]) for c in result["list"] if not c.get("failed") and c.get("checked", 0) == 0)
    got = sorted((u["kind"], u["id"]) for u in unmatched["examples"])
    if unmatched["count"] != len(zero) or (len(zero) <= H.EXAMPLES_MAX and got != zero):
        mismatch.append(["unmatched_checks", "checks.run", zero, got])
    if value["groups"]["rules"]["failed_checks"]["count"] != result["failed"]:
        mismatch.append(["failed_checks", "checks.run", result["failed"],
                         value["groups"]["rules"]["failed_checks"]["count"]])
    out["mismatch"] = mismatch
    out["ok"] = not mismatch
    return out


def say(one: dict) -> None:
    print(f"== {one['name']}（{one['source']}）: {'合う' if one['ok'] else '合わない'}")
    if "error" in one or "exception" in one:
        print(f"  止まった: {one.get('error') or one.get('exception')}")
        return
    print(f"  例外 0・{one['seconds']} 秒・記録 {one['records']}・判断 {one['judgments']}・検収の表から {one['inspections']}")
    print("  群ごとの数: " + "・".join(f"{g} {n}" for g, n in one["groups"].items()))
    print("  主な項目: " + "・".join(f"{k} {'数えられない' if n is None else n}" for k, n in one["items"].items()))
    if one["uncountable"]:
        print(f"  数えられない項目: {', '.join(one['uncountable'])}")
    for u in one["unmatched_checks"]:
        print(f"  1 件も当たらない検査: {u['kind']}.{u['id']} when={json.dumps(u['when'], ensure_ascii=False)}")
    for row in one["mismatch"]:
        print(f"  合わない {row[0]}（{row[1]}）: 期待 {row[2]}・今 {row[3]}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--developer", default=os.path.expanduser("~/Developer"))
    parser.add_argument("--media", help="1 つだけ（media.json の name か、フォルダの名前）")
    parser.add_argument("--work", required=True, help="一時の置き場を作る親（repo の外）")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    S4 = _stage4()
    work = pathlib.Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    targets = S4.media_list(args.media)
    if not targets:
        print("media.json が見つからない", file=sys.stderr)
        return 2
    results = [run_one(S4, folder, media, pathlib.Path(args.developer), work) for folder, media in targets]
    if args.json:
        print(json.dumps({"media": results}, ensure_ascii=False, indent=2, default=str))
    else:
        for one in results:
            say(one)
    return 0 if all(one["ok"] for one in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
