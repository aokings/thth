"""承認のときの読み直し（設計 §4.3 の最後の行・監査 5.15.0 直したほうがよい 2）。

`thth approve` の**確定の段**（`--confirm`・`--confirm-file`）で、頭に `from_store:` がある下書きだけ、その商品を
1 回読み直す。値段・公開・在庫切れ（買えるか）が下書きを作ったときと変わっていれば、前向きに断る
（`store_item_changed`）。見せる段（1 段目）は網に出ない。

- 読むのは店の種類の口（`kinds.py`）の商品の一覧だけ（`wire.py` の網の口・表にある道だけ）。注文は読まない。
- 値段は、下書きの頭に種類ごとの値段（`from_store_variant_prices`・5.18.1）があれば**種類ごと**に比べる: 両方にある
  種類の値段が 1 つでも違えば断る。種類が増えた・減っただけなら値段の断りにせず、知らせを 1 行返して通す
  （ただし値下げの下書きは、種類が減っていれば断る。下がった種類が無くなったのかもしれないので）。両方にある
  種類が 1 つも無ければ断る。頭に無い下書き（5.18.0 まで・BASE・種類を読み切れない商品）は行の値段で比べる。
- 店の台帳が**この機械に無い**ときは読み直せない（店の台帳と鍵は手元の thth にだけ置く決め）。断らずに 1 行の
  知らせを返す（承認の道を止めない・人が `thth shop items` で確かめる）。
- 台帳はあるのに読めない（鍵の期限切れ・BASE に届かない・読み切れずに見つからない）ときは、確かめられない
  ので断る（店の断りの符丁をそのまま言う）。
- **店の下書きかどうかは、頭でなく THTH の側の控えでも知る**（`thth/draft_origin.py`・監査 5.19.0 直したほうがよい 1）。
  頭の `from_store*` の行が落とされていたら、控えの値で補って読み直す（値段の行は「今に合わせて直す」と案内するので、
  頭にある値は頭を使う）。頭の出所（`from_store`）が控えと違えば断る。控えにも頭にも無いのに、名前が `shop draft` の
  付ける形でこの機械の店の名前を持つなら、読み直せないので断る（5.19.0 より前に作った下書きで頭を落とした物）。
"""
from __future__ import annotations

import os
import re

from .. import draft_origin, jst, redact, refusal
from . import COMMAND, kinds, ledger, rows
from .wire import StoreRefused

# 商品の id は整数の店と文字列の店がある（設計 Shopify と STORES §6.2）。比べるのは `str()` 同士。
_ORIGIN = re.compile(r"store/([A-Za-z0-9_.-]{1,64})/item/([A-Za-z0-9]{1,64})\Z")
_PRICE = re.compile(r"-?[0-9]{1,12}\Z")
_VARIANT_PAIR = re.compile(r"([A-Za-z0-9]{1,64})=(-?[0-9]{1,12})\Z")
_MATERIAL = re.compile(r"price (-?[0-9]{1,12}) -> (-?[0-9]{1,12})\Z")


_DETAIL_WORD = re.compile(r"[a-z_]{1,40}\Z")
# `draft.file_name` の付ける形（`<日>-store-<店>-<id>-<種類>.md`）。店の名前は - を含みうるので、店ごとに頭で見る。
_FILE_NAME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}-store-(.+)-[A-Za-z0-9]{1,64}-(?:new|restock|price_down)\.md\Z")


def _named_store(path: str, names: list) -> bool:
    """名前が `shop draft` の付ける形で、この機械の店のどれかの名前を持つか。"""
    matched = _FILE_NAME.fullmatch(os.path.basename(str(path)))
    return bool(matched) and any(matched.group(1).startswith(n + "-") or matched.group(1) == n for n in names)


def _with_record(path: str, front_matter: dict):
    """控えと頭を合わせる。`(使う頭, 断る理由)`。店の下書きでなければ `(None, None)`。"""
    names = ledger.list_names()
    try:
        dirs = [ledger.state_dir_for(n) for n in names]
    except StoreRefused:
        dirs = []
    record, broken = draft_origin.find(dirs, path)
    if record is None:
        if front_matter.get("from_store") is not None:
            return front_matter, None  # 控えの無い機械か、控えを書く前の下書き（今までどおり頭で読み直す）
        if _named_store(path, names) or (broken and _FILE_NAME.fullmatch(os.path.basename(str(path)))):
            return None, _line(path, "store_item_changed", "from_store_missing")
        return None, None
    if "from_store" in front_matter and str(front_matter["from_store"]).strip() != record.get("from_store"):
        return None, _line(path, "store_item_changed", "from_store_mismatch")
    merged = dict(front_matter)
    merged.update({k: v for k, v in record.items() if k.startswith("from_store") and k not in front_matter})
    return merged, None


def _line(path: str, code: str, detail: str | None) -> str:
    # 詳細が静的な語なら、その語の通し方を引く（`by_detail`・Shopify の read_budget など・監査 5.18.1 の 2）。
    word = {"reason": detail} if isinstance(detail, str) and _DETAIL_WORD.fullmatch(detail) else {}
    found = refusal.parts(code, **word) or {}
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


def _expected_variants(front_matter: dict) -> dict | None:
    """頭の `from_store_variant_prices`（`id=円,…`）。無い・形が違えば None（行の値段で比べる）。"""
    raw = front_matter.get("from_store_variant_prices")
    if not isinstance(raw, str) or not raw.strip():
        return None
    out = {}
    for part in raw.strip().split(","):
        matched = _VARIANT_PAIR.fullmatch(part.strip())
        if not matched or matched.group(1) in out:
            return None
        out[matched.group(1)] = int(matched.group(2))
    return out


def _variant_check(path: str, store: str, item_id: str, front_matter: dict, expected: dict, now: dict):
    """種類ごとに比べる。`(断る理由, 知らせ)`。"""
    common = sorted(set(expected) & set(now))
    if not common:
        return _line(path, "store_item_changed", "variants_replaced"), None
    for key in common:
        if now[key] != expected[key]:
            return _line(path, "store_item_changed", f"variant {key} price {expected[key]} -> {now[key]}"), None
    added, removed = len(set(now) - set(expected)), len(set(expected) - set(now))
    if not added and not removed:
        return None, None
    if removed and str(front_matter.get("from_store_change") or "").strip() == "price_down":
        return _line(path, "store_item_changed", f"variants_changed added {added} removed {removed}"), None
    return None, (f"{path}: 商品 {item_id} の種類が、下書きを作ったときから変わっています（増えた {added}・減った {removed}）。"
                  "残っている種類の値段は同じなので、承認は止めません。本文が今の種類に合っているか、"
                  f"thth {COMMAND} items {store} で確かめてから出してください")


def check(path: str, front_matter: dict, *, transport=None) -> tuple[str | None, str | None]:
    """`(断る理由, 知らせ)`。店の下書きでなければ `(None, None)`。"""
    front_matter, problem = _with_record(path, front_matter or {})
    if problem:
        return problem, None
    if front_matter is None:
        return None, None
    value = front_matter.get("from_store")
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
    expected_variants, now_variants = _expected_variants(front_matter), rows.variant_prices(row)
    if expected_variants is not None and now_variants is not None:
        return _variant_check(path, store, item_id, front_matter, expected_variants, now_variants)
    expected = _expected_price(front_matter)
    if expected is not None and row.get("price") != expected:
        return _line(path, "store_item_changed", f"price {expected} -> {row.get('price')}"), None
    return None, None
