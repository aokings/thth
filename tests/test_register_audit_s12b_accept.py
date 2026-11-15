"""5.23.0 積み増しの検収（s12b）軽 1: `accept --outlet all` で書く段の断り（symlink）が出ても、前の出し口の封が
進まない（全部の prepare が通るまで何も書かない）。"""
from __future__ import annotations

import os
import pathlib

from tests.test_register_stage11 import (AT, BASE_AT, env, jrun, media,  # noqa: F401
                                         shared_outlets)
from thth.register import route as R


def test_accept_allで書く段の断りが出れば何も書かず何も封をしない(env, capsys):
    led = media(env, decl=shared_outlets())
    R.baseline(str(led), "site", by="masaru", at=BASE_AT)
    repo = env / "repo"
    (repo / "data").mkdir(parents=True)
    other = env / "elsewhere.txt"
    other.write_text("x")
    os.symlink(other, repo / "data/evidence.csv")    # 内の出し口（tools）の出力の道が symlink
    rc, v, err = jrun(capsys, "register", "accept", led, "--outlet", "all", "--to", repo, "--as-of", AT,
                      "--by", "masaru")
    assert rc == 1, err
    before = sorted(str(p.relative_to(repo)) for p in repo.rglob("*"))
    rc, w, err = jrun(capsys, "register", "accept", led, "--outlet", "all", "--to", repo, "--as-of", AT,
                      "--by", "masaru", "--confirm", v["digest"])
    assert rc == 1
    assert w["outlets"]["tools"]["code"] == "register_write_unsafe"
    assert w["outlets"]["site"].get("why") == "other_outlet_refused", w
    assert not pathlib.Path(R.seal_path(str(led), "site")).exists()
    assert not pathlib.Path(R.seal_path(str(led), "tools")).exists()
    assert sorted(str(p.relative_to(repo)) for p in repo.rglob("*")) == before
    assert other.read_text() == "x"
