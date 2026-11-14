"""台帳の器 段 9 の器の不足の直し: 漢字を含む置き場の型・`lines`（見出しの無い区切りの行）・`ts_const`
（TypeScript の `export const` の値）。

段 9 の受け入れで、中受の問（照合台帳 47 枚・TypeScript の一覧 2 枚）と染めと化学（TypeScript の一覧 1 枚・見出しの
無いタブ区切り 1 枚）が「器の不足」で未完了だった。新しい形式は読むだけで、書く口・消す口は表（csv）として
扱わない（枚ごとだけ）。
"""
from __future__ import annotations

import json
import pathlib
import unicodedata

import pytest

from thth.register import tsread, vessel as V


def make_ledger(base: pathlib.Path, kinds: list, files: dict) -> pathlib.Path:
    (base / ".thth" / "kinds").mkdir(parents=True, exist_ok=True)
    for kind in kinds:
        (base / ".thth" / "kinds" / f"{kind['kind']}.json").write_text(json.dumps(kind, ensure_ascii=False),
                                                                       encoding="utf-8")
    for rel, text in files.items():
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return base


def kind(name, place, fmt, **extra):
    return {"schema": "thth.kind/1", "kind": name, "place": place, "format": fmt, **extra}


def parse_fails(raw, word):
    with pytest.raises(V.DeclarationError) as info:
        V.parse_kind(raw, ".thth/kinds/x.json")
    assert info.value.word == word


# ================================================================== 漢字を含む置き場の型

def test_漢字を含む置き場の型で枚を読み番号はファイル名(tmp_path):
    root = make_ledger(tmp_path / "l", [kind("daichou", "docs/照合台帳_*.md", "bytes", id={"name": True})],
                       {"docs/照合台帳_1月入試_2026-11-09.md": "# 照合\n", "docs/ほか.md": "x\n"})
    vessel = V.open_vessel(str(root))
    got = [(r.id, r.path) for r in vessel.all_records if r.kind == "daichou"]
    assert got == [("照合台帳_1月入試_2026-11-09", "docs/照合台帳_1月入試_2026-11-09.md")]


@pytest.mark.parametrize("place", [
    "docs/照合 台帳.md",                           # 空白
    "docs/　台帳.md",                          # 全角の空白（Z）
    "docs/台帳​.md",                          # 幅の無い空白（Cf）
    "docs/台帳\\x.md",                             # 逆斜線
    "docs/台帳:x.md",                              # ASCII で前から受けない字
    unicodedata.normalize("NFD", "docs/ガイド.md"),  # NFC でない
    "台" * 201,
])
def test_置き場の型の受けない字(place):
    parse_fails(kind("k", place, "bytes", id={"name": True}), "place")


def test_置き場の型のASCIIの読みは前のまま():
    assert V.PLACE.fullmatch("data/*.csv")
    assert not V.PLACE.fullmatch("data/a b.csv")
    assert not V.PLACE.fullmatch("")
    assert V.PLACE.fullmatch("docs/照合台帳_*.md")


def test_出口の外す型も漢字を受ける():
    from thth.register import route as R
    assert R.V.PLACE.fullmatch("docs/下書き_*.md")


# ================================================================== lines

OWNER = ("# 注の頭\n#\n"
         "/a/x/\t2026-11-01\t読んだ #6272 の注\n"
         "/a/y/\t2026-11-02\n"
         "\n   \n"
         "/a/x/\t2026-11-01\n"
         "/a/x/\t2026-11-01\tもう一度\n"
         "/a/z/\t2026-11-03\t a \t余り\n"
         "/a/w/\n")


def owner_kind(**extra):
    base = dict(delimiter="\t", columns=["path", "read_on", "memo"], columns_min=2, comment="#",
                id={"key": ["path", "read_on"], "repeat": "count"})
    base.update(extra)
    return kind("owner", "site/owner-read.txt", "lines", **base)


