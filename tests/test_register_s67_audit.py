"""台帳の器 段 6・7 の検収（Fable 11/14）の重い穴と、決めた直しの試験。

決めた直しは `docs/指示_台帳の器_段6と段7_書く口と出口の残り_2026-11-14.md` の末尾の節「検収（Fable 11/14）の重い穴と、
決めた直し」。検収の答えと再現の台本は `thth-work/audit-s67/`（`s6_*.py`・`s7_*.py`）。ここには、その再現を写した物を
置く（直す前は通ってしまう＝この試験が落ちるのを見てから直した）。

段 6 の 6-5（止まった束の半分の姿）と、段 7 の 7-1（配る封の当て直せる指紋）は別の書き手の持ち分なので、ここに無い。
"""
from __future__ import annotations

import csv
import io
import json
import pathlib

import pytest

from tests import test_register_exit as X
from tests import test_register_gate as G
from tests import test_register_write as W
from thth import cli
from thth.register import adopt as A
from thth.register import keep as K
from thth.register import reader_check as RC
from thth.register import route as R
from thth.register import seal as S
from thth.register import vessel as V
from thth.register.errors import RegisterRefused

AT = W.AT


@pytest.fixture
def env(tmp_path, monkeypatch):
    for key, sub in (("THTH_ROOT", "root"), ("HOME", "home"), ("XDG_CONFIG_HOME", "config")):
        (tmp_path / sub).mkdir()
        monkeypatch.setenv(key, str(tmp_path / sub))
    return tmp_path


@pytest.fixture
def keyed(env, monkeypatch):
    monkeypatch.delenv(S.ENV_PUBLIC, raising=False)
    try:
        S.openssl()
    except RegisterRefused:
        pytest.skip("OpenSSL 3 か 4 が決まった場所に無い")
    S.init_key()
    return env


def csv_rows(data: bytes, delimiter: str) -> list:
    return list(csv.reader(io.StringIO(data.decode("utf-8"), newline=""), delimiter=delimiter))


# ------------------------------------------------------------------ 6-1 書く口: 途中に引用符のある行

QUOTED = [
    ("comma", b'id,title,note\nt-1,A,5" pipe\nt-2,B,y\nt-3,C,z\n', None),
    ("tab", b'id\ttitle\tnote\r\nt-1\tA\t5" pipe\r\nt-2\tB\ty\r\nt-3\tC\tz\r\n', "\t"),
]


@pytest.mark.parametrize("label,data,delimiter", QUOTED)
def test_6_1_途中に引用符が1つある行をrow_setしても後ろのレコードを消さない(env, label, data, delimiter):
    """検収 段 6 重い 1（`s6_quote.py`）。器の分け方が直れば正しく書き、直る前は書かずに断る。どちらでも、csv の部品で
    読んだ後ろのレコード（t-2・t-3）は 1 バイトも変わらず、レコードの数も同じ。"""
    led = W.ledger(env, {"data/t.csv": data}, {"t": W.kind_json("t", "data/t.csv", delimiter=delimiter)})
    d = delimiter or ","
    before = csv_rows(data, d)
    try:
        A.row_set(str(led), "t:t-1", {"note": "N"}, by="a", at=AT)
    except RegisterRefused as exc:
        assert exc.code == "register_write_unsafe" and exc.detail == "row_split_mismatch", exc
        assert (led / "data/t.csv").read_bytes() == data
        return
    after = csv_rows((led / "data/t.csv").read_bytes(), d)
    assert len(after) == len(before)
    assert after[2:] == before[2:] and after[1][2] == "N"


@pytest.mark.parametrize("label,data,delimiter", QUOTED)
def test_6_1_途中に引用符が1つある表にrow_addしても開いた引用の中へ行を足さない(env, label, data, delimiter):
    led = W.ledger(env, {"data/t.csv": data}, {"t": W.kind_json("t", "data/t.csv", delimiter=delimiter)})
    d = delimiter or ","
    before = csv_rows(data, d)
    try:
        A.row_add(str(led), "t", {"id": "t-4", "title": "D", "note": "w"}, by="a", at=AT)
    except RegisterRefused as exc:
        assert exc.code == "register_write_unsafe" and exc.detail == "row_split_mismatch", exc
        assert (led / "data/t.csv").read_bytes() == data
        return
    after = csv_rows((led / "data/t.csv").read_bytes(), d)
    assert after[:len(before)] == before and after[-1] == ["t-4", "D", "w"] and len(after) == len(before) + 1


