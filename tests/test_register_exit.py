"""台帳の器 段 7「材料を出す道の残り」の試験（指示 `docs/指示_台帳の器_段6と段7_書く口と出口の残り_2026-11-14.md`
段 7 の §7.1・7.2・7.3・7.4・7.8 と「済み」の線の 1・3。§7.5〜7.7 は別の書き手の持ち分）。

見本の台帳は試験ごとに一時の場所へ組む（`_ledger`）。店の表（CSV）・覚えの枚（YAML）・品の枚（JSON）・札（内側の
単位を持つ JSON）の 4 種類。出し口の種類ごとの設定は出し口の宣言（`.thth/outlets.json`）の
`outlets.<出し口>.kinds.<種類>` に持つ（`private_fields`・`inserts`・`hide`）。

重い穴の定め（段 7）: 出さない欄・使えない記録・止めた記録が材料に書かれる／旧い材料が残ったまま緑／
差し込みが使えない値を書く。下の `test_重い穴_*` が、試験の側で全部のファイルを読んで字を探す。

設計 §8.3 の 5「柵を壊して赤を見る」の一覧と、それを見ている試験（段 1〜3 の試験が既に見ている物は名前を挙げ、
やり直さない。「この試験」と書いた物だけをここで足した）:

| 壊し方 | 見ている試験 |
|---|---|
| 1 行を 1 字変える | test_register_route.py::test_つなぐ先_主張を直すと頁から外れる件数を配る前に出す・test_register_vessel.py::test_試験1b_数値を1つ直すとその値の判断だけが効かなくなる |
| 記録を消す | この試験 test_柵_記録を消すと封のあとに狭まったとして赤 |
| 表の 1 行を変える（材料の側） | この試験 test_柵_材料の表の1行を手で変えると赤 |
| 期限を過ぎた日時で判定する | test_register_route.py::test_試験3d_封の日時より後に期限が切れた記録が載っていればCIが赤_こぴちゃvalid_untilレーン待ち・この試験 test_7_4_判断のいつまでは効き見直す日の欄は材料に効かない |
| 旧い材料を残す | test_register_route.py::test_一覧に無い旧い材料があれば止まる_消さない・この試験 test_7_8_* |
| 判断の無い記録を材料に手で足す | この試験 test_柵_判断の無い記録を材料に手で足すと赤 |
| 並びを入れ替える（前の判断が別の値に移らない） | test_register_vessel.py::test_試験1a_並べ替えと行を挟んでも判断は同じ値に付いたまま |
| 文を直して状態を据え置く | test_register_vessel.py::test_試験1f_文を直して状態を据え置くと新しい版は観測だけ・test_register_route_audit.py::test_重い2_dropでも直す前の版へ戻すと赤 |
| 文と状態を同時に書き換える | test_register_vessel.py::test_試験1f_文と状態を同時に書き換えても承認にならない |
| 材料と控えと判断の行を一緒に書き換える（封は古いまま） | test_register_route.py::test_試験3c_材料と控えと判断の行を一緒に書き換えても受け入れていない世代はCIを通らない |
| 「止める」の行を消して古い封に戻す | test_register_route.py::test_試験3e_止めるを足したあと古い封と古い材料に戻してもCIが赤・test_register_route_holes.py::test_穴1_止めるで外して配ったあと止めるの行と材料を戻すと赤 |
| 親の出典だけを変える | test_register_vessel.py::test_試験1c_レーン待ち_関東_出典の読み方_親の出典だけを変えると依る値の判断が効かなくなる・test_register_vessel_holes3.py::test_三7_親の出典だけ変えても鍵で決まらない行の指し先は変わらない |
| 逆: 依る欄でない欄（進み）だけを変えても判断は効いたまま・原物の版は変わる | test_register_vessel_holes.py::test_穴2_単位への判断は依らない欄だけの書き換えでは古くならない・この試験 test_柵_依る欄でない欄だけを変えても判断は効き原物の版は変わる |
"""
from __future__ import annotations

import json
import os
import pathlib

import pytest

from thth.register import judgment as J
from thth.register import route as R
from thth.register import seal as S
from thth.register import vessel as V
from thth.register.errors import RegisterRefused