def test_lines_見出しを宣言で渡し注と空の行を読まない(tmp_path):
    root = make_ledger(tmp_path / "l", [owner_kind()], {"site/owner-read.txt": OWNER})
    recs = [r for r in V.open_vessel(str(root)).all_records if r.kind == "owner"]
    assert [r.values for r in recs] == [
        {"path": "/a/x/", "read_on": "2026-11-01", "memo": "読んだ "},
        {"path": "/a/y/", "read_on": "2026-11-02"},
        {"path": "/a/x/", "read_on": "2026-11-01"},
        {"path": "/a/x/", "read_on": "2026-11-01", "memo": "もう一度"},
        {"path": "/a/z/", "read_on": "2026-11-03", "memo": " a "},   # 欄の字は削らない
        {"path": "/a/w/"},
    ]
    # 省ける後ろの列は読まない欄（null と別）。columns_min より短い行と長すぎる行は row_width
    assert recs[1].unread == ("memo",)
    assert [r.problems for r in recs] == [[], [], [], [], ["row_width"], ["row_width"]]
    # 書き足していく帳面: 同じ鍵の 2 回目からは .2・.3（前の番号は動かない）
    first = recs[0].id
    assert first.startswith("k-") and recs[2].id == first + ".2" and recs[3].id == first + ".3"
    assert len({r.id for r in recs}) == len(recs)
    # 版は行の元の字（注を含む）
    assert recs[0].version == V.bytes_version("/a/x/\t2026-11-01\t読んだ #6272 の注\n".encode("utf-8"))


def test_lines_repeatが無ければ同じ鍵は両方に印(tmp_path):
    root = make_ledger(tmp_path / "l", [owner_kind(id={"key": ["path", "read_on"]})],
                       {"site/owner-read.txt": "/a/\t2026-11-01\n/a/\t2026-11-01\n"})
    recs = [r for r in V.open_vessel(str(root)).all_records if r.kind == "owner"]
    assert all("id_duplicate" in r.problems for r in recs)


def test_lines_注の字が無ければ井桁は字(tmp_path):
    root = make_ledger(tmp_path / "l", [kind("owner", "o.txt", "lines", columns=["a", "b"], id={"field": "a"})],
                       {"o.txt": "x\t#1\n"})
    recs = [r for r in V.open_vessel(str(root)).all_records if r.kind == "owner"]
    assert recs[0].values == {"a": "x", "b": "#1"} and recs[0].problems == []


@pytest.mark.parametrize("extra,word", [
    ({"columns": []}, "columns"),
    ({"columns": ["a", "a"]}, "columns"),
    ({"columns": ["a b"]}, "columns"),
    ({"columns": ["a"], "columns_min": 2}, "columns_min"),
    ({"columns": ["a"], "columns_min": 0}, "columns_min"),
    ({"columns": ["a"], "columns_min": True}, "columns_min"),
    ({"columns": ["a"], "comment": ";"}, "comment"),
    ({"columns": ["a"], "delimiter": "|"}, "delimiter"),
    ({"columns": ["a"], "rows_at": ["x"]}, "rows_at"),
    ({"columns": ["a"], "records": "file"}, "records"),
])
def test_lines_宣言の断り(extra, word):
    parse_fails(kind("k", "o.txt", "lines", id={"field": "a"}, **extra), word)


def test_lines_の欄はほかの形式に書けない():
    parse_fails(kind("k", "o.csv", "csv", id={"field": "a"}, columns=["a"]), "columns")
    parse_fails(kind("k", "o.csv", "csv", id={"field": "a"}, comment="#"), "columns")
    parse_fails(kind("k", "o.csv", "csv", id={"field": "a", "repeat": "count"}), "id")


def test_lines_の既定の区切りはタブ():
    k = V.parse_kind(kind("k", "o.txt", "lines", columns=["a"], id={"field": "a"}), ".thth/kinds/k.json")
    assert k.delimiter == "\t" and k.columns_min == 1 and k.comment is None


# ================================================================== ts_const

TS = """// 注
import type { X } from './x';

export interface Dougu { id: string; title: string }

/** 道具 */
export const dougu: Dougu[] = [
  {
    id: 'a-1', // 注
    title: "題 \\"引用\\" と \\u{1F600}",
    n: -1_000, f: 1.5, ok: true, no: null, u: undefined,
    at: new Date('2026-11-07'),
    'quoted-key': `テンプレート`,
  },
  { id: 'b-2', title: 'b', list: [1, 2, ], },
];
export const HIDDEN: ReadonlySet<string> = new Set([
  'claim-1', /* 注 */ 'claim-2',
]);
export const EMPTY = new Set<string>([]);
export const NONE = new Set();
export const NOTES: Readonly<Record<string, { ja: string; en: string }>> = { a: { ja: 'あ', en: 'a' } };
export const KIND = 'k' as const;
export const fn = (r: { id: string }) => HIDDEN.has(r.id);
export const SIZE = HIDDEN.size;
export const ITEMS = [1, 2]
  .map((x) => x * 2);
export const TPL = `a${KIND}`;
export const SPREAD = [...dougu];
export function f() { return 1; }
const notExported = 1;
"""


