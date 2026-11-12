"""`thth shop …` の店を読む動詞（BASE v2 と Shopify・Instagram の商品タグの動詞は `thth/meta_shop.py`）。

    thth shop add <店> --kind base --project <project> --by <名前>     台帳を 1 本書く（0600）
    thth shop add <店> --kind shopify --project <project> --shop <ラベル> --by <名前>
    thth shop token set <店> --expires YYYY-MM-DD --by <名前> [--stdin]
                                       BASE の PAT を標準入力か隠した入力で受け、店が見えたときだけ保存する
    thth shop token set <店> [--auth client|token] --by <名前> [--stdin]
                                       Shopify: client id と secret の 2 行か、固定の鍵の 1 行。店が見え、
                                       read_products があるときだけ保存する（ほかの権限があれば知らせる）
    thth shop whoami <店> [--json]    鍵の確かめ・店の名前と URL・鍵の期限
    thth shop items <店> [--json]     商品の一覧（許した欄だけ・非公開の商品は id だけ）
    thth shop diff <店> [--accept] [--json]
                                       前の写しとの違い（読むだけ）。--accept で今の読みを写しにする。初回は基準を取るだけ
    thth shop draft <店> --to <口座> (--item <id> | --kind new|restock|price_down) [--json]
                                       違いから queue の下書き（承認されていない形）を 1 本か上限 3 本
    thth shop remove <店> --by <名前> [--confirm D] [--json]
                                       店を外す（二段確認・鍵 → 写し → 台帳の順に消す）

記録（`admin_log`）の動きの名前は `ACTION_PREFIX`（shopread_）で始める: add・token set・remove・draft。
名前・店・時刻・欄の前後だけで、鍵は入れない。

**注文と買い手の情報は読まない。店には書かない（BASE は GET だけ・Shopify は決まった 2 つの問い合わせと鍵を取る口だけ）。
鍵は引数で受けない・出力しない。** 手元の MCP と thth.me の鍵の道には出さない（鍵は手元の thth に置く形だけ）。
この道具は BASE と Shopify が提供するものではありません。
"""
from __future__ import annotations

import argparse
import datetime
import getpass
import json
import os
import sys

from .. import admin_log, jst, redact, refusal
from . import ACTION_PREFIX, COMMAND, diff as diff_mod, draft as draft_mod, kinds, ledger
from . import remove as remove_mod
from . import wire
from .wire import StoreRefused

# 試験の差し込み口（本物の網に出ない）。None なら `wire.default_transport`。
TRANSPORT = None
# 鍵の読み手の差し込み口（試験用）。None なら標準入力か getpass。
READ_SECRET = None
PROMPT = "BASE の個人用アクセストークン（PAT）を貼り付けてください（表示されません）: "


def _refuse(args, exc: StoreRefused) -> int:
    found = refusal.parts(exc.code) or {}
    line = f"{exc.code}: {found.get('why') or exc.code}"
    if exc.detail:
        line += f"（{exc.detail}）"
    payload = {"ok": False, "code": exc.code, "detail": exc.detail, **exc.extra}
    return refusal.emit(args, exc.code, line=redact.redact(line), payload=payload)


def _run(handler):
    def call(args) -> int:
        try:
            return handler(args)
        except StoreRefused as exc:
            return _refuse(args, exc)
    return call


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False))


def _today() -> datetime.date:
    return jst.now_jst().date()


def _by(args) -> str:
    try:
        return admin_log.actor(getattr(args, "by", None))
    except ValueError:
        raise StoreRefused("store_args_invalid", "by") from None


def _log_ready() -> None:
    """変更ログに書けない置き場なら、何かを変える前に断る（`thth leave` と同じ筋）。"""
    try:
        with admin_log.transaction():
            pass
    except (OSError, ValueError, admin_log.AdminLogError):
        raise StoreRefused("store_log_unavailable") from None


