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


def test_監査520の2_記録ごとの出口はcsvでない並びの種類を断る():
    """監査 5.20.0 の 2（宿題 H1）: JSON・YAML の並びの種類を記録ごとの出口に入れると、残った行が 1 つでもあれば枚を
    丸ごと書くので、止めた記録の中身が材料に出た。並びを切り出して書き直す道が無いので、宣言で断る（csv だけ受ける）。"""
    from thth.register import route as R
    kinds = {"j": V.parse_kind(kind("j", "items.json", "json", records="rows", id={"field": "id"}), ".thth/kinds/j.json"),
             "y": V.parse_kind(kind("y", "items.yaml", "yaml", records="rows", id={"field": "id"}), ".thth/kinds/y.json")}
    for name in kinds:
        for audience in ("reader", "internal"):
            with pytest.raises(V.DeclarationError) as info:
                R.parse_outlets({"schema": R.OUTLETS_SCHEMA, "outlets": {"o": {"kinds": [name], "audience": audience}}},
                                kinds)
            assert info.value.word == "outlets:o:format"
    # 1 枚 1 記録の JSON は前のまま受ける
    kinds["f"] = V.parse_kind(kind("f", "a/*.json", "json"), ".thth/kinds/f.json")
    R.parse_outlets({"schema": R.OUTLETS_SCHEMA, "outlets": {"o": {"kinds": ["f"]}}}, kinds)


def test_宿題L1_型の字の所がNFDの枚は黙って読み落とさず印を付ける(tmp_path):
    """宿題 L1: 置き場の型は NFC だけ受ける。型の字の所（`*` でない所）が NFD の名の枚（mac の古い置き場・ほかの機械
    からの写し）は型に当たらず、印なく読まれなかった。NFC にそろえれば型に当たる枚は、読まずに `name_not_nfc` の印の
    付いた行として出す。この機械の置き場は名をそろえることがあるので、たどった道の並びを渡して確かめる。"""
    k = V.parse_kind(kind("daichou", "ガイド/照合台帳_*.md", "bytes", id={"name": True}), ".thth/kinds/daichou.json")
    nfd = unicodedata.normalize("NFD", "ガイド/照合台帳_1.md")
    assert nfd != "ガイド/照合台帳_1.md"
    got = [(r.id, r.problems) for r in V.read_kind(str(tmp_path), k, {}, [(nfd, str(tmp_path / "x"))])]
    assert got == [(None, ["name_not_nfc"])], got
    from thth.register import form
    assert "NFC" in form.PROBLEM_TEXT["name_not_nfc"]          # `register check` が直し方を 1 行添える
    # 型に当たらない NFD の名は、ほかの枚と同じく数えない
    other = unicodedata.normalize("NFD", "ガイド/ほか.md")
    assert V.read_kind(str(tmp_path), k, {}, [(other, str(tmp_path / "y"))]) == []
    # フォルダの名が NFD でも、降りる前に NFC で比べる（降りなければ印も付けられない）
    assert V._could_hold(V._nfc(unicodedata.normalize("NFD", "ガイド/")), [V._literal("ガイド/照合台帳_*.md")])


def test_宿題L1_型の星の所がNFDの名でも読む(tmp_path):
    root = make_ledger(tmp_path / "l", [kind("daichou", "docs/照合台帳_*.md", "bytes", id={"name": True})], {})
    path = root / unicodedata.normalize("NFD", "docs/照合台帳_ガイド.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# 照合\n", encoding="utf-8")
    vessel = V.open_vessel(str(root))
    assert [(r.id, r.problems) for r in vessel.all_records if r.kind == "daichou"] == [("照合台帳_ガイド", [])]

def test_宿題L1_NFCの名の枚はこれまでどおり読む(tmp_path):
    root = make_ledger(tmp_path / "l", [kind("daichou", "ガイド/照合台帳_*.md", "bytes", id={"name": True})],
                       {"ガイド/照合台帳_1.md": "# 照合\n"})
    vessel = V.open_vessel(str(root))
    assert [(r.id, r.problems) for r in vessel.all_records if r.kind == "daichou"] == [("照合台帳_1", [])]


