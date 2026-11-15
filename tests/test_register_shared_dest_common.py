"""台帳の器: 書き出し先を分け合う出し口（段 10 の `dest_shared_with`）でも、読者の出し口が `common` を持てる（5.23.0）。

こぴちゃと NIGAMI は site（読者）と tools（内）が repo の根を分け合う。読者の site に `common` を足すと、共通の台帳の
出典を `common/` に書き出す。柵: `common/*` が相手（内）の種類の置き場の型と重なれば今までどおり断る。内の出し口が
`common` を持つのは今までどおり断る（要るまで開けない）。

見本は CSV と JSON だけ（PyYAML の無い箱でも走る）。
"""
from __future__ import annotations

import json

from tests.test_register_stage11 import (AT, BASE_AT, KINDS, NOW, common_ledger, env, jrun, media)  # noqa: F401
from thth.register import route as R


def shared_common_outlets(tools_kinds=("evidence",), tools_common=None) -> dict:
    tools = {"audience": "internal", "kinds": list(tools_kinds), "dest": ".", "dest_shared_with": ["site"]}
    if tools_common:
        tools["common"] = list(tools_common)
    return {"schema": "thth.outlets/1", "outlets": {
        "site": {"audience": "reader", "kinds": ["claim"], "fact": [{"question": "may_publish@site"}], "dest": ".",
                 "common": ["source", "evidence_line"]},
        "tools": tools}}


FILES = {"data/claims.csv": "id,text\nclm-bitter,苦味の受容体は 25 種\n",
         "data/evidence.csv": "id,claim_id,source_id,locator,finding\n"
                              "ev-bitter,clm-bitter,common:src-behrens,abstract,総説\n"}


def test_分け合う形で読者がcommonを持って受け入れ確かめ比べが緑(env, capsys):
    common = common_ledger(env)
    led = media(env, "nig", decl=shared_common_outlets(), kinds={k: KINDS[k] for k in ("claim", "evidence")},
                files=FILES)
    R.take(str(led), str(common), ["source:src-behrens", "evidence_line:el-behrens-1"], by="masaru", at=BASE_AT)
    R.baseline(str(led), "site", by="masaru", at=BASE_AT)
    repo = env / "repo"
    rc, v, err = jrun(capsys, "register", "accept", led, "--outlet", "all", "--to", repo, "--as-of", AT,
                      "--by", "masaru", "--common", common)
    assert rc == 1 and v.get("stage") == 1, (v, err)
    rc, w, err = jrun(capsys, "register", "accept", led, "--outlet", "all", "--to", repo, "--as-of", AT,
                      "--by", "masaru", "--common", common, "--confirm", v["digest"])
    assert rc == 0 and w["ok"], (w, err)
    assert (repo / "common/source/src-behrens.json").read_bytes() == (common / "source/src-behrens.json").read_bytes()
    assert (repo / "data/evidence.csv").is_file() and (repo / "data/claims.csv").is_file()
    rc, x, err = jrun(capsys, "register", "verify", led, "--outlet", "all", "--material", repo, "--now", NOW,
                      "--no-previous", "--common", common)
    assert rc == 0 and x["ok"], (x, err)
    rc, y, err = jrun(capsys, "register", "export", led, "--outlet", "all", "--to", repo, "--as-of", AT, "--check",
                      "--common", common)
    assert rc == 0 and y["ok"], (y, err)


def _refused_overlap(capsys, led, common, env):
    rc, v, err = jrun(capsys, "register", "accept", led, "--outlet", "all", "--to", env / "repo", "--as-of", AT,
                      "--by", "masaru", "--common", common)
    assert rc == 2, (v, err)
    return json.dumps(v, ensure_ascii=False) + err


def test_commonの置き場が相手の種類の型と重なれば断る(env, capsys):
    common = common_ledger(env)
    kinds = {k: dict(KINDS[k]) for k in ("claim", "evidence")}
    kinds["evidence"]["place"] = "common/evidence.csv"     # 内の材料を読者の common/ の中に置く形
    files = {"data/claims.csv": FILES["data/claims.csv"], "common/evidence.csv": FILES["data/evidence.csv"]}
    led = media(env, "nig", decl=shared_common_outlets(), kinds=kinds, files=files)
    said = _refused_overlap(capsys, led, common, env)
    assert "dest_shared_with:site:overlap" in said
    assert not (env / "repo").exists()


def test_内の出し口がcommonを持つのは今までどおり断る(env, capsys):
    common = common_ledger(env)
    led = media(env, "nig", decl=shared_common_outlets(tools_common=["source"]),
                kinds={k: KINDS[k] for k in ("claim", "evidence")}, files=FILES)
    said = _refused_overlap(capsys, led, common, env)
    assert "dest_shared_with:site:overlap" in said