def _record(event: str, store: str, by: str, diff: dict, kind: str = "base") -> None:
    """記録に 1 行（動きの名前は `shopread_…`・名前・店・時刻・欄の前後だけ。鍵は入れない）。"""
    try:
        admin_log.append(ACTION_PREFIX + event, store, {"media": kind}, by=by, diff=diff)
    except (OSError, ValueError, admin_log.AdminLogError):
        raise StoreRefused("store_log_unavailable", "after_change") from None


def _open(args):
    """台帳・鍵・期限・口。期限を過ぎていれば打たずに断る。"""
    cfg = ledger.load(args.store)
    token = ledger.load_token(cfg)
    when = ledger.expiry(token, _today())
    if when["state"] == "expired":
        raise StoreRefused("store_token_expired", when["expires_on"])
    return cfg, token, when, kinds.client(cfg, token, TRANSPORT)


def _expiry_note(when) -> str | None:
    # state が none（期限の日の無い鍵・Shopify）と ok は何も言わない。
    if when["state"] == "soon":
        return (f"注意: 鍵の期限は {when['expires_on']}（あと {when['days_left']} 日）です。BASE Developers で新しい PAT を"
                f"発行し、thth {COMMAND} token set で差し替えてください（期限はあとから変えられません）")
    if when["state"] == "unknown":
        return f"注意: 鍵の期限が分かりません。thth {COMMAND} token set --expires で期限の日を持たせてください"
    return None


def _note(args, when) -> None:
    note = _expiry_note(when)
    if note and not getattr(args, "json", False):
        print(note, file=sys.stderr)


# ------------------------------------------------------------------ add・token set

def cmd_add(args) -> int:
    by = _by(args)
    ledger.check_name(args.store)
    from .. import accounts
    if not accounts.name_is_safe(args.project):
        raise StoreRefused("store_args_invalid", "project")
    extra = {}
    if args.kind == "shopify":
        extra["shop"] = shop_label(args.shop)
    elif args.shop is not None:
        raise StoreRefused("store_args_invalid", "shop")
    _log_ready()
    path = ledger.write_new(args.store, ledger.build(args.store, kind=args.kind, project=args.project, **extra))
    _record("added", args.store, by, {"kind": [None, args.kind], "project": [None, args.project],
                                      **({"shop": [None, extra["shop"]]} if extra else {})}, args.kind)
    print(f"台帳を書きました: {path}（0600）")
    if args.kind == "shopify":
        print(f"次に、店の持ち主が Dev Dashboard でアプリを作り、アプリの版で scope は read_products だけを選んで店に入れ、"
              f"Settings の Client ID と Client secret を thth {COMMAND} token set {args.store} --auth client --by <名前> で"
              "渡してください（既にある管理画面のカスタムアプリの固定の鍵なら --auth token）。")
    else:
        print(f"次に、店の持ち主が BASE Developers で読むだけの権限（items.read・user.read）の PAT を発行し、"
              f"thth {COMMAND} token set {args.store} --expires <期限の日> --by <名前> で渡してください。")
    print(kinds.get(args.kind)["not_by"])
    return 0


def shop_label(value) -> str:
    """`--shop` を myshopify のラベルに。`xxx.myshopify.com` は後ろを落とす（断らない）。独自ドメインや URL は断る。"""
    text = (value or "").strip().lower() if isinstance(value, str) else ""
    if text.endswith(wire.SHOPIFY_SUFFIX):
        text = text[:-len(wire.SHOPIFY_SUFFIX)]
    if not wire.SHOPIFY_LABEL.fullmatch(text):
        raise StoreRefused("store_args_invalid", "shop")
    return text


def _read_secret(args, prompt=PROMPT) -> str:
    if READ_SECRET is not None:
        return READ_SECRET()
    if args.stdin:
        return sys.stdin.readline()
    if not sys.stdin.isatty():
        raise StoreRefused("store_token_input_unsafe")
    return getpass.getpass(prompt)