def _old_split(text: str, delimiter=None) -> list:
    """段 6 の検収の時点の器の分け方（引用符を見るたびに中・外を入れ替える。csv の部品と食い違う）。"""
    rows, start, quoted, i, n = [], 0, False, 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            quoted = not quoted
        elif not quoted and ch in "\r\n":
            end = i + 2 if ch == "\r" and i + 1 < n and text[i + 1] == "\n" else i + 1
            rows.append(text[start:end])
            start = i = end
            continue
        i += 1
    if start < n:
        rows.append(text[start:])
    return rows


@pytest.mark.parametrize("label,data,delimiter", QUOTED)
def test_6_1_器の分け方とcsvの部品の数え方が食い違う表には書く口が自分で数えて書かない(env, monkeypatch, label, data,
                                                                         delimiter):
    """読みの側の直し（`vessel.py`）が届く前の分け方でも、書く口は同じバイトを csv の部品で読んだ行の数と比べて断る。"""
    monkeypatch.setattr(V, "split_csv_rows", _old_split)
    led = W.ledger(env, {"data/t.csv": data}, {"t": W.kind_json("t", "data/t.csv", delimiter=delimiter)})
    for go in (lambda: A.row_set(str(led), "t:t-1", {"note": "N"}, by="a", at=AT),
               lambda: A.row_add(str(led), "t", {"id": "t-9", "title": "D", "note": "w"}, by="a", at=AT)):
        with pytest.raises(RegisterRefused) as exc:
            go()
        assert exc.value.code == "register_write_unsafe" and exc.value.detail == "row_split_mismatch"
    assert (led / "data/t.csv").read_bytes() == data and K.pending(str(led)) is None


def test_6_1_食い違いの無い表は今までどおり1欄直し1行足せる(env):
    data = b'id,title,note\nt-1,A,"a ""q"" b"\nt-2,B,"multi\nline"\nt-3,C,z\n'
    led = W.ledger(env, {"data/t.csv": data}, {"t": W.kind_json("t", "data/t.csv")})
    A.row_set(str(led), "t:t-3", {"note": "N"}, by="a", at=AT)
    A.row_add(str(led), "t", {"id": "t-4", "title": "D", "note": "w"}, by="a", at=AT)
    after = csv_rows((led / "data/t.csv").read_bytes(), ",")
    assert after == csv_rows(data, ",")[:3] + [["t-3", "C", "N"], ["t-4", "D", "w"]]


# ------------------------------------------------------------------ 6-2 集めた者と検めた者

@pytest.mark.parametrize("by,checked_by", [
    ("masaru", "Masaru"), ("masaru", "MASARU"), ("masaru", "ｍａｓａｒｕ"), ("masaru", "masaru​"),
    ("masaru", "ma saru"), ("masaru", "masaru."), ("Claude Opus", "claude opus"), ("が", "が"),
    ("masaru", "masaru　"), ("青木", "青 木"),
])
def test_6_2_集めた者と検めた者は書き方を変えても同じ者として断る(env, capsys, by, checked_by):
    led = W.claim_ledger(env)
    before = (led / W.CLAIMS).read_bytes()
    rc, v, _ = W.jrun(capsys, "register", "merge", led, W.bundle_of(env, W.claims_fix()), "--by", by,
                      "--checked-by", checked_by)
    assert rc != 0 and v["code"] == "register_merge_refused" and v["detail"] == "same_person", v
    assert (led / W.CLAIMS).read_bytes() == before


def test_6_2_別の者は通り跡には書かれたとおりの名前が残る(env, capsys):
    led = W.claim_ledger(env)
    rc, v, err = W.jrun(capsys, "register", "merge", led, W.bundle_of(env, W.claims_fix()), "--by", "Masaru",
                        "--checked-by", "Ｃlaude")
    assert rc == 0, err
    assert v["checked_by"] == "Ｃlaude"
    from thth.register import vessel_cli
    entered = vessel_cli.links_of(str(led), "claim:c-1")["entered"]
    assert entered["checked_by"] == "Ｃlaude" and entered["checked"] is True


