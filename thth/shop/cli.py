"""`thth shop …` の店を読む動詞（BASE v2・Shopify・STORES。Instagram の商品タグの動詞は `thth/meta_shop.py`）。

    thth shop add <店> --kind base --project <project> --by <名前>     台帳を 1 本書く（0600）
    thth shop add <店> --kind shopify --project <project> --shop <ラベル> --by <名前>
    thth shop add <店> --kind stores --project <project> --url <店の頁の URL> --by <名前>
    thth shop token set <店> --expires YYYY-MM-DD --by <名前> [--stdin]
                                       BASE の PAT を標準入力か隠した入力で受け、店が見えたときだけ保存する
    thth shop token set <店> [--auth client|token] --by <名前> [--stdin]
                                       Shopify: client id と secret の 2 行か、固定の鍵の 1 行。店が見え、
                                       read_products があるときだけ保存する（ほかの権限があれば知らせる）
    thth shop token set <店> [--expires YYYY-MM-DD] --by <名前> [--stdin]
                                       STORES: プライベートアプリのクレデンシャルの 1 行。店が見えたときだけ保存する
    thth shop whoami <店> [--json]    鍵の確かめ・店の名前と URL・鍵の期限
    thth shop items <店> [--json]     商品の一覧（許した欄だけ・非公開の商品は id だけ）
    thth shop diff <店> [--accept] [--json]
                                       前の写しとの違い（読むだけ）。--accept で今の読みを写しにする。初回は基準を取るだけ
    thth shop draft <店> --to <口座> (--item <id> | --kind new|restock|price_down) [--json]
                                       違いから queue の下書き（承認されていない形）を 1 本か上限 3 本
    thth shop remove <店> --by <名前> [--confirm D] [--json]
                                       店を外す（二段確認・鍵 → 写し → 台帳の順に消す）
    thth shop add <店> --kind square|smaregi --project <project> --url <店の頁> --location <店舗> [--contract <契約>] --by <名前>
    thth shop publish <店> (--item <id> | --category <id>) [--off] --by <名前>
                                       実店舗のレジ: 出してよい品の表（既定は空・変えたら写しを捨てる）
    thth shop items <店> --candidates  実店舗のレジ: 出す品を選ぶための名前の一覧（残さない）
    thth shop doctor <店> [--json]    台帳・鍵・期限・店の読みと、鍵の広さ・まだ確かめていない所

記録（`admin_log`）の動きの名前は `ACTION_PREFIX`（shopread_）で始める: add・token set・remove・draft。
名前・店・時刻・欄の前後だけで、鍵は入れない。

**注文と買い手の情報は読まない。店には書かない（BASE と STORES は GET だけ・Shopify は決まった 2 つの問い合わせと
鍵を取る口だけ）。鍵は引数で受けない・出力しない。** 手元の MCP と thth.me の鍵の道には出さない（鍵は手元の thth に
置く形だけ）。STORES のクレデンシャルに何が許されているかは THTH が選べないが、THTH が呼ぶのは店と商品の一覧の 2 つの道だけ（柵は
`wire.ROUTES`）。台帳の `public_url`（STORES の店の頁）には何も送らない。
この道具は BASE・Shopify・STORES が提供するものではありません。
"""
from __future__ import annotations

import argparse
import datetime
import getpass
import json
import os
import sys

from .. import admin_log, draft_origin, jst, redact, refusal
from . import ACTION_PREFIX, COMMAND, diff as diff_mod, draft as draft_mod, kinds, ledger
from . import pos as pos_mod
from . import remove as remove_mod
from . import wire
from .wire import StoreRefused

# 試験の差し込み口（本物の網に出ない）。None なら `wire.default_transport`。
TRANSPORT = None
# 鍵の読み手の差し込み口（試験用）。None なら標準入力か getpass。
READ_SECRET = None
PROMPT = "BASE の個人用アクセストークン（PAT）を貼り付けてください（表示されません）: "
PROMPT_STORES = "STORES のプライベートアプリのクレデンシャルを貼り付けてください（表示されません）: "
PROMPT_SQUARE = "Square のアクセストークン（個人の鍵か、自分の店に許した OAuth の鍵）を貼り付けてください（表示されません）: "
PROMPT_SMAREGI_ID = "スマレジのマイページで発行した client id を貼り付けてください（表示されません）: "
PROMPT_SMAREGI_SECRET = "スマレジのマイページで発行した client secret を貼り付けてください（表示されません）: "


def _refuse(args, exc: StoreRefused) -> int:
    # 詳細の語（`store_args_invalid` の `url` など）を `reason` に渡すと、目録の `by_detail` のその語の通し方が出る。
    reason = {"reason": exc.detail} if exc.detail else {}
    found = refusal.parts(exc.code, **reason) or {}
    line = f"{exc.code}: {found.get('why') or exc.code}"
    if exc.detail:
        line += f"（{exc.detail}）"
    payload = {"ok": False, "code": exc.code, "detail": exc.detail, **exc.extra}
    if exc.code == "pos_location_unknown" and exc.extra.get("locations") and not getattr(args, "json", False):
        # 選べる店舗（持ち主の店の店舗の id と名前だけ）。台帳の --location をこのどれかにすると通る。
        print("この鍵で見える店舗:", file=sys.stderr)
        for found in exc.extra["locations"]:
            print(redact.redact(f"  {found['id']}  {found['name']}"), file=sys.stderr)
    return refusal.emit(args, exc.code, line=redact.redact(line), payload=payload, **reason)


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