def _secret_line(args, prompt=PROMPT) -> str:
    value = (_read_secret(args, prompt) or "").strip()
    if not value or any(c.isspace() for c in value) or len(value) > 4096:
        raise StoreRefused("store_token_empty")
    redact.register_secret(value)
    return value


def cmd_token_set(args) -> int:
    by = _by(args)
    cfg = ledger.load(args.store)
    if cfg["kind"] == "shopify":
        return _token_set_shopify(args, cfg, by)
    if args.auth is not None:
        raise StoreRefused("store_args_invalid", "auth")
    day = ledger.parse_day(args.expires)
    if day is None:
        raise StoreRefused("store_args_invalid", "expires")
    if day < _today():
        raise StoreRefused("store_token_expires_past", day.isoformat())
    _log_ready()
    value = _secret_line(args)
    shop = kinds.client(cfg, {"access_token": value}, TRANSPORT).user()
    try:
        before = ledger.load_token(cfg)
    except StoreRefused:
        before = None
    if before and before.get("shop_id") and before["shop_id"] != shop["shop_id"] and not args.force:
        raise StoreRefused("store_shop_mismatch")
    ledger.write_token(cfg, {"access_token": value, "expires_on": day.isoformat(), "obtained_at": ledger.now_iso(),
                             "shop_id": shop["shop_id"]})
    dropped = _drop_other_shop(args.store, shop["shop_id"])
    _record("token_set", args.store, by,
            {"shop_id": [before.get("shop_id") if before else None, shop["shop_id"]],
             "expires_on": [before.get("expires_on") if before else None, day.isoformat()],
             "replaced": [None, before is not None], "snapshot_dropped": [None, dropped]}, cfg["kind"])
    print(f"保存しました: {cfg['token']}（600）・店 {shop['shop_name'] or '（名前なし）'}・鍵の期限 {day.isoformat()}")
    if dropped:
        print(f"店が替わったので、前の店の写しを捨てました。次の thth {COMMAND} diff {args.store} が基準を取り直します")
    note = _expiry_note(ledger.expiry({"expires_on": day.isoformat()}, _today()))
    if note:
        print(note, file=sys.stderr)
    return 0


def _drop_other_shop(store: str, shop_id) -> bool:
    """別の店の鍵に替えたら、前の店の写しを捨てる（次の diff が基準を取り直す・監査 5.15.0 の 4）。"""
    try:
        snapshot = ledger.read_snapshot(store)
    except StoreRefused:
        snapshot = None  # 壊れた写しは diff が断る（ここでは鍵を置き換えたことだけを確かにする）
    return bool(snapshot and snapshot.get("shop_id") != shop_id and ledger.drop_snapshot(store))


PROMPT_CLIENT_ID = "Shopify の Dev Dashboard の Client ID を貼り付けてください（表示されません）: "
PROMPT_CLIENT_SECRET = "Shopify の Dev Dashboard の Client secret を貼り付けてください（表示されません）: "
PROMPT_SHOPIFY_TOKEN = "Shopify のカスタムアプリの Admin API のアクセストークンを貼り付けてください（表示されません）: "


def scope_note(shop: dict) -> str | None:
    """鍵に read_products のほかの権限があれば、前向きに知らせる 1 行（設計 §4.4）。

    **割れる所（朝に覆せる）**: 書く・注文・お客さんの権限があれば保存を断る案もある。覆すなら、ここの 1 か所を
    「知らせる」から `store_scope_excess` の断りに替える（`_token_set_shopify` がこれを呼ぶ所）。
    """
    extra = shop.get("scopes_extra") or []
    if not extra:
        return None
    return (f"この鍵は read_products のほかに {len(extra)} つの権限を持っています（{'・'.join(extra)}）。"
            "THTH が Shopify に送るのは、鍵を取る求めと、店の名前と商品を読む決まった 2 つの問い合わせだけです。"
            "鍵が漏れたときの害を小さくするため、Dev Dashboard でアプリの版の scope を read_products だけにして"
            "店に入れ直すと安心です。")