def test_6_2_比べられない名前は断る(env, capsys):
    led = W.claim_ledger(env)
    rc, v, _ = W.jrun(capsys, "register", "merge", led, W.bundle_of(env, W.claims_fix()), "--by", "a",
                      "--checked-by", "​.")
    assert rc != 0 and v["code"] == "register_args_invalid" and v["detail"] == "checked_by"


# ------------------------------------------------------------------ 6-3 検収の記録として読めない行

@pytest.mark.parametrize("label,row,reason", [
    ("幅が 1 つ多い", "v-1,c-1,茶はとても苦い,reject,extra", "source_problem"),
    ("幅が足りない", "v-1,c-1,reject", "source_problem"),
    ("指し先が無い", "v-1,c-9,茶はとても苦い,reject", "target_missing"),
    ("指し先が空", "v-1,,茶はとても苦い,reject", "target_missing"),
    ("指し先の前後に空白", "v-1, c-1,茶はとても苦い,reject", None),
    ("番号が形に合わない", "v 1/,c-1,茶はとても苦い,reject", "id_form"),
    ("番号が空", ",c-1,茶はとても苦い,reject", "id_form"),
])
def test_6_3_束の検収の表に読めない行が1行でもあれば束ごと断る(env, capsys, label, row, reason):
    led = W.claim_ledger(env)
    before = (led / W.CLAIMS).read_bytes()
    files = dict(W.claims_fix(), **{W.VERIFY: f"id,claim_id,text,verdict\nv-0,c-2,豆は甘い,ok\n{row}\n"})
    rc, v, _ = W.jrun(capsys, "register", "merge", led, W.bundle_of(env, files), "--by", "a", "--checked-by", "b")
    assert rc != 0 and v["code"] == "register_merge_refused" and v["detail"] == "inspection_unreadable", v
    assert len(v["rows"]) == 1 and v["rows"][0]["line"] == 3 and v["rows"][0]["path"] == W.VERIFY
    if reason is not None:
        assert reason in v["rows"][0]["reason"]
    assert (led / W.CLAIMS).read_bytes() == before and K.pending(str(led)) is None


@pytest.mark.parametrize("word", ["Reject", "REJECT", "reject ", " reject", "ＲＥＪＥＣＴ", "ng", "fine"])
def test_6_3_語が憲法に無い検収の行は束ごと断る(env, capsys, word):
    led = W.claim_ledger(env)
    before = (led / W.CLAIMS).read_bytes()
    files = dict(W.claims_fix(), **{W.VERIFY: f"id,claim_id,text,verdict\nv-1,c-1,茶はとても苦い,{word}\n"})
    rc, v, _ = W.jrun(capsys, "register", "merge", led, W.bundle_of(env, files), "--by", "a", "--checked-by", "b")
    assert rc != 0 and v["code"] == "register_merge_refused" and v["detail"] == "verdict_unknown", v
    assert v["rows"][0]["record"] == "verify:v-1" and v["rows"][0]["line"] == 2
    assert (led / W.CLAIMS).read_bytes() == before


def test_6_3_読める行だけの束は今までどおり入る(env, capsys):
    led = W.claim_ledger(env)
    files = dict(W.claims_fix(), **{W.VERIFY: "id,claim_id,text,verdict\nv-1,c-1,茶はとても苦い,ok\n\n"})
    rc, v, err = W.jrun(capsys, "register", "merge", led, W.bundle_of(env, files), "--by", "a", "--checked-by", "b")
    assert rc == 0, err
    assert [b["record"] for b in v["bound"]["judgments"]] == ["claim:c-1"]


# ------------------------------------------------------------------ 6-4 器が書かない道

@pytest.mark.parametrize("rel", [".THTH/judgments.jsonl", ".Thth/kinds/extra.json", ".thth/outlets.json",
                                 "data/.THTH/x.csv", ".GIT/config", "data/.Git/hooks/x"])
