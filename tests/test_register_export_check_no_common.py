"""共通の台帳を読めない CI の `export --check`（--common 無し）は、共通から採った物を封の指紋で照らす
（こぴちゃ 第12 #8138・11/16。前は共通の物がみな「置いてある材料と違う」に出て、仮の共通の台帳を作って渡していた）。"""
from __future__ import annotations

from tests.test_register_route import SEALED_AT, accept, env, jrun, ledger  # noqa: F401

SRC = "source:fujimoto-2021"
COMMON_FILE = "common/sources/fujimoto-2021.json"


def _sealed(env, capsys):
    common, kop = ledger(env, "common"), ledger(env, "kopicha")
    site = env / "kop-site"
    rc, v, err = jrun(capsys, "register", "take", kop, "--common", common, "--record", SRC, "--by", "masaru")
    assert rc == 0, err
    accept(capsys, kop, "bib", site, SEALED_AT, "--common", common)
    assert (site / COMMON_FILE).exists()
    return common, kop, site


def check(capsys, kop, site, *extra):
    return jrun(capsys, "register", "export", kop, "--outlet", "bib", "--to", site, "--as-of", SEALED_AT,
                "--check", *extra)


def test_共通を読まずに比べても封のときの材料なら同じ(env, capsys):
    _, kop, site = _sealed(env, capsys)
    rc, v, err = check(capsys, kop, site)
    assert rc == 0 and v["same_as_seal"] and not v["differs"] and v["found"] == [], (v, err)
    assert v["common_unchecked"] is True and v["common_not_in_seal"] == []


def test_共通の物を1バイト書き換えると違いに出る(env, capsys):
    _, kop, site = _sealed(env, capsys)
    path = site / COMMON_FILE
    path.write_bytes(path.read_bytes() + b" ")
    rc, v, err = check(capsys, kop, site)
    assert rc == 1 and COMMON_FILE in v["differs"] and "differs" in v["found"], (v, err)


def test_共通の物を消すと違いに出る(env, capsys):
    _, kop, site = _sealed(env, capsys)
    (site / COMMON_FILE).unlink()
    rc, v, err = check(capsys, kop, site)
    assert rc == 1 and COMMON_FILE in v["differs"], (v, err)


def test_common_を渡したときは今までどおり印を付けない(env, capsys):
    common, kop, site = _sealed(env, capsys)
    rc, v, err = check(capsys, kop, site, "--common", common)
    assert rc == 0 and v["found"] == [] and "common_unchecked" not in v, (v, err)


def test_文の出力は共通を読まずに比べたと言う(env, capsys):
    _, kop, site = _sealed(env, capsys)
    from thth import cli
    rc = cli.main(["register", "export", str(kop), "--outlet", "bib", "--to", str(site), "--as-of", SEALED_AT,
                   "--check"])
    out, err = capsys.readouterr()
    assert rc == 0 and "共通の台帳を読まずに比べました" in out, out + err


def test_書く_export_は今までどおり(env, capsys):
    """--check でない export は共通の物を照らさない（書けないので、置いてある共通の物は一覧に無い旧い材料で止まる）。"""
    _, kop, site = _sealed(env, capsys)
    rc, v, err = jrun(capsys, "register", "export", kop, "--outlet", "bib", "--to", site, "--as-of", SEALED_AT)
    assert rc != 0 and "register_material_stale" in (err + str(v)), (rc, v, err)


def test_封の出力と記録を結ぶ():
    from thth.register import route as R
    outs = {"common/a/x.json": "v1", "common/b/y.json": "v2", "data/z.csv": "v1"}
    assert R._sealed_common_path(outs, {"record": "common:k:x", "version": "v1"}) == "common/a/x.json"
    # 出さない欄で削ると指紋が版と違う → 名前で結ぶ
    assert R._sealed_common_path(outs, {"record": "common:k:y", "version": "other"}) == "common/b/y.json"
    # 指紋も名前も 1 つに決まらなければ言わない
    two = {"common/a/x.json": "v1", "common/b/x.json": "v1"}
    assert R._sealed_common_path(two, {"record": "common:k:x", "version": "v1"}) is None
    assert R._sealed_common_path(outs, {"record": "common:k:none", "version": "zz"}) is None


def _private(common, rule):
    import json
    (common / ".thth/outlets.json").write_text(json.dumps({"schema": "thth.outlets/1", "outlets": {},
                                                          "shared_private": {"source": rule}}))


def test_検収危1_共通の側で記録ごと出さない種類はverifyと同じく違いに数えない(env, capsys):
    """5.26.0 の検収 危 1: 共通の出さない欄で記録ごと外した物（封の項目に無い）を、共通の台帳なしの --check が
    `common_not_in_seal` に数え、verify は緑なのに rc 1 にしていた（5.25.0 では rc 0）。"""
    from thth.register import route as R
    common, kop = ledger(env, "common"), ledger(env, "kopicha")
    _private(common, "*")
    rc, v, err = jrun(capsys, "register", "take", kop, "--common", common, "--record", SRC, "--by", "masaru")
    assert rc == 0, err
    site = env / "s"
    accept(capsys, kop, "bib", site, SEALED_AT, "--common", common)
    assert R.verify(str(kop), "bib", str(site), "2027-03-21", no_previous=True)["ok"]
    rc, v, err = check(capsys, kop, site)
    assert rc == 0 and v["found"] == [] and v["common_not_in_seal"] == [], (v, err)


def test_検収_出さない欄で削った共通の物は名で結ばれ書き換えれば違いに出る(env, capsys):
    import json
    common, kop = ledger(env, "common"), ledger(env, "kopicha")
    p = common / "sources/fujimoto-2021.json"
    d = json.loads(p.read_text())
    d["memo"] = "X"
    p.write_text(json.dumps(d))
    _private(common, ["memo"])
    rc, v, err = jrun(capsys, "register", "take", kop, "--common", common, "--record", SRC, "--by", "masaru")
    assert rc == 0, err
    site = env / "s"
    accept(capsys, kop, "bib", site, SEALED_AT, "--common", common)
    rc, v, err = check(capsys, kop, site)
    assert rc == 0 and v["found"] == [], (v, err)
    f = site / COMMON_FILE
    f.write_bytes(f.read_bytes() + b" ")
    rc, v, err = check(capsys, kop, site)
    assert rc == 1 and COMMON_FILE in v["differs"], v
