"""`thth secret accept <種類> <受付>`: monban の家の秘密の係から呼ばれる 1 本の口（2026-11-27）。

monban の「LLM が中身を見ない秘密の入力口」（monban `docs/設計案_monban_LLMが見ない秘密の入力口_2026-11-27.md`
§5・§10.5）で、人がブラウザの欄に入れた値は家（VM や Mac）で開けられ、家の係の表（`secret-handlers.json`）に
書いた命令の**標準入力**に、欄ごとに 1 行で渡される。この口はその命令の THTH の側。

- 値は標準入力だけで受ける（argv・環境変数では受けない）。欄ごとに 1 行。数が違う・改行（`\\r`）や 0x00 を含む・
  空の欄は `invalid_input` で断る。改行を含む値は行の数が合わなくなるので、同じく断る。
- 中で今の入口（`app set`・`token set`・Bluesky・WordPress・店・はてな）に値を渡す。照合（口座と本人）は今の
  入口のものをそのまま使う（この口で緩めない）。
- 中の断りの符丁を、下の `CODES` の終了コードに直して返す。家の係は終了コードだけを見る（標準出力・標準エラーは
  読まずに捨てる）。それでも、値は標準出力・標準エラーに出さない。中の入口の出力はすべて手元で受けて捨て、
  最後に値を含まない 1 行（種類・受付・答えの符丁）だけを標準出力に出す。
- `<受付>` は値を置く先の名前（口座・主体・店。App Secret は媒体 `threads`、thth.me の鍵は `thth.me`）。家の
  係の表の命令は受付ごとに固定なので、表の 1 行が 1 つの置き場に当たる。
- 「本人の口だけ」の種類（`owner_only`）: App Secret・thth.me の鍵・はてなの consumer。口座の本人を問えず、形しか
  検められない（はてなの consumer は request token が取れるかまで）ので、招待の口（monban 段 3）には出さない印。
  この口自身は扉の種類を知らないので、印を出すだけで断りはしない（`thth secret kinds`）。
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys

# 答えの符丁 → (終了コード, 状態)。家の係の表の `codes` は `{"<終了コード>": "<状態>:<符丁>"}`（monban §10.5）。
# `verified` は「置いた」、`rejected` は「置いていない」。表に無い終了コード（2 の使い方の断り・70 の思わぬ落ち）は
# 家が `rejected:handler_error` に丸める。
CODES = {
    "ok": (0, "verified"),
    "stored_with_warning": (3, "verified"),   # 置いたが、あとの確かめが落ちた（WordPress の入口のホスト・はてなのブログ・
                                              # 置いたあとの控え。鍵のファイルの中身が前と変わったとき・5.32.1）
    "invalid_input": (10, "rejected"),
    "target_unknown": (11, "rejected"),
    "wrong_kind": (12, "rejected"),
    "account_mismatch": (13, "rejected"),
    "identity_unknown": (14, "rejected"),      # 台帳に照合の元（handle など）が無い
    "credential_rejected": (15, "rejected"),
    "not_ready": (16, "rejected"),             # 前の段が無い（はてなの --begin・consumer）
    "busy": (17, "rejected"),
    "refused": (18, "rejected"),
    "invalid_arguments": (19, "rejected"),     # 表の命令の引数が違う（--expires の形など）
}
UNEXPECTED_EXIT = 70   # 表に載せない（家が handler_error に丸める）

KINDS = {
    "app-secret": {"fields": ("app_id", "app_secret"), "target": "媒体（threads）", "owner_only": True},
    "account-token": {"fields": ("token",), "target": "口座（Bluesky 以外）", "owner_only": False},
    "bluesky-app-password": {"fields": ("app_password",), "target": "口座（Bluesky）", "owner_only": False},
    "wordpress-app-password": {"fields": ("application_password",), "target": "主体（WordPress）",
                               "owner_only": False},
    "hatena-consumer": {"fields": ("consumer_key", "consumer_secret"), "target": "主体（はてな）",
                        "owner_only": True},
    "hatena-verifier": {"fields": ("oauth_verifier",), "target": "主体（はてな・先に --begin）", "owner_only": False},
    "shop-token": {"fields": ("token",), "target": "店（BASE・STORES・Square・Shopify の固定の鍵）",
                   "owner_only": False},
    "shop-client": {"fields": ("client_id", "client_secret"), "target": "店（Shopify の client・スマレジ）",
                    "owner_only": False},
    "thth-login-key": {"fields": ("key",), "target": "thth.me（Mac の鍵）", "owner_only": True},
}

# 中の入口の断りの符丁 → 答えの符丁。載らない符丁で rc が 0 でなければ `refused`。
INNER = {
    "token_account_mismatch": "account_mismatch", "bluesky_account_mismatch": "account_mismatch",
    "facebook_page_mismatch": "account_mismatch", "instagram_account_mismatch": "account_mismatch",
    "wp_user_mismatch": "account_mismatch", "hatena_id_mismatch": "account_mismatch",
    "store_shop_mismatch": "account_mismatch",
    "token_handle_missing": "identity_unknown", "bluesky_ledger_identifier_required": "identity_unknown",
    "store_token_unreadable": "identity_unknown",
    "token_unusable": "credential_rejected", "bluesky_session_failed": "credential_rejected",
    "wordpress_auth_rejected": "credential_rejected", "wordpress_permission_missing": "credential_rejected",
    "initiate_failed": "credential_rejected", "token_failed": "credential_rejected",
    "store_token_rejected": "credential_rejected", "store_scope_missing": "credential_rejected",
    "token_unreadable": "invalid_input", "bluesky_app_password_invalid": "invalid_input",
    "consumer_invalid": "invalid_input", "verifier_invalid": "invalid_input", "store_token_empty": "invalid_input",
    "app_set_client_missing_or_invalid": "invalid_input", "invalid_key_format": "invalid_input",
    "wordpress_token_invalid": "invalid_input",
    "verifier_pending_missing": "not_ready", "consumer_missing": "not_ready",
    "principal_token_busy": "busy", "auth_account_changed": "busy", "bluesky_credentials_changed": "busy",
    "app_client_changed": "busy",
    "token_set_wrong_media": "wrong_kind", "bluesky_account_required": "wrong_kind",
    "store_args_invalid": "invalid_arguments", "store_token_expires_past": "invalid_arguments",
}

SHOP_TOKEN_KINDS = ("base", "stores", "square", "shopify")
SHOP_CLIENT_KINDS = ("shopify", "smaregi")
STDIN_MAX = 8192
# 行の頭の符丁。「符丁: 文」の形のほか、行の全体が符丁だけの形も拾う（Bluesky・認可の入口はコロンの無い符丁だけを
# 出す所がある・監査 5.32.0 軽い 3 (a)・5.32.1）。
_CODE_LINE = re.compile(r"([a-z][a-z0-9_]{2,63})(?::|\Z)")


class Refused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def read_values(raw: bytes, count: int) -> list:
    """標準入力の bytes を欄の値に。欄ごとに 1 行（最後の行も `\\n` で終わる）。合わなければ `invalid_input`。"""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > STDIN_MAX:
        raise Refused("invalid_input")
    try:
        text = bytes(raw).decode("utf-8")
    except UnicodeDecodeError:
        raise Refused("invalid_input") from None
    if not text.endswith("\n") or "\r" in text or "\x00" in text:
        raise Refused("invalid_input")
    values = text[:-1].split("\n")
    if len(values) != count or any(not v.strip() for v in values):
        raise Refused("invalid_input")
    return values


class _Sink:
    """中の入口の出力を受けて、値を含みうる文を外に出さずに持つ（答えの符丁を拾うためだけ）。"""

    def __init__(self):
        self.err, self.out = [], []

    def err_line(self, line):
        self.err.append(str(line))

    def out_line(self, line):
        self.out.append(str(line))

    def code(self, *streams) -> str | None:
        """出力の中の符丁。表（`INNER`）に載る符丁を先に、無ければ最初に見えた符丁を返す。"""
        texts = self.err + self.out + [line for s in streams for line in s.getvalue().splitlines()]
        found = [m.group(1) for m in (_CODE_LINE.match(text.strip()) for text in texts) if m]
        return next((c for c in found if c in INNER), found[0] if found else None)

    def wrote(self, *streams) -> bool:
        texts = self.err + self.out + [line for s in streams for line in s.getvalue().splitlines()]
        return any("を書きました" in t for t in texts)


def _check_target(kind, target):
    """受付（置き場）が在り、種類に合うか。値を読む前に見る（読んでから断ると値が無駄に流れる）。"""
    from . import accounts, principals
    if kind == "app-secret":
        if target != "threads":
            raise Refused("target_unknown")   # X の client は JSON の形が違うので、この口では受けない
        return None
    if kind == "thth-login-key":
        if target != "thth.me":
            raise Refused("target_unknown")
        return None
    if kind in ("account-token", "bluesky-app-password"):
        if not accounts.name_is_safe(target):
            raise Refused("target_unknown")
        try:
            cfg = accounts.load_account(target)
        except accounts.AccountError:
            raise Refused("target_unknown") from None
        if (cfg.get("media") == "bluesky") != (kind == "bluesky-app-password"):
            raise Refused("wrong_kind")
        return cfg
    if kind in ("wordpress-app-password", "hatena-consumer", "hatena-verifier"):
        try:
            p = principals.load_principal(target)
        except (principals.PrincipalError, accounts.AccountError, ValueError):
            raise Refused("target_unknown") from None
        if (p.get("kind") == "wordpress") != (kind == "wordpress-app-password"):
            raise Refused("wrong_kind")
        return p
    if kind in ("shop-token", "shop-client"):
        from .shop import ledger
        from .shop.wire import StoreRefused
        try:
            cfg = ledger.load(target)
        except (StoreRefused, ValueError, OSError):
            raise Refused("target_unknown") from None
        if cfg.get("kind") not in (SHOP_TOKEN_KINDS if kind == "shop-token" else SHOP_CLIENT_KINDS):
            raise Refused("wrong_kind")
        return cfg
    raise Refused("target_unknown")


def _call(kind, target, values, cfg, *, by, expires, sink):
    """中の入口に値を渡す。`(rc, 中の符丁 | None)`。"""
    if kind == "app-secret":
        from . import appconfig
        secret = values[1]
        return appconfig.run("threads", app_id=values[0], input_func=lambda: secret, by=by, log=sink.err_line), None
    if kind in ("account-token", "bluesky-app-password"):
        from . import oauth
        value = values[0]
        return oauth.run_token_set(target, force=True, stdin=kind == "bluesky-app-password",
                                   input_func=lambda: value, log=sink.err_line, by=by), None
    if kind == "wordpress-app-password":
        from . import principal_cli
        value = values[0]
        return principal_cli.run_auth_wordpress(target, by=by, force=True, use_stdin=True,
                                                read_stdin=lambda: value, isatty=lambda: False,
                                                out=sink.out_line, err=sink.err_line), None
    if kind in ("hatena-consumer", "hatena-verifier"):
        from . import principal_cli
        lines = list(values)
        return principal_cli.run_auth_hatena_stdin(target, by=by, force=True, consumer=kind == "hatena-consumer",
                                                   read_stdin=lambda: lines.pop(0) if lines else "",
                                                   out=sink.out_line, err=sink.err_line), None
    if kind in ("shop-token", "shop-client"):
        from .shop import cli as shop_cli
        from .shop.wire import StoreRefused
        auth = None
        if cfg.get("kind") == "shopify":
            auth = "client" if kind == "shop-client" else "token"
        args = argparse.Namespace(store=target, auth=auth, expires=expires, by=by, stdin=True, force=False,
                                  json=False)
        old = sys.stdin
        sys.stdin = io.StringIO("".join(v + "\n" for v in values))
        try:
            return shop_cli.cmd_token_set(args), None
        except StoreRefused as exc:
            return 2, exc.code
        finally:
            sys.stdin = old
    if kind == "thth-login-key":
        from . import remote
        args = argparse.Namespace(key=None, url=None, stdin=True, paste=False, no_browser=True)
        old = sys.stdin
        sys.stdin = io.StringIO(values[0] + "\n")
        try:
            return remote.cmd_login(args), None
        finally:
            sys.stdin = old
    raise Refused("target_unknown")


def _key_paths(kind, cfg) -> tuple:
    """種類ごとの鍵のファイルの道（置いたかを見るためだけ）。App Secret・thth.me の鍵は見ない（あとの段が無い）。"""
    if not isinstance(cfg, dict):
        return ()
    if kind in ("account-token", "bluesky-app-password", "wordpress-app-password", "hatena-verifier",
                "shop-token", "shop-client"):
        return tuple(p for p in (cfg.get("token"),) if isinstance(p, str) and p)
    if kind == "hatena-consumer":
        return tuple(p for p in (cfg.get("consumer"), cfg.get("token")) if isinstance(p, str) and p)
    return ()


def _fingerprint(paths) -> tuple:
    """鍵のファイルの中身の sha256（無ければ None）。値は持たず、外にも出さない。

    `stat` でなく中身で見るのは、断ったあとに前の鍵を書き戻す（inode が変わる）入口を「置いた」と取り違えないため。
    """
    import hashlib
    out = []
    for path in paths:
        try:
            with open(path, "rb") as f:
                out.append(hashlib.sha256(f.read()).hexdigest())
        except FileNotFoundError:
            out.append(None)
        except OSError:
            out.append("unreadable")
    return tuple(out)


def accept(kind, target, *, by, expires=None, stdin_bytes=None) -> str:
    """値を受けて置く。答えの符丁（`CODES` の鍵）を返す。値はどこにも出さない。"""
    from . import admin_log
    spec = KINDS.get(kind)
    if spec is None:
        raise Refused("target_unknown")
    try:
        admin_log.actor(by)
    except ValueError:
        raise Refused("invalid_arguments") from None
    if expires is not None and kind != "shop-token":
        raise Refused("invalid_arguments")
    cfg = _check_target(kind, target)
    if stdin_bytes is None:
        stream = getattr(sys.stdin, "buffer", None)
        stdin_bytes = stream.read(STDIN_MAX + 1) if stream is not None else sys.stdin.read(STDIN_MAX + 1).encode()
    values = read_values(stdin_bytes, len(spec["fields"]))
    del stdin_bytes
    from . import redact
    for value in values:
        redact.register_secret(value)
    sink = _Sink()
    out, err = io.StringIO(), io.StringIO()
    # **置いたあとで落ちたら「置いたが確かめが落ちた」**（監査 5.32.0 軽い 4・5.32.1）。鍵を書いたあとの控え
    # （doctor の記録・admin の記録の確かめ）が落ちると、以前は鍵が置かれているのに rejected（70・18）と
    # 答えた。前とあとで鍵のファイルの中身が変わっていれば、置いたものとして `stored_with_warning` に寄せる。
    paths = _key_paths(kind, cfg)
    before = _fingerprint(paths)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc, inner = _call(kind, target, values, cfg, by=by, expires=expires, sink=sink)
    except Exception:
        if paths and _fingerprint(paths) != before:
            return "stored_with_warning"
        raise
    finally:
        del values
    if rc == 0:
        return "ok"
    if kind in ("wordpress-app-password", "hatena-verifier") and rc == 1 and sink.wrote(out, err):
        return "stored_with_warning"
    if paths and _fingerprint(paths) != before:
        return "stored_with_warning"
    inner = inner or sink.code(err, out)
    return INNER.get(inner, "refused")


def run(kind, target, *, by, expires=None, stdin_bytes=None, emit=None) -> int:
    emit = emit or (lambda line: print(line))
    try:
        code = accept(kind, target, by=by, expires=expires, stdin_bytes=stdin_bytes)
    except Refused as exc:
        code = exc.code
    except Exception:  # noqa: BLE001 — 思わぬ落ち。文に値が入りうるので出さない
        emit(json.dumps({"ok": False, "kind": kind, "code": "unexpected"}, ensure_ascii=False))
        return UNEXPECTED_EXIT
    rc, state = CODES[code]
    emit(json.dumps({"ok": state == "verified", "kind": kind, "target": target, "state": state, "code": code},
                    ensure_ascii=False))
    return rc


def handler_codes() -> dict:
    """家の係の表の `codes`（`{"<終了コード>": "<状態>:<符丁>"}`）。どの種類も同じ一覧。"""
    return {str(rc): f"{state}:{code}" for code, (rc, state) in CODES.items()}


def cmd_accept(args) -> int:
    return run(args.kind, args.target, by=args.by, expires=args.expires)


def cmd_kinds(args) -> int:
    rows = [{"kind": kind, "target": spec["target"], "fields": list(spec["fields"]), "owner_only": spec["owner_only"],
             "command": ["<thth の絶対パス>", "secret", "accept", kind, "<受付>", "--by", "<名前>"],
             "codes": handler_codes()} for kind, spec in KINDS.items()]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
        return 0
    for row in rows:
        mark = "・本人の口だけ" if row["owner_only"] else ""
        print(f"{row['kind']}: 受付 {row['target']}・欄 {'・'.join(row['fields'])}{mark}")
    print("終了コード: " + "・".join(f"{rc} {answer}" for rc, answer in handler_codes().items()))
    print("値は標準入力だけで、欄ごとに 1 行。家の係は環境に PATH・HOME・LANG しか渡さないので、VM では表の命令を"
          " /usr/bin/env THTH_ROOT=/srv/thth /srv/thth/app/bin/thth … の形にします")
    return 0


def register(sub) -> None:
    """`build_parser()` に `secret` を足す（cli.py に足すのはこの呼び出しの 1 行だけ）。MCP には出さない。"""
    p = sub.add_parser("secret", help="monban の秘密の入力口から値を受ける口（家の係が呼ぶ・MCP には出さない）")
    verbs = p.add_subparsers(dest="secret_verb", required=True)
    p_accept = verbs.add_parser("accept", help="標準入力の値（欄ごとに 1 行）を今の入口に渡して置く。答えは終了コード")
    p_accept.add_argument("kind", choices=sorted(KINDS))
    p_accept.add_argument("target", metavar="受付", help="置き場の名前（口座・主体・店・threads・thth.me）")
    p_accept.add_argument("--by", required=True)
    p_accept.add_argument("--expires", default=None, metavar="YYYY-MM-DD", help="shop-token の BASE だけ: 鍵の期限の日")
    p_accept.set_defaults(func=cmd_accept)
    p_kinds = verbs.add_parser("kinds", help="受ける種類・欄・終了コードの表（家の係の表を書くため・値は出さない）")
    p_kinds.add_argument("--json", action="store_true")
    p_kinds.set_defaults(func=cmd_kinds)
