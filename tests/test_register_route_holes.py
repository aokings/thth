"""台帳の器 段 3 の検収（Codex・11/14）で見つかった穴と、続きの反例。

Codex の途中の結果（~/Documents/THTH/codex/scratch/register-stage3-audit-kmakbbt9/seal-audit/results.json）:

1. 止めるで外して配った（封は世代 1 のまま）あと、止めるの行と材料を前に戻すと、確かめが緑で止めた記録が戻る
   → 配るとき、確かめが「配った時の判断の記録の頭」を封に添えて置く（`--publish`）。次の回は、判断の記録が
     その続きでなければ赤（設計 §5.6 の止める線・§12）。
2. 出し口 A に向けて署名した封が、出し口 B の確かめで通る → 封の出し口と、確かめる出し口を照らす。
3. 手元の `use`・`export` が、署名の合わない封を「封あり」として扱う → 手元でも封を使う前に署名を確かめ、
   合わなければ封なし（広げない・狭める物は効かせる）。

続き（Codex が次に試す予定だった 5 つを、こちらで反例にした物）: 番号の重なった記録・共通の台帳の止めると打ち直し・
手元の鍵の封を別の鍵で通す・期限と封の時刻が同じ秒・封の後に足した「よい」が前の「だめ」を上書きするか。
"""
from __future__ import annotations

import json
import pathlib
import shutil

from thth.register import judgment as J
from thth.register import route as R
from thth.register import seal as S
from thth.register import vessel as V
from thth.register.errors import RegisterRefused
from tests.test_register_route import (  # noqa: F401
    BRAZIL, MILK, SEALED_AT, accept, baseline, env, jrun, judge, ledger, use, verify, words,
)

import pytest

NOW = "2027-03-21T10:00:00+09:00"


def _outlets(led, change):
    path = led / ".thth/outlets.json"
    data = json.loads(path.read_text())
    change(data["outlets"])
    path.write_text(json.dumps(data, ensure_ascii=False))


def publish(capsys, led, outlet, site, into: pathlib.Path, *previous):
    """CI の台本のとおりに確かめて、緑なら「配った物」（封と配った時の判断の記録の頭）を公開の場所に置く。"""
    into.parent.mkdir(parents=True, exist_ok=True)
    rc, v = verify(capsys, led, outlet, site, NOW, *(previous or ("--no-previous",)), "--publish", into)
    assert rc == 0 and v["ok"], v
    assert into.exists()
    return v


# ------------------------------------------------------------------ 穴 1: 止めるで外して配ったあと、前に戻す

def _drop_ledger(env, capsys):
    led, site = ledger(env, "kopicha"), env / "site"
    _outlets(led, lambda o: o["facts"].update(on_narrow="drop"))
    baseline(capsys, led, "facts")
    accept(capsys, led, "facts", site)                      # 世代 1
    pub1 = env / "public/1.json"
    publish(capsys, led, "facts", site, pub1)
    old_j = pathlib.Path(J.path_of(str(led))).read_bytes()
    old_csv = (site / "data/knowledge/claims.csv").read_bytes()
    judge(capsys, led, BRAZIL, "--stop", "--outlet", "facts")
    rc, v, err = jrun(capsys, "register", "export", led, "--outlet", "facts", "--to", site, "--as-of", NOW)
    assert rc == 0 and v["generation"] == 1, err             # 封はし直さない（CI に鍵は無い）
    assert b"c-origin-brazil-024" not in (site / "data/knowledge/claims.csv").read_bytes()
    pub2 = env / "public/2.json"
    v = publish(capsys, led, "facts", site, pub2, "--previous", pub1)
    assert v["warnings"][-1]["word"] == "dropped"
    return led, site, old_j, old_csv, pub2


