"""台帳の器 段 8 の健康・共通の出さない欄の柵・姉妹リンクの候補（指示 `docs/指示_台帳の器_段8_健康と複数のメディア_2026-11-14.md`
§1 の「済み」の線の 1・3・§2・§3 の「共通の出さない欄」と「姉妹リンクの候補」）。

見本は `tests/fixtures/register_route/`（こぴちゃ・にがみラボ・共通の台帳の小さな写し）。秘密の鍵は試験ごとの一時の場所に作る。

- 健康: 事を 1 つ起こすたびに、対応する項目の数が 1 増える
- 読むだけ: `health`（引数の組み合わせの全部）と `sisters` の前後で、台帳のフォルダと THTH の側の控えの全ファイルの
  sha256 が同じ。判断の記録に 1 行も足されない
- 共通の出さない欄: 共通の `operator` の全部の欄と、共通の出典の出さない欄 1 つが、2 つのメディアの材料・封・
  材料の控え・配る物のどこにも出ない（試験が自分で全ファイルを読んで字を探す）。メディアの側の宣言で外せない
- 姉妹リンク: `same` の対だけ・相手が読者の出し口で使えない対は出ない・相手の台帳が無ければ「確かめられない」
- 宣言の攻め（`shared_private` に数・null・巨大な並び）で例外が漏れない
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil

import pytest

from thth import cli
from thth.register import ledger as L
from thth.register import route as R
from thth.register import seal as S
from thth.register.errors import RegisterRefused

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "register_route"
SEALED_AT = "2027-03-20T10:00:00+09:00"
AS_OF = "2027-03-25T10:00:00+09:00"


@pytest.fixture
def env(tmp_path, monkeypatch):
    for key, sub in (("THTH_ROOT", "root"), ("HOME", "home"), ("XDG_CONFIG_HOME", "config")):
        (tmp_path / sub).mkdir()
        monkeypatch.setenv(key, str(tmp_path / sub))
    monkeypatch.delenv(S.ENV_PUBLIC, raising=False)
    monkeypatch.delenv(L.REGISTERS_DIR_ENV, raising=False)
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


def enroll(led: pathlib.Path, name: str, project: str) -> None:
    L.write_new(name, L.build(name, project=project, path=str(led), kind="media"))


def run(capsys, *argv):
    rc = cli.main([str(a) for a in argv])
    out, err = capsys.readouterr()
    return rc, out, err


def jrun(capsys, *argv):
    rc, out, err = run(capsys, *argv, "--json")
    return rc, (json.loads(out) if out.strip() else None), err


def health(capsys, led, *extra):
    rc, v, err = jrun(capsys, "register", "health", led, "--as-of", AS_OF, *extra)
    assert rc == 0, err
    return v


def count(v, group, item):
    got = v["groups"][group][item]
    assert got["count"] is not None, got
    return got["count"]


def judge(capsys, led, *argv):
    rc, v, err = jrun(capsys, "register", "judge", led, *argv, "--by", "masaru")
    assert rc == 0, err
    return v


def accept(capsys, led, outlet, to, *extra):
    rc, v, err = jrun(capsys, "register", "accept", led, "--outlet", outlet, "--to", to, "--as-of", SEALED_AT,
                      "--by", "masaru", *extra)
    assert rc == 1 and v["stage"] == 1, err
    rc, v, err = jrun(capsys, "register", "accept", led, "--outlet", outlet, "--to", to, "--as-of", SEALED_AT,
                      "--by", "masaru", "--confirm", v["digest"], *extra)
    assert rc == 0, err
    return v


def edit(path: pathlib.Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


CLAIMS = "data/knowledge/claims.csv"


# ------------------------------------------------------------------ 健康: 事を 1 つ起こすと 1 増える

def test_中身を直すと古くなった判断が1増える(env, capsys):
    kop = ledger(env, "kopicha")
    judge(capsys, kop, "claim:c-origin-brazil-024", "--question", "says_so", "--word", "contested")
    before = count(health(capsys, kop), "judgment", "stale")
    edit(kop / CLAIMS, "ブラジルの豆はナッツの香りがするとされる", "ブラジルの豆はナッツの香りがする")
    assert count(health(capsys, kop), "judgment", "stale") == before + 1


def test_状態だけ書いた版を足すと観測しか無い版が1増える(env, capsys):
    kop = ledger(env, "kopicha")
    before = health(capsys, kop)
    with open(kop / CLAIMS, "a", encoding="utf-8") as f:
        f.write("c-new-1,s,p,,x,新しい主張,quantity,,supported,,,,\n")
    after = health(capsys, kop)
    assert count(after, "judgment", "observed_only") == count(before, "judgment", "observed_only") + 1
    assert "claim:c-new-1" in [e["record"] for e in after["groups"]["judgment"]["observed_only"]["examples"]]


def test_封のあとに判断を足すと受け入れ待ちが1増える(env, capsys):
    kop = ledger(env, "kopicha")
    judge(capsys, kop, "claim:c-term-tea-grade-ctc-res-milktea1-24", "--question", "says_so", "--word", "supported")
    accept(capsys, kop, "strict", env / "site")
    before = count(health(capsys, kop), "seal", "accept_pending")
    assert before == 0          # 封の中の判断は待っていない
    judge(capsys, kop, "claim:c-producer-selimbong-007", "--question", "says_so", "--word", "supported")
    after = health(capsys, kop)
    assert count(after, "seal", "accept_pending") == before + 1
    assert any(e.get("outlet") == "strict" for e in after["groups"]["seal"]["accept_pending"]["examples"])
    # 狭める判断（止める）は封を待たないので、受け入れ待ちに数えない
    judge(capsys, kop, "claim:c-origin-brazil-024", "--stop")
    assert count(health(capsys, kop), "seal", "accept_pending") == before + 1


def _with_common(env, capsys):
    common, kop = ledger(env, "common"), ledger(env, "kopicha")
    rc, v, err = jrun(capsys, "register", "take", kop, "--common", common, "--record", "source:fujimoto-2021",
                      "--by", "masaru")
    assert rc == 0, err
    accept(capsys, kop, "bib", env / "site", "--common", common)
    return common, kop


def test_共通の台帳で止めるとよそから来て効いている止めるが1増える(env, capsys):
    common, kop = _with_common(env, capsys)
    before = count(health(capsys, kop, "--common", common), "seal", "foreign_stops")
    judge(capsys, common, "source:fujimoto-2021", "--stop")
    after = health(capsys, kop, "--common", common)
    assert count(after, "seal", "foreign_stops") == before + 1
    # 共通の台帳を渡さなければ、数を作らずに「数えられない」と言う
    blind = health(capsys, kop)
    assert blind["groups"]["seal"]["foreign_stops"]["count"] is None
    assert blind["groups"]["seal"]["foreign_stops"]["uncountable"]


def test_共通の記録を直すと採り入れ待ちが1増える(env, capsys):
    common, kop = _with_common(env, capsys)
    before = count(health(capsys, kop, "--common", common), "common", "take_pending")
    edit(common / "sources/fujimoto-2021.json", "Food Chemistry 342:128228", "Food Chemistry")
    assert count(health(capsys, kop, "--common", common), "common", "take_pending") == before + 1


def test_いつまでを過ぎた日時と近い日時で見ると期限の項目が1増える(env, capsys):
    kop = ledger(env, "kopicha")
    judge(capsys, kop, "claim:c-variety-bourbon-brazil-01", "--question", "says_so", "--word", "supported",
          "--until", "2027-04-30")
    accept(capsys, kop, "strict", env / "site")

    def at(day):
        rc, v, err = jrun(capsys, "register", "health", kop, "--as-of", day)
        assert rc == 0, err
        return v["groups"]["deadline"]
    far, near, passed = at("2027-01-01"), at("2027-04-15"), at("2027-05-02")
    assert near["until_near"]["count"] == far["until_near"]["count"] + 1
    assert passed["until_passed"]["count"] == near["until_passed"]["count"] + 1
    assert passed["passed_in_last_generation"]["count"] == near["passed_in_last_generation"]["count"] + 1
    assert passed["passed_in_last_generation"]["examples"][0]["record"] == "claim:c-variety-bourbon-brazil-01"


def test_消す求めを起こすと消し終わっていない求めが1増える(env, capsys):
    kop = ledger(env, "kopicha")
    before = count(health(capsys, kop), "seal", "erase_unfinished")
    rc, v, err = jrun(capsys, "register", "erase", "request", kop, "claim:c-origin-brazil-024", "--why", "personal",
                      "--by", "masaru")
    assert rc == 0, err
    assert count(health(capsys, kop), "seal", "erase_unfinished") == before + 1


def test_whenに1件も当たらない検査を宣言すると当たらない検査が1増える(env, capsys):
    kop = ledger(env, "kopicha")
    before = count(health(capsys, kop), "rules", "unmatched_checks")
    path = kop / ".thth/kinds/claim.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["checks"] = [{"check": "required", "id": "abolished_rule", "fields": ["statement_ja"],
                       "when": {"claim_type": {"in": ["no_such_type"]}}},
                      {"check": "required", "id": "live_rule", "fields": ["statement_ja"]}]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    after = health(capsys, kop)
    assert count(after, "rules", "unmatched_checks") == before + 1
    hit = after["groups"]["rules"]["unmatched_checks"]["examples"][0]
    assert hit["id"] == "abolished_rule" and hit["kind"] == "claim" and hit["when"]


def test_先の版が変わった来歴の控えで見直す来歴の控えが1増える(env, capsys):
    kop = ledger(env, "kopicha")
    path = kop / ".thth/kinds/evidence.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["pointers"][0]["recorded_version"] = {"field": "claim_hash", "of": ["statement_ja"], "hash": "sha1:12"}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    claims = {}
    for line in (kop / CLAIMS).read_text(encoding="utf-8").splitlines()[1:]:
        cells = line.split(",")
        claims[cells[0]] = cells[5]
    lines = (kop / "data/knowledge/evidence.csv").read_text(encoding="utf-8").splitlines()
    out = [lines[0] + ",claim_hash"]
    for line in lines[1:]:
        claim = line.split(",")[1]
        out.append(line + "," + hashlib.sha1(claims[claim].encode("utf-8")).hexdigest()[:12])
    (kop / "data/knowledge/evidence.csv").write_text("\n".join(out) + "\n", encoding="utf-8")
    before = count(health(capsys, kop), "judgment", "provenance_review")
    assert before == 0
    edit(kop / CLAIMS, "CTC 茶葉の水浸出物は 17.3% とされる", "CTC 茶葉の水浸出物は 17.1% とされる")
    assert count(health(capsys, kop), "judgment", "provenance_review") == before + 1


def test_書き手だけの読みは取り込みの跡が無ければ数を作らない(env, capsys):
    kop = ledger(env, "kopicha")
    judge(capsys, kop, "claim:c-origin-brazil-024", "--question", "says_so", "--word", "contested")
    got = health(capsys, kop)["groups"]["judgment"]["self_check"]
    assert got["count"] is None and got["uncountable"]


def test_thinは線の薄い点を全部並べる_例は20件まで(env, capsys):
    kop = ledger(env, "kopicha")
    v = health(capsys, kop)
    thin = v["groups"]["net"]["thin"]
    assert thin["count"] >= 1 and len(thin["examples"]) <= 20 and "all" not in thin
    v = health(capsys, kop, "--thin")
    assert len(v["groups"]["net"]["thin"]["all"]) == v["groups"]["net"]["thin"]["count"]


def test_acrossは持ち主の台帳をまたいで同じDOIを並べる(env, capsys):
    kop, nig = ledger(env, "kopicha"), ledger(env, "nigami")
    enroll(kop, "kop", "kopicha")
    enroll(nig, "nig", "nigami")
    v = health(capsys, "kop", "--across")
    groups = v["across"]["same"]
    hit = [g for g in groups if g["key"] == "doi:10.1016/j.foodchem.2020.128228"]
    assert hit and {m["register"] for m in hit[0]["members"]} == {"kop", "nig"}


def test_allのjsonは台帳ごとに1行で群ごとの数だけ(env, capsys):
    kop, nig = ledger(env, "kopicha"), ledger(env, "nigami")
    enroll(kop, "kop", "kopicha")
    enroll(nig, "nig", "nigami")
    rc, out, err = run(capsys, "register", "health", "--all", "--json", "--as-of", AS_OF)
    assert rc == 0, err
    lines = [json.loads(line) for line in out.splitlines() if line.strip()]
    assert [line["register"] for line in lines] == ["kop", "nig"]
    for line in lines:
        assert set(line["groups"]) == {"net", "judgment", "seal", "deadline", "rules", "common"}
        assert all(isinstance(n, int) for n in line["groups"].values())
        assert "examples" not in json.dumps(line)


# ------------------------------------------------------------------ 読むだけ

def _digest_tree(*roots) -> dict:
    out = {}
    for top in roots:
        for base, _dirs, files in os.walk(top):
            for name in files:
                path = os.path.join(base, name)
                with open(path, "rb") as f:
                    out[path] = hashlib.sha256(f.read()).hexdigest()
    return out


def _sister_setup(env, capsys):
    common, kop, nig = ledger(env, "common"), ledger(env, "kopicha"), ledger(env, "nigami")
    # 対応の記録の種類（備え付けは別の書き手が作っている最中なので、見本では自分で宣言する）
    (common / ".thth/kinds/correspondence.json").write_text(json.dumps({
        "schema": "thth.kind/1", "kind": "correspondence", "place": "correspondence/*.json", "format": "json",
        "records": "file", "id": {"name": True}}), encoding="utf-8")
    (common / "correspondence").mkdir()
    pairs = {
        "c1": ("kopicha", "claim:c-term-tea-grade-ctc-res-milktea1-24", "nigami", "source:src-fujimoto-2021-coffee", "same"),
        "c2": ("kopicha", "claim:c-origin-brazil-024", "nigami", "source:src-fujimoto-2021-coffee", "related"),
        "c3": ("nigami", "source:src-missing", "kopicha", "claim:c-producer-selimbong-007", "same"),
        "c4": ("kopicha", "claim:c-variety-bourbon-brazil-01", "mmr", "credit:ap-nofield", "same"),
        "c5": ("kopicha", "claim:c-origin-brazil-024", "nigami", "source:src-stopped", "same"),
    }
    for name, (ma, ia, mb, ib, rel) in pairs.items():
        (common / "correspondence" / f"{name}.json").write_text(json.dumps(
            {"media_a": ma, "id_a": ia, "media_b": mb, "id_b": ib, "relation": rel}), encoding="utf-8")
    with open(nig / "data/sources.csv", "a", encoding="utf-8") as f:
        f.write("src-stopped,止めた出典,X,2020,journal_article,J,\n")
    (nig / ".thth/outlets.json").write_text(json.dumps({"schema": "thth.outlets/1", "outlets": {
        "bib": {"audience": "reader", "common": "source"},
        "sources": {"audience": "reader", "kinds": ["source"]}}}), encoding="utf-8")
    judge(capsys, nig, "source:src-stopped", "--stop")
    enroll(kop, "kop", "kopicha")
    enroll(nig, "nig", "nigami")
    return common, kop, nig


def test_姉妹リンクはsameで相手が読者の出し口で使える対だけ_相手が無ければ確かめられない(env, capsys):
    common, kop, nig = _sister_setup(env, capsys)
    rc, v, err = jrun(capsys, "register", "sisters", "kop", "--common", common, "--as-of", AS_OF)
    assert rc == 0, err
    got = {(c["mine"], c["partner"]["record"]) for c in v["candidates"]}
    assert got == {("claim:c-term-tea-grade-ctc-res-milktea1-24", "source:src-fujimoto-2021-coffee")}
    assert v["candidates"][0]["partner"]["outlets"] == ["sources"]
    assert [u["partner"]["media"] for u in v["unverifiable"]] == ["mmr"]
    left = {(x["correspondence"], x["why"]) for x in v["not_usable"]}
    assert ("correspondence:c3", "record_missing") in left
    assert ("correspondence:c5", "not_usable_for_readers") in left
    assert all(x["correspondence"] != "correspondence:c2" for x in v["candidates"] + v["not_usable"])


def test_健康と候補は読むだけ_台帳とTHTHの控えの全ファイルが同じ(env, capsys):
    common, kop, nig = _sister_setup(env, capsys)
    rc, v, err = jrun(capsys, "register", "take", kop, "--common", common, "--record", "source:fujimoto-2021",
                      "--by", "masaru")
    assert rc == 0, err
    accept(capsys, kop, "bib", env / "site", "--common", common)
    judge(capsys, kop, "claim:c-origin-brazil-024", "--question", "says_so", "--word", "contested")
    roots = (common, kop, nig, env / "root", env / "config", env / "home")
    before = _digest_tree(*roots)
    judgments_before = (kop / ".thth/judgments.jsonl").read_bytes()
    combos = [[], ["--thin"], ["--across"], ["--json"], ["--thin", "--across", "--json"],
              ["--common", common], ["--common", common, "--thin", "--across", "--json"],
              ["--as-of", "2030-01-01"]]
    for extra in combos:
        for target in ("kop", kop):
            rc, out, err = run(capsys, "register", "health", target, *extra)
            assert rc == 0, err
    for extra in ([], ["--json"], ["--common", common], ["--json", "--common", common]):
        rc, out, err = run(capsys, "register", "health", "--all", *extra)
        assert rc == 0, err
    for extra in ([], ["--json"]):
        rc, out, err = run(capsys, "register", "sisters", "kop", "--common", common, *extra)
        assert rc == 0, err
    assert _digest_tree(*roots) == before
    assert (kop / ".thth/judgments.jsonl").read_bytes() == judgments_before


# ------------------------------------------------------------------ 共通の出さない欄

SECRETS = ("OPNAME-ZETA", "op-contact-qq", "OPLABEL-KAPPA", "SECRET-MEMO-OMEGA")


def _private_setup(env, capsys, media_tries: bool):
    common, kop, nig = ledger(env, "common"), ledger(env, "kopicha"), ledger(env, "nigami")
    (common / ".thth/kinds/operator.json").write_text(json.dumps({
        "schema": "thth.kind/1", "kind": "operator", "place": "operator/*.json", "format": "json",
        "records": "file", "id": {"name": True}}), encoding="utf-8")
    (common / "operator").mkdir()
    (common / "operator/owner.json").write_text(json.dumps(
        {"name": "OPNAME-ZETA", "contact": "op-contact-qq@example.invalid", "label": "OPLABEL-KAPPA"}),
        encoding="utf-8")
    edit(common / "sources/fujimoto-2021.json", '"year": 2021,', '"year": 2021,\n "internal_memo": "SECRET-MEMO-OMEGA",')
    (common / ".thth/outlets.json").write_text(json.dumps({
        "schema": "thth.outlets/1", "outlets": {}, "shared_private": {"source": ["internal_memo"]}}), encoding="utf-8")
    for led in (kop, nig):
        data = json.loads((led / ".thth/outlets.json").read_text(encoding="utf-8"))
        data["outlets"]["op"] = {"audience": "reader", "common": "operator"}
        if media_tries:
            # メディアの側で外そうとする（共通の出さない欄を空にする・運営者を出してよいと言う）
            data["shared_private"] = {"source": [], "operator": []}
        (led / ".thth/outlets.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        rc, v, err = jrun(capsys, "register", "take", led, "--common", common, "--record", "source:fujimoto-2021",
                          "--record", "operator:owner", "--by", "masaru")
        assert rc == 0, err
    return common, kop, nig


@pytest.mark.parametrize("media_tries", [False, True])
def test_共通の出さない欄はどのメディアの材料にも封にも配る物にも出ない(env, capsys, media_tries):
    common, kop, nig = _private_setup(env, capsys, media_tries)
    outside = []
    for led, name in ((kop, "kop"), (nig, "nig")):
        for outlet in ("bib", "op"):
            site = env / f"{name}-{outlet}-site"
            accept(capsys, led, outlet, site, "--common", common)
            rc, v, err = jrun(capsys, "register", "export", led, "--outlet", outlet, "--to", site, "--as-of",
                              SEALED_AT, "--common", common)
            assert rc == 0, err
            pub = env / "pub" / name / f"thth-seal.{outlet}.json"
            pub.parent.mkdir(parents=True, exist_ok=True)
            v = R.verify(str(led), outlet, str(site), "2027-03-21", no_previous=True, publish=str(pub),
                         common_root=str(common))
            assert v["ok"], v
            # 共通の台帳を読めない CI でも、封のときに出さなかった物を広げたと読まない
            v = R.verify(str(led), outlet, str(site), "2027-03-21", no_previous=True)
            assert v["ok"], v
        outside.append(led)
    # 書誌の出してよい欄は出ている（柵が全部を落としたのではない）
    for name in ("kop", "nig"):
        text = (env / f"{name}-bib-site/common/sources/fujimoto-2021.json").read_text(encoding="utf-8")
        assert "Bitterness compounds" in text and "internal_memo" not in text
        assert not (env / f"{name}-op-site/common/operator").exists()
    hits = []
    for top in (kop, nig, *env.glob("*-site"), env / "pub", env / "root", env / "config", env / "home"):
        for base, _dirs, files in os.walk(top):
            for fname in files:
                data = (pathlib.Path(base) / fname).read_bytes()
                for word in SECRETS:
                    if word.encode("utf-8") in data:
                        hits.append((str(pathlib.Path(base) / fname), word))
    assert hits == []
    # 封の材料の控えには版と出力の sha256 だけ（運営者の記録は項目にも出力にも無い）
    for led in (kop, nig):
        body = json.loads(pathlib.Path(R.seal_path(str(led), "op")).read_text())["body"]
        assert body["items"] == [] and not [p for p in body["outputs"] if "operator" in p]


@pytest.mark.parametrize("bad", [5, None, "x", ["source"], {"source": 5}, {"source": None}, {"source": [1]},
                                 {"source": ["ok", "ok"]}, {"source": ["f%d" % i for i in range(300)]},
                                 {"Bad-Name": []}, {f"k{i}": [] for i in range(100)},
                                 {"source": ["x" * 70]}])
def test_shared_privateの宣言の攻めで例外が漏れない(env, capsys, bad):
    common, kop, nig = _private_setup(env, capsys, False)
    data = json.loads((common / ".thth/outlets.json").read_text(encoding="utf-8"))
    data["shared_private"] = bad
    (common / ".thth/outlets.json").write_text(json.dumps(data), encoding="utf-8")
    rc, v, err = jrun(capsys, "register", "accept", kop, "--outlet", "bib", "--to", env / "s", "--as-of", SEALED_AT,
                      "--by", "masaru", "--common", common)
    if bad is None:
        # null は書いていないのと同じ（出し口の宣言のほかの欄と同じ読み方）。運営者の既定は残る
        assert rc == 1 and v["stage"] == 1, err
        assert R.shared_private(R.open_common(str(common))) == {"operator": "*"}
    else:
        assert rc != 0 and v["code"] == "register_common_unusable", (v, err)
        with pytest.raises(RegisterRefused):
            R.shared_private(R.open_common(str(common)))
    rc, v, err = jrun(capsys, "register", "health", kop, "--common", common)
    assert rc in (0, 1) and v is not None
