"""台帳の器 段 3「短い 1 本の道」の受け入れの試験 3（設計 `docs/設計_台帳の器_2026-11-13.md` §10 の段 3・
期待する答え `docs/期待する答え_台帳の器_段1〜3_2026-11-13.md` §4）。

見本は `tests/fixtures/register_route/`（こぴちゃ・にがみラボ・MMR・共通の台帳の小さな写し）。秘密の鍵は試験ごとの
一時の場所（XDG_CONFIG_HOME）に作る。台帳にも repo にも置かない。

- (a) 共通の出典を 2 つのメディアで使い、片方だけが新しい版を採る → それぞれの材料が、採った版のとおり。止めるは採り入れを待たない
- (b) 同じ「まだ」の行が、断言には使えず、理由の種類が合えば留保には使える（`unresolved` は留保にも使えない）［MMR 問い 3 レーン待ち］
- (c) 材料・控え・判断の行を一緒に書き換えても、受け入れていない世代は CI を通らない
- (d) 封の日時より後に期限が切れた記録が載っていれば CI が赤［こぴちゃ valid_until レーン待ち: 器の命令の「いつまで」だけで試す］
- (e) 「止める」を足したあと、古い封と古い材料に戻しても CI が赤（判断の行を消す・世代の番号を戻す、の両方）
- (f) 公開鍵を repo の中のファイルで差し替えても、確かめに使われない
- つなぐ先: こぴちゃの頁の事実欄の材料が元と同じバイト（今の決まりを写したとき）・MMR の 2 つの出し口の代表の例
"""
from __future__ import annotations

import base64
import json
import os
import pathlib
import shutil

import pytest

from thth import cli
from thth.register import judgment as J
from thth.register import route as R
from thth.register import seal as S
from thth.register import vessel as V
from thth.register.errors import RegisterRefused

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "register_route"
SEALED_AT = "2027-03-20T10:00:00+09:00"


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


def ledger(env, name: str) -> pathlib.Path:
    target = env / name
    shutil.copytree(FIX / name, target)
    return target


def run(capsys, *argv):
    rc = cli.main([str(a) for a in argv])
    out, err = capsys.readouterr()
    return rc, out, err


def jrun(capsys, *argv):
    rc, out, err = run(capsys, *argv, "--json")
    return rc, (json.loads(out) if out.strip() else None), err


def accept(capsys, led, outlet, to, as_of=SEALED_AT, *extra):
    rc, v, err = jrun(capsys, "register", "accept", led, "--outlet", outlet, "--to", to, "--as-of", as_of,
                      "--by", "masaru", *extra)
    assert rc == 1 and v["stage"] == 1, err
    rc, v, err = jrun(capsys, "register", "accept", led, "--outlet", outlet, "--to", to, "--as-of", as_of,
                      "--by", "masaru", "--confirm", v["digest"], *extra)
    assert rc == 0, err
    return v


def verify(capsys, led, outlet, to, now, *extra):
    rc, v, err = jrun(capsys, "register", "verify", led, "--outlet", outlet, "--material", to, "--now", now, *extra)
    return rc, v


def words(v) -> set:
    return {p["word"] for p in (v or {}).get("problems", [])}


def judge(capsys, led, *argv):
    rc, v, err = jrun(capsys, "register", "judge", led, *argv, "--by", "masaru")
    assert rc == 0, err
    return v


def baseline(capsys, led, outlet):
    rc, v, err = jrun(capsys, "register", "judge", led, "--baseline", "--outlet", outlet, "--by", "masaru")
    assert rc == 0, err
    return v


def published(led, outlet, into: pathlib.Path, site) -> pathlib.Path:
    """「配った」: CI の台本のとおり確かめて、緑なら配る物（封と配った時の判断の記録の頭）を公開の場所に置く。"""
    into.parent.mkdir(parents=True, exist_ok=True)
    v = R.verify(str(led), outlet, str(site), "2027-03-21", no_previous=True, publish=str(into))
    assert v["ok"], v
    return into


def use(capsys, led, ref, outlet, as_, *extra):
    rc, v, err = jrun(capsys, "register", "use", led, ref, "--outlet", outlet, "--as", as_, "--as-of", SEALED_AT,
                      *extra)
    return rc, v


# ------------------------------------------------------------------ つなぐ先: こぴちゃの頁の事実欄の材料

def test_つなぐ先_こぴちゃの事実欄は基準のもとで元と同じバイト(env, capsys):
    """今の決まり（点に主張があれば状態で除かずに全部出す）を写すと、材料の表は元と 1 バイトも違わない（§8.3 の 3 の手前）。"""
    led, site = ledger(env, "kopicha"), env / "site"
    baseline(capsys, led, "facts")
    v = accept(capsys, led, "facts", site)
    assert v["generation"] == 1
    src = (FIX / "kopicha/data/knowledge/claims.csv").read_bytes()
    assert (site / "data/knowledge/claims.csv").read_bytes() == src
    # contested・unverified も事実欄に出る（今の決まり）。器は状態で除かない。
    rc, u = use(capsys, led, "claim:c-origin-brazil-024", "facts", "fact")
    assert rc == 0 and u["usable"] and u["as"] == "fact"
    rc, v = verify(capsys, led, "facts", site, "2027-03-21", "--no-previous")
    assert rc == 0 and v["ok"], v
    # 内の道具が読む出し口は、選ばずに書く（判断が無くても全部・元と同じバイト）。段 7（§7.1・§7.8）から、
    # 読者の出し口と別の書き出し先に書く（同じ先だと、読者の材料が内の出し口の旧い材料に見える）
    tools = env / "tools"
    accept(capsys, led, "tools", tools)
    assert (tools / "data/knowledge/evidence.csv").read_bytes() == (FIX / "kopicha/data/knowledge/evidence.csv").read_bytes()


