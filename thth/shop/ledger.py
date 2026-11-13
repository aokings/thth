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

鍵のファイルの形（設計 Shopify と STORES §4.1）:

- BASE: `{"access_token": …, "expires_on": "YYYY-MM-DD", "obtained_at": …, "shop_id": …}`（`auth` の欄が無い）。
- Shopify の client: `{"auth": "shopify_client", "client_id", "client_secret", "shop_id", "obtained_at",
  "expires_on": null, "no_expiry": true}`。取り直した約 24 時間の短い鍵は**別のファイル**
  `~/.config/thth/store-<名前>.access`（0600・機械が書く・`access.py`）。
- Shopify の固定の鍵: `{"auth": "shopify_token", "access_token", "shop_id", "obtained_at", "expires_on": null,
  "no_expiry": true}`。

- STORES のクレデンシャル（5.18.0）: `{"auth": "bearer", "access_token", "shop_id", "obtained_at",
  "expires_on"（`--expires` を付けたときの日・無ければ null）, "no_expiry"（`--expires` が無ければ真）}`。

鍵のファイルの `auth` は店の種類の `kinds.KINDS[<種類>]["auths"]` のどれかでないと読まない（BASE の店に STORES の
鍵のファイルを置いた、などの取り違えを `store_token_missing` で止める）。