def test_穴1_止めるで外して配ったあと止めるの行と材料を戻すと赤(env, capsys):
    led, site, old_j, old_csv, pub2 = _drop_ledger(env, capsys)
    pathlib.Path(J.path_of(str(led))).write_bytes(old_j)
    (site / "data/knowledge/claims.csv").write_bytes(old_csv)
    rc, v = verify(capsys, led, "facts", site, NOW, "--previous", pub2)
    assert rc != 0 and "judgments_not_continued" in words(v), v
    assert b"c-origin-brazil-024" in (site / "data/knowledge/claims.csv").read_bytes()


def test_穴1_材料だけ戻しても赤_続きの配りは緑(env, capsys):
    led, site, old_j, old_csv, pub2 = _drop_ledger(env, capsys)
    good = (site / "data/knowledge/claims.csv").read_bytes()
    (site / "data/knowledge/claims.csv").write_bytes(old_csv)
    rc, v = verify(capsys, led, "facts", site, NOW, "--previous", pub2)
    assert rc != 0 and "narrowed_after_seal" in words(v), v
    (site / "data/knowledge/claims.csv").write_bytes(good)
    publish(capsys, led, "facts", site, env / "public/3.json", "--previous", pub2)


def test_穴1_配った物の控えが無い前の封は読まない(env, capsys):
    """封だけを写した物（配った時の判断の記録の頭が無い）は「前に配った物」として受けない。"""
    led, site, old_j, old_csv, pub2 = _drop_ledger(env, capsys)
    bare = env / "public/bare.json"
    shutil.copy(R.seal_path(str(led), "facts"), bare)
    rc, v = verify(capsys, led, "facts", site, NOW, "--previous", bare)
    assert rc != 0 and "previous_not_deployed" in words(v), v
    # 配った物の控えの頭を、今の封より短く書き換えても（封の頭より前へは）戻せない
    data = json.loads(pub2.read_text())
    data["deployed"]["judgments"] = dict(data["body"]["judgments"])
    data["deployed"]["judgments"]["bytes"] -= 1
    bad = env / "public/bad.json"
    bad.write_text(json.dumps(data))
    rc, v = verify(capsys, led, "facts", site, NOW, "--previous", bad)
    assert rc != 0 and "previous_not_deployed" in words(v), v


# ------------------------------------------------------------------ 穴 2: 別の出し口の封

def test_穴2_出し口Aの封は出し口Bの確かめで通らない(env, capsys):
    led, site = ledger(env, "kopicha"), env / "site"
    _outlets(led, lambda o: o.__setitem__("facts2", dict(o["facts"])))
    baseline(capsys, led, "facts")
    accept(capsys, led, "facts", site)
    shutil.copy(R.seal_path(str(led), "facts"), R.seal_path(str(led), "facts2"))
    rc, v = verify(capsys, led, "facts2", site, NOW, "--no-previous")
    assert rc != 0 and "seal_other_outlet" in words(v), v
    # 手元でも、別の出し口の封は封として使わない
    rc, u = use(capsys, led, BRAZIL, "facts2", "fact")
    assert rc != 0 and u["sealed"] is False and u["seal"] == "other_outlet", u
    rc, v, err = jrun(capsys, "register", "export", led, "--outlet", "facts2", "--to", site, "--as-of", NOW, "--check")
    assert rc != 0 and v["code"] == "register_seal_unverified", v


# ------------------------------------------------------------------ 穴 3: 手元の use・export が署名を見ない

def _forge(led):
    """封の後に「止めない」を足し、封の中の判断の頭を書き換える（署名はそのまま）。"""
    R.stop(str(led), BRAZIL, outlet="facts", by="owner", at=NOW)
    R.stop(str(led), BRAZIL, outlet="facts", lift=True, by="attacker", at="2027-03-22T10:00:00+09:00")
    sp = pathlib.Path(R.seal_path(str(led), "facts"))
    seal = json.loads(sp.read_text())
    raw = R.judgments_raw(str(led))
    seal["body"]["judgments"] = {"bytes": len(raw), "count": len(R.parse_judgments(raw)), "sha256": R.sha(raw)}
    sp.write_text(json.dumps(seal))