def test_6_4_束の器の置き場の道は大文字小文字違いもどの段でも断り台帳を変えない(env, capsys, rel):
    led = W.claim_ledger(env)
    J_before = (led / ".thth").exists() and sorted(p.name for p in (led / ".thth").iterdir())
    before = {p: p.read_bytes() for p in led.rglob("*") if p.is_file()}
    files = dict(W.claims_fix(), **{rel: "{}\n"})
    rc, v, _ = W.jrun(capsys, "register", "merge", led, W.bundle_of(env, files), "--by", "a", "--checked-by", "b")
    if any(K.fold_name(p) == ".git" for p in rel.split("/")):
        # 束の中の .git のフォルダ（大文字小文字違いも）は束の作り手の物として辿らない（台帳へは置かない）
        assert not any(K.fold_name(p.name) == ".git" for p in led.rglob("*"))
        return
    assert rc != 0 and v["code"] == "register_merge_refused" and v["detail"] == "reserved_path", v
    assert {p: p.read_bytes() for p in led.rglob("*") if p.is_file()} == before
    assert sorted(p.name for p in (led / ".thth").iterdir()) == J_before


@pytest.mark.parametrize("rel,hit", [(".thth", True), (".THTH/x", True), ("a/.Thth/b", True), (".git", True),
                                     ("x/.GIT/config", True), (".thth-old/x", False), ("thth/x", False),
                                     ("a/.gitignore", False), ("data/.thth.csv", False)])
def test_6_4_器が書かない道の比べはNFCと小文字にそろえる(rel, hit):
    assert K.reserved_path(rel) is hit
    if hit:
        with pytest.raises(RegisterRefused):
            K.safe_rel(rel)


def test_6_4_取り込みの元の範囲に大文字違いの器の置き場があれば断る(env):
    src = env / "src"
    W.write(src / "data/a.csv", "id\na-1\n")
    W.write(src / "data/.THTH/judgments.jsonl", "{}\n")
    with pytest.raises(RegisterRefused) as exc:
        A.adopt(str(src), str(env / "led"), plan={"schema": A.PLAN_SCHEMA, "include": ["data/**"], "exclude": []}, by="a", at=AT)
    assert exc.value.code == "register_adopt_unusable" and exc.value.detail == "thth_in_source"


def test_6_4_材料の書き出し先も器が書かない道を言えない(env):
    for dest in (".thth", "site/.THTH", ".Git/x"):
        with pytest.raises(V.DeclarationError):
            R._dest(dest, "dest")
    with pytest.raises(RegisterRefused):
        R.check_to(str(env / "led"), str(env / "repo/.GIT/site"))
    with pytest.raises(RegisterRefused):
        R.write_files(str(env / "out"), {".THTH/judgments.jsonl": b"{}\n"})


# ------------------------------------------------------------------ 7-2 差し込みの先は出し口の材料に載る記録だけ

OP_KIND = {"schema": "thth.kind/1", "kind": "operator", "place": "ops/operators.csv", "format": "csv",
           "id": {"field": "id"}, "questions": [{"name": "may_publish", "outlet": "site", "depends": ["name"]}]}


def _site(env, *, s3_body, site_kinds=None, fact=True, extra_kinds=None, files=None):
    decl = X.outlets()
    if site_kinds is not None:
        decl["outlets"]["site"]["kinds"] = site_kinds
    if not fact:
        decl["outlets"]["site"].pop("fact")
    led = X._ledger(env, decl, shops=X.SHOPS.replace("uses {{shop:s2.name}} here", s3_body))
    for name, body in (extra_kinds or {}).items():
        (led / f".thth/kinds/{name}.json").write_text(json.dumps(body, ensure_ascii=False))
    for rel, data in (files or {}).items():
        W.write(led / rel, data)
    return led


def _s3_out(led, env):
    plan = R.accept_plan(str(led), "site", str(env / "site"), X.AT)
    return [e for e in plan["result"]["excluded"] if e["record"] == "shop:s3"], plan