def _expiry_note(when, kind: str = "base") -> str | None:
    # state が none（期限の日の無い鍵・Shopify と --expires の無い STORES）と ok は何も言わない。
    if when["state"] == "soon":
        if kind == "square":
            return (f"注意: 鍵の期限は {when['expires_on']}（あと {when['days_left']} 日・token set で入れた日）です。"
                    f"Square の OAuth の鍵は 30 日で切れます。新しい鍵を作り、thth {COMMAND} token set で差し替えてください")
        if kind == "stores":
            return (f"注意: 鍵の期限は {when['expires_on']}（あと {when['days_left']} 日・token set で入れた日）です。"
                    f"STORES から新しいクレデンシャルを受け取り、thth {COMMAND} token set で差し替えてください。"
                    f"今のクレデンシャルがまだ使えるなら、同じものを新しい日を付けて（か日を付けずに）thth {COMMAND} "
                    "token set で置き直せば通ります")
        return (f"注意: 鍵の期限は {when['expires_on']}（あと {when['days_left']} 日）です。BASE Developers で新しい PAT を"
                f"発行し、thth {COMMAND} token set で差し替えてください（期限はあとから変えられません）")
    if when["state"] == "unknown":
        return f"注意: 鍵の期限が分かりません。thth {COMMAND} token set --expires で期限の日を持たせてください"
    return None


def _note(args, when, kind: str = "base") -> None:
    note = _expiry_note(when, kind)
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
    if args.kind in ("stores",) or kinds.is_pos(args.kind):
        extra["public_url"] = public_url(args.url)
    elif args.url is not None:
        raise StoreRefused("store_args_invalid", "url")
    extra.update(_pos_add_fields(args))
    _log_ready()
    path = ledger.write_new(args.store, ledger.build(args.store, kind=args.kind, project=args.project, **extra))
    _record("added", args.store, by, {"kind": [None, args.kind], "project": [None, args.project],
                                      **{key: [None, value] for key, value in extra.items()}}, args.kind)
    print(f"台帳を書きました: {path}（0600）")
    if args.kind == "shopify":
        print(f"次に、店の持ち主が Dev Dashboard でアプリを作り、アプリの版で scope は read_products だけを選んで店に入れ、"
              f"Settings の Client ID と Client secret を thth {COMMAND} token set {args.store} --auth client --by <名前> で"
              "渡してください（既にある管理画面のカスタムアプリの固定の鍵なら --auth token）。")
    elif kinds.is_pos(args.kind):
        _pos_add_next(args)
    elif args.kind == "stores":
        print(f"次に、店の持ち主が STORES の開発パートナーの登録でプライベートアプリを申し込み（スタンダードプランが要ります）、"
              f"発行されたクレデンシャルを thth {COMMAND} token set {args.store} --by <名前> で渡してください"
              "（期限の日を知っていれば --expires <日>）。")
        print("  クレデンシャルに何が許されているかを THTH は選べません。何が許されていても、THTH が呼ぶのは"
              " api.stores.dev の店と商品の一覧の 2 つの道だけ（GET）で、注文・入金の道は呼ばず、店には書きません。")
        print(f"  店の頁の URL（{extra['public_url']}）は下書きに載せるだけで、THTH はそこに何も送りません。")
    else:
        print(f"次に、店の持ち主が BASE Developers で読むだけの権限（items.read・user.read）の PAT を発行し、"
              f"thth {COMMAND} token set {args.store} --expires <期限の日> --by <名前> で渡してください。")
    print(kinds.get(args.kind)["not_by"])
    return 0


def _pos_add_fields(args) -> dict:
    """実店舗のレジの台帳の欄（店舗の id・スマレジの契約 ID・出してよい品の表は空から）。ほかの種類に付けたら断る。"""
    location, contract = getattr(args, "location", None), getattr(args, "contract", None)
    if not kinds.is_pos(args.kind):
        if location is not None:
            raise StoreRefused("pos_args_invalid", "location")
        if contract is not None:
            raise StoreRefused("pos_args_invalid", "contract")
        return {}
    out = {}
    if args.kind == "smaregi":
        if not isinstance(contract, str) or not wire.SMAREGI_CONTRACT.fullmatch(contract.strip()):
            raise StoreRefused("pos_args_invalid", "contract")
        out["contract"] = contract.strip()
    elif contract is not None:
        raise StoreRefused("pos_args_invalid", "contract")
    text = location.strip() if isinstance(location, str) else ""
    pattern = wire.SQUARE_ID if args.kind == "square" else ledger.LOCATION_DIGITS
    if not pattern.fullmatch(text):
        raise StoreRefused("pos_args_invalid", "location")
    out["location"] = text
    out["publish"] = pos_mod.empty_publish()
    return out