def test_穴3_手元のuseとexportは署名の合わない封を封として使わない(env, capsys):
    led, site = ledger(env, "kopicha"), env / "site"
    baseline(capsys, led, "facts")
    accept(capsys, led, "facts", site)
    _forge(led)
    rc, u = use(capsys, led, BRAZIL, "facts", "fact")
    assert rc != 0 and not u["usable"] and u["sealed"] is False and u["seal"] == "signature", u
    rc, v, err = jrun(capsys, "register", "export", led, "--outlet", "facts", "--to", site, "--as-of", NOW, "--check")
    assert rc != 0 and v["code"] == "register_seal_unverified", v
    rc, v, err = jrun(capsys, "register", "export", led, "--outlet", "facts", "--to", site, "--as-of", NOW)
    assert rc != 0 and v["code"] == "register_seal_unverified", v
    # 受け入れも、署名の合わない前の封の上には重ねない（世代と頭を偽の封から継がない）
    rc, v, err = jrun(capsys, "register", "accept", led, "--outlet", "facts", "--to", site, "--as-of", NOW,
                      "--by", "masaru")
    assert rc != 0 and v["code"] == "register_seal_unverified", v


def test_穴3_公開鍵が手元に無ければ封なしとして答える(env, capsys, monkeypatch):
    led, site = ledger(env, "kopicha"), env / "site"
    baseline(capsys, led, "facts")
    accept(capsys, led, "facts", site)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(env / "nobody"))
    rc, u = use(capsys, led, BRAZIL, "facts", "fact")
    assert rc != 0 and u["sealed"] is False and u["seal"] == "public_key", u


# ------------------------------------------------------------------ 続きの反例 5 つ

def test_続き_番号の重なった記録への判断と封(env, capsys):
    """番号が重なった記録は、判断も止めるも断り、材料に載らない。封の後に重なれば、狭まるので赤。"""
    led, site = ledger(env, "kopicha"), env / "site"
    baseline(capsys, led, "facts")
    accept(capsys, led, "facts", site)
    claims = led / "data/knowledge/claims.csv"
    lines = claims.read_bytes().split(b"\n")
    dup = lines[2].replace("ナッツの香り".encode(), "チョコの香り".encode())
    claims.write_bytes(b"\n".join(lines[:3] + [dup] + lines[3:]))
    rc, v, err = jrun(capsys, "register", "judge", led, BRAZIL, "--question", "may_publish@facts", "--conclusion",
                      "good", "--by", "masaru")
    assert rc != 0 and v["code"] == "register_unit_ambiguous"
    rc, v, err = jrun(capsys, "register", "judge", led, BRAZIL, "--stop", "--by", "masaru")
    assert rc != 0 and v["code"] == "register_unit_ambiguous"
    rc, v = verify(capsys, led, "facts", site, NOW, "--no-previous")
    assert rc != 0 and "narrowed_after_seal" in words(v)
    # 重なりの行を材料に書き足しても、封の出力と合わない
    shutil.copy(claims, site / "data/knowledge/claims.csv")
    rc, v = verify(capsys, led, "facts", site, NOW, "--no-previous")
    assert rc != 0 and {"narrowed_after_seal", "material_mismatch"} <= words(v)
    # 受け入れ直すと、重なった 2 行は材料から外れる
    accept(capsys, led, "facts", site)
    assert b"c-origin-brazil-024" not in (site / "data/knowledge/claims.csv").read_bytes()
    rc, v = verify(capsys, led, "facts", site, NOW, "--no-previous")
    assert rc == 0 and v["ok"], v


SRC = "source:fujimoto-2021"


def _bib(env, capsys):
    common, nig, site = ledger(env, "common"), ledger(env, "nigami"), env / "nig-site"
    rc, v, err = jrun(capsys, "register", "take", nig, "--common", common, "--record", SRC, "--by", "masaru")
    assert rc == 0, err
    accept(capsys, nig, "bib", site, SEALED_AT, "--common", common)
    return common, nig, site


