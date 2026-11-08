"""Google の読む口の共通（OAuth の端末のアプリの形・loopback・PKCE・refresh token の置き場・網の 1 か所）。

`thth gads`（Google 広告）と `thth gsc`（Search Console）が使う。口ごとの違い（scope・置き場・符丁の頭・
断りの型・画面の名前）は `Kit` に持たせ、ここの関数は `Kit` を受け取る。

出典（2026-11-08 に読んだ）: https://developers.google.com/identity/protocols/oauth2/native-app
（転送先は `http://127.0.0.1:<空いている port>`・`code_challenge_method=S256`・`state` を確かめる・
端末のアプリには refresh token がいつも返る・client_secret は端末のアプリでは秘密として扱えないが、
client の JSON にあれば送る）。

- client の JSON（Google の「installed」の形）と refresh token のファイルの置き場は `Kit` が決める。中身は表示しない。
- refresh token のファイルは 600・置き場のディレクトリは作るとき 700。access token は残さない（呼ぶたびに refresh で取る）。
- client_secret・refresh token・access token・認可の code・code_verifier はどこにも出さない
  （`redact.register_secret` に登録し、断りは符丁と静的な語だけ）。

**網に出るのは `http_request` の 1 か所**（`thth.httpsafe` の作法: 別ホストへのリダイレクトを追わない・https だけ）。
口ごとの `wire.default_transport` がこれを呼び、届かないときは口の符丁（`<頭>_unavailable`）に換える。
"""
from __future__ import annotations

import base64
import dataclasses
import hashlib
import http.server
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from . import httpsafe, jst, redact

TIMEOUT = 60
AUTH_TIMEOUT = 300
LOOPBACK_HOST = "127.0.0.1"


class Unreachable(Exception):
    """網に届かない（DNS・接続・時間切れ）。文は持たない（例外の文に URL や本文が入りうるので）。"""


def http_request(method: str, url: str, headers: dict, body: bytes | None, *, timeout: float = TIMEOUT):
    """`(status, body_bytes)`。HTTP の 4xx・5xx も (status, body) で返す。届かなければ `Unreachable`。"""
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with httpsafe.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read(1_000_000)
        except Exception:  # 本文が読めなくても数だけで判断する
            raw = b""
        finally:
            exc.close()
        return exc.code, raw
    except (urllib.error.URLError, OSError, ValueError):
        raise Unreachable() from None


@dataclasses.dataclass(frozen=True)
class Kit:
    """口ごとの違い。`refused(code, detail)` は口の断りの型（`GadsRefused` など）。"""

    prefix: str
    label: str
    scope: str
    client_env: str
    default_client: str
    token_env: str
    default_token: str
    token_schema: str
    refused: Callable[..., Exception]

    def deny(self, suffix: str, detail: str | None = None) -> Exception:
        return self.refused(f"{self.prefix}_{suffix}", detail)


def client_path(kit: Kit) -> str:
    return os.path.expanduser(os.environ.get(kit.client_env) or kit.default_client)


def token_path(kit: Kit) -> str:
    return os.path.expanduser(os.environ.get(kit.token_env) or kit.default_token)


def _https(value) -> bool:
    try:
        httpsafe.validated_url(value)
    except ValueError:
        return False
    return urllib.parse.urlsplit(value).scheme == "https"


def load_client(kit: Kit) -> dict:
    """client の JSON を読む（`installed` の形）。返すのは client_id・client_secret（無ければ None）・auth_uri・token_uri。"""
    path = client_path(kit)
    if not os.path.isfile(path):
        raise kit.deny("client_missing")
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError, UnicodeDecodeError):
        raise kit.deny("client_invalid", "unreadable") from None
    installed = raw.get("installed") if isinstance(raw, dict) else None
    if not isinstance(installed, dict):
        # Web の形（`web`）は loopback の転送先を持たないので、この口では使わない。
        raise kit.deny("client_invalid", "not_installed")
    client = {key: installed.get(key) for key in ("client_id", "client_secret", "auth_uri", "token_uri")}
    if not isinstance(client["client_id"], str) or not client["client_id"]:
        raise kit.deny("client_invalid", "client_id")
    for key in ("auth_uri", "token_uri"):
        if not _https(client[key]):
            raise kit.deny("client_invalid", key)
    if not isinstance(client["client_secret"], str) or not client["client_secret"]:
        client["client_secret"] = None
    redact.register_secret(client["client_secret"])
    return client


