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
from . import refusal_catalog

DEFAULT_URL = "https://thth.me"
# 段 2 の bearer（Worker の `BEARER`）は印字できる ASCII の 16〜512 字。発行する鍵は
# URL 安全な base64（`opaque()`）なので、その字だけを受ける（空白・引用符の混入を断る）。
KEY = re.compile(r"[A-Za-z0-9_-]{16,512}\Z")


class RemoteError(Exception):
    """遠くの道の断り。`code` は静的な符丁（1 語）。"""

    def __init__(self, code, *, reason=None, next_at=None, rc=1, days_left=None):
        super().__init__(code)
        self.code = code
        self.reason = reason
        self.next_at = next_at
        self.rc = rc
        # 段 E の慣らし（`ramp_daily_limit`）の残りの日数（VM の答えの `assessment.ramp.days_left`・数だけ）。
        self.days_left = days_left


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
    # 1 行目はそのまま、目録の 3 つを足す（段 B・`thth/refusal.py`）。
    from . import refusal
    return refusal.emit(None, code, line=code + (f": {message}" if message else ""), rc=2, as_json=False,
                        door="remote")


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


def _days_left(value):
    """慣らしの断り（段 E）の残りの日数。`assessment.ramp.days_left` が 1〜99 の整数のときだけ。"""
    if value.get("error") != "ramp_daily_limit":
        return None
    ramp = (value.get("assessment") or {}).get("ramp") if isinstance(value.get("assessment"), dict) else None
    days = ramp.get("days_left") if isinstance(ramp, dict) else None
    return days if type(days) is int and 1 <= days <= 99 else None


def _answer(status, value):
    """200 の答え（断りなら `RemoteError`）。"""
    if status == 200:
        # 断りは `error` が符丁の文字列のとき。`posts` のように `"error": null` の欄を持つ正常な答えは通す
        # （3.14.0 で `posts` だけ request_failed になっていた・09-26 masaru の通し確認）。
        if isinstance(value.get("error"), str) and value["error"]:
            raise RemoteError(_code(value.get("error"), "request_failed"), reason=_text(value.get("reason")),
                              next_at=value.get("next_at") if isinstance(value.get("next_at"), str) else None,
                              days_left=_days_left(value))
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

NEXT = refusal_catalog.fix_view("remote.NEXT")


def jst_text(value):
    """ISO の時刻を JST の「YYYY-MM-DD HH:MM JST」に。読めなければそのまま。"""
    from . import jst
    try:
        return jst.to_jst(datetime.datetime.fromisoformat(value)).strftime("%Y-%m-%d %H:%M JST")
    except (TypeError, ValueError):
        return value


def refuse(exc, *, as_json=False, account=None) -> int:
    """断りを出す。stderr に `符丁[: 理由][（次は …）]` と 2 行目から（なぜ・通すには・もっと良く）、
    `--json` なら stdout に JSON（今の鍵に `why`・`fix`・`better`・`next_at` を足す）。

    3 つは目録の口（`refusal.emit`・段 B）が作る。目録に無い符丁は今までどおり「次の一歩」の 1 行。
    """
    from . import refusal
    line = exc.code + (f": {exc.reason}" if exc.reason else "")
    if exc.next_at:
        line += f"（次は {jst_text(exc.next_at)} から）"
    if getattr(exc, "days_left", None) is not None:
        line += f"（慣らしの期間・あと {exc.days_left} 日）"
    payload = {"error": exc.code}
    if exc.reason:
        payload["reason"] = exc.reason
    if exc.next_at:
        payload["next_at"] = exc.next_at
    if refusal.parts(exc.code) is None:
        print(line, file=sys.stderr)
        if exc.code in NEXT:
            print("次の一歩: " + NEXT[exc.code], file=sys.stderr)
        if as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        return exc.rc
    values = {"door": "remote", **({"account": account} if isinstance(account, str) else {})}
    if isinstance(exc.reason, str):
        values["reason"] = exc.reason  # 3 つが見るのは安全装置の静的な詳細だけ（`refusal.parts`）
    return refusal.emit(None, exc.code, line=line, payload=payload, next_at=exc.next_at, rc=exc.rc,
                        both=True, about=refusal.focus(exc.code, exc.reason), as_json=bool(as_json), **values)


# --------------------------------------------------------------------------
# 手元の道と遠くの道の切り替え（設計 §3.4）——判定はここ 1 か所
# --------------------------------------------------------------------------

# 遠くの道の命令（`command_key` → 実装 `run(args, account) -> rc`）。下の「命令」の節で埋める。
COMMANDS: dict = {}
# 台帳が無く鍵があるときに `remote_unsupported` と言う命令（運営の命令・手元の台帳の命令）。
# ここに無い命令は、台帳が無ければ今までどおり手元の道が断る（口座でない名前を取る命令を巻き込まない）。
UNSUPPORTED = frozenset(("throw", "run", "auth", "refresh",
                         "account", "account resume", "account leave",
                         # 計画 Meta 全部盛り: 遠くの道に出していない Meta の書く口（消すと戻せない・審査の区間に無い）。
                         "reply delete"))
# `--remote` を旗として持つ命令（それ以外に `--remote` が付けば parse の前に断る）。
REMOTE_PARSERS = ("send", "schedule", "retract", "posts", "replies", "measured", "collect",
                  "mentions", "topics", "profile", "location", "account", "queue", "inflight",
                  "thread", "where", "who", "events", "summary", "dashboard", "embed", "upcoming",
                  # 計画 Meta 全部盛り（審査員の道・`thth/server_meta.py`）。
                  "doctor", "dm", "live", "like", "unlike", "edit", "reply")


# `--remote` を子の命令（`bookmark add`・`page cta`）が持つ命令。`REMOTE_PARSERS` と違い、親には旗を足さない。
REMOTE_CHILD_PARSERS = ("bookmark", "page",
                        # 設計 広告の資格と遠くの道 段 4: `thth ads <verb> --remote`（子の命令に旗を足す）。
                        "ads")


def command_key(command, args) -> str:
    if command == "ads":
        verb = str(getattr(args, "ads_verb", "") or "")
        return f"ads catalog {getattr(args, 'catalog_verb', '')}" if verb == "catalog" else "ads " + verb
    if command == "bookmark":
        return "bookmark " + str(getattr(args, "verb", "") or "")
    if command in ("dm", "live", "reply"):
        return command + " " + str(getattr(args, "action", "") or "")
    if command == "page":
        return f"page {getattr(args, 'page_command', '')} {getattr(args, 'action', '')}"
    if command == "account":
        verb = getattr(args, "account", None)
        return "account " + verb if verb in ("status", "set", "add", "leave", "resume", "migrate") else "account"
    if command == "topics":
        return "topics search" if getattr(args, "search", None) is not None else "topics"
    if command == "location":
        return "location search"
    return command


def _account(key, args):
    if key.startswith("ads "):
        return getattr(args, "name", None)
    if key.startswith("account "):
        return getattr(args, "name", None)
    if key == "topics search":
        return getattr(args, "account_flag", None) or getattr(args, "account", None)
    if key in ("events", "summary", "dashboard"):
        # 対象は口座名か project 名。手元の台帳で project として引ける名前は手元の道（遠くへ送らない）。
        name = getattr(args, "account", None)
        from . import morning
        try:
            local = bool(morning.targets(name))
        except Exception:  # noqa: BLE001 — 台帳が読めなければ口座名として扱う
            local = False
        return None if local else name
    if key in ("where", "who"):
        # 口座は位置引数の先頭（`--project` は手元の台帳を束ねる形なので遠くの道には出さない）。
        targets = getattr(args, "targets", None) or []
        return None if getattr(args, "project", None) or not targets else targets[0]
    return getattr(args, "account", None)


