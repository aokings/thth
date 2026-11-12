"""店の種類の表（設計 `docs/設計_ShopifyとSTORESの口_2026-11-12.md` §3）。

`cli.py`・`recheck.py`・`morning.py` は店の口（`base.Client` など）を直に呼ばず、この表を通す。1 つの種類につき:

- `label`: 人に見せる名前（"BASE"）。`key_name`: 鍵の呼び名（"BASE の PAT"・morning の行）。
- `not_by`: 「この道具（THTH）は ○○ が提供するものではありません。」
- `ledger_fields`: 台帳の欄（知らない欄・足りない欄は断る・送り先のホストを台帳で変える欄は無い）。
- `auths`: 受ける鍵の形（鍵のファイルの `auth`。BASE の今のファイルには `auth` が無い）。
- `client(cfg, token, transport)`: `user()` と `items()` を持つ口を返す。
- `parse_item_id(文字列)`: 命令の `--item` と出所の id を、その店の id の形（整数か文字列）に直す。
- `read_cap()`: 1 回の読みで読める件数の上限（読み切れないときの知らせの文に使う）。
- `revoke_plan`・`revoke_done`: `thth shop remove` の、店の側の鍵は THTH が消せないことの 1 行（前と後）。

口のモジュールは呼ばれたときに import する（`ledger.py` がこの表の欄を読むので、輪にしない）。
"""
from __future__ import annotations

import re

from .wire import StoreRefused

_DIGITS = re.compile(r"[0-9]{1,18}\Z")
_ID_TEXT = re.compile(r"[A-Za-z0-9]{1,64}\Z")
BASE_FIELDS = ("store", "kind", "project", "token")
SHOPIFY_FIELDS = BASE_FIELDS + ("shop",)


def _int_id(value) -> int:
    text = str(value)
    if not _DIGITS.fullmatch(text):
        raise StoreRefused("store_args_invalid", "item")
    return int(text)


def _base_client(cfg, token, transport):
    from . import base
    return base.Client(token["access_token"], transport=transport)


def _base_cap() -> int:
    from . import base
    return base.PAGE_LIMIT * base.MAX_PAGES


def _shopify_client(cfg, token, transport):
    from . import shopify
    return shopify.Client(cfg, token, transport=transport)


def _shopify_cap() -> int:
    from . import shopify
    return shopify.PAGE_FIRST * shopify.MAX_PAGES


KINDS = {
    "base": {
        "label": "BASE",
        "key_name": "BASE の PAT",
        "not_by": "この道具（THTH）は BASE が提供するものではありません。",
        "ledger_fields": BASE_FIELDS,
        "auths": (None,),
        "client": _base_client,
        "parse_item_id": _int_id,
        "read_cap": _base_cap,
        "revoke_plan": "BASE の側の PAT は消えません。BASE Developers で店の持ち主が取り消してください",
        "revoke_done": "BASE の側の PAT は消えていません。BASE Developers で店の持ち主が取り消してください",
    },
    # 5.17.0（設計 Shopify と STORES §4）。鍵は 2 つの形（Dev Dashboard のアプリの client id と secret・既にある
    # 管理画面のカスタムアプリの固定の鍵）。どちらも期限の日が無い（`no_expiry`）。
    "shopify": {
        "label": "Shopify",
        "key_name": "Shopify の鍵",
        "not_by": "この道具（THTH）は Shopify が提供するものではありません。",
        "ledger_fields": SHOPIFY_FIELDS,
        "auths": ("shopify_client", "shopify_token"),
        "client": _shopify_client,
        "parse_item_id": _int_id,
        "read_cap": _shopify_cap,
        "revoke_plan": "Shopify の側の鍵は消えません。Dev Dashboard でアプリを店から外すか、secret を作り直してください",
        "revoke_done": "Shopify の側の鍵は消えていません。Dev Dashboard でアプリを店から外すか、secret を作り直してください",
    },
}
NAMES = tuple(KINDS)


def get(kind: str) -> dict:
    found = KINDS.get(kind)
    if found is None:
        raise StoreRefused("store_ledger_invalid")
    return found


def item_text_ok(value) -> bool:
    """命令の `--item` と出所の id の字の形（どの店でも `[A-Za-z0-9]{1,64}`）。"""
    return isinstance(value, str) and bool(_ID_TEXT.fullmatch(value))


def client(cfg: dict, token: dict, transport=None):
    return get(cfg["kind"])["client"](cfg, token, transport)