# ------------------------------------------------------------------ PKCE と URL

def new_verifier() -> str:
    """43〜128 字の unreserved の字（token_urlsafe の 64 バイトは 86 字・A-Z a-z 0-9 - _）。"""
    verifier = secrets.token_urlsafe(64)
    redact.register_secret(verifier)
    return verifier


def challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorization_url(kit: Kit, client: dict, redirect_uri: str, state: str, verifier: str) -> str:
    params = {
        "client_id": client["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": kit.scope,
        "code_challenge": challenge(verifier),
        "code_challenge_method": "S256",
        "state": state,
        "access_type": "offline",
    }
    return client["auth_uri"] + ("&" if "?" in client["auth_uri"] else "?") + urllib.parse.urlencode(params)


# ------------------------------------------------------------------ loopback の受け口

class _Catch(http.server.BaseHTTPRequestHandler):
    """転送先の 1 回目の GET の query を受けて、短い頁を返す（何も記録に残さない）。"""

    def do_GET(self):  # noqa: N802 （http.server の名前）
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        if self.server.caught is None and ("code" in query or "error" in query or "state" in query):
            self.server.caught = {key: values[0] for key, values in query.items() if values}
            body = f"THTH: {self.server.label}の認可を受け取りました。端末に戻ってください。".encode("utf-8")
            self.send_response(200)
        else:
            body = "THTH: ここは認可の戻り先です。".encode("utf-8")
            self.send_response(404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 受けた URL（code を含む）を stderr に出さない
        return


def loopback_server(kit: Kit | None = None):
    server = http.server.HTTPServer((LOOPBACK_HOST, 0), _Catch)
    server.caught = None
    server.label = kit.label if kit is not None else "Google"
    return server


def wait_for_code(kit: Kit, server, state: str, timeout: float) -> str:
    """戻りを待って code を返す。state が違う・拒まれた・時間切れは断る。"""
    deadline = time.monotonic() + timeout
    while server.caught is None:
        left = deadline - time.monotonic()
        if left <= 0:
            raise kit.deny("auth_timeout")
        server.timeout = min(left, 1.0)
        server.handle_request()
    caught = server.caught
    redact.register_secret(caught.get("code"))
    if not secrets.compare_digest(str(caught.get("state") or ""), state):
        raise kit.deny("auth_state_mismatch")
    if caught.get("error"):
        error = caught["error"]
        raise kit.deny("auth_denied", error if error.replace("_", "").isalnum() else None)
    code = caught.get("code")
    if not code:
        raise kit.deny("auth_denied")
    return code


# ------------------------------------------------------------------ 交換・更新

def _post_form(transport, url: str, form: dict) -> tuple[int, dict | None]:
    body = urllib.parse.urlencode({k: v for k, v in form.items() if v is not None}).encode("ascii")
    status, raw = transport("POST", url, {"Content-Type": "application/x-www-form-urlencoded",
                                          "Accept": "application/json"}, body)
    try:
        value = json.loads(raw.decode("utf-8")) if raw else None
    except (ValueError, UnicodeDecodeError):
        value = None
    return status, value if isinstance(value, dict) else None


def _oauth_error(value) -> str | None:
    error = value.get("error") if isinstance(value, dict) else None
    return error if isinstance(error, str) and error.replace("_", "").isalnum() else None


def exchange(kit: Kit, client: dict, code: str, verifier: str, redirect_uri: str, *, transport) -> dict:
    """認可の code を token に換える。返すのは refresh token と scope だけ（access token は捨てる）。"""
    status, value = _post_form(transport, client["token_uri"], {
        "client_id": client["client_id"], "client_secret": client["client_secret"], "code": code,
        "code_verifier": verifier, "grant_type": "authorization_code", "redirect_uri": redirect_uri})
    if value is not None:
        for key in ("access_token", "refresh_token", "id_token"):
            redact.register_secret(value.get(key))
    if status != 200 or value is None:
        raise kit.deny("auth_exchange_failed", _oauth_error(value) or f"HTTP {status}")
    refresh_token = value.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise kit.deny("no_refresh_token")
    scope = value.get("scope") if isinstance(value.get("scope"), str) else None
    return {"refresh_token": refresh_token, "scope": scope}


def refresh(kit: Kit, client: dict, token: dict, *, transport) -> str:
    """refresh token から access token を取る（呼ぶたびに・残さない）。"""
    status, value = _post_form(transport, client["token_uri"], {
        "client_id": client["client_id"], "client_secret": client["client_secret"],
        "grant_type": "refresh_token", "refresh_token": token["refresh_token"]})
    if value is not None:
        redact.register_secret(value.get("access_token"))
    error = _oauth_error(value)
    if error == "invalid_grant":
        # 取り消された・期限切れ（公開の状態が Testing の project は 7 日）・パスワードの変更など。
        raise kit.deny("token_revoked", error)
    if status != 200 or value is None:
        raise kit.deny("token_refresh_failed", error or f"HTTP {status}")
    access = value.get("access_token")
    if not isinstance(access, str) or not access:
        raise kit.deny("token_refresh_failed", "no_access_token")
    return access


# ------------------------------------------------------------------ token の置き場

def save_token(kit: Kit, value: dict, path: str | None = None) -> str:
    """refresh token を 600 のファイルに書く（置き場のディレクトリは作るとき 700・一時ファイル＋置き換え）。"""
    path = path or token_path(kit)
    directory = os.path.dirname(path) or "."
    if not os.path.isdir(directory):
        os.makedirs(directory, mode=0o700, exist_ok=True)
        os.chmod(directory, 0o700)  # makedirs の mode は umask で削られる
    tmp = os.path.join(directory, f".thth-{kit.prefix}-{secrets.token_hex(8)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return path


def load_token(kit: Kit) -> dict:
    path = token_path(kit)
    if not os.path.isfile(path):
        raise kit.deny("token_missing")
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError, UnicodeDecodeError):
        raise kit.deny("token_invalid") from None
    refresh_token = value.get("refresh_token") if isinstance(value, dict) else None
    if not isinstance(refresh_token, str) or not refresh_token:
        raise kit.deny("token_invalid")
    redact.register_secret(refresh_token)
    return value


# ------------------------------------------------------------------ 通しの流れ

def run(kit: Kit, *, transport, open_browser=True, timeout=AUTH_TIMEOUT, server=None, say=print,
        browser=None) -> dict:
    """`thth <口> auth` の中身。認可の URL を出して（ブラウザも開いてみて）戻りを待ち、refresh token を残す。

    返す値に秘密は入れない（置き場のパス・scope・時刻だけ）。"""
    client = load_client(kit)
    server = server or loopback_server(kit)
    try:
        port = server.server_address[1]
        redirect_uri = f"http://{LOOPBACK_HOST}:{port}"
        state = secrets.token_urlsafe(24)
        verifier = new_verifier()
        url = authorization_url(kit, client, redirect_uri, state, verifier)
        say(f"{kit.label}の認可の頁を開きます。開かなければ、次の URL をブラウザに貼ってください:")
        say(url)
        if open_browser:
            try:
                import webbrowser
                (browser or webbrowser.open)(url)
            except Exception:  # ブラウザが無い端末でも URL を貼れば進める
                pass
        say(f"戻りを {LOOPBACK_HOST}:{port} で待っています（{int(timeout)} 秒まで）…")
        code = wait_for_code(kit, server, state, timeout)
    finally:
        server.server_close()
    got = exchange(kit, client, code, verifier, redirect_uri, transport=transport)
    path = save_token(kit, {"schema": kit.token_schema, "refresh_token": got["refresh_token"], "scope": got["scope"],
                            "created_at": jst.now_jst().isoformat(timespec="seconds")})
    return {"ok": True, "token_path": path, "scope": got["scope"], "mode": "0600"}
