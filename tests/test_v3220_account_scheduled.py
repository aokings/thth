"""3.22.0・点検 §B-21: 運営者の CLI だけが口座を timer に載せる印（`scheduled`）を変えられる。"""
from __future__ import annotations

import json

import pytest

from thth import account_settings, admin_log
from tests.test_v212_server_writes import env, remote  # noqa: F401
from tests.test_v3120_activity import thth, read_ledger
from tests.test_v3120_guard import ledger  # noqa: F401


def test_招待の口座をscheduled_trueにでき_timerの置き方を言う(env):
    ledger(env, scheduled=False)
    done = thth("account", "set", "alpha", "scheduled", "true", "--by", "masaru")
    assert done.returncode == 0, done.stderr
    assert read_ledger(env)["scheduled"] is True
    assert "scheduled False → True" in done.stdout
    assert "thth systemd alpha" in done.stdout and "systemctl enable --now thth@alpha.timer" in done.stdout
    rows, _ = admin_log.read(event="settings_set")
    assert rows[-1]["diff"] == {"scheduled": [False, True]} and rows[-1]["by"] == "masaru"
    # timer に載ったので猶予が書ける（それまでは schedule_unavailable）。
    assert thth("account", "set", "alpha", "hold_minutes", "10", "--by", "masaru").returncode == 0


def test_猶予があるとfalseに戻さない(env):
    ledger(env, scheduled=True)
    assert thth("account", "set", "alpha", "hold_minutes", "10", "--by", "masaru").returncode == 0
    done = thth("account", "set", "alpha", "scheduled", "false", "--by", "masaru")
    assert done.returncode == 2 and "schedule_unavailable" in done.stderr and "hold_minutes 0" in done.stderr
    assert read_ledger(env)["scheduled"] is True
    assert thth("account", "set", "alpha", "hold_minutes", "0", "--by", "masaru").returncode == 0
    done = thth("account", "set", "alpha", "scheduled", "false", "--by", "masaru", "--json")
    assert json.loads(done.stdout) == {"account": "alpha", "key": "scheduled", "before": True,
                                       "after": False, "changed": True}


@pytest.mark.parametrize("value", ["yes", "1", "", "True!"])
def test_trueとfalse以外は断る(env, value):
    ledger(env, scheduled=False)
    before = read_ledger(env)
    done = thth("account", "set", "alpha", "scheduled", value, "--by", "masaru")
    assert done.returncode == 2 and "invalid_setting" in done.stderr
    assert read_ledger(env) == before


def test_by無しは断る(env):
    ledger(env, scheduled=False)
    assert thth("account", "set", "alpha", "scheduled", "true").returncode == 2
    assert read_ledger(env)["scheduled"] is False


def test_MCPやthth_meの口からは変えられない(env):
    """`change()`（MCP の thth_settings・/activity が通る口）は scheduled を知らない。"""
    ledger(env, scheduled=False)
    for tighten_only in (True, False):
        with pytest.raises(account_settings.SettingsError, match="invalid_setting"):
            account_settings.change("alpha", "scheduled", "true", by="llm", via="mcp",
                                    tighten_only=tighten_only)
    assert read_ledger(env)["scheduled"] is False