def test_つなぐ先_主張を直すと頁から外れる件数を配る前に出す(env, capsys):
    led, site = ledger(env, "kopicha"), env / "site"
    baseline(capsys, led, "facts")
    accept(capsys, led, "facts", site)
    claims = led / "data/knowledge/claims.csv"
    claims.write_bytes(claims.read_bytes().replace("17.3%".encode(), "17.1%".encode()))
    rc, v, err = jrun(capsys, "register", "export", led, "--outlet", "facts", "--to", site, "--as-of", SEALED_AT,
                      "--check")
    assert rc == 0, err
    assert v["changes"]["leaving"] == ["claim:c-term-tea-grade-ctc-res-milktea1-24=fact"]
    assert v["differs"] == ["data/knowledge/claims.csv"] and not v["same_as_seal"]


# ------------------------------------------------------------------ (a) 共通の出典

def test_試験3a_共通の出典を2つのメディアで使い片方だけが新しい版を採る(env, capsys):
    common, kop, nig = ledger(env, "common"), ledger(env, "kopicha"), ledger(env, "nigami")
    kop_site, nig_site = env / "kop-site", env / "nig-site"
    src = "source:fujimoto-2021"
    for led in (kop, nig):
        rc, v, err = jrun(capsys, "register", "take", led, "--common", common, "--record", src, "--by", "masaru")
        assert rc == 0 and len(v["taken"]) == 1, err
    accept(capsys, kop, "bib", kop_site, SEALED_AT, "--common", common)
    accept(capsys, nig, "bib", nig_site, SEALED_AT, "--common", common)
    v1 = (common / "sources/fujimoto-2021.json").read_bytes()
    assert (kop_site / "common/sources/fujimoto-2021.json").read_bytes() == v1
    assert (nig_site / "common/sources/fujimoto-2021.json").read_bytes() == v1
    # 共通の記録の掲載誌を直す（版 2）。直した時点では、どちらの材料も変わらない
    (common / "sources/fujimoto-2021.json").write_bytes(v1.replace(b"Food Chemistry 342:128228", b"Food Chemistry"))
    v2 = (common / "sources/fujimoto-2021.json").read_bytes()
    for led, site in ((kop, kop_site), (nig, nig_site)):
        rc, v, err = jrun(capsys, "register", "export", led, "--outlet", "bib", "--to", site, "--as-of", SEALED_AT,
                          "--common", common, "--check")
        assert rc == 0 and v["same_as_seal"] and not v["differs"], err
        assert [p["record"] for p in v["pending_takes"]] == ["common:" + src]   # 採り入れ待ち 1
    # こぴちゃだけが採る
    rc, v, err = jrun(capsys, "register", "take", kop, "--common", common, "--by", "masaru")
    assert rc == 0 and len(v["taken"]) == 1, err
    # 採っただけでは広げない（採るも判断。受け入れて封をするまで、材料は前の版のまま）
    rc, v, err = jrun(capsys, "register", "export", kop, "--outlet", "bib", "--to", kop_site, "--as-of", SEALED_AT,
                      "--common", common, "--check")
    assert rc == 0 and v["same_as_seal"] and not v["differs"], v
    rc, v = verify(capsys, kop, "bib", kop_site, "2027-03-21", "--no-previous", "--common", common)
    assert rc == 0 and v["ok"], v
    accept(capsys, kop, "bib", kop_site, SEALED_AT, "--common", common)
    seal_k = json.loads(pathlib.Path(R.seal_path(str(kop), "bib")).read_text())["body"]
    seal_n = json.loads(pathlib.Path(R.seal_path(str(nig), "bib")).read_text())["body"]
    assert {"record": "common:" + src, "version": R.sha(v2)} in seal_k["inputs"]
    assert {"record": "common:" + src, "version": R.sha(v1)} in seal_n["inputs"]
    assert (kop_site / "common/sources/fujimoto-2021.json").read_bytes() == v2
    # にがみラボは版 1 のまま（共通の台帳の器の控えから取り出して作り直せる）
    rc, v, err = jrun(capsys, "register", "export", nig, "--outlet", "bib", "--to", nig_site, "--as-of", SEALED_AT,
                      "--common", common)
    assert rc == 0 and v["same_as_seal"], err
    assert (nig_site / "common/sources/fujimoto-2021.json").read_bytes() == v1
    # 2 つのメディアの sources.csv（原物）は 1 バイトも変わらない
    assert (kop / "data/knowledge/sources.csv").read_bytes() == (FIX / "kopicha/data/knowledge/sources.csv").read_bytes()
    assert (nig / "data/sources.csv").read_bytes() == (FIX / "nigami/data/sources.csv").read_bytes()
    for led, site in ((kop, kop_site), (nig, nig_site)):
        rc, v = verify(capsys, led, "bib", site, "2027-03-21", "--no-previous", "--common", common)
        assert rc == 0 and v["ok"], v
    # 共通の記録に「止める」を付けると、採り入れも封も待たずに、両方の次の材料から外れる
    rc, v, err = jrun(capsys, "register", "judge", common, src, "--stop", "--by", "masaru")
    assert rc == 0, err
    for led, site in ((kop, kop_site), (nig, nig_site)):
        rc, v, err = jrun(capsys, "register", "export", led, "--outlet", "bib", "--to", site, "--as-of", SEALED_AT,
                          "--common", common, "--check")
        assert rc == 0 and v["changes"]["leaving"] == ["common:" + src + "=fact"], err
        assert {"record": "common:" + src, "why": "stopped"} in v["excluded"]
        rc, v = verify(capsys, led, "bib", site, "2027-03-21", "--no-previous", "--common", common)
        assert rc != 0 and "narrowed_after_seal" in words(v)
        # 共通の台帳を読めない CI では、配信を止めず、確かめられないまま配ったと知らせる（設計 §7.2）
        rc, v = verify(capsys, led, "bib", site, "2027-03-21", "--no-previous")
        assert rc == 0 and {"word": "common_unchecked"} in v["warnings"]


