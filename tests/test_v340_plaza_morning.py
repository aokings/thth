"""施策の広場 第 7 段——observe の 0 段と次の一手（設計 3.4.0 §5・§9-6）。

見るのは:
  - 0 段「広場: 新着 n（project）・open の新着 m」。新着は第 2 段と同じ窓（前回の観測から・
    無ければ前日 JST）の中に置かれた書き込みと返信。account 1 本の observe では、その
    account が書いたものは数えない。数には分母。
  - 参加していなければ open は null と理由 `plaza_not_joined`。
  - **他の持ち主の project 範囲の書き込みは数えも見せもしない**（サーバ型の利用者でも）。
  - 最近追試が付いた書き込み 1 件（§9-6）。
  - `next_steps` の `kind: "plaza"`: 返信の付いた自分の施策・判定待ちの施策・判定の付いた
    施策をまだ試していない媒体（宣言の account と tried・trial でつながった施策の account
    で判定。`not_tried` の追試は試したことにしない）。
"""
from __future__ import annotations

import datetime
import json

import pytest

from thth import jst, morning, plaza
from tests.test_v340_plaza_store import owners, post, viewer  # noqa: F401  (fixture)


@pytest.fixture
def quiet(owners, monkeypatch):
    monkeypatch.setattr(morning, "_world", lambda *a, **k: morning.cell(cannot_say="no_watch_words"))
    return owners


def _tool(payload):
    return next(s for s in payload["sections"] if s["section"] == "tool")["value"]


def _steps(payload):
    node = next(s for s in payload["sections"] if s["section"] == "next_steps")["value"]
    return [step for step in node["steps"] if step["kind"] == "plaza"]


def _decl(account, decl_id):
    return {"schema_version": 1, "id": decl_id, "account": account, "hypothesis": "h",
            "change": "c", "decision": {"status": "proposed"},
            "baseline_post_ids": [], "changed_post_ids": []}


def test_新着は窓の中の書き込みと返信_自分の書いたものは数えない(quiet):
    now = jst.now_jst()
    old = post(title="おととい", account="kopicha-threads", now=now - datetime.timedelta(days=2))
    fresh = post(title="けさ", account="kopicha-threads", now=now - datetime.timedelta(hours=1))
    post(title="Bluesky 自身", account="kopicha-bsky", now=now - datetime.timedelta(hours=1))
    plaza.reply(old["plaza_id"], account="kopicha-mstdn", kind="comment", text="古い 1 件に新しい返信",
                by="m", viewer=viewer("kopicha-mstdn"), now=now - datetime.timedelta(minutes=30))
    post(title="OTHER-PRIVATE-TITLE", account="other-threads", by="o",
         now=now - datetime.timedelta(hours=1))
    payload = morning.build("kopicha-bsky", now=now, mark=False)
    cell = _tool(payload)["plaza"]
    # けさの 1 件と、おとといの 1 件への返信。Bluesky 自身の書き込みは数えない。
    assert cell["project_new"] == 2 and cell["project_denominator"] == 3
    assert cell["open_new"] is None and cell["open_reason"] == "plaza_not_joined"
    assert cell["cannot_say"] is None
    assert "OTHER-PRIVATE-TITLE" not in json.dumps(payload, ensure_ascii=False)
    whole = _tool(morning.build("kopicha", now=now, mark=False))["plaza"]
    assert whole["project_new"] == 3 and fresh["plaza_id"]


def test_openの新着は参加した他の持ち主のopenだけ(quiet):
    now = jst.now_jst()
    for project in ("kopicha", "other"):
        plaza.set_membership(project, joined=True, by="operator")
    post(title="other の公開", account="other-threads", by="o", visibility="open",
         now=now - datetime.timedelta(hours=2))
    post(title="OTHER-INSIDE", account="other-threads", by="o", now=now - datetime.timedelta(hours=2))
    cell = _tool(morning.build("kopicha", now=now, mark=False))["plaza"]
    assert cell["open_new"] == 1 and cell["open_denominator"] == 1 and cell["open_reason"] is None
    assert cell["project_new"] == 0