def _token_set_shopify(args, cfg, by) -> int:
    """Shopify の鍵（client id と secret か、固定の鍵）。店が見え、read_products があるときだけ保存する。"""
    from . import access, shopify
    if args.expires is not None:
        raise StoreRefused("store_args_invalid", "expires")
    auth = args.auth or "client"
    _log_ready()
    if auth == "client":
        token = {"auth": "shopify_client", "client_id": _secret_line(args, PROMPT_CLIENT_ID),
                 "client_secret": _secret_line(args, PROMPT_CLIENT_SECRET)}
    else:
        token = {"auth": "shopify_token", "access_token": _secret_line(args, PROMPT_SHOPIFY_TOKEN)}
    client = shopify.Client(cfg, token, transport=TRANSPORT, cache=False)
    with access.locked(args.store):
        shop = client.user()
        try:
            before = ledger.load_token(cfg)
        except StoreRefused:
            before = None
        if before and before.get("shop_id") and before["shop_id"] != shop["shop_id"] and not args.force:
            raise StoreRefused("store_shop_mismatch")
        access.clear(args.store)
        ledger.write_token(cfg, {**token, "shop_id": shop["shop_id"], "obtained_at": ledger.now_iso(),
                                 "expires_on": None, "no_expiry": True})
        if client.minted is not None:
            access.record(args.store, client.minted)
    dropped = _drop_other_shop(args.store, shop["shop_id"])
    _record("token_set", args.store, by,
            {"shop_id": [before.get("shop_id") if before else None, shop["shop_id"]],
             "auth": [before.get("auth") if before else None, token["auth"]],
             "replaced": [None, before is not None], "snapshot_dropped": [None, dropped],
             "scopes_extra": [None, len(shop["scopes_extra"])]}, cfg["kind"])
    print(f"保存しました: {cfg['token']}（600）・店 {shop['shop_name'] or '（名前なし）'}（{shop['shop_id']}）・"
          "鍵の期限の日はありません")
    if client.minted is not None:
        print(f"  取り直した約 24 時間の鍵も {access.path_for(args.store)}（600）に置きました（切れる前に THTH が取り直します）")
    if dropped:
        print(f"店が替わったので、前の店の写しを捨てました。次の thth {COMMAND} diff {args.store} が基準を取り直します")
    note = scope_note(shop)
    if note:
        print(note)
    return 0


# ------------------------------------------------------------------ 読む口

def cmd_whoami(args) -> int:
    cfg, _token, when, client = _open(args)
    shop = client.user()
    not_by = kinds.get(cfg["kind"])["not_by"]
    value = {"store": args.store, "kind": cfg["kind"], **shop, "token": when, "notice": not_by}
    if args.json:
        _print(value)
        return 0
    print(f"{args.store}: {shop['shop_name'] or '（名前なし）'}  {shop['shop_url']}"
          f"（公開 {'はい' if shop['able_to_business'] else 'いいえ' if shop['able_to_business'] is False else '不明'}）")
    if when["state"] == "none":
        print("  鍵の期限: なし（期限の日の無い鍵）")
    else:
        print(f"  鍵の期限: {when['expires_on'] or '不明'}"
              + (f"（あと {when['days_left']} 日）" if when["days_left"] is not None else ""))
    note = scope_note(shop)
    if note:
        print(f"  {note}")
    print(f"  {not_by}")
    _note(args, when)
    return 0


def _incomplete_note(read, cfg) -> str | None:
    if read["complete"]:
        return None
    return (f"全部は読めていません（{read['pages']} 頁・{kinds.get(cfg['kind'])['read_cap']()} 件の上限）。"
            "消えた・売り切れは言わず、写しも進めません")


def _stock_text(stock) -> str:
    return "—（数えない）" if stock is None else str(stock)


