"""計画 Meta 全部盛り: ブランドコンテンツとクリエイターマーケットプレイスの最小の口（審査の録画に映す）。

    thth brand creators <IG> list <id>...                 承認クリエイターの一覧に、その id が載っているか（読む）
    thth brand creators <IG> approve|remove <id>... --by 名前 [--confirm D]   一覧に足す・外す（二段）
    thth brand promote <IG> <media-id>                    その投稿を広告に使ってよいと許したブランドを読む
    thth brand promote <IG> <media-id> (--allow|--deny) --sponsor <ブランドの id> --by 名前 [--confirm D]   許す・取り消す（二段）
    thth brand ads-permissions <IG> [--creator USERNAME]  口座の段の「広告に使ってよい」の許可の一覧（読む）
    thth brand content <FB|IG> --business <business-id>   提携広告に使える投稿（読む・先頭 1 頁）
    thth creators search <FB|IG> [--country JP] [--q 語] [--acting-business ID] [--limit N]   クリエイターの検索（読む）

**path と欄は公式の頁で 2026-11-09 に原文を確かめた**（`docs/精査_Meta申請の権限_2026-11-08.md` §1.4〜1.10）:

  - marketing-api/ad-creative/partnership-ads/post-level-permissioning:
    `GET|POST|DELETE /{ig-user-id}/branded_content_tag_approval`（`user_ids` 必須・コンマ区切りで 100 まで・
    1 つでも失敗すれば 1 つも変えない）、`GET|POST /{ig-media-id}/branded_content_partner_promote`（POST は
    `sponsor_id`・`permission=true|false`）。
  - graph-api/reference/shadow-ig-user/branded_content_ad_permissions と partnership-ads/account-level-permissioning:
    `GET /{ig-user-id}/branded_content_ad_permissions`（`creator_username` は任意・要る権限は
    `instagram_branded_content_ads_brand`・`instagram_basic`・`business_management`）。
  - partnership-ads/content-discovery-api: `GET /{business-id}/partnership-ads-advertisable-content`
    （`fb_page_id` か `ig_user_id` のどちらかが必須・`fields`・`limit` 1〜50・旧い IG だけの口は 2026-12-01 に消える）。
  - instagram-platform/instagram-api-with-facebook-login/creator-marketplace: `GET /{ig-user-id}/creator_marketplace_creators`
    （`creator_countries`・`query`・`fields`・`limit`・ページの鍵）。
  - fb-creator-discovery: `GET /creator_marketplace/creators`（`creator_countries`・`query`・`fields`・`limit`・ページの鍵）。

**【未確認】**（公式の頁が言い切っていない所。本物で 1 回打って確かめる）:
  - `branded_content_tag_approval` の GET は `user_ids` が必須で、引数なしの全件一覧は公式の頁に無い（なので `list` は id を渡して
    「載っているか」を読む）。要る鍵を公式の頁は「Business access token」と書く。THTH のページの鍵で通るかは未確認。
  - `branded_content_partner_promote` の POST の `sponsor_id` が必須かどうか（例には出るが引数の表に無い）。ここでは必須にしている。
  - `acting_business_id` は公式の頁では `creator_marketplace_brand_info` にだけ書いてあり、`creator_marketplace_creators` で
    受けるかは未確認（旗を付けたときだけ送る）。Facebook の口には書かれていないので送らない。

**保存**: 答えは画面に出すだけ（数と id まで・相手の名前や本文は頼まない）。残すのは runs の 1 行（命令の名前・媒体・使った権限・結果）だけ。
書く口（`approve`・`remove`・`--allow|--deny`）は `meta_write` と同じ二段確認・production・安全装置の止めの印・結果不明の扱い。
"""
from __future__ import annotations

import json
import re

from . import approval as approval_mod
from . import meta_write as mw
from .adapters import base as adapter_base
from .adapters import instagram as ig

RUN_ACTIONS = ("brand_creators", "brand_promote", "brand_ads_permissions", "brand_content", "creators_search")
COUNTRY = re.compile(r"[A-Za-z]{2}")
QUERY_MAX = 100
LIMIT_MAX = 50
DIGITS = re.compile(r"[0-9]{1,32}")
HANDLE = re.compile(r"@?[A-Za-z0-9._]{1,30}")


