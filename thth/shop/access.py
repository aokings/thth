"""短い鍵の置き場と取り直し（設計 `docs/設計_ShopifyとSTORESの口_2026-11-12.md` §4.2・使うのは Shopify だけ）。

Shopify の Dev Dashboard のアプリは、client id と secret で約 24 時間の短い鍵を取る（client credentials）。短い鍵を
毎回取ると長く生きる secret を毎回網に流すので、取った鍵を置いて使い回す（公式もそう勧める）。

- 置き場: `~/.config/thth/store-<店>.access`（0600・**機械が書く**。人が入れた秘密の `.token` とは別のファイル）。
  形は `{"schema": "thth.store_access/1", "access_token", "expires_at"（ISO）, "scope": [...], "obtained_at"}`。
- 鎖: `store_state/<店>/access.lock`（flock・排他）。待つのは `LOCK_WAIT_SECONDS` まで、取れなければ
  `store_refresh_busy`。鎖の中でもう一度置き場を読む（待つ間に別の thth が取り直していれば、それを使う）。
- 「取り方」（`mint`）は呼ぶ側が関数で渡す（網に出るのは `wire.shopify_token` だけ・ここは網の部品を持たない）。
- 取り直しの鍵が毎回入れ替わる型（STORES の OAuth）は作らない（要るときに足す・設計 §1 の 3）。

`token set` と `remove` も同じ鎖を取る（取り直しの途中で秘密を替えない・消さない）。鎖のファイルは
`store_state/<店>/` の中なので、`remove` の「写しを消す」で一緒に消える。
"""
from __future__ import annotations

import contextlib
import datetime
import fcntl
import json
import os
import time

from .. import accounts, jst, redact, secrets_fs
from . import ledger
from .wire import StoreRefused

SCHEMA = "thth.store_access/1"
LOCK_FILE = "access.lock"
LOCK_WAIT_SECONDS = 20.0
LOCK_POLL_SECONDS = 0.1
# 残りがこれより短ければ取り直す（公式の例は 60 秒前・控えめに 5 分）。
REFRESH_MARGIN_SECONDS = 300
# 置き場に置く期限の上限（`expires_in` はいつも 86399 と公式にある・それより長くは信じない）。
MAX_LIFETIME_SECONDS = 86400
SCOPE_MAX = 300
SLEEP = time.sleep
CLOCK = time.monotonic


def path_for(name: str) -> str:
    return accounts._expand(ledger.default_access(name))


def _now() -> datetime.datetime:
    return jst.now_jst()


def read(name: str) -> dict | None:
    """置き場の短い鍵（形が違えば無いものとする）。値は `redact` に登録する。"""
    try:
        with open(path_for(name), encoding="utf-8") as f:
            value = json.load(f)
    except (OSError, ValueError):
        return None
    if (not isinstance(value, dict) or value.get("schema") != SCHEMA
            or not isinstance(value.get("access_token"), str) or not value["access_token"]
            or not isinstance(value.get("expires_at"), str)):
        return None
    try:
        when = datetime.datetime.fromisoformat(value["expires_at"])
    except ValueError:
        return None
    if when.tzinfo is None:
        return None
    redact.register_secret(value["access_token"])
    return dict(value, _expires=when)


def fresh(value: dict | None, now: datetime.datetime) -> bool:
    return bool(value) and (value["_expires"] - now).total_seconds() > REFRESH_MARGIN_SECONDS


@contextlib.contextmanager
def locked(name: str, *, create: bool = True):
    """店の鎖（flock・排他）。`create` が偽で状態の置き場が無ければ、鎖を取らずに通す（`remove` が置き場を作らない）。"""
    directory = ledger.state_dir_for(name)
    if not create and not os.path.isdir(directory):
        yield
        return
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd = os.open(os.path.join(directory, LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = CLOCK() + LOCK_WAIT_SECONDS
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if CLOCK() >= deadline:
                    raise StoreRefused("store_refresh_busy") from None
                SLEEP(LOCK_POLL_SECONDS)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _scopes(value) -> list:
    if not isinstance(value, str):
        return []
    return [part for part in value.split(",") if part and len(part) <= 64 and part.replace("_", "").isalpha()][:SCOPE_MAX]


def record(name: str, minted: dict, *, now: datetime.datetime | None = None) -> str:
    """取った短い鍵を置き場に書く（atomic・0600）。`minted` は `{"access_token", "expires_in", "scope"}` の形。"""
    token, seconds = minted.get("access_token") if isinstance(minted, dict) else None, None
    if isinstance(minted, dict):
        seconds = minted.get("expires_in")
    if (not isinstance(token, str) or not token or any(c.isspace() for c in token) or len(token) > 4096
            or not isinstance(seconds, int) or isinstance(seconds, bool) or seconds <= 0):
        raise StoreRefused("store_api_changed", "token_response")
    redact.register_secret(token)
    now = now or _now()
    expires = now + datetime.timedelta(seconds=min(seconds, MAX_LIFETIME_SECONDS))
    value = {"schema": SCHEMA, "access_token": token, "expires_at": jst.iso(expires),
             "scope": _scopes(minted.get("scope")), "obtained_at": jst.iso(now)}
    secrets_fs.atomic_write_json(path_for(name), value, mode=0o600)
    return token


def ensure(name: str, mint, *, now: datetime.datetime | None = None) -> str:
    """使える短い鍵を返す。残りが `REFRESH_MARGIN_SECONDS` より長ければ置き場の鍵（網に出ない）。"""
    now = now or _now()
    cached = read(name)
    if fresh(cached, now):
        return cached["access_token"]
    with locked(name):
        cached = read(name)
        if fresh(cached, now):
            return cached["access_token"]
        return record(name, mint(), now=now)


def clear(name: str) -> bool:
    """置き場を消す（鎖は呼ぶ側が持つ）。消したら真。"""
    try:
        os.unlink(path_for(name))
    except FileNotFoundError:
        return False
    return True


def drop(name: str, rejected: str) -> None:
    """置き場の鍵が断られた鍵と同じなら消す（鎖の中で・別の thth が取り直した新しい鍵は消さない）。"""
    with locked(name):
        cached = read(name)
        if cached and cached["access_token"] == rejected:
            clear(name)