def cmd_items(args) -> int:
    cfg, _token, when, client = _open(args)
    read = client.items()
    value = {"store": args.store, "complete": read["complete"], "pages": read["pages"], "items": read["items"],
             "token": when, "note": _incomplete_note(read, cfg)}
    if args.json:
        _print(value)
        return 0
    for row in read["items"]:
        if not row["visible"]:
            print(f"{row['id']}  （非公開）")
            continue
        price = "—" if row["price"] is None else f"{row['price']:,} 円"
        print(f"{row['id']}  {row['name']}  {price}  在庫 {_stock_text(row['stock'])}  {row['sales_status']}")
    print(f"{len(read['items'])} 件" + (f"・{value['note']}" if value["note"] else ""))
    _note(args, when)
    return 0


def _snapshot_for(store: str, token: dict):
    """今の鍵の店の写し。写しの店（shop_id）が鍵の店と違えば、捨てて無いものとする（基準の取り直し）。"""
    snapshot = ledger.read_snapshot(store)
    if snapshot and snapshot.get("shop_id") and token.get("shop_id") and snapshot["shop_id"] != token["shop_id"]:
        ledger.drop_snapshot(store)
        return None, True
    return snapshot, False


def _read_and_compare(args, client, token):
    read = client.items()
    snapshot, switched = _snapshot_for(args.store, token)
    changes = diff_mod.compare(snapshot["items"], read["items"], complete=read["complete"]) if snapshot else []
    return read, snapshot, changes, switched


def cmd_diff(args) -> int:
    cfg, token, when, client = _open(args)
    read, snapshot, changes, switched = _read_and_compare(args, client, token)
    read_at = ledger.now_iso()
    baseline = snapshot is None
    if (baseline or args.accept) and not read["complete"]:
        raise StoreRefused("store_read_incomplete", pages=read["pages"])
    accepted = False
    if baseline or args.accept:
        ledger.write_snapshot(args.store, shop_id=token.get("shop_id"), items=read["items"], read_at=read_at)
        accepted = True
    value = {"store": args.store, "read_at": read_at, "since": snapshot.get("read_at") if snapshot else None,
             "complete": read["complete"], "pages": read["pages"], "baseline": baseline, "changes": changes,
             "accepted": accepted, "token": when, "note": _incomplete_note(read, cfg), "shop_switched": switched}
    if args.json:
        _print(value)
        return 0
    if switched:
        print(f"{args.store}: 写しが今の鍵の店と別の店のものだったので、捨てて基準を取り直しました")
    if baseline:
        print(f"{args.store}: 基準を取りました（{len(read['items'])} 件）。違いはまだありません。次からの thth {COMMAND} diff が"
              "この写しと比べます")
        _note(args, when)
        return 0
    for change in changes:
        extra = f"（{change['before']} → {change['after']} 円）" if "before" in change else ""
        print(f"{change['kind']:<12} {change['id']}  {change.get('name') or ''}{extra}")
    print(f"違い {len(changes)} 件（{value['since']} の写しと比べて）"
          + ("・写しを今の読みに進めました" if accepted else f"・写しは進めていません（--accept で進めます）")
          + (f"・{value['note']}" if value["note"] else ""))
    _note(args, when)
    return 0


