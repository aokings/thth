"""`thth auth`（発注 T2a）。偽 OAuth API に向けて、正常系・貼り付けの揺れ・
app.env 無し・交換の失敗・秘密が出力に出ないことを確認する。

本物の Threads API（graph.threads.net・threads.net）には一切触れない。
"""
from __future__ import annotations

import json
import os
import stat

import pytest

from tests.conftest import run_thth
from tests.helpers.fake_oauth_server import fake_oauth_server
from thth import accounts as accounts_mod
from thth import oauth as oauth_mod

# **`example.invalid` を使わない**（監査 2・C10・2026-09-13）。そこは雛形の
# ダミー（`accounts.example/threads.json`）の綴りで、`thth auth` は**認可 URL を
# 出す前に断る**ようになった。ここで確かめたいのは往復のほうなので、実在しない
# ことは同じで**ダミーではない**綴り（`.test` は RFC 6761 の予約 TLD）を使う。
# ダミーで断ることそのものは `test_C10_ダミーのredirect_uriでは認可URLを出さない`。
REDIRECT_URI = "https://nigamilab.example.test/"
APP_SECRET_VALUE = "APP-SECRET-super-value-01234"
APP_ID_VALUE = "1234567890"


# **戻り URL には `state` が要る**（セキュリティ監査 2026-09-14・P2-4）。
# `thth auth` は認可 URL に `state` を載せ、戻りでそれを照合する（載っていない
# 戻りは受け付けない）。テストは `_new_state()` を固定して、**人がブラウザから
# 持ち帰る形**（`?code=…&state=…#_`）をそのまま貼る。
FIXED_STATE = "FIXED-STATE-FOR-TESTS"


@pytest.fixture(autouse=True)
def 固定のstate(monkeypatch):
    monkeypatch.setattr(oauth_mod, "_new_state", lambda: FIXED_STATE)
    return FIXED_STATE


def 戻り(code: str = "ABC123", *, state: str = FIXED_STATE, suffix: str = "#_") -> str:
    """ブラウザが返す形の戻り URL（`code` と `state` の両方が付く）。"""
    q = f"code={code}" + (f"&state={state}" if state is not None else "")
    return f"{REDIRECT_URI}?{q}{suffix}"


def _write_app_env(tmp_path, monkeypatch, *, app_id=APP_ID_VALUE, app_secret=APP_SECRET_VALUE):
    path = tmp_path / "app.env"
    path.write_text(f"THREADS_APP_ID={app_id}\nTHREADS_APP_SECRET={app_secret}\n", encoding="utf-8")
    os.chmod(path, 0o600)
    monkeypatch.setenv("THTH_APP_ENV_PATH", str(path))
    return str(path)


def _account_with_token_path(isolated_account_factory, tmp_path, **overrides):
    token_path = str(tmp_path / "account.token")
    overrides.setdefault("redirect_uri", REDIRECT_URI)
    account = isolated_account_factory(token=token_path, **overrides)
    account["token_path"] = token_path
    # --code resumes a previously issued state; it must not create a fresh flow.
    oauth_mod._save_auth_state(account["name"], FIXED_STATE)
    return account


def test_1_正常系は短期長期めをたどってtokenを書く(tmp_path, monkeypatch, isolated_account_factory):
    _write_app_env(tmp_path, monkeypatch)
    account = _account_with_token_path(isolated_account_factory, tmp_path)

    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        lines = []
        rc = oauth_mod.run_auth(account["name"], code=戻り("ABC123"), log=lines.append, by="test-operator")

    assert rc == 0
    with open(account["token_path"], encoding="utf-8") as f:
        token = json.load(f)
    assert token["access_token"] == "LONG-SECRET-TOKEN"
    assert token["user_id"] == "999999"
    assert token["username"] == "nigamilab"
    assert token["expires_in"] == 5184000
    assert "obtained_at" in token
    assert token["scopes"]  # 既定 scope が入っている

    mode = stat.S_IMODE(os.stat(account["token_path"]).st_mode)
    assert mode == 0o600

    out = "\n".join(lines)
    assert "999999" in out and "nigamilab" in out
    assert "LONG-SECRET-TOKEN" not in out
    assert "SHORT-SECRET-TOKEN" not in out
    assert APP_SECRET_VALUE not in out


