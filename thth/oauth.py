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

from . import admin_log

import getpass
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request

from . import accounts as accounts_mod
from . import adapters as adapters_mod
from . import appenv
from . import httpsafe
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


def _auth_graph_base_url() -> str:
    """Only the official token origin or an explicit loopback fake server."""
    raw = _graph_base_url()
    try:
        parsed = urllib.parse.urlsplit(raw)
        valid = (not any(ord(c) < 32 or ord(c) == 127 for c in raw)
                 and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
                 and parsed.path in ('', '/')
                 and ((parsed.scheme == 'https' and parsed.hostname == 'graph.threads.net' and parsed.port in (None, 443))
                      or (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost', '::1'))))
    except ValueError:
        valid = False
    if not valid:
        raise OAuthError('auth_token_endpoint_invalid: 公式HTTPSかloopback試験先だけを使えます')
    return raw


def _authorize_base_url() -> str:
    return os.environ.get("THTH_THREADS_AUTH_BASE_URL", AUTHORIZE_BASE_URL_DEFAULT).rstrip("/")


def build_authorize_url(app_id: str, redirect_uri: str, scopes: list,
                         state: str | None = None) -> str:
    """認可 URL を組む。**`state` を必ず載せる**（セキュリティ監査 2026-09-14・P2-4）。

    `state` が無いと、**この道具が出した URL の戻りかどうかを確かめる術が無い**。
    別のところで作られた `code`（攻撃者のアプリの認可コード・別アカウントの
    戻り）を貼られても、そのまま交換して `.token` に書いていた。
    """
    params = {
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "scope": ",".join(scopes),
        "response_type": "code",
    }
    if state:
        params["state"] = state
    return f"{_authorize_base_url()}/oauth/authorize?" + urllib.parse.urlencode(params)


def _new_state() -> str:
    """推測できない `state`（テストはここを差し替える）。"""
    return secrets.token_urlsafe(32)


def _auth_state_path(account_name: str) -> str:
    return os.path.join(accounts_mod.state_dir_for(account_name), "auth_state.json")


def _save_auth_state(account_name: str, state: str) -> None:
    """出した URL の `state` を残す（**次の実行が戻りを照合できるように**）。

    `thth auth <account> --code …` は**別の実行**なので、その場で作った `state`
    とは照合できない（URL を出したのは前回の実行）。認可 URL を出すたびに
    600 で残し、照合できたら消す。
    """
    try:
        secrets_fs.atomic_write_json(
            _auth_state_path(account_name),
            {"state": state, "created_at": jst.iso()}, mode=0o600)
    except OSError:
        pass                       # 残せなくても認可そのものは続けられる


# **残した `state` の有効期限**（監査 2 回目・P3-5）。認可の URL を出してから
# 戻り URL を貼るまでの時間で、10 分あれば足りる（ブラウザで承認するだけ）。
#
# 期限が無いと、**去年出して貼らなかった `state` が今日の戻りを通してしまう**
# ——`auth_state.json` は照合に成功したときしか消えないので、途中でやめた認可の
# `state` は残り続ける。攻撃者がその 1 本を握れば、いつでも使える鍵になる。
AUTH_STATE_TTL_SECONDS = 600


def _saved_auth_state(account_name: str):
    """前回の実行が残した `state`（無い・読めない・**古い**なら None）。"""
    try:
        with open(_auth_state_path(account_name), encoding="utf-8") as f:
            data = json.load(f) or {}
    except (OSError, ValueError):
        return None
    created = jst.parse(data.get("created_at"))
    if created is None:
        # **いつ出したか判らないものを通さない**（規約 12・fail-closed）。
        return None
    if (jst.now_jst() - created).total_seconds() > AUTH_STATE_TTL_SECONDS:
        return None
    return data.get("state")


def _clear_auth_state(account_name: str) -> None:
    try:
        os.remove(_auth_state_path(account_name))
    except OSError:
        pass


def extract_state(raw: str):
    """貼られた戻り URL から `state` を取り出す（無ければ None）。

    `extract_code()` と同じ揺れに耐える（末尾の `#_`・URL 全体・前後の空白）。

    **鍵の照合は `parse_qs` に任せる**（監査 2 回目・P3-4）。前は
    `text.split("state=", 1)` で切っていたので、**`state` で終わる別の鍵**を
    `state` として読んでいた——`?code=X&my_state=攻撃者の値` の `my_state` が
    当たる（`"state=" in text` も通る）。照合する側は「出した `state` と同じか」を
    見るだけなので、**間違った鍵を拾えば照合は必ず外れる**か、悪いときには
    攻撃者が仕込んだ値で通る。鍵の名前は厳密に見る。
    """
    text = (raw or "").strip()
    for sep in ("#_", "#"):
        idx = text.find(sep)
        if idx != -1:
            text = text[:idx]
            break
    query = text.split("?", 1)[1] if "?" in text else text
    # `keep_blank_values=False` なので `state=` だけの空値は拾わない（＝無い）。
    values = urllib.parse.parse_qs(query).get("state") or []
    for value in values:
        value = (value or "").strip()
        if value:
            return value
    return None


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
    with httpsafe.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        return json.loads(body) if body else {}


def _get_json(url: str, params: dict, *, timeout: float) -> dict:
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{qs}", method="GET")
    with httpsafe.urlopen(req, timeout=timeout) as resp:
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
    # **値そのものを登録**（セキュリティ監査 2026-09-16・P1-1）。サーバがキー名
    # なしで値を反射しても `redact()` が消せるようにする。
    redact_mod.register_secret(app_secret)
    redact_mod.register_secret(code)
    url = f"{_auth_graph_base_url()}/oauth/access_token"
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
    redact_mod.register_secret(app_secret)
    redact_mod.register_secret(access_token)
    url = f"{_auth_graph_base_url()}/access_token"
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
    redact_mod.register_secret(access_token)
    url = f"{_auth_graph_base_url()}/v1.0/me"
    params = {"fields": "id,username", "access_token": access_token}
    try:
        return _get_json(url, params, timeout=timeout)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
        raise OAuthError(_error_message("user_id の取得に失敗しました", e)) from e


# **認可の範囲（scope）は長期トークン交換の応答には入っていない**（**L2**
# https://developers.facebook.com/docs/threads/get-started/long-lived-tokens
# ——`GET /access_token` の応答は `access_token`・`token_type`・`expires_in` の
# 3 つだけ）。トークンに実際に乗っている権限を API に訊く口は
# `GET /v1.0/debug_token?access_token=<tester のユーザートークン>&input_token=<同じ>`
# で、応答の `data.scopes` が一覧（**L2**
# https://developers.facebook.com/docs/threads/troubleshooting/debug-access-token
# ——「access_token には app access token か、Threads tester のユーザートークン」）。
#
# 運用の観測（2026-09-14）: VM の `.token` は 5 本とも `scopes` が null で、
# **認可の範囲がどこにも記録されていなかった**。`thth auth` はこれを訊いて書き、
# **どちらを書いたかを `scopes_source` に残す**（`"response"`＝`/debug_token` が
# 言った・`"requested"`＝訊けなかったので要求した一覧・`"unknown"`＝管理画面
# 発行で判らない）。推測で埋めない。
SCOPES_SOURCE_RESPONSE = "response"
SCOPES_SOURCE_REQUESTED = "requested"
SCOPES_SOURCE_UNKNOWN = "unknown"


def fetch_token_scopes(access_token: str, *, timeout: float = 10.0):
    """`/debug_token` に**そのトークン自身**を訊いて `data.scopes` を返す。

    読み取りだけ。**訊けなければ None**（例外にしない——scope の記録は認可の
    成否を左右しないので、`thth auth` は要求した一覧に落とす）。応答に値が
    無い・形が違うときも None（嘘の一覧を作らない）。
    """
    redact_mod.register_secret(access_token)
    url = f"{_auth_graph_base_url()}/v1.0/debug_token"
    params = {"access_token": access_token, "input_token": access_token}
    try:
        body = _get_json(url, params, timeout=timeout)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError,
            ValueError):
        return None
    data = body.get("data") if isinstance(body, dict) else None
    scopes = data.get("scopes") if isinstance(data, dict) else None
    if not isinstance(scopes, list) or not all(isinstance(x, str) for x in scopes):
        return None
    return list(scopes)


def refresh_long_lived_token(access_token: str, *, timeout: float = 10.0) -> dict:
    # **Threads 専用**（宛先は `graph.threads.net`）。ほかの媒体の `access_token`
    # を渡してはいけない——Mastodon の `.token` も鍵が `access_token` なので、
    # 媒体を見ずに呼ぶと**秘密が Meta のサーバへ出る**。唯一の呼び手
    # （`run_refresh()`）が入口で `refresh` の能力を見て塞いでいる（P1-1）。
    url = f"{_graph_base_url()}/refresh_access_token"
    params = {"grant_type": "th_refresh_token", "access_token": access_token}
    try:
        return _get_json(url, params, timeout=timeout)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
        raise OAuthError(_error_message("トークンの更新に失敗しました", e)) from e


def run_auth(account_name: str, *, redirect_uri: str | None = None, code: str | None = None,
             input_func=None, identifier_input=None, password_input=None,
             log=print, by=None, rehearse=False, human_output=print) -> int:
    """`thth auth <account>`。**媒体で分ける**（T3 の配線 2026-09-13）。

    以前はここが Threads 固有（OAuth の往復）だった——設計 v2 §4.2 が挙げた
    「Threads 固有になっている 6 箇所」の 1 つ。台帳の `media` で分ける:

      - `threads`  現行のまま（URL 表示 → code 入力 → 短期→長期交換 → `me`）。
      - `bluesky`  `bluesky.auth_interactive()` が handle と App Password を
                   受け取り、返った dict を `.token` に 600 で原子的に書く。
      - `mastodon` 認可はインスタンスの管理画面で行うので、ここでは受けない。
                   `thth token set` へ案内して rc=2（**黙って何もしない終わり方を
                   しない**）。
      - それ以外   知っている媒体の一覧を添えて loud に断る（T-B0）。
    """
    from . import admin_log
    try:
        admin_log.actor(by)
    except ValueError as exc:
        _out(str(exc), log=log)
        return 2
    try:
        account_cfg = accounts_mod.load_account(account_name)
    except accounts_mod.AccountError as e:
        _out(str(e), log=log)
        return 2

    if rehearse:
        from . import authflow
        return authflow.rehearse(account_cfg, redirect_uri=redirect_uri, log=log, human_output=human_output)

    media = account_cfg.get("media")
    if media == "mastodon":
        from . import authflow
        from .adapters.auth_mastodon import MastodonAuthProfile
        try:
            profile = MastodonAuthProfile.prepare(account_cfg, redirect_uri=redirect_uri, resume=code is not None)
        except (OSError, ValueError) as exc:
            detail = str(exc) if isinstance(exc, authflow.FlowError) else "client設定を確認してください"
            _out("mastodon_auth_setup_failed: " + detail, log=log)
            return 2
        return authflow.run(account_name, account_cfg, profile, code=code, input_func=input_func,
                            log=log, by=by, human_output=human_output)
    if media != "threads":
        from . import adapters as adapters_mod
        try:
            adapters_mod.adapter_class(media)
        except adapters_mod.UnknownMedium as e:
            _out(str(e), log=log)
            return 2
        if media == "bluesky":
            return admin_log.guarded(run_auth_bluesky)(
                account_name, account_cfg=account_cfg, log=log,
                identifier_input=identifier_input, password_input=password_input, by=by)
        _out(f"{media} は `thth auth` では認可できません。"
             f"インスタンスの管理画面（設定 → 開発 → 新規アプリ）で access token を"
             f"作って、`thth token set {account_name}` で貼り付けてください。", log=log)
        return 2

    try:
        app_id, app_secret = appenv.load_app_env(log=lambda line: _out(line, log=log))
    except appenv.AppEnvError as e:
        _out(str(e), log=log)
        return 2
    # **app secret を読んだ直後に登録する**（セキュリティ監査 2026-09-16・P1-1）。
    # `run_auth` はこのあと `exchange_short_lived_token()` 等も呼ぶので二重に
    # 登録されるが、`register_secret()` は重複を無視する。
    redact_mod.register_secret(app_secret)

    redirect_uri = redirect_uri or account_cfg.get("redirect_uri")
    if not redirect_uri:
        _out(f"redirect_uri が accounts/{account_name}.json に無い（運用者が設計 §9 の値を足す）", log=log)
        return 2

    # **ダミーのまま認可 URL を出さない**（監査 2・C10・masaru 裁定 2026-09-13）。
    #
    # `thth account add` が写す雛形の `redirect_uri` は `https://example.invalid/`
    # ——**存在しないホスト**。前はこの値で認可 URL を組んで表示していたので、
    # 打った人はブラウザで開き、Meta に「redirect_uri が登録と違う」と断られて
    # 初めて詰まった（しかも画面に出るのは Meta 側の英語のエラー）。**道具は
    # 開く前に知っている**のだから、URL を出す前に言う。
    #
    # 判定は `accounts.redirect_uri_is_dummy()`（`thth doctor` が名指しするのと
    # 同じ 1 か所の知識）。`--redirect-uri` で本物を渡した分にはここを通らない。
    if accounts_mod.redirect_uri_is_dummy(redirect_uri):
        _out(f"**redirect_uri がダミーです**（{redirect_uri}）。認可 URL は出しません。",
             log=log)
        _out(f"Meta アプリに登録した URL を "
             f"`thth account add {account_name} --redirect-uri <url>` か、"
             f"台帳（{os.path.join(accounts_mod.accounts_dir(), account_name + '.json')}）の "
             f"`redirect_uri` に入れてから、もう一度打ってください。", log=log)
        return 2

    scope_list = account_cfg.get("scopes") or scopes_mod.DEFAULT_SCOPES

    from . import authflow
    from .adapters.auth_threads import ThreadsAuthProfile
    profile = ThreadsAuthProfile(app_id, app_secret, redirect_uri, list(scope_list))
    return authflow.run(account_name, account_cfg, profile, code=code,
                        input_func=input_func, log=log, by=by, human_output=human_output)


def _parse_obtained_at(token: dict):
    import datetime
    raw = token.get("obtained_at")
    if not raw:
        raise OAuthError("token に obtained_at が無い")
    return datetime.datetime.fromisoformat(raw)


def token_age_and_remaining(token: dict, now):
    """`(経過秒, 残り日数)` を返す。`thth maintain` からも使う（公開の口）。

    **「判らない」と「期限を持たない」を混ぜない**（設計 v2 §4.2「認可と
    トークン」）。Bluesky の App Password と Mastodon の access token には
    期限が無い。`expires_in` が無いだけなら Threads の既定寿命（60 日）を
    当てるが、**`no_expiry: true` が立っていれば `remaining_days` は `None`**
    ——`None` はここでは「期限を持たない」の意味で、呼び出し側
    （`maintain.inspect()`）が `token_state: ok` と言い分ける。

    `no_expiry` と `expires_in` の両方があるトークンは**`expires_in` を採る**
    ——期限が書いてあるものを「期限が無い」とは言わない。
    """
    obtained_at = _parse_obtained_at(token)
    age_seconds = (now - obtained_at).total_seconds()
    if not token.get("expires_in") and token.get("no_expiry") is True:
        return age_seconds, None
    expires_in = token.get("expires_in") or DEFAULT_TOKEN_LIFETIME_SECONDS
    remaining_days = (expires_in - age_seconds) / 86400.0
    return age_seconds, remaining_days


@admin_log.guarded
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

    # **更新の口を持たない媒体では、`.token` を 1 バイトも読まないうちに断る**
    # （独立監査 1・P1-1・2026-09-13）。下の `refresh_long_lived_token()` は
    # `graph.threads.net` を直に叩き、`access_token` をクエリに載せる。Mastodon
    # の `.token` も鍵が `access_token` なので、**媒体を見ずに通すと Mastodon の
    # access token が Meta のサーバへ飛び、返ってきた Threads のトークンで
    # `.token` が上書きされる**。歯止めが `.token` の中の `no_expiry` という
    # **データの印だけ**だったのが穴で、導入文書が示す手置きの形にその印は無い。
    # 止め方は `account_report.fetch_posts()` と同じ——**能力で塞ぐ**（媒体名で
    # 分岐しない）。`--force` でも `--check` でも覆らない。
    media = account_cfg.get("media")
    if "refresh" not in adapters_mod.capabilities_for(media):
        _out(f"{account_name}（media={media}）のトークンに更新の口はありません"
             f"（更新できるのは refresh を持つ媒体だけ）。何もしませんでした",
             log=log)
        return 0

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
    # **期限を持たないトークンは更新しない**（設計 v2 §4.2）。Bluesky の App
    # Password・Mastodon の access token には更新の口が無い。**「まだ更新でき
    # ません」でも「更新が必要です」でもなく、「期限を持たない」と言う。**
    no_expiry = remaining_days is None
    needs_refresh = (not no_expiry) and age_days > REFRESH_AFTER_DAYS
    can_refresh = age_hours >= MIN_REFRESH_AGE_HOURS

    if check:
        payload = {
            "account": account_name,
            "obtained_at": token.get("obtained_at"),
            "age_days": round(age_days, 2),
            "remaining_days": None if no_expiry else round(remaining_days, 2),
            "no_expiry": no_expiry,
            "needs_refresh": needs_refresh,
            "can_refresh": can_refresh,
        }
        _out(json.dumps(payload, ensure_ascii=False), log=log)
        return 0

    if no_expiry:
        # `--force` でも覆さない（媒体側に更新の口が無いので叩いても失敗する
        # だけ。Threads の「24 時間未満は更新できない」と同じ扱い）。
        _out(f"このトークンは期限を持ちません（{account_name}: 更新は不要です）", log=log)
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
    from . import admin_log
    admin_log.append("token_refreshed", account_name, account_cfg, by="thth-refresh", diff={"token": ["present", "present"]})

    _out(f"更新しました: {account_name}", log=log)
    return 0


def _ask_bluesky(prompt: str, *, secret: bool):
    """端末から 1 つ受け取る。**端末でなければ断る**（`thth app set` と同じ作法）。

    tty を割り当てずに `ssh wt 'thth auth ...'` と打つと、**手元の画面に App
    Password がそのまま出る**（remote に tty が無いのでエコーを止められない）。
    秘密を画面に出す経路を黙って通さない。
    """
    if not sys.stdin.isatty():
        raise OAuthError(
            "標準入力が端末ではありません。App Password が画面に出てしまうので"
            "読みません。\n"
            "  ssh に -t を付けてください"
            "（例: ssh -t wt '...thth auth <account>'）")
    return getpass.getpass(prompt) if secret else input(prompt)


@admin_log.guarded
def run_auth_bluesky(account_name: str, *, account_cfg=None,
                     identifier_input=None, password_input=None, log=print, by=None) -> int:
    """`thth auth <account>`（Bluesky・設計 v2 §4.2「認可とトークン」）。

    handle と **App Password** を対話で受け（`getpass` なので画面に出ない）、
    `createSession` が通ったものだけを `~/.config/thth/<account>.token` に
    **600 で原子的に**書く（`thth/secrets_fs.py` の作法・`thth app set` と同じ）。

    **値はどこにも出さない**——標準出力・ログ・例外文のどれにも。成功時に言うのは
    handle と did と path と 600 だけ。

    書く中身は `bluesky.auth_interactive()` の戻り（`identifier`・`app_password`・
    `did`・`handle`・`no_expiry: true`・`obtained_at`）に、取り違え防止の
    `user_id`・`username` を足したもの。**`expires_in` は書かない**——App Password
    に期限は無い（`maintain` が「判らない」ではなく「期限を持たない」と言う）。
    """
    from . import admin_log
    try:
        admin_log.actor(by)
    except ValueError as exc:
        _out(str(exc), log=log)
        return 2
    from .adapters import bluesky as bluesky_mod

    if account_cfg is None:
        try:
            account_cfg = accounts_mod.load_account(account_name)
        except accounts_mod.AccountError as e:
            _out(str(e), log=log)
            return 2

    service = account_cfg.get("service") or bluesky_mod.DEFAULT_SERVICE
    ask_id = identifier_input or (
        lambda: _ask_bluesky(f"Bluesky の handle（例: name.bsky.social・{service}）: ",
                             secret=False))
    ask_pw = password_input or (
        lambda: _ask_bluesky("App Password（xxxx-xxxx-xxxx-xxxx・表示されません）: ",
                             secret=True))

    try:
        token_data = bluesky_mod.auth_interactive(ask_id, ask_pw, service=service)
    except OAuthError as e:
        _out(str(e), log=log)
        return 2
    except (ValueError, RuntimeError) as e:
        # `auth_interactive()` は既に `scrub()` を通した文だけを投げる。
        _out(f"認可できませんでした（{redact_mod.redact(str(e))}）", log=log)
        return 1

    # 取り違え防止（`token set` と同じ筋・masaru の指摘 2026-09-09）。台帳の
    # handle と、App Password が実際に指しているアカウントが食い違ったら
    # 保存しない。**通すと、そのアカウントの queue の本文が別のアカウントから出る。**
    handle = (account_cfg.get("handle") or "").strip().lstrip("@")
    got = (token_data.get("handle") or "").strip().lstrip("@")
    if handle and got and handle.lower() != got.lower():
        _out(f"保存しませんでした: 台帳 {account_name} の handle は {handle} ですが、"
             f"この App Password は {got} のものです。", log=log)
        _out("正しいアカウントで発行し直すか、台帳の handle を直してください。", log=log)
        return 1

    token_data = dict(token_data)
    # `whoami()` と同じ鍵（`board`・`doctor` がここを読む）。
    token_data["user_id"] = token_data.get("did")
    token_data["username"] = token_data.get("handle")
    token_was_present = os.path.exists(account_cfg["token"])
    secrets_fs.atomic_write_json(account_cfg["token"], token_data, mode=0o600)
    admin_log.append("token_set", account_name, account_cfg, by=by, diff={"token": ["present" if token_was_present else "absent", "present"]})

    _out(f"handle={token_data['handle']} did={token_data['did']}", log=log)
    _out(f"保存しました: {account_cfg['token']}（600）", log=log)
    return 0


# **媒体ごとの貼り付けの案内**（masaru 報告 2026-09-13: Mastodon なのに「Threads の長期
# アクセストークン」と聞かれた）。文言が違っても動きは同じだが、**別媒体の秘密を
# 貼らせる画面で媒体名を間違えるのは、道具が嘘をついている**のと同じ。
TOKEN_PASTE_PROMPTS = {
    "threads": "Threads の長期アクセストークンを貼り付けてください（表示されません）: ",
    "mastodon": "Mastodon のアクセストークン（設定 → 開発 → アプリ）を貼り付けてください（表示されません）: ",
}


def _paste_prompt(media) -> str:
    return TOKEN_PASTE_PROMPTS.get(media) or f"{media} のアクセストークンを貼り付けてください（表示されません）: "


def _read_pasted_token(*, stdin: bool, input_func, prompt: str | None = None) -> str:
    """トークンを読む。エコーしない。

    `input_func` が渡されていればそれを使う（テスト・CLI からの注入用、`run_auth`
    の `code` 引数と同じ思想）。無ければ実際の入力元から読む:
      - `--stdin` 指定時、または標準入力が端末でない（パイプ）とき: 黙って 1 行読む
        （端末ではないのでどのみち画面には出ない）。
      - 標準入力が端末のとき: `getpass.getpass()` で表示せずに読む。
    """
    if input_func is not None:
        return input_func()
    if stdin:
        return sys.stdin.readline()
    if not sys.stdin.isatty():
        # **黙って読まない**（masaru 指摘 2026-09-10「トークンの入替が出来ない」）。
        # 端末でないまま読むと、`ssh wt 'thth token set ...'` のように tty を割り
        # 当てずに実行したときに **手元の画面にトークンがそのまま表示される**
        # （remote 側に tty が無いのでエコーを止められない）。秘密を画面に出す
        # 経路を黙って通さない。どちらの意図なのかを利用者に選ばせる。
        raise OAuthError(
            "標準入力が端末ではありません。トークンが画面に出てしまうので読みません。\n"
            "  対話で入れる場合: ssh に -t を付けてください（例: ssh -t wt '...thth token set <account> --force'）\n"
            "  パイプ・ファイルから渡す場合: --stdin を付けてください")
    return getpass.getpass(prompt or TOKEN_PASTE_PROMPTS["threads"])


@admin_log.guarded
def run_token_set(account_name: str, *, force: bool = False, stdin: bool = False,
                   input_func=None, log=print, by=None) -> int:
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
        `scopes_source` に `"unknown"` を書いて、**判らないことを判った形で残す**
        （`thth auth` が書く `"response"` / `"requested"` と区別できる）。
      - `expires_in` は管理画面の応答に無いので、長期トークンの既定寿命
        （`DEFAULT_TOKEN_LIFETIME_SECONDS`＝60 日）を使う。

    **媒体で変わるのは 2 つだけ**（T3 の配線 2026-09-13）。本人の確認は
    アダプタの `whoami()`（既に境界の向こう）、期限の有無はクラス属性
    `TOKEN_NO_EXPIRY`。Mastodon は `no_expiry: true` を書いて `expires_in` を
    書かない。**貼り付けで入らない媒体（Bluesky）は loud に断る。**
    """
    from . import admin_log
    try:
        admin_log.actor(by)
    except ValueError as exc:
        _out(str(exc), log=log)
        return 2
    try:
        account_cfg = accounts_mod.load_account(account_name)
    except accounts_mod.AccountError as e:
        _out(str(e), log=log)
        return 2

    if account_cfg.get('media') == 'mastodon':
        from . import scopes
        print(scopes.mastodon_guidance(account_name), file=sys.stderr)

    token_path = account_cfg["token"]
    if os.path.exists(token_path) and not force:
        _out(f"既に token があります（{account_name}）。**入れ替える**なら --force を"
             f"付けてください: thth token set {account_name} --force", log=log)
        return 1

    try:
        raw = _read_pasted_token(stdin=stdin, input_func=input_func,
                                 prompt=_paste_prompt(account_cfg.get("media")))
    except OAuthError as e:
        _out(str(e), log=log)
        return 2
    token_value = extract_token(raw)
    if not token_value:
        _out("トークンが読み取れませんでした", log=log)
        return 2
    # **貼り付けられたトークンを登録する**（セキュリティ監査 2026-09-16・P1-1）。
    # 直後の `whoami()` がサーバの応答を反映した例外文を上げても、この値が
    # 綴りに関わらず伏字になる。
    redact_mod.register_secret(token_value)

    # **本人の確認はアダプタの `whoami()` を通す**（設計 v2 §4.2・裁定
    # 2026-09-13）。以前はここが Threads の `me` を直接叩いていた（Threads 固有に
    # なっている 6 箇所の 1 つ）。媒体ごとに確認の口は違う——Mastodon は
    # `/api/v1/accounts/verify_credentials`、Bluesky は `createSession` の応答
    # ——ので、**どこを叩くかは媒体の知識**として境界の向こうに置く。
    # Threads の挙動は変わらない（`whoami()` が `me` を包んでいるだけ）。
    from . import adapters as adapters_mod
    try:
        adapter_cls = adapters_mod.adapter_class(account_cfg.get("media"))
    except adapters_mod.UnknownMedium as e:
        _out(str(e), log=log)
        return 2
    if "access_token" not in adapter_cls.TOKEN_KEYS:
        # **貼り付けで入らない媒体に、貼り付けを勧めない**（Bluesky の App
        # Password は `thth auth` が対話で受ける・T3 の配線 2026-09-13）。
        _out(f"{account_cfg.get('media')} は `thth token set` では入りません"
             f"（この媒体の `.token` の鍵は "
             f"{'・'.join(adapter_cls.TOKEN_KEYS)}）。"
             f"`thth auth {account_name}` を使ってください。", log=log)
        return 2
    try:
        adapter = adapters_mod.make_adapter(account_cfg, {"access_token": token_value})
        me = adapter.whoami()
    except adapters_mod.base.AdapterError as e:
        _out(f"トークンが使えませんでした（{redact_mod.redact(str(e))}）", log=log)
        return 1
    user_id = me.get("user_id", "")
    username = me.get("username", "")
    # **本人確認ができなければ保存しない**（セキュリティ監査 2026-09-16・
    # P2-1）。`user_id` だけを見ていたので、`username` が空でも次の取り違え
    # 防止（`if handle and username and ...`）を素通りして保存していた。
    if not user_id or not username:
        _out("トークンが使えませんでした（me の応答に id か username が無い）", log=log)
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
    if handle_matches(handle, username) is False:
        _out(f"保存しませんでした: 台帳 {account_name} の handle は {handle} ですが、"
             f"このトークンは {username} のものです。", log=log)
        _out("正しいアカウントで発行し直すか、台帳の handle を直してください。", log=log)
        return 1

    token_data = {
        "access_token": token_value,
        "obtained_at": jst.iso(),
        "user_id": user_id,
        "username": username,
        "scopes": None,
        "scopes_source": SCOPES_SOURCE_UNKNOWN,
    }
    # **期限の有無は媒体の知識**（`TOKEN_NO_EXPIRY`・T3 の配線 2026-09-13）。
    # Threads の長期トークンは 60 日で切れるので、管理画面が発行時刻を返さない
    # ぶんを既定寿命で埋める。Mastodon の access token に期限は無いので、
    # **`expires_in` を書かず `no_expiry: true` を立てる**——書いてしまうと
    # `maintain` が 60 日後に「まもなく切れます」と嘘の督促を出し、`thth refresh`
    # が更新できないまま毎日 rc=1 で鳴り続ける。「判らない」ではなく
    # 「期限を持たない」（設計 v2 §4.2）。
    if adapter_cls.TOKEN_NO_EXPIRY:
        token_data["no_expiry"] = True
    else:
        token_data["expires_in"] = DEFAULT_TOKEN_LIFETIME_SECONDS
    token_was_present = os.path.exists(token_path)
    secrets_fs.atomic_write_json(token_path, token_data, mode=0o600)
    admin_log.append("token_set", account_name, account_cfg, by=by, diff={"token": ["present" if token_was_present else "absent", "present"]})

    _out(f"user_id={user_id} username={username}", log=log)
    _out(f"保存しました: {token_path}（600）", log=log)
    return 0


@admin_log.guarded
def run_token_revoke(account_name, *, by=None, log=print):
    """Remove the local credential only; no remote revocation API is called."""
    from . import admin_log
    try:
        admin_log.actor(by)
        cfg = accounts_mod.load_account(account_name)
        path = cfg['token']
        if not path or not os.path.isfile(path) or os.path.islink(path):
            raise ValueError('token_unavailable')
        os.unlink(path)
        admin_log.append('token_revoked', account_name, cfg, by=by,
                         diff={'token': ['present', 'absent']})
    except (OSError, ValueError, accounts_mod.AccountError):
        _out('token revoke requires --by and a readable local token', log=log)
        return 2
    return 0


def handle_matches(handle, username, *, media=None):
    """The same identity comparison used before saving an authenticated token."""
    if not isinstance(handle, str) or not isinstance(username, str) or not handle or not username:
        return None
    if media == 'bluesky':
        return handle.strip().lstrip('@').lower() == username.strip().lstrip('@').lower()
    return handle.strip().lower() == username.lower()
