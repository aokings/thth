"""遠くの道（設計 3.14.0 §2・§3.4）: 鍵で thth.me 経由で自分の口座を動かす。

`thth login` が鍵を `~/.config/thth/remote.json`（0600・親 0700・`{"url", "key"}`）に置き、
以後は手元の道と**同じ命令・同じ引数・同じ `--json` の形**で動く。

- 鍵は tty（`getpass`）か `--stdin` の 1 行でだけ受ける。**引数では受けない**（shell の履歴に残る）。
- 鍵は出力・ログ・例外の文に出さない（読んだらすぐ `redact.register_secret`）。
- 置き場は `THTH_REMOTE_CONFIG` で差し替えられる（試験用）。
"""
from __future__ import annotations

import datetime
import getpass
import http.client
import json
import os
import re
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

LOGIN_NEXT = "鍵は https://thth.me/activity で発行します"


def _fail(code, message=None) -> int:
    print(code + (f": {message}" if message else ""), file=sys.stderr)
    return 2


def cmd_login(args) -> int:
    if getattr(args, "key", None):
        # 値は読まずに捨てる（控えない・表示しない）。
        return _fail("key_in_argument", "鍵を引数で渡すと shell の履歴に残ります。"
                     "`thth login` で tty から、または `thth login --stdin` で入れてください")
    from . import httpsafe
    try:
        url = httpsafe.validated_url(args.url or DEFAULT_URL, base=True)
    except httpsafe.EndpointRejected:
        return _fail("invalid_url", "https:// で始まる URL を指定してください")
    if args.stdin:
        key = sys.stdin.readline().strip()
    else:
        if not sys.stdin.isatty():
            return _fail("tty_required", "端末から実行するか、`thth login --stdin` で標準入力から入れてください")
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
        if "error" in value:
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
    "not_logged_in": "thth login（鍵は https://thth.me/activity で発行）か、口座の台帳を手元に置く",
    "invalid_key": "https://thth.me/activity で鍵を発行し直して thth login",
    "key_expired": "https://thth.me/activity で鍵を発行し直して thth login",
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


def register(sub) -> None:
    """`thth login` / `thth logout` と、遠くの道の命令の `--remote`（`build_parser()` から 1 行で）。"""
    for name in REMOTE_PARSERS:
        parser = sub.choices.get(name)
        if parser is not None:
            parser.add_argument("--remote", action="store_true",
                                help="台帳が手元にあっても thth.me の鍵で動かす（遠くの道・thth login のあと）")
    p = sub.add_parser("login", help="thth.me の鍵を保存する（遠くの道・鍵は tty か --stdin で入れる）")
    p.add_argument("--url", default=None, help=f"既定 {DEFAULT_URL}")
    p.add_argument("--stdin", action="store_true", help="鍵を標準入力の 1 行から読む")
    # 受けるのは断るためだけ（`key_in_argument`）。help には出さない。
    p.add_argument("key", nargs="?", default=None, help=argparse_suppress())
    p.set_defaults(func=cmd_login)
    q = sub.add_parser("logout", help="保存した thth.me の鍵を消す")
    q.set_defaults(func=cmd_logout)


def argparse_suppress():
    import argparse
    return argparse.SUPPRESS
