"""3.1.1 件 4: 運用通知の案内をそのまま打てる形にする。

- `thth run` の「運用通知に未完了があります」に account 名が入る。
- `thth notifications status|test` を account 無しで打つと、汎用文に潰さず
  静的な `account_required` と正しい打ち方を stderr に出して rc=2。
- それ以外の ValueError は従前どおり汎用文（秘密値を出さない）。
"""
from types import SimpleNamespace
import pytest

from thth import cli, healthcheck, incident, selfupdate


@pytest.fixture
def root(tmp_path, monkeypatch):
    path = tmp_path / "root"; path.mkdir(mode=0o700)
    monkeypatch.setenv("THTH_ROOT", str(path))
    monkeypatch.setenv("THTH_ACCOUNTS_DIR", str(path / "accounts"))
    (path / "accounts").mkdir()
    return path


@pytest.mark.parametrize("action", ["status", "test"])
def test_missing_account_is_static_account_required_with_the_right_form(root, capsys, action):
    args = cli.build_parser().parse_args(["notifications", action])
    assert args.func(args) == 2
    err = capsys.readouterr().err
    assert err.startswith("account_required: ")
    assert f"thth notifications {action} <account>" in err
    assert "通知操作に失敗しました" not in err


def test_other_value_errors_keep_the_generic_message(root, capsys):
    args = cli.build_parser().parse_args(["notifications", "config"])  # --input 無し
    assert args.func(args) == 2
    err = capsys.readouterr().err
    assert "通知操作に失敗しました" in err and "account_required" not in err


def test_run_guidance_names_the_account(root, monkeypatch, capsys):
    # 通知に未完了がある状態だけを作る（投稿・SMTP・Git には触らない）。
    monkeypatch.setattr(selfupdate, "pull_and_reexec", lambda *a, **k: None)
    monkeypatch.setattr(healthcheck, "diagnostic", lambda *a, **k: object())
    monkeypatch.setattr(healthcheck, "notify", lambda *a, **k: SimpleNamespace(
        delivery="not_configured", category=None, state_saved=True))
    monkeypatch.setattr(incident, "notify", lambda *a, **k: "processed")
    monkeypatch.setattr(incident, "summary", lambda *a, **k: {"mail_pending": 1, "repo_pending": 0})
    args = cli.build_parser().parse_args(["run", "kopicha-mastodon"])
    assert args.func(args) == 2  # 台帳が無いので投稿はしない
    err = capsys.readouterr().err
    assert "運用通知に未完了があります: thth notifications status kopicha-mastodon で確認してください" in err


def _run_with(monkeypatch, capsys, summary, notify=lambda *a, **k: "processed", ready=None):
    monkeypatch.setattr(selfupdate, "pull_and_reexec", lambda *a, **k: None)
    monkeypatch.setattr(healthcheck, "diagnostic", lambda *a, **k: SimpleNamespace(state="success", reason_code="healthy"))
    monkeypatch.setattr(healthcheck, "notify", lambda *a, **k: SimpleNamespace(
        delivery="not_configured", category=None, state_saved=True))
    monkeypatch.setattr(incident, "notify", notify)
    monkeypatch.setattr(incident, "summary", lambda *a, **k: summary)
    if ready is not None:
        monkeypatch.setattr(incident, "readiness", lambda *a, **k: ready)
        monkeypatch.setattr(incident, "hub_reads_stuck", lambda: summary.get("hub_reads_stuck") is True)
    args = cli.build_parser().parse_args(["run", "kopicha-threads"])
    args.func(args)
    return capsys.readouterr().err


HUB_ONLY = {"user_configured": False, "admin_configured": False, "hub_reads_stuck": True,
            "mail_pending": 12, "repo_pending": 6}


def test_hubが拾う口座では未完了の行を出さない(root, monkeypatch, capsys):
    """運用 11/10: 宛先が無く hub が止まりを拾う口座で、10 分ごとに「未完了」が出ていた。"""
    assert "運用通知に未完了があります" not in _run_with(monkeypatch, capsys, dict(HUB_ONLY))


@pytest.mark.parametrize("change", [{"hub_reads_stuck": False}, {"user_configured": True}, {"admin_configured": True}])
def test_宛先があるかhubが拾わないなら今までどおり出す(root, monkeypatch, capsys, change):
    err = _run_with(monkeypatch, capsys, dict(HUB_ONLY, **change))
    assert "運用通知に未完了があります: thth notifications status kopicha-threads" in err


def _busy(*_a, **_k):
    from thth import lock
    raise lock.LockBusy("ロック取得失敗（flock）: x")


def test_錠が重なったときは次の回に回すと言い_hubが拾う口座では黙る(root, monkeypatch, capsys):
    """同じ repo の別の口座（kopicha-ig と kopicha-threads）が同じ分に回り、錠がぶつかって「完了できませんでした」と出ていた。"""
    ready = {"user_configured": False, "admin_configured": False, "smtp_configured": True}
    err = _run_with(monkeypatch, capsys, dict(HUB_ONLY), notify=_busy, ready=ready)
    assert "incident_notification" not in err
    err = _run_with(monkeypatch, capsys, dict(HUB_ONLY, hub_reads_stuck=False), notify=_busy, ready=ready)
    assert "incident_notification_busy" in err and "完了できませんでした" not in err


def test_ほかの例外は今までどおり完了できなかったと言う(root, monkeypatch, capsys):
    def boom(*_a, **_k):
        raise ValueError("invalid_outbox")
    err = _run_with(monkeypatch, capsys, dict(HUB_ONLY), notify=boom)
    assert "運用通知を完了できませんでした: incident_notification_pending" in err


def test_錠の枝で宛先の確かめが落ちても死活通知と脈は飛ばない(root, monkeypatch, capsys):
    """監査 5.14.2: readiness が例外を投げると外側に抜け、死活通知と hub への脈が飛んでいた。"""
    from thth import pulse
    pulses = []
    monkeypatch.setattr(pulse, "send", lambda *a, **k: pulses.append(k) or "sent")

    def broken(*_a, **_k):
        raise RuntimeError("x")
    monkeypatch.setattr(incident, "readiness", broken)
    err = _run_with(monkeypatch, capsys, dict(HUB_ONLY), notify=_busy)
    assert "incident_notification_busy" in err and "notification_internal_error" not in err
    assert pulses
