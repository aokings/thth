"""段 12 の残り（案 `docs/案_段12_台帳とSNSの反応をつなぐ_2026-11-16.md` の 4・監査 5.24.0 の「軽い（残す物）」）。

- 4: `thth scenario seeds` に「反応の多かった台帳の記録」の列。出した投稿の `sent/`（と連投の実行記録）の `seeds` と、
  その投稿の 24h の返ってきた返信（返信の列と同じ計算）から、記録ごとに媒体ごとに数える。自分の投稿だけ・他人の本文を
  読まない・min_n・読めない物は cannot_say に出して 0 と混ぜない。
- 連投（threadthrow）: 束の頭の `seeds:` を段の実行記録の公開の行に控える（承認の指紋・凍結の記録には入れない）。器
  （`thth/register/posts.py`）は実行記録も読む（読むだけ）。
- 軽 3: 直した記録も事実の指摘の印も無いふつうの verify では `sent/` を読まない（壊れた行のある口座を毎回「言えない」に
  出さない）。要るとき（直した記録がある・印がある）はこれまでどおり言う。
- 軽 5: `marks.json` の `facts`・`marks` が dict でないとき、`_write` が黙って読み替えない。読めないとして断り、置き場を
  書き換えない（前向きな断り `reply_marks_unreadable`）。
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

import pytest

from tests.test_register_stage11 import media
from tests.test_register_stage12_posts import _post, _register, _replies, keys  # noqa: F401
from tests.test_v500_scenario import PROJECT
from tests.test_v590_scenario_seeds import _build, _column, seeds  # noqa: F401
from tests.test_v500_scenario import kp  # noqa: F401
from thth import accounts, cli, refusal_catalog, reply_marks, sent, threadrun
from thth.register import route as R

SEEDS = "claim:clm-bitter evidence:ev-bitter"


# ------------------------------------------------------------------ 連投の段に seeds を控える

def _seeded_bundle(seeds_line):
    from tests.test_thread_publish import bundle_text
    text = bundle_text()
    if seeds_line is None:
        return text
    head, sep, rest = text.partition("topic: コーヒー\n")
    return head + sep + f"seeds: {seeds_line}\n" + rest


def _publish_bundle(tmp_path, isolated_account_factory, seeds_line):
    from tests.conftest import init_git_pair
    from tests.test_thread_publish import FakeAdapter
    from thth import threadthrow
    pair = init_git_pair(tmp_path, seed_content=_seeded_bundle(seeds_line), seed_name="thread.md")
    account = isolated_account_factory(repo_dir=pair["work"], production=True,
                                       quiet_hours=None, min_interval_hours=0)
    at = datetime.datetime.fromisoformat("2026-09-15T19:00:00+09:00")
    results = threadthrow.publish_bundle(account["name"], "docs/sns/queue/thread.md",
                                         adapter_factory=lambda *_: FakeAdapter(), now=at)
    assert [r.action for r in results] == ["published"] * 3, [r.reason for r in results]
    return account, threadrun.load(results[0].run_id)


def test_連投_束の頭のseedsを段の実行記録に控える_凍結の記録には入れない(tmp_path, isolated_account_factory):
    _account, run = _publish_bundle(tmp_path, isolated_account_factory, SEEDS)
    assert [p.get("seeds") for p in run["posts"]] == [["claim:clm-bitter", "evidence:ev-bitter"]] * 3
    # 承認の対象（凍結の記録）は今までの 3 つの欄だけ
    assert all(set(r) == {"index", "post_id", "text_sha256"} for r in threadrun.frozen_records(run))


def test_連投_seedsの無い束は実行記録の形を変えない(tmp_path, isolated_account_factory):
    _account, run = _publish_bundle(tmp_path, isolated_account_factory, None)
    assert all("seeds" not in p for p in run["posts"])


def test_連投の投稿も直した記録を指す出した投稿として引ける(tmp_path, isolated_account_factory, keys):  # noqa: F811
    account, run = _publish_bundle(tmp_path, isolated_account_factory, SEEDS)
    led = media(tmp_path, name="nig")
    _register(led)
    found = R.impact(None, {"claim:clm-bitter"}, (), graph={"edges": []}, root=str(led))
    posts = found["posts"]
    assert posts["cannot_say"] == []
    got = [(p["post_id"], p["run_id"], p["step"], p["seeds"]) for p in posts["items"]]
    assert got == [(p["post_id"], run["run_id"], p["index"], ["claim:clm-bitter"]) for p in run["posts"]]
    assert all(p["account"] == account["name"] and p["sent_at"] for p in posts["items"])
    # 事実の指摘の印も、連投の段への返信から引ける
    reply_marks.mark_fact(account["name"], "R1", by="窓口", post_id=run["posts"][1]["post_id"])
    recheck = R.impact(None, (), (), root=str(led))["recheck"]
    assert recheck["marks"] == 1 and recheck["post_unknown"] == 0
    assert sorted(r["record"] for r in recheck["items"]) == ["claim:clm-bitter", "evidence:ev-bitter"]


def test_連投の実行記録が読めなければ言えないに出す(tmp_path, isolated_account_factory, keys):  # noqa: F811
    account = isolated_account_factory()
    _post(account["name"], "P1", "claim:clm-bitter")
    directory = Path(threadrun.runs_dir())
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "run-20260915T190000-0000abcd.json").write_text("{壊れた")
    led = media(tmp_path, name="nig")
    _register(led)
    posts = R.impact(None, {"claim:clm-bitter"}, (), graph={"edges": []}, root=str(led))["posts"]
    assert [p["post_id"] for p in posts["items"]] == ["P1"]
    assert {"account": None, "why": "runs_unreadable", "rows": 1} in posts["cannot_say"]


# ------------------------------------------------------------------ 軽 3: 要るときだけ sent/ を読む

def _broken_row(account_name):
    folder = Path(sent.dir_for(accounts.state_dir_for(account_name)))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "broken.json").write_text("{壊れた")


def test_軽3_直した記録も印も無ければsentを読まず壊れた行を毎回言わない(isolated_account_factory, keys, tmp_path):  # noqa: F811
    account = isolated_account_factory()
    name = account["name"]
    _post(name, "P1", "claim:clm-bitter")
    _broken_row(name)
    led = media(tmp_path, name="nig")
    _register(led)
    plain = R.impact(None, (), (), root=str(led))
    assert plain["posts"]["cannot_say"] == [] and plain["recheck"]["cannot_say"] == []
    assert plain["posts"]["count"] == 0 and plain["recheck"]["count"] == 0
    assert plain["posts"]["accounts_not_read"] == [name]
    # 直した記録があるとき（要るとき）は、これまでどおり壊れた行を言う
    changed = R.impact(None, {"claim:clm-bitter"}, (), graph={"edges": []}, root=str(led))
    assert {"account": name, "why": "sent_rows_unreadable", "rows": 1} in changed["posts"]["cannot_say"]
    assert [p["post_id"] for p in changed["posts"]["items"]] == ["P1"]
    # 事実の指摘の印があるとき（確かめ直しに要る）も言う
    reply_marks.mark_fact(name, "R1", by="窓口", post_id="P1")
    marked = R.impact(None, (), (), root=str(led))
    assert {"account": name, "why": "sent_rows_unreadable", "rows": 1} in marked["recheck"]["cannot_say"]
    assert marked["recheck"]["count"] == 1
    assert marked["posts"]["cannot_say"] == []


# ------------------------------------------------------------------ 軽 5: facts が dict でないとき黙って直さない

def _marks_path(name):
    return Path(accounts.state_dir_for(name)) / "reply_marks" / "marks.json"


def _write_marks_raw(name, text):
    path = _marks_path(name)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(text)
    path.chmod(0o600)


@pytest.mark.parametrize("raw", [
    json.dumps({"schema": "thth.reply_marks/1", "marks": {}, "facts": [["R9", {"mark": "fact_challenge"}]]}),
    json.dumps({"schema": "thth.reply_marks/1", "marks": [["R9", {"mark": "no_reply_needed"}]]}),
    json.dumps({"schema": "thth.reply_marks/1", "marks": {}, "facts": "R9"}),
    json.dumps([["marks", {}]]),
    "{壊れた",
])
def test_軽5_印の置き場が読めない形なら黙って直さず断る(isolated_account_factory, capsys, monkeypatch, raw):
    isolated_account_factory("one", handle="owner", media="threads")
    _post("one", "ROOT", "claim:clm-bitter")
    _replies("one", "ROOT", [("R1", "reader")])
    _write_marks_raw("one", raw)
    with pytest.raises(reply_marks.MarksUnreadable):
        reply_marks.mark_fact("one", "R1", by="窓口", post_id="ROOT")
    with pytest.raises(reply_marks.MarksUnreadable):
        reply_marks.mark("one", "R1", "no_reply_needed", by="窓口")
    assert _marks_path("one").read_text() == raw
    monkeypatch.delenv("THTH_ACTOR", raising=False)
    rc = cli.main(["reply-mark", "one", "R1", "--fact-challenge", "--by", "窓口", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and out["error"] == "reply_marks_unreadable"
    assert _marks_path("one").read_text() == raw
    entry = refusal_catalog.CATALOG["reply_marks_unreadable"]
    assert entry["why"] and entry["fix"]["text"] and entry["better"]


def test_軽5_dictの置き場はこれまでどおり書ける(isolated_account_factory):
    isolated_account_factory("one", handle="owner", media="threads")
    _write_marks_raw("one", json.dumps({"schema": "thth.reply_marks/1", "marks": {}}))
    reply_marks.mark_fact("one", "R1", by="窓口", post_id="ROOT")
    assert reply_marks.load_facts("one")[0]["R1"]["post_id"] == "ROOT"


# ------------------------------------------------------------------ 案の 4: シナリオの種に台帳の記録

def _seed_row(account, post_id, seeds_value, at="2026-10-20T07:00:30+09:00"):
    sent.write(accounts.state_dir_for(account), post_id=post_id, text="PRIVATE SENT", body_hash="h",
               sent_at=at, seeds=seeds_value)


def _seed_run(account, post_id, seeds_value):
    directory = Path(threadrun.runs_dir())
    directory.mkdir(parents=True, exist_ok=True)
    row = {"run_id": "run-20261024T070000-0000beef", "account": account, "rel_path": "docs/sns/queue/t.md",
           "posts": [{"index": 1, "state": "published", "post_id": post_id,
                      "posted_at": "2026-10-24T07:00:30+09:00", "seeds": seeds_value}]}
    (directory / f"{row['run_id']}.json").write_text(json.dumps(row))


def test_4_反応の多かった台帳の記録の列_媒体ごと_連投の実行記録も読む(seeds):  # noqa: F811
    _seed_row("kp-threads", "P1", "claim:a")              # 返信 3
    _seed_row("kp-threads", "P2", "claim:a claim:b")      # 返信 7
    _seed_row("kp-threads", "P3", None)                   # seeds の無い投稿（返信 0）
    # P4 は配信の台帳も実行記録も無い（返信 2）
    _seed_run("kp-threads", "P5", ["claim:b"])            # 連投の段（返信 2）
    _seed_row("kp-x", "X1", "claim:a")                    # 別の媒体（返信 4）
    _seed_row("kp-threads", "OLD", "claim:a", at="2026-09-01T07:00:30+09:00")   # 窓の外（数えない）
    payload = _build()
    assert [c["key"] for c in payload["columns"]][-1] == "records"
    column = _column(payload, "records")
    assert column["metric"] == "replies_back_24h" and column["sort_basis"]
    threads = column["by_medium"]["threads"]
    assert [(r["record"], r["count"], r["posts"]) for r in threads["rows"]] == [("claim:a", 10, 2), ("claim:b", 9, 2)]
    assert threads["seeded"] == 3 and threads["unseeded"] == 1 and threads["sent_missing"] == 1
    assert threads["n"] == 3 and threads["comparable"] is False and threads["median"] is None
    assert [(r["record"], r["count"]) for r in column["by_medium"]["x"]["rows"]] == [("claim:a", 4)]
    assert "below_min_n:threads" in column["cannot_say"]
    whole = json.dumps(payload, ensure_ascii=False)
    assert "PRIVATE" not in whole and "SOMEONE-ELSE" not in whole and "OTHER-REPLY-TEXT" not in whole


def test_4_seedsを控えた投稿が無ければ0と言わず言えないに出す(seeds):  # noqa: F811
    column = _column(_build(), "records")
    assert column["by_medium"]["threads"]["rows"] == []
    assert "no_seeded_posts_in_window" in column["cannot_say"]


def test_4_読めない配信の台帳は言えないに出し0と混ぜない(seeds):  # noqa: F811
    _seed_row("kp-threads", "P1", "claim:a")
    _broken_row("kp-threads")
    column = _column(_build(), "records")
    threads = column["by_medium"]["threads"]
    assert [(r["record"], r["count"]) for r in threads["rows"]] == [("claim:a", 3)]
    # 行の引けなかった投稿は「控えが無い」でなく「言えない」に数える
    assert threads["sent_missing"] == 0 and threads["unknown"] == 4
    assert "seeds_unreadable" in column["cannot_say"]
    assert {"account": "kp-threads", "why": "sent_rows_unreadable", "rows": 1} in column["read_problems"]


def test_4_文の出力にも列が出る(seeds, capsys):  # noqa: F811
    _seed_row("kp-threads", "P2", "claim:a")
    assert cli.main(["scenario", "seeds", PROJECT]) == 0
    out = capsys.readouterr().out
    assert "反応の多かった台帳の記録" in out and "claim:a" in out


# ------------------------------------------------------------------ 検収 段 12 の残り（audit-s12d）の直し

def _runs_dir():
    d = Path(threadrun.runs_dir())
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run_row(account, post_id, run_id="run-20261001T000000-0000aaaa", step=1):
    return {"run_id": run_id, "account": account, "posts": [
        {"index": step, "state": "published", "post_id": post_id, "posted_at": "2026-10-01T00:00:00+09:00",
         "seeds": ["claim:clm-bitter"]}]}


def _nig(tmp_path):
    led = media(tmp_path, name="nig")
    _register(led)
    return led


def test_危1_文の出力に出した投稿の候補の言えないも出す_重ねない(isolated_account_factory, keys, tmp_path, capsys):  # noqa: F811
    from thth.register import route_cli
    account = isolated_account_factory()
    _post(account["name"], "P1", "claim:clm-bitter")
    _broken_row(account["name"])
    led = _nig(tmp_path)
    found = R.impact(None, {"claim:clm-bitter"}, (), graph={"edges": []}, root=str(led))
    capsys.readouterr()
    route_cli.show_posts(found["posts"], found["recheck"])
    out = capsys.readouterr().out
    assert "P1" in out and out.count("sent_rows_unreadable") == 1, out
    # 印もあって両方が同じ物を言うときも 1 回だけ
    reply_marks.mark_fact(account["name"], "R1", by="窓口", post_id="P1")
    found = R.impact(None, {"claim:clm-bitter"}, (), graph={"edges": []}, root=str(led))
    route_cli.show_posts(found["posts"], found["recheck"])
    assert capsys.readouterr().out.count("sent_rows_unreadable") == 1


def test_検収軽1_口座がlistの実行記録はその行だけ落とす(isolated_account_factory, keys, tmp_path):  # noqa: F811
    account = isolated_account_factory()
    _post(account["name"], "P1", "claim:clm-bitter")
    (_runs_dir() / "run-20261001T000000-0000dead.json").write_text(json.dumps({"account": ["other"], "posts": []}))
    found = R.impact(None, {"claim:clm-bitter"}, (), graph={"edges": []}, root=str(_nig(tmp_path)))
    assert [p["post_id"] for p in found["posts"]["items"]] == ["P1"], found["posts"]
    assert {"account": None, "why": "runs_unreadable", "rows": 1} in found["posts"]["cannot_say"]
    assert found["recheck"]["count"] == 0 and "posts_unreadable" not in json.dumps(found["recheck"])


def test_検収軽2_実行記録の置き場のsymlinkとFIFOは読まない(isolated_account_factory, keys, tmp_path):  # noqa: F811
    account = isolated_account_factory()
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(_run_row(account["name"], "OUTSIDE")))
    os.symlink(outside, _runs_dir() / "run-20261001T000000-0000beef.json")
    os.mkfifo(_runs_dir() / "run-20261001T000000-0000f1f0.json")
    folder = Path(sent.dir_for(accounts.state_dir_for(account["name"])))
    folder.mkdir(parents=True, exist_ok=True)
    os.mkfifo(folder / "fifo.json")
    found = R.impact(None, {"claim:clm-bitter"}, (), graph={"edges": []}, root=str(_nig(tmp_path)))
    assert found["posts"]["items"] == []
    assert {"account": None, "why": "runs_unreadable", "rows": 2} in found["posts"]["cannot_say"]
    assert {"account": account["name"], "why": "sent_rows_unreadable", "rows": 1} in found["posts"]["cannot_say"]


def test_検収軽3_同じpost_idの実行記録の写しで二重に数えない_stepの型が混ざっても落ちない(
        isolated_account_factory, keys, tmp_path):  # noqa: F811
    account = isolated_account_factory()
    row = _run_row(account["name"], "T1")
    (_runs_dir() / "run-20261001T000000-0000aaaa.json").write_text(json.dumps(row))
    (_runs_dir() / "run-20261001T000000-0000aaaa.copy.json").write_text(json.dumps(row))
    odd = _run_row(account["name"], "T2", run_id="run-20261001T000000-0000bbbb", step="2")
    (_runs_dir() / "run-20261001T000000-0000bbbb.json").write_text(json.dumps(odd))
    posts = R.impact(None, {"claim:clm-bitter"}, (), graph={"edges": []}, root=str(_nig(tmp_path)))["posts"]
    assert posts["count"] == 2 and [p["post_id"] for p in posts["items"]] == ["T1", "T2"], posts
    assert posts["items"][1]["step"] is None


def test_検収軽4_設定の読めない口座の投稿は控えなしでなく言えない(isolated_account_factory, monkeypatch):
    from thth.register import posts as P
    isolated_account_factory("good")
    real = accounts.load_account

    def load(name):
        if name == "gone":
            raise accounts.AccountError("x")
        return real(name)
    monkeypatch.setattr(accounts, "load_account", load)
    got = P.seeds_by_post(["good", "gone"])
    assert "gone" in got["unreadable"] and "good" not in got["unreadable"]


def test_検収軽5_他のprojectの壊れた実行記録は数えず_口座の言えない物は全口座に広げない(
        isolated_account_factory, keys, tmp_path):  # noqa: F811
    from thth.register import posts as P
    mine = isolated_account_factory()
    isolated_account_factory("other", project="elsewhere")
    _post(mine["name"], "P1", "claim:clm-bitter")
    (_runs_dir() / "run-20261001T000000-0000cccc.json").write_text(json.dumps({"account": "other", "posts": "x"}))
    led = _nig(tmp_path)
    posts = R.impact(None, {"claim:clm-bitter"}, (), graph={"edges": []}, root=str(led))["posts"]
    assert posts["cannot_say"] == [], posts["cannot_say"]
    (_runs_dir() / "run-20261001T000000-0000dddd.json").write_text(json.dumps({"account": mine["name"], "posts": 1}))
    posts = R.impact(None, {"claim:clm-bitter"}, (), graph={"edges": []}, root=str(led))["posts"]
    assert posts["cannot_say"] == [{"account": mine["name"], "why": "runs_unreadable", "rows": 1}]
    isolated_account_factory("third")
    got = P.seeds_by_post([mine["name"], "third"])
    assert got["unreadable"] == [mine["name"]]


def test_検収軽6_accountsは配信の台帳を読めた口座_読んでいない口座は別の欄(isolated_account_factory, keys, tmp_path):  # noqa: F811
    account = isolated_account_factory()
    _post(account["name"], "P1", "claim:clm-bitter")
    led = _nig(tmp_path)
    plain = R.impact(None, (), (), root=str(led))["posts"]
    assert plain["accounts"] == [] and plain["accounts_not_read"] == [account["name"]]
    read = R.impact(None, {"claim:clm-bitter"}, (), graph={"edges": []}, root=str(led))["posts"]
    assert read["accounts"] == [account["name"]] and read["accounts_not_read"] == []


def test_検収軽7_印の置き場のschemaと知らない欄を書き込みで落とさない(isolated_account_factory):
    isolated_account_factory("one", handle="owner", media="threads")
    _write_marks_raw("one", json.dumps({"schema": "thth.reply_marks/2", "marks": {}, "later": {"keep": 1}}))
    reply_marks.mark_fact("one", "R1", by="窓口", post_id="ROOT")
    value = json.loads(_marks_path("one").read_text())
    assert value["schema"] == "thth.reply_marks/2" and value["later"] == {"keep": 1}
    assert value["facts"]["R1"]["post_id"] == "ROOT"


def test_残した軽1_どの口座か言えない壊れた実行記録があれば行の無い投稿は言えないに数える(seeds):  # noqa: F811
    """5.25.0 の監査の「軽い（残す物）」1。JSON でない実行記録はどの口座の物か言えないので、その口座の連投だった
    かもしれない行の無い投稿を「控えの行なし」（sent_missing）でなく「言えない」（unknown）に数える。"""
    _seed_row("kp-threads", "P1", "claim:a")
    directory = Path(threadrun.runs_dir())
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "run-20261024T070000-0000dead.json").write_text("{not json")
    column = _column(_build(), "records")
    threads = column["by_medium"]["threads"]
    assert threads["sent_missing"] == 0 and threads["unknown"] >= 1, threads
    assert any(p.get("why") == "runs_unreadable" and p.get("account") is None for p in column["read_problems"])
