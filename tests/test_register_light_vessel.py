"""5.22.0 の監査の軽い点（台帳の器）を 5.23.0 で直した物の試験。

- fix: 同じ記録に先にほかの者の判断の無い直しがあれば名指しし、封へは進めない。
- 影響の候補: 根拠の記録を消したとき、支えていた主張が候補に出る。
- `common:<番号>` の先が無い指しは、受け入れで断り、確かめで赤。
- `accept --outlet all` は、どれかの出し口が断れば何も封をしない。
- 見出しだけの表: 出し口の外の種類の置き場にも当たる枚は書き出さない。

見本は CSV と JSON だけ（PyYAML の無い箱でも走る）。
"""
from __future__ import annotations

import json
import pathlib

from tests.test_register_stage11 import (AT, BASE_AT, KINDS, NOW, common_ledger, common_media, env, fix,  # noqa: F401
                                         jrun, media, put, sealed, shared_outlets, site_outlets)
from thth.register import adopt as A
from thth.register import route as R
from thth.register import seal as S
from thth.register.errors import RegisterRefused


def test_先に判断の無い直しがあればfixは名指しして封へ進まない(env, capsys):
    led = media(env, decl=site_outlets(fix_seal=True))
    out = sealed(env, led)
    # ほかの者が表だけ直して判断していない
    A.row_set(str(led), "evidence:ev-bitter", {"locator": "p.2"}, by="someone", at=AT)
    rc, v, err = fix(capsys, led, "evidence:ev-bitter", "finding=ヒトの苦味受容体（TAS2R）を総説", to=out / "site")
    assert rc == 1 and not v["sealed"], (v, err)
    assert v["prior_unjudged"] == [{"outlet": "site", "item": "evidence:ev-bitter=fact"}]
    assert "prior_unjudged_change" in v["outlets"]["site"]["stopped_because"]


def test_根拠の記録を消すと支えていた主張が影響の候補に出る(env, capsys):
    led = media(env)
    out = sealed(env, led)
    ev = led / "data/evidence.csv"
    ev.write_text("\n".join(line for line in ev.read_text(encoding="utf-8").splitlines()
                            if not line.startswith("ev-bitter")) + "\n", encoding="utf-8")
    v = R.verify(str(led), "site", str(out / "site"), NOW, no_previous=True)
    records = {c["record"] for c in v["impact"]["candidates"]}
    assert "claim:clm-bitter" in records, v["impact"]
    assert "evidence:ev-bitter" in v["impact"]["changed"]


def test_commonの先が無い指しは受け入れで断り確かめで赤(env, capsys):
    common = common_ledger(env)
    nig = common_media(env, "nig")
    R.take(str(nig), str(common), ["source:src-behrens", "evidence_line:el-behrens-1"], by="masaru", at=BASE_AT)
    R.baseline(str(nig), "site", by="masaru", at=BASE_AT)
    out = env / "out"
    plan = R.accept_plan(str(nig), "site", str(out), AT, common_root=str(common))
    R.accept(str(nig), "site", str(out), AT, confirm=plan["digest"], at=AT, common_root=str(common))
    assert R.verify(str(nig), "site", str(out), NOW, no_previous=True, common_root=str(common))["ok"]
    # 番号の取り違え（共通の台帳に無い番号）
    ev = nig / "data/evidence.csv"
    ev.write_text(ev.read_text(encoding="utf-8").replace("common:src-behrens", "common:src-nobody"), encoding="utf-8")
    v = R.verify(str(nig), "site", str(out), NOW, no_previous=True, common_root=str(common))
    assert not v["ok"] and "common_pointer_missing" in {p["word"] for p in v["problems"]}, v
    rc, w, err = jrun(capsys, "register", "accept", nig, "--outlet", "site", "--to", out, "--as-of", AT,
                      "--by", "masaru", "--common", common)
    assert rc == 2 and w["code"] == "register_record_missing" and w["detail"] == "common_pointer", (w, err)


def test_accept_allはどれかの出し口が断れば何も封をしない(env, capsys, monkeypatch):
    led = media(env, decl=shared_outlets())
    R.baseline(str(led), "site", by="masaru", at=BASE_AT)
    repo = env / "repo"
    rc, v, err = jrun(capsys, "register", "accept", led, "--outlet", "all", "--to", repo, "--as-of", AT,
                      "--by", "masaru")
    assert rc == 1, err
    real, calls = S.sign, []

    def sign(*a, **k):
        calls.append(1)
        if len(calls) == 2:      # 2 つめの出し口の署名で鍵が使えない
            raise RegisterRefused("register_seal_key_missing")
        return real(*a, **k)
    monkeypatch.setattr(S, "sign", sign)
    rc, w, err = jrun(capsys, "register", "accept", led, "--outlet", "all", "--to", repo, "--as-of", AT,
                      "--by", "masaru", "--confirm", v["digest"])
    assert rc == 1 and not w["ok"], (w, err)
    for name in ("site", "tools"):
        assert not pathlib.Path(R.seal_path(str(led), name)).exists(), name
    assert not repo.exists() or not any(repo.rglob("*.csv"))


def test_見出しだけの表は出し口の外の種類の置き場に当たる枚を書かない(env, capsys):
    kinds = {k: dict(KINDS[k]) for k in ("claim", "evidence", "source")}
    kinds["claim"]["place"] = "data/claims*.csv"
    kinds["love"] = {"schema": "thth.kind/1", "kind": "love", "place": "data/claims-loves.csv", "format": "csv",
                     "id": {"field": "id"}}
    files = {"data/claims.csv": "id,text\nclm-bitter,苦味\n", "data/claims-loves.csv": "id,name\n",
             "data/evidence.csv": "id,claim_id,source_id,locator,finding\n",
             "data/sources.csv": "id,title,doi\n"}
    led = media(env, kinds=kinds, files=files, decl=site_outlets(kinds=("claim",)))
    R.baseline(str(led), "site", by="masaru", at=BASE_AT)
    out = env / "out"
    plan = R.accept_plan(str(led), "site", str(out), AT)
    R.accept(str(led), "site", str(out), AT, confirm=plan["digest"], at=AT)
    assert (out / "data/claims.csv").is_file()
    assert not (out / "data/claims-loves.csv").exists()