def test_ts_値だけを読み関数と式は名だけ():
    consts, not_literal, problem = tsread.exports(TS)
    assert problem is None
    assert consts["dougu"][0] == {"id": "a-1", "title": '題 "引用" と \U0001F600', "n": -1000, "f": 1.5,
                                  "ok": True, "no": None, "u": None, "at": "2026-11-07",
                                  "quoted-key": "テンプレート"}
    assert consts["dougu"][1] == {"id": "b-2", "title": "b", "list": [1, 2]}
    assert consts["HIDDEN"] == ["claim-1", "claim-2"]
    assert consts["EMPTY"] == [] and consts["NONE"] == []
    assert consts["NOTES"] == {"a": {"ja": "あ", "en": "a"}}
    assert consts["KIND"] == "k"
    assert not_literal == ["fn", "SIZE", "ITEMS", "TPL", "SPREAD"]
    assert "notExported" not in consts and "f" not in consts


@pytest.mark.parametrize("src", [
    "export const A = { a: 1, a: 2 };",          # 重なる鍵
    "export const A = { a };",                   # 短い書き方
    "export const A = { [k]: 1 };",              # 計算した鍵
    "export const A = new Map([[1, 2]]);",
    "export const A = 'a' + 'b';",
    "export const A = 'abc",                     # 閉じない字
    "export const A = [1, 2",                    # 閉じない並び
    "export const A = 1n;",
    "export const A = '\\1';",
    "export const A = " + "[" * 70 + "]" * 70 + ";",
    "export const A = obj.b;",
    "export const A: number;",
])
def test_ts_読まない値(src):
    consts, not_literal, problem = tsread.exports(src)
    assert problem is None and consts == {} and not_literal == ["A"]


def test_ts_同じ名の定数は枚ごと読まない():
    assert tsread.exports("export const A = 1;\nexport const A = 2;\n")[2] == "const_duplicate"


def test_ts_種類として読む_並びと枚(tmp_path):
    kinds = [kind("dougu", "src/lib/dougu-list.ts", "ts_const", records="rows", rows_at=["dougu"], id={"field": "id"}),
             kind("rules", "src/lib/rules.ts", "ts_const", id={"name": True},
                  pointers=[{"field": "HIDDEN", "meaning": "limits", "to": "dougu"}])]
    root = make_ledger(tmp_path / "l", kinds, {
        "src/lib/dougu-list.ts": TS,
        "src/lib/rules.ts": "export const HIDDEN = new Set(['a-1', 'zz']);\nexport const f = () => 1;\n"})
    vessel = V.open_vessel(str(root))
    rows = [r for r in vessel.all_records if r.kind == "dougu"]
    assert [r.id for r in rows] == ["a-1", "b-2"] and all(r.problems == [] for r in rows)
    assert rows[0].version != rows[1].version
    rule = vessel.get("rules:rules")
    assert rule.values == {"HIDDEN": ["a-1", "zz"], "_not_literal": ["f"]}
    targets, _blank, _unrouted = V.pointer_refs(rule, kinds[1]["pointers"][0])
    assert sorted(targets) == [("dougu", "a-1"), ("dougu", "zz")]


def test_ts_並びの頭の定数が値でない_無い(tmp_path):
    kinds = [kind("a", "a.ts", "ts_const", records="rows", rows_at=["ITEMS"], id={"field": "id"}),
             kind("b", "b.ts", "ts_const", records="rows", rows_at=["MISSING"], id={"field": "id"})]
    root = make_ledger(tmp_path / "l", kinds, {"a.ts": "export const ITEMS = make();\n", "b.ts": "export const X = 1;\n"})
    vessel = V.open_vessel(str(root))
    assert [r.problems for r in vessel.all_records if r.kind == "a"] == [["ts_not_literal"]]
    assert [r.problems for r in vessel.all_records if r.kind == "b"] == [["ts_const_missing"]]


def test_ts_並びには頭の段が要る():
    parse_fails(kind("k", "a.ts", "ts_const", records="rows", id={"field": "id"}), "rows_at")


def test_ts_の種類は書く口で欄ごとに伏せられない():
    """新しい形式は、出口の欄ごとの設定（出さない欄・差し込み・欄を伏せる）を受けない（元の形のまま書き換える道が無い）。"""
    from thth.register import route as R
    k = V.parse_kind(kind("k", "a.ts", "ts_const", id={"name": True}), ".thth/kinds/k.json")
    with pytest.raises(V.DeclarationError):
        R._kind_conf({"private_fields": ["X"]}, k, "record", "outlets:o:k")
    k2 = V.parse_kind(kind("k", "o.txt", "lines", columns=["a"], id={"field": "a"}), ".thth/kinds/k.json")
    with pytest.raises(V.DeclarationError):
        R._kind_conf({"private_fields": ["a"]}, k2, "record", "outlets:o:k")