def _pos_add_next(args) -> None:
    if args.kind == "square":
        print(f"次に、店の持ち主が Square の Developer Console でアプリを作り、本番のアクセストークンを "
              f"thth {COMMAND} token set {args.store} --by <名前> で渡してください（OAuth の鍵なら --expires <切れる日>）。")
    else:
        print(f"次に、店の契約の管理者がマイページの「プラットフォーム API 設定」で基本情報とスコープ（pos.products:read・"
              f"pos.stores:read・pos.stock:read）を設定し、発行された client id と secret を "
              f"thth {COMMAND} token set {args.store} --by <名前> で渡してください（プレミアムプラン以上が要ります）。")
    for line in pos_mod.KEY_NOTES[args.kind]:
        print(f"  {line}")
    print(f"  レジの品は、持ち主が thth {COMMAND} publish {args.store} --item <id> か --category <id> で出してよいとした物だけが"
          "下書きの材料になります（今は 1 つもありません）。")


def shop_label(value) -> str:
    """`--shop` を myshopify のラベルに。`xxx.myshopify.com` は後ろを落とす（断らない）。独自ドメインや URL は断る。"""
    text = (value or "").strip().lower() if isinstance(value, str) else ""
    if text.endswith(wire.SHOPIFY_SUFFIX):
        text = text[:-len(wire.SHOPIFY_SUFFIX)]
    if not wire.SHOPIFY_LABEL.fullmatch(text):
        raise StoreRefused("store_args_invalid", "shop")
    return text


def public_url(value) -> str:
    """`--url` を STORES の店の頁の URL に（https だけ・userinfo と query と fragment なし・2048 字まで）。"""
    text = value.strip() if isinstance(value, str) else ""
    found = ledger.check_public_url(text)
    if found is None:
        raise StoreRefused("store_args_invalid", "url")
    return found


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
    if cfg["kind"] == "stores":
        return _token_set_stores(args, cfg, by)
    if cfg["kind"] == "square":
        return _token_set_stores(args, cfg, by, PROMPT_SQUARE)
    if cfg["kind"] == "smaregi":
        return _token_set_smaregi(args, cfg, by)
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


def _token_set_stores(args, cfg, by, prompt=PROMPT_STORES) -> int:
    """STORES のクレデンシャル（1 行）。`GET /retail/202211/store` が通ったときだけ保存する。

    期限は公式に書かれていない【未確認】ので `--expires` は任意。付ければ BASE と同じく 14 日前から知らせ、
    無ければ期限の日の無い鍵（`no_expiry`）として持つ。
    """
    day = None
    if args.expires is not None:
        day = ledger.parse_day(args.expires)
        if day is None:
            raise StoreRefused("store_args_invalid", "expires")
        if day < _today():
            raise StoreRefused("store_token_expires_past", day.isoformat())
    _log_ready()
    value = _secret_line(args, prompt)
    token = {"auth": "bearer", "access_token": value}
    shop = kinds.client(cfg, token, TRANSPORT).user()
    try:
        before = ledger.load_token(cfg)
    except StoreRefused:
        before = None
    if before and before.get("shop_id") and before["shop_id"] != shop["shop_id"] and not args.force:
        raise StoreRefused("store_shop_mismatch")
    expires_on = day.isoformat() if day else None
    ledger.write_token(cfg, {**token, "shop_id": shop["shop_id"], "obtained_at": ledger.now_iso(),
                             "expires_on": expires_on, "no_expiry": day is None})
    dropped = _drop_other_shop(args.store, shop["shop_id"])
    _record("token_set", args.store, by,
            {"shop_id": [before.get("shop_id") if before else None, shop["shop_id"]],
             "expires_on": [before.get("expires_on") if before else None, expires_on],
             "replaced": [None, before is not None], "snapshot_dropped": [None, dropped]}, cfg["kind"])
    print(f"保存しました: {cfg['token']}（600）・店 {shop['shop_name'] or '（名前なし）'}（{shop['shop_id']}）・"
          + (f"鍵の期限 {expires_on}" if expires_on else "鍵の期限の日はありません（--expires を付けていないので）"))
    if dropped:
        print(f"店が替わったので、前の店の写しを捨てました。次の thth {COMMAND} diff {args.store} が基準を取り直します")
    if day:
        note = _expiry_note(ledger.expiry({"expires_on": expires_on}, _today()), cfg["kind"])
        if note:
            print(note, file=sys.stderr)
    _pos_notes(cfg["kind"], shop)
    return 0


def _pos_notes(kind: str, shop: dict | None = None, *, indent: str = "") -> None:
    """実店舗のレジの鍵の広さ・THTH が打つもの・まだ確かめていない所（token set・whoami・doctor）。"""
    if not kinds.is_pos(kind):
        return
    for line in pos_mod.KEY_NOTES[kind] + pos_mod.UNVERIFIED[kind]:
        print(indent + line)
    extra = (shop or {}).get("scopes_extra") or []
    if extra:
        print(indent + f"この鍵は読む 3 つのほかに {len(extra)} つの scope が付いて返りました（{'・'.join(extra)}）。"
              "THTH は使いませんが、マイページのスコープの設定で外すと、漏れたときの害が小さくなります。")