def test_2_hash_underscore付きの貼り付けも通る(tmp_path, monkeypatch, isolated_account_factory):
    _write_app_env(tmp_path, monkeypatch)
    account = _account_with_token_path(isolated_account_factory, tmp_path)

    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc = oauth_mod.run_auth(account["name"], code=戻り("ABC123"), log=lambda _l: None, by="test-operator")

    assert rc == 0
    assert os.path.exists(account["token_path"])


def test_3_url全体の貼り付けも通る(tmp_path, monkeypatch, isolated_account_factory):
    _write_app_env(tmp_path, monkeypatch)
    account = _account_with_token_path(isolated_account_factory, tmp_path)
    pasted = 戻り("ABC123")

    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc = oauth_mod.run_auth(account["name"], code=pasted, log=lambda _l: None, by="test-operator")

    assert rc == 0
    with open(account["token_path"], encoding="utf-8") as f:
        token = json.load(f)
    assert token["access_token"] == "LONG-SECRET-TOKEN"


def test_4_app_env無しはexit2(tmp_path, monkeypatch, isolated_account_factory):
    monkeypatch.setenv("THTH_APP_ENV_PATH", str(tmp_path / "does-not-exist.env"))
    account = _account_with_token_path(isolated_account_factory, tmp_path)

    lines = []
    rc = oauth_mod.run_auth(account["name"], code=戻り("ABC123"), log=lines.append, by="test-operator")

    assert rc == 2
    assert not os.path.exists(account["token_path"])
    assert any("app.env" in line for line in lines)


def test_5_交換が4xxならexit1でtokenを作らない(tmp_path, monkeypatch, isolated_account_factory):
    _write_app_env(tmp_path, monkeypatch)
    account = _account_with_token_path(isolated_account_factory, tmp_path)

    with fake_oauth_server({"short": "4xx"}) as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        lines = []
        rc = oauth_mod.run_auth(account["name"], code=戻り("ABC123"), log=lines.append, by="test-operator")

    assert rc == 1
    assert not os.path.exists(account["token_path"])


def test_6_長期交換が4xxならexit1でtokenを作らない(tmp_path, monkeypatch, isolated_account_factory):
    _write_app_env(tmp_path, monkeypatch)
    account = _account_with_token_path(isolated_account_factory, tmp_path)

    with fake_oauth_server({"long": "4xx"}) as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc = oauth_mod.run_auth(account["name"], code=戻り("ABC123"), log=lambda _l: None, by="test-operator")

    assert rc == 1
    assert not os.path.exists(account["token_path"])


def test_7_meが失敗してもexit1でtokenを作らない(tmp_path, monkeypatch, isolated_account_factory):
    _write_app_env(tmp_path, monkeypatch)
    account = _account_with_token_path(isolated_account_factory, tmp_path)

    with fake_oauth_server({"me": "5xx"}) as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc = oauth_mod.run_auth(account["name"], code=戻り("ABC123"), log=lambda _l: None, by="test-operator")

    assert rc == 1
    assert not os.path.exists(account["token_path"])


def test_8_redirect_uri無しはexit2(tmp_path, monkeypatch, isolated_account_factory):
    _write_app_env(tmp_path, monkeypatch)
    token_path = str(tmp_path / "account.token")
    account = isolated_account_factory(token=token_path)  # redirect_uri を渡さない

    rc = oauth_mod.run_auth(account["name"], code=戻り("ABC123"), log=lambda _l: None, by="test-operator")

    assert rc == 2
    assert not os.path.exists(token_path)


def test_C10_ダミーのredirect_uriでは認可URLを出さない(
        tmp_path, monkeypatch, isolated_account_factory):
    """**(d) ダミーのまま認可 URL を出さない**（監査 2・C10・masaru 裁定 2026-09-13）。

    `thth account add` が写す雛形の `redirect_uri` は `https://example.invalid/`
    ——存在しないホスト。前はその値で認可 URL を組んで表示していたので、打った人は
    ブラウザで開き、**Meta 側の英語のエラー**で初めて詰まった。道具は開く前に
    知っているのだから、**URL を出す前に**言う。
    """
    _write_app_env(tmp_path, monkeypatch)
    token_path = str(tmp_path / "account.token")
    account = isolated_account_factory(token=token_path,
                                        redirect_uri="https://example.invalid/")

    lines = []
    rc = oauth_mod.run_auth(account["name"], code=戻り("ABC123"), log=lines.append, by="test-operator")
    out = "\n".join(lines)

    assert rc == 2, out
    # **URL を出す前**に断っている（出してしまったら、その人はもう開いている）。
    assert "oauth/authorize" not in out, out
    assert "ダミー" in out and "https://example.invalid/" in out, out
    assert "--redirect-uri" in out, out          # 直し方 その 1
    assert f"{account['name']}.json" in out, out  # 直し方 その 2（台帳の場所を名指し）
    assert not os.path.exists(token_path)

    # --codeは既存stateの再開なので、前回発行したstateを置く。
    oauth_mod._save_auth_state(account["name"], FIXED_STATE)
    # **`--redirect-uri` で本物を渡した分には通る**（断る条件が広すぎない）。
    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        lines2 = []
        rc2 = oauth_mod.run_auth(account["name"], redirect_uri=REDIRECT_URI,
                                 code=戻り("ABC123"), log=lines2.append, by="test-operator")
    assert rc2 == 0, "\n".join(lines2)
    assert os.path.exists(token_path)


