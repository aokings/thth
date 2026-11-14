"""台帳の器 段 5「消す求め」の試験（指示 `docs/指示_台帳の器_段5_跡と消す求め_2026-11-14.md` §1 の 3・4 と §3）。

見本は `tests/fixtures/register_route/kopicha/`（こぴちゃの主張の表の小さな写し）。秘密の鍵は試験ごとの一時の場所に作る。

- 通しの見本（「済み」の線の 3）: 住所を書く → 訂正する → 判断の理由にも書く → 材料に出して封をする → 消す。
  消したあと、台帳のフォルダ（`.git/` を除く・`.thth/` を含む）の全部のファイルを**試験が自分で読み**、住所の字も、
  住所を含む版の sha256・器の指紋（行の版・項目の指紋）も無いことを確かめる。
- 求めだけ・範囲の判断の 1 段めだけでは何も消えない。範囲は求めより広げられない。
- 途中で止めて打ち直すと続きから終わる。
- 偽の消す求めで柵を抜けない（重い穴の定め）: (a)〜(f)。
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess

import pytest

from thth import cli
from thth.register import adopt as A
from thth.register import erase as E
from thth.register import judgment as J
from thth.register import keep as K
from thth.register import route as R
from thth.register import seal as S
from thth.register import trail as T
from thth.register import vessel as V
from thth.register.errors import RegisterRefused

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "register_route"
SEALED_AT = "2027-03-20T10:00:00+09:00"
BRAZIL = "claim:c-origin-brazil-024"
MILK = "claim:c-term-tea-grade-ctc-res-milktea1-24"
ADDR1 = "東京都千代田区架空町一丁目2番3号 運営者宅"
ADDR2 = "東京都千代田区架空町一丁目2番4号 運営者宅"   # 訂正したあとの住所（前の版ができる）


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


def run(capsys, *argv):
    rc = cli.main([str(a) for a in argv])
    out, err = capsys.readouterr()
    return rc, out, err


def jrun(capsys, *argv):
    rc, out, err = run(capsys, *argv, "--json")
    return rc, (json.loads(out) if out.strip() else None), err


def accept(capsys, led, outlet, to, as_of=SEALED_AT):
    rc, v, err = jrun(capsys, "register", "accept", led, "--outlet", outlet, "--to", to, "--as-of", as_of,
                      "--by", "masaru")
    assert rc == 1 and v["stage"] == 1, err
    rc, v, err = jrun(capsys, "register", "accept", led, "--outlet", outlet, "--to", to, "--as-of", as_of,
                      "--by", "masaru", "--confirm", v["digest"])
    assert rc == 0, err
    return v


def verify(capsys, led, outlet, to, *extra):
    rc, v, err = jrun(capsys, "register", "verify", led, "--outlet", outlet, "--material", to,
                      "--now", "2027-03-21", *extra)
    return rc, v


def words(v) -> set:
    return {p["word"] for p in (v or {}).get("problems", [])}


def judge(capsys, led, *argv):
    rc, v, err = jrun(capsys, "register", "judge", led, *argv, "--by", "masaru")
    assert rc == 0, err
    return v


def publish(led, outlet, site, into: pathlib.Path, previous=None) -> pathlib.Path:
    """「配った」: CI の台本のとおり確かめて、緑なら配る物を公開の場所（台帳の外）に置く。"""
    into.parent.mkdir(parents=True, exist_ok=True)
    kw = {"previous_path": str(previous)} if previous else {"no_previous": True}
    v = R.verify(str(led), outlet, str(site), "2027-03-21", publish=str(into), **kw)
    assert v["ok"], v
    return into


def use(capsys, led, ref, outlet="facts"):
    rc, v, err = jrun(capsys, "register", "use", led, ref, "--outlet", outlet, "--as", "fact", "--as-of", SEALED_AT)
    return rc, v


def setup_ledger(env, capsys, *, outlets=("facts",)):
    """見本の台帳に住所を 1 つ書き、訂正し、判断の理由にも書き、材料に出して封をし、配る。"""
    led, site = env / "kopicha", env / "site"
    shutil.copytree(FIX / "kopicha", led)
    A.row_set(str(led), BRAZIL, {"notes": ADDR1}, by="masaru", at="2027-03-19T09:00:00+09:00")
    A.row_set(str(led), BRAZIL, {"notes": ADDR2}, by="masaru", at="2027-03-19T09:10:00+09:00")   # 訂正
    rc, v, err = jrun(capsys, "register", "judge", led, "--baseline", "--outlet", "facts", "--by", "masaru")
    assert rc == 0, err
    judge(capsys, led, BRAZIL, "--question", "says_so", "--conclusion", "good",
          "--reason-text", f"運営者の住所 {ADDR2} を本人に確かめた")
    judge(capsys, led, MILK, "--question", "says_so", "--conclusion", "good", "--reason-text", "公的な資料で確かめた")
    published = {}
    for outlet in outlets:
        accept(capsys, led, outlet, site)
        published[outlet] = publish(led, outlet, site, env / "published" / f"{outlet}.json")
    return led, site, published


# ------------------------------------------------------------------ 試験が自分で全部のファイルを読む

def _rows(data: bytes, kind) -> list:
    text = data.decode("utf-8", errors="replace").lstrip("﻿")
    return V._read_csv("data/knowledge/claims.csv", text, kind)


def needles(led: pathlib.Path) -> dict:
    """消す前に、試験が自分で集める「残ってはいけない物」: 住所の字・住所を含む版のファイルの sha256・
    行の版（器の指紋）・封の項目の指紋と書いたファイルの sha256。"""
    vessel = V.open_vessel(str(led))
    kind = vessel.kinds["claim"]
    out = {ADDR1: ADDR1.encode(), ADDR2: ADDR2.encode()}
    blobs = []
    objects = led / ".thth" / "kept" / "objects"
    for path in objects.rglob("*"):
        if path.is_file():
            blobs.append((path.parent.name + path.name, path.read_bytes()))
    current = (led / "data/knowledge/claims.csv").read_bytes()
    blobs.append((K.sha256(current), current))
    for digest, data in blobs:
        if ADDR1.encode() in data or ADDR2.encode() in data:
            out[f"file:{digest[:12]}"] = digest.encode()
            for rec in _rows(data, kind):
                if rec.ref == BRAZIL:
                    out[f"row:{rec.version[:12]}"] = rec.version.encode()
    for seal in (led / ".thth" / "seals").glob("*.json"):
        body = json.loads(seal.read_text(encoding="utf-8"))["body"]
        for key, value in body["fingerprints"]["items"].items():
            if key.startswith(BRAZIL + "="):
                out[f"item:{value[:12]}"] = value.encode()
        out["output:claims"] = body["outputs"]["data/knowledge/claims.csv"].encode()
    return out


def scan(led: pathlib.Path, wanted: dict) -> list:
    """台帳のフォルダ（`.git/` を除く・`.thth/` を含む）の全部のファイルを読み、見つかった `(道, 名)` を返す。"""
    hits = []
    for directory, dirs, files in os.walk(led):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in files:
            path = pathlib.Path(directory) / name
            data = path.read_bytes()
            for label, needle in wanted.items():
                if needle in data:
                    hits.append((str(path.relative_to(led)), label))
    return sorted(hits)


def erase_all(capsys, led, ref=BRAZIL, field="notes"):
    rc, v, err = jrun(capsys, "register", "erase", "request", led, ref, *(("--field", field) if field else ()),
                      "--by", "masaru", "--why", "personal")
    assert rc == 0, err
    req = v["request"]
    rc, v, err = jrun(capsys, "register", "erase", "scope", led, req, "--versions", "all", "--by", "masaru")
    assert rc == 1 and v["stage"] == 1, err
    rc, v, err = jrun(capsys, "register", "erase", "scope", led, req, "--versions", "all", "--by", "masaru",
                      "--confirm", v["digest"])
    assert rc == 0, err
    rc, v, err = jrun(capsys, "register", "erase", "run", led, req, "--by", "masaru")
    assert rc == 0, err
    return req, v


# ------------------------------------------------------------------ 通しの見本

def test_通しの見本_住所を書いて訂正し判断と材料と封に載せてから消す(env, capsys):
    led, site, published = setup_ledger(env, capsys)
    # 判断を足したあとで控える（跡の注に、判断の記録の今の中身が控えに入る。理由の文に住所がある・段 5 §2.2）
    rc, kept, err = jrun(capsys, "register", "keep", led, "--by", "masaru")
    assert rc == 0, err
    note = K.load_snapshot(str(led), kept["snapshot"])["trail"][-1]
    assert ADDR2.encode() in K.get_object(str(led), note["judgments"])
    wanted = needles(led)
    before = scan(led, wanted)
    # 消す前は、今の本文・前の版・判断の理由・封のどこにもある（見る物が空振りしていない）
    where = {path.split("/")[1] if path.startswith(".thth/") else path for path, _ in before}
    assert {"data/knowledge/claims.csv", "kept", "judgments.jsonl", "seals"} <= where, where
    assert any(label.startswith("row:") for _, label in before) and any(label.startswith("item:") for _, label in before)
    claims_before = (led / "data/knowledge/claims.csv").read_bytes()

    # 求めだけでは何も消えない（出口にも効かない）
    rc, v, err = jrun(capsys, "register", "erase", "request", led, BRAZIL, "--field", "notes", "--by", "masaru",
                      "--why", "personal")
    assert rc == 0, err
    req = v["request"]
    assert ADDR2 not in json.dumps(v, ensure_ascii=False) and v["exit_unaffected"] is True
    assert scan(led, wanted) == before and (led / "data/knowledge/claims.csv").read_bytes() == claims_before
    # 範囲の判断の 1 段めは見積りだけ（再現できなくなる世代を出す）
    rc, v, err = jrun(capsys, "register", "erase", "scope", led, req, "--versions", "all", "--by", "masaru")
    assert rc == 1 and v["stage"] == 1, err
    est = v["estimate"]
    assert est["unreproducible"] == [{"outlet": "facts", "generations": [1, 1], "at_most": True}]
    assert est["places"]["current_text"]["in_range"] is True and est["places"]["kept_objects"]["objects"] >= 2
    assert est["places"]["judgments"]["lines"] >= 2
    assert scan(led, wanted) == before
    rc, v, err = jrun(capsys, "register", "erase", "scope", led, req, "--versions", "all", "--by", "masaru",
                      "--confirm", v["digest"])
    assert rc == 0, err
    assert scan(led, wanted) == before                       # 範囲の判断を置いただけでも消えない

    rc, v, err = jrun(capsys, "register", "erase", "run", led, req, "--by", "masaru")
    assert rc == 0, err
    # 台帳のフォルダの全部のファイルに、住所の字も、住所を含む版の sha256・器の指紋も無い（試験が自分で読む）
    assert scan(led, wanted) == []
    # 今の本文: 欄だけが空になり、ほかの行は 1 バイトも変わらない
    rows_after = _rows((led / "data/knowledge/claims.csv").read_bytes(), V.open_vessel(str(led)).kinds["claim"])
    mine = [r for r in rows_after if r.ref == BRAZIL]
    assert len(mine) == 1 and mine[0].values["notes"] == "" and mine[0].values["statement_ja"]
    before_rows = [l for l in claims_before.decode().splitlines() if "brazil" not in l]
    after_rows = [l for l in (led / "data/knowledge/claims.csv").read_text().splitlines() if "brazil" not in l]
    assert before_rows == after_rows
    # 結果の記録: 場所ごとの結果・再現できない世代・書き換えた判断の行
    status = v
    places = status["places"]
    for place in ("current_text", "kept_objects", "snapshots", "judgments", "seals", "batch", "self_check"):
        assert places[place]["state"] == "erased", (place, places[place])
    for place in ("git_history", "clones", "pages", "copies", "common", "materials"):
        assert places[place]["state"] == "not_checked", place
    assert status["state"] == "unfinished"
    assert status["unreproducible"] == [{"outlet": "facts", "generations": [1, 1], "at_most": True}]
    marked = [j for j in J.read(str(led)) if J.is_erased(j)]
    assert sorted(j["id"] for j in marked) == status["rewritten_lines"] and len(marked) >= 2
    for j in marked:
        assert "seen" not in j and "text" not in j["reason"] and j["erased"] == {"request": req}
    # 前の版は取り出せない（取り出せないと言う）
    with pytest.raises(RegisterRefused):
        A.restore(str(led), str(env / "old"), at_snapshot="first")
    # 「消し終わっていない」と言い続ける（数える関数も）
    assert E.unfinished(str(led)) == 1
    rc, v, err = jrun(capsys, "register", "erase", "status", led, req)
    assert rc == 0 and v["state"] == "unfinished"
    # 持ち主が外の場所を確かめて 1 つずつ結果を付けると、全部に結果が付いて初めて「消し終わり」
    outside = [p for p, x in v["places"].items() if x["state"] == "not_checked"]
    for i, place in enumerate(outside):
        rc, v, err = jrun(capsys, "register", "erase", "done", led, req, "--place", place, "--by", "masaru",
                          "--result", "kept" if place == "git_history" else "erased")
        assert rc == 0, err
        assert v["state"] == ("finished" if i == len(outside) - 1 else "unfinished")
    assert E.unfinished(str(led)) == 0
    assert scan(led, wanted) == []
    # 消したあとの記録（結果・封・目録・判断の行）に、消した中身もその指紋も無い
    record = (led / ".thth" / "erase" / req / "result.json").read_text(encoding="utf-8")
    assert "notes" in record and ADDR2 not in record


def test_求めだけと範囲の判断の1段めでは何も消えず範囲は求めより広げられない(env, capsys):
    led, site, _ = setup_ledger(env, capsys)
    wanted = needles(led)
    before = scan(led, wanted)
    rc, v, err = jrun(capsys, "register", "erase", "request", led, BRAZIL, "--field", "notes", "--by", "masaru",
                      "--why", "personal")
    req = v["request"]
    # 欄を広げる・番号を変える、は断る
    rc, v, err = jrun(capsys, "register", "erase", "scope", led, req, "--versions", "all", "--field", "statement_ja",
                      "--by", "masaru")
    assert rc != 0 and v["code"] == "register_erase_invalid" and v["detail"] == "scope_wider"
    rc, v, err = jrun(capsys, "register", "erase", "scope", led, req, "--versions", "all", "--record", MILK,
                      "--by", "masaru")
    assert rc != 0 and v["code"] == "register_erase_invalid" and v["detail"] == "scope_wider"
    # 理由は種類の語だけ（文を取らない）
    rc, v, err = jrun(capsys, "register", "erase", "request", led, BRAZIL, "--field", "notes", "--by", "masaru",
                      "--why", ADDR2)
    assert rc != 0 and v["code"] == "register_erase_invalid"
    # 範囲の判断が無い求めは実行しない
    rc, v, err = jrun(capsys, "register", "erase", "run", led, req, "--by", "masaru")
    assert rc != 0 and v["detail"] == "scope_missing"
    rc, v, err = jrun(capsys, "register", "erase", "scope", led, req, "--versions", "all", "--by", "masaru")
    assert rc == 1
    rc, v, err = jrun(capsys, "register", "erase", "scope", led, req, "--versions", "all", "--by", "masaru",
                      "--confirm", "0000000000000000")
    assert rc != 0 and v["code"] == "register_confirm_mismatch"
    assert scan(led, wanted) == before
    # 求めの記録に中身は無い
    for path in (led / ".thth" / "erase").rglob("*.json"):
        assert ADDR2 not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("where", ["seals", "current_text"])
def test_途中で止めて打ち直すと続きから終わる(env, capsys, monkeypatch, where):
    led, site, _ = setup_ledger(env, capsys, outlets=("facts", "strict"))
    wanted = needles(led)
    rc, v, err = jrun(capsys, "register", "erase", "request", led, BRAZIL, "--field", "notes", "--by", "masaru",
                      "--why", "personal")
    req = v["request"]
    est = E.scope(str(led), req, ["all"], by="masaru", at="2027-03-22T09:00:00+09:00")
    E.scope(str(led), req, ["all"], by="masaru", at="2027-03-22T09:00:00+09:00", confirm=est["digest"])
    calls = {"n": 0}
    if where == "seals":
        real = E._write_seal

        def broken(*args, **kw):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("落ちた")       # 2 つめの封を書く前に落ちる（場所の途中）
            return real(*args, **kw)
        monkeypatch.setattr(E, "_write_seal", broken)
    else:
        def broken(tmp, target):
            raise RuntimeError("落ちた")           # まとめての入れ替えの途中で落ちる
        monkeypatch.setattr(K, "_replace", broken)
    with pytest.raises(RuntimeError):
        E.run(str(led), req, by="masaru", at="2027-03-22T09:10:00+09:00")
    monkeypatch.undo()
    first = json.loads((led / ".thth" / "erase" / req / "result.json").read_text(encoding="utf-8"))
    assert first["places"]["judgments"]["state"] == "erased"     # 済んだ場所は置いてから次へ
    rc, v, err = jrun(capsys, "register", "erase", "run", led, req, "--by", "masaru")
    assert rc == 0, err
    assert v["places"]["seals"]["state"] == "erased" and sorted(v["seals"]) == ["facts", "strict"]
    assert v["places"]["self_check"]["state"] == "erased"
    assert v["rewritten_lines"] == first["rewritten_lines"]       # 済んだ場所はやり直さない
    assert scan(led, wanted) == []
    assert K.pending(str(led)) is None


# ------------------------------------------------------------------ 偽の消す求めで柵を抜けない（重い穴の定め）

def _marked(line: dict, req: str) -> dict:
    t = line["target"]
    return {"schema": line["schema"], "id": line["id"], "question": line["question"], "target": {"record": t["record"]},
            "answer": {"word": line["answer"].get("word"), "conclusion": line["answer"].get("conclusion")},
            "reason": {"kind": None}, "by": line["by"], "at": line["at"], "until": line.get("until"),
            "path": line.get("path"), "carries": line.get("carries"), "erased": {"request": req}}


def _write_lines(led, items) -> None:
    pathlib.Path(J.path_of(str(led))).write_text(
        "".join(json.dumps(i, ensure_ascii=False, sort_keys=True) + "\n" for i in items), encoding="utf-8")


def test_重い穴a_鍵なしで止めるの行を印の形にして消す求めの記録を置いてもCIは赤(env, capsys):
    led, site, published = setup_ledger(env, capsys)
    judge(capsys, led, MILK, "--stop", "--outlet", "facts")
    accept(capsys, led, "facts", site)
    prev = publish(led, "facts", site, env / "published" / "facts2.json", previous=published["facts"])
    lines = J.read(str(led))
    req = "e-" + "0" * 16
    stop = next(j for j in lines if j["question"] == "stop@facts")
    seal_before = pathlib.Path(R.seal_path(str(led), "facts")).read_bytes()
    for change in ("same", "lift"):
        forged = _marked(stop, req)
        if change == "lift":
            forged["answer"] = {"word": "resume", "conclusion": "good"}
        base = led / ".thth" / "erase" / req
        base.mkdir(parents=True, exist_ok=True)
        (base / "scope.json").write_text(json.dumps({"request": req}), encoding="utf-8")
        (base / "result.json").write_text(json.dumps({"request": req, "rewritten_lines": [stop["id"]]}),
                                          encoding="utf-8")
        _write_lines(led, [forged if j["id"] == stop["id"] else j for j in lines])
        shutil.copytree(site, env / f"site-{change}")
        rc, v = verify(capsys, led, "facts", env / f"site-{change}", "--previous", prev)
        assert rc != 0 and {"judgments_rewritten"} <= words(v), v
        # 封の項目の指紋を外して署名を合わなくしても、赤のまま（鍵が無いので封をし直せない）
        seal = json.loads(pathlib.Path(R.seal_path(str(led), "facts")).read_text(encoding="utf-8"))
        seal["body"]["erased"] = [req]
        pathlib.Path(R.seal_path(str(led), "facts")).write_text(json.dumps(seal, ensure_ascii=False),
                                                                encoding="utf-8")
        rc, v = verify(capsys, led, "facts", env / f"site-{change}", "--previous", prev)
        assert rc != 0 and "signature" in words(v), v
        # 手元でも、その封は使わない（受け入れ直しにも使えない: 求めの結果がこの出し口の封を書き換えたと言わない）
        rc, u = use(capsys, led, MILK)
        assert rc != 0
        rc, out, err = run(capsys, "register", "accept", led, "--outlet", "facts", "--to", site, "--as-of",
                           SEALED_AT, "--by", "masaru")
        assert rc != 0 and "register_seal_unverified" in out + err
        _write_lines(led, lines)
        # 封を元に戻す（次の回のため）: 書き換える前の封のバイトを書き戻す
        pathlib.Path(R.seal_path(str(led), "facts")).write_bytes(seal_before)


def test_重い穴b_印の形にした止めるは効いたまま_材料に載れば赤(env, capsys):
    led, site, published = setup_ledger(env, capsys)
    judge(capsys, led, BRAZIL, "--stop", "--outlet", "facts")   # 範囲の版への止める（受け入れる前に効く）
    accept(capsys, led, "facts", site)
    prev = publish(led, "facts", site, env / "published" / "facts2.json", previous=published["facts"])
    erase_all(capsys, led)
    stop = next(j for j in J.read(str(led)) if j["question"] == "stop@facts")
    assert J.is_erased(stop) and stop["answer"]["conclusion"] == "bad"
    v = accept(capsys, led, "facts", site)
    assert v["generation"] == 3
    rc, u = use(capsys, led, BRAZIL)
    assert rc != 0 and u["why"] == "stopped"
    rc, v = verify(capsys, led, "facts", site, "--previous", prev)
    assert rc == 0 and v["ok"], v
    # 止めた記録が材料に載っていれば赤（止めるは封を待たない）
    shutil.copy(led / "data/knowledge/claims.csv", site / "data/knowledge/claims.csv")
    rc, v = verify(capsys, led, "facts", site, "--previous", prev)
    assert rc != 0 and "material_mismatch" in words(v)


def test_重い穴c_印の形にしたよいと止めないは効かない(env, capsys):
    led, site, published = setup_ledger(env, capsys)
    judge(capsys, led, BRAZIL, "--stop", "--outlet", "facts")
    judge(capsys, led, BRAZIL, "--resume", "--outlet", "facts")
    accept(capsys, led, "facts", site)
    rc, u = use(capsys, led, BRAZIL)
    assert rc == 0 and u["usable"]                            # 消す前は、止めないで使える
    erase_all(capsys, led)
    lines = J.read(str(led))
    assert all(J.is_erased(j) for j in lines if j["target"]["record"] == BRAZIL)
    accept(capsys, led, "facts", site)
    rc, u = use(capsys, led, BRAZIL)
    assert rc != 0 and u["why"] == "stopped"                  # 印の形の止めないは効かず、止めるが効く
    # 止めるの行が無いとしても（止めないだけ）、印の形の「よい」は効かない（新しい版への判断が要る）
    vessel = V.open_vessel(str(led))
    for j in lines:
        if j["target"]["record"] == BRAZIL:
            conclusion = (j.get("answer") or {}).get("conclusion")
            assert J.evaluate(vessel, j) == ("effective" if conclusion == "bad" else "stale"), j["question"]
    rest = [j for j in lines if j["question"] != "stop@facts"]
    ctx = R.Ctx(str(led), vessel, rest, rest, R.parse_time(SEALED_AT))
    got = R.decide(ctx, R.outlet_of(str(led), vessel, "facts"), BRAZIL)
    assert got["as"] is None and got["why"] != "stopped", got


def test_重い穴d_印の行の番号が結果に載っていなければ読む入口で断る(env, capsys):
    led, site, published = setup_ledger(env, capsys)
    req, _ = erase_all(capsys, led)
    lines = J.read(str(led))
    other = next(j for j in lines if j["target"]["record"] == MILK)
    forged = _marked(other, req)                              # 求めは本物・行は結果に載っていない
    _write_lines(led, [forged if j["id"] == other["id"] else j for j in lines])
    with pytest.raises(RegisterRefused) as got:
        J.read(str(led))
    assert got.value.detail.endswith("erased_not_in_result")
    with pytest.raises(RegisterRefused) as got:
        R.parse_judgments(R.judgments_raw(str(led)), str(led))
    assert got.value.detail.endswith("erased_not_in_result")
    rc, u = use(capsys, led, MILK)
    assert rc != 0 and u["code"] == "register_judgment_invalid"
    # 求めの記録が無い印の行も断る（台帳の根を渡さない読みも断る）
    _write_lines(led, lines)
    with pytest.raises(RegisterRefused):
        R.parse_judgments(R.judgments_raw(str(led)))


@pytest.mark.parametrize("tamper", ["行を減らす", "載っていない行を変える", "印の行の結論を変える"])
def test_重い穴e_鍵を持つ者でも消す求めを装って行を減らす変えるは赤(env, capsys, tamper):
    led, site, published = setup_ledger(env, capsys)
    judge(capsys, led, MILK, "--stop", "--outlet", "facts")
    judge(capsys, led, BRAZIL, "--stop", "--outlet", "facts")
    accept(capsys, led, "facts", site)
    prev = publish(led, "facts", site, env / "published" / "facts2.json", previous=published["facts"])
    erase_all(capsys, led)
    lines = J.read(str(led))
    if tamper == "行を減らす":
        victim = next(j for j in lines if j["question"] == "stop@facts" and j["target"]["record"] == MILK)
        lines = [j for j in lines if j["id"] != victim["id"]]
    elif tamper == "載っていない行を変える":
        victim = next(j for j in lines if j["question"] == "stop@facts" and j["target"]["record"] == MILK)
        changed = dict(victim, answer={"word": "resume", "conclusion": "good", "scope": None})
        changed["id"] = J.new_id(changed)
        lines = [changed if j["id"] == victim["id"] else j for j in lines]
    else:
        victim = next(j for j in lines if j["question"] == "stop@facts" and J.is_erased(j))
        lines = [dict(j, answer={"word": "resume", "conclusion": "good"}) if j["id"] == victim["id"] else j
                 for j in lines]
    _write_lines(led, lines)
    # 持ち主の鍵で受け入れ直す（消す求めの封から世代を継ぐ道）
    accept(capsys, led, "facts", site)
    rc, v = verify(capsys, led, "facts", site, "--previous", prev)
    assert rc != 0 and "judgments_not_continued" in words(v), v


def test_重い穴f_消したあと受け入れ直す前はCIが赤_受け入れ直すと緑で世代は増える(env, capsys):
    led, site, published = setup_ledger(env, capsys)
    erase_all(capsys, led)
    rc, v = verify(capsys, led, "facts", site, "--previous", published["facts"])
    assert rc != 0 and words(v) & {"signature", "seal_shape"}  # 受け入れ直すまで封なし（頭の指紋も外した）
    rc, u = use(capsys, led, MILK)
    assert rc != 0 and u["why"] == "seal_unverified"
    v = accept(capsys, led, "facts", site)
    assert v["generation"] == 2                               # 世代は戻さない（増えるだけ）
    seal = json.loads(pathlib.Path(R.seal_path(str(led), "facts")).read_text(encoding="utf-8"))["body"]
    assert seal["erase"]["requests"] and seal["erase"]["lines"]
    assert seal["erase"]["previous"]["generation"] == 1
    rc, v = verify(capsys, led, "facts", site, "--previous", published["facts"])
    assert rc == 0 and v["ok"], v
    assert "erase_rewrite" in {w["word"] for w in v["warnings"]}
    # 印の形の「よい」は効かないので、住所を消した記録は新しい版への判断を足して受け入れるまで材料に出ない
    assert BRAZIL + "=fact" not in seal["items"] and MILK + "=fact" in seal["items"]


def test_断りの符丁は目録に3つがそろう_段5():
    from thth import refusal_catalog as rc
    for code in ("register_erase_invalid", "register_erase_left"):
        entry = rc.CATALOG[code]
        assert entry["why"] and entry["fix"]["text"] and entry["better"], code


def test_JSONの内側の単位の欄を消す_ほかの単位と止めるは残る(env, capsys):
    """1 枚 = 1 記録の JSON の、内側の単位（行）の 1 つの欄を消す。枚はまとめての入れ替えで書き直す。"""
    led = env / "mmr"
    shutil.copytree(FIX / "mmr", led)
    rel = "site/src/credits/ap-alex-gopher.mastered_by.json"
    ref = "credit:ap-alex-gopher.mastered_by"
    value = json.loads((led / rel).read_text(encoding="utf-8"))
    value["rows"][2]["held_reason_ja"] = f"作り手の自宅 {ADDR1} の記載"
    bundle = env / "bundle"
    (bundle / rel).parent.mkdir(parents=True)
    (bundle / rel).write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    A.merge(str(led), str(bundle), by="masaru", at="2027-03-19T09:00:00+09:00", checked_by="検める者")
    rec = V.open_vessel(str(led)).get(ref)
    pointer = next(u.pointer for u in rec.units if u.item.get("seed") == "seed-liked-0443")
    judge(capsys, led, ref, "--stop", "--unit", pointer)
    wanted = {"addr": ADDR1.encode(), "file": K.sha256((led / rel).read_bytes()).encode()}
    assert scan(led, wanted)
    rc, v, err = jrun(capsys, "register", "erase", "request", led, ref, "--unit", pointer, "--field", "held_reason_ja",
                      "--by", "masaru", "--why", "personal")
    assert rc == 0, err
    req = v["request"]
    est = E.scope(str(led), req, ["all"], by="masaru", at="2027-03-22T09:00:00+09:00")
    E.scope(str(led), req, ["all"], by="masaru", at="2027-03-22T09:00:00+09:00", confirm=est["digest"])
    rc, v, err = jrun(capsys, "register", "erase", "run", led, req, "--by", "masaru")
    assert rc == 0, err
    assert scan(led, wanted) == []
    after = json.loads((led / rel).read_text(encoding="utf-8"))
    assert after["rows"][2]["held_reason_ja"] == "" and after["rows"][2]["status"] == "held"
    assert after["rows"][3] == value["rows"][3]
    stop = next(j for j in J.read(str(led)) if j["question"] == "stop")
    assert J.is_erased(stop) and stop["target"]["unit"] == pointer       # 鍵の指し先は中身の指紋でないので残す
    # 記録に無い欄・単位に無い欄は断る
    for extra in (("--field", "no_such"), ("--unit", pointer, "--field", "no_such")):
        rc, v, err = jrun(capsys, "register", "erase", "request", led, ref, *extra, "--by", "masaru", "--why", "mistake")
        assert rc != 0 and v["code"] == "register_erase_invalid", v


# ------------------------------------------------------------------ 二次の指紋（行の番号・控えの名・目録の指紋・判断の記録の頭）

def second_needles(led: pathlib.Path) -> dict:
    """消す前に集める二次の指紋: 範囲の記録への判断の行の番号・住所を含む版を目録に持つ控えの名と目録の指紋・
    その段取りの名・封の判断の記録の頭の指紋。"""
    out = {}
    for j in J.read(str(led)):
        if j["target"].get("record") == BRAZIL:
            out[f"id:{j['id']}"] = j["id"].encode()
    shas = {label.split(":", 1)[1] for label in needles(led) if label.startswith("file:")}
    full = {v.decode() for k, v in needles(led).items() if k.startswith("file:")}
    assert shas
    renamed = set()
    for path in (led / ".thth/kept/snapshots").glob("*.json"):
        snap = json.loads(path.read_text(encoding="utf-8"))
        if any(e["sha256"] in full for e in snap["files"]):
            out[f"snapshot:{snap['id'][:12]}"] = snap["id"].encode()
            out[f"catalog:{snap['fp'][:12]}"] = snap["fp"].encode()
            renamed.add(snap["id"])
    for path in sorted((led / ".thth/batch").rglob("*.json")):
        plan = json.loads(path.read_text(encoding="utf-8"))
        if any(e.get("old") in full or e.get("new") in full for e in plan.get("entries", [])):
            out[f"plan:{plan['id']}"] = plan["id"].encode()
    for seal in (led / ".thth" / "seals").glob("*.json"):
        body = json.loads(seal.read_text(encoding="utf-8"))["body"]
        out[f"head:{body['judgments']['sha256'][:12]}"] = body["judgments"]["sha256"].encode()
    assert renamed
    return out


def test_消したあと古い行の番号と控えの名と目録の指紋と判断の記録の頭がどこにも無い(env, capsys):
    led, site, published = setup_ledger(env, capsys)
    judge(capsys, led, BRAZIL, "--stop", "--outlet", "facts")
    old = second_needles(led)
    assert scan(led, old)                                       # 消す前はある（空振りしていない）
    req, v = erase_all(capsys, led)
    assert scan(led, old) == [], scan(led, old)
    marked = [j for j in J.read(str(led)) if J.is_erased(j)]
    assert marked and all(J.X_ID.fullmatch(j["id"]) for j in marked)
    assert sorted(j["id"] for j in marked) == v["rewritten_lines"]
    assert v["renamed"]["snapshots"] >= 2 and v["renamed"]["plans"] >= 1
    assert any("judgments.sha256" in line for line in v["second_fingerprints"])
    # 控えの名を付け直しても、跡の名で控えを引ける（消した版は取り出せない）
    for name in K.snapshots(str(led)):
        snap = K.load_snapshot(str(led), name)
        assert snap["id"] == name
    for h in K.history(str(led)):
        if h.get("snapshot"):
            assert h["snapshot"] in K.snapshots(str(led))


def test_骨は番号と誰がを持たず位置で比べる(env, capsys):
    base = {"schema": J.SCHEMA, "id": "j-0000000000000000", "question": "stop@facts", "target": {"record": BRAZIL},
            "answer": {"word": "stop", "conclusion": "bad"}, "by": "masaru", "at": "2027-03-20", "until": None}
    same = dict(base, id="x-1111111111111111", by="someone")
    assert R.line_skeleton(base) == R.line_skeleton(same)
    assert R.line_skeleton(base) != R.line_skeleton(dict(base, answer={"word": "resume", "conclusion": "good"}))


def test_重い穴e_消す求めを装って行の並びを入れ替えると赤(env, capsys):
    led, site, published = setup_ledger(env, capsys)
    judge(capsys, led, MILK, "--stop", "--outlet", "facts")
    judge(capsys, led, BRAZIL, "--stop", "--outlet", "facts")
    accept(capsys, led, "facts", site)
    prev = publish(led, "facts", site, env / "published" / "facts2.json", previous=published["facts"])
    erase_all(capsys, led)
    lines = J.read(str(led))
    i = next(n for n, j in enumerate(lines) if j["question"] == "stop@facts" and j["target"]["record"] == MILK)
    lines[i], lines[i - 1] = lines[i - 1], lines[i]
    _write_lines(led, lines)
    accept(capsys, led, "facts", site)
    rc, v = verify(capsys, led, "facts", site, "--previous", prev)
    assert rc != 0 and "judgments_not_continued" in words(v), v


def test_取り込み直しとdiffとrestoreは付け直したあとも動く_消した版は取り出せない(env, capsys):
    src = env / "src"
    shutil.copytree(FIX / "kopicha", src, ignore=shutil.ignore_patterns(".thth"))
    led = env / "led"
    plan = A.parse_plan(None)
    A.adopt(str(src), str(led), plan=plan, by="masaru", at="2027-03-18T09:00:00+09:00",
            decl=str(FIX / "kopicha" / ".thth"))
    A.row_set(str(led), BRAZIL, {"notes": ADDR1}, by="masaru", at="2027-03-19T09:00:00+09:00")
    A.row_set(str(led), BRAZIL, {"notes": ADDR2}, by="masaru", at="2027-03-19T09:10:00+09:00")
    before = set(K.snapshots(str(led)))
    adopted = json.loads((led / ".thth/adopt.json").read_text(encoding="utf-8"))["adopted"][0]["snapshot"]
    erase_all(capsys, led)
    names = set(K.snapshots(str(led)))
    assert adopted in names                                     # 範囲の版を持たない控えは名を変えない
    erased = [n for n in names if K.erased_entries(K.load_snapshot(str(led), n))]
    assert erased and not (set(erased) & before)                # 範囲の版を持つ控えは新しい名
    with pytest.raises(RegisterRefused) as got:
        A.restore(str(led), str(env / "old"), at_snapshot=erased[0])
    assert got.value.code == "register_kept_missing"
    A.restore(str(led), str(env / "now"))
    A.restore(str(led), str(env / "first"), at_snapshot=adopted)
    v = A.diff(str(led), f"{adopted}..now")
    assert not v["red"] and v["records"]["changed"] == []     # 欄を空にした今は、取り込んだ時の行と同じ
    with pytest.raises(RegisterRefused):
        A.diff(str(led), f"{erased[0]}..now")
    # 取り込み直し（元は住所を持たない。元でほかの行を直した）: 入れ替えと控えが動く
    claims = src / "data/knowledge/claims.csv"
    claims.write_bytes(claims.read_bytes().replace("17.3%".encode(), "17.1%".encode()))
    v = A.adopt(str(src), str(led), plan=plan, by="masaru", at="2027-03-23T09:00:00+09:00")
    assert v["same"] and v["changed"] == 1
    assert A.diff(str(led), "last..now")["red"] is False


def test_止まった入れ替えがあれば消す求めは実行せず_続けたあとなら付け直しても次の入れ替えが動く(env, capsys, monkeypatch):
    led, site, _ = setup_ledger(env, capsys)
    rc, v, err = jrun(capsys, "register", "erase", "request", led, BRAZIL, "--field", "notes", "--by", "masaru",
                      "--why", "personal")
    req = v["request"]
    est = E.scope(str(led), req, ["all"], by="masaru", at="2027-03-22T09:00:00+09:00")
    E.scope(str(led), req, ["all"], by="masaru", at="2027-03-22T09:00:00+09:00", confirm=est["digest"])

    def broken(tmp, target):
        raise RuntimeError("落ちた")
    monkeypatch.setattr(K, "_replace", broken)
    with pytest.raises(RuntimeError):
        A.row_set(str(led), MILK, {"notes": "別の直し"}, by="masaru", at="2027-03-22T09:05:00+09:00")
    monkeypatch.undo()
    with pytest.raises(RegisterRefused) as got:
        E.run(str(led), req, by="masaru", at="2027-03-22T09:10:00+09:00")
    assert got.value.code == "register_batch_pending"
    K.apply(str(led), by="masaru", at="2027-03-22T09:11:00+09:00")      # resume --continue と同じ
    result = E.run(str(led), req, by="masaru", at="2027-03-22T09:12:00+09:00")
    assert result["places"]["self_check"]["state"] == "erased"
    A.row_set(str(led), MILK, {"notes": "もう 1 つの直し"}, by="masaru", at="2027-03-22T09:20:00+09:00")
    assert K.pending(str(led)) is None and K.load_snapshot(str(led), "last")["files"]


# ------------------------------------------------------------------ 跡とつなぐ（段 5 §2.1・§2.2）

GIT_ENV = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def _git(repo, *args):
    subprocess.run([T.git_binary(), "-C", str(repo), *args], capture_output=True, env=GIT_ENV, check=True)


def test_跡_消したあとcheck_againstとdiffは結果に載った物だけを通し控えの判断の記録は取り出せない(env, capsys):
    led, _site, _published = setup_ledger(env, capsys)
    rc, kept, err = jrun(capsys, "register", "keep", led, "--by", "masaru")
    assert rc == 0, err
    _git(led, "init", "-q", "-b", "main")
    _git(led, "add", "-A")
    _git(led, "commit", "-q", "-m", "消す前")
    prev = (led / ".thth/judgments.jsonl").read_bytes()
    assert T.against(str(led), "HEAD")["continues"]
    req, _v = erase_all(capsys, led, field=None)          # 記録ごと消す（番号が今の本文から消える）
    now = (led / ".thth/judgments.jsonl").read_bytes()
    # 結果の記録だけでは通さない: 受け入れ直して、署名の合った封に載ってから（段 5 の検収の穴 3）
    assert T.erase_excuses(str(led))["requests"] == set() and T.erase_excuses(str(led))["unsealed"] == [req]
    assert not T.against(str(led), "HEAD")["continues"] and A.diff(str(led), "HEAD..now")["red"]
    accept(capsys, led, "facts", _site)
    excuses = T.erase_excuses(str(led))
    assert excuses["requests"] == {req} and excuses["records"] == {BRAZIL} and excuses["lines"]
    # 判断の記録: 結果に載った印の行だけを通す（骨で・位置で）。結果が無ければ赤
    got = T.against(str(led), "HEAD")
    assert got["continues"] and got["excused"]
    assert not T.judgments_continue(prev, now)["continues"]
    # 結果に載っていない書き換えは赤のまま（印の行の結論を変える・印でない行を変える）
    lines = now.decode().splitlines(keepends=True)
    marked = [i for i, l in enumerate(lines) if '"erased"' in l]
    plain = [i for i, l in enumerate(lines) if '"erased"' not in l]
    for i, swap in ((marked[0], ('"good"', '"bad"')), (plain[0], ('"masaru"', '"other"'))):
        body = list(lines)
        body[i] = body[i].replace(*swap) if swap[0] in body[i] else body[i].replace('"at": "', '"at": "1')
        assert body[i] != lines[i]
        assert not T.judgments_continue(prev, "".join(body).encode(), excuses)["continues"]
    # 控えに置いた判断の記録（理由の文に住所があった）は除いた: その時点への比べは「取り出せない」
    gone = [name for name in K.snapshots(str(led))
            if any(n.get("judgments_erased") == req
                   for n in K._read_json(str(led), K.snapshot_path(str(led), name), "snapshot").get("trail", []))]
    assert gone, "控えの判断の記録を除いた目録がある"
    with pytest.raises(RegisterRefused) as caught:
        T.against(str(led), gone[0])
    assert caught.value.code == "register_version_unavailable" and caught.value.detail == "judgments_erased"
    for name in K.snapshots(str(led)):
        for note in K._read_json(str(led), K.snapshot_path(str(led), name), "snapshot").get("trail", []):
            assert "judgments" not in note or note["judgments"] is None or \
                ADDR2.encode() not in K.get_object(str(led), note["judgments"])
    # 番号が消えた差: 結果に載った番号は赤にしない。結果を見なければ赤
    v = A.diff(str(led), "HEAD..now")
    assert v["records"]["vanished"] == [BRAZIL] and not v["red"] and v["excused"] == [BRAZIL]
    assert A.diff(str(led), "HEAD..now", excused=frozenset())["red"]
    # check に消し終わっていない求めの数が出る
    from thth.register import vessel_cli
    assert vessel_cli.check(str(led))["erase_unfinished"] == 1
