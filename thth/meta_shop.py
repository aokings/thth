"""計画 Meta 全部盛り: Instagram の商品タグの最小の口（審査の録画に映す・`instagram_shopping_tag_products`）。

    thth shop eligibility <IG>                          商品タグを付けられる口座か（読む）
    thth shop catalogs <IG>                             使えるカタログ（id・名前・店・商品数・読む）
    thth shop products <IG> --catalog <id> [--q 語]     カタログの商品（id・名前・審査の状態・読む）
    thth shop tags <IG> <media-id>                      投稿に付いている商品タグ（読む）
    thth shop tag <IG> <media-id> --product <id> [--x 0.5 --y 0.5] --by 名前 [--confirm D]   既存の投稿に付ける（二段）

**path と欄は公式の頁で 2026-11-09 に原文を確かめた**
（developers.facebook.com/docs/instagram-platform/instagram-api-with-facebook-login/product-tagging）:

  - `GET /{ig-user-id}?fields=shopping_product_tag_eligibility`（true/false）。
  - `GET /{ig-user-id}/available_catalogs`（応答は `available_catalogs.data`・欄は catalog_id・catalog_name・shop_name・
    product_count。共同カタログは非対応）。
  - `GET /{ig-user-id}/catalog_product_search`（`catalog_id` 必須・`q` は商品名か SKU・任意・カーソルの頁。欄は product_id・
    product_name・review_status ほか。review_status が approved の商品だけが公開された投稿に出る。承認前でも API は断らない）。
  - `GET|POST /{ig-media-id}/product_tags`（POST は `updated_tags`＝product_id と、画像だけ x・y（0.0〜1.0）。足すだけで、
    同じ商品を再び渡すと位置の更新。上限はフィードで 20・リールで 30）。
  - 要る権限は `instagram_shopping_tag_products` と `catalog_management`。アプリの利用者がショップを持つビジネスマネージャの
    管理者であること。承認済みの Instagram ショップと商品が要る。ストーリーズ・ライブ・クリエイター口座は不可。
    公開時の上限は 24 時間に 25 投稿。2023-08-10 以降はチェックアウトを有効にしていないショップはタグ付けできないことがある。

**【未確認】**:
  - タグを外す口（`DELETE /{ig-media-id}/product_tags`）は公式の頁に無い。なので `shop untag` は作っていない。
  - 投稿の作成（`POST /{ig-user-id}/media` の `product_tags`）に載せる `send --product` は、投稿の指紋・queue・lint を通る
    変更になるので、この口には入れていない（既存の投稿に付ける `shop tag` だけ）。
  - `product_tags` の POST が、THTH のページの鍵で通るか（公式の頁は各 endpoint の参照に token の型を譲っている）は、
    本物で 1 回打って確かめる。

**保存**: 答えは画面に出すだけ（数と id と商品名まで・保存しない）。残すのは runs の 1 行（命令の名前・媒体・使った権限・結果）だけ。
書く口（`tag`）は `meta_write` と同じ二段確認・production・安全装置の止めの印・結果不明の扱い。
"""
from __future__ import annotations

import json
import re

from . import approval as approval_mod
from . import meta_write as mw
from .adapters import instagram as ig

RUN_ACTIONS = ("shop_eligibility", "shop_catalogs", "shop_products", "shop_tags", "shop_tag")
DIGITS = re.compile(r"[0-9]{1,32}")
QUERY_MAX = 100


def _bad(message, **extra):
    mw._refuse("shop_args_invalid", message, rc=2, **extra)


def _digits(value, what) -> str:
    text = str(value or "").strip()
    if not DIGITS.fullmatch(text):
        _bad(f"{what}は数字の id です")
    return text


def _note(args, action, media, permission) -> None:
    mw._record(args.account, action, media, "ok", permission=permission, mode="read")


def cmd_eligibility(args) -> int:
    account_cfg, adapter_cls, token = mw._load(args, "shopping_tags", "商品タグ")
    mw._need(args, adapter_cls, token, [ig.SHOP_PERMISSION, "instagram_basic"])
    adapter = mw._adapter(account_cfg, token)
    eligible = mw._read("商品タグの資格", adapter.shop_eligibility)
    _note(args, "shop_eligibility", account_cfg.get("media"), ig.SHOP_PERMISSION)
    if args.json:
        mw._print_json({"ok": True, "account": args.account, "eligible": eligible, "storage": "display_only"})
        return 0
    print(f"{args.account}  商品タグ: {'付けられます' if eligible else '付けられません'}（保存しません）")
    if not eligible:
        print("  なぜ: 承認済みの Instagram ショップに結ばれていないと、Meta は資格なしと返します")
        print("  通すには: 自社のドメインで実際に売っている物を載せたショップを Commerce Manager で作り、"
              "この口座を結んで Instagram の承認を受けます（Meta のコマースの要件は、事業が自分のドメインで売っていること）")
        print("  より良くするには: 承認後に thth shop catalogs で、商品の入ったカタログが見えるのを確かめます")
    return 0


