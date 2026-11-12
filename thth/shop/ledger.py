"""店の台帳 `stores/<名前>.json` と状態 `store_state/<名前>/`（主体の `thth/principals.py` と同じ作り）。

置き場:

- 台帳: `$THTH_ROOT/stores/<名前>.json`（`THTH_STORES_DIR` で差し替え可・0600）。**口座の台帳（`accounts/`）には
  置かない**（run・collect・doctor・board が読む所に混ぜない）。
- 状態: `$THTH_ROOT/store_state/<名前>/snapshot.json`（0600・最新の 1 枚だけ・履歴を持たない）。
- 鍵: 台帳の `token` の欄のパス（既定 `~/.config/thth/store-<名前>.token`・0600）。**手元の thth に置く形だけ**
  （VM と thth.me の relay に載せる道は作らない）。

台帳の形（知らない欄は断る。送り先のホストを台帳で変える欄は無い）:

```
{"store": "kopicha-base", "kind": "base", "project": "kopicha", "token": "~/.config/thth/store-kopicha-base.token"}
```

鍵のファイルの形: `{"access_token": …, "expires_on": "YYYY-MM-DD", "obtained_at": …, "shop_id": …}`。
"""
from __future__ import annotations

import datetime
import json
import os

from .. import accounts, jst, redact, secrets_fs
from .wire import StoreRefused

STORES_DIR_ENV = "THTH_STORES_DIR"
KINDS = ("base",)  # Shopify は初めの版から外した（2026-11-12）。足すときはここと `base.py` の隣に。
FIELDS = ("store", "kind", "project", "token")
SNAPSHOT_FILE = "snapshot.json"
SNAPSHOT_SCHEMA = "thth.store_snapshot/1"
# 鍵の期限の知らせ（`thth morning` と同じ 14 日前から）。
EXPIRY_NOTICE_DAYS = 14


def stores_dir() -> str:
    env = os.environ.get(STORES_DIR_ENV)
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.join(accounts.thth_root(), "stores")


def check_name(name) -> str:
    if not accounts.name_is_safe(name):
        raise StoreRefused("store_args_invalid", "store_name")
    return name


def path_for(name: str) -> str:
    return os.path.join(stores_dir(), f"{check_name(name)}.json")


def state_dir_for(name: str) -> str:
    return os.path.join(accounts.thth_root(), "store_state", check_name(name))


def list_names() -> list:
    try:
        entries = sorted(os.listdir(stores_dir()))
    except OSError:
        return []
    return [e[:-5] for e in entries if e.endswith(".json") and accounts.name_is_safe(e[:-5])]


def validate(name: str, data) -> None:
    if (not isinstance(data, dict) or set(data) != set(FIELDS) or data["store"] != name
            or data["kind"] not in KINDS
            or not isinstance(data["project"], str) or not accounts.name_is_safe(data["project"])
            or not isinstance(data["token"], str) or not data["token"].strip()):
        raise StoreRefused("store_ledger_invalid")


def load(name: str) -> dict:
    path = path_for(name)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise StoreRefused("store_missing") from None
    except (OSError, ValueError):
        raise StoreRefused("store_ledger_invalid") from None
    validate(name, data)
    out = dict(data)
    out["token"] = accounts._expand(out["token"])
    return out


def build(name: str, *, kind: str, project: str) -> dict:
    return {"store": name, "kind": kind, "project": project, "token": f"~/.config/thth/store-{name}.token"}


def write_new(name: str, data: dict) -> str:
    validate(name, data)
    path = path_for(name)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise StoreRefused("store_name_taken") from None
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path


# ------------------------------------------------------------------ 鍵

def load_token(cfg: dict) -> dict:
    """鍵を読む（値は `redact` に登録する）。無い・読めない・形が違えば `store_token_missing`。"""
    try:
        with open(cfg["token"], encoding="utf-8") as f:
            value = json.load(f)
    except (OSError, ValueError, TypeError):
        raise StoreRefused("store_token_missing") from None
    if not isinstance(value, dict) or not isinstance(value.get("access_token"), str) or not value["access_token"]:
        raise StoreRefused("store_token_missing")
    redact.register_secret(value["access_token"])
    return value


def write_token(cfg: dict, token: dict) -> str:
    secrets_fs.atomic_write_json(cfg["token"], token, mode=0o600)
    return cfg["token"]


def parse_day(value) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(value) if isinstance(value, str) and len(value) == 10 else None
    except ValueError:
        return None


def expiry(token: dict, today: datetime.date) -> dict:
    """`{"expires_on", "days_left", "state"}`。state は ok・soon（14 日以内）・expired・unknown。"""
    day = parse_day(token.get("expires_on"))
    if day is None:
        return {"expires_on": None, "days_left": None, "state": "unknown"}
    left = (day - today).days
    state = "expired" if left < 0 else ("soon" if left <= EXPIRY_NOTICE_DAYS else "ok")
    return {"expires_on": day.isoformat(), "days_left": left, "state": state}


# ------------------------------------------------------------------ 写し（最新の 1 枚だけ）

def snapshot_path(name: str) -> str:
    return os.path.join(state_dir_for(name), SNAPSHOT_FILE)


def read_snapshot(name: str) -> dict | None:
    try:
        with open(snapshot_path(name), encoding="utf-8") as f:
            value = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise StoreRefused("store_ledger_invalid", "snapshot") from None
    if (not isinstance(value, dict) or value.get("schema") != SNAPSHOT_SCHEMA
            or not isinstance(value.get("items"), dict)):
        raise StoreRefused("store_ledger_invalid", "snapshot")
    return value


def write_snapshot(name: str, *, shop_id, items: list, read_at: str) -> str:
    """写しを 1 枚に置き換える。持つのは許した欄だけ（`base.snapshot_row`）。"""
    from . import base
    value = {"schema": SNAPSHOT_SCHEMA, "read_at": read_at, "shop_id": shop_id,
             "items": {str(row["id"]): base.snapshot_row(row) for row in items}}
    path = snapshot_path(name)
    secrets_fs.atomic_write_json(path, value, mode=0o600)
    return path


def now_iso() -> str:
    return jst.iso(jst.now_jst())