yaml_needed = pytest.mark.skipif(not V.yamlread.available(), reason="PyYAML が無い機械（pip install 'thth[yaml]'）")

AT = "2027-03-20T10:00:00+09:00"
NOW = "2027-03-21"

SHOPS = (
    "id,name,price,secret,body,review_by,status\n"
    "s1,Alpha,100,SECRET-ALPHA-7f3,Alpha sells {{note:n1.title}},2020-01-01,open\n"
    's2,"Beta, Ltd",200,SECRET-BETA-9k2,plain body,,open\n'
    "s3,Gamma,300,SECRET-GAMMA-1q8,uses {{shop:s2.name}} here,,open\n"
)
NOTE = (
    "title: Note One\n"
    "secret: SECRET-NOTE-x1\n"
    "body: see {{shop:s2.name}}\n"
    "tags:\n"
    "- a\n"
    "- b\n"
    "# 終わりの注\n"
)
ITEM = '{\n  "name": "Widget",\n  "secret": "SECRET-ITEM-z9",\n  "price": 5\n}\n'
CARD = {"rows": [{"seed": "a", "role": "mix", "memo": "SECRET-CARD-m1", "status": "ok"},
                 {"seed": "b", "role": "master", "memo": "SECRET-CARD-m2", "status": "ok"}]}

KINDS = {
    "shop": {"schema": "thth.kind/1", "kind": "shop", "place": "data/shops.csv", "format": "csv",
             "id": {"field": "id"},
             "questions": [{"name": "fresh", "depends": ["name", "price", "secret", "body"]}]},
    "note": {"schema": "thth.kind/1", "kind": "note", "place": "notes/*.yaml", "format": "yaml", "records": "file",
             "id": {"name": True}},
    "item": {"schema": "thth.kind/1", "kind": "item", "place": "items/*.json", "format": "json", "records": "file",
             "id": {"name": True},
             "questions": [{"name": "may_publish", "outlet": "site", "depends": ["name"]}]},
    "card": {"schema": "thth.kind/1", "kind": "card", "place": "cards/*.json", "format": "json", "records": "file",
             "id": {"name": True}, "questions": [{"name": "card_ok", "level": "unit", "state": "status"}],
             "units": {"groups": [], "items": ["rows", "*"], "key": ["seed"]}},
}

SITE_KINDS = {
    "shop": {"private_fields": ["secret"], "inserts": ["body"], "hide": {"field": ["price"]}},
    "note": {"private_fields": ["secret"], "inserts": ["body"]},
    "item": {"private_fields": ["secret"]},
}


def outlets(**over) -> dict:
    site = {"audience": "reader", "kinds": SITE_KINDS, "fact": [{"question": "may_publish@site"}], "dest": "site"}
    data = {"site": site,
            "tools": {"audience": "internal", "kinds": ["shop", "note", "item"], "dest": "tools"},
            "cards": {"audience": "reader", "level": "unit", "kinds": {"card": {"private_fields": ["memo"]}},
                      "fact": [{"question": "card_ok", "conclusion": "good"}], "dest": "cards"}}
    for name, value in over.items():
        if value is None:
            data.pop(name, None)
        else:
            data[name] = value
    return {"schema": "thth.outlets/1", "outlets": data}


@pytest.fixture
def env(tmp_path, monkeypatch):
    for key, sub in (("THTH_ROOT", "root"), ("HOME", "home"), ("XDG_CONFIG_HOME", "config")):
        (tmp_path / sub).mkdir()
        monkeypatch.setenv(key, str(tmp_path / sub))
    monkeypatch.delenv(S.ENV_PUBLIC, raising=False)
    try:
        S.openssl()
    except RegisterRefused:
        pytest.skip("OpenSSL 3 か 4 が決まった場所に無い")
    S.init_key()
    return tmp_path


