"""段 12 の 1〜3（案 `docs/案_段12_台帳とSNSの反応をつなぐ_2026-11-16.md`・道具の側だけ・読むだけ・赤にしない）。

- 1: 出した投稿の配信の台帳（`sent/`）に、原稿の頭の `seeds:` を控える。承認の指紋には入れない。無ければ欄を足さない。
- 2: 台帳の訂正の影響の候補（`verify`・`fix`・`health`）に、直した記録と候補を `seeds` で指す出した投稿を並べる。
  本文は入れない。読めない口座の台帳は `cannot_say` に名前を出し、0 と混ぜない。判定（ok・赤の語・rc）は変えない。
- 3: `thth reply-mark --fact-challenge`（読者が事実を指摘した返信）。印の付いた返信の投稿が指す記録を「確かめ直しの
  候補」に。未回答の数え方は変えない（事実の指摘は外さない）。

見本は CSV と JSON だけ（PyYAML の無い箱でも走る）。偽の配信の台帳と偽の返信の台帳だけ（外の API に触らない）。
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

import pytest

from tests.conftest import commit_and_push_path, make_queue_text
from tests.test_register_stage11 import BASE_AT, NOW, accept, media, words
from thth import accounts, cli, core, jst, reply_marks, sent, unanswered
from thth.adapters import base as adapter_base
from thth.register import adopt as A
from thth.register import fix as F
from thth.register import health as H
from thth.register import ledger as L
from thth.register import route as R
from thth.register import seal as S
from thth.register.errors import RegisterRefused

SEEDS = "claim:clm-bitter evidence:ev-bitter common:source:src-behrens"
THROW_NOW = datetime.datetime.fromisoformat("2026-09-09T10:00:00+09:00")
AT = "2027-03-20T10:00:00+09:00"


@pytest.fixture
def keys(tmp_path, monkeypatch):
    for key, sub in (("HOME", "home"), ("XDG_CONFIG_HOME", "config")):
        (tmp_path / sub).mkdir(exist_ok=True)
        monkeypatch.setenv(key, str(tmp_path / sub))
    for key in ("THTH_REGISTERS_DIR", "THTH_PRINCIPALS_DIR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv(S.ENV_PUBLIC, raising=False)
    try:
        S.openssl()
    except RegisterRefused:
        pytest.skip("OpenSSL 3 か 4 が決まった場所に無い")
    S.init_key()
    return tmp_path


def _seeded_queue(queue_dir, name, seeds=SEEDS):
    text = make_queue_text()
    head, sep, rest = text.partition("\n")
    path = os.path.join(queue_dir, name)
    os.makedirs(queue_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(head + sep + (f"seeds: {seeds}\n" if seeds is not None else "") + rest)
    commit_and_push_path(path, message=f"test: {name}")
    return path


class _Spy:
    def __init__(self, post_id):
        self.post_id = post_id

    def publish(self, post, *, dry_run, on_container_created=None):
        return adapter_base.PublishResult(post_id=self.post_id, url=None, ts="2026-09-09T10:00:30+09:00")


def _throw(account, post_id):
    return core.throw_once(account["name"], production_flag=True,
                           adapter_factory=lambda *_: _Spy(post_id), now=THROW_NOW)


# ------------------------------------------------------------------ 1. 出した投稿に番号を控える

def test_1_公開した投稿の配信の台帳にseedsを控える_無ければ欄を足さない(isolated_account_factory):
    account = isolated_account_factory(production=True)
    _seeded_queue(account["queue_dir"], "a.md")
    result = _throw(account, "P1")
    assert result.exit_code == 0 and result.post_id == "P1", result
    row = sent.read(accounts.state_dir_for(account["name"]), "P1")
    assert row["seeds"] == SEEDS.split()


def test_1_seedsの無い原稿は今までの形のまま(isolated_account_factory):
    account = isolated_account_factory(production=True)
    _seeded_queue(account["queue_dir"], "b.md", seeds=None)
    assert _throw(account, "P2").exit_code == 0
    assert "seeds" not in sent.read(accounts.state_dir_for(account["name"]), "P2")


def test_1_崩れた番号は落とすだけで断らない(tmp_path):
    assert sent.seeds_field("claim:a  bad 日本語:x claim:a common:source:s-1 :x") == ["claim:a", "common:source:s-1"]
    assert sent.seeds_field(None) == [] and sent.seeds_field(3) == []
    sent.write(str(tmp_path), post_id="Q1", text="t", body_hash="h", sent_at=AT, seeds="  ")
    assert "seeds" not in sent.read(str(tmp_path), "Q1")


def test_1_はてなの記事の材料はseedsを持つが指紋には入らない():
    from thth import hatena_entry
    import inspect
    src = inspect.getsource(hatena_entry.material)
    head, _sep, tail = src.partition("FINGERPRINTS[")
    assert "seeds" not in head, "seeds を指紋の材料に入れていない"
    assert '"seeds": fm.get("seeds")' in tail


# ------------------------------------------------------------------ 2・3 を通しで

def _register(led, project="nigamilab", name="nig"):
    L.write_new(name, L.build(name, project=project, path=str(led), kind="media"))
    return name


def _post(account_name, post_id, seeds, at="2026-09-09T10:00:30+09:00"):
    sent.write(accounts.state_dir_for(account_name), post_id=post_id, text="PRIVATE SENT", body_hash="h",
               sent_at=at, seeds=seeds)


def _replies(account_name, root, rows, at="2026-09-09T09:00:00+09:00"):
    cfg = accounts.load_account(account_name)
    directory = Path(accounts.data_dirs(cfg, account_name)["replies"])
    directory.mkdir(parents=True, exist_ok=True)
    lines = [{"id": rid, "username": user, "text": "PRIVATE REPLY", "timestamp": at, "post_id": root}
             for rid, user in rows]
    lines.append({"kind": "fetch", "post_id": root, "collected_at": at})
    (directory / (root + ".ndjson")).write_text("".join(json.dumps(r) + "\n" for r in lines))


def test_通しで_seedsを控える_記録を直す_投稿が候補に出る_印を付ける_確かめ直しの候補に出る(
        isolated_account_factory, keys, tmp_path, capsys, monkeypatch):
    account = isolated_account_factory(production=True, handle="owner")
    name = account["name"]
    # 1. 原稿の seeds を控えて出す（承認の 2 段めのあとの公開と同じ道）
    _seeded_queue(account["queue_dir"], "a.md", seeds="evidence:ev-bitter")
    assert _throw(account, "P1").exit_code == 0
    _post(name, "P2", "claim:clm-other")              # ほかの記録を指す投稿（候補に出ない）
    _post(name, "P3", None)                           # seeds の無い投稿
    led = media(tmp_path, name="nig")
    _register(led)
    R.baseline(str(led), "site", by="masaru", at=BASE_AT)
    out = tmp_path / "nig-out"
    accept(led, "site", out / "site")
    before = R.verify(str(led), "site", str(out / "site"), NOW, no_previous=True)
    assert before["ok"], before
    assert before["impact"]["posts"]["count"] == 0 and before["impact"]["recheck"]["count"] == 0

    # 2. 根拠を直す → 直した記録を指す P1 が出した投稿として候補に出る（本文は入れない）
    A.row_set(str(led), "evidence:ev-bitter", {"finding": "直した"}, by="masaru", at=AT)
    v = R.verify(str(led), "site", str(out / "site"), NOW, no_previous=True)
    posts = v["impact"]["posts"]
    assert posts["project"] == "nigamilab" and posts["accounts"] == [name] and posts["cannot_say"] == []
    assert [(p["post_id"], p["seeds"], p["account"], p["media"]) for p in posts["items"]] == [
        ("P1", ["evidence:ev-bitter"], name, "threads")]
    assert posts["items"][0]["sent_at"]
    assert "PRIVATE" not in json.dumps(v, ensure_ascii=False)
    # 判定は今のまま（投稿の欄を足さない計算と ok・赤の語が同じ）
    from thth.register import posts as P
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(P, "attach", lambda found, root, vessel=None: found)
        plain = R.verify(str(led), "site", str(out / "site"), NOW, no_previous=True)
    assert "posts" not in plain["impact"]
    assert plain["ok"] == v["ok"] and plain["problems"] == v["problems"]

    # 3. P1 への返信に事実の指摘の印 → 指す記録が確かめ直しの候補に（未回答の数え方は変えない）
    _replies(name, "P1", [("R1", "reader_a"), ("R2", "reader_b")], at="2026-09-09T10:30:00+09:00")
    now = jst.parse("2026-09-09T11:00:00+09:00")
    counted = unanswered.answer(name, now=now)
    monkeypatch.delenv("THTH_ACTOR", raising=False)
    rc = cli.main(["reply-mark", name, "R1", "--fact-challenge", "--by", "窓口", "--reason", "数が違う", "--json"])
    got = json.loads(capsys.readouterr().out)
    assert rc == 0 and got["fact_challenge"]["post_id"] == "P1" and "mark" not in got
    after = unanswered.answer(name, now=now)
    assert after["n_total"] == counted["n_total"] == 2 and after["n_marked"] == counted["n_marked"] == 0
    rc, h, err = _jrun(capsys, "register", "health", "nig")
    item = h["groups"]["seal"]["impact_candidates"]
    recheck = item["recheck"]
    assert [(r["record"], r["post_id"], r["reply_id"], r["marked_by"], r["in_register"]) for r in recheck["items"]] == [
        ("evidence:ev-bitter", "P1", "R1", "窓口", True)]
    assert recheck["marks"] == 1 and recheck["unseeded"] == 0 and recheck["post_unknown"] == 0
    assert "PRIVATE" not in json.dumps(h, ensure_ascii=False)
    v2 = R.verify(str(led), "site", str(out / "site"), NOW, no_previous=True)
    assert v2["impact"]["recheck"]["count"] == 1
    assert v2["ok"] == v["ok"] and words(v2) == words(v)
    # 文の出力にも名指しが出る
    rc, text, _ = _run(capsys, "register", "health", "nig")
    assert rc == 0 and "確かめ直しの候補 1 件" in text and "この記録を指す出した投稿 1 件" in text
    # 印を外せば候補から消える（未回答は今までどおり）
    assert cli.main(["reply-mark", name, "R1", "--undo", "--fact-challenge", "--by", "窓口"]) == 0
    capsys.readouterr()
    assert R.verify(str(led), "site", str(out / "site"), NOW, no_previous=True)["impact"]["recheck"]["count"] == 0


def test_2_fixも出した投稿を候補に出す(isolated_account_factory, keys, tmp_path, capsys):
    account = isolated_account_factory()
    _post(account["name"], "P9", "claim:clm-bitter")
    led = media(tmp_path, name="nig")
    _register(led)
    R.baseline(str(led), "site", by="masaru", at=BASE_AT)
    out = tmp_path / "nig-out"
    accept(led, "site", out / "site")
    value = F.fix(str(led), "evidence:ev-bitter", {"finding": "直した"}, outlet="site", to=str(out / "site"),
                  as_of=AT, by="claude-v", at=AT, reason_text="原文と照らした")
    assert not value["sealed"]
    # 直した根拠が支える主張（1 段の候補）を指す投稿
    assert [p["post_id"] for p in value["impact"]["posts"]["items"]] == ["P9"]


def test_2_読めない口座の台帳は言えないに名前を出し0と混ぜない(isolated_account_factory, keys, tmp_path):
    good = isolated_account_factory("good")
    bad = isolated_account_factory("bad")
    _post(good["name"], "G1", "claim:clm-bitter")
    _post(bad["name"], "B1", "claim:clm-bitter")
    folder = Path(sent.dir_for(accounts.state_dir_for("bad")))
    folder.chmod(0)
    try:
        led = media(tmp_path, name="nig")
        _register(led)
        found = R.impact(None, {"claim:clm-bitter"}, (), graph={"edges": []}, root=str(led))
    finally:
        folder.chmod(0o700)
    posts = found["posts"]
    if os.geteuid() == 0:
        pytest.skip("root では権限で読めなくできない")
    assert [p["post_id"] for p in posts["items"]] == ["G1"]
    assert {"account": "bad", "why": "sent_unreadable"} in posts["cannot_say"]
    assert "bad" not in posts["accounts"]


def test_2_控えの無い台帳は数を作らない(tmp_path, thth_root):
    led = tmp_path / "loose"
    led.mkdir()
    found = R.impact(None, {"claim:x"}, (), graph={"edges": []}, root=str(led))
    assert found["posts"]["count"] is None and found["recheck"]["count"] is None
    assert found["posts"]["cannot_say"] == [{"account": None, "why": "register_not_registered"}]
    assert found["count"] == 0  # 影響の候補そのものは変えない


def test_3_事実の指摘はほかの印と別に持ち_同じ返信に両方付けられる(isolated_account_factory):
    account = isolated_account_factory("one", handle="owner", media="threads")
    _post("one", "ROOT", "claim:clm-bitter")
    _replies("one", "ROOT", [("R1", "reader")])
    reply_marks.mark_fact("one", "R1", by="窓口", post_id="ROOT")
    reply_marks.mark("one", "R1", "no_reply_needed", by="窓口")
    marks, ok = reply_marks.load("one")
    facts, ok2 = reply_marks.load_facts("one")
    assert ok and ok2 and marks["R1"]["mark"] == "no_reply_needed" and facts["R1"]["post_id"] == "ROOT"
    assert reply_marks.unmark("one", "R1")["mark"] == "no_reply_needed"
    assert "R1" in reply_marks.load_facts("one")[0]
    raw = (Path(accounts.state_dir_for("one")) / "reply_marks" / "marks.json").read_text()
    assert "PRIVATE" not in raw and "reader" not in raw


def _run(capsys, *argv):
    rc = cli.main([str(a) for a in argv])
    out, err = capsys.readouterr()
    return rc, out, err


def _jrun(capsys, *argv):
    rc, out, err = _run(capsys, *argv, "--json")
    return rc, json.loads(out), err


def test_台帳の機械が字として読む置き場の形は配信の台帳と返信の印と同じ(tmp_path):
    from thth.register import posts as P
    assert sent.dir_for(str(tmp_path)) == os.path.join(str(tmp_path), P.SENT_DIR)
    assert P.MARKS_PATH == ("reply_marks", reply_marks.MARKS_FILE)
    assert P.FACT_CHALLENGE == reply_marks.FACT_CHALLENGE


# ------------------------------------------------------------------ 検収 段 12（audit-s12c）の直し

def _write_marks(account_name, value):
    path = Path(accounts.state_dir_for(account_name)) / "reply_marks" / "marks.json"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value, ensure_ascii=False))
    path.chmod(0o600)


def _sealed_nig(tmp_path):
    led = media(tmp_path, name="nig")
    _register(led)
    R.baseline(str(led), "site", by="masaru", at=BASE_AT)
    out = tmp_path / "nig-out"
    accept(led, "site", out / "site")
    return led, out


@pytest.mark.parametrize("bad", [["P1"], {"x": 1}, 7, "P\x001"])
def test_危1_印の置き場のpost_idが壊れていてもverify_health_fixの判定はattachを外したときと同じ(
        isolated_account_factory, keys, tmp_path, capsys, bad):
    from thth.register import posts as P
    from thth.register import vessel as V
    account = isolated_account_factory()
    name = account["name"]
    _post(name, "P1", "evidence:ev-bitter")
    _write_marks(name, {"schema": "thth.reply_marks/1", "marks": {},
                        "facts": {"R1": {"mark": "fact_challenge", "post_id": bad},
                                  "R\n2": {"mark": "fact_challenge", "post_id": "P1"}}})
    led, out = _sealed_nig(tmp_path)
    A.row_set(str(led), "evidence:ev-bitter", {"finding": "直した"}, by="masaru", at=AT)
    v = R.verify(str(led), "site", str(out / "site"), NOW, no_previous=True)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(P, "attach", lambda found, root, vessel=None: found)
        plain = R.verify(str(led), "site", str(out / "site"), NOW, no_previous=True)
    assert v["ok"] == plain["ok"] and v["problems"] == plain["problems"]
    assert v["impact"]["count"] == plain["impact"]["count"]
    recheck = v["impact"]["recheck"]
    assert recheck["count"] == 0 and recheck["marks"] == 0
    assert {"account": name, "why": "reply_marks_unreadable"} in recheck["cannot_say"]
    # health は rc 0 で JSON を返す（traceback で抜けない）
    rc, h, _err = _jrun(capsys, "register", "health", "nig")
    assert rc == 0 and h["groups"]["seal"]["impact_candidates"]["recheck"]["count"] == 0
    # fix は記録を書いたあとに落ちない（1 段めの形で返る）
    before = V.open_vessel(str(led)).unique("claim:clm-bitter").version
    value = F.fix(str(led), "claim:clm-bitter", {"text": "苦味の受容体はおよそ 25 種"}, outlet="site",
                  to=str(out / "site"), as_of=AT, by="claude-v", at=AT, reason_text="原文と照らした")
    assert V.open_vessel(str(led)).unique("claim:clm-bitter").version != before
    assert value["sealed"] is False and value["digest"] and "recheck" in value["impact"]


def test_危1_attachは中で何が起きても例外を出さず言えないに送る(tmp_path, thth_root, monkeypatch):
    from thth.register import posts as P

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(P, "_Index", lambda root: type("I", (), {"project": "p", "register": "r", "accounts": [],
                                                                  "rows": [], "cannot": [],
                                                                  "head": lambda self: {}})())
    monkeypatch.setattr(P, "rechecks", boom)
    found = {"changed": ["claim:x"], "count": 0, "candidates": []}
    got = R.impact(None, {"claim:x"}, (), graph={"edges": []}, root=str(tmp_path))
    assert got["count"] == 0 and got["candidates"] == []
    assert got["recheck"]["cannot_say"] == [{"account": None, "why": "posts_unreadable:RuntimeError"}]
    assert P.attach(found, str(tmp_path))["posts"]["count"] is None


def test_軽2_取り下げた投稿への事実の指摘は確かめ直しの候補にしない(isolated_account_factory, keys, tmp_path):
    account = isolated_account_factory()
    name = account["name"]
    _post(name, "P1", "evidence:ev-bitter")
    reply_marks.mark_fact(name, "R1", by="窓口", post_id="P1")
    led, out = _sealed_nig(tmp_path)
    found = R.impact(None, (), (), root=str(led))
    assert found["recheck"]["count"] == 1
    sent.mark_retracted(accounts.state_dir_for(name), "P1", retracted_at=AT, retracted_by="テスト",
                        retract_reason="test")
    found = R.impact(None, (), (), root=str(led))
    assert found["recheck"]["count"] == 0 and found["recheck"]["post_unknown"] == 1


def test_軽4_2つの印は1回の書き込みで付ける(isolated_account_factory, capsys, monkeypatch):
    isolated_account_factory("one", handle="owner", media="threads")
    _post("one", "ROOT", "claim:clm-bitter")
    _replies("one", "ROOT", [("R1", "reader")])
    calls = []
    real = reply_marks._write

    def counted(*a, **k):
        calls.append(k.get("field", "marks"))
        return real(*a, **k)
    monkeypatch.setattr(reply_marks, "_write", counted)
    monkeypatch.delenv("THTH_ACTOR", raising=False)
    rc = cli.main(["reply-mark", "one", "R1", "--no-reply-needed", "--fact-challenge", "--by", "窓口", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["mark"] == "no_reply_needed" and out["fact_challenge"]["post_id"] == "ROOT"
    assert len(calls) == 1
    # 書き込みが失敗すれば、どちらの印も残らない
    monkeypatch.setattr(reply_marks.server_files, "replace_at",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("full")))
    rc = cli.main(["reply-mark", "one", "R1", "--unreplyable", "--fact-challenge", "--by", "別の窓口"])
    capsys.readouterr()
    assert rc == 2
    marks, _ = reply_marks.load("one")
    facts, _ = reply_marks.load_facts("one")
    assert marks["R1"]["mark"] == "no_reply_needed" and facts["R1"]["by"] == "窓口"
