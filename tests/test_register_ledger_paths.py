"""台帳の器: 控えが「1 名前 1 道」のつまずきと、`--json` の後ろに report_channel の行が混ざるつまずき（にがみラボ 5.22.0）。

- 1: 控えた名前に `--path <別の checkout の台帳>` を添えると、控え直しなしで確かめ・受け入れられる。別の道が同じ台帳かは
  台帳の中の名乗り（`.thth/register.json`）で確かめ、名乗りの名前が違う・名乗りが無い道は断る。`<台帳>` に台帳のフォルダの
  道を渡しても、名乗りから控えの名前を引く。
- 2: `--json` のときは stdout も `2>&1` でまとめた出力も 1 つの JSON。断りの報告の 1 行は JSON の `report_channel` の欄に入る。
  `--json` でないときは今までどおり stderr の最後の行。

見本は CSV と JSON だけ（PyYAML の無い箱でも走る）。
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys

from tests.test_register_stage11 import AT, NOW, BASE_AT, env, jrun, media, run, sealed  # noqa: F401
from thth.register import ledger as L
from thth.register import route as R


def declare(led: pathlib.Path, name: str) -> None:
    (led / ".thth/register.json").write_text(json.dumps({"schema": "thth.register_name/1", "register": name}) + "\n")


def checkout(env, led: pathlib.Path, where: str) -> pathlib.Path:
    """同じ台帳の別の checkout（作業木の写し）。"""
    other = env / where / "register"
    shutil.copytree(led, other)
    return other


def add(capsys, name, led, project="nigamilab"):
    rc, v, err = jrun(capsys, "register", "add", name, "--path", led, "--project", project, "--kind", "media",
                      "--by", "masaru")
    assert rc == 0, err
    return v


def one_json(out: str, err: str):
    """stdout と stderr をまとめた出力（`2>&1` と同じ）が 1 つの JSON として読めるか。"""
    return json.loads(out + err)


# ------------------------------------------------------------------ 1. 控えと別の checkout

def test_名乗りの同じ別のcheckoutをpathで指すと控え直しなしで確かめ受け入れられる(env, capsys):
    led = media(env, "main")
    declare(led, "nigami")
    add(capsys, "nigami", led)
    before = pathlib.Path(L.path_for("nigami")).read_bytes()
    R.baseline(str(led), "site", by="masaru", at=BASE_AT)
    other = checkout(env, led, "wt")
    # 控えの道の checkout は無くなった（作業木を片づけた・本体を動かした）。控え直さずに作業木の側を読む
    led.rename(env / "main-moved")
    rc, v, err = jrun(capsys, "register", "check", "nigami", "--path", other)
    assert rc == 0, err
    rc, v, err = jrun(capsys, "register", "kinds", "nigami", "--path", other)
    assert rc == 0, err
    out = env / "wt-out"
    rc, v, err = jrun(capsys, "register", "accept", "nigami", "--path", other, "--outlet", "site", "--to", out,
                      "--as-of", AT, "--by", "masaru")
    assert rc == 1 and v["stage"] == 1, err
    rc, w, err = jrun(capsys, "register", "accept", "nigami", "--path", other, "--outlet", "site", "--to", out,
                      "--as-of", AT, "--by", "masaru", "--confirm", v["digest"])
    assert rc == 0, err
    assert (out / "data/evidence.csv").read_bytes() == (other / "data/evidence.csv").read_bytes()
    # 封は作業木の台帳の中に置かれる
    assert pathlib.Path(R.seal_path(str(other), "site")).is_file()
    rc, x, err = jrun(capsys, "register", "verify", "nigami", "--path", other, "--outlet", "site", "--material", out,
                      "--now", NOW, "--no-previous")
    assert rc == 0 and x["ok"], (x, err)
    # 控えは 1 バイトも変わらない（控え直していない）
    assert pathlib.Path(L.path_for("nigami")).read_bytes() == before
    # --path を付けなければ、今までどおり控えの道を読む（もう無いので断る）
    rc, x, err = jrun(capsys, "register", "verify", "nigami", "--outlet", "site", "--material", out,
                      "--now", NOW, "--no-previous")
    assert rc == 2 and x["code"] == "register_path_unusable", (x, err)


def test_名乗りの名前が違う台帳をpathで指すと断り何も書かない(env, capsys):
    led = media(env, "main")
    declare(led, "nigami")
    add(capsys, "nigami", led)
    kop = media(env, "kop")
    declare(kop, "kopicha")
    R.baseline(str(kop), "site", by="masaru", at=BASE_AT)
    before = {p: p.read_bytes() for p in kop.rglob("*") if p.is_file()}
    out = env / "kop-out"
    for argv in (("check", "nigami", "--path", kop),
                 ("accept", "nigami", "--path", kop, "--outlet", "site", "--to", out, "--as-of", AT, "--by", "masaru"),
                 ("verify", "nigami", "--path", kop, "--outlet", "site", "--material", out, "--now", NOW,
                  "--no-previous")):
        rc, v, err = jrun(capsys, "register", *argv)
        assert rc == 2 and v["code"] == "register_path_other" and v["detail"] == "other_name", (argv, v, err)
    assert {p: p.read_bytes() for p in kop.rglob("*") if p.is_file()} == before
    assert not out.exists()


def test_名乗りの無いフォルダは控えの道と同じでなければpathで指せない(env, capsys):
    led = media(env, "main")
    add(capsys, "nigami", led)
    other = checkout(env, led, "wt")
    rc, v, err = jrun(capsys, "register", "check", "nigami", "--path", other)
    assert rc == 2 and v["code"] == "register_path_other" and v["detail"] == "undeclared", (v, err)
    # 控えの道そのものなら、名乗りが無くても通る
    rc, v, err = jrun(capsys, "register", "check", "nigami", "--path", led)
    assert rc == 0, err


def test_名乗りが崩れていれば断る(env, capsys):
    led = media(env, "main")
    add(capsys, "nigami", led)
    other = checkout(env, led, "wt")
    (other / ".thth/register.json").write_text('{"schema": "thth.register_name/1", "register": "nigami", "x": 1}')
    rc, v, err = jrun(capsys, "register", "check", "nigami", "--path", other)
    assert rc == 2 and v["code"] == "register_path_other" and v["detail"] == "declaration", (v, err)


def test_台帳の引数にフォルダの道を渡すと名乗りから控えの名前を引く(env, capsys):
    led = media(env, "main")
    declare(led, "nigami")
    add(capsys, "nigami", led)
    other = checkout(env, led, "wt")
    rc, v, err = jrun(capsys, "register", "check", other)
    assert rc == 0 and v["register"] == "nigami", err
    # 名乗りの名前に控えが無ければ、控えの無い台帳として断る
    declare(other, "nobody")
    rc, v, err = jrun(capsys, "register", "check", other)
    assert rc == 2 and v["code"] == "register_missing", (v, err)


def test_控えの道の台帳の名乗りが控えの名前と違えば断る(env, capsys):
    led = media(env, "main")
    add(capsys, "nigami", led)
    declare(led, "kopicha")
    rc, v, err = jrun(capsys, "register", "check", "nigami")
    assert rc == 2 and v["code"] == "register_path_other" and v["detail"] == "other_name", (v, err)


# ------------------------------------------------------------------ 2. --json の出力は 1 つの JSON

def test_jsonの断りはstderrに行を出さずreport_channelの欄に入る(env, capsys):
    rc, out, err = run(capsys, "register", "check", "nobody", "--json")
    assert rc == 2 and err == "", err
    value = one_json(out, err)
    assert value["code"] == "register_missing"
    assert value["report_channel"].startswith("report_channel: thth report file ")
    assert '--title "register_missing で断られた"' in value["report_channel"]
    # --json でなければ今までどおり最後の行
    rc, out, err = run(capsys, "register", "check", "nobody")
    assert rc == 2 and err.strip().splitlines()[-1].startswith("report_channel: thth report file ")


def test_jsonの1段めと赤の確かめもまとめた出力が1つのJSON(env, capsys):
    led = media(env, "main")
    out = sealed(env, led)
    site = out / "site"
    # 受け入れの 1 段め（rc 1）
    rc, o, e = run(capsys, "register", "accept", led, "--outlet", "site", "--to", env / "x", "--as-of", AT,
                   "--by", "masaru", "--json")
    assert rc == 1 and one_json(o, e)["stage"] == 1, e
    # 確かめが赤（rc 1）・export --check の違い（rc 1）
    (site / "data/evidence.csv").write_text("id,claim_id,source_id,locator,finding\n", encoding="utf-8")
    rc, o, e = run(capsys, "register", "verify", led, "--outlet", "site", "--material", site, "--now", NOW,
                   "--no-previous", "--json")
    assert rc != 0 and one_json(o, e)["ok"] is False, e
    rc, o, e = run(capsys, "register", "export", led, "--outlet", "site", "--to", site, "--as-of", AT, "--check",
                   "--json")
    assert rc == 1 and one_json(o, e)["found"], e
    # fix の断り（何も変えない）
    rc, o, e = run(capsys, "register", "fix", led, "evidence:ev-bitter", "--set", "finding=ヒトの苦味受容体を総説",
                   "--reason-text", "同じ", "--outlet", "site", "--to", site, "--as-of", AT, "--by", "claude-v", "--json")
    assert rc == 2 and one_json(o, e)["ok"] is False, e


def test_2と1をまとめた実の出力もjsonとして読める(env, capsys):
    led = media(env, "main")
    R.baseline(str(led), "site", by="masaru", at=BASE_AT)
    proc = subprocess.run([sys.executable, "-m", "thth", "register", "accept", str(led), "--outlet", "site",
                           "--to", str(env / "x"), "--as-of", AT, "--by", "masaru", "--json"],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=dict(os.environ),
                          cwd=str(pathlib.Path(__file__).resolve().parents[1]))
    assert proc.returncode == 1, proc.stdout
    assert json.loads(proc.stdout)["stage"] == 1
