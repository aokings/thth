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

from thth import accounts, cli, doctor, healthcheck, incident, jst, morning, notification_route, report

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


def _declare_hub(thth_root):
    path = Path(thth_root) / incident.CONFIG_FILE
    incident._save(path, {**json.loads(path.read_text(encoding="utf-8")), "hub_reads_stuck": True})


@pytest.mark.parametrize("url,smtp", [(False, False), (False, True), (True, False), (True, True)])
def test_hubの宣言があれば欠けているとは言わない(tmp_path, isolated_account_factory, thth_root, url, smtp):
    info = _account(tmp_path, isolated_account_factory, url=url, smtp=smtp, thth_root=thth_root)
    _declare_hub(thth_root)
    cfg = accounts.load_account(info["name"])
    assert notification_route.status(cfg) == {"healthcheck": url, "incident": smtp, "hub": True,
                                              "cannot_say": None}
    assert notification_route.missing({info["name"]: cfg}) == []
    # メールの準備の値は宣言で変わらない（held の積みが見る `smtp_configured`）。
    assert incident.readiness(cfg)["smtp_configured"] is smtp


def test_doctorはhubの宣言で読み手を言いメールの未設定を言わない(tmp_path, isolated_account_factory, thth_root):
    info = _account(tmp_path, isolated_account_factory, url=False, smtp=True, thth_root=thth_root)
    bare = []
    rc = doctor.run_doctor(info["name"], log=bare.append)
    assert any(line.startswith("停止メール通知が未設定です") for line in bare)
    _declare_hub(thth_root)
    text = []
    assert doctor.run_doctor(info["name"], log=text.append) == rc
    assert notification_route.HUB_LINE in text
    assert not any(line.startswith(("停止メール通知が未設定です", "no_notification_route", "notification_route_partial"))
                   for line in text)
    lines = []
    doctor.run_doctor(info["name"], as_json=True, log=lines.append)
    assert json.loads(lines[-1])["notification_route"] == {"scheduled": True, "healthcheck": False, "incident": True,
                                                           "hub": True, "cannot_say": None}


def test_scheduledでないaccountにはhubの行も足さない(tmp_path, isolated_account_factory, thth_root):
    info = _account(tmp_path, isolated_account_factory, url=False, smtp=False,
                    thth_root=thth_root, scheduled=False)
    _declare_hub(thth_root)
    text = []
    doctor.run_doctor(info["name"], log=text.append)
    assert notification_route.HUB_LINE not in text


def test_morningとboardもhubの宣言を届く道に数える(tmp_path, isolated_account_factory, thth_root, capsys):
    info = _account(tmp_path, isolated_account_factory, url=False, smtp=False, thth_root=thth_root)

    def row():
        return next(r for r in report.board_summary()["accounts"] if r["account"] == info["name"])

    state_dir = accounts.state_dir_for(info["name"])
    healthcheck.notify(info["name"], None, healthcheck.diagnostic(info["name"], "fail", state_dir=state_dir,
                                                                  reason="publish_result_unknown"),
                       state_dir=state_dir)
    assert row()["notification_configured"] is False
    assert "hub_reads_stuck" not in row()["incident_notifications"]
    cli.main(["board"])
    bare = capsys.readouterr().out
    assert "停止メール: 設定不足" in bare and "死活通知: **未設定**" in bare
    _declare_hub(thth_root)
    assert row()["notification_configured"] is True
    assert row()["incident_notifications"]["hub_reads_stuck"] is True
    assert row()["notification_delivery"] == "not_configured"
    cli.main(["board"])
    declared = capsys.readouterr().out
    assert "停止メール: 宛先なし（止まりは hub が読む）" in declared
    assert "死活通知: なし（止まりは hub が読む）" in declared and "**未設定**" not in declared
    payload = morning.build(info["name"], now=jst.now_jst(), mark=False)
    assert {s["section"]: s for s in payload["sections"]}["tool"]["value"]["notification_routes"] == []


@pytest.mark.parametrize("value,kept", [(True, True), (False, None)])
def test_hubの宣言はnotifications_configの入力で入れ外しできる(tmp_path, isolated_account_factory, thth_root,
                                                value, kept):
    _account(tmp_path, isolated_account_factory, url=False, smtp=True, thth_root=thth_root)
    private = tmp_path / "hub.json"
    private.write_text(json.dumps({"hub_reads_stuck": True}), encoding="utf-8")
    assert incident.cmd_notifications(cli.build_parser().parse_args(
        ["notifications", "config", "--input", str(private)])) == 0
    private.write_text(json.dumps({"hub_reads_stuck": value}), encoding="utf-8")
    assert incident.cmd_notifications(cli.build_parser().parse_args(
        ["notifications", "config", "--input", str(private)])) == 0
    saved = json.loads((Path(thth_root) / incident.CONFIG_FILE).read_text(encoding="utf-8"))
    assert saved.get("hub_reads_stuck") is kept
    assert saved["admin_email"] == "admin@example.org" and "smtp" in saved   # ほかの設定は残る
    assert incident.hub_reads_stuck() is value


def test_hubの宣言は真偽値だけ受ける(tmp_path, isolated_account_factory, thth_root):
    _account(tmp_path, isolated_account_factory, url=False, smtp=False, thth_root=thth_root)
    private = tmp_path / "hub.json"
    private.write_text(json.dumps({"hub_reads_stuck": "yes"}), encoding="utf-8")
    assert incident.cmd_notifications(cli.build_parser().parse_args(
        ["notifications", "config", "--input", str(private)])) == 2
    assert incident.hub_reads_stuck() is False


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