def _token_set_smaregi(args, cfg, by) -> int:
    """スマレジの client id と secret（2 行）。鍵を取り、店舗が見えたときだけ保存する。期限の日は無い。"""
    if args.expires is not None:
        raise StoreRefused("store_args_invalid", "expires")
    _log_ready()
    token = {"auth": "smaregi_client", "client_id": _secret_line(args, PROMPT_SMAREGI_ID),
             "client_secret": _secret_line(args, PROMPT_SMAREGI_SECRET)}
    shop = kinds.client(cfg, token, TRANSPORT).user()
    try:
        before = ledger.load_token(cfg)
    except StoreRefused:
        before = None
    if before and before.get("shop_id") and before["shop_id"] != shop["shop_id"] and not args.force:
        raise StoreRefused("store_shop_mismatch")
    ledger.write_token(cfg, {**token, "shop_id": shop["shop_id"], "obtained_at": ledger.now_iso(),
                             "expires_on": None, "no_expiry": True})
    dropped = _drop_other_shop(args.store, shop["shop_id"])
    _record("token_set", args.store, by,
            {"shop_id": [before.get("shop_id") if before else None, shop["shop_id"]],
             "replaced": [None, before is not None], "snapshot_dropped": [None, dropped],
             "scopes_extra": [None, len(shop.get("scopes_extra") or [])]}, cfg["kind"])
    print(f"保存しました: {cfg['token']}（600）・店舗 {shop['shop_name'] or '（名前なし）'}（契約 {shop['shop_id']}・"
          f"店舗 {shop['location']}）・鍵の期限の日はありません")
    if dropped:
        print(f"店が替わったので、前の店の写しを捨てました。次の thth {COMMAND} diff {args.store} が基準を取り直します")
    _pos_notes(cfg["kind"], shop)
    return 0


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
          + ("（台帳に入れた値）" if cfg["kind"] == "stores" else "")
          + f"（公開 {'はい' if shop['able_to_business'] else 'いいえ' if shop['able_to_business'] is False else '不明'}）")
    if when["state"] == "none":
        print("  鍵の期限: なし（期限の日の無い鍵）")
    else:
        print(f"  鍵の期限: {when['expires_on'] or '不明'}"
              + (f"（あと {when['days_left']} 日）" if when["days_left"] is not None else ""))
    note = scope_note(shop) if cfg["kind"] == "shopify" else None
    if note:
        print(f"  {note}")
    if kinds.is_pos(cfg["kind"]):
        _print_hours(shop.get("hours"), indent="  ")
        print(f"  出してよい品: {_publish_text(cfg)}")
        _pos_notes(cfg["kind"], shop, indent="  ")
    print(f"  {not_by}")
    _note(args, when, cfg["kind"])
    return 0


_DAY_WORDS = {"MON": "月", "TUE": "火", "WED": "水", "THU": "木", "FRI": "金", "SAT": "土", "SUN": "日"}


def _print_hours(hours, *, indent: str = "") -> None:
    if hours is None:
        print(indent + "営業の時間: レジに営業の時間の欄がありません（台帳の営業の時間が正本です）")
        return
    if not hours:
        print(indent + "営業の時間: レジに入っていません（台帳の営業の時間が正本です）")
        return
    text = "・".join(f"{_DAY_WORDS[p['day']]} {p['start']}〜{p['end']}" for p in hours)
    print(indent + f"営業の時間（レジに入っている値・台帳の営業の時間が正本です）: {text}")


def _publish_text(cfg) -> str:
    publish = cfg["publish"]
    if pos_mod.nothing_published(publish):
        return (f"まだありません（レジの品は既定で下書きの材料にしません。thth {COMMAND} items {cfg['store']} --candidates"
                f" で選び、thth {COMMAND} publish {cfg['store']} --item <id> で足します）")
    return f"品 {len(publish['items'])} つ・部門 {len(publish['categories'])} つ"


def _incomplete_note(read, cfg) -> str | None:
    if read["complete"]:
        return None
    return (f"全部は読めていません（{read['pages']} 頁・{kinds.get(cfg['kind'])['read_cap']()} 件の上限）。"
            "消えた・売り切れは言わず、写しも進めません")


def _adjusted_note(read) -> str | None:
    """点数の見込みが外れて読み方を変えたときの知らせ（Shopify・5.18.1）。読めた分はいつもどおり使える。"""
    adjusted = read.get("adjusted")
    if not adjusted:
        return None
    parts = []
    if adjusted["shrinks"]:
        parts.append(f"Shopify の点数が見込みより大きかったので、1 頁を {adjusted['shrinks'][0]['from']} 件から "
                     f"{adjusted['page_first']} 件に減らして読みました")
    if adjusted["throttled_retries"]:
        parts.append(f"Shopify が混んでいたので {adjusted['throttled_retries']} 回待って打ち直しました")
    cost = adjusted.get("requested_cost_max")
    return ("・".join(parts) + "。読めた分はそのまま使えます"
            + (f"（頼んだ点の最大 {cost}）" if cost else "")
            + "。続くなら --json の read_adjusted を添えて thth report file で開発のセッションへ知らせると、頁の大きさを直せます")


def _stock_text(stock) -> str:
    return "—（数えない）" if stock is None else str(stock)


