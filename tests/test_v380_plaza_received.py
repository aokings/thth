"""3.8.0 D3 置いた側に届いた知らせ（設計 3.8.0 §D3）。

observe の 0 段に「あなたの書き込みに 追試 n（再現 a・再現せず b）・賛否 m」（前回の観測から）。
数えるのは置いた account 以外が付けた追試と賛否だけ。本文は出さない。分母は置いた書き込みの数。
"""
from __future__ import annotations

import datetime
import json

import pytest

from thth import jst, morning, plaza
from tests.test_v340_plaza_store import owners, post, reply  # noqa: F401  (fixture)
from tests.test_v380_plaza_owner import grouped, viewer  # noqa: F401  (fixture)


@pytest.fixture
def quiet(grouped, monkeypatch):
    monkeypatch.setattr(morning, "_world", lambda *a, **k: morning.cell(cannot_say="no_watch_words"))
    return grouped


def _received(payload):
    return next(s for s in payload["sections"] if s["section"] == "tool")["value"]["plaza"]["received"]


def test_あなたの書き込みに届いた追試と賛否_前回の観測から(quiet, capsys):
    now = jst.now_jst()
    plaza.set_owner("masaru", ["kopicha", "other"], by="operator")
    mine = post(account="kopicha-threads", title="朝の問い", visibility="owner",
                now=now - datetime.timedelta(days=2))
    post(account="kopicha-threads", title="もう 1 件", now=now - datetime.timedelta(days=2))
    old = now - datetime.timedelta(days=2) + datetime.timedelta(hours=1)
    reply(mine["plaza_id"], account="kopicha-bsky", kind="trial", result="reproduced",
          text=None, by="b", viewer=viewer("kopicha-bsky"), now=old)
    for account, result in (("kopicha-bsky", "reproduced"), ("other-threads", "not_reproduced"),
                            ("kopicha-mstdn", "not_tried")):
        reply(mine["plaza_id"], account=account, kind="trial", result=result, text="REPLY-TEXT",
              by="x", viewer=viewer(account), now=now - datetime.timedelta(hours=2))
    reply(mine["plaza_id"], account="other-threads", kind="agree", text=None, by="o",
          viewer=viewer("other-threads"), now=now - datetime.timedelta(hours=1))
    reply(mine["plaza_id"], account="kopicha-bsky", kind="disagree", text="理由", by="b",
          viewer=viewer("kopicha-bsky"), now=now - datetime.timedelta(hours=1))
    # 自分（置いた account）の返信は数えない。
    reply(mine["plaza_id"], account="kopicha-threads", kind="agree", text=None, by="t",
          viewer=viewer("kopicha-threads"), now=now - datetime.timedelta(minutes=30))
    payload = morning.build("kopicha-threads", now=now, mark=False)
    got = _received(payload)
    assert got["trials"] == {"reproduced": 1, "not_reproduced": 1, "not_tried": 1, "n": 3}
    assert got["agree"] == 1 and got["disagree"] == 1 and got["votes"] == 2
    assert got["denominator"] == 2 and got["n_posts_touched"] == 1
    assert "REPLY-TEXT" not in json.dumps(got, ensure_ascii=False)
    morning.render(payload, out=print)
    assert ("広場: あなたの書き込みに 追試 3（再現 1・再現せず 1・試していない 1）・賛否 2（賛成 1・反対 1）"
            "（前回の観測から・書き込み 2 件のうち 1 件）") in capsys.readouterr().out


def test_project全体のobserveでは同じprojectの返信は数えない(quiet):
    now = jst.now_jst()
    mine = post(account="kopicha-threads", title="朝の問い", now=now - datetime.timedelta(days=1))
    reply(mine["plaza_id"], account="kopicha-bsky", kind="trial", result="reproduced", text=None,
          by="b", viewer=viewer("kopicha-bsky"), now=now - datetime.timedelta(hours=1))
    got = _received(morning.build("kopicha", now=now, mark=False))
    assert got["trials"]["n"] == 0 and got["denominator"] == 1


def test_届いた知らせが無ければ行を出さない(quiet, capsys):
    now = jst.now_jst()
    post(account="kopicha-threads", title="静か", now=now - datetime.timedelta(days=1))
    payload = morning.build("kopicha-threads", now=now, mark=False)
    assert _received(payload)["trials"]["n"] == 0
    morning.render(payload, out=print)
    assert "あなたの書き込みに" not in capsys.readouterr().out