# ================================================================== 段 9 の検収の直し（H1〜H4・L3・L4）

def test_検収H1_記録ごとの出口はlinesとts_constを断る():
    from thth.register import route as R
    kinds = {"k": V.parse_kind(kind("k", "o.txt", "lines", columns=["a"], id={"field": "a"}), ".thth/kinds/k.json"),
             "t": V.parse_kind(kind("t", "a.ts", "ts_const", id={"name": True}), ".thth/kinds/t.json")}
    for name in kinds:
        with pytest.raises(V.DeclarationError) as info:
            R.parse_outlets({"schema": R.OUTLETS_SCHEMA, "outlets": {"o": {"kinds": [name]}}}, kinds)
        assert info.value.word == "outlets:o:format"
    # 表（csv）の種類は前のまま受ける
    kinds["c"] = V.parse_kind(kind("c", "o.csv", "csv", id={"field": "a"}), ".thth/kinds/c.json")
    R.parse_outlets({"schema": R.OUTLETS_SCHEMA, "outlets": {"o": {"kinds": ["c"]}}}, kinds)


def test_検収H2_読みは枚の長さにほぼ比例する():
    import time
    src = "".join(f"export const A{i}: {{\n" for i in range(8000))     # 閉じない型の注釈 8,000 本（約 175KB）
    t0 = time.monotonic()
    consts, not_literal, problem = tsread.exports(src)
    assert time.monotonic() - t0 < 5 and consts == {} and len(not_literal) == 8000
    src = "".join(f"export const A{i} = [\n" for i in range(8000))     # 閉じない並び
    t0 = time.monotonic()
    assert len(tsread.exports(src)[1]) == 8000 and time.monotonic() - t0 < 5
    src = 'export const A = "' + "\\x41" * 300_000 + '" as const;\n'
    t0 = time.monotonic()
    assert tsread.exports(src)[0] == {"A": "A" * 300_000} and time.monotonic() - t0 < 5


@pytest.mark.parametrize("src", [
    "/*\nexport const A = 1;\n*/\nexport const B = 2;\n",
    "// x\n/* a\nexport const A = 1; */ export const C = 0;\nexport const B = 2;\n",
    "const s = `\nexport const A = ${x}\n`;\nexport const B = 2;\n",
    "const s = `${`\nexport const A = 1;\n`}`;\nexport const B = 2;\n",
    "const r = /a'/;\nexport const B = 2;\n",
    "const q = \"x\";\nexport const B = 2;\n",
])
def test_検収H3_注や字の中のexport_constは読まない(src):
    consts, not_literal, problem = tsread.exports(src)
    assert (consts, not_literal, problem) == ({"B": 2}, [], None)


@pytest.mark.parametrize("src", ["export const A = {1.0: 'a', 1: 'b'};", "export const A = {1e3: 'y'};",
                                 "export const A = {0x1: 'y'};", "export const A = {01: 'y'};"])
def test_検収H4_JSの鍵の名と字が変わる数の鍵は読まない(src):
    assert tsread.exports(src)[:2] == ({}, ["A"])


def test_検収H4_整数の鍵は字の鍵():
    assert tsread.exports("export const A = {0: 'a', 12: 'b'};")[0] == {"A": {"0": "a", "12": "b"}}


@pytest.mark.parametrize("src", ["export const A = 1_;", "export const A = 1__0;", "export const A = 01;",
                                 "export const A = '\\08';"])
def test_検収L3_JSで誤りの書き方は読まない(src):
    assert tsread.exports(src)[:2] == ({}, ["A"])


def test_検収L4_欄の名_not_literalの定数は枚ごと読まない(tmp_path):
    root = make_ledger(tmp_path / "l", [kind("t", "a.ts", "ts_const", id={"name": True})],
                       {"a.ts": "export const _not_literal = ['x'];\n"})
    recs = [r for r in V.open_vessel(str(root)).all_records if r.kind == "t"]
    assert [r.problems for r in recs] == [["ts:name_reserved"]] and recs[0].values is None


def test_検収_inspectsは表だけ():
    with pytest.raises(V.DeclarationError):
        V.parse_kind(kind("k", "o.txt", "lines", columns=["a", "b"], id={"field": "a"},
                          pointers=[{"field": "b", "meaning": "about", "to": "k"}],
                          inspects={"follow": ["b"]}), ".thth/kinds/k.json")