def test_試験3a_採った版を取り出せなければ断る(env, capsys):
    common, nig = ledger(env, "common"), ledger(env, "nigami")
    take = R.take(str(nig), str(common), ["source:fujimoto-2021"], by="m", at="2027-03-01T00:00:00+09:00")
    assert len(take["taken"]) == 1
    (common / "sources/fujimoto-2021.json").write_bytes(b'{"title": "x"}\n')
    # 控えの置き場ごと別の名前に移すと、採った版を取り出せない（写しをメディアに作らないので）
    (common / ".thth/kept").rename(common / ".thth/kept-moved")
    rc, v, err = jrun(capsys, "register", "accept", nig, "--outlet", "bib", "--to", env / "s", "--as-of", SEALED_AT,
                      "--by", "m", "--common", common)
    assert rc != 0 and v["code"] == "register_kept_missing"


# ------------------------------------------------------------------ (b) MMR の「まだ」の行と、2 つの出し口

def _unit(led, record, seed):
    vessel = V.open_vessel(str(led))
    return V.find_units(vessel.get(record), {"seed": seed}).pointer


GOPHER = "credit:ap-alex-gopher.mastered_by"


def test_試験3b_まだの行は断言に使えず理由の種類が合えば留保に使える_MMR問い3レーン待ち(env, capsys):
    """［待ち］MMR 問い 3: 判断を枚に付けるか行に付けるか。ここは「出してよいかは枚、使えるかは行」で組んだ答え。"""
    led, site = ledger(env, "mmr"), env / "site"
    for outlet in ("song_page", "artist_page"):
        baseline(capsys, led, outlet)
    # 段 7（§7.8）から、出し口ごとに書き出し先を分ける（同じ先だと、もう片方の材料が旧い材料に見える）
    for outlet in ("song_page", "artist_page"):
        accept(capsys, led, outlet, site / outlet)
    table = {"seed-liked-0443": ("other_version", True), "seed-liked-0831": ("release_credits", True),
             "seed-liked-1094": ("conflict", True), "seed-liked-0665": ("unresolved", False)}
    for seed, (kind, as_note) in table.items():
        unit = _unit(led, GOPHER, seed)
        rc, v = use(capsys, led, GOPHER, "song_page", "fact", "--unit", unit)
        assert rc != 0 and not v["usable"], seed            # 断言（事実として）は使えない
        rc, v = use(capsys, led, GOPHER, "song_page", "reservation", "--unit", unit)
        assert v["usable"] is as_note, seed                  # 留保として: unresolved だけ使えない
        if as_note:
            assert v["reason_kind"] == kind
        else:
            assert v["why"] == "reason_kind_not_allowed"
    # 出し口の無い組（public・public_song が false）と、public_song の欄が無い組は、種類によらず出ない
    for record, seed in (("credit:ap-buck-rodger.remixed_by", "seed-liked-1103"),
                         ("credit:ap-nofield.produced_by", "seed-liked-0900")):
        rc, v = use(capsys, led, record, "song_page", "reservation", "--unit", _unit(led, record, seed))
        assert not v["usable"] and v["why"] == "not_permitted", record
    # 材料: 曲の頁は shown の印と held の注（理由の種類と、説明の文を付けて渡す）、人の頁は shown だけ
    song = json.loads((site / "song_page/materials/song_page.json").read_text())
    got = {(i["values"]["seed"], i["as"], i.get("reason_kind")) for i in song}
    assert got == {("seed-liked-0161", "fact", None), ("seed-liked-0383", "fact", None),
                   ("seed-liked-0443", "reservation", "other_version"),
                   ("seed-liked-0831", "reservation", "release_credits"),
                   ("seed-liked-1094", "reservation", "conflict")}
    assert {i["note"] for i in song if i["as"] == "reservation"} == {
        "別の版の記載（見本の説明）", "盤全体の記載（見本の説明）", "資料で食い違う（見本の説明）"}
    artist = json.loads((site / "artist_page/materials/artist_page.json").read_text())
    assert {(i["values"]["seed"], i["as"]) for i in artist} == {("seed-liked-0161", "fact"), ("seed-liked-0383", "fact")}
    for outlet in ("song_page", "artist_page"):
        rc, v = verify(capsys, led, outlet, site / outlet, "2027-03-21", "--no-previous")
        assert rc == 0 and v["ok"], (outlet, v)