def test_サーバ型の利用者の1枚でも自分の範囲だけ(quiet):
    now = jst.now_jst()
    post(title="OTHER-INSIDE", account="other-threads", by="o", now=now - datetime.timedelta(hours=1))
    post(title="けさ", account="kopicha-threads", now=now - datetime.timedelta(hours=1))
    payload = morning.build("kopicha", now=now, mark=False, allowed_names=("kopicha-bsky",))
    cell = _tool(payload)["plaza"]
    assert cell["project_new"] == 1 and cell["project_denominator"] == 1
    assert "OTHER-INSIDE" not in json.dumps(payload, ensure_ascii=False)


def test_前回の観測から数える(quiet):
    now = jst.now_jst()
    post(title="観測の前", account="kopicha-threads", now=now - datetime.timedelta(hours=3))
    first = morning.build("kopicha-bsky", now=now, mark=True)
    assert _tool(first)["plaza"]["project_new"] == 1
    later = now + datetime.timedelta(hours=2)
    post(title="観測の後", account="kopicha-threads", now=now + datetime.timedelta(hours=1))
    cell = _tool(morning.build("kopicha-bsky", now=later, mark=False))["plaza"]
    assert cell["project_new"] == 1 and cell["since"] == jst.iso(now)


def test_次の一手_返信の付いた施策と判定待ち(quiet):
    now = jst.now_jst()
    measure = post(kind="measure", title="問いの冒頭", account="kopicha-threads",
                   now=now - datetime.timedelta(days=3))
    waiting = post(kind="measure", title="期間の途中", account="kopicha-threads",
                   until=jst.iso(now + datetime.timedelta(days=2)),
                   now=now - datetime.timedelta(days=3))
    plaza.reply(measure["plaza_id"], account="kopicha-bsky", kind="comment", text="Bluesky でも",
                by="b", viewer=viewer("kopicha-bsky"), now=now - datetime.timedelta(hours=2))
    steps = _steps(morning.build("kopicha-threads", now=now, mark=False))
    assert {"kind": "plaza", "candidate": "read_replies", "plaza_id": measure["plaza_id"],
            "account": "kopicha-threads", "title": "問いの冒頭", "n_new_replies": 1} in steps
    verdicts = [s["plaza_id"] for s in steps if s["candidate"] == "verdict"]
    assert verdicts == [measure["plaza_id"]] and waiting["plaza_id"] not in verdicts
    assert all("body" not in step for step in steps)
    plaza.update(measure["plaza_id"], account="kopicha-threads", by="t", verdict="adopted",
                 reason="効いた", viewer=viewer("kopicha-threads"))
    steps = _steps(morning.build("kopicha-threads", now=now, mark=False))
    assert not [s for s in steps if s["candidate"] == "verdict"]


def test_次の一手_まだ試していない媒体(quiet):
    now = jst.now_jst()
    measure = post(kind="measure", title="問いの冒頭", account="kopicha-threads",
                   declarations=[_decl("kopicha-threads", "d-threads")],
                   now=now - datetime.timedelta(days=3))
    # 判定の前は候補に出さない。
    assert not [s for s in _steps(morning.build("kopicha", now=now, mark=False))
                if s["candidate"] == "try_on_medium"]
    plaza.update(measure["plaza_id"], account="kopicha-threads", by="t", verdict="adopted",
                 reason="効いた", viewer=viewer("kopicha-threads"))
    untried = sorted(s["account"] for s in _steps(morning.build("kopicha", now=now, mark=False))
                     if s["candidate"] == "try_on_medium")
    assert untried == ["kopicha-bsky", "kopicha-mstdn"]
    # Mastodon で追試（自分の measure をリンク・再現しなかった）→ 試したことになる。
    mine = post(kind="measure", title="Mastodon の追試", account="kopicha-mstdn",
                declarations=[_decl("kopicha-mstdn", "d-mstdn")], by="m")
    plaza.reply(measure["plaza_id"], account="kopicha-mstdn", kind="trial", text=None,
                result="not_reproduced", measure_id=mine["plaza_id"], by="m",
                viewer=viewer("kopicha-mstdn"))
    # Bluesky の「試していない」追試は試したことにしない。
    plaza.reply(measure["plaza_id"], account="kopicha-bsky", kind="trial", text=None,
                result="not_tried", by="b", viewer=viewer("kopicha-bsky"))
    steps = [s for s in _steps(morning.build("kopicha", now=now, mark=False))
             if s["candidate"] == "try_on_medium"]
    assert [(s["account"], s["medium"], s["verdict"]) for s in steps] == [
        ("kopicha-bsky", "bluesky", "adopted")]
    # account 1 本の observe では、その account の候補だけ。
    only = [s for s in _steps(morning.build("kopicha-mstdn", now=now, mark=False))
            if s["candidate"] == "try_on_medium"]
    assert only == []


