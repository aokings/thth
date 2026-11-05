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
from pathlib import Path
from types import SimpleNamespace

import pytest

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


def _account(factory, tmp_path, *, name="nigamilab-threads", token=True, ledger=None, **token_kw):
    now = jst.now_jst()
    overrides = dict(ledger or {})
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


# ---- 鍵の止まり（5.12.3・11/03〜05 の kopicha-x）: メールは出さず board の `stuck` で言う

@pytest.fixture
def stuck_account(tmp_path, monkeypatch, isolated_account_factory, thth_root):
    """本番の口座 1 本。宛先と SMTP も設定してあるので、メールが出る形なら `_send` で赤になる。"""
    from thth import incident
    for key in list(os.environ):
        if key.startswith("THTH_SMTP_"):
            monkeypatch.delenv(key)
    incident._save(Path(thth_root) / incident.CONFIG_FILE, {
        "admin_email": "admin@example.org",
        "smtp": {"host": "smtp.example.org", "sender": "sender@example.org", "tls": "ssl"}})
    account = _account(isolated_account_factory, tmp_path, age_days=55,
                       ledger={"production": True, "notification_email": "user@example.org"})
    mails = []
    monkeypatch.setattr(incident, "_send", lambda s, recipient, event, name:
                        mails.append((event["state"], event["reason"])) or True)
    account["mails"] = mails
    account["state"] = accounts_mod.state_dir_for(account["name"])
    account["cfg"] = accounts_mod.load_account(account["name"])
    return account


def _outbox(account):
    from thth import incident
    return json.loads((Path(account["state"]) / incident.STATE_FILE).read_text())


def _board_stuck(account):
    rows = report.board_summary(now=account["now"])["accounts"]
    return next(row for row in rows if row["account"] == account["name"])["stuck"]


def _fails(name, *, log=None, now=None):
    return 2


def _ok_refresh(account):
    def refresh(name, *, log=None, now=None):
        _write_token(account["token_path"], age_days=0, now=account["now"])
        return 0
    return refresh


def _run_notify(account, state, result, reason="publish_http_401"):
    from thth import healthcheck, incident
    diag = healthcheck.Diagnostic(account["name"], state, None, None, "x", "x",
                                  reason if state == "fail" else "healthy", "none")
    incident.notify(account["name"], account["cfg"], diag, state_dir=account["state"], result=result)


FAILED = SimpleNamespace(file=None, mode="production", action="post", exit_code=1, post_id=None)
IDLE = SimpleNamespace(file=None, mode="production", action="none", exit_code=0, post_id=None)
POSTED = SimpleNamespace(file=None, mode="production", action="post", exit_code=0, post_id="1")


def _later(hours):
    return jst.now_jst() + datetime.timedelta(hours=hours)


def test_鍵の更新の失敗はメールを出さずstuckに出て成功で消える(stuck_account, frozen_now_jst):
    account = stuck_account
    assert _board_stuck(account) is None
    rc, _ = _run(now=account["now"], refresh=_fails)
    assert rc == 1
    value = _board_stuck(account)
    assert set(value) == {"code", "since", "what", "fix"}
    assert value["code"] == "token_refresh_failed"
    assert "鍵（アクセストークン）の更新が失敗しています" in value["what"]
    assert f"thth refresh {account['name']}" in value["fix"]
    assert f"thth auth {account['name']} --by <名前>" in value["fix"]
    since = value["since"]
    frozen_now_jst(_later(24))
    _run(now=account["now"], refresh=_fails)  # 翌日も同じ失敗: 始まりの時刻は動かない
    assert _board_stuck(account)["since"] == since
    _write_token(account["token_path"], age_days=61, now=account["now"])
    frozen_now_jst(_later(24))
    _run(now=account["now"], refresh=_fails)  # 符丁が変わっても止まりは続いている
    assert _board_stuck(account)["code"] == "token_expired"
    assert _board_stuck(account)["since"] == since
    _write_token(account["token_path"], age_days=0, now=account["now"])  # 認可をやり直した
    rc, _ = _run(now=account["now"])
    assert rc == 0
    assert _board_stuck(account) is None
    assert account["mails"] == [] and _outbox(account)["events"] == []


def test_11月3日の並びでstuckはsinceを保ち本当の成功で消える(stuck_account, frozen_now_jst):
    """失敗→何もしない run→失敗→何もしない run→成功（時計も進める）。"""
    account = stuck_account
    _run_notify(account, "fail", FAILED)
    first = _board_stuck(account)
    assert first["code"] == "publish_http_401"
    assert "thth refresh" in first["fix"]  # 401 の次の一手は鍵（メールの文は変えない）
    for state, result in (("success", IDLE), ("fail", FAILED), ("success", IDLE)):
        frozen_now_jst(_later(6))
        _run_notify(account, state, result)
        assert _board_stuck(account) == first
    _run_notify(account, "success", POSTED)
    assert _board_stuck(account) is None


def test_401と鍵の失敗が重なれば根の鍵で言いsinceは早い方(stuck_account, frozen_now_jst):
    account = stuck_account
    _run_notify(account, "fail", FAILED)
    since = _board_stuck(account)["since"]
    frozen_now_jst(_later(6))
    _run(now=account["now"], refresh=_fails)
    value = _board_stuck(account)
    assert value["code"] == "token_refresh_failed" and value["since"] == since


def test_鍵が戻っても公開の止まりは公開が通るまで残る(stuck_account):
    account = stuck_account
    _run_notify(account, "fail", FAILED)
    _run(now=account["now"], refresh=_fails)
    _run(now=account["now"], refresh=_ok_refresh(account))
    assert _board_stuck(account)["code"] == "publish_http_401"
    _run_notify(account, "success", POSTED)
    assert _board_stuck(account) is None