def test_試験3b_決まりは理由の説明の文を読まない(env, capsys):
    led, site = ledger(env, "mmr"), env / "site"
    baseline(capsys, led, "song_page")
    accept(capsys, led, "song_page", site)
    path = led / "site/src/credits/ap-alex-gopher.mastered_by.json"
    data = json.loads(path.read_text())
    for row in data["rows"]:
        if row["seed"] == "seed-liked-0665":
            row["held_reason_ja"] = "別の版の記載（説明だけ注に寄せた）"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    rc, v = use(capsys, led, GOPHER, "song_page", "reservation", "--unit", _unit(led, GOPHER, "seed-liked-0665"))
    assert not v["usable"] and v["why"] == "reason_kind_not_allowed"


# ------------------------------------------------------------------ (c) 受け入れていない世代

def _rewrite_together(env, led, site):
    """主張 1 件の文を書き換え、その版への「よい」の判断の行を手で足し、材料も書き換える（accept はしない）。"""
    claims = led / "data/knowledge/claims.csv"
    claims.write_bytes(claims.read_bytes().replace("17.3%".encode(), "17.1%".encode()))
    vessel = V.open_vessel(str(led))
    J.append(str(led), [J.make(vessel, {"record": "claim:c-term-tea-grade-ctc-res-milktea1-24"}, "may_publish@facts",
                               by="someone", at="2027-03-22T00:00:00+09:00", conclusion="good")])
    shutil.copy(claims, site / "data/knowledge/claims.csv")


def test_試験3c_材料と控えと判断の行を一緒に書き換えても受け入れていない世代はCIを通らない(env, capsys):
    led, site = ledger(env, "kopicha"), env / "site"
    baseline(capsys, led, "facts")
    accept(capsys, led, "facts", site)
    prev = published(led, "facts", env / "published/facts.json", site)
    _rewrite_together(env, led, site)
    rc, v = verify(capsys, led, "facts", site, "2027-03-23", "--previous", prev)
    assert rc != 0 and {"narrowed_after_seal", "material_mismatch"} <= words(v), v
    # 手元で使えるかを問うても同じ線（受け入れていない判断は広げない）
    rc, u = use(capsys, led, "claim:c-term-tea-grade-ctc-res-milktea1-24", "facts", "fact")
    assert rc != 0 and u["why"] == "not_accepted"
    # 封（材料の控え）の出力まで書き換えると、署名が合わない
    sp = pathlib.Path(R.seal_path(str(led), "facts"))
    seal = json.loads(sp.read_text())
    seal["body"]["outputs"]["data/knowledge/claims.csv"] = R.sha((site / "data/knowledge/claims.csv").read_bytes())
    seal["body"]["judgments"] = {"bytes": len(R.judgments_raw(str(led))), "count": 99,
                                 "sha256": R.sha(R.judgments_raw(str(led)))}
    sp.write_text(json.dumps(seal, ensure_ascii=False))
    rc, v = verify(capsys, led, "facts", site, "2027-03-23", "--previous", prev)
    assert rc != 0 and words(v) == {"signature"}


def test_試験3c_受け入れれば同じ判断が手元とCIで効く(env, capsys):
    led, site = ledger(env, "kopicha"), env / "site"
    baseline(capsys, led, "facts")
    accept(capsys, led, "facts", site)
    prev = published(led, "facts", env / "published/facts.json", site)
    _rewrite_together(env, led, site)
    v = accept(capsys, led, "facts", site)
    assert v["generation"] == 2
    rc, u = use(capsys, led, "claim:c-term-tea-grade-ctc-res-milktea1-24", "facts", "fact")
    assert rc == 0 and u["usable"]
    rc, v = verify(capsys, led, "facts", site, "2027-03-23", "--previous", prev)
    assert rc == 0 and v["ok"], v


def test_試験3c_封のあとで判断の行を書き換えると赤(env, capsys):
    led, site = ledger(env, "kopicha"), env / "site"
    baseline(capsys, led, "facts")
    accept(capsys, led, "facts", site)
    path = pathlib.Path(J.path_of(str(led)))
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    path.write_text("".join(lines[1:]), encoding="utf-8")   # 受け入れた行を 1 つ消す
    rc, v = verify(capsys, led, "facts", site, "2027-03-21", "--no-previous")
    assert rc != 0 and words(v) == {"judgments_rewritten"}


def test_試験3c_宣言を広げても封のときの決まりでなければ赤(env, capsys):
    led, site = ledger(env, "mmr"), env / "site"
    baseline(capsys, led, "song_page")
    accept(capsys, led, "song_page", site)
    path = led / ".thth/outlets.json"
    path.write_text(path.read_text().replace('"role_differs"', '"role_differs", "unresolved"'))
    rc, v = verify(capsys, led, "song_page", site, "2027-03-21", "--no-previous")
    assert rc != 0 and "decl_changed" in words(v)


# ------------------------------------------------------------------ (d) 期限

SELIMBONG = "claim:c-producer-selimbong-007"


def _with_until(env, capsys, on_narrow=None):
    led, site = ledger(env, "kopicha"), env / "site"
    if on_narrow:
        path = led / ".thth/outlets.json"
        data = json.loads(path.read_text())
        data["outlets"]["facts"]["on_narrow"] = on_narrow
        path.write_text(json.dumps(data, ensure_ascii=False))
    baseline(capsys, led, "facts")
    rc, v, err = jrun(capsys, "register", "judge", led, SELIMBONG, "--question", "may_publish@facts",
                      "--conclusion", "good", "--until", "2027-03-31", "--by", "masaru")
    assert rc == 0, err
    accept(capsys, led, "facts", site, SEALED_AT)
    return led, site