def test_他の持ち主の判定は候補にしない(quiet):
    for project in ("kopicha", "other"):
        plaza.set_membership(project, joined=True, by="operator")
    theirs = post(kind="measure", title="other の施策", account="other-threads", by="o",
                  visibility="open", declarations=[_decl("other-threads", "d-other")])
    plaza.update(theirs["plaza_id"], account="other-threads", by="o", verdict="adopted",
                 reason="効いた", viewer=viewer("other-threads"))
    steps = _steps(morning.build("kopicha", now=jst.now_jst(), mark=False))
    assert not [s for s in steps if s["plaza_id"] == theirs["plaza_id"]]


def test_最近追試が付いた書き込み(quiet, capsys):
    now = jst.now_jst()
    first = post(title="朝の問い", account="kopicha-threads", now=now - datetime.timedelta(days=1))
    second = post(title="夜の問い", account="kopicha-threads", now=now - datetime.timedelta(days=1))
    plaza.reply(first["plaza_id"], account="kopicha-bsky", kind="trial", text=None,
                result="reproduced", by="b", viewer=viewer("kopicha-bsky"),
                now=now - datetime.timedelta(hours=5))
    plaza.reply(second["plaza_id"], account="kopicha-mstdn", kind="trial", text=None,
                result="not_reproduced", by="m", viewer=viewer("kopicha-mstdn"),
                now=now - datetime.timedelta(hours=1))
    payload = morning.build("kopicha", now=now, mark=False)
    trial = _tool(payload)["plaza"]["recent_trial"]
    assert trial["plaza_id"] == second["plaza_id"] and trial["result"] == "not_reproduced"
    assert trial["trials"] == {"reproduced": 0, "not_reproduced": 1, "not_tried": 0, "denominator": 1}
    morning.render(payload, out=print)
    out = capsys.readouterr().out
    assert "広場: 新着 4（project 2 件のうち）・open: 参加していません" in out
    assert (f"広場の最近の追試: {second['plaza_id']}  夜の問い"
            "（再現した 0・再現しなかった 1・試していない 0／1 件）") in out
    assert "広場の施策を判定する" not in out


def test_人向けの次の一手(quiet, capsys):
    now = jst.now_jst()
    measure = post(kind="measure", title="問いの冒頭", account="kopicha-threads",
                   declarations=[_decl("kopicha-threads", "d")], now=now - datetime.timedelta(days=1))
    morning.render(morning.build("kopicha-threads", now=now, mark=False), out=print)
    assert f"広場の施策を判定する  kopicha-threads  {measure['plaza_id']}  問いの冒頭" in \
        capsys.readouterr().out
    plaza.update(measure["plaza_id"], account="kopicha-threads", by="t", verdict="adopted",
                 reason="効いた", viewer=viewer("kopicha-threads"))
    morning.render(morning.build("kopicha", now=now, mark=False), out=print)
    assert (f"広場の施策を bluesky で試す（まだ試していない媒体・判定 adopted）  kopicha-bsky  "
            f"{measure['plaza_id']}") in capsys.readouterr().out


def test_置き場が読めなければ言えないと言う(quiet, monkeypatch):
    monkeypatch.setattr(plaza, "load_all",
                        lambda: (_ for _ in ()).throw(plaza.PlazaError("plaza_store_unavailable")))
    payload = morning.build("kopicha", now=jst.now_jst(), mark=False)
    cell = _tool(payload)["plaza"]
    assert cell["cannot_say"] == "plaza_store_unavailable" and cell["project_new"] is None
    assert _steps(payload) == []
