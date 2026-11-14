#!/usr/bin/env python3
"""台帳の器 段 6「書く口」を、各メディアの実物の表の写しで確かめる台本（指示 段 6 の「済み」の線の 1）。

各メディアの repo は**読むだけ**。表 1 枚を、器の取り込み（`adopt`。元には書かない）で作業場の下の台帳に写し、
その写しに `row add` で 1 行足し、`row set` で 1 欄直す。書く前と後で次を見る:

- レコードごとの sha256（見出しも 1 つのレコード。元の字のまま・改行も含む）が、変えた 1 レコードの外は全部同じ
  （足すときは、前の全部のバイトが後の頭にそのまま残る）
- ファイルの頭（BOM と見出し）と末尾（直す行が最後でなければ、最後のレコードのバイト）
- 改行の数の内訳（CRLF・LF・CR）: 足すと最後のレコードの改行が 1 つ増えるだけ・直すと変わらない
- 元の repo の表の sha256 が、確かめの前と後で同じ

出力は表ごとの「合う・合わない」と rc（全部合えば 0）。

    python3 tools/register_stage6_realcheck.py --developer /Volumes/NexDev/Developer \\
        --work /Volumes/NexDev/Developer/thth-work/stage6/run-1
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from thth.register import adopt as A, vessel as V  # noqa: E402

DECLS = HERE / "tools" / "register_decls"
AT = "2026-11-14T12:00:00+09:00"

# (名前, repo, 宣言, 種類, 表の道, 表の形の説明)
TABLES = [
    ("にがみラボ 混ざった改行の表", "nigamilab", "nigami", "topic_hub", "data/topic-hubs.csv",
     {"crlf": 14, "lf": 41, "blank_lines": 1}),
    ("にがみラボ CRLF の表", "nigamilab", "nigami", "recipe", "data/recipes.csv", {"lf": 0}),
    ("染めと化学 の表", "sometokagaku", "sometokagaku", "lead", "data/leads.csv", {"crlf": 0}),
]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def records(data: bytes) -> list:
    body = data[3:] if data.startswith(b"\xef\xbb\xbf") else data
    return V.split_csv_rows(body.decode("utf-8"))


def eols(data: bytes) -> dict:
    crlf = data.count(b"\r\n")
    return {"crlf": crlf, "lf": data.count(b"\n") - crlf, "cr": data.count(b"\r") - crlf}


def eol_of(raw: str) -> str:
    return "crlf" if raw.endswith("\r\n") else "lf" if raw.endswith("\n") else "cr" if raw.endswith("\r") else ""


def blank_lines(data: bytes) -> int:
    return sum(1 for raw in records(data)[1:] if not raw.strip("\r\n"))


def check_shape(data: bytes, expect: dict) -> list:
    got = dict(eols(data), blank_lines=blank_lines(data))
    return [f"表の形が見込みと違う: {k} {got[k]}（見込み {v}）" for k, v in expect.items() if got[k] != v]


def compare_add(before: bytes, after: bytes) -> list:
    bad = []
    a, b = records(before), records(after)
    if not after.startswith(before):
        bad.append("前の全部のバイトが後の頭に残っていない")
    if [sha(r.encode()) for r in a] != [sha(r.encode()) for r in b[:len(a)]] or len(b) != len(a) + 1:
        bad.append("レコードごとの sha256 の差が、足した 1 レコードだけでない")
    last = next(r for r in reversed(a) if r.strip("\r\n"))
    want = eols(before)
    if eol_of(last):
        want[eol_of(last)] += 1
    if eols(after) != want:
        bad.append(f"改行の内訳 {eols(after)}（見込み {want}・最後のレコードの改行 {eol_of(last)}）")
    if b and eol_of(b[-1]) != eol_of(last):
        bad.append("足した行の改行が最後のレコードの改行と違う")
    return bad


def compare_set(before: bytes, after: bytes, index: int) -> list:
    bad = []
    a, b = records(before), records(after)
    diff = [i for i, (x, y) in enumerate(zip(a, b)) if sha(x.encode()) != sha(y.encode())]
    if len(a) != len(b) or diff != [index]:
        bad.append(f"レコードごとの sha256 の差が、直した 1 レコードだけでない（差のある所 {diff[:5]}）")
    if a[0] != b[0] or before[:3] != after[:3]:
        bad.append("ファイルの頭（BOM と見出し）が変わった")
    if index != len(a) - 1 and a[-1] != b[-1]:
        bad.append("ファイルの末尾（最後のレコード）が変わった")
    if eols(before) != eols(after):
        bad.append(f"改行の内訳が変わった {eols(before)} → {eols(after)}")
    if eol_of(a[index]) != eol_of(b[index]):
        bad.append("直した行の改行が変わった")
    return bad


def check_table(developer: pathlib.Path, work: pathlib.Path, item) -> dict:
    name, repo, decl, kind_name, rel, shape = item
    src = developer / repo
    original = (src / rel).read_bytes()
    out = {"table": name, "path": f"{repo}/{rel}", "problems": check_shape(original, shape)}
    reg = work / f"{repo}-{kind_name}"
    plan = A.parse_plan({"schema": A.PLAN_SCHEMA, "include": [rel]})
    A.adopt(str(src), str(reg), plan=plan, by="段6の確かめ", at=AT, decl=str(DECLS / decl),
            checked_by="段6の確かめ（読むだけ）")
    vessel = V.open_vessel(str(reg))
    kind = vessel.kinds[kind_name]
    id_field = kind.id_rule["field"]
    rows = [r for r in vessel.all_records if r.kind == kind_name and r.id is not None and not r.problems]
    before = (reg / rel).read_bytes()
    if before != original:
        out["problems"].append("写しのバイトが元と違う")
    # 1 行足す
    new_id = f"{rows[0].id}-thth-s6check"
    A.row_add(str(reg), kind_name, {id_field: new_id}, by="段6の確かめ", at=AT, path=rel)
    added = (reg / rel).read_bytes()
    out["add"] = {"record": f"{kind_name}:{new_id}", "problems": compare_add(before, added)}
    # 1 欄直す（真ん中あたりの行の、番号でない最初の欄）
    target = rows[len(rows) // 2]
    header = V._parse_csv_line(records(added)[0], kind.delimiter)
    field = next(h for h in header if h != id_field)
    A.row_set(str(reg), target.ref, {field: (target.values.get(field) or "") + "（段6の確かめ）"}, by="段6の確かめ",
              at=AT)
    after = (reg / rel).read_bytes()
    index = next(i for i, raw in enumerate(records(added)) if V.bytes_version(raw.encode("utf-8")) == target.version)
    out["set"] = {"record": target.ref, "field": field, "problems": compare_set(added, after, index)}
    if (src / rel).read_bytes() != original:
        out["problems"].append("元の repo の表が変わった")
    out["ok"] = not (out["problems"] or out["add"]["problems"] or out["set"]["problems"])
    out["eols"] = {"before": eols(before), "after": eols(after)}
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--developer", default="/Volumes/NexDev/Developer")
    p.add_argument("--work", required=True, help="写しの置き場（新しいフォルダ。thth-work/stage6/ の下）")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    work = pathlib.Path(args.work)
    if work.exists():
        print(f"写しの置き場 {work} がもうあります。新しい名前で打ち直してください。", file=sys.stderr)
        return 2
    work.mkdir(parents=True)
    results = [check_table(pathlib.Path(args.developer), work, item) for item in TABLES]
    rc = 0 if all(r["ok"] for r in results) else 1
    if args.json:
        print(json.dumps({"rc": rc, "results": results}, ensure_ascii=False, indent=1))
        return rc
    for r in results:
        print(f"[{'合う' if r['ok'] else '合わない'}] {r['table']}（{r['path']}）")
        print(f"    足す {r['add']['record']}: " + ("差は 1 レコード" if not r["add"]["problems"]
                                                   else "・".join(r["add"]["problems"])))
        print(f"    直す {r['set']['record']} の {r['set']['field']}: " + ("差は 1 レコード" if not r["set"]["problems"]
                                                                       else "・".join(r["set"]["problems"])))
        print(f"    改行の内訳 {r['eols']['before']} → {r['eols']['after']}")
        for x in r["problems"]:
            print(f"    {x}")
    print(f"rc={rc}（合わない {sum(1 for r in results if not r['ok'])}）")
    return rc


if __name__ == "__main__":
    sys.exit(main())