def _bad(message, **extra):
    mw._refuse("brand_args_invalid", message, rc=2, **extra)


def _ids(raw, what) -> list:
    """位置引数（コンマ区切りも可）の数字の id。重複は 1 つにする。"""
    values = []
    for item in raw or []:
        values.extend(part.strip() for part in str(item).split(",") if part.strip())
    values = list(dict.fromkeys(values))
    if not values:
        _bad(f"{what}の id を渡してください（数字・1〜{ig.BRAND_IDS_MAX} 個）")
    if len(values) > ig.BRAND_IDS_MAX:
        _bad(f"{what}の id は 1 回に {ig.BRAND_IDS_MAX} 個までです（{len(values)} 個）")
    wrong = [v for v in values if not DIGITS.fullmatch(v)]
    if wrong:
        _bad(f"{what}の id は数字です（{wrong[0]} は数字ではありません）")
    return values


def _digits(value, what) -> str:
    text = str(value or "").strip()
    if not DIGITS.fullmatch(text):
        _bad(f"{what}は数字の id です")
    return text


def _note(args, action, media, permission) -> None:
    """読みが通った印（runs に 1 行）。相手の id・名前は入れない。"""
    mw._record(args.account, action, media, "ok", permission=permission, mode="read")


def _production_note(account_cfg) -> list:
    return ([] if approval_mod.is_true(account_cfg.get("production"))
            else ["  ⚠ 台帳に production: true が無いので、二段目でも媒体は呼びません"])


# ------------------------------------------------------------------ brand creators

def cmd_creators(args) -> int:
    account_cfg, adapter_cls, token = mw._load(args, "branded_content", "ブランドコンテンツ")
    media = account_cfg.get("media")
    ids = _ids(args.ids, "クリエイター")
    mw._need(args, adapter_cls, token, [ig.BRAND_PERMISSION, "instagram_basic"])
    adapter = mw._adapter(account_cfg, token)
    present = mw._read("承認クリエイターの一覧", lambda: adapter.brand_creators_check(ids))
    if args.action == "list":
        _note(args, "brand_creators", media, ig.BRAND_PERMISSION)
        if args.json:
            mw._print_json({"ok": True, "account": args.account, "checked": len(ids), "approved": present,
                            "approved_count": len(present), "storage": "display_only"})
            return 0
        print(f"{args.account}  尋ねた {len(ids)} 件のうち承認済み {len(present)} 件（保存しません）")
        for value in present:
            print(f"  承認済み id={value}")
        for value in ids:
            if value not in present:
                print(f"  載っていない id={value}")
        print("  Meta の公開の口は、尋ねた id が載っているかを返します（引数なしの全件一覧は公式の頁に無い）")
        return 0
    by = mw._by(args)
    change = "POST" if args.action == "approve" else "DELETE"
    todo = [v for v in ids if (v not in present) == (args.action == "approve")]
    digest = mw.digest_of("brand_creators_" + args.action, args.account, ",".join(sorted(ids)),
                          ",".join(sorted(present)))
    word = "足す" if args.action == "approve" else "外す"
    if not args.confirm:
        return mw._stage_one(args, digest=digest, payload={"action": args.action, "ids": ids, "already": present,
                                                          "by": by},
                             lines=[f"{word}ことはしません（確認の一段目です）: {args.account} 承認クリエイター",
                                    f"  対象 {len(ids)} 件: " + "、".join(ids),
                                    f"  いま一覧に載っている: {len(present)} 件・実際に変わる: {len(todo)} 件",
                                    f"  by  : {by}",
                                    "  Meta は 1 つでも失敗すると 1 つも変えません",
                                    *_production_note(account_cfg)],
                             again=f"thth brand creators {args.account} {args.action} {' '.join(ids)} "
                                   f"--by {json.dumps(by, ensure_ascii=False)}")
    mw._check_confirm(args, digest)
    mw._ready(args, account_cfg)
    mw._write(args, account_cfg, "brand_creators", ig.BRAND_PERMISSION,
              lambda: adapter.brand_creators_change(change, ids))
    if args.json:
        mw._print_json({"ok": True, "changed": True, "account": args.account, "action": args.action,
                        "count": len(ids), "by": by})
    else:
        print(f"{word}ました: {args.account} 承認クリエイター {len(ids)} 件（{by}）")
    return 0


