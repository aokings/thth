"""`thth maintain`: 投稿とは独立にトークンを保つ（外部レビュー §5・2026-09-10）。

要点は「トークンの保守が、投稿できることに依存しない」こと。timer を持たない
アカウント・`inflight` で詰まっているアカウント・長期停止中のアカウントでも、
60 日の時限だけは進む。本物の Threads API には触れない
（`tests/helpers/fake_oauth_server.py`）。
"""
from __future__ import annotations

import datetime
import json
import os

from tests.helpers.fake_oauth_server import fake_oauth_server
from thth import accounts as accounts_mod
from thth import inflight as inflight_mod
from thth import jst
from thth import maintain as maintain_mod
from thth import report

TOKEN_VALUE = "MAINTAIN-SECRET-TOKEN-value"
SIXTY_DAYS = 60 * 24 * 3600


def _write_token(path, *, age_days=0.0, expires_in=SIXTY_DAYS, now=None,
                  omit_obtained_at=False, access_token=TOKEN_VALUE):
    now = now if now is not None else jst.now_jst()
    payload = {"access_token": access_token, "expires_in": expires_in,
               "user_id": "999999", "username": "nigamilab", "scopes": ["threads_basic"]}
    if not omit_obtained_at:
        payload["obtained_at"] = jst.iso(now - datetime.timedelta(days=age_days))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    os.chmod(path, 0o600)
    return path


def _account(factory, tmp_path, *, name="nigamilab-threads", token=True, **token_kw):
    now = jst.now_jst()
    overrides = {}
    if token:
        overrides["token"] = _write_token(str(tmp_path / f"{name}.token"), now=now, **token_kw)
    else:
        overrides["token"] = str(tmp_path / "存在しない.token")
    account = factory(name=name, **overrides)
    account["now"] = now
    account["token_path"] = overrides["token"]
    return account


def _run(account_name=None, **kw):
    lines = []
    rc = maintain_mod.run_maintain(account_name, log=lines.append, **kw)
    return rc, lines


def test_余裕があるトークンは何もしない(tmp_path, isolated_account_factory):
    account = _account(isolated_account_factory, tmp_path, age_days=10)
    calls = []
    rc, lines = _run(now=account["now"], refresh=lambda *a, **k: calls.append(a) or 0)
    assert rc == 0
    assert calls == []
    assert any("ok" in line for line in lines)


def test_50日を超えたら更新する(tmp_path, monkeypatch, isolated_account_factory):
    account = _account(isolated_account_factory, tmp_path, age_days=55)
    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc, lines = _run(now=account["now"])
    assert rc == 0, lines
    with open(account["token_path"], encoding="utf-8") as f:
        token = json.load(f)
    assert token["access_token"] != TOKEN_VALUE, "更新されていない"
    runs = _read_runs(account["name"])
    assert runs[-1]["action"] == "maintain" and runs[-1]["refreshed"] is True


def test_更新に失敗したら要確認で非ゼロ(tmp_path, monkeypatch, isolated_account_factory):
    account = _account(isolated_account_factory, tmp_path, age_days=55)
    with fake_oauth_server({"refresh": "5xx"}) as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc, lines = _run(now=account["now"])
    assert rc == 1
    assert any("refresh_failed" in line for line in lines)
    runs = _read_runs(account["name"])
    assert runs[-1]["status"] == "error" and runs[-1]["error"] == "refresh_failed"


def test_期限切れは更新を試みずに取り直しを促す(tmp_path, isolated_account_factory):
    """失効したトークンは更新では戻らない（Meta 側が受け付けない）。API を叩かない。"""
    account = _account(isolated_account_factory, tmp_path, age_days=61)
    calls = []
    rc, lines = _run(now=account["now"], refresh=lambda *a, **k: calls.append(a) or 0)
    assert rc == 1
    assert calls == [], "期限切れなのに更新を試みた"
    assert any("expired" in line and "取り直して" in line for line in lines)


def test_tokenが無いアカウントは要確認(tmp_path, isolated_account_factory):
    account = _account(isolated_account_factory, tmp_path, token=False)
    rc, lines = _run(now=account["now"])
    assert rc == 1
    assert any("no_token" in line for line in lines)


def test_obtained_atが無いtokenは要確認(tmp_path, isolated_account_factory):
    account = _account(isolated_account_factory, tmp_path, omit_obtained_at=True)
    rc, lines = _run(now=account["now"])
    assert rc == 1
    assert any("unreadable" in line for line in lines)


def test_inflightで詰まっていても保守は走る(tmp_path, monkeypatch, isolated_account_factory):
    """**この 1 件が `thth maintain` を分けた理由そのもの。**

    `thth run` の中で更新していたら、`inflight` が残っている間ずっと手前で止まり、
    トークンだけが 60 日で死ぬ。maintain は queue も inflight も見ない。
    """
    account = _account(isolated_account_factory, tmp_path, age_days=55)
    state_dir = accounts_mod.state_dir_for(account["name"])
    inflight_mod.write(state_dir, file="a.md", started=jst.iso(account["now"]))
    assert inflight_mod.read(state_dir) is not None

    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc, lines = _run(now=account["now"])

    assert rc == 0, lines
    token_path = accounts_mod.load_account(account["name"])["token"]
    with open(token_path, encoding="utf-8") as f:
        assert json.load(f)["access_token"] != TOKEN_VALUE, "inflight に阻まれて更新されなかった"
    # 保守は inflight を消さない（投稿側の状態には触れない）。
    assert inflight_mod.read(state_dir) is not None


def test_timerを持たないアカウントも対象になる(tmp_path, monkeypatch, isolated_account_factory):
    """`masaru-threads`（同席専用・timer 無効）のようなアカウントが黙って死なない。"""
    account = _account(isolated_account_factory, tmp_path, name="masaru-threads", age_days=55)
    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc, lines = _run(now=account["now"])
    assert rc == 0, lines
    assert any("masaru-threads" in line for line in lines)


def test_checkは何も更新しない(tmp_path, isolated_account_factory):
    account = _account(isolated_account_factory, tmp_path, age_days=55)
    calls = []
    rc, lines = _run(now=account["now"], check=True,
                     refresh=lambda *a, **k: calls.append(a) or 0)
    assert calls == [], "--check なのに更新した"
    assert rc == 0  # refresh_due は「人の手が要る」ではない（次の実行で更新される）
    assert any("refresh_due" in line for line in lines)


def test_出力に秘密が出ない(tmp_path, isolated_account_factory):
    account = _account(isolated_account_factory, tmp_path, age_days=10)
    rc, lines = _run(now=account["now"], as_json=True)
    blob = "\n".join(lines)
    assert TOKEN_VALUE not in blob
    assert "access_token" not in blob


def test_boardにトークンの状態が出る(tmp_path, isolated_account_factory):
    _account(isolated_account_factory, tmp_path, age_days=59, expires_in=SIXTY_DAYS)
    row = next(r for r in report.board_summary()["accounts"]
               if r["account"] == "nigamilab-threads")
    assert row["token_state"] in {"refresh_due", "expiring"}
    assert row["token_remaining_days"] is not None and row["token_remaining_days"] < 7


def _read_runs(account_name: str) -> list:
    from thth import runs as runs_mod
    return runs_mod.read_runs(accounts_mod.state_dir_for(account_name))
