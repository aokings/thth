"""台帳の器 段 5 の検収（Fable 11/14）の重い穴 4 つと、段 7 の検収の 7-1（公開の写しの塩）の反例。

検収の答え: `thth-work/audit-s5/答え_段5の検収_Fable_2026-11-14.md` §3（再現は `sigbrute.py`・`interrupt.py`・
`attacks.py` の K2・K3・H1・H2）。直しの決めは本線（11/14）。

- 穴 1: 消したあと、台帳に残った封の署名と公開鍵から、消した値を当て直せない。
- 穴 2: 途中で止めて打ち直しても、控えの判断の記録に値の字が残らない。一覧を作り直せない打ち直しは rc が 0 でない。
  `erase done --place self_check` は、器が読み直して残りが無いときだけ受ける。
- 穴 3: 偽の結果の記録（`result.json`）だけでは `diff` と `check --against` は緑にならない。
- 穴 4: 消す求めを 1 つ通しても、結果に載っていない行（「だめ」）を書き換えて受け入れ直すと CI は赤。
- 7-1: 公開の写しには、記録の番号・版・素の指紋を出さない（塩を混ぜた要約だけ）。
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil

import pytest

from thth.register import adopt as A
from thth.register import erase as E
from thth.register import judgment as J
from thth.register import keep as K
from thth.register import route as R
from thth.register import seal as S
from thth.register import trail as T
from thth.register import vessel as V
from thth.register.errors import RegisterRefused
from tests.test_register_erase import (BRAZIL, FIX, MILK, SEALED_AT, _git, accept, env, erase_all,  # noqa: F401
                                       jrun, judge, publish, run, setup_ledger, use, verify, words)

CLAIMS = "data/knowledge/claims.csv"
PHONE = "09012340137"


def tree(led: pathlib.Path) -> dict:
    out = {}
    for directory, dirs, files in os.walk(led):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in files:
            path = pathlib.Path(directory) / name
            if not path.is_symlink():
                out[str(path.relative_to(led))] = path.read_bytes()
    return out


def plain_hits(led: pathlib.Path, *values) -> list:
    return sorted(p for p, d in tree(led).items() for v in values if v.encode() in d)


def jlines(led) -> list:
    return [json.loads(x) for x in pathlib.Path(J.path_of(str(led))).read_text(encoding="utf-8").splitlines()
            if x.strip()]


def write_jlines(led, items) -> None:
    pathlib.Path(J.path_of(str(led))).write_text(
        "".join(json.dumps(i, ensure_ascii=False, sort_keys=True) + "\n" for i in items), encoding="utf-8")


def request_and_scope(led, ref=BRAZIL, field="notes", at="2027-03-22T09:00:00+09:00") -> str:
    req = E.request(str(led), ref, field=field, by="masaru", at=at, why="personal")["request"]
    est = E.scope(str(led), req, ["all"], by="masaru", at=at)
    E.scope(str(led), req, ["all"], by="masaru", at=at, confirm=est["digest"])
    return req


def phone_ledger(env, capsys, *, outlets=("strict",), with_bad=False, private=False):
    """見本: BRAZIL の notes に電話の形の値。MILK と BRAZIL に says_so の「よい」。with_bad で MILK に後から「だめ」。"""
    led, site = env / "led", env / "site"
    shutil.copytree(FIX / "kopicha", led)
    if private:
        path = led / ".thth/outlets.json"
        decl = json.loads(path.read_text(encoding="utf-8"))
        for name in ("facts", "strict"):
            decl["outlets"][name]["kinds"] = {"claim": {"private_fields": ["notes"]}}
        path.write_text(json.dumps(decl, ensure_ascii=False), encoding="utf-8")
    A.row_set(str(led), BRAZIL, {"notes": PHONE}, by="masaru", at="2027-03-19T09:00:00+09:00")
    judge(capsys, led, MILK, "--question", "says_so", "--conclusion", "good", "--reason-text", "資料で確かめた")
    judge(capsys, led, BRAZIL, "--question", "says_so", "--conclusion", "good", "--reason-text", "確かめた")
    if with_bad:
        judge(capsys, led, MILK, "--question", "says_so", "--conclusion", "bad", "--reason-text", "元の資料が取り下げ")
    pubs = {}
    for outlet in outlets:
        accept(capsys, led, outlet, site)
        pubs[outlet] = publish(led, outlet, site, env / "published" / f"{outlet}.json")
    return led, site, pubs


# ================================================================== 穴 1: 封の古い署名から値を当て直せる

def test_穴1_消したあと台帳に残った物と公開鍵だけでは消した値を当て直せない(env, capsys):
    led, site = env / "led", env / "site"
    shutil.copytree(FIX / "kopicha", led)
    A.row_set(str(led), BRAZIL, {"notes": PHONE}, by="masaru", at="2027-03-19T09:00:00+09:00")
    rc, v, err = jrun(capsys, "register", "judge", led, "--baseline", "--outlet", "facts", "--by", "masaru")
    assert rc == 0, err
    accept(capsys, led, "facts", site)
    erase_all(capsys, led)
    after = tree(led)
    assert not any(PHONE.encode() in d for d in after.values())
    seal = json.loads(after[".thth/seals/facts.json"])
    # ---- 攻める側: 消したあとの台帳（塩も含む）・公開鍵・公開のコードだけで、残った署名らしい物を全部試す
    pem = S.local_public_pem()
    sigs = sorted({m.decode() for d in after.values() for m in re.findall(rb"[A-Za-z0-9+/]{86}==", d)})
    salt = after.get(".thth/seals/salt")
    work, wsite = env / "attacker", env / "attacker-site"
    shutil.copytree(led, work, ignore=shutil.ignore_patterns("seals", "erase", "kept", "batch"))
    wsite.mkdir()
    lines = [json.loads(x) for x in after[".thth/judgments.jsonl"].decode().splitlines() if x.strip()]
    shape = next(j for j in lines if "erased" not in j)
    text = after[CLAIMS].decode()
    row = next(x for x in text.splitlines(keepends=True) if x.startswith("c-origin-brazil-024,"))
    cells = row.rstrip("\n").split(",")
    found = []
    for n in range(200):
        cand = "0901234%04d" % n
        cells[11] = cand
        (work / CLAIMS).write_text(text.replace(row, ",".join(cells) + "\n"), encoding="utf-8")
        vessel = V.open_vessel(str(work))
        out = []
        for j in lines:
            if "erased" not in j:
                out.append(j)
                continue
            out.append(J.make(vessel, {"record": j["target"]["record"]}, j["question"], by=j["by"], at=j["at"],
                              path=j["path"], word=j["answer"]["word"], conclusion=j["answer"]["conclusion"],
                              reason_kind=j["reason"]["kind"], reason_text=(shape.get("reason") or {}).get("text"),
                              how=shape.get("how"), until=j["until"], scope=(shape.get("answer") or {}).get("scope"),
                              carries=j["carries"]))
        write_jlines(work, out)
        if not sigs:
            continue
        plan = R.accept_plan(str(work), "facts", str(wsite), seal["body"]["as_of"])
        body = dict(plan["body"], at=seal["body"].get("at"), public_key=seal["body"].get("public_key"))
        messages = [V.canonical(body).encode("utf-8")]
        if salt is not None and hasattr(R, "public_of"):
            messages.append(V.canonical(R.public_of(body, salt)).encode("utf-8"))
        if any(S.verify(m, sig, pem) for m in messages for sig in sigs):
            found.append(cand)
    assert found == [], f"当て直せた: {found}"
    assert "signature" not in seal or not seal["signature"], "範囲の封に署名が残っている"


def test_穴1_署名を外した封からも受け入れ直せて世代を継ぐ(env, capsys):
    led, site, published = setup_ledger(env, capsys)
    erase_all(capsys, led)
    seal = json.loads(pathlib.Path(R.seal_path(str(led), "facts")).read_text(encoding="utf-8"))
    assert not seal.get("signature")
    v = accept(capsys, led, "facts", site)
    assert v["generation"] == 2
    rc, v = verify(capsys, led, "facts", site, "--previous", published["facts"])
    assert rc == 0 and v["ok"], v


# ================================================================== 穴 2: 途中で止めて打ち直す

ADDR = "東京都千代田区架空町一丁目2番4号 運営者宅"   # setup_ledger の ADDR2（判断の理由の文にも入る）


@pytest.mark.parametrize("where", ["kept_objects", "batch", "snapshots"])
def test_穴2_前の版を除いたあとで止めて打ち直しても控えの判断の記録に値が残らない(env, capsys, monkeypatch, where):
    led, site, _ = setup_ledger(env, capsys)
    rc, kept, err = jrun(capsys, "register", "keep", led, "--by", "masaru")
    assert rc == 0, err
    old_judgments = pathlib.Path(J.path_of(str(led))).read_bytes()
    assert ADDR.encode() in old_judgments
    req = request_and_scope(led)
    real = E.PLACE_FUNCS[where]

    def broken(*a, **k):
        real(*a, **k)
        raise RuntimeError("落ちた")
    monkeypatch.setitem(E.PLACE_FUNCS, where, broken)
    with pytest.raises(RuntimeError):
        E.run(str(led), req, by="masaru", at="2027-03-22T09:10:00+09:00")
    monkeypatch.undo()
    rc, v, err = jrun(capsys, "register", "erase", "run", led, req, "--by", "masaru")
    assert plain_hits(led, ADDR) == [], plain_hits(led, ADDR)
    # 一覧を作り直せなかった打ち直しは、確かめを「確かめていない」とし、rc を 0 にしない
    assert rc != 0, v
    status = E.status(str(led), req)
    assert status["places"]["self_check"]["state"] == "not_checked"
    # 器の外の場所を持ち主が付けても、self_check が残る間は消し終わりにならない
    for place in E.OUTSIDE:
        E.done(str(led), req, place, by="masaru", at="2027-03-23T09:00:00+09:00", outcome="erased")
    assert E.status(str(led), req)["state"] == "unfinished"
    # 控えの置き場に、範囲の行を印でない形で持つ判断の記録を置き直すと、持ち主の「消した」は受けない
    digest = K.put_object(str(led), old_judgments)
    with pytest.raises(RegisterRefused) as got:
        E.done(str(led), req, "self_check", by="masaru", at="2027-03-23T09:00:00+09:00", outcome="erased")
    assert got.value.code == "register_erase_left"
    K.remove_object(str(led), digest)
    # 器が読み直して残りが無いときだけ受ける
    got = E.done(str(led), req, "self_check", by="masaru", at="2027-03-23T09:00:00+09:00", outcome="erased")
    assert got["state"] == "finished" and got["places"]["self_check"]["state"] == "erased"
    assert plain_hits(led, ADDR) == []


def test_穴2_範囲の封に署名が残っていれば持ち主の消したは受けない(env, capsys):
    led, site, _ = setup_ledger(env, capsys)
    req, v = erase_all(capsys, led)
    path = pathlib.Path(R.seal_path(str(led), "facts"))
    seal = json.loads(path.read_text(encoding="utf-8"))
    seal["signature"] = "A" * 86 + "=="
    path.write_text(json.dumps(seal), encoding="utf-8")
    result = E._load(str(led), req, E.RESULT_FILE)
    result["places"]["self_check"] = {"state": "not_checked"}
    E._save(str(led), req, E.RESULT_FILE, result)
    with pytest.raises(RegisterRefused) as got:
        E.done(str(led), req, "self_check", by="masaru", at="2027-03-23T09:00:00+09:00", outcome="erased")
    assert got.value.code == "register_erase_left"


# ================================================================== 穴 3: 偽の result.json

def _forge(led, req, **result):
    d = led / ".thth/erase" / req
    d.mkdir(parents=True, exist_ok=True)
    (d / "scope.json").write_text("{}", encoding="utf-8")
    (d / "result.json").write_text(json.dumps({"request": req, **result}), encoding="utf-8")


def test_穴3_偽の結果の記録だけでは番号の消えたdiffが緑にならない(env, capsys):
    led, site, _ = phone_ledger(env, capsys)
    _git(led, "init", "-q", "-b", "main")
    _git(led, "add", "-A")
    _git(led, "commit", "-q", "-m", "前")
    c = led / CLAIMS
    c.write_text("".join(x for x in c.read_text(encoding="utf-8").splitlines(keepends=True)
                         if not x.startswith("c-term-tea-grade-ctc-res-milktea1-24,")), encoding="utf-8")
    assert A.diff(str(led), "HEAD..now")["red"]
    _forge(led, "e-" + "0" * 16, erased_records=[MILK])
    v = A.diff(str(led), "HEAD..now")
    assert v["red"] and v["excused"] == [], v
    rc, out, err = run(capsys, "register", "diff", led, "HEAD..now")
    assert rc != 0


def test_穴3_偽の結果の記録と印の形でcheck_againstは緑にならない(env, capsys):
    led, site, _ = phone_ledger(env, capsys, with_bad=True)
    _git(led, "init", "-q", "-b", "main")
    _git(led, "add", "-A")
    _git(led, "commit", "-q", "-m", "前")
    ls = jlines(led)
    i = next(n for n, j in enumerate(ls) if j["target"]["record"] == MILK and j["answer"]["conclusion"] == "bad")
    req = "e-" + "0" * 16
    t = ls[i]
    ls[i] = {"schema": t["schema"], "id": "x-" + "1" * 16, "question": t["question"],
             "target": {"record": MILK, "ledger": "elsewhere"},
             "answer": {"word": t["answer"].get("word"), "conclusion": "bad"}, "reason": {"kind": None},
             "by": t["by"], "at": t["at"], "until": t.get("until"), "path": t.get("path"),
             "carries": t.get("carries"), "erased": {"request": req}}
    write_jlines(led, ls)
    _forge(led, req, rewritten_lines=[ls[i]["id"]])
    assert not T.against(str(led), "HEAD")["continues"]


def test_穴3_本物の消す求めでも受け入れ直すまではdiffとagainstは赤_受け入れ直すと通る(env, capsys):
    led, site, _ = setup_ledger(env, capsys)
    _git(led, "init", "-q", "-b", "main")
    _git(led, "add", "-A")
    _git(led, "commit", "-q", "-m", "消す前")
    req, _v = erase_all(capsys, led, field=None)
    v = A.diff(str(led), "HEAD..now")
    assert v["red"] and v["records"]["vanished"] == [BRAZIL]
    assert req in v.get("erase_unsealed", []), v
    assert not T.against(str(led), "HEAD")["continues"]
    accept(capsys, led, "facts", site)
    v = A.diff(str(led), "HEAD..now")
    assert not v["red"] and v["excused"] == [BRAZIL]
    assert T.against(str(led), "HEAD")["continues"]
    # 封の結果を書き換えると署名が合わず、通さない
    path = pathlib.Path(R.seal_path(str(led), "facts"))
    seal = json.loads(path.read_text(encoding="utf-8"))
    seal["body"]["erase"]["records"] = [BRAZIL, MILK]
    path.write_text(json.dumps(seal, ensure_ascii=False), encoding="utf-8")
    assert A.diff(str(led), "HEAD..now")["red"]


def test_穴3_出し口の無い台帳は消したあとの差を確かめられないと言う(env, capsys):
    led = env / "led"
    shutil.copytree(FIX / "kopicha", led)
    os.unlink(led / ".thth/outlets.json")
    A.row_set(str(led), BRAZIL, {"notes": PHONE}, by="masaru", at="2027-03-19T09:00:00+09:00")
    _git(led, "init", "-q", "-b", "main")
    _git(led, "add", "-A")
    _git(led, "commit", "-q", "-m", "消す前")
    erase_all(capsys, led, field=None)
    v = A.diff(str(led), "HEAD..now")
    assert v["red"] and v.get("erase_no_outlets") is True, v
    rc, out, err = run(capsys, "register", "diff", led, "HEAD..now", "--json")
    assert rc != 0
    from thth import refusal_catalog
    assert "封が無い" in refusal_catalog.CATALOG["register_record_vanished"]["fix"]["text"]


# ================================================================== 穴 4: 載っていない行の書き換え

def test_穴4_消す求めを1つ通しても載っていないだめの行を書き換えて受け入れ直すと赤(env, capsys):
    led, site, pubs = phone_ledger(env, capsys, with_bad=True)
    rc, u = use(capsys, led, MILK, "strict")
    assert rc != 0
    req, res = erase_all(capsys, led)
    ls = jlines(led)
    i = next(n for n, j in enumerate(ls) if j["target"]["record"] == MILK and j["answer"]["conclusion"] == "bad")
    assert "erased" not in ls[i] and ls[i]["id"] not in res["rewritten_lines"]
    ls[i] = dict(ls[i], seen={"version": "x", "depend": "x"})
    ls[i]["id"] = J.new_id(ls[i])
    write_jlines(led, ls)
    accept(capsys, led, "strict", site)
    rc, v = verify(capsys, led, "strict", site, "--previous", pubs["strict"])
    assert rc != 0 and "judgments_not_continued" in words(v), v


def test_穴4_印の行に決まった欄の外を足すと赤(env, capsys):
    led, site, pubs = phone_ledger(env, capsys)
    judge(capsys, led, BRAZIL, "--question", "says_so", "--conclusion", "bad", "--reason-text", "取り下げ")
    accept(capsys, led, "strict", site)
    prev = publish(led, "strict", site, env / "published" / "strict2.json", previous=pubs["strict"])
    erase_all(capsys, led)
    ls = jlines(led)
    i = next(n for n, j in enumerate(ls) if j["target"]["record"] == BRAZIL and j["answer"]["conclusion"] == "bad")
    assert "erased" in ls[i]
    ls[i] = dict(ls[i], target=dict(ls[i]["target"], ledger="elsewhere"))
    write_jlines(led, ls)
    with pytest.raises(RegisterRefused):
        J.read(str(led))                                   # 読む入口が、印の形の外の欄を断る


def test_穴4_単位の指し先を外した印の行はunit_droppedが無ければ通さない():
    a = {"question": "credit_holds", "target": {"record": "credit:x", "unit": "k:abc"},
         "answer": {"conclusion": "bad"}, "at": "t", "reason": {"kind": None}}
    b = dict(a, id="x-" + "1" * 16, erased={"request": "e-" + "1" * 16}, target={"record": "credit:x"})
    pair = J.skeleton_pair(a)
    assert J.line_continues(pair, b, lines={b["id"]}, requests={b["erased"]["request"]}) is not None
    c = dict(b, target={"record": "credit:x", "unit_dropped": True})
    assert J.line_continues(pair, c, lines={c["id"]}, requests={c["erased"]["request"]}) is None


def test_穴4_前に配った物が古い形なら消したあとの回は赤(env, capsys):
    led, site, pubs = phone_ledger(env, capsys)
    data = json.loads(pubs["strict"].read_text(encoding="utf-8"))
    for entry in data["deployed"]["lines"]:
        del entry[2:]                                      # 骨だけの古い形
    old = env / "published" / "old.json"
    old.write_text(json.dumps(data), encoding="utf-8")
    erase_all(capsys, led)
    accept(capsys, led, "strict", site)
    rc, v = verify(capsys, led, "strict", site, "--previous", old)
    assert rc != 0 and any(p.get("erase") == "previous_lines_missing" for p in v["problems"]), v


# ================================================================== 中: 印の「だめ」は出し口の条件を満たさない

def test_中_印の行は出し口の条件を満たす判断に数えない(env, capsys):
    led, site, _ = phone_ledger(env, capsys, outlets=())
    judge(capsys, led, BRAZIL, "--question", "says_so", "--conclusion", "bad", "--reason-text", "取り下げ")
    erase_all(capsys, led)
    vessel = V.open_vessel(str(led))
    js = J.read(str(led))
    assert any(J.is_erased(j) and j["answer"]["conclusion"] == "bad" for j in js)
    outlet = R.Outlet(name="badonly", audience="reader", kinds=["claim"], level="record", requires=[],
                      fact=[("says_so", "bad")], reservation=None, common=None, on_narrow="stop")
    ctx = R.Ctx(str(led), vessel, js, js, R.parse_time(SEALED_AT))
    got = R.decide(ctx, outlet, BRAZIL)
    assert got["as"] is None, got


# ================================================================== 7-1: 公開の写しに当て直せる指紋を出さない

def test_7_1_公開の写しには番号も版も素の指紋も無い(env, capsys):
    led, site, pubs = phone_ledger(env, capsys, private=True)
    text = pubs["strict"].read_text(encoding="utf-8")
    vessel = V.open_vessel(str(led))
    raw = R.judgments_raw(str(led))
    for needle in (BRAZIL, MILK, vessel.get(BRAZIL).version, vessel.get(MILK).version, R.sha(raw)):
        assert needle not in text, needle
    assert "body" not in json.loads(text) and "public" in json.loads(text)
    assert PHONE not in (site / CLAIMS).read_text(encoding="utf-8")       # 出さない欄


def test_7_1_塩を伏せると公開の写しと材料から出さない欄の値も理由の文の値も当てられない(env, capsys):
    led, site, pubs = phone_ledger(env, capsys, private=True)
    judge(capsys, led, MILK, "--question", "says_so", "--conclusion", "good", "--reason-text", "番号 4821 で確かめた")
    accept(capsys, led, "strict", site)
    pub2 = publish(led, "strict", site, env / "published" / "strict2.json", previous=pubs["strict"])
    public = pub2.read_text(encoding="utf-8")
    tokens = set(re.findall(r"[0-9a-f]{16,}", public))
    # 攻める側: 塩以外は全部知っている（台帳の写しから塩と封だけを伏せる）
    work = env / "attacker"
    shutil.copytree(led, work, ignore=shutil.ignore_patterns("seals", "erase", "kept", "batch"))
    raw = R.judgments_raw(str(led))
    text = (work / CLAIMS).read_text(encoding="utf-8")
    row = next(x for x in text.splitlines(keepends=True) if x.startswith("c-origin-brazil-024,"))
    hits = []
    for n in range(100, 200):
        cand = "0901234%04d" % n
        cells = row.rstrip("\n").split(",")
        cells[11] = cand
        version = V.bytes_version((",".join(cells) + "\n").encode())
        guesses = {version, V.bytes_version(",".join(cells).encode()), R.sha(raw.replace(b"4821", b"%04d" % n))}
        for g in guesses:
            g = g.split(":", 1)[-1]
            if any(g[:k] in tokens for k in (16, 32, 64)):
                hits.append(cand)
        for salt in (b"", b"\0" * 32):                      # 塩を当て推量しても要約は合わない
            if hasattr(R, "salted") and R.salted(salt, version) in public:
                hits.append(cand)
    assert hits == [], hits


def test_7_1_細目を書き換えると赤_塩を書き換えると赤_塩が無いと赤(env, capsys):
    led, site, pubs = phone_ledger(env, capsys)
    path = pathlib.Path(R.seal_path(str(led), "strict"))
    good = path.read_bytes()
    seal = json.loads(good)
    seal["body"]["fingerprints"]["tables"] = {"x": ["0" * 64]}
    path.write_text(json.dumps(seal, ensure_ascii=False), encoding="utf-8")
    rc, v = verify(capsys, led, "strict", site, "--previous", pubs["strict"])
    assert rc != 0 and "signature" in words(v), v
    path.write_bytes(good)
    salt_file = pathlib.Path(R.salt_path(str(led)))
    salt = salt_file.read_bytes()
    assert len(salt) == 32 and (salt_file.stat().st_mode & 0o777) == 0o600
    salt_file.write_bytes(b"\1" * 32)
    rc, v = verify(capsys, led, "strict", site, "--previous", pubs["strict"])
    assert rc != 0 and "signature" in words(v), v
    os.unlink(salt_file)
    rc, v = verify(capsys, led, "strict", site, "--previous", pubs["strict"])
    assert rc != 0 and "salt_missing" in words(v), v
    rc, u = use(capsys, led, MILK, "strict")
    assert rc != 0
    salt_file.write_bytes(salt)
    os.chmod(salt_file, 0o600)
    rc, v = verify(capsys, led, "strict", site, "--previous", pubs["strict"])
    assert rc == 0, v
    # 塩は材料にも配る物にも入らない
    for p in list(site.rglob("*")) + [pubs["strict"]]:
        if p.is_file():
            assert salt not in p.read_bytes()


def test_7_1_前の写しが古い形なら赤で案内する(env, capsys):
    led, site, pubs = phone_ledger(env, capsys)
    seal = json.loads(pathlib.Path(R.seal_path(str(led), "strict")).read_text(encoding="utf-8"))
    raw = R.judgments_raw(str(led))
    old = {"body": seal["body"], "signature": seal["signature"],
           "deployed": {"outlet": "strict", "generation": seal["body"]["generation"],
                        "judgments": {"bytes": len(raw), "sha256": R.sha(raw)}, "lines": [], "at": "x",
                        "dropped": []}}
    path = env / "published" / "old.json"
    path.write_text(json.dumps(old), encoding="utf-8")
    rc, v = verify(capsys, led, "strict", site, "--previous", path)
    assert rc != 0 and "previous_old_form" in words(v), v
    from thth import refusal_catalog
    detail = refusal_catalog.CATALOG["register_seal_unverified"]["by_detail"]["previous_old_form"]
    assert "配り直" in detail["fix"]