def test_7_2_出し口の外の種類からは差し込めない(keyed):
    env = keyed
    kinds = {k: v for k, v in X.SITE_KINDS.items() if k != "item"}
    led = _site(env, s3_body="x {{item:i1.secret}}", site_kinds=kinds)
    X.judge_all(led)
    out, _ = _s3_out(led, env)
    assert out and out[0]["why"] == "insert_unusable" and out[0]["inserts"][0]["why"] == "outside_outlet"
    X.accept(led, "site", env / "site")
    assert b"SECRET-ITEM" not in X.all_bytes(env / "site", led / ".thth/seals")


@pytest.mark.parametrize("fact", [True, False])
@pytest.mark.parametrize("in_kinds", [True, False])
def test_7_2_運営者の種類はどの出し口でも差し込めない(keyed, fact, in_kinds):
    env = keyed
    kinds = dict(X.SITE_KINDS, **({"operator": {}} if in_kinds else {}))
    led = _site(env, s3_body="x {{operator:o1.phone}}", site_kinds=kinds, fact=fact,
                extra_kinds={"operator": OP_KIND}, files={"ops/operators.csv": "id,name,phone\no1,M,PHONE-0901\n"})
    X.judge_all(led)
    R.judge(str(led), "operator:o1", "may_publish@site", by="m", at=X.AT, conclusion="good")
    out, _ = _s3_out(led, env)
    assert out and out[0]["why"] == "insert_unusable" and out[0]["inserts"][0]["why"] == "operator", out
    X.accept(led, "site", env / "site")
    assert b"x PHONE-0901" not in X.all_bytes(env / "site", led / ".thth/seals")


def test_7_2_差し込みが外れた記録の欄を別の記録が差し込めない(keyed):
    """s1 が s2 の名前を、s2 が止めた s3 の欄を差し込む。s2 が外れるので、s1 も外れる（外れた記録の値を出さない）。"""
    env = keyed
    shops = ("id,name,price,secret,body,review_by,status\n"
             "s1,Alpha,100,SA,x {{shop:s2.name}},,open\n"
             "s2,Beta,200,SB,y {{shop:s3.body}},,open\n"
             "s3,Gamma,300,SC,STOPPED-BODY,,open\n")
    led = X._ledger(env, shops=shops)
    X.judge_all(led)
    R.stop(str(led), "shop:s3", by="m", at=X.AT)
    plan = R.accept_plan(str(led), "site", str(env / "site"), X.AT)
    whys = {e["record"]: e for e in plan["result"]["excluded"]}
    assert whys["shop:s2"]["why"] == "insert_unusable"
    assert whys["shop:s1"]["why"] == "insert_unusable"
    assert whys["shop:s1"]["inserts"] == [{"insert": "shop:s2", "why": "not_in_material"}]
    X.accept(led, "site", env / "site")
    got = (env / "site/data/shops.csv").read_bytes()
    assert b"STOPPED-BODY" not in got and b"s1," not in got and b"s2," not in got and b"x Beta" not in got
    assert R.verify(str(led), "site", str(env / "site"), X.NOW, no_previous=True)["ok"]


@pytest.mark.parametrize("body,why", [
    ("x {{shop:s2 .name}}", "malformed"), ("x {{ shop:s2.name }}", "malformed"), ("x {{Shop:s2.name}}", "malformed"),
    ("x {{shop:s2.name", "malformed"),
])
def test_中_形の崩れた差し込みが残る欄は記録ごと外す(keyed, body, why):
    env = keyed
    led = _site(env, s3_body=body)
    X.judge_all(led)
    out, _ = _s3_out(led, env)
    assert out and out[0]["why"] == "insert_unusable" and out[0]["inserts"][0]["why"] == why, out


# ------------------------------------------------------------------ 7-3 読者に見える欄の点検は差し込んだあとの値でも