# ------------------------------------------------------------------ brand promote

def cmd_promote(args) -> int:
    if args.sponsor and not (args.allow or args.deny):
        _bad("--sponsor だけでは何も書きません（読むだけになり、--sponsor が使われません）。許すなら --allow、取り消すなら --deny を"
             f"付けてください: thth brand promote {args.account} {args.media_id} --allow --sponsor {args.sponsor} --by <名前>")
    account_cfg, adapter_cls, token = mw._load(args, "branded_content", "ブランドコンテンツ")
    media = account_cfg.get("media")
    media_id = _digits(args.media_id, "投稿（media）")
    mw._need(args, adapter_cls, token, [ig.CREATOR_PERMISSION, "instagram_basic"])
    adapter = mw._adapter(account_cfg, token)
    approved = mw._read("広告に使う許可", lambda: adapter.promote_approved(media_id))
    if not (args.allow or args.deny):
        _note(args, "brand_promote", media, ig.CREATOR_PERMISSION)
        if args.json:
            mw._print_json({"ok": True, "account": args.account, "media_id": media_id, "approved": approved,
                            "approved_count": len(approved), "storage": "display_only"})
            return 0
        print(f"{args.account}  投稿 {media_id} を広告に使ってよいと許したブランド {len(approved)} 件（保存しません）")
        for value in approved:
            print(f"  ブランド id={value}")
        print(f"  許す・取り消すとき: thth brand promote {args.account} {media_id} --allow|--deny --sponsor <ブランドの id> --by <名前>")
        return 0
    by = mw._by(args)
    sponsor = _digits(args.sponsor, "--sponsor（ブランドのビジネス口座）")
    allow = bool(args.allow)
    digest = mw.digest_of("brand_promote", args.account, media_id, sponsor, "allow" if allow else "deny",
                          ",".join(sorted(approved)))
    if not args.confirm:
        return mw._stage_one(args, digest=digest, payload={"media_id": media_id, "sponsor_id": sponsor,
                                                          "allow": allow, "already": sponsor in approved, "by": by},
                             lines=[f"{'許し' if allow else '取り消し'}ません（確認の一段目です）: {args.account} 投稿 {media_id}",
                                    f"  ブランド: {sponsor}（いま許可 {'あり' if sponsor in approved else 'なし'}）",
                                    f"  やること: そのブランドが、この投稿を広告（パートナーシップ広告）に使うのを"
                                    f"{'許す' if allow else '取り消す'}",
                                    f"  by  : {by}", *_production_note(account_cfg)],
                             again=f"thth brand promote {args.account} {media_id} {'--allow' if allow else '--deny'} "
                                   f"--sponsor {sponsor} --by {json.dumps(by, ensure_ascii=False)}")
    mw._check_confirm(args, digest)
    mw._ready(args, account_cfg)
    mw._write(args, account_cfg, "brand_promote", ig.CREATOR_PERMISSION,
              lambda: adapter.promote_set(media_id, sponsor, allow))
    if args.json:
        mw._print_json({"ok": True, "changed": True, "account": args.account, "media_id": media_id,
                        "allowed": allow, "by": by})
    else:
        print(f"{'許しました' if allow else '取り消しました'}: {args.account} 投稿 {media_id}（{by}）")
    return 0


# ------------------------------------------------------------------ brand ads-permissions / content

