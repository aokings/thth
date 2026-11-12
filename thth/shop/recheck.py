"""承認のときの読み直し（設計 §4.3 の最後の行・監査 5.15.0 直したほうがよい 2）。

`thth approve` の**確定の段**（`--confirm`・`--confirm-file`）で、頭に `from_store:` がある下書きだけ、その商品を
1 回読み直す。値段・公開・在庫切れ（買えるか）が下書きを作ったときと変わっていれば、前向きに断る
（`store_item_changed`）。見せる段（1 段目）は網に出ない。

- 読むのは店の種類の口（`kinds.py`）の商品の一覧だけ（`wire.py` の網の口・表にある道だけ）。注文は読まない。
- 店の台帳が**この機械に無い**ときは読み直せない（店の台帳と鍵は手元の thth にだけ置く決め）。断らずに 1 行の
  知らせを返す（承認の道を止めない・人が `thth shop items` で確かめる）。
- 台帳はあるのに読めない（鍵の期限切れ・BASE に届かない・読み切れずに見つからない）ときは、確かめられない
  ので断る（店の断りの符丁をそのまま言う）。
"""
from __future__ import annotations

import re

from .. import jst, redact, refusal
from . import COMMAND, kinds, ledger, rows
from .wire import StoreRefused

# 商品の id は整数の店と文字列の店がある（設計 Shopify と STORES §6.2）。比べるのは `str()` 同士。
_ORIGIN = re.compile(r"store/([A-Za-z0-9_.-]{1,64})/item/([A-Za-z0-9]{1,64})\Z")
_PRICE = re.compile(r"-?[0-9]{1,12}\Z")
_MATERIAL = re.compile(r"price (-?[0-9]{1,12}) -> (-?[0-9]{1,12})\Z")


def _line(path: str, code: str, detail: str | None) -> str:
    found = refusal.parts(code) or {}
    text = f"{path}: {code}: {found.get('why') or code}" + (f"（{detail}）" if detail else "")
    fix = found.get("fix")
    if isinstance(fix, dict):
        fix = fix.get("text")
    if fix:
        text += " " + fix
    if found.get("better"):
        text += " " + found["better"]
    return redact.redact(text)


def _expected_price(front_matter: dict):
    raw = front_matter.get("from_store_price")
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    if isinstance(raw, str) and _PRICE.fullmatch(raw.strip()):
        return int(raw.strip())
    matched = _MATERIAL.fullmatch(str(front_matter.get("from_store_material") or "").strip())
    return int(matched.group(2)) if matched else None


def check(path: str, front_matter: dict, *, transport=None) -> tuple[str | None, str | None]:
    """`(断る理由, 知らせ)`。店の下書きでなければ `(None, None)`。"""
    value = front_matter.get("from_store")
    if value is None:
        return None, None
    matched = _ORIGIN.fullmatch(str(value).strip())
    if not matched:
        return _line(path, "store_item_changed", "from_store_unreadable"), None
    store, item_id = matched.group(1), matched.group(2)
    try:
        cfg = ledger.load(store)
    except StoreRefused as exc:
        if exc.code == "store_missing":
            return None, (f"{path}: 店 {store} の台帳がこの機械に無いので、商品 {item_id} を読み直していません。"
                          f"承認の前に、店の台帳のある機械で thth {COMMAND} items {store} を打ち、値段・公開・在庫を"
                          "確かめてください")
        return _line(path, exc.code, exc.detail), None
    try:
        token = ledger.load_token(cfg)
        when = ledger.expiry(token, jst.now_jst().date())
        if when["state"] == "expired":
            raise StoreRefused("store_token_expired", when["expires_on"])
        if transport is None:
            from . import cli as store_cli
            transport = store_cli.TRANSPORT
        item_id = str(kinds.get(cfg["kind"])["parse_item_id"](item_id))
        read = kinds.client(cfg, token, transport).items()
    except StoreRefused as exc:
        if exc.code == "store_args_invalid":
            return _line(path, "store_item_changed", "from_store_unreadable"), None
        return _line(path, exc.code, exc.detail), None
    row = next((r for r in read["items"] if str(r["id"]) == item_id), None)
    if row is None:
        return _line(path, "store_item_changed" if read["complete"] else "store_read_incomplete",
                     "item_gone" if read["complete"] else None), None
    if not row.get("visible"):
        return _line(path, "store_item_changed", "unpublished"), None
    if not rows.buyable(row):
        return _line(path, "store_item_changed", "not_buyable"), None
    expected = _expected_price(front_matter)
    if expected is not None and row.get("price") != expected:
        return _line(path, "store_item_changed", f"price {expected} -> {row.get('price')}"), None
    return None, None