def test_9_CLI経由でも動く_codeフラグ(tmp_path, monkeypatch, isolated_account_factory):
    """`bin/thth auth <account>` → 戻り URL を `--code` で渡す（subprocess・2 段）。

    **1 回では終われない**（セキュリティ監査 2026-09-14・P2-4）。`state` を出した
    のは 1 段目の実行なので、戻りを照合できるのは**その次の実行**。1 段目は URL を
    出してから「端末から読めません」で rc=2（黙って traceback にしない）。
    """
    import urllib.parse as _up

    _write_app_env(tmp_path, monkeypatch)
    account = _account_with_token_path(isolated_account_factory, tmp_path)
    env = {"THTH_APP_ENV_PATH": os.environ["THTH_APP_ENV_PATH"]}

    # 1 段目: 認可 URL を出す（state を残す）。標準入力は空なので読めずに rc=2。
    first = run_thth(["auth", account["name"], "--by", "test-operator"], env=env, stdin="")
    assert first.returncode == 2, first.stdout + first.stderr
    url = next(line for line in first.stdout.splitlines() if "oauth/authorize" in line)
    state = _up.parse_qs(_up.urlsplit(url).query)["state"][0]
    assert state, url
    assert not os.path.exists(account["token_path"])

    # 2 段目: ブラウザから持ち帰った戻り URL をそのまま渡す。
    with fake_oauth_server() as base_url:
        env2 = dict(env, THTH_THREADS_BASE_URL=base_url)
        result = run_thth(
            ["auth", account["name"], "--code", 戻り("ABC123", state=state), "--by", "test-operator"], env=env2)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "LONG-SECRET-TOKEN" not in result.stdout
    assert "LONG-SECRET-TOKEN" not in result.stderr
    assert APP_SECRET_VALUE not in result.stdout
    assert os.path.exists(account["token_path"])


def test_10_app_envのパーミッションが600でなければ直す(tmp_path, monkeypatch, isolated_account_factory):
    path = tmp_path / "app.env"
    path.write_text(f"THREADS_APP_ID={APP_ID_VALUE}\nTHREADS_APP_SECRET={APP_SECRET_VALUE}\n",
                     encoding="utf-8")
    os.chmod(path, 0o644)  # わざと緩くしておく
    monkeypatch.setenv("THTH_APP_ENV_PATH", str(path))
    account = _account_with_token_path(isolated_account_factory, tmp_path)

    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        lines = []
        rc = oauth_mod.run_auth(account["name"], code=戻り("ABC123"), log=lines.append, by="test-operator")

    assert rc == 0
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert any("600" in line for line in lines)  # 警告が出ている


def test_20260909_authが標準出力に秘密を一切出さない(tmp_path, monkeypatch, isolated_account_factory, capsys):
    """事故防止テスト: token・app secret・code のいずれも標準出力・ログに出ないことを、
    実際に仕込んだ秘密文字列で確認する（設計 §3.6・masaru の指示）。"""
    secret_code = "SUPER-SECRET-CODE-VALUE-9999"
    _write_app_env(tmp_path, monkeypatch, app_secret=APP_SECRET_VALUE)
    account = _account_with_token_path(isolated_account_factory, tmp_path)

    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        lines = []
        rc = oauth_mod.run_auth(account["name"], code=戻り(secret_code), log=lines.append, by="test-operator")

    assert rc == 0
    out = "\n".join(lines)
    for secret in (secret_code, APP_SECRET_VALUE, "LONG-SECRET-TOKEN", "SHORT-SECRET-TOKEN"):
        assert secret not in out, f"秘密が出力に出た: {secret}"

    # print() へも一切出していないこと（run_auth の既定 log=print 経路も確認する）。
    capsys.readouterr()  # 直前の出力を捨てる
    lines2 = []
    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        token_path2 = str(tmp_path / "account2.token")
        account2 = isolated_account_factory("nigamilab-threads-2", token=token_path2,
                                             redirect_uri=REDIRECT_URI, repo_dir=account["repo_dir"])
        oauth_mod.run_auth(account2["name"], code=戻り(secret_code), by="test-operator")  # log=print(既定)
    captured = capsys.readouterr()
    for secret in (secret_code, APP_SECRET_VALUE, "LONG-SECRET-TOKEN", "SHORT-SECRET-TOKEN"):
        assert secret not in captured.out
        assert secret not in captured.err