def has_ledger(account) -> bool:
    """口座の台帳が手元にあるか（読めるかは見ない・読めなければ手元の道が言う）。"""
    from . import accounts
    if not accounts.name_is_safe(account):
        return True  # 名前の断りは手元の道の 1 か所（`validate_name`）に任せる
    try:
        if os.path.exists(os.path.join(accounts.accounts_dir(), account + ".json")):
            return True
        # 広告の口座の台帳（`adaccounts/<名前>.json`）も手元の台帳（設計 広告の資格と遠くの道 段 4）。
        from .ads import ledger as ads_ledger
        return os.path.exists(ads_ledger.path_for(account))
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
    as_json = bool(getattr(args, "json", False) or getattr(args, "as_json", False))
    run = COMMANDS.get(key)
    if run is None:
        # 広告の出さない口は「手元の台帳で打つ」でなく「運営者に頼む」の断り（設計 広告の資格と遠くの道 §5 (2)）。
        refused = (RemoteError("ads_remote_unsupported", rc=2) if key.startswith("ads ")
                   else RemoteError("remote_unsupported", rc=2))
        return refuse(refused, as_json=as_json)
    if not isinstance(account, str) or not account:
        return refuse(RemoteError("invalid_request", reason="口座を指定してください", rc=2), as_json=as_json)
    try:
        return run(args, account)
    except RemoteError as exc:
        return refuse(exc, as_json=as_json, account=account)


def refuse_flag(argv):
    """`--remote` を持たない命令に `--remote` が付いていれば断る（parse の前・1 か所）。"""
    if "--remote" in argv and argv and argv[0] not in REMOTE_PARSERS + REMOTE_CHILD_PARSERS:
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
    elif path == "-":
        # 4.5.3: `--text-file -` は標準入力（明示の「-」）。
        try:
            body = sys.stdin.read()
        except OSError:
            raise RemoteError("invalid_request", reason="標準入力を読めません（--text か --text-file）", rc=2) from None
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


# 添付の拡張子 → (種類, MIME)。種類ごとの大きさの上限と中身の検査はサーバ（`media_uploads`）が言う。
MEDIA_TYPES = {
    "jpg": ("image", "image/jpeg"), "jpeg": ("image", "image/jpeg"), "png": ("image", "image/png"),
    "webp": ("image", "image/webp"), "gif": ("image", "image/gif"),
    "mp4": ("video", "video/mp4"), "mov": ("video", "video/quicktime"),
    "mp3": ("audio", "audio/mpeg"), "flac": ("audio", "audio/flac"), "ogg": ("audio", "audio/ogg"),
    "wav": ("audio", "audio/wav"), "webm": ("audio", "audio/webm"), "m4a": ("audio", "audio/mp4"),
}
UPLOAD_TIMEOUT = 300  # 添付の 1 回の PUT（大きな動画もある）


def _put(cfg, url, data, mime):
    """置き場（`<url>/media-upload/…`）へ 1 回 PUT する。鍵は付けない（URL そのものが一回限りの資格）。"""
    from . import __version__, httpsafe
    headers = {"Content-Type": mime, "Accept": "application/json", "User-Agent": "thth/" + __version__}
    try:
        status, _headers, raw = _transport("PUT", url, headers, data, UPLOAD_TIMEOUT)
    except (urllib.error.URLError, OSError, http.client.HTTPException, httpsafe.EndpointRejected, ValueError):
        raise RemoteError("media_upload_failed", rc=2) from None
    if status != 200:
        try:
            value = json.loads(raw)
        except ValueError:
            value = {}
        raise RemoteError(_code(value.get("error") if type(value) is dict else None, "media_upload_failed"), rc=2)


def _media(args, account):
    """`--media <ファイル> --alt <説明>` を thth.me に置いて確定し、`[{media_id, alt}]` を返す（無ければ None）。

    手順は MCP の道具と同じ（`media_upload_url` → PUT → `media_complete`・10/15 点検 B-18）。
    ファイルは手元の機械のもの（VM のパスではない）。中身の検査・位置情報などを落とすのはサーバ。
    """
    files = getattr(args, "media_files", None) or []
    alts = getattr(args, "alts", None) or []
    if not files:
        return None
    if len(files) != len(alts) or any(not isinstance(a, str) or not a.strip() for a in alts):
        raise RemoteError("invalid_request", reason="--media ごとに --alt（画像の説明）を 1 つ", rc=2)
    cfg = load()
    if cfg is None:
        raise RemoteError("not_logged_in", rc=2)
    rows = []
    for path, alt in zip(files, alts):
        kind_mime = MEDIA_TYPES.get(os.path.splitext(path)[1].lstrip(".").lower())
        if kind_mime is None:
            raise RemoteError("media_kind_unsupported", reason=os.path.basename(path), rc=2)
        try:
            size = os.path.getsize(path)
            digest = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    digest.update(chunk)
        except OSError:
            raise RemoteError("invalid_request", reason=f"添付のファイルを読めません: {os.path.basename(path)}",
                              rc=2) from None
        kind, mime = kind_mime
        made = call("media_upload_url", account, kind=kind, size=size, sha256=digest.hexdigest(), mime=mime)
        url, media_id = made.get("upload_url"), made.get("media_id")
        if isinstance(url, str):
            redact.register_secret(url)  # 一回限りの資格（画面・ログに出さない）
        # 置き場は鍵と同じ thth.me の上だけ（答えが別の所を指しても中身を送らない）。
        if not isinstance(url, str) or not url.startswith(cfg["url"] + "/media-upload/") \
                or not isinstance(media_id, str):
            raise RemoteError("remote_unavailable", rc=2)
        part = made.get("part_size")
        with open(path, "rb") as f:
            if isinstance(part, int) and part > 0:
                number = 1
                for chunk in iter(lambda: f.read(part), b""):
                    _put(cfg, f"{url}/{number}", chunk, mime)
                    number += 1
            else:
                _put(cfg, url, f.read(), mime)
        call("media_complete", account, media_id=media_id)
        rows.append({"media_id": media_id, "alt": alt})
    return rows


def _refuse_unread(args, command: str, flags: dict) -> None:
    """遠くの道が読まない旗が付いていたら、黙って捨てずに断る（10/15 道ごとの意味の差 §揃える案 1）。

    それまでは捨てていたので、`retract --already-gone`（記録だけ）が本当の DELETE に、
    `send --community` が普段のタイムラインへの公開になった。`flags` は `{dest: 旗の綴り}`。
    """
    used = [spelled for dest, spelled in flags.items()
            if getattr(args, dest, None) not in (None, False, 0, "", [])]
    if used:
        raise RemoteError("remote_unsupported",
                          reason=f"thth.me の鍵の道の {command} は {'・'.join(used)} を読みません"
                                 "（付けたまま出すと意図と違う結果になるので断りました）", rc=2)


def _send(args, account):
    # **`--production` が無ければ出さない**（lint だけ・手元の道と同じ意味・masaru 10/15）。それまでは
    # 3.14.0 の「頼んだらその場で出す」で `--production` を読まず、help の「既定は乾式」と食い違って
    # キョンシーラボの口座で試すつもりの本文が出た（#2477）。`--confirm` は要らない（digest はサーバが
    # その場で突き合わせる）。MCP の `thth_send_request` は今までどおり呼べば出す。
    _refuse_unread(args, "send", {"community_id": "--community", "goal": "--goal", "found_by": "--found-by",
                                  "share_to_instagram": "--share-to-instagram",
                                  "reply_to_root": "--reply-to-root",
                                  "reply_to_author_key": "--reply-to-author-key", "confirm": "--confirm"})
    has_media = bool(getattr(args, "media_files", None))
    no_text = getattr(args, "text", None) is None and not getattr(args, "text_file", None)
    # 添付だけの投稿は本文を空にできる（手元の `thth send --media` と同じ）。標準入力は待たない。
    body = "" if has_media and no_text else _body(args)
    media = _media(args, account)
    fields = {"body": body, "topic": args.topic, "reply_to": args.reply_to, "media": media}
    if getattr(args, "dry_run", False) or not getattr(args, "production", False):
        # lint だけ通す: 下書きを置いて結果を返す（出さない・予約しない）。
        from . import jst
        value = call("draft_put", account, publish_at=jst.iso(), **fields)
        hint = "" if getattr(args, "dry_run", False) else "。出すなら同じ命令に --production"
        return _shown(args, value, lambda v: _say(f"{account}: 通りました（出していません・下書き "
                                                  f"{str(v.get('draft_id'))[:12]}…）{hint}"))
    # ⑦（4.4.1）: hub の承認の番号。口座の台帳が `require_gate: true` なら VM が番号の無い公開を断る。
    value = call("send_request", account, approval=getattr(args, "approval", None), **fields)
    return _shown(args, value, _show_sent)