def test_試験3d_封の日時より後に期限が切れた記録が載っていればCIが赤_こぴちゃvalid_untilレーン待ち(env, capsys):
    """［待ち］こぴちゃ: 主張の valid_until を期限として読むか（今は読まない）。ここは器の命令で付けた「いつまで」だけ。"""
    led, site = _with_until(env, capsys)
    rc, v = verify(capsys, led, "facts", site, "2027-04-01", "--no-previous")
    assert rc != 0 and "narrowed_after_seal" in words(v)
    item = next(p for p in v["problems"] if p["word"] == "narrowed_after_seal")["items"]
    assert item == [{"item": SELIMBONG + "=fact", "why": "expired"}]
    rc, v = verify(capsys, led, "facts", site, "2027-03-25", "--no-previous")
    assert rc == 0 and v["ok"], v
    # 期限の切れた判断の前にある、期限の無い基準の判断へは戻らない
    rc, u, err = jrun(capsys, "register", "use", led, SELIMBONG, "--outlet", "facts", "--as", "fact",
                      "--as-of", "2027-04-01")
    assert rc != 0 and u["why"] == "expired"


def test_試験3d_伏せ方の宣言でCIが外して配る(env, capsys):
    led, site = _with_until(env, capsys, on_narrow="drop")
    rc, v = verify(capsys, led, "facts", site, "2027-04-01", "--no-previous")
    assert rc != 0, "外さずに置いたままなら drop でも赤"
    rc, v, err = jrun(capsys, "register", "export", led, "--outlet", "facts", "--to", site, "--as-of", "2027-04-01")
    assert rc == 0 and v["changes"]["leaving"] == [SELIMBONG + "=fact"], err
    assert b"selimbong" not in (site / "data/knowledge/claims.csv").read_bytes()
    rc, v = verify(capsys, led, "facts", site, "2027-04-01", "--no-previous")
    assert rc == 0 and v["warnings"][-1]["word"] == "dropped", v


# ------------------------------------------------------------------ (e) 止めるを足したあと、古い封に戻す

BRAZIL = "claim:c-origin-brazil-024"
MILK = "claim:c-term-tea-grade-ctc-res-milktea1-24"


def _snapshot(led, site, into: pathlib.Path):
    shutil.copytree(site, into / "site")
    shutil.copy(R.seal_path(str(led), "facts"), into / "seal.json")
    shutil.copy(J.path_of(str(led)), into / "judgments.jsonl")


def _restore_old(led, site, snap: pathlib.Path, *, judgments: bool):
    moved = site.parent / (site.name + "-new")
    site.rename(moved)
    shutil.copytree(snap / "site", site)
    shutil.copy(snap / "seal.json", R.seal_path(str(led), "facts"))
    if judgments:
        shutil.copy(snap / "judgments.jsonl", J.path_of(str(led)))


@pytest.mark.parametrize("drop_line", [True, False], ids=["判断の行を消す", "世代の番号を戻す"])
def test_試験3e_止めるを足したあと古い封と古い材料に戻してもCIが赤(env, capsys, drop_line):
    led, site = ledger(env, "kopicha"), env / "site"
    baseline(capsys, led, "facts")
    accept(capsys, led, "facts", site)                       # 世代 N
    snap = env / "gen1"
    snap.mkdir()
    _snapshot(led, site, snap)
    rc, v, err = jrun(capsys, "register", "judge", led, BRAZIL, "--stop", "--outlet", "facts", "--by", "masaru")
    assert rc == 0, err
    # 止めるは封を待たない: 受け入れる前でも、今の封のままでは CI が赤
    rc, v = verify(capsys, led, "facts", site, "2027-03-21", "--no-previous")
    assert rc != 0 and {"item": BRAZIL + "=fact", "why": "stopped"} in next(
        p for p in v["problems"] if p["word"] == "narrowed_after_seal")["items"]
    v = accept(capsys, led, "facts", site)                   # 世代 N+1 を受け入れて配る
    assert v["generation"] == 2 and v["changes"]["leaving"] == [BRAZIL + "=fact"]
    prev = published(led, "facts", env / "published/facts.json", site)
    rc, v = verify(capsys, led, "facts", site, "2027-03-21", "--previous", prev)
    assert rc == 0 and v["ok"], v
    _restore_old(led, site, snap, judgments=drop_line)
    rc, v = verify(capsys, led, "facts", site, "2027-03-21", "--previous", prev)
    assert rc != 0
    assert "generation_back" in words(v)
    if drop_line:
        assert "judgments_not_continued" in words(v)        # 判断の記録が、前に配った世代の続きでない
    else:
        assert "narrowed_after_seal" in words(v)            # 止めた記録が材料に載っている（封を待たない）
    # 前に配った世代を読まなければ、それだけで赤（黙って通さない）
    rc, v = verify(capsys, led, "facts", site, "2027-03-21")
    assert rc != 0 and "previous_unknown" in words(v)


def test_試験3e_止めないは広げる判断なので受け入れるまで効かない(env, capsys):
    led, site = ledger(env, "kopicha"), env / "site"
    baseline(capsys, led, "facts")
    judge(capsys, led, BRAZIL, "--stop")
    accept(capsys, led, "facts", site)
    judge(capsys, led, BRAZIL, "--resume")
    rc, u = use(capsys, led, BRAZIL, "facts", "fact")
    assert rc != 0 and u["why"] == "stopped"
    accept(capsys, led, "facts", site)
    rc, u = use(capsys, led, BRAZIL, "facts", "fact")
    assert rc == 0 and u["usable"]


