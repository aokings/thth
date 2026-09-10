"""`thth auth` / `thth refresh` / `thth token set`（発注 T2 前半・T2b・設計 §2.2「トークン」節）。

masaru が VM で対話的に実行する。ブラウザは masaru の Mac にあるので、ここでは
「認可 URL を表示 → masaru がブラウザで承認 → 戻り URL に付く `code` を貼ってもらう」
までを対話でやり、あとは機械的に短期→長期トークンへ交換して `.token` に書く
（`thth auth`）。

2026-09-09、Meta の管理画面に「ユーザートークン生成ツール」があり、Threads
テスターの長期アクセストークンを OAuth の往復無しでボタン 1 つで発行できることが
分かった。`thth token set` はそのトークンを masaru から直接受け取って `.token` に
保存する（`thth auth` は tester 以外を扱う日のために残す。削除しない）。

トークン・app secret・code は標準出力・ログ・例外文に一切出さない。すべての
出力は `redact()` を通す（`_out()` を経由すれば自動で通る）。

本物の Threads API は叩かない。`base_url` は環境変数で差し替え可能（テストは
`http.server` の偽 API にだけ向ける）:
  - `THTH_THREADS_AUTH_BASE_URL`: 認可 URL の組み立てに使うホスト（既定
    `https://threads.net`）。masaru のブラウザが開くだけで、THTH 自身は
    ここへ HTTP アクセスしない。
  - `THTH_THREADS_BASE_URL`: 短期→長期トークン交換・`me`・更新の API 呼び出し
    （`graph.threads.net` 相当）。`thth/adapters/threads.py` と同じ環境変数を
    共有する（同じホストを指すので、テストの偽サーバも 1 つで両方賄える）。
"""
from __future__ import annotations

import getpass
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from . import accounts as accounts_mod
from . import appenv
from . import jst
from . import redact as redact_mod
from . import scopes as scopes_mod
from . import secrets_fs
from .adapters import threads as threads_mod

AUTHORIZE_BASE_URL_DEFAULT = "https://threads.net"

# 公式の条件（設計 §2.2 トークンの節）: 24 時間以上経過・未失効でないと更新できない。
MIN_REFRESH_AGE_HOURS = 24.0
# 運用の既定（発注 T2a 指示）: 50 日超で更新。`--force` で無条件（24 時間の下限は除く）。
REFRESH_AFTER_DAYS = 50.0
# 長期トークンの既定寿命（応答に expires_in が無いときの控え。公式ドキュメントの 60 日）。
DEFAULT_TOKEN_LIFETIME_SECONDS = 60 * 24 * 3600


class OAuthError(Exception):
    """トークン取得・更新のどこかで失敗した（メッセージは redact 済み）。"""


def _out(line: str, *, log) -> None:
    log(redact_mod.redact(str(line)))


def _graph_base_url() -> str:
    return os.environ.get("THTH_THREADS_BASE_URL", threads_mod.DEFAULT_BASE_URL).rstrip("/")


def _authorize_base_url() -> str:
    return os.environ.get("THTH_THREADS_AUTH_BASE_URL", AUTHORIZE_BASE_URL_DEFAULT).rstrip("/")


def build_authorize_url(app_id: str, redirect_uri: str, scopes: list) -> str:
    params = {
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "scope": ",".join(scopes),
        "response_type": "code",
    }
    return f"{_authorize_base_url()}/oauth/authorize?" + urllib.parse.urlencode(params)


def extract_code(raw: str) -> str:
    """masaru がどう貼っても `code` の値だけを取り出す（設計 §9-2・L3）。

    対応する形:
      - code の値そのまま（例: `AQD...`）
      - 末尾に Meta が付ける `#_`（例: `AQD...#_`）
      - 戻り URL 全体（例: `https://nigamilab.com/?code=AQD...#_`）
    """
    text = (raw or "").strip()
    # Meta が付ける末尾の `#_`（または他のフラグメント）を先に落とす。
    for sep in ("#_", "#"):
        idx = text.find(sep)
        if idx != -1:
            text = text[:idx]
            break
    if "code=" in text:
        after = text.split("code=", 1)[1]
        for sep in ("&", " ", "\t", "\n"):
            cut = after.find(sep)
            if cut != -1:
                after = after[:cut]
        text = after
    text = text.strip()
    try:
        text = urllib.parse.unquote(text)
    except Exception:
        pass
    return text