Shopify の台帳は＋`shop`（myshopify のラベルだけ・`wire.SHOPIFY_LABEL`）。STORES の台帳は＋`public_url`
（店の頁の URL・応答に無いので持ち主が入れる・`check_public_url`。**THTH はこの URL に何も送らない**）。
"""
from __future__ import annotations

import datetime
import json
import os
import re
import urllib.parse

from .. import accounts, jst, redact, secrets_fs
from . import kinds as kinds_mod
from .wire import SHOPIFY_LABEL, StoreRefused

STORES_DIR_ENV = "THTH_STORES_DIR"
# 店の種類は `kinds.py` の表（足すときはそこと口のモジュールに）。
KINDS = kinds_mod.NAMES
# BASE の台帳の欄（種類ごとの欄は `kinds.KINDS[<種類>]["ledger_fields"]`）。
FIELDS = kinds_mod.BASE_FIELDS
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


PUBLIC_URL_MAX = 2048
# 店の頁の URL は ASCII の見える字だけ（下書きの本文にそのまま載るので、全角の空白・幅の無い空白・向きを変える印・
# 行を分ける字を断る。日本語のドメインは punycode で入れてもらう・監査 5.18.0 の 1）。
_PUBLIC_URL_CHARS = re.compile(r"[!-~]+\Z")


def check_public_url(value) -> str | None:
    """STORES の店の頁の URL（持ち主が入れる値）。https だけ・ASCII の見える字だけ・userinfo と query と fragment なし・
    2048 字まで。

    合えばそのまま返し、合わなければ None。THTH はこの URL に何も送らない（下書きの本文に載せるだけ）ので、
    ホストは問わない。
    """
    from .rows import https_url
    if (not isinstance(value, str) or len(value) > PUBLIC_URL_MAX or not _PUBLIC_URL_CHARS.match(value)
            or https_url(value) is None):
        return None
    try:
        parts = urllib.parse.urlsplit(value)
        parts.port  # noqa: B018 — 形の壊れた port はここで ValueError
    except ValueError:
        return None
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return None
    if parts.query or parts.fragment or "?" in value or "#" in value:
        return None
    return value


def fields_for(kind) -> tuple:
    found = kinds_mod.KINDS.get(kind) if isinstance(kind, str) else None
    return found["ledger_fields"] if found else ()


def validate(name: str, data) -> None:
    if (not isinstance(data, dict) or data.get("kind") not in KINDS or set(data) != set(fields_for(data["kind"]))
            or data["store"] != name
            or not isinstance(data["project"], str) or not accounts.name_is_safe(data["project"])
            or not isinstance(data["token"], str) or not data["token"].strip()
            or ("shop" in data and not (isinstance(data["shop"], str) and SHOPIFY_LABEL.fullmatch(data["shop"])))
            or ("public_url" in data and check_public_url(data["public_url"]) is None)
            or not _pos_fields_ok(data)):
        raise StoreRefused("store_ledger_invalid")


def _pos_fields_ok(data: dict) -> bool:
    """実店舗のレジの欄（`location`・`contract`・`publish`）。無い種類は真。"""
    from . import pos
    from .wire import SMAREGI_CONTRACT, SQUARE_ID
    if "publish" in data and not pos.publish_ok(data["publish"]):
        return False
    if "contract" in data and not (isinstance(data["contract"], str) and SMAREGI_CONTRACT.fullmatch(data["contract"])):
        return False
    if "location" in data:
        pattern = SQUARE_ID if data.get("kind") == "square" else LOCATION_DIGITS
        if not (isinstance(data["location"], str) and pattern.fullmatch(data["location"])):
            return False
    return True


# スマレジの店舗 ID（数字 9 桁まで・公式の在庫の欄の説明）。
LOCATION_DIGITS = re.compile(r"[0-9]{1,9}\Z")


def load(name: str) -> dict:
    out = load_raw(name)
    out["token"] = accounts._expand(out["token"])
    return out


def load_raw(name: str) -> dict:
    """台帳を書いたままの形で（鍵の置き場は `~` のまま・置き換える `rewrite` の元）。"""
    path = path_for(name)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise StoreRefused("store_missing") from None
    except (OSError, ValueError):
        raise StoreRefused("store_ledger_invalid") from None
    validate(name, data)
    return dict(data)


def default_token(name: str) -> str:
    """鍵の既定の置き場（`~` のまま）。`thth shop remove` が消してよい鍵は、この置き場のものだけ。"""
    return f"~/.config/thth/store-{check_name(name)}.token"


def default_access(name: str) -> str:
    """Shopify の短い鍵の置き場（`~` のまま・機械が書く・`thth shop remove` が消す）。"""
    return f"~/.config/thth/store-{check_name(name)}.access"


def build(name: str, *, kind: str, project: str, **extra) -> dict:
    """台帳の 1 本。種類ごとの欄（Shopify の `shop` など）は `extra` で渡す（検査は `validate`）。"""
    return {"store": name, "kind": kind, "project": project, "token": default_token(name), **extra}


def rewrite(name: str, data: dict) -> str:
    """今ある台帳を置き換える（出してよい品の表を変えるとき・`thth shop publish`）。0600 で原子的に書く。"""
    validate(name, data)
    path = path_for(name)
    if not os.path.isfile(path) or os.path.islink(path):
        raise StoreRefused("store_missing")
    secrets_fs.atomic_write_json(path, data, mode=0o600)
    return path


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
    if not isinstance(value, dict):
        raise StoreRefused("store_token_missing")
    found = kinds_mod.KINDS.get(cfg.get("kind"))
    if found is not None and value.get("auth") not in found["auths"]:
        # 店の種類と鍵の形が合わない（取り違え）。
        raise StoreRefused("store_token_missing")
    if value.get("auth") in ("shopify_client", "smaregi_client"):
        wanted = ("client_id", "client_secret")
    elif value.get("auth") in (None, "shopify_token", "bearer"):
        wanted = ("access_token",)
    else:
        raise StoreRefused("store_token_missing")
    if not all(isinstance(value.get(key), str) and value[key] for key in wanted):
        raise StoreRefused("store_token_missing")
    for key in wanted:
        redact.register_secret(value[key])
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
    """`{"expires_on", "days_left", "state"}`。state は ok・soon（14 日以内）・expired・unknown・none。

    none は期限の日の無い鍵（Shopify の client secret と固定の鍵・`--expires` を付けなかった STORES の
    クレデンシャル・`no_expiry` が真）。morning にも命令の注意にも
    何も出さない（BASE の unknown「期限を入れ忘れた」とは別）。
    """
    if token.get("no_expiry") is True:
        return {"expires_on": None, "days_left": None, "state": "none"}
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


def write_snapshot(name: str, *, shop_id, items: list, read_at: str, hours=None) -> str:
    """写しを 1 枚に置き換える。持つのは許した欄だけ（`rows.snapshot_row`）。

    `hours` は実店舗のレジ（Square）の曜日ごとの営業の時間（`square.hours_of` の形）。あるときだけ写す（BASE などの
    写しの形は変えない）。
    """
    from . import rows
    value = {"schema": SNAPSHOT_SCHEMA, "read_at": read_at, "shop_id": shop_id,
             "items": {str(row["id"]): rows.snapshot_row(row) for row in items}}
    if hours is not None:
        value["hours"] = hours
    path = snapshot_path(name)
    secrets_fs.atomic_write_json(path, value, mode=0o600)
    return path


def drop_snapshot(name: str) -> bool:
    """写しを捨てる（別の店の鍵に替えたとき・次の `diff` が基準を取り直す）。捨てたら真。"""
    try:
        os.unlink(snapshot_path(name))
    except FileNotFoundError:
        return False
    return True


def now_iso() -> str:
    return jst.iso(jst.now_jst())