# --- T3 の配線（2026-09-13）: `thth auth` を媒体で分ける ------------------------

def test_T3_mastodonのsetup不明はrc2で保存しない(tmp_path, isolated_account_factory, monkeypatch):
    """2.11: 不明なmetadataからPKCEなしへ降りない。"""
    from thth.adapters import auth_mastodon
    def unavailable(*args, **kwargs):
        raise auth_mastodon.FlowError('metadata unavailable')
    monkeypatch.setattr(auth_mastodon, 'request', unavailable)
    account = _account_with_token_path(
        isolated_account_factory, tmp_path, media="mastodon",
        instance="https://mastodon.invalid")
    lines = []
    rc = oauth_mod.run_auth(account["name"], log=lines.append, by="test-operator")
    assert rc == 2
    out = "\n".join(lines)
    assert "mastodon_auth_setup_failed" in out
    assert not os.path.exists(account["token_path"])


def test_T3_知らない媒体はloudに断る(tmp_path, isolated_account_factory):
    account = _account_with_token_path(
        isolated_account_factory, tmp_path, media="carrier-pigeon")
    lines = []
    assert oauth_mod.run_auth(account["name"], log=lines.append, by="test-operator") == 2
    out = "\n".join(lines)
    assert "carrier-pigeon" in out and "知っている媒体" in out


def test_T3_blueskyはtokenを600で書く(tmp_path, isolated_account_factory):
    """`bluesky.auth_interactive()` の戻りを `.token` に 600 で原子的に書く。"""
    from tests.test_bluesky_adapter import APP_PASSWORD, DID, HANDLE, fake_bluesky

    with fake_bluesky() as service:
        account = _account_with_token_path(
            isolated_account_factory, tmp_path, media="bluesky",
            handle=HANDLE, user_id=DID, service=str(service))
        lines = []
        rc = oauth_mod.run_auth(
            account["name"], log=lines.append,
            identifier_input=lambda: HANDLE,
            password_input=lambda: APP_PASSWORD, by="test-operator")

    assert rc == 0, lines
    with open(account["token_path"], encoding="utf-8") as f:
        token = json.load(f)
    assert token["identifier"] == HANDLE
    assert token["app_password"] == APP_PASSWORD
    assert token["did"] == DID and token["handle"] == HANDLE
    # **期限を持たない**（`expires_in` は書かない・設計 v2 §4.2）。
    assert token["no_expiry"] is True and "expires_in" not in token
    # `whoami()` と同じ鍵（board・doctor がここを読む）。
    assert token["user_id"] == DID and token["username"] == HANDLE
    # 600（`thth app set`・`token set` と同じ作法）。
    assert stat.S_IMODE(os.stat(account["token_path"]).st_mode) == 0o600
    # **値はどこにも出さない。**
    out = "\n".join(lines)
    assert APP_PASSWORD not in out
    assert "ACCESS-SECRET-JWT" not in out and "REFRESH-SECRET-JWT" not in out
    assert "600" in out


def test_T3_blueskyのcreateSessionが落ちたらtokenを作らない(tmp_path,
                                                    isolated_account_factory):
    from tests.test_bluesky_adapter import APP_PASSWORD, HANDLE, fake_bluesky

    with fake_bluesky({"com.atproto.server.createSession": "4xx"}) as service:
        account = _account_with_token_path(
            isolated_account_factory, tmp_path, media="bluesky",
            handle=HANDLE, service=str(service))
        lines = []
        rc = oauth_mod.run_auth(
            account["name"], log=lines.append,
            identifier_input=lambda: HANDLE,
            password_input=lambda: APP_PASSWORD, by="test-operator")

    assert rc == 1
    assert not os.path.exists(account["token_path"])
    assert APP_PASSWORD not in "\n".join(lines)