def _rc_ledger(env, words=("台帳", "統合担当"), body="統合担当の台帳より（根拠 obs-0021）"):
    kk = dict(X.KINDS, obs={"schema": "thth.kind/1", "kind": "obs", "place": "data/obs.csv", "format": "csv",
                            "id": {"field": "id"}})
    sk = {"shop": {"private_fields": ["secret"], "inserts": ["body"], "reader_fields": ["body", "name"]},
          "note": {"reader_fields": ["title"]}, "item": {}}
    decl = X.outlets(site={"audience": "reader", "kinds": sk, "fact": [{"question": "may_publish@site"}],
                           "dest": "site"}, cards=None)
    decl["internal_words"] = list(words)
    led = env / "led"
    for name, b in kk.items():
        W.write(led / f".thth/kinds/{name}.json", json.dumps(b, ensure_ascii=False))
    W.write(led / ".thth/outlets.json", json.dumps(decl, ensure_ascii=False))
    W.write(led / "data/shops.csv", "id,name,price,secret,body,review_by,status\n"
                                    "s1,Alpha,100,x,紹介 {{note:n1.body}},,open\n")
    W.write(led / "notes/n1.yaml", f"title: T\nbody: {body}\n")
    W.write(led / "items/i1.json", '{"name": "W"}\n')
    W.write(led / "data/obs.csv", "id,memo\nobs-0021,m\n")
    return led


def test_7_3_差し込みで材料に入る内部の語と番号を点検が拾う(keyed):
    env = keyed
    led = _rc_ledger(env)
    X.judge_all(led, refs=["shop:s1", "note:n1", "item:i1"])
    plan = R.accept_plan(str(led), "site", str(env / "site"), X.AT)
    assert plan["result"]["inserted"]["shop:s1"]["body"].startswith("紹介 統合担当")
    for res in (RC.check(str(led)), RC.check_material(str(led), "site", plan["result"])):
        got = {(r["record"], r["reason"]) for c in res for r in c["records"]}
        assert ("shop:s1", "internal_word") in got and ("shop:s1", "hidden_id") in got, res
        assert RC.red(res)


def test_7_3_台帳の値でも見る_差し込みが無くても今までどおり(keyed):
    env = keyed
    led = _rc_ledger(env, body="plain")
    shops = led / "data/shops.csv"
    shops.write_text(shops.read_text().replace("紹介 {{note:n1.body}}", "台帳の紹介"), encoding="utf-8")
    got = {(r["record"], r["reason"]) for c in RC.check(str(led)) for r in c["records"]}
    assert got == {("shop:s1", "internal_word")}


@pytest.mark.parametrize("text", ["ＳＵＰＰＯＲＴＥＤ", "Supported", "sup​ported", "ＯＢＳ-００２１"])
def test_中_読者に見える欄の点検はNFKCと小文字にそろえゼロ幅の字を除いて比べる(keyed, text):
    env = keyed
    led = _rc_ledger(env, words=("supported",), body="plain")
    shops = led / "data/shops.csv"
    shops.write_text(shops.read_text().replace("紹介 {{note:n1.body}}", f"紹介 {text}"), encoding="utf-8")
    assert RC.red(RC.check(str(led))), text


# ------------------------------------------------------------------ 中から直す物（出し口の宣言）

@pytest.mark.parametrize("tools_dest,site_dest", [("site/tools", "site"), ("site", "site/tools"), ("Site", "site"),
                                                  ("./a/../site", "site"), ("site/./x", "SITE")])
def test_中_書き出し先の重なりは道をそろえ入れ子も重なりとして断る(keyed, tools_dest, site_dest):
    env = keyed
    decl = X.outlets()
    decl["outlets"]["tools"]["dest"] = tools_dest
    decl["outlets"]["site"]["dest"] = site_dest
    led = X._ledger(env, decl)
    with pytest.raises(RegisterRefused) as exc:
        R.load_outlets(str(led), V.open_vessel(str(led)))
    assert exc.value.code == "register_outlet_unknown" and "dest_shared" in str(exc.value)


@pytest.mark.parametrize("name,header", [("Secret", None), ("SECRET", None), ("nothing", None),
                                         ("secret", " secret"), ("secret", "Secret")])