def _say(line) -> int:
    print(line)
    return 0


def _show_sent(value) -> int:
    if value.get("status") == "queued":
        # 返信の burst（3.25.0）: 口座は止めず、窓が空く時刻の順番待ちに並べた。
        print(f"{value.get('account')}: burst に当たったので {jst_text(value.get('publish_at'))} に出す順番待ちに"
              "並べました。止めていません（取り消しは https://thth.me/activity）")
        return 0
    if value.get("status") == "held":
        print(f"{value.get('account')}: {value.get('hold_minutes')} 分の猶予つきで予約しました"
              f"（{jst_text(value.get('publish_at'))} に出ます・取り消しは https://thth.me/activity）")
        return 0
    print(f"{value.get('account')}: 出しました post_id {value.get('post_id')}")
    if value.get("permalink"):
        print(f"  {value['permalink']}")
    return 0


def _schedule(args, account):
    if getattr(args, "unapproved", False) or getattr(args, "window", None) is not None:
        # 11/06: 未承認の数え上げは運営の道具（VM の台帳と queue を読む）。遠くの道には出さない。
        raise RemoteError("remote_unsupported", rc=2)
    draft = getattr(args, "draft", None)
    if draft is not None and getattr(args, "not_after", None):
        # 4.5.6: 期限は下書きを置くとき（--text・--text-file）に付ける。--draft では付けられない
        # （黙って捨てると、付いたつもりのまま期限なしで予約される・監査 Fable 10/23）。
        raise RemoteError("invalid_request", reason="--not-after は --draft と一緒に使えません。"
                          "期限は下書きを置くとき（--text か --text-file と --at）に付けてください", rc=2)
    if draft is None:
        if getattr(args, "text", None) is None and not getattr(args, "text_file", None):
            # 「いつ何が出るか」の一覧（手元の `thth schedule <口座>` と同じ行・10/15 点検 B-18）。
            from . import cli
            value = call("schedule_list", account, days=getattr(args, "days", None))
            rows = value.get("schedule") if isinstance(value.get("schedule"), list) else []
            if getattr(args, "json", False):
                _print_json(rows)
                return 0
            return cli.show_schedule(rows)
        if not getattr(args, "at", None):
            raise RemoteError("invalid_request", reason="--at <ISO 時刻> を付けてください", rc=2)
        _refuse_past(args.at)
        put = call("draft_put", account, body=_body(args), publish_at=args.at,
                   topic=args.topic, reply_to=args.reply_to, media=_media(args, account),
                   not_after=getattr(args, "not_after", None))
        draft = put.get("draft_id")
        if not isinstance(draft, str):
            raise RemoteError("remote_unavailable", rc=2)
        if not getattr(args, "production", False):
            # **`--production` が無ければ予約しない**（send と同じ・10/16）。下書きは lint を通って残る。
            return _shown(args, put, lambda v: _say(
                f"{account}: 通りました（予約していません・下書き {str(v.get('draft_id'))[:12]}…）。"
                f"予約するなら --draft={v.get('draft_id')} --production"))
    elif not getattr(args, "production", False):
        raise RemoteError("invalid_request", reason="予約するなら --production を付けてください（付けなければ何もしません）",
                          rc=2)
    # ⑦（4.4.1）: hub の承認の番号。口座の台帳が `require_gate: true` なら VM が番号の無い予約を断る。
    value = call("schedule_request", account, draft_id=draft, confirm=getattr(args, "confirm", None),
                 approval=getattr(args, "approval", None))
    if value.get("stage") == 1:
        # はてなの口座（設計 4.0 S1 §6.4）: 1 段目は見せて digest を返すだけ（何も刻んでいない）。
        from . import refusals
        refusals.mark_first_stage()
        if getattr(args, "json", False):
            _print_json(value)
            return 1
        print(f"{account}: 予約しません（確認の一段目です）")
        print(value.get("preview") or "")
        gate = getattr(args, "approval", None)
        print(f"刻むなら: thth schedule {account} --draft={draft} --production --confirm {value.get('digest')}"
              + (f" --approval {gate}" if gate else ""))
        return 1
    return _shown(args, value, lambda v: _say(
        f"{account}: 予約しました（{jst_text(v.get('publish_at'))}・下書き {str(v.get('draft_id'))[:12]}…）"))


def _bookmark_add(args, account, op="add"):
    """`thth bookmark add|delete`（遠くの道・`bookmark_request`）。手元の道と同じ 2 段（4.0 S4: `--url`・`op`）。"""
    fields = {"comment": args.comment, "tags": args.tags, "confirm": args.confirm}
    if getattr(args, "url", None):
        fields["url"] = args.url
    else:
        fields["delivery"] = args.delivery
    if op != "add":
        fields["op"] = op
    value = call("bookmark_request", account, **fields)
    if value.get("stage") == 1:
        from . import refusals
        refusals.mark_first_stage()
        if getattr(args, "json", False):
            _print_json(value)
            return 1
        print(f"{account}: ブックマークしません（確認の一段目です）")
        for key, item in (value.get("preview") or {}).items():
            print(f"  {key}: {item}")
        if (value.get("preview") or {}).get("lane_allowed") is False:
            # 実機の確かめ 10/17 の 7: 2 段目の断りを 1 段目で予告する（直す先は主体の台帳）。
            print("  → 2 段目で断ります（bookmark_not_allowed_for_lane）: このレーンは主体のブックマークを許すレーン"
                  "（主体の台帳の bookmark.lanes）にありません。運営者が thth principal set <主体> bookmark.lanes"
                  " <lane>[,<lane>…] --by <名前> で許せます")
        print(f"digest: {value.get('digest')}")
        return 1
    if value.get("status") == "unknown":
        # 4.0 S4 §7: 結果不明（サーバは sent/bm: に unknown の行を書いた）。手元の道と同じ 2 行・rc 1。
        if getattr(args, "json", False):
            _print_json(value)
            return 1
        print(f"結果が分かりません（{value.get('error')}）: {value.get('url')}", file=sys.stderr)
        print(f"打ち直しません。thth bookmark reconcile {account} で確かめてください", file=sys.stderr)
        return 1
    done = "ブックマークを消しました" if op == "delete" else "ブックマークしました"
    return _shown(args, value, lambda v: _say(f"{account}: {done}（{v.get('url')}）"))


def _bookmark_delete(args, account):
    return _bookmark_add(args, account, op="delete")


