"""accounts/<account>.json の読みと形式検査（設計 §4.2）。"""
from __future__ import annotations

import json
import os

# この thth アプリ repo 自身の場所（thth/ パッケージの 1 つ上）。
# VM では $THTH_ROOT/app がここに一致する。accounts/ は常にここ基準で探す
# （THTH_ROOT が別ディレクトリを指しても、台帳は app repo に commit されたものを使う）。
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REQUIRED_FIELDS = [
    "account", "project", "media", "handle", "repo_dir", "queue_dir",
    "replies_dir", "quiet_hours", "min_interval_hours", "collect_days",
    "hashtags", "stale_days", "env", "token", "ping", "timeout",
    "dry_run_env", "production",
]


class AccountError(Exception):
    """台帳が無い・壊れている・必須項目が足りない。"""


def thth_root() -> str:
    """state・logs の置き場の基準。設計 §3.1 の $THTH_ROOT。

    優先順位:
      1. 環境変数 `THTH_ROOT`（systemd unit が渡す）
      2. **app repo の basename が "app" なら、その親**（VM の `$THTH_ROOT/app` 配置）
      3. それ以外は app repo 自身（Mac 手元・pytest。.gitignore の state/・logs/ と対応）

    2 を足した理由（2026-09-09・最初の本番投稿で踏んだ）: VM で masaru が手で
    `thth send` を打つと `THTH_ROOT` が無いので state が `/srv/thth/app/state/` に、
    timer から走ると unit が渡すので `/srv/thth/state/` に出来ていた。**置き場が
    2 つに割れると、ロックも inflight も別物になる**。§3.7 で「ロックを core の
    入口に置く」と直したのに、パスが割れていては同じ穴が開く（timer が走っている
    最中の手打ちがロックを踏まない）。**同じ機械の上では、呼び方が違っても同じ
    場所を指す**ことをコードで保証する。
    """
    env = os.environ.get("THTH_ROOT")
    if env:
        return env
    if os.path.basename(APP_DIR) == "app":
        return os.path.dirname(APP_DIR)
    return APP_DIR


def app_dir() -> str:
    """`accounts/` を探す基準。`THTH_APP_DIR` 環境変数で上書きできる（テストが
    subprocess 越しに隔離した accounts/ ディレクトリを指すのに使う。単体テストは
    `accounts.APP_DIR` を直接 monkeypatch してもよい。両方に対応するため、ここは
    毎回 `APP_DIR` を読み直す＝モジュール属性の書き換えを拾える）。"""
    return os.environ.get("THTH_APP_DIR") or APP_DIR


def accounts_dir() -> str:
    return os.path.join(app_dir(), "accounts")


def _expand(value):
    if not isinstance(value, str):
        return value
    value = value.replace("$THTH_ROOT", thth_root())
    if value.startswith("~"):
        value = os.path.expanduser(value)
    return value


def load_account(name: str) -> dict:
    """`accounts/<name>.json` を読んで検査する。$THTH_ROOT・~ を展開したコピーを返す。"""
    path = os.path.join(accounts_dir(), f"{name}.json")
    if not os.path.exists(path):
        raise AccountError(f"台帳が無い: {name}（{path}）")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise AccountError(f"台帳が壊れている: {name}（{e}）") from e
    missing = [k for k in REQUIRED_FIELDS if k not in data]
    if missing:
        raise AccountError(f"{name}: 台帳に項目が足りません: {missing}")
    out = dict(data)
    for key in ("repo_dir", "env", "token"):
        out[key] = _expand(out[key])
    return out


def state_dir_for(account_name: str) -> str:
    return os.path.join(thth_root(), "state", account_name)


def logs_dir_for(account_name: str) -> str:
    return os.path.join(thth_root(), "logs", account_name)


def load_token(account_cfg: dict) -> dict | None:
    """`<account>.token` を読む。無ければ None（T1 はここに触れない・秘密を扱わない）。"""
    path = account_cfg.get("token")
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def token_exists(account_cfg: dict) -> bool:
    """`thth run` の事前確認（設計 §3.2・T3a 訂正 2026-09-09）。

    以前の名前は `env_and_token_exist()` で、env ファイルと token の**両方**が
    無ければ False にしていた。**設計が変わって env ファイルは要らなくなった**:
    アプリ ID・シークレットは Meta 管理画面の「ユーザートークン生成ツール」で
    tester ごとに直接発行する運用にしたので（設計 §4.2）、`~/.config/thth/<account>.env`
    を置かない。実際 VM に置いてあるのは `<account>.token` だけ（統括が 2026-09-09
    に確認・L1）。env を必須のままにすると `thth run` は毎回ここで exit 2 になり、
    timer を立てても何も投げない。

    **token だけを必須にする。env は任意**（あれば `load_env()` で読める。中身は
    `HEALTHCHECK_URL` 等・秘密ではない付随情報）。
    """
    token_path = account_cfg.get("token")
    return bool(token_path and os.path.exists(token_path))


def load_env(account_cfg: dict) -> dict:
    """`accounts/<account>.json` の `env`（任意・`HEALTHCHECK_URL` 等）を読む。

    無ければ空の dict（`thth run` を止める理由にはしない・`token_exists()` 参照）。
    """
    path = account_cfg.get("env")
    if not path or not os.path.exists(path):
        return {}
    data: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            data[key] = value
    return data


def list_account_names() -> list[str]:
    d = accounts_dir()
    if not os.path.isdir(d):
        return []
    return sorted(name[:-5] for name in os.listdir(d) if name.endswith(".json"))
