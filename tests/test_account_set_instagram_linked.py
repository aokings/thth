"""11/23: 運営者の CLI で Threads の口座の `instagram_linked` を変えられる（それまでは JSON を手で直していた）。"""
from __future__ import annotations

import json

import pytest

from thth import account_settings, admin_log
from tests.test_v212_server_writes import env, remote  # noqa: F401
from tests.test_v3120_activity import thth, read_ledger
from tests.test_v3120_guard import ledger  # noqa: F401


def test_instagram_linkedをtrueにでき_変更ログに残る(env):
    ledger(env)
    assert read_ledger(env).get("media") == "threads"
    done = thth("account", "set", "alpha", "instagram_linked", "true", "--by", "masaru")
    assert done.returncode == 0, done.stderr
    assert read_ledger(env)["instagram_linked"] is True
    assert "instagram_linked False → True" in done.stdout
    rows, _ = admin_log.read(event="settings_set")
    assert rows[-1]["diff"] == {"instagram_linked": [False, True]} and rows[-1]["by"] == "masaru"
    done = thth("account", "set", "alpha", "instagram_linked", "false", "--by", "masaru", "--json")
    assert json.loads(done.stdout) == {"account": "alpha", "key": "instagram_linked", "before": True,
                                       "after": False, "changed": True}


@pytest.mark.parametrize("value", ["yes", "1", ""])
def test_trueとfalse以外は断る(env, value):
    ledger(env)
    before = read_ledger(env)
    done = thth("account", "set", "alpha", "instagram_linked", value, "--by", "masaru")
    assert done.returncode == 2 and "invalid_setting" in done.stderr
    assert read_ledger(env) == before


def test_Threads以外の口座では断る(env, monkeypatch):
    ledger(env)
    original = account_settings.accounts.load_account
    monkeypatch.setattr(account_settings.accounts, "load_account",
                        lambda name: {**original(name), "media": "instagram"})
    with pytest.raises(account_settings.SettingsError, match="invalid_setting"):
        account_settings.change_instagram_linked("alpha", "true", by="masaru")


def test_MCPやthth_meの口からは変えられない(env):
    ledger(env)
    for tighten_only in (True, False):
        with pytest.raises(account_settings.SettingsError, match="invalid_setting"):
            account_settings.change("alpha", "instagram_linked", "true", by="llm", via="mcp",
                                    tighten_only=tighten_only)