def _bookmark_edit(args, account):
    """`thth bookmark edit`（遠くの道・`bookmark_request` の `op: edit`・設計 S4 §21.7）。"""
    fields = {"op": "edit", "comment": args.comment, "tags": args.tags, "confirm": args.confirm}
    if getattr(args, "url", None):
        fields["url"] = args.url
    else:
        fields["delivery"] = args.delivery
    value = call("bookmark_request", account, **fields)
    if value.get("stage") == 1:
        from . import refusals
        refusals.mark_first_stage()
        if getattr(args, "json", False):
            _print_json(value)
            return 1
        print(f"{account}: 直しません（確認の一段目です）")
        for key, item in (value.get("preview") or {}).items():
            print(f"  {key}: {item}")
        print(f"digest: {value.get('digest')}")
        return 1
    if value.get("status") == "unknown":
        if getattr(args, "json", False):
            _print_json(value)
            return 1
        print(f"結果が分かりません（{value.get('error')}）: {value.get('url')}", file=sys.stderr)
        print(f"打ち直しません。thth bookmark reconcile {account} で確かめてください", file=sys.stderr)
        return 1
    return _shown(args, value, lambda v: _say(f"{account}: ブックマークを直しました（{v.get('url')}）"))


BATCH_REMOTE_MAX_BYTES = 48000


def _bookmark_batch(args, account):
    """`thth bookmark batch <口座> <ファイル>`（遠くの道・`bookmark_batch_request`・設計 S4 §20）。

    ファイルの中身をそのまま `text` で送る（形の検査と 1 段目の材料はサーバが手元と同じ関数で作る）。
    """
    from . import bookmark_batch
    try:
        text = bookmark_batch.read_source(args.file)
    except bookmark_batch.BatchError as exc:
        raise RemoteError(exc.code, rc=2) from None
    if len(text.encode("utf-8")) > BATCH_REMOTE_MAX_BYTES:
        # Worker が 413 で断る大きさ。送る前に手元で言う（手元の道の上限は 256 KB のまま）。
        print("札のファイルが 48 KB を超えます。分けてください（遠くの道は 48 KB まで）", file=sys.stderr)
        return 2
    value = call("bookmark_batch_request", account, text=text, confirm=args.confirm)
    if value.get("stage") == 1:
        from . import refusals
        refusals.mark_first_stage()
        if getattr(args, "json", False):
            _print_json(value)
            return 1
        print("ブックマークしません（まとめて承認の一段目です）")
        for line in bookmark_batch.lines(value.get("preview") or {}):
            print(line)
        print(f"並べるなら: thth bookmark batch {account} {args.file} --confirm {value.get('digest')} --production")
        return 1
    return _shown(args, value, lambda v: _say(
        f"{account}: {v.get('queued')} 件を順番待ちに置きました（{v.get('batch')}・除いた {v.get('excluded')} 件）。"
        f"状態: thth bookmark batch-status {account}"))


def _bookmark_batch_status(args, account):
    from . import bookmark_batch
    value = call("bookmark_batch_status", account, batch=getattr(args, "batch", None))
    return _shown(args, value, lambda v: _say("\n".join(bookmark_batch.status_lines(v))))


def _bookmark_batch_cancel(args, account):
    value = call("bookmark_batch_cancel", account, batch=args.batch, item=getattr(args, "item", None))
    return _shown(args, value, lambda v: _say(
        f"{account}: {v.get('batch')} の待ちの札を {v.get('cancelled')} 件取り消しました"))


PAST_GRACE_MINUTES = 5


def _refuse_past(at) -> None:
    """`--at` が過去なら断る（10/15 調べ_道ごとの意味の差 §1-3 の 12）。

    サーバは過去の時刻もそのまま予約し、timer の次の run がすぐ出す。年の打ち間違いなどで
    「後で出すつもりが今出る」を防ぐ。読めない時刻はサーバの lint が断る（ここでは見ない）。
    """
    from . import jst
    try:
        when = datetime.datetime.fromisoformat(at)
    except (TypeError, ValueError):
        return
    if when.tzinfo is None:
        return
    if when < jst.now_jst() - datetime.timedelta(minutes=PAST_GRACE_MINUTES):
        raise RemoteError("invalid_request",
                          reason=f"--at {at} は過去です（予約するとすぐ出ます）。出す時刻を未来にしてください", rc=2)


def _retract(args, account):
    """`thth retract <口座> <post_id> --reason …`（二段・手元の道と同じ・masaru 10/15「揃える」）。

    一段目（`--confirm` 無し）は対象と digest を見せて何もしない（rc=1）。二段目は同じ digest で
    サーバに頼む。digest は口座・post_id・理由から手元で計算する（`approval.compute_retract_digest`
    ・手元の道と同じ値）。それまでは一回で DELETE していた（調べ_道ごとの意味の差 §上位 1）。
    MCP の `thth_retract_request` は今までどおり一回で消す（中で digest を付けて呼ぶ）。
    """
    from . import approval
    _refuse_unread(args, "retract", {"already_gone": "--already-gone"})
    reason = (args.reason or "").strip()
    if not reason:
        raise RemoteError("invalid_request", reason="--reason を付けてください（なぜ取り下げるかを記録します）", rc=1)
    digest = approval.compute_retract_digest(post_id=str(args.post_id), account=account, reason=reason)
    confirm = getattr(args, "confirm", None)
    if confirm is None:
        row = _find_post(account, str(args.post_id))
        if getattr(args, "json", False):
            _print_json({"ok": False, "retracted": False, "stage": 1, "account": account,
                         "post_id": str(args.post_id), "reason": reason, "digest": digest,
                         "permalink": (row or {}).get("permalink"), "text": (row or {}).get("text")})
            return 1
        print(f"取り下げません（確認の一段目です）: {account} post_id {args.post_id}")
        if row:
            print(f"  URL : {row.get('permalink') or '—'}")
            print("--- 取り下げる本文 ---")
            print((row.get("text") or "").rstrip())
            print("--- ここまで ---")
        else:
            print("  （直近の投稿の一覧に見当たらないので、本文は見せられません）")
        print(f"  理由: {reason}")
        print(f"digest: {digest}")
        print(f"この投稿を取り下げるなら: thth retract {account} {args.post_id} --reason "
              f"{json.dumps(reason, ensure_ascii=False)} --confirm {digest}")
        return 1
    if confirm != digest:
        raise RemoteError("confirm_mismatch", reason=f"digest が一致しないので取り下げません（いまの digest は {digest}）",
                          rc=1)
    value = call("retract_request", account, post_id=args.post_id, reason=reason)
    return _shown(args, value, lambda v: _say(f"{account}: 取り下げました post_id {v.get('post_id')}"
                                              + ("（記録の送信は保留）" if v.get("push_pending") else "")))


def _find_post(account, post_id):
    """一段目に本文を見せるため、直近の自分の投稿から探す（見つからなければ None・失敗しても止めない）。"""
    try:
        value = call("posts", account, limit=50)
    except RemoteError:
        return None
    for row in (value.get("posts") if isinstance(value, dict) else None) or []:
        if isinstance(row, dict) and str(row.get("post_id")) == post_id:
            return row
    return None


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
    value = call("mentions", account, limit=_limit(args), since=getattr(args, "since", None) or None,
                 refresh=True if getattr(args, "refresh", False) else None)
    return _shown(args, value, lambda v: threads_read_cli.show_mentions(account, v.get("mentions") or []))


def _topics_search(args, account):
    from . import threads_read_cli
    query = (args.search or "").strip()
    if not query:
        raise RemoteError("invalid_request", reason="--search に語を書いてください", rc=2)
    tag, keep = bool(getattr(args, "tag", False)), bool(getattr(args, "keep", False))
    if keep and not tag:
        raise RemoteError("invalid_request", reason="--keep は --tag と一緒に使います", rc=2)
    # `--since`／`--until`／`--media`（Threads の検索の絞り込み・点検 §B-5）も渡す。黙って落とさない。
    # `--tag`・`--keep`（5.7.0）: トピックで引いた数と時刻だけ。
    value = call("topics_search", account, query=query, limit=_limit(args),
                 recent=True if getattr(args, "recent", False) else None,
                 since=getattr(args, "since", None), until=getattr(args, "until", None),
                 media=getattr(args, "media", None), tag=True if tag else None,
                 keep=True if keep else None)
    if tag:
        return _shown(args, value, threads_read_cli.show_tag)
    return _shown(args, value, lambda v: threads_read_cli.show_search(account, v))