def cmd_ads_permissions(args) -> int:
    account_cfg, adapter_cls, token = mw._load(args, "branded_content", "ブランドコンテンツ")
    media = account_cfg.get("media")
    handle = args.creator
    if handle is not None and not HANDLE.fullmatch(str(handle).strip()):
        _bad("--creator は Instagram の username（英数・.・_ の 30 字まで）です")
    mw._need(args, adapter_cls, token, [ig.BRAND_ADS_PERMISSION, "instagram_basic", "business_management"])
    adapter = mw._adapter(account_cfg, token)
    rows = mw._read("広告の許可の一覧", lambda: adapter.ad_permissions(creator_username=handle))
    _note(args, "brand_ads_permissions", media, ig.BRAND_ADS_PERMISSION)
    counts = {}
    for row in rows:
        counts[row["status"] or "—"] = counts.get(row["status"] or "—", 0) + 1
    if args.json:
        mw._print_json({"ok": True, "account": args.account, "permissions": rows, "count": len(rows),
                        "by_status": counts, "storage": "display_only"})
        return 0
    print(f"{args.account}  広告に使ってよいの許可 {len(rows)} 件（先頭 1 頁・保存しません）"
          + ("  " + "・".join(f"{k} {v}" for k, v in sorted(counts.items())) if counts else ""))
    for row in rows:
        print(f"  {row['status'] or '—'}  クリエイター id={row['creator_id'] or '—'}")
    if not rows:
        print("  0 件です（許可がまだ無いだけで、読みは通っています）")
    return 0


def cmd_content(args) -> int:
    account_cfg, adapter_cls, token = mw._load(args, "partnership_content", "提携広告に使える投稿")
    media = account_cfg.get("media")
    business = _digits(args.business, "--business（business-id）")
    limit = _limit(args.limit, 25)
    mw._need(args, adapter_cls, token, list(adapter_cls.CONTENT_PERMISSIONS))
    adapter = mw._adapter(account_cfg, token)
    rows = mw._read("提携広告に使える投稿", lambda: adapter.partnership_content(business, limit=limit))
    _note(args, "brand_content", media, adapter_cls.ADS_BRAND_PERMISSION)
    if args.json:
        mw._print_json({"ok": True, "account": args.account, "business_id": business, "content": rows,
                        "count": len(rows), "storage": "display_only"})
        return 0
    print(f"{args.account}  提携広告に使える投稿 {len(rows)} 件（先頭 1 頁・本文は頼まない・保存しません）")
    for row in rows:
        print(f"  content_id={row['content_id']}  {row.get('platform') or '—'}  {row.get('media_type') or '—'}  "
              f"{row.get('post_type') or '—'}  {row.get('ad_usage') or '—'}")
    return 0


# ------------------------------------------------------------------ creators search

def _limit(value, default) -> int:
    if value is None:
        return default
    if not 1 <= int(value) <= LIMIT_MAX:
        _bad(f"--limit は 1〜{LIMIT_MAX} です")
    return int(value)


def cmd_search(args) -> int:
    account_cfg, adapter_cls, token = mw._load(args, "creator_marketplace", "クリエイターマーケットプレイス")
    media = account_cfg.get("media")
    country = None
    if args.country:
        if not COUNTRY.fullmatch(args.country.strip()):
            _bad("--country は 2 字の国コード（例 JP）です")
        country = args.country.strip().upper()
    query = _clean_query(args.q)
    acting = _digits(args.acting_business, "--acting-business") if args.acting_business else None
    limit = _limit(args.limit, 10)
    mw._need(args, adapter_cls, token, list(adapter_cls.MARKET_PERMISSIONS))
    adapter = mw._adapter(account_cfg, token)
    try:
        rows = mw._read("クリエイターの検索", lambda: adapter.creator_search(
            country=country, query=query, acting_business_id=acting, limit=limit))
    except adapter_base.PermissionMissing as exc:
        mw._refuse("creator_marketplace_not_ready",
                   f"Meta が断りました（{exc.permission}）。鍵に権限があっても、ブランドが onboard 前か資格の外だと断られます",
                   rc=1, permission=exc.permission)
    _note(args, "creators_search", media, adapter_cls.MARKET_PERMISSION)
    if args.json:
        mw._print_json({"ok": True, "account": args.account, "creators": rows, "count": len(rows),
                        "storage": "display_only"})
        return 0
    print(f"{args.account}  クリエイター {len(rows)} 件（id まで・名前と自己紹介は頼まない・保存しません）")
    for row in rows:
        print(f"  creator_id={row['creator_id']}")
    print("  Standard Access の間は、Meta が試験用（模擬）のデータを返します")
    return 0