def test_続き_共通の止めるはメディアが採り直しても外れない(env, capsys):
    """共通で止めているあいだに、共通の記録が直り、メディアが新しい版を採って受け入れても、止めたまま。"""
    common, nig, site = _bib(env, capsys)
    R.stop(str(common), SRC, by="masaru", at="2027-03-20T11:00:00+09:00")
    path = common / "sources/fujimoto-2021.json"
    path.write_bytes(path.read_bytes().replace(b"Food Chemistry 342:128228", b"Food Chemistry"))
    v = R.take(str(nig), str(common), [], by="masaru", at="2027-03-20T12:00:00+09:00")   # 止めたあとに採る
    assert len(v["taken"]) == 1
    (site / "common/sources/fujimoto-2021.json").rename(env / "moved.json")   # 旧い材料は人が退ける（消さない）
    accept(capsys, nig, "bib", site, SEALED_AT, "--common", common)
    assert not (site / "common/sources/fujimoto-2021.json").exists()
    rc, v, err = jrun(capsys, "register", "export", nig, "--outlet", "bib", "--to", site, "--as-of", NOW,
                      "--common", common, "--check")
    assert {"record": "common:" + SRC, "why": "stopped"} in v["excluded"], v


def test_続き_共通の止めないはメディアが受け入れ直すまで広げない_採るの途中で止めて打ち直す(env, capsys):
    common, nig, site = _bib(env, capsys)
    judge(capsys, common, SRC, "--stop")
    (site / "common/sources/fujimoto-2021.json").rename(env / "moved.json")
    accept(capsys, nig, "bib", site, SEALED_AT, "--common", common)          # 止めたまま受け入れた（外れた）
    judge(capsys, common, SRC, "--resume")
    rc, v = verify(capsys, nig, "bib", site, NOW, "--no-previous", "--common", common)
    assert rc != 0 and "widened_after_seal" in words(v), v                   # 共通が止めなくても、すぐには広げない
    # 採るの途中で止まる（2 つめの記録が無い）→ 何も書かない → 打ち直せる
    rc, v, err = jrun(capsys, "register", "take", nig, "--common", common, "--record", SRC, "--record",
                      "source:nothing", "--by", "masaru")
    assert rc != 0 and v["code"] == "register_record_missing"
    before = R.judgments_raw(str(nig))
    rc, v, err = jrun(capsys, "register", "take", nig, "--common", common, "--record", SRC, "--by", "masaru")
    assert rc == 0 and v["same"] == [SRC] and R.judgments_raw(str(nig)) == before, err
    v = accept(capsys, nig, "bib", site, SEALED_AT, "--common", common)
    assert v["changes"]["entering"] == ["common:" + SRC + "=fact"]
    rc, v = verify(capsys, nig, "bib", site, NOW, "--no-previous", "--common", common)
    assert rc == 0 and v["ok"], v


def test_続き_手元の鍵で作った封はCIの鍵でない物では通らない(env, capsys, monkeypatch):
    led, site = ledger(env, "kopicha"), env / "site"
    baseline(capsys, led, "facts")
    accept(capsys, led, "facts", site)                       # 手元の鍵 A の封
    owner = S.public_info()["public_key_base64"]
    monkeypatch.setenv("XDG_CONFIG_HOME", str(env / "ci-config"))
    (env / "ci-config").mkdir()
    other = S.init_key()["public_key_base64"]               # CI の手元に別の鍵 B がある
    monkeypatch.setenv(S.ENV_PUBLIC, other)                 # CI の変数も B
    rc, v = verify(capsys, led, "facts", site, NOW, "--no-previous")
    assert rc != 0 and words(v) == {"signature"} and v["key"] == "env"
    monkeypatch.delenv(S.ENV_PUBLIC)                         # 変数が無く、手元の B から作る
    rc, v = verify(capsys, led, "facts", site, NOW, "--no-previous")
    assert rc != 0 and words(v) == {"signature"} and v["key"] == "local"
    monkeypatch.setenv(S.ENV_PUBLIC, owner)                  # CI の変数が A なら、手元の B があっても A で確かめる
    rc, v = verify(capsys, led, "facts", site, NOW, "--no-previous")
    assert rc == 0 and v["key"] == "env", v


