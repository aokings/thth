"""台帳の決まった置き場（`.thth/judgments.jsonl`・`.thth/seals`・`.thth/published`）が台帳の外への symlink のとき、
書く命令（judge・accept・published --record）は断り、台帳の外を 1 バイトも変えない（監査 5.20.0「出したあとでよい」1）。

前は読み手（use・verify）は symlink を断るのに、書き手は確かめずに外のファイルへ行を足し、外のフォルダに塩と封を作った。
段 6 の `keep.guard` を、段 3 と段 7 の書く口にも通した。
"""
from __future__ import annotations

import json
import os
import pathlib

from tests import test_register_gate as G
from tests.test_register_route import SEALED_AT, env, jrun, ledger  # noqa: F401

def tree(path: pathlib.Path) -> dict:
    if not path.exists():
        return {}
    if path.is_file():
        return {".": path.read_bytes()}
    return {str(p.relative_to(path)): p.read_bytes() for p in sorted(path.rglob("*")) if p.is_file()}


def test_判断の記録がsymlinkならjudgeは断り外へ足さない(env, capsys):
    led = ledger(env, "kopicha")
    outside = env / "victim.txt"
    outside.write_bytes(b"keep me\n")
    os.symlink(outside, led / ".thth/judgments.jsonl")
    rc, v, err = jrun(capsys, "register", "judge", led, "--baseline", "--outlet", "facts", "--by", "masaru")
    assert rc != 0 and v["code"] == "register_batch_conflict" and v["detail"] == "symlink", (v, err)
    assert outside.read_bytes() == b"keep me\n"


def test_指す先がまだ無いsymlinkでも外に作らない(env, capsys):
    led = ledger(env, "kopicha")
    outside = env / "nothing-yet.txt"
    os.symlink(outside, led / ".thth/judgments.jsonl")
    rc, v, err = jrun(capsys, "register", "judge", led, "--baseline", "--outlet", "facts", "--by", "masaru")
    assert rc != 0 and v["code"] == "register_batch_conflict", (v, err)
    assert not outside.exists()


def _judged(env, capsys):
    led = ledger(env, "kopicha")
    rc, v, err = jrun(capsys, "register", "judge", led, "--baseline", "--outlet", "facts", "--by", "masaru")
    assert rc == 0, err
    return led


def test_封の置き場がsymlinkならacceptは断り外に塩も封も作らない(env, capsys):
    led = _judged(env, capsys)
    outside = env / "outside-seals"
    outside.mkdir()
    os.symlink(outside, led / ".thth/seals")
    site = env / "site"
    args = ("register", "accept", led, "--outlet", "facts", "--to", site, "--as-of", SEALED_AT, "--by", "masaru")
    rc, v, err = jrun(capsys, *args)
    if rc == 1 and v.get("stage") == 1:
        rc, v, err = jrun(capsys, *args, "--confirm", v["digest"])
    assert rc != 0 and v["code"] == "register_batch_conflict" and v["detail"] == "symlink", (v, err)
    assert tree(outside) == {}
    assert not site.exists() or tree(site) == {}


def test_控えの置き場がsymlinkならpublished_recordは断り外を変えない(env, capsys):
    led = G.seeded(env)
    pub = led / ".thth/published"
    outside = env / "outside-published"
    pub.rename(outside)
    os.symlink(outside, pub)
    before = tree(outside)
    listed = G.lst(env, "/", "/a/", "/new/", "/日本/", "/c/", name="second.txt")
    rc, v, err = jrun(capsys, "register", "published", led, "--outlet", "site", "--list", listed, "--now", G.NOW,
                      "--no-previous", "--record")
    assert rc != 0 and v["code"] == "register_batch_conflict" and v["detail"] == "symlink", (v, err)
    assert tree(outside) == before


def test_symlinkでなければ今までどおり書ける(env, capsys):
    led = _judged(env, capsys)
    rows = (led / ".thth/judgments.jsonl").read_text(encoding="utf-8").splitlines()
    assert rows and all(json.loads(r) for r in rows)