def cmd_items(args) -> int:
    cfg, _token, when, client = _open(args)
    if getattr(args, "candidates", False):
        return _items_candidates(args, cfg, when, client)
    read = client.items()
    value = {"store": args.store, "complete": read["complete"], "pages": read["pages"], "items": read["items"],
             "token": when, "note": _incomplete_note(read, cfg), "read_adjusted": read.get("adjusted"),
             "read_note": _adjusted_note(read)}
    if args.json:
        _print(value)
        return 0
    hidden = "（出さない品）" if kinds.is_pos(cfg["kind"]) else "（非公開）"
    for row in read["items"]:
        if not row["visible"]:
            print(f"{row['id']}  {hidden}")
            continue
        price = "—" if row["price"] is None else f"{row['price']:,} 円"
        print(f"{row['id']}  {row['name']}  {price}  在庫 {_stock_text(row['stock'])}  {row['sales_status']}")
    print(f"{len(read['items'])} 件" + (f"・{value['note']}" if value["note"] else ""))
    if value["read_note"]:
        print(value["read_note"])
    _note(args, when, cfg["kind"])
    return 0


def _items_candidates(args, cfg, when, client) -> int:
    """出す品を選ぶための一覧（レジだけ・名前を見せるが写しにもファイルにも残さない・在庫は読まない）。"""
    if not kinds.is_pos(cfg["kind"]):
        raise StoreRefused("pos_args_invalid", "candidates")
    found = client.candidates()
    value = {"store": args.store, **found, "token": when,
             "note": "この一覧は出す品を選ぶためのもので、写しにもファイルにも残しません"}
    if args.json:
        _print(value)
        return 0
    for row in found["candidates"]:
        mark = "出す" if row["published"] else "出さない"
        cats = "・".join(f"{c['id']} {c['name']}".strip() for c in row["categories"])
        print(f"{row['id']}  {row['name']}  [{mark}]" + (f"  部門 {cats}" if cats else "")
              + ("  （アーカイブ）" if row.get("archived") else ""))
    print(f"{len(found['candidates'])} 件・{value['note']}。出すなら thth {COMMAND} publish {args.store} --item <id> --by <名前>"
          "（部門ごとなら --category <id>）")
    _note(args, when, cfg["kind"])
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


# 下書きを作らない値段と種類の違いの一言（5.18.1・監査 5.18.0 の 5）。なぜ上下を言わないかと、確かめ方を添える。
_PRICE_WORDS = {
    "mixed": "上がった種類と下がった種類があります。値上げとも値下げとも言わず、下書きは作りません。店で確かめてください",
    "snapshot_without_variants": "前の写しに種類ごとの値段が無いので、種類が増えた・減っただけかもしれません。"
                                 "上下を言わず、下書きは作りません",
    "variants_unread": "種類が多くて全部は読めないので、種類が減っただけかもしれません。上下を言わず、下書きは作りません。"
                       "店で確かめてください",
}


def _change_hint(change: dict) -> str:
    if change["kind"] == "price_changed":
        return "・" + _PRICE_WORDS.get(change.get("why"), _PRICE_WORDS["mixed"])
    if change["kind"] == "sale_started":
        return f"（販売が始まりました。thth {COMMAND} draft <店> --kind new で新着と同じように下書きを作れます）"
    if change["kind"] == "variants_changed":
        return (f"（種類が {change.get('added', 0)} 増え {change.get('removed', 0)} 減りました。値段の上下ではないので、"
                "下書きは作りません）")
    return ""


def _variant_note(changes: list, accepted: bool) -> str | None:
    """前の版の写しと比べた 1 回目の知らせ（写しを進めれば、次から種類ごとに比べる）。"""
    if not any(c.get("why") == "snapshot_without_variants" for c in changes):
        return None
    if accepted:
        return "写しに種類ごとの値段を入れました。次の diff から、値段の上下は同じ種類の値段で決めます"
    return (f"前の写しには種類ごとの値段が無いので、今回は値段の上下を言いません。thth {COMMAND} diff <店> --accept で"
            "写しを進めると、次から同じ種類の値段で上下を決めます")


def cmd_diff(args) -> int:
    cfg, token, when, client = _open(args)
    read, snapshot, changes, switched = _read_and_compare(args, client, token)
    read_at = ledger.now_iso()
    baseline = snapshot is None
    if (baseline or args.accept) and not read["complete"]:
        raise StoreRefused("store_read_incomplete", pages=read["pages"])
    accepted = False
    hours_change = _hours_change(snapshot, read)
    if baseline or args.accept:
        ledger.write_snapshot(args.store, shop_id=token.get("shop_id"), items=read["items"], read_at=read_at,
                              hours=read.get("hours"))
        accepted = True
    value = {"store": args.store, "read_at": read_at, "since": snapshot.get("read_at") if snapshot else None,
             "complete": read["complete"], "pages": read["pages"], "baseline": baseline, "changes": changes,
             "accepted": accepted, "token": when, "note": _incomplete_note(read, cfg), "shop_switched": switched,
             "variant_note": _variant_note(changes, accepted), "read_adjusted": read.get("adjusted"),
             "read_note": _adjusted_note(read)}
    if kinds.is_pos(cfg["kind"]):
        value["store_changes"] = [hours_change] if hours_change else []
        value["publish_note"] = (_publish_text(cfg) if pos_mod.nothing_published(cfg["publish"]) else None)
    if args.json:
        _print(value)
        return 0
    if switched:
        print(f"{args.store}: 写しが今の鍵の店と別の店のものだったので、捨てて基準を取り直しました")
    if baseline:
        print(f"{args.store}: 基準を取りました（{len(read['items'])} 件）。違いはまだありません。次からの thth {COMMAND} diff が"
              "この写しと比べます")
        if value["read_note"]:
            print(value["read_note"])
        _note(args, when, cfg["kind"])
        return 0
    for change in changes:
        extra = f"（{change['before']} → {change['after']} 円）" if "before" in change else ""
        print(f"{change['kind']:<12} {change['id']}  {change.get('name') or ''}{extra}{_change_hint(change)}")
    print(f"違い {len(changes)} 件（{value['since']} の写しと比べて）"
          + ("・写しを今の読みに進めました" if accepted else f"・写しは進めていません（--accept で進めます）")
          + (f"・{value['note']}" if value["note"] else ""))
    if value["variant_note"]:
        print(value["variant_note"])
    if value["read_note"]:
        print(value["read_note"])
    if hours_change:
        print("営業の時間がレジで変わりました（台帳の営業の時間が正本です。合っているか確かめてください・下書きは作りません）: "
              + _hours_words(hours_change["before"]) + " → " + _hours_words(hours_change["after"]))
    if value.get("publish_note"):
        print("出してよい品: " + value["publish_note"])
    _note(args, when, cfg["kind"])
    return 0