def test_試験3e_止めるは中身が変わっても外れない_出し口を限れる(env, capsys):
    """止めるは番号に付く（中身が変わっても外れない）。出し口を限れば、ほかの出し口には効かない（染めと化学の図鑑の頁）。"""
    led, site = ledger(env, "kopicha"), env / "site"
    judge(capsys, led, MILK, "--stop", "--outlet", "strict")
    claims = led / "data/knowledge/claims.csv"
    claims.write_bytes(claims.read_bytes().replace("17.3%".encode(), "17.1%".encode()))
    baseline(capsys, led, "facts")
    baseline(capsys, led, "strict")
    accept(capsys, led, "facts", site)
    accept(capsys, led, "strict", site)
    rc, u = use(capsys, led, MILK, "facts", "fact")
    assert rc == 0 and u["usable"]
    rc, u = use(capsys, led, MILK, "strict", "fact")
    assert rc != 0 and u["why"] == "stopped"
    rc, u = use(capsys, led, "claim:c-producer-selimbong-007", "strict", "fact")
    assert rc == 0 and u["usable"]


# ------------------------------------------------------------------ (f) 公開鍵の差し替え

def _attacker_seal(env, led, site, monkeypatch):
    """別の鍵で作った封と、それに合わせた材料（主張の文を書き換えた物）。"""
    claims = led / "data/knowledge/claims.csv"
    claims.write_bytes(claims.read_bytes().replace("17.3%".encode(), "99.9%".encode()))
    shutil.copy(claims, site / "data/knowledge/claims.csv")
    owner = os.environ["XDG_CONFIG_HOME"]
    other = env / "attacker-config"
    other.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(other))
    info = S.init_key()
    # 別の鍵の封: 今の判断の全部を受け入れたことにして、材料に合う出力を書く
    vessel = V.open_vessel(str(led))
    J.append(str(led), [J.make(vessel, {"record": "claim:c-term-tea-grade-ctc-res-milktea1-24"},
                               "may_publish@facts", by="attacker", at="2027-03-22T00:00:00+09:00", conclusion="good")])
    monkeypatch.setenv("XDG_CONFIG_HOME", owner)            # 中身の計算だけは持ち主の封の上で（署名は別の鍵）
    plan = R.accept_plan(str(led), "facts", str(site), "2027-03-22")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(other))
    body = dict(plan["body"], at="2027-03-22T00:00:00+09:00", public_key=info["fingerprint"])
    sig = S.sign(V.canonical(body).encode("utf-8"), register_root=str(led))
    pathlib.Path(R.seal_path(str(led), "facts")).write_text(json.dumps({"body": body, "signature": sig}))
    # repo の中に、別の鍵の公開鍵のファイルを置く（どの名前でも読まれない）
    for name in ("seal_public.pem", "public_key.pem", "seal.pub"):
        (led / ".thth" / name).write_text(info["public_key"])
        (led / name).write_text(info["public_key"])
    monkeypatch.setenv("XDG_CONFIG_HOME", owner)
    return info


def test_試験3f_公開鍵をrepoの中のファイルで差し替えても確かめに使われない(env, capsys, monkeypatch):
    led, site = ledger(env, "kopicha"), env / "site"
    baseline(capsys, led, "facts")
    accept(capsys, led, "facts", site)
    owner_pub = S.public_info()["public_key_base64"]
    attacker = _attacker_seal(env, led, site, monkeypatch)
    monkeypatch.setenv(S.ENV_PUBLIC, owner_pub)              # CI の変数（repo の木の外）
    rc, v = verify(capsys, led, "facts", site, "2027-03-23", "--no-previous")
    assert rc != 0 and words(v) == {"signature"} and v.get("key") == "env"
    monkeypatch.delenv(S.ENV_PUBLIC)                         # 手元の配信: 持ち主の鍵から作った公開鍵
    rc, v = verify(capsys, led, "facts", site, "2027-03-23", "--no-previous")
    assert rc != 0 and words(v) == {"signature"} and v.get("key") == "local"
    # 確かめの意味の裏: 別の鍵の公開鍵を CI の変数に置けば通る（だから鍵は変数だけから取る）
    monkeypatch.setenv(S.ENV_PUBLIC, attacker["public_key_base64"])
    rc, v = verify(capsys, led, "facts", site, "2027-03-23", "--no-previous")
    assert rc == 0, v


def test_試験3f_正しい封ならrepoの中に鍵のファイルがあっても緑(env, capsys, monkeypatch):
    led, site = ledger(env, "kopicha"), env / "site"
    baseline(capsys, led, "facts")
    accept(capsys, led, "facts", site)
    junk = "-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEA0PCtqxq6QRDzcd5XpBrc0OLGPW7Ii+weFvNmkafzxc8=\n-----END PUBLIC KEY-----\n"
    for name in ("seal_public.pem", "seal.pub"):
        (led / ".thth" / name).write_text(junk)
    monkeypatch.setenv(S.ENV_PUBLIC, S.public_info()["public_key"])
    rc, v = verify(capsys, led, "facts", site, "2027-03-21", "--no-previous")
    assert rc == 0 and v["ok"] and v["key"] == "env", v


# ------------------------------------------------------------------ 結び直す口・根拠の変わり

