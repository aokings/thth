"""3.3.0 A4 知らせる先が無いことを言う（morning の 0 段と doctor）。

scheduled なのに死活通知（HEALTHCHECK_URL）も運用通知（SMTP）も無ければ
`no_notification_route`、片方だけ無ければ欠けている側を名指しして
`notification_route_partial: healthcheck|incident`。値（URL・宛先）は出さない。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from thth import accounts, doctor, incident, jst, morning, notification_route

URL = "https://hc-ping.com/secret-route-check"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("THTH_SMTP_") or key == "HEALTHCHECK_URL":
            monkeypatch.delenv(key)


def _account(tmp_path, factory, *, url, smtp, thth_root, **overrides):
    env = tmp_path / "route.env"
    env.write_text(f"HEALTHCHECK_URL={URL}\n" if url else "", encoding="utf-8")
    config = ({"admin_email": "admin@example.org",
               "smtp": {"host": "smtp.example.org", "sender": "sender@example.org", "tls": "ssl"}}
              if smtp else {})
    incident._save(Path(thth_root) / incident.CONFIG_FILE, config)
    return factory(env=str(env), **overrides)


@pytest.mark.parametrize("url,smtp,expected", [
    (False, False, "no_notification_route"),
    (False, True, "notification_route_partial: healthcheck"),
    (True, False, "notification_route_partial: incident"),
    (True, True, None),
])
def test_経路の有無で静的な符丁を言う(tmp_path, isolated_account_factory, thth_root,
                          url, smtp, expected):
    info = _account(tmp_path, isolated_account_factory, url=url, smtp=smtp, thth_root=thth_root)
    cfg = accounts.load_account(info["name"])
    status = notification_route.status(cfg)
    assert status == {"healthcheck": url, "incident": smtp, "cannot_say": expected}
    assert URL not in json.dumps(status)


def test_morningの0段に経路の欠けたscheduledのaccountを出す(tmp_path, isolated_account_factory,
                                              thth_root):
    info = _account(tmp_path, isolated_account_factory, url=False, smtp=True, thth_root=thth_root)
    payload = morning.build(info["name"], now=jst.now_jst(), mark=False)
    tool = {s["section"]: s for s in payload["sections"]}["tool"]["value"]
    assert tool["notification_routes"] == [
        {"account": info["name"], "cannot_say": "notification_route_partial: healthcheck"}]
    lines = []
    morning.render(payload, out=lines.append)
    assert any("知らせる先" in line and "notification_route_partial: healthcheck" in line
               for line in lines)
    assert URL not in json.dumps(payload, ensure_ascii=False)


def test_scheduledでないaccountは言わない(tmp_path, isolated_account_factory, thth_root):
    info = _account(tmp_path, isolated_account_factory, url=False, smtp=False,
                    thth_root=thth_root, scheduled=False)
    payload = morning.build(info["name"], now=jst.now_jst(), mark=False)
    tool = {s["section"]: s for s in payload["sections"]}["tool"]["value"]
    assert tool["notification_routes"] == []


def test_両方揃っていれば空(tmp_path, isolated_account_factory, thth_root):
    info = _account(tmp_path, isolated_account_factory, url=True, smtp=True, thth_root=thth_root)
    payload = morning.build(info["name"], now=jst.now_jst(), mark=False)
    tool = {s["section"]: s for s in payload["sections"]}["tool"]["value"]
    assert tool["notification_routes"] == []


def test_doctorも経路なしを言いrcは変えない(tmp_path, isolated_account_factory, thth_root):
    info = _account(tmp_path, isolated_account_factory, url=False, smtp=False, thth_root=thth_root)
    lines = []
    rc = doctor.run_doctor(info["name"], as_json=True, log=lines.append)
    payload = json.loads(lines[-1])
    assert payload["notification_route"] == {"scheduled": True, "healthcheck": False,
                                             "incident": False,
                                             "cannot_say": "no_notification_route"}
    assert any(n.startswith("no_notification_route:") for n in payload["notices"])
    # 経路の有無で rc は動かない（揃えた台帳と同じ rc）。
    _account(tmp_path, isolated_account_factory, url=True, smtp=True, thth_root=thth_root)
    assert doctor.run_doctor(info["name"], as_json=True, log=lambda _l: None) == rc
    _account(tmp_path, isolated_account_factory, url=False, smtp=False, thth_root=thth_root)
    text = []
    doctor.run_doctor(info["name"], log=text.append)
    assert any(line.startswith("no_notification_route:") for line in text)
    assert URL not in "\n".join(text + lines)
