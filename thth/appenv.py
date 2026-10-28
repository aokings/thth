"""`~/.config/thth/app.env`（`THREADS_APP_ID`・`THREADS_APP_SECRET`）の読み書き（設計 §3.1）。

**全アカウント共通の 1 本**。**`thth auth`（認可コードから長期トークンを取る道）
だけが要ります。** 管理画面で発行したトークンを `thth token set` で入れる運用なら
無くて構いません（masaru 裁定 2026-09-13。延長 `refresh_access_token` は app secret
を使わない——`thth/oauth.py` を見よ）。

**値は一切出力しません。** 読み（`load_app_env`）も、書き（`run_app_set`）も、
状態の報告（`run_app_show`）も、path と鍵の名前とパーミッションまでしか言わない。
"""
from __future__ import annotations

import getpass
import json
import os
import stat
import sys

from . import secrets_fs

REQUIRED_KEYS = ("THREADS_APP_ID", "THREADS_APP_SECRET")


class AppEnvError(Exception):
    """app.env が無い・壊れている・項目が足りない。"""


def default_path() -> str:
    # THTH_APP_ENV_PATH はテスト用の隔離のみに使う（`accounts.THTH_APP_DIR` と同じ流儀）。
    # 未設定なら実運用どおり ~/.config/thth/app.env を見る。
    return os.environ.get("THTH_APP_ENV_PATH") or os.path.expanduser("~/.config/thth/app.env")


def parse_app_env(text: str) -> dict:
    data = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        data[key.strip()] = value
    return data


def _parse_env_file(path: str) -> dict:
    with open(path, encoding='utf-8') as f:
        return parse_app_env(f.read())


def load_app_env(path: str | None = None, *, log=print) -> tuple[str, str]:
    """`(THREADS_APP_ID, THREADS_APP_SECRET)` を返す。無ければ `AppEnvError`。"""
    path = path or default_path()
    if not os.path.exists(path):
        raise AppEnvError(
            f"app.env が無い: {path}（運用者が ~/.config/thth/app.env に "
            "THREADS_APP_ID・THREADS_APP_SECRET を書く。設計 §3.1・§9）"
        )
    secrets_fs.ensure_mode_600(path, log=log)
    data = _parse_env_file(path)
    missing = [k for k in REQUIRED_KEYS if not data.get(k)]
    if missing:
        raise AppEnvError(f"app.env に項目が足りません: {missing}（{path}）")
    return data["THREADS_APP_ID"], data["THREADS_APP_SECRET"]


# ---------------------------------------------------------------------------
# Facebook ページ（Meta のアプリ「THTH」・ID 1648991573463758）
# ---------------------------------------------------------------------------
#
# **Threads のアプリ ID／secret とは別の組**（手順 `docs/手順_FBページと広告_2026-10-28.md`
# §4 の F1・10/28 に管理画面で見た）。置き場は同じ app.env の別の行にする——
# 全アカウント共通の 1 本、という app.env の性格は Threads と同じなので。
# `META_LOGIN_CONFIG_ID` は Facebook ログイン for Business の「構成」の ID（秘密ではない）。
# 有れば認可の URL に `config_id`、無ければ `scope` を載せる（`auth_facebook.py`）。
META_REQUIRED_KEYS = ("META_APP_ID", "META_APP_SECRET")
META_CONFIG_KEY = "META_LOGIN_CONFIG_ID"


def load_meta_app_env(path: str | None = None, *, log=print) -> tuple[str, str, str | None]:
    """`(META_APP_ID, META_APP_SECRET, META_LOGIN_CONFIG_ID か None)`。足りなければ `AppEnvError`。

    **値は出さない**（エラー文にも鍵の名前と path だけ）。Threads の 2 行は見ない。
    """
    path = path or default_path()
    if not os.path.exists(path):
        raise AppEnvError(
            f"app.env が無い: {path}（運用者が META_APP_ID・META_APP_SECRET を書く。"
            "Facebook ページの `thth auth` に要ります）")
    secrets_fs.ensure_mode_600(path, log=log)
    data = _parse_env_file(path)
    missing = [k for k in META_REQUIRED_KEYS if not data.get(k)]
    if missing:
        raise AppEnvError(f"app.env に項目が足りません: {missing}（{path}・Facebook ページの認可に要ります）")
    config_id = (data.get(META_CONFIG_KEY) or "").strip() or None
    return data["META_APP_ID"].strip(), data["META_APP_SECRET"].strip(), config_id


# ---------------------------------------------------------------------------
# 状態の見立て（`thth doctor` と `thth app show` が共有する）
# ---------------------------------------------------------------------------

ABSENT = "absent"
OK = "ok"
BROKEN = "broken"


