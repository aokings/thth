#!/usr/bin/env python3
"""台帳の器 段 3 を、こぴちゃと MMR の実物で一度だけ確かめる台本（各メディアの repo を**読むだけ**。何も書かない）。

    python3 tools/register_route_realcheck.py [--developer ~/Developer]
                                              [--shadow /Volumes/NexDev/Developer/thth-work/shadow] [--only kopicha]

影の台帳・材料・試しの鍵は、repo の外の `<shadow>/route-<日時>/` に作る（毎回新しいフォルダ。前の回の物は消さない）。
既定の置き場は `thth-work/` の下（大きな写しを iCloud の `~/Documents` に置かない。11/14 の決まり）。
MMR の 2 つの出し口は、書き出し先を出し口ごとに分ける（段 7 の前半から、読む者の同じ出し口でも、別の出し口の封の
材料が置いてある道へ違う中身を書くと断る。11/14 の段 7 の後半で直した）。
宣言は `tests/fixtures/register_route/` の見本の物を使う。見る物（期待する答え `docs/期待する答え_台帳の器_段1〜3_2026-11-13.md` §4）:

- こぴちゃ: 主張の表（`data/knowledge/claims.csv`）を、今の決まり（状態で除かずに全部出す）のまま基準で受けて封をすると、
  頁の事実欄の材料が元と同じバイト。`8ead0fc` と同じ型の直し（文だけ・状態は据え置き）で、配る前に「外れる 1 件」と、
  CI の確かめが赤。
- MMR: 照合台帳の全部（`site/src/credits/*.json`）で、曲の頁と人の頁の 2 つの出し口。留保に使える 85・`unresolved` で
  出ない 39・出し口の無い組で出ない行（期待する答えの 328 は held だけの数。shown を足すと 335）。
"""
from __future__ import annotations

import argparse
import collections
import datetime
import hashlib
import json
import os
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

FIX = pathlib.Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "register_route"
AT = "2026-11-14T12:00:00+09:00"


def sha(path) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--developer", default="~/Developer")
    ap.add_argument("--shadow", default="/Volumes/NexDev/Developer/thth-work/shadow")
    ap.add_argument("--only", choices=("kopicha", "mmr"), default=None)
    args = ap.parse_args()
    dev = pathlib.Path(args.developer).expanduser()
    work = pathlib.Path(args.shadow).expanduser() / ("route-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    work.mkdir(parents=True)
    os.environ["XDG_CONFIG_HOME"] = str(work / "config")     # 試しの鍵（repo の外・この回だけ）
    os.environ["THTH_ROOT"] = str(work / "root")
    (work / "root").mkdir()
    os.environ.pop("THTH_REGISTER_SEAL_PUBLIC_KEY", None)
    from thth.register import route as R, seal as S, vessel as V

    S.init_key()
    ok = True

    def accept(led, outlet, to):
        plan = R.accept_plan(str(led), outlet, str(to), AT)
        return R.accept(str(led), outlet, str(to), AT, confirm=plan["digest"], at=AT)

    # ---- こぴちゃ
    if args.only == "mmr":
        return mmr(dev, work, R, V, accept, ok)
    src = dev / "kopicha/data/knowledge/claims.csv"
    led = work / "kopicha"
    (led / "data/knowledge").mkdir(parents=True)
    shutil.copytree(FIX / "kopicha/.thth", led / ".thth")
    shutil.copy(src, led / "data/knowledge/claims.csv")
    made = R.baseline(str(led), "facts", by="register_route_realcheck", at=AT)
    site = work / "kopicha-site"
    v = accept(led, "facts", site)
    same = sha(src) == sha(site / "data/knowledge/claims.csv")
    ok &= same
    print(f"こぴちゃ: 主張 {len(made)} 件を基準で受けた・世代 {v['generation']}・材料 {v['items']} 件・"
          f"元と材料の sha256 {sha(src)[:16]} / {sha(site / 'data/knowledge/claims.csv')[:16]}（同じ={same}）")
    r = R.verify(str(led), "facts", str(site), "2026-11-15", no_previous=True)
    ok &= r["ok"]
    print(f"  確かめ: ok={r['ok']}")
    p = led / "data/knowledge/claims.csv"
    data = p.read_bytes()
    p.write_bytes(data.replace("水浸出物17.1%".encode(), "水浸出物17.0%".encode(), 1))
    e = R.export(str(led), "facts", str(site), AT, check=True)
    r = R.verify(str(led), "facts", str(site), "2026-11-15", no_previous=True)
    ok &= len(e["changes"]["leaving"]) == 1 and not r["ok"]
    print(f"  文だけを直す（状態は据え置き）: 外れる {e['changes']['leaving']}・確かめ ok={r['ok']} "
          f"{[x['word'] for x in r['problems']]}")

    if args.only == "kopicha":
        print(f"置き場: {work}")
        print("MMR は回していない（未確認）")
        print("全部が期待のとおり" if ok else "期待と違う所がある")
        return 0 if ok else 1
    return mmr(dev, work, R, V, accept, ok)


def mmr(dev, work, R, V, accept, ok) -> int:
    led = work / "mmr"
    shutil.copytree(FIX / "mmr/.thth", led / ".thth")
    shutil.copytree(dev / "music-research/site/src/credits", led / "site/src/credits")
    kp = led / ".thth/kinds/credit.json"
    kind = json.loads(kp.read_text())
    kind["max_bytes"] = 1024 * 1024   # 64 KB を超える枚がある（ap-alex-gopher など 29 枚）
    kp.write_text(json.dumps(kind, ensure_ascii=False, indent=2))
    vessel = V.open_vessel(str(led))
    print(f"MMR: 枚 {len(vessel.all_records)}・行 {sum(len(r.units) for r in vessel.all_records)}・"
          f"読めない枚 {sum(1 for r in vessel.all_records if r.problems)}")
    for outlet in ("song_page", "artist_page"):
        R.baseline(str(led), outlet, by="register_route_realcheck", at=AT)
    sites = {outlet: work / f"mmr-site-{outlet}" for outlet in ("song_page", "artist_page")}
    for outlet, site in sites.items():
        accept(led, outlet, site)
    song = json.loads((sites["song_page"] / "materials/song_page.json").read_text())
    artist = json.loads((sites["artist_page"] / "materials/artist_page.json").read_text())
    res = [i for i in song if i["as"] == "reservation"]
    plan = R.accept_plan(str(led), "song_page", str(sites["song_page"]), AT)
    out = collections.Counter(e["why"] for e in plan["result"]["excluded"])
    print(f"  曲の頁: 事実 {sum(1 for i in song if i['as'] == 'fact')}・留保 {len(res)} "
          f"{dict(collections.Counter(i['reason_kind'] for i in res))}・出ない {dict(out)}")
    print(f"  人の頁: 事実 {sum(1 for i in artist if i['as'] == 'fact')}・留保 "
          f"{sum(1 for i in artist if i['as'] == 'reservation')}")
    ok &= len(res) == 85 and out.get("reason_kind_not_allowed") == 39
    for outlet in ("song_page", "artist_page"):
        r = R.verify(str(led), outlet, str(sites[outlet]), "2026-11-15", no_previous=True)
        ok &= r["ok"]
        print(f"  確かめ {outlet}: ok={r['ok']}")
    print(f"置き場: {work}")
    print("全部が期待のとおり" if ok else "期待と違う所がある")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