def _thread(args, account):
    """`thth thread <口座> <post_id>`（枝をその場で読む・保存しない・10/15 点検 B-17）。

    長い枝は thth.me の 1 件の上限で後ろが落ちる（`counts.truncated`）。続きは
    `--since <provenance.continue_from>` でもう一度読む。
    """
    from . import thread_read
    max_messages = getattr(args, "max_messages", None)
    value = call("thread_read", account, post_id=args.post_id, since=getattr(args, "since", None),
                 max_messages=None if max_messages == thread_read.DEFAULT_MAX_MESSAGES else max_messages,
                 limit=getattr(args, "limit", None))
    if getattr(args, "json", False):
        _print_json(value)
        return 0
    thread_read._render_human(value)
    follow = (value.get("provenance") or {}).get("continue_from")
    if (value.get("counts") or {}).get("truncated") and follow:
        print(f"  続き: thth thread {account} {args.post_id} --since {follow}")
    return 0


def _where(args, account):
    """`thth where <口座> <語…>`（検索の一覧に自分の履歴を重ねる・保存しない・10/15 点検 B-17）。"""
    from . import where_cli
    words = list(args.targets[1:]) + list(getattr(args, "word", None) or [])
    lexicon = None
    if getattr(args, "lexicon", None):
        try:
            lexicon = where_cli.load_lexicon(args.lexicon)  # 手元のファイルを読んで中身を送る
        except where_cli.WhereError as exc:
            raise RemoteError("invalid_request", reason=str(exc), rc=2) from None
    flags = {name: True if getattr(args, name, False) else None
             for name in ("recent", "exclude_engaged", "aggregate", "suggest", "tag", "keep", "ja",
                          "question", "no_replies")}
    value = call("where_to_appear", account, words=words, limit=getattr(args, "limit", None),
                 since=getattr(args, "since", None), until=getattr(args, "until", None),
                 media=getattr(args, "media", None),
                 max_per_author=getattr(args, "max_per_author", None),
                 also=getattr(args, "also", None), lexicon=lexicon, author=getattr(args, "author", None),
                 **flags)
    if getattr(args, "json", False):
        _print_json(value)
        return 0
    where_cli._render_human(value)
    return 0


def _who(args, account):
    """`thth who <口座> <author_key|@username>`（仮名の履歴・発言の内容は持たない・10/15 点検 B-17）。"""
    from . import who_cli
    if len(args.targets) != 2:
        raise RemoteError("invalid_request", reason="thth who <口座> <author_key|@username>（1 人ずつ）", rc=2)
    target = args.targets[1]
    key = {"username": target[1:]} if target.startswith("@") else {"author_key": target}
    value = call("who_is_this", account, profile=True if getattr(args, "profile", False) else None, **key)
    if getattr(args, "json", False):
        _print_json(value)
        return 0
    who_cli._render_human(value)
    return 0


def _embed(args, account):
    """`thth embed <口座> <URL>`（計画 Meta 全部盛り M3・審査員の道）。"""
    from . import meta_public
    return _shown(args, call("embed", account, url=args.url), meta_public.show_embed)


def _upcoming(args, account):
    """`thth upcoming <口座>`（計画 Meta 全部盛り M3・審査員の道）。"""
    from . import meta_public
    return _shown(args, call("upcoming_events", account), meta_public.show_upcoming)


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


def _inflight(args, account):
    """`thth inflight <口座>`（見る）と `… resolve --not-published|--published <id>`（解く・二段確認）。

    手元の道と同じ見せ方と digest。解いた人は鍵の持ち主（`--by` は読まない）。
    """
    from . import inflight_cli
    if getattr(args, "action", "show") != "resolve":
        value = call("inflight_status", account)
        if getattr(args, "json", False):
            _print_json(value)
            return 0
        if value.get("inflight") is None:
            return _say(f"{account}: inflight はありません")
        info = value["inflight"]
        if "candidates" in value:
            # 4.0 S5 §8.3・監査 低-2: はてなの inflight の一覧の候補（手元の道と同じ文）。
            info = dict(info, candidates_found={"checked": value.get("candidates_checked"),
                                                "error": value.get("candidates_error"),
                                                "op": value.get("candidates_op"),
                                                "candidates": value.get("candidates") or [],
                                                "gone": value.get("candidates_gone")})
        return inflight_cli.show_info(account, info, str(value.get("next") or ""))
    if not getattr(args, "not_published", False) and not getattr(args, "published", None):
        raise RemoteError("decision_required", rc=1)
    decision = "not_published" if args.not_published else "published"
    value = call("inflight_resolve", account, decision=decision,
                 post_id=(args.published or None) if decision == "published" else None,
                 confirm=getattr(args, "confirm", None))
    if value.get("resolved") is not True:
        flag = "--not-published" if decision == "not_published" else f"--published {value.get('post_id')}"
        command = f"thth inflight {account} resolve {flag} --confirm {value.get('digest')}"
        if getattr(args, "json", False):
            _print_json({**value, "command": command})
            return 1
        info = value.get("inflight") or {}
        print(f"解きません（確認の一段目です）: {account} の inflight（{info.get('file')}・{info.get('started')}）")
        if decision == "not_published":
            print("  決めること: 媒体に**出ていない**。次の公開から出せるようになります")
            print("  ⚠ 出ていたのに解くと 2 度出ます。媒体の画面（自分の投稿の一覧）で確かめてから")
        else:
            print(f"  決めること: 媒体に出ていた（post_id {value.get('post_id')}）。記録して解きます")
            if info.get("media"):
                print("  ⚠ 媒体の画面で、その post_id の投稿の画像と本文が原稿どおりかを確かめてから")
                print(f"  {inflight_cli.MEDIA_GRANT_NOTE}")
        print(f"digest: {value.get('digest')}")
        print(f"解くなら: {command}")
        return 1
    return _shown(args, value, lambda v: _say(f"inflight を解きました: {account}（{v.get('resolution')}）"))


def _events(args, account):
    """`thth events <口座> --since …`（出来事の流れ・読むだけ・4.0 の柱 1）。手元と同じ形と見せ方。"""
    from . import events_cli
    value = call("events", account, since=getattr(args, "since", None), after=getattr(args, "after", None),
                 limit=getattr(args, "limit", None))
    return _shown(args, value, lambda v: events_cli.render(v) or 0)


def _summary(args, account):
    """`thth summary <口座>`（数の要約・読むだけ・4.0 の柱 1）。手元と同じ形と見せ方。"""
    from . import summary_cli
    value = call("summary", account)
    return _shown(args, value, lambda v: summary_cli.render(v) or 0)


def _dashboard(args, account):
    """`thth dashboard <口座> [campaign|series <id>]`（5.0 段 2・読むだけ）。手元と同じ形と見せ方。"""
    from . import dashboard_cli
    value = call("dashboard", account, level=getattr(args, "level", None), id=getattr(args, "ident", None),
                 full=True if getattr(args, "full", False) else None)
    if getattr(args, "json", False):
        # 手元と同じく `</` を `<\\/` に（型に貼る答え）。
        print(dashboard_cli.dumps(value, indent=2))
        return 0
    return _shown(args, value, lambda v: dashboard_cli.render(v) or 0)


# --------------------------------------------------------------------------
# 計画 Meta 全部盛り: 審査員の道（doctor・dm・live・page cta・like・edit・reply hide）。中身は VM の
# `thth/server_meta.py` が手元の道と同じ関数で行う。**二段確認の digest は VM が作り VM が照合する**（ここは
# 一段目が返した digest を `--confirm` から渡すだけで、自分では作らない）。`--by` は読まない（鍵の持ち主が
# 記録される・retract と同じ）。`--wait` は読まないので、付けると断る。
# --------------------------------------------------------------------------