def cmd_draft(args) -> int:
    if (args.item is None) == (args.kind is None):
        raise StoreRefused("store_args_invalid", "item_or_kind")
    # 下書きは承認されていない形なので --by は任意（無ければ THTH_ACTOR か命令の名前で記録する）。
    by = _by(argparse.Namespace(by=getattr(args, "by", None) or os.environ.get("THTH_ACTOR")
                                or f"thth {COMMAND} draft"))
    cfg, token, when, client = _open(args)
    # `--item` は字の形だけ見て受け、店の種類の id の形（整数か文字列）にここで直す。
    item = kinds.get(cfg["kind"])["parse_item_id"](args.item) if args.item is not None else None
    account = draft_mod.load_account(args.to)
    queue = draft_mod.queue_dir(account)
    shop = client.user()
    if shop["able_to_business"] is False:
        # 店そのものが公開前なら、見た人が買えないお知らせになるので作らない（監査 5.15.0 の 6）。
        raise StoreRefused("store_not_open")
    read, snapshot, changes, _switched = _read_and_compare(args, client, token)
    if snapshot is None:
        raise StoreRefused("store_draft_no_baseline")
    picked = draft_mod.choose(changes, read["items"], item=item, kind=args.kind)
    if not picked:
        raise StoreRefused("store_draft_nothing")
    since = snapshot.get("read_at")
    for change, row in picked:
        # 写しを進めていない間に、同じ違いから 2 本目を作らない（日をまたいでも・監査 5.15.0 の 3）。
        found = draft_mod.same_change_exists(queue, args.store, row["id"], change, since)
        if found:
            raise StoreRefused("store_draft_exists", "same_change")
    _log_ready()
    now = jst.now_jst()
    read_at = jst.iso(now)
    written = []
    for change, row in picked:
        text = draft_mod.text_for(args.store, args.to, account["media"], change, row, shop_url=shop["shop_url"],
                                  read_at=read_at, publish_at=draft_mod.publish_at(now), since=since)
        name = draft_mod.file_name(args.store, row["id"], change["draft_kind"], now)
        written.append({"id": row["id"], "kind": change["draft_kind"],
                        "path": draft_mod.write(queue, name, text)})
    _record("drafted", args.store, by, {"to": [None, args.to], "items": [[], [row["id"] for row in written]],
                                        "kinds": [[], [row["kind"] for row in written]]}, cfg["kind"])
    notes = ["status は draft です（承認されていません）。本文は素の 1〜2 行なので、出す前に整えて thth approve に通してください"]
    if any(draft_mod.url_kind(row) == "shop" for _change, row in picked):
        notes.append("商品頁の URL は組み方を公式で確かめられなかったので、店の URL を載せました（from_store_url: shop）")
    notes += ["画像は載せていません（from_store_image: none）",
              f"写しは進めていません。済んだら thth {COMMAND} diff {args.store} --accept で進めます"]
    if account.get("project") != cfg["project"]:
        notes.append(f"注意: 口座 {args.to} の project（{account.get('project')}）が店の project（{cfg['project']}）と"
                     "違います。出す先が合っているか確かめてください")
    value = {"store": args.store, "account": args.to, "drafts": written, "notes": notes, "token": when}
    if args.json:
        _print(value)
        return 0
    for row in written:
        print(f"下書きを書きました: {row['path']}（{row['kind']}・商品 {row['id']}）")
    for line in notes:
        print(f"  {line}")
    _note(args, when)
    return 0


# ------------------------------------------------------------------ 外す（二段確認）

def _remove_lines(found) -> list:
    words = {"token": "鍵", "access": "短い鍵", "state": "写し", "ledger": "台帳"}
    lines = [f"  {words[t['category']]}: {t['path']}" for t in found["targets"]]
    for kept in found["kept"]:
        lines.append(f"  残す: {words[kept['category']]} {kept['path']}（既定の置き場でないので、THTH は消しません。"
                     "店の鍵なら手で消してください）")
    return lines


def _kind_of(store: str) -> str:
    """店の種類（台帳が読めなければ base・外すときの文と記録の媒体に使う）。"""
    try:
        return ledger.load(store)["kind"]
    except StoreRefused:
        return "base"


def _revoke_note(store: str, *, done: bool) -> str:
    """店の側の鍵は THTH が消せないことの 1 行（店の種類ごと・台帳が読めなければ BASE の文）。"""
    return kinds.get(_kind_of(store))["revoke_done" if done else "revoke_plan"]