def _strip_quotes(text: str) -> str:
    text = (text or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    return text


def extract_token(raw: str) -> str:
    """masaru が管理画面のトークン発行ツールからどう貼っても値だけを取り出す
    （`extract_code()` と同じ思想。`thth token set` 用）。

    対応する形:
      - トークンの値そのまま
      - 前後の空白・改行・引用符
      - `access_token=...` のような接頭辞（URL・クエリ文字列ごと貼った場合）
    """
    text = _strip_quotes(raw)
    if "access_token=" in text:
        after = text.split("access_token=", 1)[1]
        for sep in ("&", " ", "\t", "\n", "#"):
            cut = after.find(sep)
            if cut != -1:
                after = after[:cut]
        text = _strip_quotes(after)
    try:
        text = urllib.parse.unquote(text)
    except Exception:
        pass
    return text


def _post_form(url: str, params: dict, *, timeout: float) -> dict:
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        return json.loads(body) if body else {}


def _get_json(url: str, params: dict, *, timeout: float) -> dict:
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{qs}", method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        return json.loads(body) if body else {}


def _error_message(prefix: str, exc: Exception) -> str:
    """例外から人が読めるメッセージだけを取り出し、伏字にして返す（URL・secret は含めない）。"""
    if isinstance(exc, urllib.error.HTTPError):
        detail = f"{exc.code} {exc.reason}"
        try:
            body = exc.read()
            if body:
                payload = json.loads(body)
                msg = (payload.get("error") or {}).get("message")
                if msg:
                    detail = f"{exc.code} {msg}"
        except Exception:
            pass
        return redact_mod.redact(f"{prefix}: {detail}")
    return redact_mod.redact(f"{prefix}: {exc}")


def exchange_short_lived_token(app_id: str, app_secret: str, redirect_uri: str, code: str,
                                *, timeout: float = 10.0) -> dict:
    url = f"{_graph_base_url()}/oauth/access_token"
    params = {
        "client_id": app_id,
        "client_secret": app_secret,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
        "code": code,
    }
    try:
        return _post_form(url, params, timeout=timeout)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
        raise OAuthError(_error_message("短期トークンの取得に失敗しました", e)) from e


def exchange_long_lived_token(app_secret: str, access_token: str, *, timeout: float = 10.0) -> dict:
    url = f"{_graph_base_url()}/access_token"
    params = {
        "grant_type": "th_exchange_token",
        "client_secret": app_secret,
        "access_token": access_token,
    }
    try:
        return _get_json(url, params, timeout=timeout)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
        raise OAuthError(_error_message("長期トークンの交換に失敗しました", e)) from e


def fetch_me(access_token: str, *, timeout: float = 10.0) -> dict:
    url = f"{_graph_base_url()}/v1.0/me"
    params = {"fields": "id,username", "access_token": access_token}
    try:
        return _get_json(url, params, timeout=timeout)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
        raise OAuthError(_error_message("user_id の取得に失敗しました", e)) from e


def refresh_long_lived_token(access_token: str, *, timeout: float = 10.0) -> dict:
    url = f"{_graph_base_url()}/refresh_access_token"
    params = {"grant_type": "th_refresh_token", "access_token": access_token}
    try:
        return _get_json(url, params, timeout=timeout)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
        raise OAuthError(_error_message("トークンの更新に失敗しました", e)) from e


def run_auth(account_name: str, *, redirect_uri: str | None = None, code: str | None = None,
             input_func=input, log=print) -> int:
    """`thth auth <account>`。masaru との対話 1 往復（URL 表示 → code 入力）＋
    短期→長期トークン交換 →`me` → `.token` 書き込み。トークン等は一切出力しない。
    """
    try:
        account_cfg = accounts_mod.load_account(account_name)
    except accounts_mod.AccountError as e:
        _out(str(e), log=log)
        return 2

    try:
        app_id, app_secret = appenv.load_app_env(log=lambda line: _out(line, log=log))
    except appenv.AppEnvError as e:
        _out(str(e), log=log)
        return 2

    redirect_uri = redirect_uri or account_cfg.get("redirect_uri")
    if not redirect_uri:
        _out(f"redirect_uri が accounts/{account_name}.json に無い（masaru が設計 §9 の値を足す）", log=log)
        return 2

    scope_list = account_cfg.get("scopes") or scopes_mod.DEFAULT_SCOPES

    url = build_authorize_url(app_id, redirect_uri, scope_list)
    _out("次の URL をブラウザで開いて認可してください:", log=log)
    _out(url, log=log)
    _out("承認後の戻り URL に付く code を貼ってください"
         "（そのまま貼ってよい。#_ が付いていても、URL 全体でも構いません）:", log=log)

    raw = code if code is not None else input_func()
    code_value = extract_code(raw)
    if not code_value:
        _out("code が読み取れませんでした", log=log)
        return 2

    try:
        short = exchange_short_lived_token(app_id, app_secret, redirect_uri, code_value)
    except OAuthError as e:
        _out(str(e), log=log)
        return 1
    short_token = short.get("access_token")
    if not short_token:
        _out("短期トークンの取得に失敗しました（応答に access_token が無い）", log=log)
        return 1

    try:
        long_ = exchange_long_lived_token(app_secret, short_token)
    except OAuthError as e:
        _out(str(e), log=log)
        return 1
    long_token = long_.get("access_token")
    if not long_token:
        _out("長期トークンの交換に失敗しました（応答に access_token が無い）", log=log)
        return 1
    expires_in = long_.get("expires_in", DEFAULT_TOKEN_LIFETIME_SECONDS)

    try:
        me = fetch_me(long_token)
    except OAuthError as e:
        _out(str(e), log=log)
        return 1
    user_id = me.get("id", "")
    username = me.get("username", "")

    token_path = account_cfg["token"]
    token_data = {
        "access_token": long_token,
        "obtained_at": jst.iso(),
        "expires_in": expires_in,
        "user_id": user_id,
        "username": username,
        "scopes": scope_list,
    }
    secrets_fs.atomic_write_json(token_path, token_data, mode=0o600)

    _out(f"user_id={user_id} username={username}", log=log)
    _out(f"保存しました: {token_path}（600）", log=log)
    return 0


def _parse_obtained_at(token: dict):
    import datetime
    raw = token.get("obtained_at")
    if not raw:
        raise OAuthError("token に obtained_at が無い")
    return datetime.datetime.fromisoformat(raw)


def token_age_and_remaining(token: dict, now):
    """`(経過秒, 残り日数)` を返す。`thth maintain` からも使う（公開の口）。"""
    obtained_at = _parse_obtained_at(token)
    age_seconds = (now - obtained_at).total_seconds()
    expires_in = token.get("expires_in") or DEFAULT_TOKEN_LIFETIME_SECONDS
    remaining_days = (expires_in - age_seconds) / 86400.0
    return age_seconds, remaining_days


def run_refresh(account_name: str, *, force: bool = False, check: bool = False,
                 log=print, now=None) -> int:
    """`thth refresh <account>`。50 日超で更新（`--force` で無条件）。24 時間未満は
    公式の条件（設計 §2.2）により常に拒否する。`--check` は更新せず JSON で状態を返す。
    """
    now = now if now is not None else jst.now_jst()
    try:
        account_cfg = accounts_mod.load_account(account_name)
    except accounts_mod.AccountError as e:
        _out(str(e), log=log)
        return 2

    token = accounts_mod.load_token(account_cfg)
    if token is None:
        _out(f"token が無い: {account_name}（先に thth auth を実行してください）", log=log)
        return 2

    secrets_fs.ensure_mode_600(account_cfg["token"], log=lambda line: _out(line, log=log))

    try:
        age_seconds, remaining_days = token_age_and_remaining(token, now)
    except OAuthError as e:
        _out(str(e), log=log)
        return 1

    age_hours = age_seconds / 3600.0
    age_days = age_seconds / 86400.0
    needs_refresh = age_days > REFRESH_AFTER_DAYS
    can_refresh = age_hours >= MIN_REFRESH_AGE_HOURS

    if check:
        payload = {
            "account": account_name,
            "obtained_at": token.get("obtained_at"),
            "age_days": round(age_days, 2),
            "remaining_days": round(remaining_days, 2),
            "needs_refresh": needs_refresh,
            "can_refresh": can_refresh,
        }
        _out(json.dumps(payload, ensure_ascii=False), log=log)
        return 0

    # 公式の条件（設計 §2.2）: 24 時間未満は更新できない。--force でも覆さない
    # （Meta 側の実際の制約なので、無理に叩いても失敗するだけ）。
    if not can_refresh:
        _out(f"まだ更新できません（{account_name}: 取得から {age_hours:.1f} 時間・24 時間以上必要）", log=log)
        return 0

    if not force and not needs_refresh:
        _out(f"まだ更新の必要がありません（{account_name}: 取得から {age_days:.1f} 日・50 日超で更新）", log=log)
        return 0

    try:
        resp = refresh_long_lived_token(token["access_token"])
    except OAuthError as e:
        _out(str(e), log=log)
        return 1
    new_token = resp.get("access_token")
    if not new_token:
        _out("更新に失敗しました（応答に access_token が無い）", log=log)
        return 1

    updated = dict(token)
    updated["access_token"] = new_token
    updated["obtained_at"] = jst.iso(now)
    updated["expires_in"] = resp.get("expires_in", token.get("expires_in", DEFAULT_TOKEN_LIFETIME_SECONDS))
    secrets_fs.atomic_write_json(account_cfg["token"], updated, mode=0o600)

    _out(f"更新しました: {account_name}", log=log)
    return 0


def _read_pasted_token(*, stdin: bool, input_func) -> str:
    """トークンを読む。エコーしない。

    `input_func` が渡されていればそれを使う（テスト・CLI からの注入用、`run_auth`
    の `code` 引数と同じ思想）。無ければ実際の入力元から読む:
      - `--stdin` 指定時、または標準入力が端末でない（パイプ）とき: 黙って 1 行読む
        （端末ではないのでどのみち画面には出ない）。
      - 標準入力が端末のとき: `getpass.getpass()` で表示せずに読む。
    """
    if input_func is not None:
        return input_func()
    if stdin or not sys.stdin.isatty():
        return sys.stdin.readline()
    return getpass.getpass("Threads の長期アクセストークンを貼り付けてください（表示されません）: ")


def run_token_set(account_name: str, *, force: bool = False, stdin: bool = False,
                   input_func=None, log=print) -> int:
    """`thth token set <account>`（T2b・masaru の指示 2026-09-09）。

    Meta 管理画面の「ユーザートークン生成ツール」で発行した Threads テスターの
    長期アクセストークンを、`thth auth` の OAuth 往復を経ずに直接受け取って
    `.token` に保存する。`me` で実在確認できたときだけ書く。トークンは一切
    出力しない（標準出力・ログ・例外文すべて `redact()` を経由する）。

    `.token` の中身は `thth auth` と同じ形にするが、次の 2 点は正直に劣る:
      - `obtained_at` は「管理画面でトークンを発行した時刻」ではなく「この
        コマンドを打った時刻」になる（管理画面はいつ発行したかを返さない）。
        `thth refresh` は 50 日超で更新するだけなので、数日ずれても実害は無い。
      - `scopes` は管理画面発行では分からないので `null`（嘘の一覧は書かない）。
      - `expires_in` は管理画面の応答に無いので、長期トークンの既定寿命
        （`DEFAULT_TOKEN_LIFETIME_SECONDS`＝60 日）を使う。
    """
    try:
        account_cfg = accounts_mod.load_account(account_name)
    except accounts_mod.AccountError as e:
        _out(str(e), log=log)
        return 2

    token_path = account_cfg["token"]
    if os.path.exists(token_path) and not force:
        _out(f"既にあります: {token_path}。上書きするなら --force", log=log)
        return 1

    raw = _read_pasted_token(stdin=stdin, input_func=input_func)
    token_value = extract_token(raw)
    if not token_value:
        _out("トークンが読み取れませんでした", log=log)
        return 2

    try:
        me = fetch_me(token_value)
    except OAuthError as e:
        _out(f"トークンが使えませんでした（{e}）", log=log)
        return 1
    user_id = me.get("id", "")
    username = me.get("username", "")
    if not user_id:
        _out("トークンが使えませんでした（me の応答に id が無い）", log=log)
        return 1

    # 取り違え防止（masaru の指摘 2026-09-09）。台帳の handle と、トークンが
    # 実際に指しているアカウントが食い違ったら保存しない。
    #
    # Meta 側にも「選択中のテスタープロフィールと一致しません」という検査があるが、
    # それが見ているのは「管理画面で押した行」と「ブラウザでログイン中のアカウント」の
    # 一致だけ。**正しく発行したトークンを、別のアカウントの枠に貼る**取り違えは
    # 見てくれない（nigamilab のトークンを kopicha-threads に入れる等）。そこを塞ぐ。
    #
    # 通してしまうと、そのアカウントの queue の本文が別のアカウントから出る。
    # 取り消せない公開行為なので、疑わしければ保存しない（--force でも覆さない）。
    handle = (account_cfg.get("handle") or "").strip()
    if handle and username and handle.lower() != username.lower():
        _out(f"保存しませんでした: 台帳 {account_name} の handle は {handle} ですが、"
             f"このトークンは {username} のものです。", log=log)
        _out("正しいアカウントで発行し直すか、台帳の handle を直してください。", log=log)
        return 1

    token_data = {
        "access_token": token_value,
        "obtained_at": jst.iso(),
        "expires_in": DEFAULT_TOKEN_LIFETIME_SECONDS,
        "user_id": user_id,
        "username": username,
        "scopes": None,
    }
    secrets_fs.atomic_write_json(token_path, token_data, mode=0o600)

    _out(f"user_id={user_id} username={username}", log=log)
    _out(f"保存しました: {token_path}（600）", log=log)
    return 0