def probe(path: str | None = None, *, log=print) -> tuple[str, str | None]:
    """`(状態, 説明)` を返す。状態は `absent` / `ok` / `broken` のどれか。

    **「無い」と「置いたのに使えない」を分ける**（masaru 裁定 2026-09-13）。
    無いのは任意——`thth auth` を使わない運用では正しい状態なので、直せとは
    言わない。置いてあるのに項目が空・読めない、は直すべきもの。
    """
    path = path or default_path()
    if not os.path.exists(path):
        return ABSENT, None
    try:
        load_app_env(path, log=log)
    except AppEnvError as e:
        return BROKEN, str(e)
    except OSError as e:
        # 読めない（パーミッション・壊れた symlink 等）。**中身は出さない。**
        return BROKEN, f"app.env が読めません: {path}（{type(e).__name__}）"
    return OK, None


def describe(path: str | None = None) -> dict:
    """`thth app show` が出す事実だけ。**値は入れない。**

    `mode_ok` は「いま 600 か」であって直した結果ではない——`show` は読むだけで
    パーミッションを書き換えない（`load_app_env` の `ensure_mode_600` と違う）。
    """
    path = path or default_path()
    exists = os.path.exists(path)
    keys_present: list[str] = []
    mode_ok = False
    if exists:
        try:
            mode_ok = stat.S_IMODE(os.stat(path).st_mode) == 0o600
        except OSError:
            mode_ok = False
        try:
            data = _parse_env_file(path)
        except OSError:
            data = {}
        keys_present = [k for k in REQUIRED_KEYS if data.get(k)]
    return {"path": path, "exists": exists, "keys_present": keys_present,
            "mode_ok": mode_ok}


# ---------------------------------------------------------------------------
# `thth app set` / `thth app show`
# ---------------------------------------------------------------------------

def _read_secret(*, stdin: bool, input_func) -> str:
    """App Secret を読む。エコーしない（`thth/oauth.py` `_read_pasted_token` と同じ作法）。

      - `input_func` があればそれ（テスト・注入用）。
      - `--secret-stdin`: 黙って 1 行読む（端末ではないのでどのみち画面に出ない）。
      - 端末: `getpass.getpass()` で表示せずに読む。
      - 端末でないのに `--secret-stdin` が無い: **黙って読まない**。tty を割り
        当てずに `ssh wt 'thth app set ...'` と打つと、手元の画面に secret が
        そのまま出る（remote に tty が無いのでエコーを止められない）。
    """
    if input_func is not None:
        return input_func()
    if stdin:
        return sys.stdin.readline()
    if not sys.stdin.isatty():
        raise AppEnvError(
            "標準入力が端末ではありません。App Secret が画面に出てしまうので読みません。\n"
            "  対話で入れる場合: ssh に -t を付けてください"
            "（例: ssh -t wt '...thth app set --app-id <ID>'）\n"
            "  パイプ・ファイルから渡す場合: --secret-stdin を付けてください")
    return getpass.getpass("Threads の App Secret を貼り付けてください（表示されません）: ")


def render_app_env(app_id: str, app_secret: str, *, keep_from: str | None = None) -> str:
    """Threads の 2 行を書く。`keep_from`（いまの app.env の中身）の**ほかの行は残す**。

    同じ app.env に Facebook ページの `META_*` の行を置くので、`thth app set`
    （Threads）で書き直したときにそれが消えないようにする。
    """
    kept = []
    for line in (keep_from or "").splitlines():
        key = line.partition("=")[0].strip()
        if line.strip() and key not in REQUIRED_KEYS:
            kept.append(line)
    head = f"{REQUIRED_KEYS[0]}={app_id}\n{REQUIRED_KEYS[1]}={app_secret}\n"
    return head + "".join(line + "\n" for line in kept)


def run_app_set(*, app_id: str | None, stdin: bool = False, input_func=None,
                path: str | None = None, log=print, by=None) -> int:
    """Legacy Threads flags retain their spelling; all changes require --by."""
    from . import appconfig
    return appconfig.run('threads',app_id=app_id,secret_stdin=stdin,input_func=input_func,path=path,log=log,by=by)


def run_app_show(*, as_json: bool = False, path: str | None = None, log=print) -> int:
    """`thth app show`。**存在・鍵の名前の有無・パーミッションだけ。値は出さない。**"""
    info = describe(path)
    if as_json:
        log(json.dumps(info, ensure_ascii=False))
        return 0
    log(f"app.env: {info['path']}")
    if not info["exists"]:
        log("  無し（任意。`thth auth` を使うときだけ要ります。置くなら `thth app set`）")
        return 0
    missing = [k for k in REQUIRED_KEYS if k not in info["keys_present"]]
    log("  あり")
    log("  項目: " + ("・".join(info["keys_present"]) if info["keys_present"] else "（1 つも入っていません）"))
    if missing:
        log("  足りない項目: " + "・".join(missing))
    log("  パーミッション: " + ("600" if info["mode_ok"] else "600 ではありません（`thth auth` が読むときに 600 へ直します）"))
    log("  値は表示しません。")
    return 0