def _clean_query(text):
    if text is None:
        return None
    value = str(text).strip()
    if not value or len(value) > QUERY_MAX or any(ord(c) < 32 or ord(c) == 127 for c in value):
        _bad(f"--q は制御文字なしの 1〜{QUERY_MAX} 字です")
    return value


# ------------------------------------------------------------------ 登録

def _flags(parser, *, write=False) -> None:
    if write:
        parser.add_argument("--by", default=None, help="誰が頼んだか（書く命令は必須・記録には残さない）")
        parser.add_argument("--confirm", default=None, help="一段目が表示した digest。これが無いと何もしない")
        parser.add_argument("--wait", type=mw.lock_mod.wait_seconds, default=0, help="ロックを待つ秒数（既定 0）")
    parser.add_argument("--json", action="store_true")


def register(sub) -> None:
    """`thth/cli.py` の `build_parser()` から 1 行で呼ばれる。"""
    brand = sub.add_parser("brand", help="ブランドコンテンツ（承認クリエイター・広告に使う許可・提携広告の投稿・"
                                         "書く口は二段確認）")
    brand_sub = brand.add_subparsers(dest="brand_command", required=True)

    creators = brand_sub.add_parser("creators", help="承認クリエイターの一覧（list は読む・approve/remove は二段確認）")
    creators.add_argument("account")
    creators.add_argument("action", choices=("list", "approve", "remove"))
    creators.add_argument("ids", nargs="*", help="クリエイターの Instagram の数字の id（コンマ区切りも可・100 まで）")
    _flags(creators, write=True)
    creators.set_defaults(func=mw._run(cmd_creators))

    promote = brand_sub.add_parser("promote", help="投稿を広告に使ってよいとブランドに許す・取り消す"
                                                   "（旗なしは読む・--allow/--deny は二段確認）")
    promote.add_argument("account")
    promote.add_argument("media_id", help="タイアップ投稿の Instagram の media の数字の id")
    group = promote.add_mutually_exclusive_group()
    group.add_argument("--allow", action="store_true", help="そのブランドが広告に使うのを許す")
    group.add_argument("--deny", action="store_true", help="許可を取り消す")
    promote.add_argument("--sponsor", default=None, help="--allow/--deny で対象にするブランドのビジネス口座の id")
    _flags(promote, write=True)
    promote.set_defaults(func=mw._run(cmd_promote))

    perms = brand_sub.add_parser("ads-permissions", help="口座の段の「広告に使ってよい」の許可の一覧（読むだけ）")
    perms.add_argument("account")
    perms.add_argument("--creator", default=None, help="このクリエイターの username だけに絞る")
    _flags(perms)
    perms.set_defaults(func=mw._run(cmd_ads_permissions))

    content = brand_sub.add_parser("content", help="提携広告に使える投稿（Content Discovery API・読むだけ）")
    content.add_argument("account")
    content.add_argument("--business", required=True, help="business-id（数字）")
    content.add_argument("--limit", type=int, default=None, help="件数（1〜50・既定 25・先頭 1 頁）")
    _flags(content)
    content.set_defaults(func=mw._run(cmd_content))

    market = sub.add_parser("creators", help="クリエイターマーケットプレイス（読むだけ）")
    market_sub = market.add_subparsers(dest="creators_command", required=True)
    search = market_sub.add_parser("search", help="クリエイターを探す（id まで・Standard は模擬のデータ）")
    search.add_argument("account")
    search.add_argument("--country", default=None, help="クリエイターの国（2 字・例 JP）")
    search.add_argument("--q", default=None, help="検索の語")
    search.add_argument("--acting-business", dest="acting_business", default=None,
                        help="代理店が顧客の代わりに探すときの business id（Instagram のみ・公式の頁では未確認）")
    search.add_argument("--limit", type=int, default=None, help="件数（1〜50・既定 10）")
    _flags(search)
    search.set_defaults(func=mw._run(cmd_search))