def _meta_json(args) -> bool:
    return bool(getattr(args, "json", False) or getattr(args, "as_json", False))


def _meta_stage_one(args, value, lines, again) -> int:
    """一段目: VM が見せた中身と digest を出し、何もしない（rc=1・手元の道と同じ）。"""
    from . import refusals
    refusals.mark_first_stage()
    if _meta_json(args):
        _print_json(value)
        return 1
    for line in lines:
        print(line)
    print(f"digest: {value.get('digest')}")
    print(f"打つなら: {again} --confirm {value.get('digest')}")
    return 1


def _meta_text(args):
    """`--text`・`--text-file`（`-` は標準入力）のどちらか。無ければ None（検査は VM の手元の関数が言う）。"""
    text, path = getattr(args, "text", None), getattr(args, "text_file", None)
    if text is not None and path is not None:
        raise RemoteError("invalid_request", reason="--text と --text-file はどちらか 1 つ", rc=2)
    if text is not None or path is None:
        return text
    try:
        if path == "-":
            return sys.stdin.read()
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        raise RemoteError("invalid_request", reason="本文のファイルを読めません", rc=2) from None


def _quoted(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _meta_doctor(args, account):
    """`thth doctor <口座>`（読み取りの probe だけ・VM の置き場の欄は返らない）。"""
    from . import doctor
    value = call("doctor", account)
    probes = [p for p in value.get("probes") or [] if isinstance(p, dict)]
    if _meta_json(args):
        _print_json(value)
    elif value.get("error"):
        print(f"{account}: {value['error']}")
    else:
        print(f"{account}（{value.get('username')}・user_id={value.get('user_id')}・thth.me の鍵の道）")
        print(doctor.recorded_scopes_line(value.get("scopes_recorded")))
        line = doctor.debug_token_line(probes)
        if line:
            print(line)
        print("")
        for p in probes:
            mark = "○" if p.get("ok") else ("―" if p.get("ok") is None else "×")
            print(f"  {mark} {p.get('label')}（{p.get('permission')}）")
            print(f"      {p.get('detail')}")
        print("")
        print("読み取りだけを試しました。投稿・返信・削除は呼んでいません。")
    if value.get("error") or value.get("auth_only"):
        return 2
    return 1 if any(p.get("ok") is False for p in probes) else 0


def _meta_page_cta(args, account):
    _refuse_unread(args, "page cta", {"wait": "--wait"})
    if args.action == "show":
        value = call("page_cta_show", account)
        if _meta_json(args):
            _print_json(value)
            return 0
        rows = value.get("call_to_actions") or []
        print(f"{account}  行動ボタン {len(rows)} 件")
        for row in rows:
            print(f"  id={row.get('id')}  {row.get('type') or '—'}  {row.get('web_url') or ''}  {row.get('status') or ''}")
        print("  Meta の公開の口は行動ボタンを作る・直すを持たず、消すだけです（thth page cta delete）")
        return 0
    value = call("page_cta_delete", account, cta_id=args.cta_id, confirm=args.confirm)
    if value.get("stage") == 1:
        row = value.get("cta") or {}
        return _meta_stage_one(args, value, [
            f"消しません（確認の一段目です）: {account} 行動ボタン {args.cta_id}",
            f"  種類: {row.get('type') or '—'}  行き先: {row.get('web_url') or '—'}",
            "  Meta の公開の口では作り直せません。消したあとはページの画面で付け直します"],
            f"thth page cta delete {account} {args.cta_id}")
    return _shown(args, value, lambda v: _say(f"消しました: {account} 行動ボタン {v.get('cta_id')}"))


def _meta_live(args, account):
    _refuse_unread(args, "live", {"wait": "--wait"})
    if args.action == "end":
        value = call("live_end", account, live_id=args.live_id, confirm=args.confirm)
        if value.get("stage") == 1:
            live = value.get("live") or {}
            return _meta_stage_one(args, value, [
                f"終えません（確認の一段目です）: {account} ライブ {args.live_id}",
                f"  題: {live.get('title') or '—'}  状態: {live.get('status') or '—'}",
                "  終えると録画が動画としてページに残ります"], f"thth live end {account} {args.live_id}")
        return _shown(args, value, lambda v: _say(f"終えました: {account} ライブ {v.get('live_id')}"))
    value = call("live_create", account, title=args.title, description=args.description, confirm=args.confirm)
    if value.get("stage") == 1:
        return _meta_stage_one(args, value, [
            f"作りません（確認の一段目です）: {account} ライブ配信",
            f"  題  : {value.get('title')}", f"  説明: {value.get('description') or '—'}",
            "  作るとすぐ「配信中」になり、宛先に映像を送るとページに出ます（止め絵・ループは使わないでください）。"
            "終えるのは thth live end"],
            f"thth live create {account} --title {_quoted(value.get('title'))}"
            + (f" --description {_quoted(value['description'])}" if value.get("description") else ""))
    target = value.get("stream_url")
    if isinstance(target, str):
        redact.register_secret(target)  # 配信の鍵を含む（ログ・例外の文に出さない）
    if _meta_json(args):
        _print_json(value)
        return 0
    print(f"作りました: {account} ライブ {value.get('live_id')}")
    if isinstance(target, str):
        print("  配信の宛先（配信の鍵を含みます・VM にも残していません・人に見せないでください）:")
        print(f"  {target}")
    elif "stream_url" in (value.get("not_kept") or []):
        print("  配信の宛先は届けられませんでした（VM に残さないので出し直せません）。"
              "下の命令で終えてから作り直してください")
    else:
        print("  （宛先が返りませんでした・ページの画面で確かめてください）")
    print(f"  終えるとき: thth live end {account} {value.get('live_id')}")
    return 0


def _meta_names(people) -> str:
    return "、".join((p or {}).get("name") or "（名前は返りません）" for p in people or []) or "—"


def _meta_dm(args, account):
    _refuse_unread(args, "dm", {"wait": "--wait"})
    if args.action == "list":
        value = call("dm_list", account)
        if _meta_json(args):
            _print_json(value)
            return 0
        rows = value.get("conversations") or []
        print(f"{account}  受信箱 {len(rows)} 件（本文は出しません・保存しません）")
        for row in rows:
            print(f"  {row.get('updated_time') or '—'}  {_meta_names(row.get('with'))}  会話 {row.get('id')}")
        print(f"  読むとき: thth dm read {account} <会話の id>")
        return 0
    if args.action == "read":
        value = call("dm_read", account, target=args.target, profile=True if args.profile else None)
        if _meta_json(args):
            _print_json(value)
            return 0
        convo = value.get("conversation") or {}
        print(f"{account}  会話 {convo.get('id')}  相手: {_meta_names(convo.get('with'))}（本文は画面に出すだけ・保存しません）")
        profile = value.get("profile")
        if isinstance(profile, dict):
            print(f"  相手の言語 {profile.get('locale') or '—'}・時間帯 "
                  f"{profile.get('timezone') if profile.get('timezone') is not None else '—'}"
                  f"・性別 {profile.get('gender') or '—'}（権限が無い欄は —）")
        for row in sorted(convo.get("messages") or [], key=lambda r: str(r.get("created_time") or "")):
            who = _meta_names([row.get("from")]) if row.get("inbound") else "こちら"
            print(f"  {row.get('created_time') or '—'}  {who}: {row.get('text') or ''}")
        state = value.get("window")
        if isinstance(state, dict):
            print(f"  返せる窓: {state.get('closes_at')} まで（相手の最後の通 {state.get('last_inbound')}）")
        else:
            print("  返せる窓: 閉じています（24 時間を過ぎました。人の担当者なら --human-agent で 7 日の内）")
        print(f"  返すとき: thth dm send {account} {convo.get('id')} --text-file <file>")
        return 0
    text = _meta_text(args)
    value = call("dm_send", account, target=args.target, comment=args.comment, text=text,
                 human_agent=True if args.human_agent else None, confirm=args.confirm)
    if value.get("stage") == 1:
        return _meta_stage_one(args, value, [
            f"送りません（確認の一段目です）: {account} → {value.get('to')}",
            f"  窓: {value.get('closes_at')} まで",
            "--- 送る本文（保存しません） ---", value.get("text") or "", "--- ここまで ---"],
            f"thth dm send {account} " + (f"--comment {args.comment}" if args.comment else str(args.target))
            + " --text-file <同じ本文>" + (" --human-agent" if args.human_agent else ""))
    return _shown(args, value, lambda v: _say(f"送りました: {account}（本文は保存していません）"))


def _meta_reaction(args, account):
    """`thth like|unlike <口座> <投稿>`（1 段・戻せる・手元の道と同じ）。"""
    command = getattr(args, "reaction_command", "like")
    _refuse_unread(args, command, {"wait": "--wait"})
    value = call(command, account, post=args.post)
    word = "いいねの取り消し" if command == "unlike" else "いいね"
    if _meta_json(args):
        _print_json(value)
        return 0
    if value.get("already_done"):
        return _say(f"既に{word}しています（SNS には送っていません）: {account} {value.get('post_id')}")
    return _say(f"{word}しました: {account} {value.get('post_id')}")


def _meta_edit(args, account):
    _refuse_unread(args, "edit", {"wait": "--wait"})
    value = call("edit_post", account, post_id=args.post_id, text=_meta_text(args), reason=args.reason,
                 confirm=args.confirm)
    if value.get("stage") == 1:
        return _meta_stage_one(args, value, [
            f"直しません（確認の一段目です）: {account} post_id {args.post_id}",
            f"  URL : {value.get('url') or '（記録にありません）'}",
            "--- いまの本文（記録） ---", value.get("previous_text") or "",
            "--- 直したあとの本文 ---", value.get("text") or "", "--- ここまで ---"],
            f"thth edit {account} {args.post_id} --text-file <同じ本文のファイル>")
    return _shown(args, value, lambda v: _say(f"直しました: {account} post_id {v.get('post_id')}（{v.get('edited_at')}）"))


def _meta_reply(args, account):
    """`thth reply hide|unhide <口座> <comment_id>`（1 段・戻せる）。`delete` は遠くの道に出していない。"""
    _refuse_unread(args, "reply", {"wait": "--wait", "confirm": "--confirm"})
    value = call("reply_" + args.action, account, comment_id=args.comment_id, reason=args.reason)
    if _meta_json(args):
        _print_json(value)
        return 0
    if value.get("changed") is False:
        state = "隠れています" if args.action == "hide" else "見えています"
        return _say(f"既に{state}（何もしていません）: {account} comment {args.comment_id}")
    word = "隠しました" if args.action == "hide" else "戻しました"
    return _say(f"{word}: {account} comment {args.comment_id}（{value.get('at')}）")


COMMANDS.update({
    "doctor": _meta_doctor, "page cta show": _meta_page_cta, "page cta delete": _meta_page_cta,
    "live create": _meta_live, "live end": _meta_live,
    "dm list": _meta_dm, "dm read": _meta_dm, "dm send": _meta_dm,
    "like": _meta_reaction, "unlike": _meta_reaction, "edit": _meta_edit,
    "reply hide": _meta_reply, "reply unhide": _meta_reply,
})


COMMANDS.update({
    "events": _events, "summary": _summary, "dashboard": _dashboard,
    "inflight": _inflight,
    "send": _send, "schedule": _schedule, "retract": _retract, "bookmark add": _bookmark_add,
    "bookmark delete": _bookmark_delete, "bookmark edit": _bookmark_edit,
    "bookmark batch": _bookmark_batch, "bookmark batch-status": _bookmark_batch_status,
    "bookmark batch-cancel": _bookmark_batch_cancel,
    "posts": _posts, "replies": _replies, "measured": _measured, "collect": _collect,
    "mentions": _mentions, "topics search": _topics_search, "profile": _profile,
    "location search": _location, "account status": _account_status, "account set": _account_set,
    "queue": _queue, "thread": _thread, "where": _where, "who": _who, "embed": _embed, "upcoming": _upcoming,
})


# --------------------------------------------------------------------------
# 広告（設計 広告の資格と遠くの道 2026-11-06 §3・段 4・VM は `thth/server_ads.py`）
# --------------------------------------------------------------------------

# 遠くの道に出す広告の口（読む 9・お金が動かない書く 3）。人向けの画面は手元の `thth ads` と同じ関数。
ADS_COMMANDS = ("ads doctor", "ads show", "ads insights", "ads estimate", "ads catalog list", "ads catalog products",
                "ads leads", "ads network", "ads shop", "ads boost", "ads pause", "ads stop")
# 出さない広告の口（配信を始める・延ばす・止めの印を外す・台帳と鍵を書く）。`ads_remote_unsupported` で断る。
# `ads add` は入れない（新しい名前には台帳がまだ無いので、手元の道の台帳を書く命令を巻き込む）。
ADS_UNSUPPORTED = frozenset(("ads activate", "ads extend", "ads resume", "ads end", "ads budget", "ads watch",
                             "ads auth", "ads collect", "ads accounts", "ads leave"))
UNSUPPORTED = UNSUPPORTED | ADS_UNSUPPORTED


def _ads_targeting(text):
    """`--targeting @ファイル` は手元の機械のファイル。中身を送る（VM のファイルは読ませない）。"""
    if isinstance(text, str) and text.startswith("@"):
        try:
            with open(text[1:], encoding="utf-8") as f:
                return f.read()
        except (OSError, UnicodeDecodeError):
            raise RemoteError("invalid_request", reason="--targeting のファイルを読めません", rc=2) from None
    return text


def _ads_operation(args):
    verb = getattr(args, "ads_verb", "")
    if verb == "catalog":
        return "ads_catalog_" + str(getattr(args, "catalog_verb", ""))
    return "ads_" + str(verb)


def _ads(args, account):
    """`thth ads <verb> <名前>` を thth.me の鍵で VM に頼む。`--by` は送らない（鍵の持ち主が記録される）。"""
    from .ads import cli as ads_cli
    operation = _ads_operation(args)
    # 手元だけの旗（`--offline` は VM の snapshot・`--post` は台帳のページの投稿）は黙って捨てずに断る。
    _refuse_unread(args, "ads " + operation[len("ads_"):].replace("_", " "),
                   {"offline": "--offline", "post": "--post"})
    get = lambda name: getattr(args, name, None)  # noqa: E731
    fields = {
        "ads_insights": lambda: dict(preset=get("preset"), since=get("since"), until=get("until"),
                                     level=get("level"), breakdown=get("by")),
        "ads_estimate": lambda: dict(targeting=_ads_targeting(get("targeting")), goal=get("goal")),
        "ads_catalog_products": lambda: dict(catalog=get("catalog"), limit=get("limit")),
        "ads_leads": lambda: dict(page=get("page"), form=get("form"), limit=get("limit")),
        "ads_network": lambda: dict(since=get("since"), until=get("until")),
        "ads_boost": lambda: dict(object_story_id=get("object_story_id"), lifetime=get("lifetime"),
                                  start=get("start"), end=get("end"), platforms=get("platforms"),
                                  objective=get("objective"), url_tags=get("url_tags"),
                                  utm_campaign=get("utm_campaign"), landing_url=get("landing_url")),
        "ads_pause": lambda: dict(all=True if get("all") else None, ids=list(get("id")) if get("id") else None),
    }.get(operation, dict)()
    value = call(operation, account, **fields)
    show = {"ads_doctor": ads_cli.render_doctor, "ads_show": ads_cli.render_show,
            "ads_insights": ads_cli.render_insights, "ads_estimate": ads_cli.render_estimate,
            "ads_catalog_list": ads_cli.render_catalog_list, "ads_catalog_products": ads_cli.render_catalog_products,
            "ads_leads": ads_cli.render_leads, "ads_network": ads_cli.render_network,
            "ads_shop": ads_cli.render_shop, "ads_boost": ads_cli.render_boost, "ads_pause": ads_cli.render_pause,
            "ads_stop": ads_cli.render_stop}[operation]
    if getattr(args, "json", False):
        print(json.dumps(value, ensure_ascii=False))
    else:
        show(value)
    # rc は手元と同じ: doctor は × が 1 つで 1、書く口は結果が failed・unconfirmed なら 3。
    if operation == "ads_doctor":
        return 1 if any(isinstance(r, dict) and r.get("ok") is False for r in value.get("rows") or []) else 0
    return 3 if value.get("result") in ("failed", "unconfirmed") else 0


COMMANDS.update({key: _ads for key in ADS_COMMANDS})


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
    # `thth page cta …` は子の命令に旗を足す（`thth page --remote cta` の形にしない）。
    page = choices.get("page")
    for action in (page._actions if page is not None else ()):
        if isinstance(action, argparse_subparsers()) and "cta" in action.choices:
            action.choices["cta"].add_argument("--remote", action="store_true",
                                               help="台帳が手元にあっても thth.me の鍵で動かす（遠くの道・thth login のあと）")
    _register_ads(choices.get("ads"))
    for name in ("dm", "live", "edit", "reply"):
        _tell(sub, name, "by", "thth.me の道: 読まない（鍵の持ち主が記録される）")
        _tell(sub, name, "wait", "thth.me の道: 使えない（付けると断る）")
    _tell(sub, "reply", None, "thth.me の道は hide・unhide だけ（delete は手元の道）")
    _tell(sub, "dm", None, "thth.me の道も二段・digest は VM が作って照合する")
    _tell(sub, "live", None, "thth.me の道も二段・配信の宛先は端末に 1 回だけ返り VM に残さない")
    send = choices["send"]
    send.add_argument("--text", default=None, help="本文（1 行の文。長い本文は --text-file か標準入力）")
    send.add_argument("--dry-run", dest="dry_run", action="store_true",
                      help="出さずに lint の結果だけ（手元の道では既定の dry-run と同じ。"
                           "thth.me の道では下書きを 1 本置いて返す・下書きは残る）")
    _tell(sub, "send", None, "thth.me の道: --production が無ければ下書きを置いて lint だけ")
    _tell(sub, "send", "production", "thth.me の道: 台帳が production: true でなければ production_disabled で断る。"
                                     "猶予（hold_minutes）が 1 以上ならその分先の予約になる。返信が急な連投（burst）を超えると"
                                     "口座を止めず、窓が空く時刻の順番待ちに並べる")
    _tell(sub, "send", "confirm", "thth.me の道: 使えない（digest はサーバがその場で突き合わせる・付けると断る）")
    _tell(sub, "send", "media_files", "thth.me の道: 手元の機械のファイル（置いてから出す）")
    _tell(sub, "retract", None, "thth.me の道も二段・--already-gone は使えない")
    _tell(sub, "retract", "confirm", "thth.me の道も同じ（口座・post_id・理由から作る digest）")
    _tell(sub, "retract", "already_gone", "thth.me の道では使えない（付けると断る）")
    _tell(sub, "retract", "by", "thth.me の道: 読まない（鍵の持ち主が記録される）")
    _tell(sub, "schedule", None, "thth.me の道: --text と --at か --draft に --production を付けると予約する"
                                 "（無ければ下書きを置いて lint だけ・過去の時刻は断る）")
    send.add_argument("--json", action="store_true", help="結果を JSON 1 つで（経過の行は stderr）")
    schedule = choices["schedule"]
    schedule.add_argument("--text", default=None, help="遠くの道: 予約する本文（1 行）")
    schedule.add_argument("--text-file", dest="text_file", default=None, help="遠くの道: 予約する本文のファイル")
    schedule.add_argument("--at", default=None, help="遠くの道: 出す時刻（ISO・例 2026-09-27T09:00+09:00）")
    schedule.add_argument("--not-after", dest="not_after", default=None,
                          help="遠くの道: 出さない期限（ISO・+09:00 付き。この時刻を過ぎたら出さない・4.5.6）")
    schedule.add_argument("--draft", default=None, help="遠くの道: 置いた下書き（draft_id）を予約する")
    schedule.add_argument("--topic", default=None)
    schedule.add_argument("--reply-to", dest="reply_to", default=None)
    schedule.add_argument("--media", dest="media_files", action="append",
                          help="遠くの道: 添付のファイル（手元の機械のもの・繰返し可）")
    schedule.add_argument("--alt", dest="alts", action="append", help="遠くの道: 対応する添付の説明（各 --media に必須）")
    schedule.add_argument("--production", action="store_true",
                          help="遠くの道: 予約まで行う（無ければ下書きを置いて lint だけ・send と同じ）")
    schedule.add_argument("--confirm", default=None,
                          help="遠くの道: はてなの口座の予約の 2 段目（1 段目が出した digest・4.0 S1）")
    schedule.add_argument("--approval", default=None, metavar="A261021-25",
                          help="遠くの道: hub の承認の番号（VM の承認の台帳に残す。口座が require_gate: true なら必須・4.4.1）")
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


def _register_ads(ads) -> None:
    """`thth ads <verb>` の遠くの道に出す 12 の口（`ADS_COMMANDS`）にだけ `--remote` を足す。

    出さない口（activate・resume など）の引数は変えない（`tests/test_meta_stage5_1102.py` が resume の引数を縛る）。
    台帳が無く `thth login` 済みなら、旗が無くても `ads_remote_unsupported` で前向きに断る（`route`）。
    """
    if ads is None:
        return
    remote_help = "台帳が手元にあっても thth.me の鍵で動かす（遠くの道・thth login のあと）"
    for action in ads._actions:
        if not isinstance(action, argparse_subparsers()):
            continue
        for verb, parser in action.choices.items():
            children = {"ads " + verb: parser}
            for child in parser._actions:
                if isinstance(child, argparse_subparsers()):   # catalog list|products
                    children = {f"ads {verb} {name}": sub for name, sub in child.choices.items()}
            for key, target in children.items():
                if key not in ADS_COMMANDS:
                    continue
                target.add_argument("--remote", action="store_true", help=remote_help)
                for option in target._actions:
                    if option.dest == "by" and verb in ("boost", "pause", "stop"):
                        option.help = "打った人の名前（thth.me の道: 読まない・鍵の持ち主が記録される）"


def _tell(sub, command, dest, text) -> None:
    """手元の道の help の後ろに thth.me の道での動きを 1 句足す（10/15 調べ_道ごとの意味の差 §5 の 4）。

    `dest` が None なら命令そのものの help（`thth -h` の一覧に出る行）。旗や命令が無ければ何もしない。
    """
    parser = sub.choices.get(command)
    if parser is None:
        return
    if dest is None:
        for action in sub._choices_actions:
            if action.dest == command and action.help and text not in action.help:
                action.help = f"{action.help}。{text}"
        return
    for action in parser._actions:
        if action.dest == dest and text not in (action.help or ""):
            action.help = f"{action.help}。{text}" if action.help else text


def argparse_suppress():
    import argparse
    return argparse.SUPPRESS


def argparse_subparsers():
    import argparse
    return argparse._SubParsersAction