def _ledger(env, decl=None, *, shops=SHOPS, note=NOTE) -> pathlib.Path:
    led = env / "led"
    (led / ".thth/kinds").mkdir(parents=True)
    for name, body in KINDS.items():
        (led / f".thth/kinds/{name}.json").write_text(json.dumps(body, ensure_ascii=False))
    write_outlets(led, decl or outlets())
    (led / "data").mkdir()
    (led / "data/shops.csv").write_text(shops, encoding="utf-8", newline="")
    (led / "notes").mkdir()
    (led / "notes/n1.yaml").write_text(note, encoding="utf-8", newline="")
    (led / "items").mkdir()
    (led / "items/i1.json").write_text(ITEM, encoding="utf-8", newline="")
    (led / "cards").mkdir()
    (led / "cards/c1.json").write_text(json.dumps(CARD, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return led


def write_outlets(led, decl) -> None:
    (led / ".thth/outlets.json").write_text(json.dumps(decl, ensure_ascii=False, indent=1))


def judge_all(led, question="may_publish@site", refs=None, **kw):
    vessel = V.open_vessel(str(led))
    refs = refs or [r.ref for r in vessel.all_records if r.kind in ("shop", "note", "item")]
    for ref in refs:
        R.judge(str(led), ref, question, by="masaru", at="2027-03-01T00:00:00+09:00", conclusion="good", **kw)


def accept(led, outlet, to, as_of=AT):
    plan = R.accept_plan(str(led), outlet, str(to), as_of)
    return R.accept(str(led), outlet, str(to), as_of, confirm=plan["digest"], at=as_of)


def all_bytes(*places) -> bytes:
    """試験の側で、置き場の全部のファイル（`.` で始まる物も）を読む。"""
    out = b""
    for place in places:
        place = pathlib.Path(place)
        if place.is_file():
            out += place.read_bytes()
            continue
        for directory, _dirs, files in os.walk(place):
            for name in files:
                out += pathlib.Path(directory, name).read_bytes()
    return out


def words(v) -> set:
    return {p["word"] for p in v["problems"]}


def rows(path) -> list:
    return pathlib.Path(path).read_text(encoding="utf-8").splitlines(keepends=True)


# ------------------------------------------------------------------ 7.1 内の道具が読む出し口

def test_7_1_内の道具は選ばずに書く_判断が無くても元と同じバイトで出さない欄も書く(env):
    led = _ledger(env)
    tools = env / "tools"
    v = accept(led, "tools", tools)
    assert v["generation"] == 1
    for rel in ("data/shops.csv", "notes/n1.yaml", "items/i1.json"):
        assert (tools / rel).read_bytes() == (led / rel).read_bytes()
    assert b"SECRET-ALPHA-7f3" in (tools / "data/shops.csv").read_bytes()   # 内の道具には書く
    assert b"{{note:n1.title}}" in (tools / "data/shops.csv").read_bytes()  # 差し込みも置き換えない
    assert R.verify(str(led), "tools", str(tools), NOW, no_previous=True)["ok"]


def test_7_1_読者の出し口と同じ書き出し先を言う内の出し口は宣言の読みで断る(env):
    decl = outlets()
    decl["outlets"]["tools"]["dest"] = "./site/"
    led = _ledger(env, decl)
    with pytest.raises(RegisterRefused) as exc:
        R.outlet_of(str(led), V.open_vessel(str(led)), "tools")
    assert exc.value.code == "register_outlet_unknown" and "dest_shared" in str(exc.value)
    # 読者の出し口どうし・内どうしは同じ先を言ってよい（旧い材料の柵が、混ざれば赤にする）
    decl = outlets()
    decl["outlets"]["cards"]["dest"] = "site"
    write_outlets(led, decl)
    assert R.outlet_of(str(led), V.open_vessel(str(led)), "cards").dest == "site"


@pytest.mark.parametrize("dest", ["/abs", "../up", "a/../../b", "*", ""])
def test_7_1_書き出し先の形が崩れていれば断る(env, dest):
    decl = outlets()
    decl["outlets"]["tools"]["dest"] = dest
    led = _ledger(env, decl)
    with pytest.raises(RegisterRefused):
        R.load_outlets(str(led), V.open_vessel(str(led)))


@yaml_needed
def test_7_1_内の出し口を読者の書き出し先に書くと旧い材料の柵で止まる(env):
    led = _ledger(env)
    site = env / "site"
    judge_all(led)
    accept(led, "site", site)
    before = all_bytes(site)
    # 書き出し先を宣言しない台帳でも、内の材料が読者の材料を上書きしない（同じ道へ違う中身）
    decl = outlets()
    for name in ("site", "tools", "cards"):
        decl["outlets"][name].pop("dest")
    write_outlets(led, decl)
    accept(led, "site", site)
    accept(led, "tools", env / "tools")          # 内の出し口の封は、別の書き出し先で作る
    for go in (lambda: accept(led, "tools", site), lambda: R.export(str(led), "tools", str(site), AT)):
        with pytest.raises(RegisterRefused) as exc:
            go()
        assert exc.value.code == "register_material_stale" and "other_outlet" in str(exc.value)
    assert all_bytes(site) == before and b"SECRET-" not in before
    assert R.export(str(led), "tools", str(site), AT, check=True)["mixed"]


# ------------------------------------------------------------------ 7.2 出さない欄

@yaml_needed
def test_7_2_読者の材料では表は列を空に枚は鍵を除きほかのバイトは変えない(env):
    led = _ledger(env)
    site = env / "site"
    judge_all(led)
    accept(led, "site", site)
    got, src = rows(site / "data/shops.csv"), rows(led / "data/shops.csv")
    assert got[0] == src[0]
    assert got[2] == 's2,"Beta, Ltd",200,,plain body,,open\n'          # 列は残し、値だけ空
    note = (site / "notes/n1.yaml").read_text(encoding="utf-8")
    assert "secret" not in note and note.startswith("title: Note One\nbody: ")
    assert note.endswith("tags:\n- a\n- b\n# 終わりの注\n")
    item = (site / "items/i1.json").read_text(encoding="utf-8")
    assert item == '{\n  "name": "Widget",\n  "price": 5\n}\n'
    # 内側の単位の材料（札）も、出さない欄の鍵を除く
    vessel = V.open_vessel(str(led))
    for unit in vessel.get("card:c1").units:
        R.judge(str(led), "card:c1", "card_ok", unit=unit.pointer, by="m", at=AT, conclusion="good")
    cards = env / "cards"
    accept(led, "cards", cards)
    mat = json.loads((cards / "materials/cards.json").read_text())
    assert len(mat) == 2 and all("memo" not in i["values"] and i["values"]["role"] for i in mat)


@yaml_needed
def test_重い穴_出さない欄の値は材料にも封にも配る物にも出力にも無い(env):
    led = _ledger(env)
    site, cards = env / "site", env / "cards"
    judge_all(led)
    vessel = V.open_vessel(str(led))
    for unit in vessel.get("card:c1").units:
        R.judge(str(led), "card:c1", "card_ok", unit=unit.pointer, by="m", at=AT, conclusion="good")
    out = [accept(led, "site", site), accept(led, "cards", cards)]
    out.append(R.export(str(led), "site", str(site), AT))
    out.append(R.export(str(led), "cards", str(cards), AT, check=True))
    pub = env / "public"
    for name, place in (("site", site), ("cards", cards)):
        v = R.verify(str(led), name, str(place), NOW, no_previous=True, publish=str(pub / f"{name}.json"))
        assert v["ok"], v
        out.append(v)
    seen = all_bytes(site, cards, pub, led / ".thth/seals") + json.dumps(out, ensure_ascii=False, default=str).encode()
    for secret in ("SECRET-ALPHA-7f3", "SECRET-BETA-9k2", "SECRET-GAMMA-1q8", "SECRET-NOTE-x1", "SECRET-ITEM-z9",
                   "SECRET-CARD-m1", "SECRET-CARD-m2"):
        assert secret.encode() not in seen, secret


@yaml_needed
def test_7_2_鍵を除けない書き方の枚は記録ごと外し理由を言う(env):
    led = _ledger(env, note="{title: Note One, secret: SECRET-NOTE-x1, body: plain}\n")
    site = env / "site"
    judge_all(led)
    plan = R.accept_plan(str(led), "site", str(site), AT)
    assert {"record": "note:n1", "why": "private_unremovable"} in plan["result"]["excluded"]
    accept(led, "site", site)
    assert not (site / "notes/n1.yaml").exists()
    assert b"SECRET-NOTE-x1" not in all_bytes(site)


def test_7_2_設定の形が崩れていれば断る(env):
    for bad in ({"private_fields": "secret"}, {"private_fields": ["a b"]}, {"hide": "row"},
                {"hide": {"field": []}}, {"inserts": ["body"], "extra": 1}):
        decl = outlets()
        decl["outlets"]["site"]["kinds"] = dict(SITE_KINDS, shop=bad)
        led = _ledger(env, decl) if not (env / "led").exists() else env / "led"
        write_outlets(led, decl)
        with pytest.raises(RegisterRefused) as exc:
            R.load_outlets(str(led), V.open_vessel(str(led)))
        assert exc.value.code == "register_outlet_unknown", bad
    # 内の道具の出し口に設定を書くのは断る（内の道具は選ばずに書くので、書いても効かない）
    decl = outlets()
    decl["outlets"]["tools"]["kinds"] = {"shop": {"private_fields": ["secret"]}}
    write_outlets(env / "led", decl)
    with pytest.raises(RegisterRefused):
        R.load_outlets(str(env / "led"), V.open_vessel(str(env / "led")))


# ------------------------------------------------------------------ 7.3 差し込み

@yaml_needed
def test_7_3_差し込みは事実として使える記録の値に置き換え先の版を入力の組に入れる(env):
    led = _ledger(env)
    site = env / "site"
    judge_all(led)
    accept(led, "site", site)
    got = rows(site / "data/shops.csv")
    assert got[1] == "s1,Alpha,100,,Alpha sells Note One,2020-01-01,open\n"
    assert got[3] == 's3,Gamma,300,,"uses Beta, Ltd here",,open\n'      # 区切りの字があれば引用で包む
    assert (site / "notes/n1.yaml").read_text().splitlines()[1] == "body: see Beta, Ltd"
    seal = json.loads(pathlib.Path(R.seal_path(str(led), "site")).read_text())["body"]
    vessel = V.open_vessel(str(led))
    assert {"record": "note:n1", "version": vessel.get("note:n1").version} in seal["inputs"]
    assert R.verify(str(led), "site", str(site), NOW, no_previous=True)["ok"]
    # 差し込み元の中身が変わると材料が古くなり、CI が赤
    note = led / "notes/n1.yaml"
    note.write_text(note.read_text().replace("Note One", "Note Two"))
    v = R.verify(str(led), "site", str(site), NOW, no_previous=True)
    assert not v["ok"] and "narrowed_after_seal" in words(v)


def test_7_3_出し口の外の種類からは差し込まず記録ごと外す(env):
    """検収 11/14 の 7-2: 差し込める先は、その出し口の `kinds` に挙げた種類の、材料に載る記録だけ。出し口の外の種類（品）
    からの差し込みは、値を書かずに記録ごと外す（前は品の値を書き、品の版を入力の組に入れていた）。"""
    decl = outlets()
    decl["outlets"]["site"]["kinds"] = {k: v for k, v in SITE_KINDS.items() if k != "item"}
    led = _ledger(env, decl, shops=SHOPS.replace("Alpha sells {{note:n1.title}}", "Alpha sells {{item:i1.name}}"))
    site = env / "site"
    judge_all(led)
    plan = R.accept_plan(str(led), "site", str(site), AT)
    out = [e for e in plan["result"]["excluded"] if e["record"] == "shop:s1"]
    assert out and out[0]["why"] == "insert_unusable" and out[0]["inserts"][0]["why"] == "outside_outlet"
    accept(led, "site", site)
    assert b"Widget" not in (site / "data/shops.csv").read_bytes()
    assert R.verify(str(led), "site", str(site), NOW, no_previous=True)["ok"]
    # 差し込み先の版の組が古いと赤になる確かめは test_7_3_差し込みは事実として… が出し口の中の種類で見ている


@pytest.mark.parametrize("case", ["no_judgment", "stopped", "reservation_only", "private", "no_field", "missing"])
def test_重い穴_使えない先からの差し込みは値を書かず記録ごと外す(env, case):
    body = {"no_judgment": "x {{item:i1.name}}", "stopped": "x {{shop:s2.name}}",
            "reservation_only": "x {{shop:s2.name}}", "private": "x {{shop:s2.secret}}",
            "no_field": "x {{shop:s2.nothing}}", "missing": "x {{shop:s9.name}}"}[case]
    shops = SHOPS.replace("uses {{shop:s2.name}} here", body)
    decl = outlets()
    if case == "reservation_only":
        decl["outlets"]["site"]["fact"] = [{"question": "fresh"}]
        decl["outlets"]["site"]["reservation"] = {"question": "fresh", "conclusion": "pending",
                                                  "reason_kinds": ["thin"]}
    led = _ledger(env, decl, shops=shops)
    site = env / "site"
    refs = ["shop:s1", "shop:s2", "shop:s3", "note:n1"] + ([] if case == "no_judgment" else ["item:i1"])
    if case == "reservation_only":
        for ref in refs:
            R.judge(str(led), ref, "fresh", by="m", at=AT,
                    conclusion="pending" if ref == "shop:s2" else "good", reason_kind="thin")
    else:
        judge_all(led, refs=refs)
    if case == "stopped":
        R.stop(str(led), "shop:s2", by="m", at=AT)
    plan = R.accept_plan(str(led), "site", str(site), AT)
    bad = [e for e in plan["result"]["excluded"] if e["record"] == "shop:s3"]
    assert len(bad) == 1 and bad[0]["why"] == "insert_unusable", plan["result"]["excluded"]
    assert bad[0]["inserts"][0]["insert"] == body[2:].strip("{}")
    accept(led, "site", site)
    got = (site / "data/shops.csv").read_bytes()
    assert b"s3," not in got and b"Gamma" not in got and b"SECRET-BETA-9k2" not in got
    if case != "stopped":
        assert b"s2," in got    # 先の記録そのものは、使えるなら載る（外すのは差し込みを持つ側）


@yaml_needed
def test_7_3_入れ子は読まず記録ごと外す_1つの欄に64か所まで(env):
    """検収 11/14: 置き換えたあとの値に `{{` が残れば（先の値の中の差し込み）、番号を字のまま出さずに記録ごと外す。"""
    nested = SHOPS.replace("s2,\"Beta, Ltd\"", "s2,\"Beta {{shop:s1.secret}}\"")
    led = _ledger(env, shops=nested)
    site = env / "site"
    judge_all(led)
    plan = R.accept_plan(str(led), "site", str(site), AT)
    out = [e for e in plan["result"]["excluded"] if e["record"] == "shop:s3"]
    assert out and out[0]["why"] == "insert_unusable" and out[0]["inserts"][0]["why"] == "nested"
    accept(led, "site", site)
    got = (site / "data/shops.csv").read_bytes()
    assert b"s3," not in got and b"SECRET-ALPHA" not in got
    for n, ok in ((64, True), (65, False)):
        env2 = env / f"n{n}"
        env2.mkdir()
        led = _ledger(env2, shops=SHOPS.replace("uses {{shop:s2.name}} here", "{{note:n1.title}}" * n))
        judge_all(led)
        plan = R.accept_plan(str(led), "site", str(env2 / "site"), AT)
        out = [e for e in plan["result"]["excluded"] if e["record"] == "shop:s3"]
        assert (not out) if ok else (out[0]["why"] == "insert_unusable" and out[0]["inserts"][0]["why"] == "too_many")


# ------------------------------------------------------------------ 7.4 期限と伏せ方

@yaml_needed
def test_7_4_伏せ方が欄なら期限の切れた記録はその欄だけを空にしほかのバイトは変えない(env):
    led = _ledger(env, note=NOTE.replace("see {{shop:s2.name}}", "plain"))
    site = env / "site"
    judge_all(led, refs=["shop:s1", "shop:s3", "note:n1", "item:i1"])
    R.judge(str(led), "shop:s2", "may_publish@site", by="m", at=AT, conclusion="good", until="2027-03-25")
    accept(led, "site", site)
    before = rows(site / "data/shops.csv")
    e = R.export(str(led), "site", str(site), "2027-03-26", check=True)
    assert "shop:s2=fact" in e["changes"]["leaving"] and "shop:s2=hidden" in e["changes"]["entering"]
    accept(led, "site", site, "2027-03-26")
    after = rows(site / "data/shops.csv")
    assert after[2] == 's2,"Beta, Ltd",,,plain body,,open\n'               # 値段だけ空（出さない欄も空のまま）
    assert after[:2] == before[:2]                                       # ほかの記録の行はバイトが同じ
    # 伏せた記録は事実として使えないので、そこからの差し込みは使えない（s3 は s2 の名前を差し込む）
    assert len(after) == 3 and before[3].startswith("s3,")
    plan = R.accept_plan(str(led), "site", str(site), "2027-03-26")
    assert [e["why"] for e in plan["result"]["excluded"] if e["record"] == "shop:s3"] == ["insert_unusable"]


def test_7_4_止めた記録は伏せ方が欄でも記録ごと外す(env):
    led = _ledger(env)
    site = env / "site"
    judge_all(led)
    R.stop(str(led), "shop:s1", by="m", at=AT)
    accept(led, "site", site)
    got = (site / "data/shops.csv").read_bytes()
    assert b"Alpha" not in got


def test_7_4_伏せ方の既定は記録ごと(env):
    decl = outlets()
    decl["outlets"]["site"]["kinds"] = dict(SITE_KINDS, shop={"private_fields": ["secret"]})
    led = _ledger(env, decl)
    site = env / "site"
    judge_all(led, refs=["shop:s1", "shop:s3", "note:n1", "item:i1"])
    R.judge(str(led), "shop:s2", "may_publish@site", by="m", at=AT, conclusion="good", until="2027-03-25")
    accept(led, "site", site, "2027-03-26")
    got = (site / "data/shops.csv").read_bytes()
    assert b"Beta" not in got


@yaml_needed
def test_7_4_判断のいつまでは効き見直す日の欄は材料に効かない(env):
    led = _ledger(env)
    site = env / "site"
    judge_all(led)
    accept(led, "site", site, "2030-01-01")
    got = rows(site / "data/shops.csv")
    assert got[1].startswith("s1,Alpha,100,,") and ",2020-01-01," in got[1]   # 見直す日は過ぎても載る


def test_7_4_内側の単位は伏せ方で記録ごとか行だけかを選ぶ(env):
    for hide, left in (("unit", ["b"]), ("record", [])):
        sub = env / hide
        sub.mkdir()
        decl = outlets()
        decl["outlets"]["cards"]["kinds"] = {"card": {"private_fields": ["memo"], "hide": hide}}
        led = _ledger(sub, decl)
        vessel = V.open_vessel(str(led))
        units = {u.key["seed"]: u.pointer for u in vessel.get("card:c1").units}
        for seed, pointer in units.items():
            R.judge(str(led), "card:c1", "card_ok", unit=pointer, by="m", at=AT, conclusion="good")
        R.stop(str(led), "card:c1", unit=units["a"], by="m", at=AT)
        accept(led, "cards", sub / "cards")
        mat = json.loads((sub / "cards/materials/cards.json").read_text())
        assert sorted(i["key"]["seed"] for i in mat) == left, hide


def test_7_4_表の行だけの伏せ方は枚の種類の記録の出し口では断る(env):
    decl = outlets()
    decl["outlets"]["site"]["kinds"] = dict(SITE_KINDS, note={"hide": "unit"})
    led = _ledger(env, decl)
    with pytest.raises(RegisterRefused):
        R.load_outlets(str(led), V.open_vessel(str(led)))


# ------------------------------------------------------------------ 7.8 旧い材料

@pytest.mark.parametrize("rel", ["old.csv", ".cache/old.csv", "data/.hidden/x.json", "node_modules/a/b.js"])
def test_重い穴_旧い材料が残ったまま緑にならない(env, rel):
    led = _ledger(env)
    site = env / "site"
    judge_all(led)
    accept(led, "site", site)
    assert R.verify(str(led), "site", str(site), NOW, no_previous=True)["ok"]
    stale = site / rel
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("old")
    v = R.verify(str(led), "site", str(site), NOW, no_previous=True)
    assert not v["ok"] and "stale_material" in words(v) and rel in v["problems"][0]["files"]
    with pytest.raises(RegisterRefused) as exc:
        R.export(str(led), "site", str(site), AT)
    assert exc.value.code == "register_material_stale"
    assert stale.exists()   # THTH は消さない


def test_7_8_見ないのは宣言のignoreに書いた道の型だけ(env):
    decl = outlets()
    decl["outlets"]["site"]["ignore"] = ["node_modules", "package.json", ".astro/*"]
    led = _ledger(env, decl)
    site = env / "site"
    judge_all(led)
    accept(led, "site", site)
    for rel in ("node_modules/a/b.js", "package.json", ".astro/types.d.ts"):
        (site / rel).parent.mkdir(parents=True, exist_ok=True)
        (site / rel).write_text("x")
    assert R.verify(str(led), "site", str(site), NOW, no_previous=True)["ok"]
    (site / "package.lock").write_text("x")
    assert "stale_material" in words(R.verify(str(led), "site", str(site), NOW, no_previous=True))


def test_7_8_書き出し先の中のフォルダのsymlinkも旧い材料として赤(env):
    led = _ledger(env)
    site = env / "site"
    judge_all(led)
    accept(led, "site", site)
    (env / "elsewhere").mkdir()
    (env / "elsewhere/x.csv").write_text("x")
    os.symlink(env / "elsewhere", site / "linked")
    v = R.verify(str(led), "site", str(site), NOW, no_previous=True)
    assert "stale_material" in words(v) and "linked" in v["problems"][0]["files"]


# ------------------------------------------------------------------ 柵を壊して赤を見る（§8.3 の 5 のまだ無い物）

def test_柵_記録を消すと封のあとに狭まったとして赤(env):
    led = _ledger(env)
    site = env / "site"
    judge_all(led)
    accept(led, "site", site)
    (led / "items/i1.json").rename(env / "i1.json.moved")
    v = R.verify(str(led), "site", str(site), NOW, no_previous=True)
    assert not v["ok"] and "narrowed_after_seal" in words(v)


@yaml_needed
def test_柵_材料の表の1行を手で変えると赤(env):
    led = _ledger(env)
    site = env / "site"
    judge_all(led)
    accept(led, "site", site)
    p = site / "data/shops.csv"
    p.write_text(p.read_text().replace("Alpha sells", "Alpha sold"))
    v = R.verify(str(led), "site", str(site), NOW, no_previous=True)
    assert not v["ok"] and "material_mismatch" in words(v)


def test_柵_判断の無い記録を材料に手で足すと赤(env):
    led = _ledger(env)
    site = env / "site"
    judge_all(led, refs=["shop:s1", "shop:s2", "note:n1", "item:i1"])   # s3 には判断が無い
    accept(led, "site", site)
    p = site / "data/shops.csv"
    assert b"Gamma" not in p.read_bytes()
    p.write_bytes(p.read_bytes() + b"s3,Gamma,300,,uses Beta here,,open\n")
    v = R.verify(str(led), "site", str(site), NOW, no_previous=True)
    assert not v["ok"] and "material_mismatch" in words(v)


def test_柵_依る欄でない欄だけを変えても判断は効き原物の版は変わる(env):
    led = _ledger(env)
    vessel = V.open_vessel(str(led))
    j = R.judge(str(led), "shop:s1", "fresh", by="m", at=AT, conclusion="good")
    old = vessel.get("shop:s1").version
    p = led / "data/shops.csv"
    p.write_text(p.read_text().replace("2020-01-01,open", "2020-01-01,closed"))
    vessel = V.open_vessel(str(led))
    assert vessel.get("shop:s1").version != old
    assert J.evaluate(vessel, j) == "effective"
    p.write_text(p.read_text().replace("Alpha,100", "Alpha,101"))
    assert J.evaluate(V.open_vessel(str(led)), j) == "stale"


def test_7_8_配る物の封はこの出し口の物だけ見ずほかの出し口の物は旧い材料(env):
    led = _ledger(env)
    site = env / "site"
    judge_all(led)
    accept(led, "site", site)
    v = R.verify(str(led), "site", str(site), NOW, no_previous=True, publish=str(site / "thth-seal.site.json"))
    assert v["ok"], v
    assert R.verify(str(led), "site", str(site), NOW, no_previous=True)["ok"]
    (site / "thth-seal.tools.json").write_text("{}")
    assert "stale_material" in words(R.verify(str(led), "site", str(site), NOW, no_previous=True))
