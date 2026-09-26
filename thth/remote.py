"""遠くの道（設計 3.14.0 §2・§3.4）: 鍵で thth.me 経由で自分の口座を動かす。

`thth login` が鍵を `~/.config/thth/remote.json`（0600・親 0700・`{"url", "key"[, "account"]}`）に置き、
以後は手元の道と**同じ命令・同じ引数・同じ `--json` の形**で動く。

- 3.14.2 から `thth login` の既定はブラウザ式（`gh auth login` と同じ形・設計 3.14.2 §2）: 手元で code と
  poll_token を作り、ブラウザで口座名と口座の secret を入れて許可すると、thth.me が発行した鍵を 1 度だけ受け取る。
  入力が要らないので tty が無くても動く。
- 貼る道も残す: `--stdin`（標準入力の 1 行）・`--paste`（tty で `getpass`）。**引数では受けない**（shell の履歴に残る）。
- 鍵は出力・ログ・例外の文に出さない（読んだらすぐ `redact.register_secret`）。
- 置き場は `THTH_REMOTE_CONFIG` で差し替えられる（試験用）。
"""
from __future__ import annotations

import datetime
import getpass
import hashlib
import http.client
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.request

from . import redact, secrets_fs

DEFAULT_URL = "https://thth.me"
# 段 2 の bearer（Worker の `BEARER`）は印字できる ASCII の 16〜512 字。発行する鍵は
# URL 安全な base64（`opaque()`）なので、その字だけを受ける（空白・引用符の混入を断る）。
KEY = re.compile(r"[A-Za-z0-9_-]{16,512}\Z")


class RemoteError(Exception):
    """遠くの道の断り。`code` は静的な符丁（1 語）。"""

    def __init__(self, code, *, reason=None, next_at=None, rc=1):
        super().__init__(code)
        self.code = code
        self.reason = reason
        self.next_at = next_at
        self.rc = rc


def config_path() -> str:
    return os.environ.get("THTH_REMOTE_CONFIG") or os.path.expanduser("~/.config/thth/remote.json")


def configured() -> bool:
    """`remote.json` が置かれているか（中身は見ない・壊れていれば使うときに断る）。"""
    return os.path.isfile(config_path())


def load():
    """`{"url", "key"}` を返す。無ければ None。壊れていれば `remote_config_invalid`。"""
    path = config_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise RemoteError("remote_config_invalid", rc=2) from None
    if type(data) is not dict or not isinstance(data.get("key"), str) or not KEY.fullmatch(data["key"]):
        raise RemoteError("remote_config_invalid", rc=2)
    redact.register_secret(data["key"])
    from . import httpsafe
    try:
        url = httpsafe.validated_url(data.get("url") or DEFAULT_URL, base=True)
    except httpsafe.EndpointRejected:
        raise RemoteError("remote_config_invalid", rc=2) from None
    return {"url": url, "key": data["key"]}


# --------------------------------------------------------------------------
# thth login / thth logout
# --------------------------------------------------------------------------

LOGIN_NEXT = "鍵は https://thth.me/activity で発行します（`thth login` だけならブラウザで許可すれば貼らずに済みます）"
# ブラウザ式（設計 3.14.2 §2）。code は大文字と数字から紛らわしい字（0 O 1 I L）を除いた 8 字（表示は XXXX-XXXX）。
LOGIN_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
LOGIN_POLL_SECONDS = 2
LOGIN_MAX_SECONDS = 600
ACCOUNT_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}\Z")


def _fail(code, message=None) -> int:
    print(code + (f": {message}" if message else ""), file=sys.stderr)
    return 2


