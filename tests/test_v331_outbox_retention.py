"""3.3.1 §5: 運用通知の事象を 30 日残し、「最後に送った運用通知」を board と observe に出す。

09-22 の停止の通知は outbox から消えていて、送ったかどうかを翌日に言えなかった。
届いたかどうかは道具には分からない（SMTP の受領は到達ではない）——人が受信箱と
照合できるよう、時刻・状態・受領した役割を残す。宛先は出さない。
"""
from __future__ import annotations

import json
import os

import pytest

from tests.test_incident_notifications import notify, row, setup  # noqa: F401 — fixture
from thth import accounts, cli, incident, morning


def _save_row(s, value):
    incident._save(os.path.join(s.state, incident.STATE_FILE), value)


def test_送り終えた事象を残し最後に送った通知を控える(setup):
    s = setup
    notify(s)
    notify(s, "success", result=False)
    value = row(s)
    assert [e["state"] for e in value["events"]] == ["blocked", "recovered"]
    sent = value["last_sent"]
    assert sent["state"] == "recovered" and sent["roles"] == ["admin", "user"]
    assert sent["reason"] == "healthy" and sent["at"]
    # 宛先は控えない。
    assert "example.org" not in json.dumps(sent)
    summary = incident.summary(s.cfg, s.state)["last_sent"]
    assert summary["basis"] == "recorded" and summary["state"] == "recovered"


def test_30日を過ぎた送り終えた事象は畳む_最後の1件は残す(setup):
    s = setup
    notify(s)
    notify(s, "success", result=False)
    value = row(s)
    for event in value["events"]:
        event["at"] = "2026-07-01T10:00:00+09:00"
    _save_row(s, value)
    notify(s, "success", result=False)   # 遷移なし・畳むだけ
    assert len(row(s)["events"]) == 1
    assert row(s)["last_sent"]["state"] == "recovered"   # 控えは畳まれても残る


def test_送り終えていない事象は古くても畳まない(setup, monkeypatch):
    s = setup
    notify(s)
    value = row(s)
    value["events"][0]["accepted"] = {}
    value["events"][0]["at"] = "2026-07-01T10:00:00+09:00"
    value["events"].append(dict(value["events"][0], id="f" * 32, accepted={}))
    _save_row(s, value)
    # SMTP が受け付けない（両方失敗）まま流す。
    monkeypatch.setattr(incident, "_send", lambda *a, **k: False)
    notify(s)
    assert len(row(s)["events"]) == 2


def test_30日以内でも数が溢れたら古い順に畳む(setup):
    s = setup
    notify(s)
    value = row(s)
    template = value["events"][0]
    value["events"] = [dict(template, id=f"{i:032x}") for i in range(60)]
    _save_row(s, value)
    notify(s)   # 同じ停止（遷移なし）
    assert len(row(s)["events"]) == incident.RETENTION_CAP


def test_控えの無い古いoutboxは残っている事象から言う(setup):
    s = setup
    notify(s)
    value = row(s)
    value.pop("last_sent")
    _save_row(s, value)
    summary = incident.last_sent(row(s))
    assert summary["basis"] == "event_at" and summary["state"] == "blocked"
    assert "事象の時刻" in incident.last_sent_line(summary)


def test_送った記録が無ければ記録なしと言う():
    assert incident.last_sent({"events": []}) is None
    assert "記録なし" in incident.last_sent_line(None)


def test_壊れた控えはoutboxを壊れたものとして扱う(setup):
    s = setup
    notify(s)
    value = row(s)
    value["last_sent"] = {"at": "x", "state": "blocked", "reason": "healthy", "roles": ["user"]}
    with pytest.raises(ValueError):
        incident._validate(value)
    value["last_sent"] = {"at": "2026-09-09T10:00:00+09:00", "state": "blocked",
                          "reason": "healthy", "roles": ["someone@example.org"]}
    with pytest.raises(ValueError):
        incident._validate(value)


def _outbox_for(account_name, sent):
    state = accounts.state_dir_for(account_name)
    incident._save(os.path.join(state, incident.STATE_FILE), {
        "version": 1, "last": "success", "events": [], "last_sent": sent})


SENT = {"at": "2026-09-22T20:05:00+09:00", "state": "blocked", "reason": "publish_http_500",
        "event_at": "2026-09-22T20:04:00+09:00", "roles": ["admin", "user"]}


def test_boardに最後に送った運用通知が出る(isolated_account, capsys):
    _outbox_for(isolated_account["name"], SENT)
    assert cli.main(["board"]) == 0
    out = capsys.readouterr().out
    line = next(l for l in out.splitlines() if l.strip().startswith("最後に送った運用通知:"))
    assert "2026-09-22T20:05:00+09:00" in line and "blocked" in line and "admin・user" in line
    assert cli.main(["board", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    acct = next(r for r in payload["accounts"] if r["account"] == isolated_account["name"])
    assert acct["incident_notifications"]["last_sent"]["roles"] == ["admin", "user"]


def test_observeに最後に送った運用通知が出る(isolated_account):
    _outbox_for(isolated_account["name"], SENT)
    payload = morning.build(isolated_account["name"], mark=False)
    today = next(sec for sec in payload["sections"] if sec["section"] == "today")
    node = today["value"]["by_account"][isolated_account["name"]]["value"]
    cell = node["last_notification"]
    assert cell["cannot_say"] is None and cell["value"]["state"] == "blocked"
    lines = []
    morning.render(payload, out=lines.append)
    assert any("最後に送った運用通知" in l and "publish_http_500" in l for l in lines)