def test_T3_blueskyは秘密を返してくるサーバでも値を出さない(tmp_path,
                                                 isolated_account_factory):
    """**200 のまま `error` を返し、秘密をそのまま echo するサーバ。**"""
    from tests.test_bluesky_adapter import APP_PASSWORD, HANDLE, fake_bluesky

    with fake_bluesky({"com.atproto.server.createSession": "echo_secret"}) as service:
        account = _account_with_token_path(
            isolated_account_factory, tmp_path, media="bluesky",
            handle=HANDLE, service=str(service))
        lines = []
        rc = oauth_mod.run_auth(
            account["name"], log=lines.append,
            identifier_input=lambda: HANDLE,
            password_input=lambda: APP_PASSWORD, by="test-operator")

    assert rc == 1
    assert not os.path.exists(account["token_path"])
    assert APP_PASSWORD not in "\n".join(lines)


def test_T3_blueskyはアカウントの取り違えを保存しない(tmp_path, isolated_account_factory):
    """台帳の handle と App Password の指す先が違えば書かない（`token set` と同じ筋）。

    通すと、**そのアカウントの queue の本文が別のアカウントから出る。**
    """
    from tests.test_bluesky_adapter import APP_PASSWORD, HANDLE, fake_bluesky

    with fake_bluesky() as service:
        account = _account_with_token_path(
            isolated_account_factory, tmp_path, media="bluesky",
            handle="someone-else.bsky.social", service=str(service))
        lines = []
        rc = oauth_mod.run_auth(
            account["name"], log=lines.append,
            identifier_input=lambda: HANDLE,
            password_input=lambda: APP_PASSWORD, by="test-operator")

    assert rc == 1
    assert not os.path.exists(account["token_path"])
    assert "保存しませんでした" in "\n".join(lines)


def test_T3_blueskyはアカウントのパスワードを断る(tmp_path, isolated_account_factory):
    """App Password の形（xxxx-xxxx-xxxx-xxxx）でなければ loud に断る。

    アカウントのパスワードは管理画面から取り消せない——`.token` に置いてよい
    ものではない（`bluesky.auth_interactive()` の但し書き）。
    """
    from tests.test_bluesky_adapter import HANDLE, fake_bluesky

    with fake_bluesky() as service:
        account = _account_with_token_path(
            isolated_account_factory, tmp_path, media="bluesky",
            handle=HANDLE, service=str(service))
        lines = []
        rc = oauth_mod.run_auth(
            account["name"], log=lines.append,
            identifier_input=lambda: HANDLE,
            password_input=lambda: "my-real-account-password", by="test-operator")

    assert rc == 1
    assert not os.path.exists(account["token_path"])
    out = "\n".join(lines)
    assert "App Password" in out
    assert "my-real-account-password" not in out


def test_T3_端末でなければ読まない(tmp_path, monkeypatch, isolated_account_factory):
    """tty を割り当てない ssh で秘密が画面に出る経路を塞ぐ（`thth app set` と同じ）。"""
    import sys as _sys

    class _NotATty:
        def isatty(self):
            return False

    monkeypatch.setattr(_sys, "stdin", _NotATty())
    account = _account_with_token_path(
        isolated_account_factory, tmp_path, media="bluesky",
        handle="x.bsky.social", service="https://bsky.invalid")
    lines = []
    rc = oauth_mod.run_auth(account["name"], log=lines.append, by="test-operator")
    assert rc == 2
    assert "端末ではありません" in "\n".join(lines)
    assert not os.path.exists(account["token_path"])


def test_T3_threadsの経路は現行のまま(tmp_path, monkeypatch, isolated_account_factory):
    """**媒体分岐を足しても Threads の挙動は変わらない。**"""
    _write_app_env(tmp_path, monkeypatch)
    account = _account_with_token_path(isolated_account_factory, tmp_path)
    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        monkeypatch.setenv("THTH_THREADS_AUTH_BASE_URL", base_url)
        rc = oauth_mod.run_auth(account["name"], code=戻り("THE-CODE"),
                                 log=lambda _l: None, by="test-operator")
    assert rc == 0
    with open(account["token_path"], encoding="utf-8") as f:
        token = json.load(f)
    # Threads は期限を持つ（`expires_in` が入り、`no_expiry` は立たない）。
    assert token["expires_in"] and not token.get("no_expiry")


