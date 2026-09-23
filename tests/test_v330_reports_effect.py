"""3.3.0 B5 効いたことを見せる（あなたの project の報告: 開いている n・この版で閉じた m）。

`admin reports close --version` の版がいまの道具の版と一致するものを「この版で閉じた」
とする。自分の project の範囲だけ（他 project の報告は数えない）。本文は出さない。
"""
from __future__ import annotations

import json

import pytest

from thth import __version__, jst, morning, report_inbox


@pytest.fixture
def projects(isolated_account_factory, monkeypatch):
    monkeypatch.setattr(morning, "_world", lambda *a, **k: morning.cell(cannot_say="no_watch_words"))
    info = isolated_account_factory("kopicha-threads", project="kopicha")
    isolated_account_factory("kopicha-bsky", project="kopicha", media="bluesky")
    isolated_account_factory("other-threads", project="other")
    return info


def _tool(payload):
    return next(s for s in payload["sections"] if s["section"] == "tool")["value"]


def test_この版で閉じた報告の題と開いている件数を0段に出す(projects, capsys):
    fixed = report_inbox.file_report("kopicha-bsky", kind="bug", title="46 本が黙って止まった",
                                     body="本文は出さない", by="s")
    report_inbox.close(fixed["report_id"], by="masaru", reason="fixed", version=__version__)
    older = report_inbox.file_report("kopicha-threads", kind="request", title="前の版で直った",
                                     body="b", by="s")
    report_inbox.close(older["report_id"], by="masaru", reason="fixed", version="3.1.2")
    report_inbox.file_report("kopicha-threads", kind="friction", title="迷った", body="b", by="s")
    theirs = report_inbox.file_report("other-threads", kind="bug", title="他の題", body="b", by="o")
    report_inbox.close(theirs["report_id"], by="masaru", reason="fixed", version=__version__)

    payload = morning.build("kopicha", now=jst.now_jst(), mark=False)
    mine = _tool(payload)["project_reports"]
    assert mine["open"] == 1 and mine["m"] == 1 and mine["denominator"] == 3
    assert mine["version"] == __version__
    assert mine["closed_this_version"] == [{"report_id": fixed["report_id"],
                                            "title": "46 本が黙って止まった",
                                            "kind": "bug", "reason": "fixed"}]
    dumped = json.dumps(_tool(payload), ensure_ascii=False)
    assert "他の題" not in dumped and "本文は出さない" not in dumped
    morning.render(payload, out=print)
    assert (f"あなたの project の報告: 開いている 1・この版（{__version__}）で閉じた 1"
            "（46 本が黙って止まった）") in capsys.readouterr().out


def test_サーバ型の利用者の1枚でも自分の範囲だけ(projects):
    report_inbox.file_report("other-threads", kind="bug", title="他の題", body="b", by="o")
    payload = morning.build("kopicha", now=jst.now_jst(), mark=False,
                            allowed_names=("kopicha-threads",))
    mine = _tool(payload)["project_reports"]
    assert mine["open"] == 0 and mine["m"] == 0 and mine["closed_this_version"] == []


def test_置き場が読めなければ言えないと言う(projects, monkeypatch):
    monkeypatch.setattr(report_inbox, "load_all",
                        lambda: (_ for _ in ()).throw(report_inbox.ReportError("report_store_unavailable")))
    payload = morning.build("kopicha", now=jst.now_jst(), mark=False)
    mine = _tool(payload)["project_reports"]
    assert mine["cannot_say"] == "report_store_unavailable" and mine["open"] is None