def test_宿題L2_repeatの番号がずれても判断は別の行へ黙って移らない(tmp_path):
    """宿題 L2: `repeat: count` の番号（`.2`・`.3`）は、前に同じ鍵の行が増える・消えるとずれる（数えるだけで決めるので）。
    ずれた番号に付いていた判断は、問いの依る所（既定は行の全部の欄）の指紋が違えば効かなくなる（stale）。依る所が
    同じ行（同じ鍵・同じ中身）へ移ったときだけ効いたまま（中身が同じなので、言っていることは変わらない）。"""
    from thth.register import judgment as J
    k = owner_kind(questions=[{"name": "read"}])
    text = "/a/x/\t2026-11-01\t一回め\n/a/x/\t2026-11-01\t二回め\n"
    root = make_ledger(tmp_path / "l", [k], {"site/owner-read.txt": text})
    vessel = V.open_vessel(str(root))
    second = next(r for r in vessel.all_records if r.values.get("memo") == "二回め")
    assert second.id.endswith(".2")
    j = J.make(vessel, {"record": second.ref}, "read", by="masaru", at="2026-11-17T10:00:00+09:00", conclusion="good")
    assert J.evaluate(vessel, j) == "effective"
    # 前に同じ鍵の行が 1 つ増えた: 番号 .2 は「一回め」を指すようになる → 判断は効かない
    (root / "site/owner-read.txt").write_text("/a/x/\t2026-11-01\tはじめ\n" + text, encoding="utf-8")
    moved = V.open_vessel(str(root))
    assert next(r for r in moved.all_records if r.id == second.id).values["memo"] == "一回め"
    assert J.evaluate(moved, j) == "stale"
    # 同じ中身の行どうしでずれたときは効いたまま（同じことを言っている）
    dup = "/a/x/\t2026-11-01\t同じ\n"
    (root / "site/owner-read.txt").write_text(dup * 2, encoding="utf-8")
    twice = V.open_vessel(str(root))
    rec = next(r for r in twice.all_records if r.id == second.id)
    j2 = J.make(twice, {"record": rec.ref}, "read", by="masaru", at="2026-11-17T10:00:00+09:00", conclusion="good")
    (root / "site/owner-read.txt").write_text(dup * 3, encoding="utf-8")
    assert J.evaluate(V.open_vessel(str(root)), j2) == "effective"


def test_宿題L5_ts_constの枚は消す口で枚ごと消せない():
    """宿題 L5: 1 枚 = 1 記録の ts_const の種類は、消す口の「枚ごと」に当たっていた。TypeScript の枚はアプリのコードで、
    ほかの定数や関数も入るので、枚ごと消すと壊れる。形の断り（register_erase_invalid の format）にする。"""
    from thth.register import erase as E
    from thth.register.errors import RegisterRefused
    k = V.parse_kind(kind("t", "src/data/*.ts", "ts_const", id={"name": True}), ".thth/kinds/t.json")
    rec = V.Record("t", "a", "src/data/a.ts", "v", {"A": 1}, True)
    with pytest.raises(RegisterRefused) as info:
        E._check_target(k, rec, None, None)
    assert info.value.code == "register_erase_invalid" and info.value.detail == "format"
    # 1 枚 = 1 記録の JSON は前のまま枚ごと消せる
    j = V.parse_kind(kind("j", "a/*.json", "json"), ".thth/kinds/j.json")
    E._check_target(j, V.Record("j", "a", "a/a.json", "v", {"x": 1}, True), None, None)


def test_宿題lines_番号を作る欄の前後の空白には印を付け欄の字は削らない(tmp_path):
    """宿題（lines の空白）: 器は欄の字を削らない（元の字のまま）。染めと化学の gate_publish.py はパスと日の前後の
    空白を削って読むので、番号を作る欄に空白があると、器と道具で指す行が食い違う。その行に `key_space` の印。
    メモの欄（番号を作らない）の空白は印なし（11/17 に実物を数えた: 前後に空白のある欄はメモの 1 つだけ）。"""
    from thth.register import form
    text = "/a/x/ \t2026-11-01\tメモ\n/a/y/\t2026-11-02\t後ろに空白 \n"
    root = make_ledger(tmp_path / "l", [owner_kind()], {"site/owner-read.txt": text})
    recs = [r for r in V.open_vessel(str(root)).all_records if r.kind == "owner"]
    assert [r.problems for r in recs] == [["key_space"], []]
    assert recs[0].values["path"] == "/a/x/ " and recs[1].values["memo"] == "後ろに空白 "
    assert form.PROBLEM_TEXT["key_space"]