def _hours_words(hours) -> str:
    if not hours:
        return "（なし）"
    return "・".join(f"{_DAY_WORDS[p['day']]} {p['start']}〜{p['end']}" for p in hours)


def _hours_change(snapshot, read) -> dict | None:
    """前の写しと今のレジの営業の時間の違い（Square だけ・写しに時間の無い前の版は言わない）。"""
    now = read.get("hours")
    if snapshot is None or now is None or "hours" not in snapshot or not isinstance(snapshot.get("hours"), list):
        return None
    before = snapshot["hours"]
    return None if before == now else {"kind": "hours_changed", "before": before, "after": now}


def cmd_draft(args) -> int:
    if (args.item is None) == (args.kind is None):
        raise StoreRefused("store_args_invalid", "item_or_kind")
    # 下書きは承認されていない形なので --by は任意（無ければ THTH_ACTOR か命令の名前で記録する）。
    by = _by(argparse.Namespace(by=getattr(args, "by", None) or os.environ.get("THTH_ACTOR")
                                or f"thth {COMMAND} draft"))
    cfg, token, when, client = _open(args)
    if kinds.is_pos(cfg["kind"]) and pos_mod.nothing_published(cfg["publish"]):
        # レジの品は既定で材料にしない（設計 実店舗 §5.2）。網に出る前に断る。
        raise StoreRefused("pos_publish_empty")
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
        if (queue / name).exists():
            raise StoreRefused("store_draft_exists")  # 控えを書く前に（あるファイルの控えを書き換えない）
        # 書く前に THTH の側に出所を控える（頭の行が落とされても、承認の確定の段が控えから読み直す・監査 5.19.0 の 1）。
        draft_origin.record(ledger.state_dir_for(args.store), queue / name, draft_mod.head(text))
        written.append({"id": row["id"], "kind": change["draft_kind"],
                        "path": draft_mod.write(queue, name, text)})
    _record("drafted", args.store, by, {"to": [None, args.to], "items": [[], [row["id"] for row in written]],
                                        "kinds": [[], [row["kind"] for row in written]]}, cfg["kind"])
    notes = ["status は draft です（承認されていません）。本文は素の 1〜2 行なので、出す前に整えて thth approve に通してください"]
    if kinds.is_pos(cfg["kind"]):
        notes.append("レジの品には商品頁が無いので、台帳の店の頁の URL を載せました（from_store_url: shop）")
    elif any(draft_mod.url_kind(row) == "shop" for _change, row in picked):
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
    _note(args, when, cfg["kind"])
    return 0


# ------------------------------------------------------------------ 出してよい品（レジだけ）

def cmd_publish(args) -> int:
    """出してよい品（か部門）の表を変える（網に出ない）。変えたら写しを捨て、次の diff が基準を取り直す。

    写しを残すと、いま足した品が次の diff で「新しく入った」に見え、前からある品の新着の下書きができてしまうので。
    """
    by = _by(args)
    raw = ledger.load_raw(args.store)
    if not kinds.is_pos(raw["kind"]):
        raise StoreRefused("pos_args_invalid", "publish_kind")
    if (args.item is None) == (args.category is None):
        raise StoreRefused("pos_args_invalid", "item_or_category")
    key, value = ("items", args.item) if args.item is not None else ("categories", args.category)
    publish = {k: list(v) for k, v in raw["publish"].items()}
    before = list(publish[key])
    if args.off:
        publish[key] = [i for i in publish[key] if i != value]
    elif value not in publish[key]:
        if len(publish[key]) >= pos_mod.PUBLISH_MAX:
            raise StoreRefused("pos_args_invalid", "publish_full")
        publish[key].append(value)
    if publish[key] == before:
        print(f"変わりませんでした（{'品' if key == 'items' else '部門'} {value} は"
              f"{'もともと表にありません' if args.off else 'もう表にあります'}）")
        return 0
    _log_ready()
    ledger.rewrite(args.store, dict(raw, publish=publish))
    dropped = ledger.drop_snapshot(args.store)
    _record("publish_set", args.store, by, {key: [before, publish[key]], "snapshot_dropped": [None, dropped]}, raw["kind"])
    word = "外しました" if args.off else "足しました"
    print(f"{'品' if key == 'items' else '部門'} {value} を出してよい品の表から{word}"
          f"（品 {len(publish['items'])} つ・部門 {len(publish['categories'])} つ）")
    if dropped:
        print(f"写しを捨てました。次の thth {COMMAND} diff {args.store} が基準を取り直します（いま足した品を「新しく入った」と"
              "取り違えないため）")
    return 0