def test_boardは何も書かない(stuck_account):
    account = stuck_account
    _run(now=account["now"], refresh=_fails)
    state = Path(account["state"])

    def snapshot():
        return {path: path.read_bytes() for path in state.rglob("*") if path.is_file()}
    before = snapshot()
    _board_stuck(account)
    assert snapshot() == before


def test_stuckに宛先と鍵の値が入らない(stuck_account):
    account = stuck_account
    _run_notify(account, "fail", FAILED)
    text = json.dumps(_board_stuck(account), ensure_ascii=False)
    assert "example.org" not in text and TOKEN_VALUE not in text


def test_読めないoutboxは止まっていないと言わない(stuck_account):
    from thth import incident
    state = Path(stuck_account["state"])
    state.mkdir(parents=True, exist_ok=True)
    (state / incident.STATE_FILE).write_text("{broken")
    assert _board_stuck(stuck_account)["code"] == "incident_outbox_unreadable"
    assert _board_stuck(stuck_account)["since"] is None
    rc, _ = _run(now=stuck_account["now"], refresh=_fails)  # maintain も落ちない
    assert rc == 1


def test_heldもstuckに出て0本で消える(stuck_account):
    from thth import healthcheck, incident
    account = stuck_account
    held = healthcheck.Diagnostic(account["name"], "held", None, None, "x", "x", "approved_but_held: stale 2", "none")
    incident.notify(account["name"], account["cfg"], held, state_dir=account["state"], result=IDLE)
    value = _board_stuck(account)
    assert value["code"] == "approved_but_held" and "2 本" in value["what"]
    _run_notify(account, "success", IDLE)
    assert _board_stuck(account) is None


def test_checkは何も控えない(stuck_account):
    from thth import incident
    account = stuck_account
    _run(now=account["now"], check=True)
    assert not (Path(account["state"]) / incident.STATE_FILE).exists()
    _run(now=account["now"], refresh=_fails)
    before = _outbox(account)
    _write_token(account["token_path"], age_days=0, now=account["now"])
    _run(now=account["now"], check=True)  # 鍵が戻っていても --check では解かない
    assert _outbox(account) == before


def test_載せない要確認では鍵が戻ったと言わない(stuck_account):
    account = stuck_account
    _run(now=account["now"], refresh=_fails)
    os.unlink(account["token_path"])  # no_token は run の止まりが言う
    _run(now=account["now"], refresh=_fails)
    assert _outbox(account)["token_incident"] == "token_refresh_failed"


def test_accountのロックを握られていても控えられる(stuck_account):
    from thth import lock as lock_mod
    account = stuck_account
    held = lock_mod.AccountLock(accounts_mod.account_lock_path_for(account["name"]))
    held.acquire()
    try:
        _run(now=account["now"], refresh=_fails)
    finally:
        held.release()
    assert _outbox(account)["token_incident"] == "token_refresh_failed"


def test_止まったことのない口座にoutboxを作らない(tmp_path, isolated_account_factory):
    from thth import incident
    account = _account(isolated_account_factory, tmp_path, age_days=10)
    _run(now=account["now"])
    assert not (Path(accounts_mod.state_dir_for(account["name"])) / incident.STATE_FILE).exists()


def _x_account(factory, tmp_path, *, seconds_left, name="demo-x"):
    now = jst.now_jst()
    path = str(tmp_path / f"{name}.token")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"access_token": "a" * 20, "refresh_token": "r" * 20,
                   "obtained_at": jst.iso(now - datetime.timedelta(hours=2)),
                   "expires_in": 7200 + seconds_left}, f)
    return factory(name=name, media="x", token=path, production=True)


def test_読む口の自動更新の失敗は時刻だけ残し次のrunでstuckになる(tmp_path, monkeypatch, isolated_account_factory):
    from thth import healthcheck, incident
    from thth.adapters import auth_x
    account = _x_account(isolated_account_factory, tmp_path, seconds_left=60)
    name = account["name"]
    cfg = accounts_mod.load_account(name)
    state = accounts_mod.state_dir_for(name)
    monkeypatch.setattr(auth_x, "run_refresh", lambda *a, **k: 2)
    assert accounts_mod.load_token(cfg)["access_token"]  # 読む口は止まらない
    marker = Path(state) / accounts_mod.TOKEN_REFRESH_FAILURE_FILE
    assert list(json.loads(marker.read_text())) == ["at"]
    diag = healthcheck.Diagnostic(name, "success", None, None, "x", "x", "healthy", "none")
    incident.notify(name, cfg, diag, state_dir=state)
    assert incident.stuck(name, state)["code"] == "token_refresh_failed"

    # 手の `thth refresh` で鍵が戻ったら、次の run が解く（目印は古いまま残っていてよい）。
    with open(cfg["token"], encoding="utf-8") as f:
        token = json.load(f)
    token.update(obtained_at=jst.iso(jst.now_jst()), expires_in=7200)
    with open(cfg["token"], "w", encoding="utf-8") as f:
        json.dump(token, f)
    incident.notify(name, cfg, diag, state_dir=state)
    assert incident.stuck(name, state) is None


def test_入れ子の管理の処理で断られただけなら失敗に数えない(tmp_path, monkeypatch, isolated_account_factory):
    from thth import admin_log
    from thth.adapters import auth_x
    account = _x_account(isolated_account_factory, tmp_path, seconds_left=60)
    cfg = accounts_mod.load_account(account["name"])
    monkeypatch.setattr(auth_x, "run_refresh", lambda *a, **k: 2)
    reset = admin_log._active_fd.set(99)
    try:
        accounts_mod.load_token(cfg)
    finally:
        admin_log._active_fd.reset(reset)
    assert accounts_mod.token_refresh_failed_at(accounts_mod.state_dir_for(account["name"])) is None
