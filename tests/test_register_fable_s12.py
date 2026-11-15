"""5.23.0 に積んだ物（相談役の推し）の試験: 記録を並べた judge・1 つの命令の中で読み直さない・確かめの結果の記録・
共通の記録の届いた先・原稿の seeds の数え・共通の記録の検めた跡の名指し。

見本は CSV と JSON だけ（PyYAML の無い箱でも走る）。
"""
from __future__ import annotations

import json
import os
import pathlib

from tests.test_register_stage11 import (AT, BASE_AT, NOW, common_ledger, common_media, env, jrun, media, put,  # noqa: F401
                                         run, sealed, shared_outlets)
from thth.register import route as R
from thth.register import vessel as V


def test_judgeは記録を並べると同じ判断を1回で足し1つでも作れなければ何も足さない(env, capsys):
    led = media(env)
    path = led / ".thth/judgments.jsonl"
    before = path.read_bytes() if path.exists() else b""
    rc, v, err = jrun(capsys, "register", "judge", led, "claim:clm-bitter", "evidence:ev-nobody",
                      "--question", "may_publish@site", "--conclusion", "good", "--reason-text", "読んだ", "--by", "m")
    assert rc == 2, (v, err)
    assert (path.read_bytes() if path.exists() else b"") == before
    rc, v, err = jrun(capsys, "register", "judge", led, "claim:clm-bitter", "claim:clm-other",
                      "--question", "may_publish@site", "--conclusion", "good", "--reason-text", "読んだ", "--by", "m")
    assert rc == 0 and v["count"] == 2, (v, err)
    lines = [json.loads(x) for x in path.read_text().splitlines()[len(before.splitlines()):]]
    assert [j["target"]["record"] for j in lines] == ["claim:clm-bitter", "claim:clm-other"]
    assert {j["reason"]["text"] for j in lines} == {"読んだ"}


def test_verify_allは同じ台帳を読み直さず結果は同じ(env, capsys, monkeypatch):
    led = media(env, decl=shared_outlets())
    R.baseline(str(led), "site", by="masaru", at=BASE_AT)
    repo = env / "repo"
    rc, v, err = jrun(capsys, "register", "accept", led, "--outlet", "all", "--to", repo, "--as-of", AT, "--by", "m")
    rc, w, err = jrun(capsys, "register", "accept", led, "--outlet", "all", "--to", repo, "--as-of", AT, "--by", "m",
                      "--confirm", v["digest"])
    assert rc == 0, err
    plain = {name: R.verify(str(led), name, str(repo), NOW, no_previous=True) for name in ("site", "tools")}
    calls = []
    real = V._walk

    def walk(root, *a, **k):
        calls.append(os.path.realpath(root))
        return real(root, *a, **k)
    monkeypatch.setattr(V, "_walk", walk)
    rc, x, err = jrun(capsys, "register", "verify", led, "--outlet", "all", "--material", repo, "--now", NOW,
                      "--no-previous")
    assert rc == 0, err
    assert calls.count(os.path.realpath(led)) == 1, calls
    for name in ("site", "tools"):
        assert x["outlets"][name] == json.loads(json.dumps(plain[name], ensure_ascii=False, default=str)), name
    assert V.READ_MEMO is None          # 命令が済めば覚えは消える


def test_value_problemの答えは前と同じ():
    assert V.value_problem({"a": ["苦味", "ascii", {"b": "受容体"}]}) is None
    assert V.value_problem(["ok", "x\ud800y"]) == "surrogate"
    assert V.value_problem({"\udfff": 1}) == "surrogate"


def test_確かめの結果を記録に残し書けなくても確かめのrcは変わらない(env, capsys):
    from thth.register import runs
    led = media(env)
    out = sealed(env, led)
    rc, v, err = jrun(capsys, "register", "verify", led, "--outlet", "site", "--material", out / "site", "--now", NOW,
                      "--no-previous")
    assert rc == 0, err
    (out / "site/data/evidence.csv").write_text("id,claim_id,source_id,locator,finding\n", encoding="utf-8")
    rc, v, err = jrun(capsys, "register", "verify", led, "--outlet", "site", "--material", out / "site", "--now", NOW,
                      "--no-previous")
    assert rc != 0
    lines = runs.read()
    assert [(x["command"], x["ok"]) for x in lines[-2:]] == [("verify", True), ("verify", False)], lines
    assert lines[-2]["generation"] == 1 and lines[-1]["words"] and lines[-1]["version"]
    # 記録の置き場が使えなくても（ファイルが居座る）、確かめの rc は変わらない
    folder = pathlib.Path(runs.runs_dir())
    folder.rename(folder.with_name("register_runs.moved"))
    folder.write_text("x")
    (out / "site/data/evidence.csv").write_bytes((led / "data/evidence.csv").read_bytes())
    rc, v, err = jrun(capsys, "register", "verify", led, "--outlet", "site", "--material", out / "site", "--now", NOW,
                      "--no-previous")
    assert rc == 0, err


def test_共通の記録の届いた先を後から出せる_検めた跡が無ければ名指しする(env, capsys):
    common = common_ledger(env)
    nig = common_media(env, "nig")
    got = R.take(str(nig), str(common), ["source:src-behrens", "evidence_line:el-behrens-1"], by="masaru", at=BASE_AT)
    assert got["unchecked"] == ["evidence_line:el-behrens-1", "source:src-behrens"]
    R.baseline(str(nig), "site", by="masaru", at=BASE_AT)
    out = env / "out"
    plan = R.accept_plan(str(nig), "site", str(out), AT, common_root=str(common))
    R.accept(str(nig), "site", str(out), AT, confirm=plan["digest"], at=AT, common_root=str(common))
    rc, v, err = jrun(capsys, "register", "used", nig, "common:source:src-behrens", "--common", common)
    assert rc == 0, err
    assert v["pointed_by"] == ["evidence:ev-bitter"]
    assert [o["outlet"] for o in v["outlets"]] == ["site"] and v["outlets"][0]["stale"] is False
    assert "claim:clm-bitter" in {c["record"] for c in v["reached"]}
    rc, h, err = jrun(capsys, "register", "health", nig, "--common", common)
    assert h["groups"]["common"]["pointers_untaken"]["unchecked"] == 2, h["groups"]["common"]


def test_原稿のseedsを台帳と突き合わせて数える(env, capsys):
    led = media(env)
    queue = env / "queue"
    put(queue, "a.md", "---\nthth: 1\nseeds: claim:clm-bitter evidence:ev-nobody common:source:src-x\n---\n本文\n")
    put(queue, "sub/b.md", "---\nthth: 1\nseeds: claim:clm-bitter\n---\n本文\n")
    put(queue, "c.md", "---\nthth: 1\n---\n本文\n")
    rc, v, err = jrun(capsys, "register", "seeds", led, "--queue", queue)
    assert rc == 0, err
    assert v["drafts"] == 3 and v["seeded"] == 2
    assert v["by_kind"] == {"claim": 2, "common": 1, "evidence": 1}
    assert v["missing"] == [{"draft": "a.md", "seed": "evidence:ev-nobody"}] and v["common_seeds"] == 1
    assert v["never_seeded"]["claim"]["count"] == 1 and v["never_seeded"]["evidence"]["count"] == 2