def cmd_remove(args) -> int:
    by = _by(args)
    found = remove_mod.plan(args.store)
    kind = _kind_of(args.store)   # 台帳を消す前に読む（記録の媒体・監査 5.17.0 直したほうがよい 4）
    revoke_done = _revoke_note(args.store, done=True)
    if not found["targets"] and not found["kept"]:
        raise StoreRefused("store_missing")
    digest = remove_mod.digest(found)
    payload = {"store": args.store, "targets": found["targets"], "kept": found["kept"], "by": by}
    if not args.confirm:
        if args.json:
            _print({"ok": False, "stage": 1, "digest": digest, **payload})
            return 1
        print(f"消しません（確認の一段目です）: 店 {args.store}。二段目で次の順に消します")
        for line in _remove_lines(found):
            print(line)
        print("  " + _revoke_note(args.store, done=False))
        print(f"  消すなら: thth {COMMAND} remove {args.store} --by {json.dumps(by, ensure_ascii=False)} "
              f"--confirm {digest}")
        return 1
    if args.confirm != digest:
        raise StoreRefused("store_remove_unconfirmed", digest)
    _log_ready()
    done = remove_mod.execute(found)
    _record("removed", args.store, by, {"deleted": [[], done], "kept": [[], [k["category"] for k in found["kept"]]]},
            kind)
    if args.json:
        _print({"ok": True, "deleted": done, **payload})
        return 0
    words = {"token": "鍵", "access": "短い鍵", "state": "写し", "ledger": "台帳"}
    for target in found["targets"]:
        if target["category"] in done:
            print(f"消しました: {words[target['category']]} {target['path']}")
    for line in _remove_lines({"targets": [], "kept": found["kept"]}):
        print(line)
    print(revoke_done)
    return 0


# ------------------------------------------------------------------ 登録

def _item_id(value: str) -> str:
    """字の形（英数字 64 字まで）だけ見る。店の id の形（BASE は数字）は台帳を読んでから直す（`kinds.parse_item_id`）。"""
    if not kinds.item_text_ok(value):
        raise argparse.ArgumentTypeError("商品の id は英数字です（thth shop items に出ます）")
    return value


PARENT_HELP = "店（店を読む: BASE・Shopify）と Instagram の商品タグ"
PARENT_DESCRIPTION = """店を読む（BASE など）: 第 1 引数は店の名前（thth shop add で書いた台帳の名前）
    thth shop add <店> --kind base --project <project> --by <名前>
    thth shop add <店> --kind shopify --project <project> --shop <ラベル> --by <名前>
                                                                          ラベルは <ここ>.myshopify.com の <ここ>
    thth shop token set <店> --expires YYYY-MM-DD --by <名前> [--stdin]   BASE の PAT（鍵は標準入力か隠した入力で・引数では受けない）
    thth shop token set <店> [--auth client|token] --by <名前> [--stdin]  Shopify: client id と secret の 2 行か固定の鍵の 1 行
    thth shop whoami|items <店> [--json]                                  読むだけ・注文は読まない
    thth shop diff <店> [--accept] [--json]                               写しとの違い（読むだけ・--accept で写しを進める）
    thth shop draft <店> --to <口座> (--item <id> | --kind new|restock|price_down) [--json]
                                                                          queue の下書き（承認されていない形・1 本か上限 3 本）
    thth shop remove <店> --by <名前> [--confirm D]                       店を外す（二段確認・鍵・写し・台帳を消す）
  この道具は BASE と Shopify が提供するものではありません。

Instagram の商品タグ: 第 1 引数は Instagram の口座
    thth shop eligibility|catalogs <IG> [--json]                          読む
    thth shop products <IG> --catalog <id> [--q 語] [--json]              読む
    thth shop tags <IG> <media-id> [--json]                               読む
    thth shop tag <IG> <media-id> --product <id> --by <名前> [--confirm D]  既存の投稿に付ける（二段確認）
"""
# この家族が足す動詞（Instagram の商品タグの動詞とぶつからない）。
VERBS = ("add", "token", "whoami", "items", "diff", "draft", "remove")


