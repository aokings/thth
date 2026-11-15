"""台帳の器 段 11 の試験（指示 `docs/指示_台帳の器_段11_共通の中身と手数_2026-11-15.md` の A〜F）。

- A: 共通の台帳の備え付けの種類に、出典（書誌だけ）・根拠の 1 行・公開の約束を足す。すでにある共通の台帳へは
  `add --common` の打ち直しで足す（前の宣言と記録は変えない）。メディアの記録は `common:<番号>` で指し、版は採る判断が持つ。
- B: 訂正の 1 本の命令 `fix`（書く → 名指し → 判断 → 受け入れの 1 段め。宣言が許すときだけ封まで）。
- C: `export`・`accept`・`verify` の `--outlet all`。
- D: `export --check` は違いで rc 1・`THTH_ROOT` が無ければ作る・git の中の台帳では直した枚だけを控える。
- E: 記録 0 の表（見出しだけ）も書き出す。
- F: 版の変わった記録から、指す欄で 1 段の記録を「影響の候補」に出す（判断は無効にしない）。

見本は CSV と JSON だけ（PyYAML の無い箱でも走る）。秘密の鍵は試験ごとの一時の場所に作る。
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil

import pytest

from thth import cli
from thth.register import adopt as A
from thth.register import keep as K
from thth.register import route as R
from thth.register import seal as S
from thth.register.errors import RegisterRefused

AT = "2027-03-20T10:00:00+09:00"
BASE_AT = "2027-03-01T00:00:00+09:00"
NOW = "2027-03-21"
BUILTIN = pathlib.Path(R.__file__).resolve().parent / "builtin_kinds"


@pytest.fixture
def env(tmp_path, monkeypatch):
    for key, sub in (("THTH_ROOT", "root"), ("HOME", "home"), ("XDG_CONFIG_HOME", "config")):
        (tmp_path / sub).mkdir()
        monkeypatch.setenv(key, str(tmp_path / sub))
    for key in ("THTH_REGISTERS_DIR", "THTH_PRINCIPALS_DIR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv(S.ENV_PUBLIC, raising=False)
    try:
        S.openssl()
    except RegisterRefused:
        pytest.skip("OpenSSL 3 か 4 が決まった場所に無い")
    S.init_key()
    return tmp_path


def run(capsys, *argv):
    rc = cli.main([str(a) for a in argv])
    out, err = capsys.readouterr()
    return rc, out, err


def jrun(capsys, *argv):
    rc, out, err = run(capsys, *argv, "--json")
    try:
        value = json.loads(out) if out.strip() else None
    except ValueError:
        value = None
    if value is None and err.strip():
        # 断りの JSON は stderr に出る形もある
        for line in err.splitlines():
            if line.strip().startswith("{"):
                try:
                    value = json.loads(line)
                except ValueError:
                    pass
    return rc, value, err


def put(base: pathlib.Path, rel: str, text: str) -> None:
    (base / rel).parent.mkdir(parents=True, exist_ok=True)
    (base / rel).write_text(text, encoding="utf-8", newline="")


# ------------------------------------------------------------------ メディアの見本（主張・根拠・出典）

KINDS = {
    "claim": {"schema": "thth.kind/1", "kind": "claim", "place": "data/claims.csv", "format": "csv",
              "id": {"field": "id"}},
    "evidence": {"schema": "thth.kind/1", "kind": "evidence", "place": "data/evidence.csv", "format": "csv",
                 "id": {"field": "id"},
                 "pointers": [{"field": "claim_id", "meaning": "supports", "to": "claim"},
                              {"field": "source_id", "meaning": "derived_from", "to": "source"}]},
    "source": {"schema": "thth.kind/1", "kind": "source", "place": "data/sources.csv", "format": "csv",
               "id": {"field": "id"}},
    "love": {"schema": "thth.kind/1", "kind": "love", "place": "data/loves/records.csv", "format": "csv",
             "id": {"field": "id"}},
}
CLAIMS = "id,text\nclm-bitter,苦味の受容体は 25 種\nclm-other,渋みは触覚\n"
EVIDENCE = ("id,claim_id,source_id,locator,finding\n"
            "ev-bitter,clm-bitter,src-behrens,abstract,ヒトの苦味受容体を総説\n"
            "ev-other,clm-other,src-wang,p.3,渋みの機構\n")
SOURCES = "id,title,doi\nsrc-behrens,Bitter taste receptors,10.1007/s00018-006-6113-8\nsrc-wang,Astringency,10.1093/chemse/bjae016\n"
LOVES = "id,name\n"   # 見出しだけ（記録 0・にがみラボの data/loves/records.csv の形）


def site_outlets(fix_seal: bool = False, kinds=("claim", "evidence", "source"), **extra) -> dict:
    site = {"audience": "reader", "kinds": list(kinds), "fact": [{"question": "may_publish@site"}], "dest": "site"}
    if fix_seal:
        site["fix_seal"] = True
    site.update(extra)
    return {"schema": "thth.outlets/1", "outlets": {"site": site}}


def media(env, name="nig", decl=None, kinds=None, files=None) -> pathlib.Path:
    led = env / name
    (led / ".thth/kinds").mkdir(parents=True)
    for k, body in (kinds or KINDS).items():
        (led / f".thth/kinds/{k}.json").write_text(json.dumps(body, ensure_ascii=False))
    (led / ".thth/outlets.json").write_text(json.dumps(decl or site_outlets(), ensure_ascii=False, indent=1))
    for rel, text in (files or {"data/claims.csv": CLAIMS, "data/evidence.csv": EVIDENCE,
                                "data/sources.csv": SOURCES, "data/loves/records.csv": LOVES}).items():
        put(led, rel, text)
    return led


def accept(led, outlet, to, *, common=None):
    plan = R.accept_plan(str(led), outlet, str(to), AT, common_root=common and str(common))
    return R.accept(str(led), outlet, str(to), AT, confirm=plan["digest"], at=AT, common_root=common and str(common))


def sealed(env, led, outlet="site"):
    R.baseline(str(led), outlet, by="masaru", at=BASE_AT)
    out = env / f"{led.name}-out"
    accept(led, outlet, out / "site")
    return out


def words(v) -> set:
    return {p["word"] for p in (v or {}).get("problems", [])}


# ------------------------------------------------------------------ A. 共通の台帳の備え付けの種類

def test_A_共通の台帳に出典と根拠の1行と公開の約束の宣言が写る(env, capsys):
    common = env / "shared"
    common.mkdir()
    rc, v, err = jrun(capsys, "register", "add", "shared", "--path", common, "--common", "--by", "masaru")
    assert rc == 0, err
    assert set(v["builtin_kinds"]) == {"correspondence", "operator", "account", "source", "evidence_line", "pledge"}
    for kind in ("source", "evidence_line", "pledge"):
        assert (common / f".thth/kinds/{kind}.json").read_bytes() == (BUILTIN / f"{kind}.json").read_bytes()
    put(common, "source/src-behrens.json", json.dumps(
        {"title": "Bitter taste receptors", "authors": "Behrens M; Meyerhof W", "publication_year": "2006",
         "doi": "10.1007/s00018-006-6113-8", "published_access": "open", "rights_status": "fact_only"}))
    put(common, "evidence_line/el-1.json", json.dumps(
        {"source_id": "src-behrens", "locator": "abstract", "finding": "ヒトの苦味受容体を総説"}, ensure_ascii=False))
    put(common, "pledge/nigami.json", json.dumps(
        {"media": "nigamilab", "text": "広告・スポンサー・提供品はありません。銘柄の推薦をしません。",
         "page_url": "https://example.test/about/", "copied_on": "2026-11-15"}, ensure_ascii=False))
    rc, v, err = jrun(capsys, "register", "check", "shared")
    assert rc == 0, (v, err)


@pytest.mark.parametrize("rel,body,check_id", [
    ("source/no-link.json", {"title": "DOI も URL も無い"}, "source_doi_or_url"),
    ("source/bad-access.json", {"title": "t", "url": "https://x.test/", "published_access": "full"},
     "source_published_access"),
    # 確かめた範囲（媒体の access_status）は共通の出典にも根拠の 1 行にも入れない（11/15 こぴちゃ 第12）
    ("source/checked.json", {"title": "t", "url": "https://x.test/", "access_status": "open"}, "source_no_checked_range"),
    ("evidence_line/checked.json", {"source_id": "s", "locator": "p.1", "finding": "f", "access_status": "full_text"},
     "line_no_media_view"),
    ("evidence_line/no-place.json", {"source_id": "s", "finding": "f"}, "line_fields"),
    ("evidence_line/with-claim.json", {"source_id": "s", "locator": "p.1", "finding": "f", "claim_id": "c1"},
     "line_no_media_view"),
    ("pledge/no-text.json", {"media": "nigamilab"}, "pledge_media_text"),
    # 名前を替えた確かめた範囲・使ってよいかも通さない（置ける欄の一覧で閉じる。段 11 の検収 軽 7）
    ("evidence_line/rs.json", {"source_id": "s", "locator": "p.1", "finding": "f", "review_status": "checked"},
     "line_fields_allowed"),
    ("source/ex.json", {"title": "t", "url": "https://x.test/", "extraction_status": "full_text"}, "source_fields"),
    ("evidence_line/us.json", {"source_id": "s", "locator": "p.1", "finding": "f", "usable_for": "site"},
     "line_fields_allowed"),
])
def test_A_備え付けの種類の検査が断る(env, capsys, rel, body, check_id):
    common = env / "shared"
    common.mkdir()
    rc, _v, err = jrun(capsys, "register", "add", "shared", "--path", common, "--common", "--by", "masaru")
    assert rc == 0, err
    put(common, "source/s.json", json.dumps({"title": "t", "url": "https://x.test/"}))
    put(common, rel, json.dumps(body, ensure_ascii=False))
    rc, out, err = run(capsys, "register", "check", "shared")
    assert rc != 0 and check_id in out + err


def test_A_すでにある共通の台帳へ打ち直すと無い種類だけを足し前の物は変えない(env, capsys):
    common = env / "shared"
    common.mkdir()
    rc, _v, err = jrun(capsys, "register", "add", "shared", "--path", common, "--common", "--by", "masaru")
    assert rc == 0, err
    # 段 10 までの共通の台帳の形（備え付けは 3 つ）にする。運営者の宣言は持ち主が直していた
    for kind in ("source", "evidence_line", "pledge"):
        (common / f".thth/kinds/{kind}.json").unlink()
    (common / ".thth/kinds/operator.json").write_text('{"schema": "thth.kind/1", "kind": "operator", '
                                                      '"place": "operator/*.json", "format": "json", '
                                                      '"records": "file", "id": {"name": true}}\n')
    put(common, "correspondence/kakishibu.json", json.dumps(
        {"media_a": "a", "id_a": "point:1", "media_b": "b", "id_b": "point:2", "relation": "same"}))
    before = {p: p.read_bytes() for p in common.rglob("*") if p.is_file()}
    rc, v, err = jrun(capsys, "register", "add", "shared", "--path", common, "--common", "--by", "masaru")
    assert rc == 0, err
    assert v["again"] and sorted(v["builtin_kinds"]) == ["evidence_line", "pledge", "source"]
    assert v["kept_different"] == ["operator"] and sorted(v["already"]) == ["account", "correspondence"]
    for path, data in before.items():
        assert path.read_bytes() == data          # 前の宣言と記録は 1 バイトも変わらない
    # もう一度打つと、足す物は無い
    rc, v, err = jrun(capsys, "register", "add", "shared", "--path", common, "--common", "--by", "masaru")
    assert rc == 0 and v["builtin_kinds"] == [], err
    # 違うフォルダで打てば、今までどおり 2 つめの共通の台帳として断る
    other = env / "other"
    other.mkdir()
    rc, v, err = jrun(capsys, "register", "add", "shared", "--path", other, "--common", "--by", "masaru")
    assert rc != 0 and "register_common_exists" in err + json.dumps(v or {})


def common_ledger(env) -> pathlib.Path:
    """共通の台帳（器の宣言は備え付けの写し）。出典 1 つと根拠の 1 行 1 つ。"""
    common = env / "common"
    (common / ".thth/kinds").mkdir(parents=True)
    for kind in ("source", "evidence_line"):
        shutil.copy(BUILTIN / f"{kind}.json", common / f".thth/kinds/{kind}.json")
    put(common, "source/src-behrens.json", json.dumps(
        {"title": "Bitter taste receptors", "doi": "10.1007/s00018-006-6113-8", "published_access": "open"}) + "\n")
    put(common, "evidence_line/el-behrens-1.json", json.dumps(
        {"source_id": "src-behrens", "locator": "abstract", "finding": "苦味受容体の総説"}, ensure_ascii=False) + "\n")
    return common


def common_media(env, name) -> pathlib.Path:
    """共通の出典を `common:<番号>` で指すメディア（出し口は共通の出典と根拠の 1 行を採る）。"""
    files = {"data/claims.csv": "id,text\nclm-bitter,苦味の受容体は 25 種\n",
             "data/evidence.csv": "id,claim_id,source_id,locator,finding\n"
                                  "ev-bitter,clm-bitter,common:src-behrens,abstract,総説\n"}
    kinds = {k: KINDS[k] for k in ("claim", "evidence")}
    decl = site_outlets(kinds=("claim", "evidence"), common=["source", "evidence_line"])
    return media(env, name, decl=decl, kinds=kinds, files=files)


def test_A_メディアの記録が共通の記録を指しても先の無い指しにならず版は採る判断が持つ(env, capsys):
    common = common_ledger(env)
    nig = common_media(env, "nig")
    rc, v, err = jrun(capsys, "register", "health", nig, "--common", common)
    assert rc == 0, err
    assert v["groups"]["net"]["dangling"]["count"] == 0
    untaken = v["groups"]["common"]["pointers_untaken"]
    assert untaken["count"] == 1 and untaken["missing_in_common"] == 0
    assert untaken["examples"][0] == {"from": "evidence:ev-bitter", "to": "common:source:src-behrens",
                                      "field": "source_id"}
    # 採ると、指しは採った版を持つ（採っていない指しは 0）
    R.take(str(nig), str(common), ["source:src-behrens", "evidence_line:el-behrens-1"], by="masaru", at=BASE_AT)
    R.baseline(str(nig), "site", by="masaru", at=BASE_AT)
    out = env / "out"
    got = accept(nig, "site", out, common=common)
    assert (out / "common/source/src-behrens.json").read_bytes() == (common / "source/src-behrens.json").read_bytes()
    assert (out / "common/evidence_line/el-behrens-1.json").is_file()
    seal = json.loads(pathlib.Path(R.seal_path(str(nig), "site")).read_text())["body"]
    assert {"record": "common:source:src-behrens",
            "version": R.sha((common / "source/src-behrens.json").read_bytes())} in seal["inputs"]
    assert got["generation"] == 1
    rc, v, err = jrun(capsys, "register", "health", nig, "--common", common)
    assert v["groups"]["common"]["pointers_untaken"]["count"] == 0
    v = R.verify(str(nig), "site", str(out), NOW, no_previous=True, common_root=str(common))
    assert v["ok"], v


def test_A_共通の種類の並びの形が違えば断る(env):
    from thth.register import vessel as V
    for bad in ([], ["source", "source"], ["Source"], 3):
        led = media(env, f"bad{len(str(bad))}{abs(hash(str(bad))) % 1000}",
                    decl=site_outlets(common=bad))
        with pytest.raises(RegisterRefused):
            R.load_outlets(str(led), V.open_vessel(str(led)))


# ------------------------------------------------------------------ B. 訂正の 1 本の命令

def fix(capsys, led, ref, *sets, outlet="site", to=None, extra=()):
    argv = ["register", "fix", led, ref]
    for one in sets:
        argv += ["--set", one]
    argv += ["--reason-text", "原文の要旨と照らした", "--how", "abstract を読んだ", "--outlet", outlet,
             "--to", to, "--as-of", AT, "--by", "claude-v", *extra]
    return jrun(capsys, *argv)


def test_B_外れる0入る0新しい判断だけなら1本の命令で封まで進み確かめが緑(env, capsys):
    led = media(env, decl=site_outlets(fix_seal=True))
    out = sealed(env, led)
    site = out / "site"
    rc, v, err = fix(capsys, led, "evidence:ev-bitter", "finding=ヒトの苦味受容体（TAS2R）を総説", to=site)
    assert rc == 0 and v["sealed"], (v, err)
    named = [n["item"] for n in v["named"]]
    assert named == ["evidence:ev-bitter=fact"] and len(v["judgments"]) == 1
    assert v["outlets"]["site"]["generation"] == 2
    assert "TAS2R" in (site / "data/evidence.csv").read_text(encoding="utf-8")
    got = R.verify(str(led), "site", str(site), NOW, no_previous=True)
    assert got["ok"], got
    # 判断の記録は足すだけで、誰が・何で照らしたかが残る
    lines = [json.loads(x) for x in (led / ".thth/judgments.jsonl").read_text().splitlines()]
    last = lines[-1]
    assert last["by"] == "claude-v" and last["how"] == "abstract を読んだ" and last["target"]["record"] == "evidence:ev-bitter"


def test_B_宣言が許さなければ1段めで止め印でacceptできる(env, capsys):
    led = media(env)
    out = sealed(env, led)
    site = out / "site"
    before = (site / "data/evidence.csv").read_bytes()
    rc, v, err = fix(capsys, led, "evidence:ev-bitter", "finding=直した", to=site)
    assert rc == 1 and not v["sealed"], (v, err)
    assert v["outlets"]["site"]["stopped_because"] == ["fix_seal_not_declared"]
    assert (site / "data/evidence.csv").read_bytes() == before        # 封も材料もまだ
    rc, w, err = jrun(capsys, "register", "accept", led, "--outlet", "site", "--to", site, "--as-of", AT,
                      "--by", "masaru", "--confirm", v["digest"])
    assert rc == 0 and w["generation"] == 2, err


def test_B_ほかの判断が待っていれば封まで進まない(env, capsys):
    led = media(env, decl=site_outlets(fix_seal=True))
    out = sealed(env, led)
    R.judge(str(led), "claim:clm-other", "may_publish@site", by="masaru", at=AT, conclusion="good",
            reason_text="別の件")
    rc, v, err = fix(capsys, led, "evidence:ev-bitter", "finding=直した", to=out / "site")
    assert rc == 1 and "other_judgments" in v["outlets"]["site"]["stopped_because"], (v, err)


def jl(led) -> list:
    return [json.loads(x) for x in (led / ".thth/judgments.jsonl").read_text().splitlines() if x.strip()]


def test_B_ほかの記録の判断の無い変更は判断せず1段めで止める(env, capsys):
    # 段 11 の検収 危 1: 直していない別の記録の変更を、fix が自動で「よい」と判断して封まで進めていた
    led = media(env, decl=site_outlets(fix_seal=True))
    out = sealed(env, led)
    site = out / "site"
    before = (site / "data/claims.csv").read_bytes()
    A.row_set(str(led), "claim:clm-other", {"text": "渋みは味覚である（誤り）"}, by="someone", at=AT)
    rc, v, err = fix(capsys, led, "evidence:ev-bitter", "finding=直した", to=site)
    assert rc == 1 and not v["sealed"], (v, err)
    assert "leaving" in v["outlets"]["site"]["stopped_because"]
    other = [n for n in v["named"] if n["item"].startswith("claim:clm-other")]
    assert other and not other[0]["judged"] and other[0]["why"] == "not_this_fix"
    assert all(j["target"]["record"] != "claim:clm-other" for j in jl(led) if j["by"] == "claude-v")
    assert (site / "data/claims.csv").read_bytes() == before            # 封も材料もまだ


def test_B_何も変えないfixは断る(env, capsys):
    led = media(env, decl=site_outlets(fix_seal=True))
    out = sealed(env, led)
    A.row_set(str(led), "claim:clm-other", {"text": "誤り"}, by="someone", at=AT)
    n = len(jl(led))
    rc, v, err = fix(capsys, led, "evidence:ev-bitter", "finding=ヒトの苦味受容体を総説", to=out / "site")
    assert rc != 0 and "fix_no_change" in err + json.dumps(v or {}, ensure_ascii=False), (v, err)
    assert len(jl(led)) == n


def test_B_ほかの者のだめの判断に自動のよいを重ねない(env, capsys):
    led = media(env, decl=site_outlets(fix_seal=True))
    out = sealed(env, led)
    R.judge(str(led), "claim:clm-other", "may_publish@site", by="reviewer", at=AT, conclusion="bad",
            reason_text="誤りを見つけた")
    rc, v, err = fix(capsys, led, "evidence:ev-bitter", "finding=直した", to=out / "site")
    assert rc == 1 and not v["sealed"], (v, err)
    last = [j for j in jl(led) if j["target"]["record"] == "claim:clm-other"][-1]
    assert last["by"] == "reviewer" and last["answer"]["conclusion"] == "bad"


def test_B_直す記録そのもののだめの判断にも自動のよいを重ねない(env, capsys):
    led = media(env, decl=site_outlets(fix_seal=True))
    out = sealed(env, led)
    R.judge(str(led), "evidence:ev-bitter", "may_publish@site", by="reviewer", at=AT, conclusion="bad",
            reason_text="出典と合わない")
    rc, v, err = fix(capsys, led, "evidence:ev-bitter", "finding=直した", to=out / "site")
    assert rc == 1 and not v["sealed"], (v, err)
    mine = [n for n in v["named"] if n["item"].startswith("evidence:ev-bitter")]
    assert mine and not mine[0]["judged"] and mine[0]["why"] == "bad_judgment"
    assert not v["judgments"]
    last = [j for j in jl(led) if j["target"]["record"] == "evidence:ev-bitter"][-1]
    assert last["by"] == "reviewer"


def test_B_againstの先が無ければ書く前に断る(env, capsys):
    led = media(env, decl=site_outlets(fix_seal=True))
    out = sealed(env, led)
    before = (led / "data/evidence.csv").read_bytes()
    n = len(jl(led))
    rc, v, err = fix(capsys, led, "evidence:ev-bitter", "finding=直した", to=out / "site",
                     extra=("--against", "source:nosuch"))
    assert rc != 0 and "register_record_missing" in err + json.dumps(v or {}), (v, err)
    assert (led / "data/evidence.csv").read_bytes() == before and len(jl(led)) == n


def test_B_形の違う直しは書かずに断る(env, capsys):
    led = media(env)
    out = sealed(env, led)
    before = (led / "data/evidence.csv").read_bytes()
    rc, v, err = fix(capsys, led, "evidence:ev-bitter", "nosuch=1", to=out / "site")
    assert rc != 0
    assert (led / "data/evidence.csv").read_bytes() == before


def test_B_封まで進みうるのに鍵が使えなければ書く前に断る(env, capsys, monkeypatch):
    led = media(env, decl=site_outlets(fix_seal=True))
    out = sealed(env, led)
    before = (led / "data/evidence.csv").read_bytes(), (led / ".thth/judgments.jsonl").read_bytes()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(env / "no-key-here"))
    rc, v, err = fix(capsys, led, "evidence:ev-bitter", "finding=直した", to=out / "site")
    assert rc != 0 and "register_seal_" in err + json.dumps(v or {})   # 鍵が無ければ封も確かめられない
    assert ((led / "data/evidence.csv").read_bytes(), (led / ".thth/judgments.jsonl").read_bytes()) == before


def test_B_鍵の置き場が台帳の中なら書く前に断る(env, capsys, monkeypatch):
    led = media(env, decl=site_outlets(fix_seal=True))
    out = sealed(env, led)
    shutil.copytree(env / "config", led / "cfg")        # 鍵の置き場を台帳の中へ（こぴちゃの写しで踏んだ形）
    monkeypatch.setenv("XDG_CONFIG_HOME", str(led / "cfg"))
    before = (led / "data/evidence.csv").read_bytes(), (led / ".thth/judgments.jsonl").read_bytes()
    rc, v, err = fix(capsys, led, "evidence:ev-bitter", "finding=直した", to=out / "site")
    assert rc != 0 and "inside_register" in err + json.dumps(v or {})
    assert ((led / "data/evidence.csv").read_bytes(), (led / ".thth/judgments.jsonl").read_bytes()) == before


# ------------------------------------------------------------------ C. --outlet all

def shared_outlets() -> dict:
    return {"schema": "thth.outlets/1", "outlets": {
        "site": {"audience": "reader", "kinds": ["claim", "source"], "fact": [{"question": "may_publish@site"}],
                 "dest": "."},
        "tools": {"audience": "internal", "kinds": ["evidence"], "dest": ".", "dest_shared_with": ["site"]}}}


def test_C_出し口の全部を1回で受け入れ確かめ比べる(env, capsys):
    led = media(env, decl=shared_outlets())
    R.baseline(str(led), "site", by="masaru", at=BASE_AT)
    repo = env / "repo"
    rc, v, err = jrun(capsys, "register", "accept", led, "--outlet", "all", "--to", repo, "--as-of", AT,
                      "--by", "masaru")
    assert rc == 1 and v["stage"] == 1 and set(v["outlets"]) == {"site", "tools"}, err
    rc, w, err = jrun(capsys, "register", "accept", led, "--outlet", "all", "--to", repo, "--as-of", AT,
                      "--by", "masaru", "--confirm", v["digest"])
    assert rc == 0 and w["ok"] and set(w["outlets"]) == {"site", "tools"}, err
    assert (repo / "data/evidence.csv").read_bytes() == (led / "data/evidence.csv").read_bytes()
    pub = env / "pub"
    pub.mkdir()
    rc, x, err = jrun(capsys, "register", "verify", led, "--outlet", "all", "--material", repo, "--now", NOW,
                      "--no-previous", "--publish", pub)
    assert rc == 0 and x["ok"] and all(o["ok"] for o in x["outlets"].values()), err
    assert (pub / "thth-seal.site.json").is_file() and (pub / "thth-seal.tools.json").is_file()
    rc, y, err = jrun(capsys, "register", "export", led, "--outlet", "all", "--to", repo, "--as-of", AT, "--check")
    assert rc == 0 and y["ok"], err
    # 次の回は --previous のフォルダから出し口ごとの配る物を読む
    rc, x, err = jrun(capsys, "register", "verify", led, "--outlet", "all", "--material", repo, "--now", NOW,
                      "--previous", pub)
    assert rc == 0 and x["ok"], err
    # 1 つの出し口だけ赤（内の道具の材料を書き換える）でも、全体が赤
    (repo / "data/evidence.csv").write_text(EVIDENCE + "ev-x,clm-other,src-wang,p.9,足した\n", encoding="utf-8")
    rc, x, err = jrun(capsys, "register", "verify", led, "--outlet", "all", "--material", repo, "--now", NOW,
                      "--no-previous")
    assert rc == 1 and not x["ok"] and x["outlets"]["site"]["ok"] and not x["outlets"]["tools"]["ok"]
    rc, y, err = jrun(capsys, "register", "export", led, "--outlet", "all", "--to", repo, "--as-of", AT, "--check")
    assert rc == 1 and not y["ok"] and y["outlets"]["tools"]["found"] == ["differs"]


def test_C_確かめの印が違えば何も封をしない(env, capsys):
    led = media(env, decl=shared_outlets())
    R.baseline(str(led), "site", by="masaru", at=BASE_AT)
    repo = env / "repo"
    rc, v, err = jrun(capsys, "register", "accept", led, "--outlet", "all", "--to", repo, "--as-of", AT,
                      "--by", "masaru", "--confirm", "0" * 16)
    assert rc != 0
    assert R.read_seal(str(led), "site") is None and R.read_seal(str(led), "tools") is None


def test_C_書き出し先を言わない出し口があればallは断る(env, capsys):
    decl = site_outlets()
    del decl["outlets"]["site"]["dest"]
    led = media(env, decl=decl)
    R.baseline(str(led), "site", by="masaru", at=BASE_AT)
    rc, v, err = jrun(capsys, "register", "accept", led, "--outlet", "all", "--to", env / "repo", "--as-of", AT,
                      "--by", "masaru")
    assert rc != 0 and "outlet_all_dest" in err + json.dumps(v or {})


def test_C_allという名前の出し口は宣言できない(env):
    from thth.register import vessel as V
    decl = {"schema": "thth.outlets/1", "outlets": {"all": site_outlets()["outlets"]["site"]}}
    led = media(env, decl=decl)
    with pytest.raises(RegisterRefused):
        R.load_outlets(str(led), V.open_vessel(str(led)))


# ------------------------------------------------------------------ D. こぴちゃの報告 #7899 の 3 つ

def test_D1_export_checkは違いがあれば0以外で終わり同じなら0(env, capsys):
    led = media(env)
    out = sealed(env, led)
    site = out / "site"
    rc, v, err = jrun(capsys, "register", "export", led, "--outlet", "site", "--to", site, "--as-of", AT, "--check")
    assert rc == 0 and v["found"] == [], err
    (site / "data/claims.csv").write_text(CLAIMS + "clm-x,手で足した\n", encoding="utf-8")
    rc, v, err = jrun(capsys, "register", "export", led, "--outlet", "site", "--to", site, "--as-of", AT, "--check")
    assert rc == 1 and v["found"] == ["differs"], err
    rc, out_text, err = run(capsys, "register", "export", led, "--outlet", "site", "--to", site, "--as-of", AT,
                            "--check")
    assert rc == 1 and "違いがあります" in out_text
    put(site, "data/old.csv", "id\n")       # 一覧に無い旧い材料
    rc, v, err = jrun(capsys, "register", "export", led, "--outlet", "site", "--to", site, "--as-of", AT, "--check")
    assert rc == 1 and "stale" in v["found"]


def test_D2_THTH_ROOTのフォルダが無くても記録の置き場で止まらない(env, capsys, monkeypatch):
    root = env / "fresh-root"
    monkeypatch.setenv("THTH_ROOT", str(root))
    assert not root.exists()
    common = env / "shared"
    common.mkdir()
    rc, v, err = jrun(capsys, "register", "add", "shared", "--path", common, "--common", "--by", "masaru")
    assert rc == 0, err
    assert root.is_dir() and (os.stat(root).st_mode & 0o777) == 0o700


def test_D2_作れない所では今までどおり断る(env, capsys, monkeypatch):
    blocker = env / "a-file"
    blocker.write_text("x")
    monkeypatch.setenv("THTH_ROOT", str(blocker / "root"))
    common = env / "shared"
    common.mkdir()
    rc, v, err = jrun(capsys, "register", "add", "shared", "--path", common, "--common", "--by", "masaru")
    assert rc != 0


def objects(led) -> list:
    base = led / ".thth/kept/objects"
    return sorted(p for p in base.rglob("*") if p.is_file()) if base.exists() else []


def big_media(env, name, git: bool):
    files = {"data/claims.csv": CLAIMS, "data/evidence.csv": EVIDENCE, "data/sources.csv": SOURCES}
    for n in range(20):
        files[f"data/extra/t{n:02d}.csv"] = f"id,text\nx{n},{n}\n"
    kinds = dict(KINDS)
    kinds["extra"] = {"schema": "thth.kind/1", "kind": "extra", "place": "data/extra/*.csv", "format": "csv",
                      "id": {"field": "id"}}
    repo = env / name
    repo.mkdir()
    if git:
        (repo / ".git").mkdir()     # git の作業木（器は .git を見るだけで git を呼ばない）
    led = media(repo, "register", kinds=kinds, files=files)
    return led


def test_D3_gitの中の台帳ではrow_setが直した枚だけを控える(env):
    led = big_media(env, "repo", git=True)
    A.row_set(str(led), "claim:clm-bitter", {"text": "苦味の受容体は約 25 種"}, by="masaru", at=AT)
    # 直した枚の前と後と、宣言（種類 5 つ）だけ（前は台帳の 23 枚の全部も控えた）
    assert len(objects(led)) == 2 + 5
    assert not K.os.path.exists(K.object_path(str(led), K.sha256((led / "data/extra/t05.csv").read_bytes())))
    A.row_set(str(led), "claim:clm-other", {"text": "渋みは主に触覚"}, by="masaru", at=AT)
    assert len(objects(led)) == 3 + 5
    # 控えを読む所は壊れない: 控えの名どうしの差・今との差
    v = A.diff(str(led), "first..now")
    assert v is not None
    snap = K.load_snapshot(str(led), "first")
    into = env / "restored"
    K.materialize(str(led), snap, str(into))   # 直さなかった枚は、今の枚から（sha256 が同じとき）
    assert (into / "data/extra/t05.csv").read_bytes() == (led / "data/extra/t05.csv").read_bytes()


def test_D3_gitの中の台帳でもrestore_atで前の版を取り出せる(env):
    # 段 11 の検収 軽 2: 直さなかった枚は控えに無いので、restore --at が断っていた（diff と同じく今の枚から読む）
    led = big_media(env, "repo", git=True)
    A.row_set(str(led), "claim:clm-bitter", {"text": "1"}, by="masaru", at=AT)
    A.row_set(str(led), "claim:clm-bitter", {"text": "2"}, by="masaru", at=AT)
    got = A.restore(str(led), str(env / "restored"), at_snapshot="first")
    assert got["files"]
    assert (env / "restored/data/extra/t05.csv").read_bytes() == (led / "data/extra/t05.csv").read_bytes()
    assert "clm-bitter,1\n" in (env / "restored/data/claims.csv").read_text(encoding="utf-8")   # 1 回めの直しの後
    # 今の枚が違えば今までどおり断る
    (led / "data/extra/t05.csv").write_text("id,text\nx5,changed\n", encoding="utf-8")
    with pytest.raises(RegisterRefused) as exc:
        A.restore(str(led), str(env / "restored2"), at_snapshot="first")
    assert exc.value.code == "register_kept_missing"


def test_D3_gitの外の台帳では今までどおり全部を控える(env):
    led = big_media(env, "plain", git=False)
    A.row_set(str(led), "claim:clm-bitter", {"text": "苦味の受容体は約 25 種"}, by="masaru", at=AT)
    assert len(objects(led)) >= 24


def test_D3_控えに無く今の枚も違えば取り出せないと言う(env):
    led = big_media(env, "repo", git=True)
    A.row_set(str(led), "claim:clm-bitter", {"text": "1"}, by="masaru", at=AT)
    (led / "data/extra/t05.csv").write_text("id,text\nx5,changed\n", encoding="utf-8")   # git で直した（器の外）
    snap = K.load_snapshot(str(led), "first")
    with pytest.raises(RegisterRefused) as exc:
        K.materialize(str(led), snap, str(env / "restored"))
    assert exc.value.code == "register_kept_missing"


# ------------------------------------------------------------------ E. 見出しだけの表

def test_E_記録0の表も見出しのまま書き出し受け入れの2段めが止まらない(env):
    led = media(env, decl=site_outlets(kinds=("claim", "evidence", "source", "love")))
    R.baseline(str(led), "site", by="masaru", at=BASE_AT)
    repo = env / "repo"
    put(repo, "data/loves/records.csv", LOVES)      # repo の根に同じファイル（今の build が読む）
    plan = R.accept_plan(str(led), "site", str(repo), AT)
    assert "data/loves/records.csv" in plan["body"]["outputs"] and plan["stale"] == []
    got = R.accept(str(led), "site", str(repo), AT, confirm=plan["digest"], at=AT)
    assert got["generation"] == 1
    assert (repo / "data/loves/records.csv").read_bytes() == LOVES.encode()
    v = R.verify(str(led), "site", str(repo), NOW, no_previous=True)
    assert v["ok"], v
    # 見出しを変えれば、封のあとの違いとして赤
    (repo / "data/loves/records.csv").write_text("id,name,extra\n", encoding="utf-8")
    v = R.verify(str(led), "site", str(repo), NOW, no_previous=True)
    assert not v["ok"] and "material_mismatch" in words(v)


# ------------------------------------------------------------------ F. 影響の候補

def test_F_根拠だけを直すと支える主張が影響の候補に出る_判断は無効にしない(env, capsys):
    led = media(env)
    out = sealed(env, led)
    site = out / "site"
    A.row_set(str(led), "evidence:ev-bitter", {"finding": "直した"}, by="masaru", at=AT)
    v = R.verify(str(led), "site", str(site), NOW, no_previous=True)
    assert not v["ok"]
    cands = {c["record"] for c in v["impact"]["candidates"]}
    assert "claim:clm-bitter" in cands and "source:src-behrens" in cands
    assert "claim:clm-other" not in cands
    assert v["impact"]["changed"] == ["evidence:ev-bitter"]
    # 主張は候補に出るだけ（判断は効いたまま・事実として使える）
    u = R.use(str(led), "claim:clm-bitter", "site", "fact", AT)
    assert u["usable"]
    # health も同じ候補を出す
    rc, h, err = jrun(capsys, "register", "health", led)
    item = h["groups"]["seal"]["impact_candidates"]
    assert "claim:clm-bitter" in {c["record"] for c in item["examples"]}


def test_F_fixも影響の候補を出す(env, capsys):
    led = media(env)
    out = sealed(env, led)
    rc, v, err = fix(capsys, led, "evidence:ev-bitter", "finding=直した", to=out / "site")
    assert rc == 1, err      # 宣言が封を許していないので 1 段めで止まる（候補は出す）
    assert "claim:clm-bitter" in {c["record"] for c in v["impact"]["candidates"]}


def test_F_共通の出典を直すと2つのメディアの根拠と主張が候補に出る(env, capsys):
    common = common_ledger(env)
    medias = [common_media(env, "kop"), common_media(env, "nig")]
    for led in medias:
        R.take(str(led), str(common), ["source:src-behrens"], by="masaru", at=BASE_AT)
        R.baseline(str(led), "site", by="masaru", at=BASE_AT)
        accept(led, "site", env / f"{led.name}-out", common=common)
    (common / "source/src-behrens.json").write_text(json.dumps(
        {"title": "Bitter taste receptors and human bitter taste perception",
         "doi": "10.1007/s00018-006-6113-8", "published_access": "abstract_only"}) + "\n")
    for led in medias:
        # 採る前の確かめ・健康: 採り入れ待ちの共通の記録から候補
        v = R.verify(str(led), "site", str(env / f"{led.name}-out"), NOW, no_previous=True, common_root=str(common))
        cands = {c["record"] for c in v["impact"]["candidates"]}
        assert {"evidence:ev-bitter", "claim:clm-bitter"} <= cands, v["impact"]
        rc, h, err = jrun(capsys, "register", "health", led, "--common", common)
        assert {"evidence:ev-bitter", "claim:clm-bitter"} <= {
            c["record"] for c in h["groups"]["seal"]["impact_candidates"]["examples"]}
        # 採ると、採った版の進んだ共通の記録から候補
        t = R.take(str(led), str(common), [], by="masaru", at=AT)
        assert t["taken"] and {"evidence:ev-bitter", "claim:clm-bitter"} <= {
            c["record"] for c in t["impact"]["candidates"]}