def test_続き_期限と封の時刻が同じ秒(env, capsys):
    """期限の時刻ちょうどは「切れた」。受け入れ・手元・CI の 3 つで同じ答え。CI の時刻が封より前なら赤。"""
    led, site = ledger(env, "kopicha"), env / "site"
    baseline(capsys, led, "facts")
    judge(capsys, led, BRAZIL, "--question", "may_publish@facts", "--conclusion", "good", "--until", SEALED_AT)
    v = accept(capsys, led, "facts", site, SEALED_AT)
    assert b"c-origin-brazil-024" not in (site / "data/knowledge/claims.csv").read_bytes()
    rc, u, err = jrun(capsys, "register", "use", led, BRAZIL, "--outlet", "facts", "--as", "fact", "--as-of", SEALED_AT)
    assert rc != 0 and u["why"] == "expired"
    rc, v = verify(capsys, led, "facts", site, SEALED_AT, "--no-previous")
    assert rc == 0 and v["ok"], v
    rc, v = verify(capsys, led, "facts", site, "2027-03-20T09:59:59+09:00", "--no-previous")
    assert rc != 0 and "widened_after_seal" in words(v)
    # 止めるの期限も同じ: ちょうどの時刻には止まっていない・1 秒前は止まっている
    judge(capsys, led, MILK, "--stop", "--until", SEALED_AT)
    rc, u, err = jrun(capsys, "register", "use", led, MILK, "--outlet", "facts", "--as", "fact", "--as-of", SEALED_AT)
    assert rc == 0 and u["usable"], u
    rc, u, err = jrun(capsys, "register", "use", led, MILK, "--outlet", "facts", "--as", "fact", "--as-of",
                      "2027-03-20T09:59:59+09:00")
    assert rc != 0 and u["why"] == "stopped", u


def test_続き_封の後に足したよいは前のだめを上書きして広げない(env, capsys):
    led, site = ledger(env, "kopicha"), env / "site"
    baseline(capsys, led, "facts")
    judge(capsys, led, BRAZIL, "--question", "may_publish@facts", "--conclusion", "bad")
    accept(capsys, led, "facts", site)
    assert b"c-origin-brazil-024" not in (site / "data/knowledge/claims.csv").read_bytes()
    judge(capsys, led, BRAZIL, "--question", "may_publish@facts", "--conclusion", "good")
    rc, u = use(capsys, led, BRAZIL, "facts", "fact")
    assert rc != 0 and u["why"] == "not_accepted", u
    rc, v, err = jrun(capsys, "register", "export", led, "--outlet", "facts", "--to", site, "--as-of", NOW, "--check")
    assert rc == 0 and v["same_as_seal"] and not v["changes"]["entering"], v
    rc, v = verify(capsys, led, "facts", site, NOW, "--no-previous")
    assert rc == 0 and v["ok"], v
    # 材料に書き足せば赤（封の中の判断では使えない物が出ている）
    claims_src = (led / "data/knowledge/claims.csv").read_bytes()
    (site / "data/knowledge/claims.csv").write_bytes(claims_src)
    rc, v = verify(capsys, led, "facts", site, NOW, "--no-previous")
    assert rc != 0 and "material_mismatch" in words(v)
    # 封の中の「よい」のあとに封の外の「だめ」→ 封の外の「よい」と重ねても、だめが効く（狭める物は封を待たない）
    accept(capsys, led, "facts", site)
    judge(capsys, led, BRAZIL, "--question", "may_publish@facts", "--conclusion", "bad")
    rc, u = use(capsys, led, BRAZIL, "facts", "fact")
    assert rc != 0