def test_根拠だけが変わったら要る判断が効かなくなる(env, capsys):
    led, site = ledger(env, "kopicha"), env / "site"
    judge(capsys, led, MILK, "--question", "says_so", "--word", "supported", "--how", "original",
          "--against", "evidence:e-milk-01")
    accept(capsys, led, "strict", site)
    rc, u = use(capsys, led, MILK, "strict", "fact")
    assert rc == 0 and u["usable"]
    ev = led / "data/knowledge/evidence.csv"
    ev.write_bytes(ev.read_bytes().replace("水浸出物 17.3%,,,,".encode(), "水浸出物 17.1%,,,,".encode()))
    rc, u = use(capsys, led, MILK, "strict", "fact")
    assert rc != 0 and u["why"] == "against_changed"
    rc, v = verify(capsys, led, "strict", site, "2027-03-21", "--no-previous")
    assert rc != 0 and "narrowed_after_seal" in words(v)


def test_結び直す口は別の者だけが新しい版へ結ぶ(env, capsys):
    led, site = ledger(env, "kopicha"), env / "site"
    v = judge(capsys, led, MILK, "--question", "says_so", "--word", "supported")
    claims = led / "data/knowledge/claims.csv"
    claims.write_bytes(claims.read_bytes().replace("17.3%".encode(), "17.1%".encode()))
    version = V.open_vessel(str(led)).get(MILK).version
    rc, out, err = jrun(capsys, "register", "judge", led, "--carry", v["judgment"], "--to", version[:12],
                        "--changed-by", "masaru", "--by", "masaru")
    assert rc != 0 and out["code"] == "register_judgment_same_person"
    rc, out, err = jrun(capsys, "register", "judge", led, "--carry", v["judgment"], "--to", "deadbeef00",
                        "--changed-by", "masaru", "--by", "reviewer")
    assert rc != 0 and out["code"] == "register_args_invalid"
    rc, out, err = jrun(capsys, "register", "judge", led, "--carry", v["judgment"], "--to", version[:12],
                        "--changed-by", "masaru", "--by", "reviewer")
    assert rc == 0, err
    carried = next(j for j in J.read(str(led)) if j["id"] == out["judgment"])
    assert carried["carries"] == v["judgment"] and carried["path"] == "carry" and carried["by"] == "reviewer"
    accept(capsys, led, "strict", site)
    rc, u = use(capsys, led, MILK, "strict", "fact")
    assert rc == 0 and u["usable"]


# ------------------------------------------------------------------ 断り・鍵・旧い材料

def test_封の無い出し口は材料を作らず受け入れを勧める(env, capsys):
    led = ledger(env, "kopicha")
    rc, v, err = jrun(capsys, "register", "export", led, "--outlet", "facts", "--to", env / "s", "--as-of", SEALED_AT)
    assert rc != 0 and v["code"] == "register_seal_missing"
    rc, u = use(capsys, led, MILK, "facts", "fact")
    assert rc != 0 and u["why"] == "no_seal"
    rc, v, err = jrun(capsys, "register", "export", led, "--outlet", "nothing", "--to", env / "s", "--as-of", SEALED_AT)
    assert rc != 0 and v["code"] == "register_outlet_unknown"


def test_一覧に無い旧い材料があれば止まる_消さない(env, capsys):
    led, site = ledger(env, "kopicha"), env / "site"
    baseline(capsys, led, "facts")
    old = site / "materials/facts.json"
    old.parent.mkdir(parents=True)
    old.write_text("[]\n")
    rc, v, err = jrun(capsys, "register", "accept", led, "--outlet", "facts", "--to", site, "--as-of", SEALED_AT,
                      "--by", "m")
    assert v["stale"] == ["materials/facts.json"]
    rc, v, err = jrun(capsys, "register", "accept", led, "--outlet", "facts", "--to", site, "--as-of", SEALED_AT,
                      "--by", "m", "--confirm", v["digest"])
    assert rc != 0 and v["code"] == "register_material_stale"
    assert old.exists() and not pathlib.Path(R.seal_path(str(led), "facts")).exists()


def test_秘密の鍵は台帳にもrepoにも置かず値を出さない(env, capsys, monkeypatch):
    led, site = ledger(env, "kopicha"), env / "site"
    key = pathlib.Path(S.key_path())
    assert oct(key.stat().st_mode & 0o777) == "0o600"
    secret = key.read_text()
    baseline(capsys, led, "facts")
    rc, out, err = run(capsys, "register", "accept", led, "--outlet", "facts", "--to", site, "--as-of", SEALED_AT,
                       "--by", "m")
    digest = out.strip().rsplit("--confirm ", 1)[-1].split()[0]
    rc, out2, err2 = run(capsys, "register", "accept", led, "--outlet", "facts", "--to", site, "--as-of", SEALED_AT,
                         "--by", "m", "--confirm", digest)
    assert rc == 0, err2
    rc, out3, err3 = run(capsys, "register", "key", "show")
    body = "".join(l for l in secret.splitlines() if not l.startswith("-----"))
    for text in (out, err, out2, err2, out3, err3, pathlib.Path(R.seal_path(str(led), "facts")).read_text()):
        assert "PRIVATE KEY" not in text and body not in text
    for path in list(led.rglob("*")) + list(site.rglob("*")):
        if path.is_file():
            assert body.encode() not in path.read_bytes(), path
    # 鍵の置き場を台帳の repo の中に向けると、封をせずに断る（前の封は持ち主の公開鍵で確かめられる）
    monkeypatch.setenv(S.ENV_PUBLIC, S.public_info()["public_key_base64"])
    (led / ".git").mkdir()
    inside = led / "conf"
    inside.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(inside))
    S.init_key()
    judge(capsys, led, BRAZIL, "--stop")
    rc, v, err = jrun(capsys, "register", "accept", led, "--outlet", "facts", "--to", site, "--as-of", SEALED_AT,
                      "--by", "m")
    rc, v, err = jrun(capsys, "register", "accept", led, "--outlet", "facts", "--to", site, "--as-of", SEALED_AT,
                      "--by", "m", "--confirm", v["digest"])
    assert rc != 0 and v["code"] == "register_seal_key_unsafe" and v["detail"] == "inside_register"
    rc, v, err = jrun(capsys, "register", "key", "init")
    assert rc != 0 and v["code"] == "register_seal_key_unsafe" and v["detail"] == "key_exists"