def cmd_login(args) -> int:
    if getattr(args, "key", None):
        # 値は読まずに捨てる（控えない・表示しない）。
        return _fail("key_in_argument", "鍵を引数で渡すと shell の履歴に残ります。"
                     "`thth login`（ブラウザで許可）か、`thth login --stdin` で入れてください")
    from . import httpsafe
    try:
        url = httpsafe.validated_url(args.url or DEFAULT_URL, base=True)
    except httpsafe.EndpointRejected:
        return _fail("invalid_url", "https:// で始まる URL を指定してください")
    if not args.stdin and not getattr(args, "paste", False):
        return _browser_login(url, open_browser=not getattr(args, "no_browser", False))
    if args.stdin:
        key = sys.stdin.readline().strip()
    else:
        if not sys.stdin.isatty():
            return _fail("tty_required", "端末から実行するか、`thth login`（ブラウザで許可）か "
                         "`thth login --stdin` で入れてください")
        try:
            key = getpass.getpass("thth.me の鍵（表示しません）: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("", file=sys.stderr)
            return _fail("login_cancelled")
    redact.register_secret(key)
    if not KEY.fullmatch(key):
        return _fail("invalid_key_format", "鍵の形ではありません（英数字と - _ の 16 字以上）。" + LOGIN_NEXT)
    path = config_path()
    try:
        secrets_fs.atomic_write_json(path, {"url": url, "key": key}, mode=0o600, dir_mode=0o700)
    except OSError as exc:
        return _fail("remote_config_unwritable", f"{path}（{exc.strerror or type(exc).__name__}）")
    print(f"保存しました: {path}（{url}）")
    print("確かめるには: thth account status <口座>")
    return 0


def _open_browser(page) -> bool:
    """既定のブラウザで開く。開けなくても（ssh の先・ブラウザが無い）断らない。"""
    try:
        import webbrowser
        return bool(webbrowser.open(page))
    except Exception:  # noqa: BLE001 — 開けなければ URL を出すだけ
        return False


def _login_exchange(method, target, headers, payload=None):
    """ブラウザ式の login の 1 回の HTTP。`(status, dict | None)`。網の失敗は `(None, None)`。"""
    from . import __version__, httpsafe
    headers = {"Accept": "application/json", "User-Agent": "thth/" + __version__, **headers}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    try:
        status, _headers, raw = _transport(method, target, headers, body, TIMEOUT)
    except (urllib.error.URLError, OSError, http.client.HTTPException, httpsafe.EndpointRejected, ValueError):
        return None, None
    try:
        value = json.loads(raw) if len(raw) <= RESPONSE_MAX else None
    except ValueError:
        value = None
    return status, value if type(value) is dict else None


def _sha256(text) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _browser_login(url, *, open_browser=True) -> int:
    """ブラウザ式の `thth login`（設計 3.14.2 §2）。鍵は受け取ったらすぐ伏せる対象に入れ、画面には出さない。"""
    raw = "".join(secrets.choice(LOGIN_ALPHABET) for _ in range(8))
    code = raw[:4] + "-" + raw[4:]
    poll_token = secrets.token_urlsafe(32)  # 43 字（URL を見た人が鍵を取れないように poll は別の token で守る）
    redact.register_secret(poll_token)
    status, value = _login_exchange("POST", url + "/api/v1/login/start", {},
                                    {"code_hash": _sha256(code), "poll_token_hash": _sha256(poll_token)})
    if status is None:
        return _fail("remote_unavailable", "thth.me に届きませんでした。網を確かめてもう一度"
                     "（鍵が手元にあれば `thth login --stdin` でも入れられます）")
    if status == 429:
        return _fail("rate_limited", "少し待ってもう一度")
    if status != 202 or value is None or not isinstance(value.get("url"), str):
        return _fail("login_unavailable", "thth.me がブラウザ式の login を受けませんでした"
                     "（鍵が手元にあれば `thth login --stdin` で入れられます）")
    page = url + "/login/" + code
    print(f"ブラウザで許可してください: {page}（10 分有効）", flush=True)
    if not open_browser or not _open_browser(page):
        print("（上の URL をブラウザで開いてください）", flush=True)
    else:
        print("（ブラウザが開かなければ上の URL を開いてください）", flush=True)
    print("待っています…（口座名と口座の secret を入れて「この機械に鍵を渡す」を押す）", flush=True)
    deadline = _clock() + LOGIN_MAX_SECONDS
    target = url + "/api/v1/login/poll/" + code
    try:
        while True:
            if _clock() >= deadline:
                return _fail("login_expired", "10 分で切れました。`thth login` をやり直してください")
            _sleep(LOGIN_POLL_SECONDS)
            status, value = _login_exchange("GET", target, {"Authorization": "Bearer " + poll_token})
            if status is None or status in (202, 429, 503):
                continue  # 待つ・網が一時的に切れた・回数制限: 期限まで見続ける
            if status == 200 and value is not None:
                break
            if status in (404, 410):
                return _fail("login_expired", "期限が切れたか、もう使われました。`thth login` をやり直してください")
            return _fail("login_failed", "thth.me が鍵を渡しませんでした。`thth login` をやり直してください")
    except KeyboardInterrupt:
        print("", file=sys.stderr)
        return _fail("login_cancelled")
    key, account = value.get("key"), value.get("account")
    if isinstance(key, str):
        redact.register_secret(key)
    if not isinstance(key, str) or not KEY.fullmatch(key) or not isinstance(account, str) \
            or not ACCOUNT_NAME.fullmatch(account):
        return _fail("login_failed", "thth.me の答えが鍵の形ではありません。`thth login` をやり直してください")
    path = config_path()
    try:
        secrets_fs.atomic_write_json(path, {"url": url, "key": key, "account": account}, mode=0o600, dir_mode=0o700)
    except OSError as exc:
        return _fail("remote_config_unwritable", f"{path}（{exc.strerror or type(exc).__name__}）")
    print(f"保存しました: {path}（{account}）")
    print(f"鍵はサーバが切り替えた時点（数十秒後）から使えます。確かめるには: thth account status {account}")
    return 0


def cmd_logout(args) -> int:
    path = config_path()
    try:
        os.unlink(path)
    except FileNotFoundError:
        print(f"ログインしていません（{path} はありません）")
        return 0
    except OSError as exc:
        return _fail("remote_config_unwritable", f"{path}（{exc.strerror or type(exc).__name__}）")
    print(f"消しました: {path}")
    return 0


# --------------------------------------------------------------------------
# HTTP（標準ライブラリだけ・`httpsafe` の作法: 同じ origin 以外へは追わない・https だけ）
# --------------------------------------------------------------------------

TIMEOUT = 25          # 1 回の HTTP（Worker は最長 20 秒保留する）
POLL_SECONDS = 1      # 202 のあと /result/ を見る間隔
POLL_MAX_SECONDS = 90
RETRY_AFTER_MAX = 60  # 429 の Retry-After をこれより長くは待たない（1 回だけやり直す）
RESPONSE_MAX = 262144
CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{43}\.[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")

_sleep = time.sleep
_clock = time.monotonic


def _transport(method, url, headers, body, timeout):
    """1 回の HTTP。`(status, headers, bytes)` を返す。網の失敗は `OSError` 系をそのまま上げる。"""
    from . import httpsafe
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with httpsafe.opener().open(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read(RESPONSE_MAX + 1)
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read(RESPONSE_MAX + 1)
        finally:
            exc.close()
        return exc.code, dict(exc.headers or {}), raw


def _exchange(cfg, method, path, payload=None):
    from . import __version__, httpsafe
    headers = {"Authorization": "Bearer " + cfg["key"], "Accept": "application/json",
               "User-Agent": "thth/" + __version__}
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    try:
        status, response_headers, raw = _transport(method, cfg["url"] + path, headers, body, TIMEOUT)
    except (urllib.error.URLError, OSError, http.client.HTTPException, httpsafe.EndpointRejected, ValueError):
        raise RemoteError("remote_unavailable", rc=2) from None
    try:
        value = json.loads(raw) if len(raw) <= RESPONSE_MAX else None
    except ValueError:
        value = None
    if type(value) is not dict:
        raise RemoteError("remote_unavailable", rc=2)
    return status, {str(k).lower(): v for k, v in (response_headers or {}).items()}, value


def _code(value, default):
    return value if isinstance(value, str) and CODE.fullmatch(value) else default


def _text(value, limit=200):
    """断りに添える短い理由（サーバが既に落としてある）。念のため 1 行・秘密を伏せる。"""
    if not isinstance(value, str) or not value.strip():
        return None
    return (" ".join((redact.redact(value) or "").split()))[:limit] or None


def _answer(status, value):
    """200 の答え（断りなら `RemoteError`）。"""
    if status == 200:
        # 断りは `error` が符丁の文字列のとき。`posts` のように `"error": null` の欄を持つ正常な答えは通す
        # （3.14.0 で `posts` だけ request_failed になっていた・09-26 masaru の通し確認）。
        if isinstance(value.get("error"), str) and value["error"]:
            raise RemoteError(_code(value.get("error"), "request_failed"), reason=_text(value.get("reason")),
                              next_at=value.get("next_at") if isinstance(value.get("next_at"), str) else None)
        return value
    if status == 401:
        raise RemoteError(value["error"] if value.get("error") in ("invalid_key", "key_expired") else "invalid_key",
                          rc=2)
    if status == 429:
        raise RemoteError(_code(value.get("error"), "rate_limited"))
    if status == 404:
        raise RemoteError("result_not_found")
    raise RemoteError(_code(value.get("error"), "remote_unavailable"), rc=2)


def _retry_after(headers):
    try:
        seconds = int(str(headers.get("retry-after")).strip())
    except (TypeError, ValueError):
        return None
    return seconds if 0 <= seconds <= RETRY_AFTER_MAX else None


def call(operation, account, **fields):
    """`POST <url>/api/v1/<operation>`。成功なら VM の JSON（dict）、断りは `RemoteError`。"""
    cfg = load()
    if cfg is None:
        raise RemoteError("not_logged_in", rc=2)
    body = {"account": account, **{k: v for k, v in fields.items() if v is not None}}
    status, headers, value = _exchange(cfg, "POST", "/api/v1/" + operation, body)
    if status == 429:
        wait = _retry_after(headers)
        if wait is not None:
            _sleep(wait)
            status, headers, value = _exchange(cfg, "POST", "/api/v1/" + operation, body)
    if status == 202:
        request_id = value.get("request_id")
        if value.get("status") != "pending" or not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id):
            raise RemoteError("remote_unavailable", rc=2)
        status, value = _wait(cfg, request_id)
    return _answer(status, value)


def _wait(cfg, request_id):
    deadline = _clock() + POLL_MAX_SECONDS
    while True:
        _sleep(POLL_SECONDS)
        status, _headers, value = _exchange(cfg, "GET", "/api/v1/result/" + request_id)
        if status != 202:
            return status, value
        if _clock() >= deadline:
            raise RemoteError("remote_pending")


# --------------------------------------------------------------------------
# 断りの出し方（手元の道と同じ: 1 語＋次の一手・rc≠0）
# --------------------------------------------------------------------------

NEXT = {
    "not_logged_in": "thth login（ブラウザで許可）か、口座の台帳を手元に置く",
    "invalid_key": "thth login をやり直す（ブラウザで許可すると鍵を発行し直す・https://thth.me/activity でも発行できる）",
    "key_expired": "thth login をやり直す（ブラウザで許可すると鍵を発行し直す・https://thth.me/activity でも発行できる）",
    "remote_config_invalid": "thth logout のあと thth login でもう一度",
    "remote_unavailable": "網を確かめてもう一度（thth.me に届きませんでした）",
    "remote_pending": "まだ結果が返っていません。出たかどうかは thth posts で確かめてから打ち直す",
    "remote_unsupported": "この命令は thth.me の鍵では使えません（手元の台帳で動かす命令です）",
    "rate_limited": "少し待ってもう一度（鍵ごと 1 分に 60 回まで）",
    "too_many_requests": "前の依頼が終わるのを待ってもう一度",
    "daily_limit": "明日まで待つ（上げるのは持ち主が https://thth.me/activity で）",
    "retract_limit": "明日まで待つ（上げるのは持ち主が https://thth.me/activity で）",
    "quiet_hours": "静かな時間帯が明けてから",
    "account_stopped": "持ち主が https://thth.me/activity で戻すまで公開・削除・予約はできません",
    "settings_loosen_requires_owner": "緩めるのは持ち主が https://thth.me/activity で",
    "scope_unavailable": "この鍵の口座か確かめる（thth account status <口座>）",
    "writes_not_allowed": "読むだけの鍵です。https://thth.me/activity で書ける鍵を発行し直す",
    "invalid_draft": "本文を直してもう一度（--dry-run で lint だけ確かめられます）",
}


def jst_text(value):
    """ISO の時刻を JST の「YYYY-MM-DD HH:MM JST」に。読めなければそのまま。"""
    from . import jst
    try:
        return jst.to_jst(datetime.datetime.fromisoformat(value)).strftime("%Y-%m-%d %H:%M JST")
    except (TypeError, ValueError):
        return value


def refuse(exc, *, as_json=False) -> int:
    """断りを出す。stderr に `符丁[: 理由][（次は …）]` と次の一手、`--json` なら stdout に JSON。"""
    line = exc.code + (f": {exc.reason}" if exc.reason else "")
    if exc.next_at:
        line += f"（次は {jst_text(exc.next_at)} から）"
    print(line, file=sys.stderr)
    if exc.code in NEXT:
        print("次の一歩: " + NEXT[exc.code], file=sys.stderr)
    if as_json:
        payload = {"error": exc.code}
        if exc.reason:
            payload["reason"] = exc.reason
        if exc.next_at:
            payload["next_at"] = exc.next_at
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return exc.rc


# --------------------------------------------------------------------------
# 手元の道と遠くの道の切り替え（設計 §3.4）——判定はここ 1 か所
# --------------------------------------------------------------------------

# 遠くの道の命令（`command_key` → 実装 `run(args, account) -> rc`）。下の「命令」の節で埋める。
COMMANDS: dict = {}
# 台帳が無く鍵があるときに `remote_unsupported` と言う命令（運営の命令・手元の台帳の命令）。
# ここに無い命令は、台帳が無ければ今までどおり手元の道が断る（口座でない名前を取る命令を巻き込まない）。
UNSUPPORTED = frozenset(("throw", "run", "auth", "refresh", "doctor", "inflight", "thread",
                         "account", "account resume", "account leave"))
# `--remote` を旗として持つ命令（それ以外に `--remote` が付けば parse の前に断る）。
REMOTE_PARSERS = ("send", "schedule", "retract", "posts", "replies", "measured", "collect",
                  "mentions", "topics", "profile", "location", "account", "queue")


def command_key(command, args) -> str:
    if command == "account":
        verb = getattr(args, "account", None)
        return "account " + verb if verb in ("status", "set", "add", "leave", "resume", "migrate") else "account"
    if command == "topics":
        return "topics search" if getattr(args, "search", None) is not None else "topics"
    if command == "location":
        return "location search"
    return command


def _account(key, args):
    if key.startswith("account "):
        return getattr(args, "name", None)
    if key == "topics search":
        return getattr(args, "account_flag", None) or getattr(args, "account", None)
    return getattr(args, "account", None)


def has_ledger(account) -> bool:
    """口座の台帳が手元にあるか（読めるかは見ない・読めなければ手元の道が言う）。"""
    from . import accounts
    if not accounts.name_is_safe(account):
        return True  # 名前の断りは手元の道の 1 か所（`validate_name`）に任せる
    try:
        return os.path.exists(os.path.join(accounts.accounts_dir(), account + ".json"))
    except Exception:  # noqa: BLE001 — 置き場が読めないなら手元の道が言う
        return True


def route(args, command):
    """手元の道なら None。遠くの道なら行って rc を返す（`cli._main` の 1 行から）。

    台帳が手元にあれば手元の道。無くて `remote.json` があれば遠くの道。両方あれば手元が先で、
    `--remote` で遠くを指せる。どちらも無ければ手元の道がそのまま断る（台帳が無い・`thth login`）。
    """
    key = command_key(command, args)
    forced = bool(getattr(args, "remote", False))
    account = _account(key, args)
    if not forced:
        if key not in COMMANDS and key not in UNSUPPORTED:
            return None
        if not isinstance(account, str) or not account or has_ledger(account) or not configured():
            return None
    as_json = bool(getattr(args, "json", False))
    run = COMMANDS.get(key)
    if run is None:
        return refuse(RemoteError("remote_unsupported", rc=2), as_json=as_json)
    if not isinstance(account, str) or not account:
        return refuse(RemoteError("invalid_request", reason="口座を指定してください", rc=2), as_json=as_json)
    try:
        return run(args, account)
    except RemoteError as exc:
        return refuse(exc, as_json=as_json)


def refuse_flag(argv):
    """`--remote` を持たない命令に `--remote` が付いていれば断る（parse の前・1 か所）。"""
    if "--remote" in argv and argv and argv[0] not in REMOTE_PARSERS:
        return refuse(RemoteError("remote_unsupported", rc=2))
    return None


# --------------------------------------------------------------------------
# 命令（手元の道と同じ名前・引数・`--json` の形。人向けの表示は手元の道の関数をそのまま使う）
# --------------------------------------------------------------------------

def _print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _shown(args, value, show) -> int:
    if getattr(args, "json", False):
        _print_json(value)
        return 0
    return show(value)


def _body(args) -> str:
    """本文: `--text`（1 行）・`--text-file`・標準入力のどれか 1 つ。"""
    text = getattr(args, "text", None)
    path = getattr(args, "text_file", None)
    if text is not None and path:
        raise RemoteError("invalid_request", reason="--text と --text-file はどちらか 1 つ", rc=2)
    if text is not None:
        body = text
    elif path:
        try:
            with open(path, encoding="utf-8") as f:
                body = f.read()
        except (OSError, UnicodeDecodeError):
            raise RemoteError("invalid_request", reason="本文のファイルを読めません", rc=2) from None
    else:
        try:
            body = sys.stdin.read()
        except OSError:
            raise RemoteError("invalid_request", reason="標準入力を読めません（--text か --text-file）", rc=2) from None
    if not body.strip():
        raise RemoteError("invalid_request", reason="本文が空です", rc=2)
    return body


def _no_media(args):
    # 添付（media_upload_url → PUT → media_complete）は遠くの道ではまだ踏まない。
    if getattr(args, "media_files", None):
        raise RemoteError("remote_unsupported", reason="添付", rc=2)


def _send(args, account):
    _no_media(args)
    # 遠くの道は rehearsal を挟まない（設計 §6）: 頼んだらその場で出す（サーバが lint と安全装置で断る）。
    # `--production`・`--confirm` は要らない（付いていても読まない・口座の台帳はサーバにある）。
    body = _body(args)
    fields = {"body": body, "topic": args.topic, "reply_to": args.reply_to}
    if getattr(args, "dry_run", False):
        # lint だけ通す: 下書きを置いて結果を返す（出さない・予約しない）。
        from . import jst
        value = call("draft_put", account, publish_at=jst.iso(), **fields)
        return _shown(args, value, lambda v: _say(f"{account}: 通りました（出していません・下書き "
                                                  f"{str(v.get('draft_id'))[:12]}…）"))
    value = call("send_request", account, **fields)
    return _shown(args, value, _show_sent)


def _say(line) -> int:
    print(line)
    return 0


def _show_sent(value) -> int:
    if value.get("status") == "held":
        print(f"{value.get('account')}: {value.get('hold_minutes')} 分の猶予つきで予約しました"
              f"（{jst_text(value.get('publish_at'))} に出ます・取り消しは https://thth.me/activity）")
        return 0
    print(f"{value.get('account')}: 出しました post_id {value.get('post_id')}")
    if value.get("permalink"):
        print(f"  {value['permalink']}")
    return 0


def _schedule(args, account):
    _no_media(args)
    draft = getattr(args, "draft", None)
    if draft is None:
        if getattr(args, "text", None) is None and not getattr(args, "text_file", None):
            # 「いつ何が出るか」の一覧は遠くの道では下書きの一覧（thth queue）で見る。
            raise RemoteError("remote_unsupported", reason="一覧は thth queue <口座>", rc=2)
        if not getattr(args, "at", None):
            raise RemoteError("invalid_request", reason="--at <ISO 時刻> を付けてください", rc=2)
        put = call("draft_put", account, body=_body(args), publish_at=args.at,
                   topic=args.topic, reply_to=args.reply_to)
        draft = put.get("draft_id")
        if not isinstance(draft, str):
            raise RemoteError("remote_unavailable", rc=2)
    value = call("schedule_request", account, draft_id=draft)
    return _shown(args, value, lambda v: _say(
        f"{account}: 予約しました（{jst_text(v.get('publish_at'))}・下書き {str(v.get('draft_id'))[:12]}…）"))


def _retract(args, account):
    reason = (args.reason or "").strip()
    if not reason:
        raise RemoteError("invalid_request", reason="--reason を付けてください（なぜ取り下げるかを記録します）", rc=1)
    value = call("retract_request", account, post_id=args.post_id, reason=reason)
    return _shown(args, value, lambda v: _say(f"{account}: 取り下げました post_id {v.get('post_id')}"
                                              + ("（記録の送信は保留）" if v.get("push_pending") else "")))


def _limit(args):
    value = getattr(args, "limit", None)
    return value if isinstance(value, int) and value > 0 else None


def _posts(args, account):
    from . import cli
    value = call("posts", account, limit=_limit(args), refresh=True if getattr(args, "refresh", False) else None)
    return _shown(args, value, lambda v: cli.show_posts(account, v))


def _replies(args, account):
    from . import cli
    value = call("replies", account, limit=_limit(args), post_id=getattr(args, "post", None),
                 refresh=True if getattr(args, "refresh", False) else None)
    return _shown(args, value, lambda v: cli.show_replies(v, v.get("refresh")))


def _measured(args, account):
    from . import cli
    value = call("measured", account, limit=_limit(args), post_id=getattr(args, "post", None))
    return _shown(args, value, cli.show_measured)


def _collect(args, account):
    from . import cli
    return _shown(args, call("collect", account), cli.show_measured)


def _mentions(args, account):
    from . import threads_read_cli
    if getattr(args, "since", None):
        raise RemoteError("remote_unsupported", reason="--since", rc=2)
    value = call("mentions", account, limit=_limit(args),
                 refresh=True if getattr(args, "refresh", False) else None)
    return _shown(args, value, lambda v: threads_read_cli.show_mentions(account, v.get("mentions") or []))


def _topics_search(args, account):
    from . import threads_read_cli
    if getattr(args, "recent", False):
        raise RemoteError("remote_unsupported", reason="--recent", rc=2)
    query = (args.search or "").strip()
    if not query:
        raise RemoteError("invalid_request", reason="--search に語を書いてください", rc=2)
    value = call("topics_search", account, query=query, limit=_limit(args))
    return _shown(args, value, lambda v: threads_read_cli.show_search(account, v))


def _profile(args, account):
    from . import threads_read_cli
    value = call("profile", account, username=args.username)
    return _shown(args, value, lambda v: threads_read_cli.show_profile(account, v.get("profile") or {}))


def _location(args, account):
    from . import retract_cli
    value = call("location_search", account, query=args.query)
    return _shown(args, value, lambda v: retract_cli.show_locations(args.query, v.get("locations") or []))


def _account_status(args, account):
    from . import account_settings
    if getattr(args, "rest", None):
        raise RemoteError("invalid_request", reason="thth account status <口座>", rc=2)
    return _shown(args, call("account_status", account), account_settings.show_status)


def _account_set(args, account):
    """締める向きだけ通る（緩める向きは `settings_loosen_requires_owner` のまま）。"""
    from . import account_settings
    try:
        pairs = account_settings._pairs(list(getattr(args, "rest", None) or []))
    except account_settings.SettingsError:
        raise RemoteError("invalid_setting", reason="thth account set <口座> <名前> <値>", rc=2) from None
    results = [call("settings", account, key=key, value=value) for key, value in pairs]
    if getattr(args, "json", False):
        _print_json(results if len(results) > 1 else results[0])
        return 0
    return account_settings.show_changes(results)


def _queue(args, account):
    return _shown(args, call("draft_list", account), _show_drafts)


def _show_drafts(value) -> int:
    drafts = value.get("drafts") or []
    for row in drafts:
        head = " ".join(str(row.get("body") or "").split())[:40]
        topic = f" [{row['topic']}]" if row.get("topic") else ""
        print(f"{str(row.get('publish_at') or '')[:16]}  {row.get('status')}  "
              f"{str(row.get('draft_id'))[:12]}…{topic} {head}")
    print(f"—— {value.get('account')}: {len(drafts)} 本")
    return 0


COMMANDS.update({
    "send": _send, "schedule": _schedule, "retract": _retract,
    "posts": _posts, "replies": _replies, "measured": _measured, "collect": _collect,
    "mentions": _mentions, "topics search": _topics_search, "profile": _profile,
    "location search": _location, "account status": _account_status, "account set": _account_set,
    "queue": _queue,
})


def register(sub) -> None:
    """`thth login` / `thth logout` と遠くの道の旗（`build_parser()` から 1 行で）。

    旗は手元の道でも同じ意味で受ける（`--limit` は先頭から切る・`--json` は同じ形）。
    """
    for name in REMOTE_PARSERS:
        parser = sub.choices.get(name)
        if parser is not None:
            parser.add_argument("--remote", action="store_true",
                                help="台帳が手元にあっても thth.me の鍵で動かす（遠くの道・thth login のあと）")
    choices = sub.choices
    send = choices["send"]
    send.add_argument("--text", default=None, help="本文（1 行の文。長い本文は --text-file か標準入力）")
    send.add_argument("--dry-run", dest="dry_run", action="store_true",
                      help="出さずに lint の結果だけ（手元の道では既定の dry-run と同じ）")
    send.add_argument("--json", action="store_true", help="結果を JSON 1 つで（経過の行は stderr）")
    schedule = choices["schedule"]
    schedule.add_argument("--text", default=None, help="遠くの道: 予約する本文（1 行）")
    schedule.add_argument("--text-file", dest="text_file", default=None, help="遠くの道: 予約する本文のファイル")
    schedule.add_argument("--at", default=None, help="遠くの道: 出す時刻（ISO・例 2026-09-27T09:00+09:00）")
    schedule.add_argument("--draft", default=None, help="遠くの道: 置いた下書き（draft_id）を予約する")
    schedule.add_argument("--topic", default=None)
    schedule.add_argument("--reply-to", dest="reply_to", default=None)
    choices["posts"].add_argument("--refresh", action="store_true",
                                  help="媒体から引き直す（thth posts は元から毎回引く・同じ動き）")
    for name in ("replies", "measured", "mentions"):
        choices[name].add_argument("--limit", type=int, default=None, help="先頭から何件")
    choices["mentions"].add_argument("--refresh", action="store_true",
                                     help="媒体から引き直す（thth mentions は元から毎回引く・同じ動き）")
    choices["collect"].add_argument("--json", action="store_true",
                                    help="採ったあとの数字を thth measured --json と同じ形で（口座を 1 つ指定）")
    p = sub.add_parser("login", help="thth.me の鍵を受け取って保存する（遠くの道・既定はブラウザで許可）")
    p.add_argument("--url", default=None, help=f"既定 {DEFAULT_URL}")
    p.add_argument("--no-browser", dest="no_browser", action="store_true",
                   help="ブラウザを開かず URL だけ出す（別の機械のブラウザで開くとき）")
    p.add_argument("--stdin", action="store_true", help="貼る道: 発行済みの鍵を標準入力の 1 行から読む")
    p.add_argument("--paste", action="store_true", help="貼る道: 発行済みの鍵を端末で入れる（表示しない）")
    # 受けるのは断るためだけ（`key_in_argument`）。help には出さない。
    p.add_argument("key", nargs="?", default=None, help=argparse_suppress())
    p.set_defaults(func=cmd_login)
    q = sub.add_parser("logout", help="保存した thth.me の鍵を消す")
    q.set_defaults(func=cmd_logout)


def argparse_suppress():
    import argparse
    return argparse.SUPPRESS
