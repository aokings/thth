"""遠くの道（設計 3.14.0 §2・§3.4）: 鍵で thth.me 経由で自分の口座を動かす。

`thth login` が鍵を `~/.config/thth/remote.json`（0600・親 0700・`{"url", "key"}`）に置き、
以後は手元の道と**同じ命令・同じ引数・同じ `--json` の形**で動く。

- 鍵は tty（`getpass`）か `--stdin` の 1 行でだけ受ける。**引数では受けない**（shell の履歴に残る）。
- 鍵は出力・ログ・例外の文に出さない（読んだらすぐ `redact.register_secret`）。
- 置き場は `THTH_REMOTE_CONFIG` で差し替えられる（試験用）。
"""
from __future__ import annotations

import getpass
import json
import os
import re
import sys

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


def register(sub) -> None:
    """`thth login` / `thth logout`（`build_parser()` から 1 行で呼ばれる）。"""
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