# ------------------------------------------------------------------ 診る（読むだけ）

def cmd_doctor(args) -> int:
    """台帳・鍵・期限を手元で確かめ、店を 1 回だけ読む（whoami と同じ道）。鍵の広さと THTH が打つものを言う。"""
    cfg = ledger.load(args.store)
    entry = kinds.get(cfg["kind"])
    checks = [{"name": "ledger", "ok": True}]
    token = ledger.load_token(cfg)
    checks.append({"name": "token_file", "ok": True})
    when = ledger.expiry(token, _today())
    checks.append({"name": "token_expiry", "ok": when["state"] != "expired", "state": when["state"]})
    if when["state"] == "expired":
        raise StoreRefused("store_token_expired", when["expires_on"])
    shop = kinds.client(cfg, token, TRANSPORT).user()
    checks.append({"name": "read_shop", "ok": True})
    notes = list(pos_mod.KEY_NOTES.get(cfg["kind"], ())) + list(pos_mod.UNVERIFIED.get(cfg["kind"], ()))
    value = {"store": args.store, "kind": cfg["kind"], "checks": checks, "shop_name": shop["shop_name"],
             "token": when, "notes": notes, "notice": entry["not_by"]}
    if kinds.is_pos(cfg["kind"]):
        value["publish"] = {k: len(v) for k, v in cfg["publish"].items()}
        value["hours"] = shop.get("hours")
    if args.json:
        _print(value)
        return 0
    print(f"{args.store}（{entry['label']}）: 台帳・鍵のファイル・期限・店の読みはどれも通りました（店 {shop['shop_name'] or '（名前なし）'}）")
    if kinds.is_pos(cfg["kind"]):
        _print_hours(shop.get("hours"), indent="  ")
        print(f"  出してよい品: {_publish_text(cfg)}")
        _pos_notes(cfg["kind"], shop, indent="  ")
    print(f"  {entry['not_by']}")
    _note(args, when, cfg["kind"])
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
    shown = set()
    for target in found["targets"]:
        if target["category"] in done:
            print(f"消しました: {words[target['category']]} {target['path']}")
            shown.add(target["category"])
    for category in done:
        if category not in shown:                  # 一段目のあと鎖の中で見つけて消した機械の物（remove.execute）
            print(f"消しました: {words.get(category, category)}（一段目のあと、取り直しの途中に書かれた物）")
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


