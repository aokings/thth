"""4.0.1（見つけたこと 17）: `thth board`・`thth account` の token の行が、はてなの口座で `config_error` と出ていた。

はてなの口座は主体の token（`principals.load_principal(...)["token"]` のパス・OAuth 1.0a・期限なし）を見る:
在る＝`ok`/期限なし・無い＝`no_token`・読めない／形が違う＝`unreadable`・主体の台帳が読めない＝`config_error`。
値は出さない。SNS の口座の表示は変えない（`tests/test_refresh_destination.py` などが見る）。はてなには出ない。
"""
from __future__ import annotations

import datetime
import json
import os

from tests.hatena_s1_helpers import world  # noqa: F401
from tests.hatena_s2_http import FAKE_TOKEN
from thth import account_report, jst, maintain as maintain_mod, report as report_mod

NOW = datetime.datetime(2026, 10, 17, 20, 0, 0, tzinfo=jst.JST)
ACCOUNT = "lane-a-hatenablog"


def put_token(world, data):
    path = world.main_token
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    os.chmod(path, 0o600)


def test_主体のtokenがあればok_期限なし(world):
    """柵: はてなの枝を外す（SNS の adapter を引く）と `config_error` で赤。"""
    put_token(world, FAKE_TOKEN)
    row = maintain_mod.inspect(ACCOUNT, now=NOW)
    assert row["state"] == "ok" and row["no_expiry"] is True and row["remaining_days"] is None, row
    assert not maintain_mod.needs_attention(row)
    for value in (FAKE_TOKEN["oauth_token"], FAKE_TOKEN["oauth_token_secret"]):
        assert value not in json.dumps(row, ensure_ascii=False)


def test_主体のtokenが無ければno_token(world):
    row = maintain_mod.inspect(ACCOUNT, now=NOW)
    assert row["state"] == "no_token", row
    assert "thth principal auth hatena-main" in row["message"]


def test_形が違うtokenはunreadable(world):
    put_token(world, dict(FAKE_TOKEN, schema="other/1"))
    assert maintain_mod.inspect(ACCOUNT, now=NOW)["state"] == "unreadable"
    put_token(world, dict(FAKE_TOKEN, hatena_id="someone-else"))
    assert maintain_mod.inspect(ACCOUNT, now=NOW)["state"] == "unreadable"
    put_token(world, "{broken")
    row = maintain_mod.inspect(ACCOUNT, now=NOW)
    assert row["state"] == "unreadable" and "broken" not in row["message"]


def test_主体の台帳が読めなければconfig_error(world):
    put_token(world, FAKE_TOKEN)
    (world.principals_dir / "hatena-main.json").write_text("{broken", encoding="utf-8")
    assert maintain_mod.inspect(ACCOUNT, now=NOW)["state"] == "config_error"


def test_boardとaccountの行(world):
    """柵: board の行（`token_state`・`token_no_expiry`）と `thth account` の token の行。"""
    put_token(world, FAKE_TOKEN)
    board = report_mod.board_summary(now=NOW)
    row = {r["account"]: r for r in board["accounts"]}[ACCOUNT]
    assert row["token_state"] == "ok" and row["token_no_expiry"] is True and row["token_remaining_days"] is None
    detail = account_report.account_detail(ACCOUNT, now=NOW, remote=False)
    assert detail["token"]["state"] == "ok"
    assert not any(str(b).startswith("token:") for b in detail.get("blockers", []))