def test_中_出さない欄の名前がその種類の記録に無ければ黙って出さずに断る(keyed, name, header):
    env = keyed
    kinds = dict(X.SITE_KINDS, shop=dict(X.SITE_KINDS["shop"], private_fields=[name]))
    decl = X.outlets()
    decl["outlets"]["site"]["kinds"] = kinds
    shops = X.SHOPS if header is None else X.SHOPS.replace(",secret,", f",{header},", 1)
    led = X._ledger(env, decl, shops=shops)
    X.judge_all(led)
    with pytest.raises(RegisterRefused) as exc:
        R.accept_plan(str(led), "site", str(env / "site"), X.AT)
    assert exc.value.code == "register_outlet_unknown" and "private_missing" in str(exc.value)
    assert not (env / "site").exists()


# ------------------------------------------------------------------ 線引き: published は --previous か --no-previous

def test_線引き_publishedはpreviousかno_previousのどちらかを言わせる(keyed, capsys):
    env = keyed
    led = G.seeded(env)
    listed = G.lst(env, "/", "/a/", "/new/", "/日本/", name="same.txt")
    before = G.tree_sha(led)
    rc, v, _ = W.jrun(capsys, "register", "published", led, "--outlet", "site", "--list", listed, "--now", G.NOW)
    assert rc != 0 and v["code"] == "register_args_invalid" and v["detail"] == "previous"
    assert G.tree_sha(led) == before
    rc, v, err = W.jrun(capsys, "register", "published", led, "--outlet", "site", "--list", listed, "--now", G.NOW,
                        "--no-previous")
    assert rc == 0, err
    assert "previous_skipped" in {w["word"] for w in v["warnings"]}
    head = env / "pub/head.json"
    assert P_published(led, listed, publish=head)["ok"]
    rc, v, err = W.jrun(capsys, "register", "published", led, "--outlet", "site", "--list", listed, "--now", G.NOW,
                        "--previous", head)
    assert rc == 0, err
    with pytest.raises(SystemExit):
        cli.main(["register", "published", str(led), "--outlet", "site", "--list", str(listed), "--previous",
                  str(head), "--no-previous"])
    capsys.readouterr()


def P_published(led, listed, **kw):
    from thth.register import published as P
    return P.published(str(led), "site", str(listed), G.NOW, **kw)


# ------------------------------------------------------------------ 6-5 のつなぎ: 止まった入れ替えの間は出口も差も断る

def test_6_5_止まった入れ替えの間はexportとacceptとverifyとdiffの今が断る(keyed, monkeypatch):
    """器の読み（`open_vessel`）の断りは段 4 の書き手の持ち分。ここでは、材料を出す道と差の「今」が、そのまま断る
    ことを 1 つずつ見る（前でも後でもない半分の姿を材料にしない）。"""
    env = keyed
    led = X._ledger(env)
    site = env / "site"
    X.judge_all(led)
    X.accept(led, "site", site)

    def broken(tmp, target):
        raise RuntimeError("落ちた")
    monkeypatch.setattr(K, "_replace", broken)
    with pytest.raises(RuntimeError):
        A.row_set(str(led), "shop:s2", {"price": "201"}, by="a", at=AT)
    monkeypatch.undo()
    assert K.pending(str(led)) is not None
    for go in (lambda: R.export(str(led), "site", str(site), X.AT, check=True),
               lambda: R.accept_plan(str(led), "site", str(site), X.AT),
               lambda: R.verify(str(led), "site", str(site), X.NOW, no_previous=True),
               lambda: A.diff(str(led), "first..now")):
        with pytest.raises(RegisterRefused) as exc:
            go()
        assert exc.value.code == "register_batch_pending"


@pytest.mark.parametrize("text,hit", [("脚注＊＊", False), ("脚注**", True)])
def test_中_記号だけの内部の語は字のまま比べ全角の脚注の印を拾わない(keyed, text, hit):
    """染めと化学の実物で、`**`（書きかけの太字の印）が NFKC で全角の `＊＊`（脚注の印）に当たった。記号だけの語は
    そろえずに比べる（字か数を含む語だけ NFKC・小文字にそろえる）。"""
    env = keyed
    led = _rc_ledger(env, words=("**",), body="plain")
    shops = led / "data/shops.csv"
    shops.write_text(shops.read_text().replace("紹介 {{note:n1.body}}", text), encoding="utf-8")
    assert bool(RC.red(RC.check(str(led)))) is hit