def test_受け入れの印が違えば書かない(env, capsys):
    led, site = ledger(env, "kopicha"), env / "site"
    baseline(capsys, led, "facts")
    rc, v, err = jrun(capsys, "register", "accept", led, "--outlet", "facts", "--to", site, "--as-of", SEALED_AT,
                      "--by", "m", "--confirm", "0000000000000000")
    assert rc != 0 and v["code"] == "register_confirm_mismatch"
    assert not site.exists() and not pathlib.Path(R.seal_path(str(led), "facts")).exists()


def test_断りの符丁は目録に3つがそろう_段3():
    from thth import refusal_catalog as rc
    for code in ("register_outlet_unknown", "register_use_not_usable", "register_material_stale",
                 "register_seal_missing", "register_seal_unverified", "register_seal_key_missing",
                 "register_seal_key_unsafe", "register_common_unusable"):
        entry = rc.CATALOG[code]
        assert entry["why"] and entry["fix"]["text"] and entry["better"], code


def test_出し口の宣言の崩れは断る(env, capsys):
    led = ledger(env, "kopicha")
    path = led / ".thth/outlets.json"
    for bad in ({"schema": "thth.outlets/1", "outlets": {"facts": {"kinds": ["nothing"]}}},
                {"schema": "thth.outlets/1", "outlets": {"facts": {"kinds": ["claim"], "run": "rm -rf /"}}},
                {"schema": "thth.outlets/1", "outlets": {"facts": {"kinds": ["claim"], "level": "unit"}}}):
        path.write_text(json.dumps(bad))
        rc, v, err = jrun(capsys, "register", "export", led, "--outlet", "facts", "--to", env / "s", "--as-of", SEALED_AT)
        assert rc != 0 and v["code"] == "register_outlet_unknown", bad


def test_dateの無い期限は年末と月末まで():
    assert R.until_end("2026") == R.parse_time("2027-01-01")
    assert R.until_end("2026-12") == R.parse_time("2027-01-01")
    assert R.until_end("2027-03-31") == R.parse_time("2027-04-01")
    assert R.until_end("いつか") is None


def test_封の署名は1バイトでも変われば合わない(env):
    sig = S.sign(b"abc", register_root=None)
    pem = S.local_public_pem()
    assert S.verify(b"abc", sig, pem)
    assert not S.verify(b"abd", sig, pem)
    assert not S.verify(b"abc", base64.b64encode(b"x" * 64).decode(), pem)


# ------------------------------------------------------------------ 公開の直前の台本（CI・手元の配信）

def _gate(env, led, site, previous, extra_env=None):
    import subprocess
    import sys
    child = dict(os.environ, THTH=f"{sys.executable} -m thth", PYTHONPATH=str(ROOT),
                 THTH_REGISTER_SEAL_PUBLIC_KEY=S.public_info()["public_key_base64"], **(extra_env or {}))
    return subprocess.run(["sh", str(ROOT / "tools/register_verify_gate.sh"), str(led), "facts", str(site), previous],
                          capture_output=True, text=True, env=child, cwd=str(env), timeout=120)


def test_台本_公開の直前に封と公開鍵で確かめ前に配った世代を公開の場所から読む(env, capsys):
    if shutil.which("curl") is None:
        pytest.skip("curl が無い")
    led, site = ledger(env, "kopicha"), env / "site"
    baseline(capsys, led, "facts")
    accept(capsys, led, "facts", site)
    r = _gate(env, led, site, "first")
    assert r.returncode == 0, r.stderr
    gen1 = env / "public-gen1.json"
    shutil.copy(site / "thth-seal.facts.json", gen1)          # 配った世代 1（公開の場所）
    snap = env / "gen1"
    snap.mkdir()
    _snapshot(led, site, snap)
    judge(capsys, led, BRAZIL, "--stop")
    accept(capsys, led, "facts", site)
    r = _gate(env, led, site, gen1.as_uri())
    assert r.returncode == 0, r.stderr
    gen2 = env / "public-gen2.json"
    shutil.copy(site / "thth-seal.facts.json", gen2)          # 配った世代 2
    _restore_old(led, site, snap, judgments=False)            # 古い封と古い材料に戻す
    r = _gate(env, led, site, gen2.as_uri())
    assert r.returncode != 0 and "register_seal_unverified" in r.stderr
    r = _gate(env, led, site, (env / "nothing.json").as_uri())
    assert r.returncode != 0 and "前に配った世代の封を取れません" in r.stderr
    r = _gate(env, led, site, gen2.as_uri(), {"THTH_PIN": "0.0.0"})
    assert r.returncode != 0 and "止めた版と違います" in r.stderr