def cmd_catalogs(args) -> int:
    account_cfg, adapter_cls, token = mw._load(args, "shopping_tags", "商品タグ")
    mw._need(args, adapter_cls, token, [ig.SHOP_CATALOG_PERMISSION, "instagram_basic"])
    adapter = mw._adapter(account_cfg, token)
    rows = mw._read("使えるカタログ", adapter.shop_catalogs)
    _note(args, "shop_catalogs", account_cfg.get("media"), ig.SHOP_CATALOG_PERMISSION)
    if args.json:
        mw._print_json({"ok": True, "account": args.account, "catalogs": rows, "count": len(rows),
                        "storage": "display_only"})
        return 0
    print(f"{args.account}  使えるカタログ {len(rows)} 件（保存しません）")
    for row in rows:
        count = row.get("product_count")
        print(f"  catalog_id={row['catalog_id']}  {row.get('catalog_name') or '—'}  "
              f"店={row.get('shop_name') or '—'}  商品数={count if count is not None else '—'}")
    if not rows:
        print("  0 件です（読みは通っています。ショップに結んだカタログがまだ無いだけかもしれません）")
    return 0


def cmd_products(args) -> int:
    account_cfg, adapter_cls, token = mw._load(args, "shopping_tags", "商品タグ")
    catalog = _digits(args.catalog, "--catalog（カタログ）")
    query = None
    if args.q is not None:
        query = str(args.q).strip()
        if not query or len(query) > QUERY_MAX or any(ord(c) < 32 or ord(c) == 127 for c in query):
            _bad(f"--q は制御文字なしの 1〜{QUERY_MAX} 字です")
    mw._need(args, adapter_cls, token, [ig.SHOP_PERMISSION, "instagram_basic"])
    adapter = mw._adapter(account_cfg, token)
    rows = mw._read("商品の検索", lambda: adapter.shop_products(catalog, query=query))
    _note(args, "shop_products", account_cfg.get("media"), ig.SHOP_PERMISSION)
    if args.json:
        mw._print_json({"ok": True, "account": args.account, "products": rows, "count": len(rows),
                        "storage": "display_only"})
        return 0
    print(f"{args.account}  商品 {len(rows)} 件（先頭 1 頁・保存しません）")
    for row in rows:
        print(f"  product_id={row['product_id']}  {row.get('product_name') or '—'}  審査={row.get('review_status') or '—'}")
    if any(row.get("review_status") not in (None, "approved") for row in rows):
        print("  審査が approved でない商品は、タグを付けても公開された投稿には出ません（API は断りません）")
    return 0


def cmd_tags(args) -> int:
    account_cfg, adapter_cls, token = mw._load(args, "shopping_tags", "商品タグ")
    media_id = _digits(args.media_id, "投稿（media）")
    mw._need(args, adapter_cls, token, [ig.SHOP_PERMISSION, "instagram_basic"])
    adapter = mw._adapter(account_cfg, token)
    rows = mw._read("商品タグの確認", lambda: adapter.shop_tags(media_id))
    _note(args, "shop_tags", account_cfg.get("media"), ig.SHOP_PERMISSION)
    if args.json:
        mw._print_json({"ok": True, "account": args.account, "media_id": media_id, "tags": rows,
                        "count": len(rows), "storage": "display_only"})
        return 0
    print(f"{args.account}  投稿 {media_id} の商品タグ {len(rows)} 件（保存しません）")
    for row in rows:
        place = f"  位置=({row['x']}, {row['y']})" if row.get("x") is not None and row.get("y") is not None else ""
        print(f"  product_id={row['product_id']}  {row.get('name') or '—'}  審査={row.get('review_status') or '—'}{place}")
    return 0