# --- セキュリティ監査 2026-09-14・P2-4 ----------------------------------------
# (1) 認可 URL に `state` が載り、戻りで照合する（載っていない戻りは受け付けない）。
# (2) `thth token set` にはあった **handle の照合**が `thth auth` に無かった
#     ——同じ危険の同じ守りが片方にしか無い。

def test_P2_4_認可URLにstateが載る(tmp_path, monkeypatch, isolated_account_factory):
    import urllib.parse as _up

    _write_app_env(tmp_path, monkeypatch)
    account = _account_with_token_path(isolated_account_factory, tmp_path)
    lines = []
    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        oauth_mod.run_auth(account["name"], input_func=lambda: 戻り("ABC123"),
                           log=lambda _: None, human_output=lines.append, by="test-operator")
    url = next(line for line in lines if "oauth/authorize" in line)
    assert _up.parse_qs(_up.urlsplit(url).query)["state"] == [FIXED_STATE]


def test_P2_4_stateが無い戻りは受け付けない(tmp_path, monkeypatch, isolated_account_factory):
    """**`code` の値だけ**の貼り付けは、もう通さない。"""
    _write_app_env(tmp_path, monkeypatch)
    account = _account_with_token_path(isolated_account_factory, tmp_path)
    lines = []
    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc = oauth_mod.run_auth(account["name"], code="ABC123", log=lines.append, by="test-operator")
    assert rc == 2
    out = "\n".join(lines)
    assert "state がありません" in out
    assert not os.path.exists(account["token_path"])


def test_P2_4_stateが違う戻りは受け付けない(tmp_path, monkeypatch, isolated_account_factory):
    """別のところで作られた認可の戻り（攻撃者のアプリ・別の往復）を貼られた形。"""
    _write_app_env(tmp_path, monkeypatch)
    account = _account_with_token_path(isolated_account_factory, tmp_path)
    lines = []
    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc = oauth_mod.run_auth(account["name"],
                                 code=戻り("ABC123", state="よその-state"),
                                 log=lines.append, by="test-operator")
    assert rc == 1 or rc == 2
    assert "state が一致しません" in "\n".join(lines)
    assert not os.path.exists(account["token_path"])


def test_P2_4_前回の実行が出したstateなら通る(tmp_path, monkeypatch, isolated_account_factory):
    """`--code` は**次の実行**なので、前回の `state` と照合できる（2 段の筋）。"""
    _write_app_env(tmp_path, monkeypatch)
    account = _account_with_token_path(isolated_account_factory, tmp_path)

    # 1 段目（URL を出すだけ。state が残る）。
    monkeypatch.setattr(oauth_mod, "_new_state", lambda: "前回の-state")
    oauth_mod.run_auth(account["name"], input_func=lambda: "", log=lambda _l: None, by="test-operator")
    # 2 段目は保存済みstateを再開し、新しいstateで上書きしない。
    monkeypatch.setattr(oauth_mod, "_new_state", lambda: "こんかいの-state")
    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc = oauth_mod.run_auth(account["name"],
                                 code=戻り("ABC123", state="前回の-state"),
                                 log=lambda _l: None, by="test-operator")
    assert rc == 0
    assert os.path.exists(account["token_path"])


def test_P2_4_handleが食い違えば保存しない(tmp_path, monkeypatch, isolated_account_factory):
    """偽 `me` は `nigamilab` を返す。台帳の handle を別人にして確かめる。"""
    _write_app_env(tmp_path, monkeypatch)
    account = _account_with_token_path(isolated_account_factory, tmp_path,
                                        handle="べつのひと")
    lines = []
    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc = oauth_mod.run_auth(account["name"], code=戻り("ABC123"),
                                 log=lines.append, by="test-operator")
    assert rc == 1
    out = "\n".join(lines)
    assert "保存しませんでした" in out and "べつのひと" in out and "nigamilab" in out
    assert not os.path.exists(account["token_path"])


def test_P2_4_handleが合っていれば今までどおり保存する(tmp_path, monkeypatch,
                                                    isolated_account_factory):
    _write_app_env(tmp_path, monkeypatch)
    account = _account_with_token_path(isolated_account_factory, tmp_path,
                                        handle="NigamiLab")   # 大文字小文字は問わない
    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc = oauth_mod.run_auth(account["name"], code=戻り("ABC123"),
                                 log=lambda _l: None, by="test-operator")
    assert rc == 0
    assert os.path.exists(account["token_path"])
