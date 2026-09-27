"""VM の hub への脈（`hub pulse thth-<account>`・media-hub 依頼 §2・§4）。

Healthchecks への ping と同じ分岐で、実体の無い `hub` バイナリを偽の script に
差し替えて引数だけを記録する。実ネットワーク・実 subprocess の hub は使わない。
"""
from __future__ import annotations

import os
import stat
from types import SimpleNamespace

from thth import cli, core, pulse


def make_fake_hub(tmp_path, log_path):
    """呼ばれた引数を 1 行ずつ `log_path` に書くだけの偽 `hub`。"""
    script = tmp_path / "hub"
    script.write_text(
        "#!/bin/sh\n"
        f'printf \'%s\\n\' "$*" >> "{log_path}"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(script)


def test_send_runs_hub_with_pulse_name(tmp_path, monkeypatch):
    log = tmp_path / "calls.log"
    monkeypatch.setenv(pulse.ENV_KEY, make_fake_hub(tmp_path, log))

    status = pulse.send("nigamilab-threads")

    assert status == "pulse_sent"
    assert log.read_text().strip() == "pulse thth-nigamilab-threads"


def test_send_fail_and_note(tmp_path, monkeypatch):
    log = tmp_path / "calls.log"
    monkeypatch.setenv(pulse.ENV_KEY, make_fake_hub(tmp_path, log))

    status = pulse.send("acct", fail=True, note="held approval_stale 46")

    assert status == "pulse_sent"
    assert log.read_text().strip() == "pulse thth-acct --fail --note held approval_stale 46"


def test_send_without_fail_has_no_note(tmp_path, monkeypatch):
    log = tmp_path / "calls.log"
    monkeypatch.setenv(pulse.ENV_KEY, make_fake_hub(tmp_path, log))

    status = pulse.send("acct")

    assert status == "pulse_sent"
    assert log.read_text().strip() == "pulse thth-acct"


def test_send_is_unavailable_when_hub_binary_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv(pulse.ENV_KEY, str(tmp_path / "does-not-exist"))

    status = pulse.send("acct", fail=True, note="missing_token")

    assert status == "pulse_unavailable"


def test_send_default_binary_missing_on_this_machine(monkeypatch):
    """timer 用の既定 `THTH_HUB_PULSE` はこの開発機には存在しない——落ちずに
    `pulse_unavailable` を返すことだけを確かめる（黙って何もしない側の記録）。"""
    monkeypatch.delenv(pulse.ENV_KEY, raising=False)
    assert not os.path.exists(pulse.DEFAULT_HUB_BIN)

    assert pulse.send("acct") == "pulse_unavailable"


def test_send_rejects_unsafe_account_name(tmp_path, monkeypatch):
    log = tmp_path / "calls.log"
    monkeypatch.setenv(pulse.ENV_KEY, make_fake_hub(tmp_path, log))

    status = pulse.send("../escape")

    assert status == "pulse_invalid_account"
    assert not log.exists()


def test_note_is_flattened_and_truncated(tmp_path, monkeypatch):
    log = tmp_path / "calls.log"
    monkeypatch.setenv(pulse.ENV_KEY, make_fake_hub(tmp_path, log))
    long_note = ("held approval_stale 46\nsecret 原稿名.md " + "x" * 200)

    pulse.send("acct", fail=True, note=long_note)

    logged = log.read_text().strip()
    assert "\n" not in logged
    note_part = logged.split("--note ", 1)[1]
    assert len(note_part) <= pulse.NOTE_LIMIT
    assert "秘密" not in logged  # 呼び出し側が渡さない前提の確認（下の note 3 も参照）


# --- cmd_run からの呼び出し（Healthchecks の ping と同じ分岐で並べて打つ） ---


def prepare_run(monkeypatch):
    monkeypatch.setattr(cli.selfupdate_mod, "pull_and_reexec", lambda *_a, **_k: None)
    monkeypatch.setattr(cli.collect_mod, "run_collect", lambda *_a, **_k: 0)


def test_cmd_run_pulses_on_missing_token(tmp_path, isolated_account_factory, monkeypatch):
    log = tmp_path / "calls.log"
    monkeypatch.setenv(pulse.ENV_KEY, make_fake_hub(tmp_path, log))
    monkeypatch.delenv("HEALTHCHECK_URL", raising=False)
    prepare_run(monkeypatch)
    account = isolated_account_factory()  # 既定: token が存在しないパス

    rc = cli.cmd_run(SimpleNamespace(account=account["name"]))

    assert rc == 2
    assert log.read_text().strip() == f"pulse thth-{account['name']} --fail --note missing_token"


def test_cmd_run_pulses_success_without_note(tmp_path, isolated_account_factory, monkeypatch):
    log = tmp_path / "calls.log"
    monkeypatch.setenv(pulse.ENV_KEY, make_fake_hub(tmp_path, log))
    monkeypatch.delenv("HEALTHCHECK_URL", raising=False)
    prepare_run(monkeypatch)
    token_path = tmp_path / "account.token"
    token_path.write_text("{}\n", encoding="utf-8")
    account = isolated_account_factory(token=str(token_path))
    monkeypatch.setattr(
        cli.core, "throw_once",
        lambda *_a, **_k: core.ThrowResult(0, "production", "none", "出すものなし"))

    rc = cli.cmd_run(SimpleNamespace(account=account["name"]))

    assert rc == 0
    assert log.read_text().strip() == f"pulse thth-{account['name']}"


def test_cmd_run_pulse_note_never_carries_the_file_name(
        tmp_path, isolated_account_factory, monkeypatch):
    """公開失敗の原稿名（front-matter・本文の手掛かり）が note に出ないことを確かめる
    ——Healthchecks の ping 本文と同じ規律（`docs/運用_停止通知.md`）。"""
    log = tmp_path / "calls.log"
    monkeypatch.setenv(pulse.ENV_KEY, make_fake_hub(tmp_path, log))
    monkeypatch.delenv("HEALTHCHECK_URL", raising=False)
    prepare_run(monkeypatch)
    token_path = tmp_path / "account.token"
    token_path.write_text("{}\n", encoding="utf-8")
    account = isolated_account_factory(token=str(token_path))
    secret_file = os.path.join(account["queue_dir"], "内緒の原稿タイトル.md")
    monkeypatch.setattr(
        cli.core, "throw_once",
        lambda *_a, **_k: core.ThrowResult(
            1, "production", "post", "失敗", file=secret_file,
            error="公開失敗: HTTP 500 remote supplied detail"))

    cli.cmd_run(SimpleNamespace(account=account["name"]))

    logged = log.read_text().strip()
    assert "内緒" not in logged
    assert ".md" not in logged
    assert "--fail" in logged


def test_cmd_run_survives_missing_hub_binary(tmp_path, isolated_account_factory, monkeypatch):
    monkeypatch.setenv(pulse.ENV_KEY, str(tmp_path / "does-not-exist"))
    monkeypatch.delenv("HEALTHCHECK_URL", raising=False)
    prepare_run(monkeypatch)
    account = isolated_account_factory()

    rc = cli.cmd_run(SimpleNamespace(account=account["name"]))

    assert rc == 2  # token が無い側の既定の rc がそのまま（脈の欠落で変わらない）