def cmd_tag(args) -> int:
    account_cfg, adapter_cls, token = mw._load(args, "shopping_tags", "商品タグ")
    media_id = _digits(args.media_id, "投稿（media）")
    product = _digits(args.product, "--product（商品）")
    if (args.x is None) != (args.y is None):
        _bad("--x と --y は 2 つ一緒に渡します（画像の投稿は位置が要ります。動画・リールは付けません）")
    if args.x is not None and not (0.0 <= args.x <= 1.0 and 0.0 <= args.y <= 1.0):
        _bad("--x と --y は 0.0〜1.0 です")
    mw._need(args, adapter_cls, token, [ig.SHOP_PERMISSION, "instagram_basic"])
    adapter = mw._adapter(account_cfg, token)
    current = mw._read("商品タグの確認", lambda: adapter.shop_tags(media_id))
    already = any(row["product_id"] == product for row in current)
    by = mw._by(args)
    place = f"({args.x}, {args.y})" if args.x is not None else "位置なし（動画・リール）"
    digest = mw.digest_of("shop_tag", args.account, media_id, product, args.x, args.y,
                          ",".join(sorted(row["product_id"] for row in current)))
    if not args.confirm:
        warn = ([] if approval_mod.is_true(account_cfg.get("production"))
                else ["  ⚠ 台帳に production: true が無いので、二段目でも媒体は呼びません"])
        return mw._stage_one(
            args, digest=digest,
            payload={"media_id": media_id, "product_id": product, "x": args.x, "y": args.y, "already": already,
                     "tags_now": len(current), "by": by},
            lines=[f"付けません（確認の一段目です）: {args.account} 投稿 {media_id}",
                   f"  商品: {product}（いま{'付いています・位置の更新になります' if already else '付いていません'}）",
                   f"  位置: {place}",
                   f"  いま付いているタグ: {len(current)} 件（上限 フィード {ig.SHOP_TAGS_MAX}・リール 30）",
                   f"  by  : {by}",
                   "  承認前（approved でない）の商品は、付けても公開された投稿には出ません", *warn],
            again=f"thth shop tag {args.account} {media_id} --product {product}"
                  + (f" --x {args.x} --y {args.y}" if args.x is not None else "")
                  + f" --by {json.dumps(by, ensure_ascii=False)}")
    mw._check_confirm(args, digest)
    mw._ready(args, account_cfg)
    mw._write(args, account_cfg, "shop_tag", ig.SHOP_PERMISSION,
              lambda: adapter.shop_tag(media_id, product, x=args.x, y=args.y))
    if args.json:
        mw._print_json({"ok": True, "changed": True, "account": args.account, "media_id": media_id, "by": by})
    else:
        print(f"付けました: {args.account} 投稿 {media_id}（{by}）")
    return 0


def register(shop_sub) -> None:
    """親の `thth shop`（`thth/shop/cli.py` の `parent()` が 1 か所で作る）の下に、Instagram の商品タグの動詞を足す。"""

    def add(name, help_text, func, *, write=False):
        parser = shop_sub.add_parser(name, help=help_text)
        parser.add_argument("account")
        if write:
            parser.add_argument("--by", default=None, help="誰が頼んだか（必須・記録には残さない）")
            parser.add_argument("--confirm", default=None, help="一段目が表示した digest。これが無いと何もしない")
            parser.add_argument("--wait", type=mw.lock_mod.wait_seconds, default=0, help="ロックを待つ秒数（既定 0）")
        parser.add_argument("--json", action="store_true")
        parser.set_defaults(func=mw._run(func))
        return parser

    add("eligibility", "商品タグを付けられる口座か（読むだけ）", cmd_eligibility)
    add("catalogs", "使えるカタログ（読むだけ）", cmd_catalogs)
    products = add("products", "カタログの商品（読むだけ）", cmd_products)
    products.add_argument("--catalog", required=True, help="カタログの id（thth shop catalogs に出る）")
    products.add_argument("--q", default=None, help="商品名か SKU の語")
    tags = add("tags", "投稿に付いている商品タグ（読むだけ）", cmd_tags)
    tags.add_argument("media_id", help="Instagram の media の数字の id")
    tag = add("tag", "既存の投稿に商品タグを付ける（二段確認）", cmd_tag, write=True)
    tag.add_argument("media_id", help="Instagram の media の数字の id")
    tag.add_argument("--product", required=True, help="商品の id（thth shop products に出る）")
    tag.add_argument("--x", type=float, default=None, help="画像の横の位置 0.0〜1.0（--y と一緒に）")
    tag.add_argument("--y", type=float, default=None, help="画像の縦の位置 0.0〜1.0")