PARENT_HELP = "店（店を読む: BASE・Shopify・STORES・実店舗のレジの Square・スマレジ）と Instagram の商品タグ"
PARENT_DESCRIPTION = """店を読む（BASE など）: 第 1 引数は店の名前（thth shop add で書いた台帳の名前）
    thth shop add <店> --kind base --project <project> --by <名前>
    thth shop add <店> --kind shopify --project <project> --shop <ラベル> --by <名前>
                                                                          ラベルは <ここ>.myshopify.com の <ここ>
    thth shop add <店> --kind stores --project <project> --url <店の頁の URL> --by <名前>
                                                                          URL は下書きに載せるだけ（何も送らない）
    thth shop token set <店> --expires YYYY-MM-DD --by <名前> [--stdin]   BASE の PAT（鍵は標準入力か隠した入力で・引数では受けない）
    thth shop token set <店> [--auth client|token] --by <名前> [--stdin]  Shopify: client id と secret の 2 行か固定の鍵の 1 行
    thth shop token set <店> [--expires YYYY-MM-DD] --by <名前> [--stdin] STORES: クレデンシャルの 1 行（期限の日は任意）
    thth shop add <店> --kind square --project <project> --url <店の頁の URL> --location <店舗の id> --by <名前>
    thth shop add <店> --kind smaregi --project <project> --url <店の頁の URL> --contract <契約 ID> --location <店舗 ID> --by <名前>
                                                                          実店舗のレジ（読むだけ・Square は GET だけ）
    thth shop token set <店> [--expires YYYY-MM-DD] --by <名前> [--stdin] Square: アクセストークンの 1 行
    thth shop token set <店> --by <名前> [--stdin]                        スマレジ: client id と secret の 2 行
    thth shop publish <店> (--item <id> | --category <id>) [--off] --by <名前>
                                                                          レジの品を出してよい品の表に足す・外す（既定は 0）
    thth shop items <店> --candidates [--json]                            レジ: 出す品を選ぶための名前の一覧（残さない）
    thth shop doctor <店> [--json]                                        台帳・鍵・期限・店の読みと、鍵の広さの知らせ
    thth shop whoami|items <店> [--json]                                  読むだけ・注文は読まない
    thth shop diff <店> [--accept] [--json]                               写しとの違い（読むだけ・--accept で写しを進める）
    thth shop draft <店> --to <口座> (--item <id> | --kind new|restock|price_down) [--json]
                                                                          queue の下書き（承認されていない形・1 本か上限 3 本）
    thth shop remove <店> --by <名前> [--confirm D]                       店を外す（二段確認・鍵・写し・台帳を消す）
  この道具は BASE・Shopify・STORES・Square・スマレジが提供するものではありません。

Instagram の商品タグ: 第 1 引数は Instagram の口座
    thth shop eligibility|catalogs <IG> [--json]                          読む
    thth shop products <IG> --catalog <id> [--q 語] [--json]              読む
    thth shop tags <IG> <media-id> [--json]                               読む
    thth shop tag <IG> <media-id> --product <id> --by <名前> [--confirm D]  既存の投稿に付ける（二段確認）
"""
# この家族が足す動詞（Instagram の商品タグの動詞とぶつからない）。
VERBS = ("add", "token", "whoami", "items", "diff", "draft", "remove", "publish", "doctor")


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
    p_add.add_argument("--url", default=None, metavar="URL",
                       help="STORES の店の頁の URL（https・応答に無いので持ち主が入れる・下書きに載せるだけで何も送らない）")
    p_add.add_argument("--location", default=None, metavar="店舗",
                       help="実店舗のレジ: 読む店舗の id（Square の Location の id・スマレジの店舗 ID）")
    p_add.add_argument("--contract", default=None, metavar="契約 ID", help="スマレジの契約 ID")
    p_add.add_argument("--by", required=True)
    p_add.set_defaults(func=_run(cmd_add))

    p_token = verbs.add_parser("token", help="店を読む: 鍵（BASE の PAT・Shopify のアプリの鍵・STORES のクレデンシャル）の受け取り")
    token_verbs = p_token.add_subparsers(dest="store_token_verb", required=True)
    p_set = token_verbs.add_parser("set", help="鍵を標準入力か隠した入力で受け、店が見えたときだけ保存する（引数では受けない）")
    p_set.add_argument("store", metavar="店")
    p_set.add_argument("--expires", default=None, metavar="YYYY-MM-DD",
                       help="BASE: Developers で発行したときに決めた期限の日（あとから変えられない・BASE では要る）。"
                            "Shopify の鍵には期限の日が無いので付けない。STORES は知っていれば付ける（任意）")
    p_set.add_argument("--auth", default=None, choices=("client", "token"),
                       help="Shopify: client は Dev Dashboard の Client ID と secret の 2 行（既定）、token は既にある"
                            "カスタムアプリの固定の鍵の 1 行")
    p_set.add_argument("--by", required=True)
    p_set.add_argument("--stdin", action="store_true", help="標準入力から黙って 1 行読む（パイプ用）")
    p_set.add_argument("--force", action="store_true", help="前の鍵と別の店でも置き換える")
    p_set.set_defaults(func=_run(cmd_token_set))

    for verb, help_text, func in (("whoami", "鍵の確かめ・店の名前と URL・鍵の期限", cmd_whoami),
                                  ("doctor", "台帳・鍵・期限・店の読みを確かめ、鍵の広さを知らせる（読むだけ）", cmd_doctor),
                                  ("items", "商品の一覧（許した欄だけ）", cmd_items),
                                  ("diff", "前の写しとの違い（読むだけ・--accept で写しを進める）", cmd_diff),
                                  ("draft", "違いから queue の下書き（承認されていない形・1 本か上限 3 本）", cmd_draft)):
        q = verbs.add_parser(verb, help="店を読む: " + help_text)
        q.add_argument("store", metavar="店")
        q.add_argument("--json", action="store_true")
        q.set_defaults(func=_run(func))
        if verb == "diff":
            q.add_argument("--accept", action="store_true", help="今の読みを写しにする（全部読めたときだけ）")
        if verb == "items":
            q.add_argument("--candidates", action="store_true",
                           help="実店舗のレジ: 出す品を選ぶための名前の一覧（写しにもファイルにも残さない）")
        if verb == "draft":
            q.add_argument("--to", required=True, metavar="口座", help="下書きを置く THTH の口座")
            q.add_argument("--item", type=_item_id, default=None, help="この商品 1 つだけ")
            q.add_argument("--kind", choices=tuple(draft_mod.PHRASES), default=None,
                           help=f"この種類の違いから（上限 {draft_mod.MAX_DRAFTS} 本）")
            q.add_argument("--by", default=None, help="誰が頼んだか（記録に残す・無ければ THTH_ACTOR）")

    p_publish = verbs.add_parser("publish", help="店を読む: レジの品を出してよい品の表に足す・外す（網に出ない）")
    p_publish.add_argument("store", metavar="店")
    p_publish.add_argument("--item", type=_item_id, default=None, help="品の id（thth shop items <店> --candidates に出る）")
    p_publish.add_argument("--category", type=_item_id, default=None, help="部門の id（その部門の品を全部出してよい）")
    p_publish.add_argument("--off", action="store_true", help="表から外す")
    p_publish.add_argument("--by", required=True)
    p_publish.set_defaults(func=_run(cmd_publish))

    p_remove = verbs.add_parser("remove", help="店を読む: 店を外す（二段確認・鍵・写し・台帳を消す）")
    p_remove.add_argument("store", metavar="店")
    p_remove.add_argument("--by", required=True)
    p_remove.add_argument("--confirm", default=None, help="一段目が表示した digest。これが無いと何も消さない")
    p_remove.add_argument("--json", action="store_true")
    p_remove.set_defaults(func=_run(cmd_remove))