def parent(sub):
    """親の `thth shop` を 1 か所で作り、動詞を足す先（subparsers）を返す（cli.py が 1 度だけ呼ぶ）。"""
    shop = sub.add_parser(COMMAND, help=PARENT_HELP, description=PARENT_DESCRIPTION,
                          formatter_class=argparse.RawDescriptionHelpFormatter)
    return shop.add_subparsers(dest="shop_command", required=True)


def register(verbs) -> None:
    """親の `thth shop` の下に、店を読む口の動詞を足す。"""
    p_add = verbs.add_parser("add", help="店を読む: 店の台帳を 1 本書く")
    p_add.add_argument("store", metavar="店")
    p_add.add_argument("--kind", required=True, choices=ledger.KINDS)
    p_add.add_argument("--project", required=True)
    p_add.add_argument("--shop", default=None, metavar="ラベル",
                       help="Shopify の店の myshopify のラベル（admin.shopify.com/store/<ここ> か <ここ>.myshopify.com）")
    p_add.add_argument("--by", required=True)
    p_add.set_defaults(func=_run(cmd_add))

    p_token = verbs.add_parser("token", help="店を読む: 鍵（BASE の PAT・Shopify のアプリの鍵）の受け取り")
    token_verbs = p_token.add_subparsers(dest="store_token_verb", required=True)
    p_set = token_verbs.add_parser("set", help="鍵を標準入力か隠した入力で受け、店が見えたときだけ保存する（引数では受けない）")
    p_set.add_argument("store", metavar="店")
    p_set.add_argument("--expires", default=None, metavar="YYYY-MM-DD",
                       help="BASE: Developers で発行したときに決めた期限の日（あとから変えられない・BASE では要る）。"
                            "Shopify の鍵には期限の日が無いので付けない")
    p_set.add_argument("--auth", default=None, choices=("client", "token"),
                       help="Shopify: client は Dev Dashboard の Client ID と secret の 2 行（既定）、token は既にある"
                            "カスタムアプリの固定の鍵の 1 行")
    p_set.add_argument("--by", required=True)
    p_set.add_argument("--stdin", action="store_true", help="標準入力から黙って 1 行読む（パイプ用）")
    p_set.add_argument("--force", action="store_true", help="前の鍵と別の店でも置き換える")
    p_set.set_defaults(func=_run(cmd_token_set))

    for verb, help_text, func in (("whoami", "鍵の確かめ・店の名前と URL・鍵の期限", cmd_whoami),
                                  ("items", "商品の一覧（許した欄だけ）", cmd_items),
                                  ("diff", "前の写しとの違い（読むだけ・--accept で写しを進める）", cmd_diff),
                                  ("draft", "違いから queue の下書き（承認されていない形・1 本か上限 3 本）", cmd_draft)):
        q = verbs.add_parser(verb, help="店を読む: " + help_text)
        q.add_argument("store", metavar="店")
        q.add_argument("--json", action="store_true")
        q.set_defaults(func=_run(func))
        if verb == "diff":
            q.add_argument("--accept", action="store_true", help="今の読みを写しにする（全部読めたときだけ）")
        if verb == "draft":
            q.add_argument("--to", required=True, metavar="口座", help="下書きを置く THTH の口座")
            q.add_argument("--item", type=_item_id, default=None, help="この商品 1 つだけ")
            q.add_argument("--kind", choices=tuple(draft_mod.PHRASES), default=None,
                           help=f"この種類の違いから（上限 {draft_mod.MAX_DRAFTS} 本）")
            q.add_argument("--by", default=None, help="誰が頼んだか（記録に残す・無ければ THTH_ACTOR）")

    p_remove = verbs.add_parser("remove", help="店を読む: 店を外す（二段確認・鍵・写し・台帳を消す）")
    p_remove.add_argument("store", metavar="店")
    p_remove.add_argument("--by", required=True)
    p_remove.add_argument("--confirm", default=None, help="一段目が表示した digest。これが無いと何も消さない")
    p_remove.add_argument("--json", action="store_true")
    p_remove.set_defaults(func=_run(cmd_remove))
