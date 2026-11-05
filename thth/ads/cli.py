"""`thth ads …`（計画 段 4・段 5）。

    thth ads add <名前> --act-id <数字> --project <project> [--page <口座>]… [--ig <口座>]… --by <名前>
    thth ads auth <名前> --by <名前> [--code <戻り URL>] [--exchange]
    thth ads accounts <名前> [--json]          鍵で見える広告アカウントの一覧（/me/adaccounts）
    thth ads doctor <名前> [--json]            台帳・鍵・権限・読めるか（読むだけ・鍵の期限の欄は今の値で書き直す）
    thth ads show <名前> [--json] [--offline]  アカウント・spend_cap・amount_spent・動いている物
    thth ads insights <名前> [--preset last_30d|maximum|…] [--since YYYY-MM-DD [--until YYYY-MM-DD]] [--level account] [--json]
    thth ads collect [<名前>] [--json]         読みを snapshot.json に残す（collect のたびにも回る）
    thth ads insights <名前> --by age-gender|region|country|placement|device|hour   内訳（5.6.3）
    thth ads estimate <名前> --targeting <JSON|@ファイル> [--goal LINK_CLICKS|…] [--json]
                                                出す前の見積もり（狙う相手の大きさ・予算あたりの見込み・読むだけ）
    thth ads skill [--print] [--json]          広告の手順の skill（thth-ads）の場所と全文
    thth ads catalog list <名前> [--json]      商品カタログと商品数（計画 Meta 全部盛り M3 の 4 つは読むだけ・保存しない）
    thth ads catalog products <名前> --catalog <id> [--limit N] [--json]
    thth ads leads <名前> [--page <id>] [--form <id>] [--limit N] [--json]   リードは端末に出すだけ
    thth ads network <名前> [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--json]   Audience Network の数
    thth ads shop <名前> [--json]              ショップの設定

**広告の手順は skill thth-ads**（`thth ads skill --print`）。広告の命令の次の一手とエラーの文は、先頭にそれを示す。

段 5（書く・お金が動くのは activate と extend だけ）:

    thth ads boost <名前> (--post <post_id> | --object-story-id <ページID>_<数字>) --lifetime <額>
                   --start <時刻|now> --end <時刻> [--platforms facebook[,instagram]] [--objective engagement|traffic|reach] --by <名前>
                                                全部 PAUSED で作る（お金は動かない）
    thth ads activate <名前> <campaign_id> --by <名前>                       1 段目: digest を出す
    thth ads activate <名前> <campaign_id> --confirm <digest> --approval <hub の番号> --by <名前>
                                                2 段目: Meta から木を読み直して一致したら始める
    thth ads extend <名前> <adset_id> [--end <時刻>] [--lifetime <額>] --by <名前> [--confirm … --approval …]
    thth ads pause <名前> (--all | --id <id>…) --by <名前>        止める（承認なし・止めた口座でも・待たない）
    thth ads stop <名前> --by <名前>             全部止める → THTH を止める → 読み直して確かめる → spend_cap を下げる
    thth ads budget show <名前>                  今月の消費の見積もり（請求書ではない）
    thth ads budget lower <名前> (--monthly <額> | --lifetime-max <額> | --days-max <日>)   台帳の上限を下げる
    thth ads budget lower <名前> --adset <id> (--lifetime <額> | --daily <額>)              広告セットの予算を減らす
    thth ads end <名前> <adset_id> --end <時刻|now> --by <名前>   終了日を早める
    thth ads watch <名前> <campaign_id> [--stop-at-spend <額>] [--stop-at-impressions <数>] [--interval <秒>]
                   [--until <時刻>] --by <名前>   成績を読み続け、達したら stop と同じに止める（指定なしは始まったら知らせる）
    thth ads resume <名前> --by <名前>           1 段目: 止めの印と口座の木を出して digest（何も変えない）
    thth ads resume <名前> --confirm <digest> --approval <hub の番号> --by <名前>
                                                2 段目: THTH の止めの印だけを外す（広告は PAUSED のまま・masaru 11/02 の例外）

**上げる命令は無い**: `spend_cap` を上げる・外す、月の上限・1 操作の上限・`ads_enabled`・`production` を
上げるのは THTH の外（`spend_cap` は管理画面で masaru、上限と旗は VM の台帳 `adaccounts/<名前>.json` を
`cp -a` で控えてから直接書く・D-1 の 3）。`budget lower` は今より小さい値だけを受ける。**例外は 1 つ**:
止めた印を外す `resume`（masaru 11/02）。2 段＋hub の番号が必須で、外すのは理由 owner の印だけ（広告は
ACTIVE にしない・spend_cap・上限・旗に触らない）。ほかの印は VM で `guard/stopped.json` を控えてから消す。

MCP に出すのは show・boost（PAUSED まで）・pause・stop だけ。activate・extend・resume・watch は CLI で masaru か運用が押す。
金額は最小単位の整数（JPY なら円）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .. import accounts, admin_log, jst, redact, refusal
from .. import scopes as meta_scopes
from ..meta import errors as meta_errors
from ..meta import tokens as meta_tokens
from ..meta import usage as meta_usage
from . import graphread, graphwrite, guide, ledger, watch

# **命令の全部**（`tests/test_meta_stage5.py` がこの集合と parser の一致を縛る）。
READ_VERBS = ("accounts", "add", "auth", "doctor", "show", "insights", "collect", "estimate", "skill",
              "catalog", "leads", "network", "shop",
              # 設計 広告の資格と遠くの道 段 5: 退出（Meta には書かない・`add` と同じく手元の台帳と資格の操作）。
              "leave")
# 段 5 の書く命令。Meta に書くのは `ops.py` の決まった関数だけ（`graphwrite.ALLOWED_CALLERS`）。
# `watch` は指定に達したら `ops.stop` を呼ぶので書く側。`resume` は止めの印を外す唯一の例外（masaru 11/02）。
WRITE_VERBS = ("boost", "activate", "extend", "pause", "stop", "budget", "end", "watch", "resume")
VERBS = READ_VERBS + WRITE_VERBS
# MCP に出す道具（**activate・extend・budget・end は出さない**・段 5 の 7）。
MCP_TOOLS = ("thth_ads_show", "thth_ads_boost", "thth_ads_pause", "thth_ads_stop")
EXPECTED_SCOPES = ("ads_read", "ads_management", "business_management")
# 計画 Meta 全部盛り M3 の読む口の断り（`graphread.AdsReadError` の符丁のうち 3 つを出すもの）。
READ_REFUSALS = ("ads_business_unknown", "ads_page_unknown", "ads_form_other_page", "ads_catalog_other_business")
LIST_DEFAULT = 25


def _fail(reason, *, json_output=False) -> int:
    """広告の命令の断り・エラー。**先頭の行は skill thth-ads の在り処**（masaru 11/03「読めとかやれとか言う前に
    skills を提示するべき」）。2 行目からが個別の理由。"""
    print(guide.skill_line(), file=sys.stderr)
    print(redact.redact(str(reason)), file=sys.stderr)
    return 2


def _emit(value, json_output, render) -> None:
    if json_output:
        print(json.dumps(value, ensure_ascii=False))
    else:
        render(value)


def _reader(name):
    cfg = ledger.load(name)
    token = ledger.load_token(cfg)
    if not token or token.get("unreadable"):
        raise graphread.AdsReadError(f"ads_token_missing: 鍵がありません。thth ads auth {name} --by <名前>")
    return cfg, token, graphread.Reader(name, cfg, token)


def _error_text(exc) -> str:
    return redact.redact(str(exc))[:300]


# ------------------------------------------------------------------ add

def cmd_add(args) -> int:
    try:
        by = admin_log.actor(getattr(args, "by", None))
    except ValueError as exc:
        return _fail(exc)
    name = args.name
    if not accounts.name_is_safe(name):
        return _fail(f"invalid_adaccount_name: 名前に使えない字が入っています: {name!r}")
    act_id = (args.act_id or "").removeprefix("act_")
    if not ledger.ACT_ID_RE.fullmatch(act_id):
        return _fail("act_id_invalid: --act-id は広告マネージャの URL の act= の数字です")
    if name in accounts.list_account_names():
        return _fail(f"adaccount_name_taken: {name} は THTH の口座の名前です。広告の口座には別の名前を"
                     "（例 kopicha-ads）")
    try:
        data = ledger.build(name, act_id=act_id, project=args.project, pages=args.page or [],
                            ig_accounts=args.ig or [], business_id=args.business_id)
        ledger.validate(name, data)
    except ledger.AdAccountError as exc:
        return _fail(f"{exc.code}: {exc}")
    path = ledger.path_for(name)
    if os.path.exists(path):
        return _fail(f"既にあります。上書きしません: {path}")
    data["provenance"] = admin_log.provenance(by)
    try:
        ledger.write_new(name, data)
    except OSError as exc:
        return _fail(f"台帳を書けませんでした: {path}（{exc.strerror or exc}）")
    try:
        admin_log.append("adaccount_added", name, {"media": ledger.MEDIA}, by=by,
                         diff=admin_log.difference({}, {k: v for k, v in data.items() if k != "provenance"}))
    except (OSError, ValueError, admin_log.AdminLogError):
        try:
            os.unlink(path)
        except OSError:
            pass
        return _fail("admin_change_refused: --by and a private writable event log are required")
    if args.json:
        print(json.dumps({"path": path, "adaccount": data}, ensure_ascii=False))
        return 0
    print(f"書きました: {path}（上限は 0・ads_enabled と production は false。上げるのは VM の台帳を直接書く）")
    print(f"次の一手: thth ads auth {name} --by <名前>（許可の画面で広告アカウントに印）→ thth ads show {name}")
    return 0


# ------------------------------------------------------------------ leave

def cmd_leave(args) -> int:
    """`thth ads leave <名前> --by <名前>`（止める → thth.me の鍵を無効に → 台帳・鍵・状態を消す → 資格の行を消す）。"""
    from .. import relay
    from . import leave
    try:
        result = leave.run(args.name, by=args.by)
    except (OSError, ValueError, accounts.AccountError, relay.RelayError, admin_log.AdminLogError) as exc:
        detail = " ".join(str(exc).split())[:200] or type(exc).__name__
        if getattr(args, "json", False):
            print(json.dumps({"adaccount": args.name, "completed": False, "reason": "leave_incomplete",
                              "detail": detail}, ensure_ascii=False))
        return _fail(f"leave_incomplete: {detail}。止めた状態のまま残しています。同じ thth ads leave を打ち直してください")
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False))
        return 0
    print(f"local_complete: {args.name}（台帳・鍵・状態と thth.me の鍵を消しました）")
    print("remote_unconfirmed: Meta の側の許可は本人が外してください（Facebook の設定 → ビジネス連携で THTH を削除）")
    return 0


# ------------------------------------------------------------------ auth

def cmd_auth(args) -> int:
    from . import auth
    if args.exchange:
        return auth.run_exchange(args.name, by=args.by, log=lambda line: print(line, file=sys.stderr))
    return auth.run_auth(args.name, by=args.by, code=args.code,
                         log=lambda line: print(line, file=sys.stderr), human_output=print)


# ------------------------------------------------------------------ accounts

def cmd_accounts(args) -> int:
    try:
        _cfg, _token, reader = _reader(args.name)
        rows = reader.adaccounts()
    except (accounts.AccountError, graphread.AdsReadError, meta_errors.RateLimited, ValueError) as exc:
        return _fail(_error_text(exc))

    def render(value):
        print(f"この鍵で見える広告アカウント {len(value['adaccounts'])} 件（{args.name} の鍵）")
        for row in value["adaccounts"]:
            print(f"  act_{row['act_id']}  {row['name']}  {row['currency']}  {row['timezone_name']}"
                  f"  account_status={row['account_status']}")
    _emit({"adaccount": args.name, "adaccounts": rows}, args.json, render)
    return 0


# ------------------------------------------------------------------ 計画 Meta 全部盛り M3（読むだけ・保存しない）

def _record_read(name, action, ok) -> None:
    """広告の口座の runs に 1 行（命令の名前・結果だけ。カタログ・リード・数・設定の中身は入れない）。
    `permission_watch` が `adaccount_state/<名前>/` のここを数える。書けなくても読む口は止めない。"""
    from .. import runs as runs_mod
    now = jst.now_jst()
    try:
        runs_mod.append_run(ledger.ensure_state_dir(name), {
            "account": name, "run_id": f"{action}-{jst.iso(now)}", "mode": "read", "action": action,
            "medium": "ads", "status": "ok" if ok else "error", "error": None}, jst.month_str(now))
    except (OSError, ValueError, accounts.AccountError):
        pass


def _read_only(args, call, render, action) -> int:
    """読むだけの口の枠: 読んで出すだけ（snapshot にも台帳にも書かない。残すのは runs の 1 行だけ）。"""
    try:
        _cfg, _token, reader = _reader(args.name)
        value = call(reader)
    except graphread.AdsReadError as exc:
        if exc.code in READ_REFUSALS:
            print(guide.skill_line(), file=sys.stderr)
            return refusal.emit(args, exc.code)
        _record_read(args.name, action, False)
        return _fail(_error_text(exc))
    except (accounts.AccountError, meta_errors.RateLimited, ValueError) as exc:
        return _fail(_error_text(exc))
    _record_read(args.name, action, True)
    _emit({"adaccount": args.name, "storage": "display_only", **value}, args.json, render)
    return 0


# 人向けの画面（手元の道と遠くの道 `thth/remote.py` が同じ関数を使う・設計 広告の資格と遠くの道 段 4）。
# 引数は答えの JSON だけ（`args` を見ない）。

def render_catalog_list(value):
    print(f"商品カタログ {len(value['catalogs'])} 件（{value['adaccount']}）")
    for row in value["catalogs"]:
        print(f"  {row['id']}  {row['name']}  商品 {row['product_count']}")


def render_catalog_products(value):
    print(f"商品 {len(value['products'])} 件（カタログ {value['catalog']}・先頭の 1 頁）")
    for row in value["products"]:
        print(f"  {row.get('id')}  {row.get('retailer_id') or '—'}  {row.get('name') or '—'}")


def cmd_catalog(args) -> int:
    if args.catalog_verb == "list":
        return _read_only(args, lambda reader: {"catalogs": reader.catalogs()}, render_catalog_list, "ads_catalog")
    return _read_only(args, lambda reader: {"catalog": args.catalog,
                                            "products": reader.catalog_products(args.catalog, args.limit)},
                      render_catalog_products, "ads_catalog")


def _lead_page(reader, wanted) -> str:
    """`--page` か、台帳に結びつけたページの id（1 つに決まらなければ断る）。"""
    if wanted:
        return wanted
    pages = []
    for name in reader.cfg.get("pages") or []:
        page_id = accounts.load_account(name).get("page_id")
        if page_id:
            pages.append(str(page_id))
    if len(pages) != 1:
        raise graphread.AdsReadError("ads_page_unknown")
    return pages[0]


def render_leads(value):
    if value.get("forms") is not None:
        print(f"リードのフォーム {len(value['forms'])} 件（ページ {value['page']}）")
        for row in value["forms"]:
            print(f"  {row['id']}  {row['status']}  件数 {row['leads_count']}  {row['name']}")
        return
    print(f"リード {len(value['leads'])} 件（フォーム {value['form']}・先頭の 1 頁・端末に出すだけで残しません）")
    for row in value["leads"]:
        print(f"  {row['created_time']}  {row['id']}  {json.dumps(row['fields'], ensure_ascii=False)}")


def cmd_leads(args) -> int:
    def call(reader):
        page = _lead_page(reader, args.page)
        if args.form:
            return {"page": page, "form": args.form, "leads": reader.leads(page, args.form, args.limit)}
        return {"page": page, "forms": reader.lead_forms(page)}
    return _read_only(args, call, render_leads, "ads_leads")


def render_network(value):
    print(f"Audience Network の数（{value['adaccount']}・省略 {value['omitted']} 行）")
    for row in value["rows"]:
        print(f"  {row['metric']}  {row['value']}")


def cmd_network(args) -> int:
    return _read_only(args, lambda reader: reader.audience_network(args.since, args.until), render_network,
                      "ads_network")


def render_shop(value):
    print(f"ショップの設定 {len(value['shops'])} 件（{value['adaccount']}）")
    for row in value["shops"]:
        print(f"  {row.get('id')}  {row.get('cta') or '—'}  {row.get('shop_setup') or '—'}")


def cmd_shop(args) -> int:
    return _read_only(args, lambda reader: {"shops": reader.shop_settings()}, render_shop, "ads_shop")


# ------------------------------------------------------------------ show

def _ledger_view(name, cfg) -> dict:
    return {"act_id": cfg["act_id"], "project": cfg.get("project"), "currency": cfg.get("currency"),
            "timezone_name": cfg.get("timezone_name"), "production": bool(cfg.get("production")),
            "ads_enabled": bool(cfg.get("ads_enabled")), "ceilings": ledger.ceilings(cfg),
            "pages": cfg.get("pages") or [], "ig_accounts": cfg.get("ig_accounts") or [],
            "writes_refused": ledger.write_refusal(cfg), "stopped": ledger.stopped(name)}


def _tree_view(tree) -> dict:
    by_status = {}
    for key in ("campaigns", "adsets", "ads"):
        counts = {}
        for row in tree.get(key) or []:
            status = row.get("effective_status") or "?"
            counts[status] = counts.get(status, 0) + 1
        by_status[key] = counts
    return {"complete": tree.get("complete"), "by_effective_status": by_status,
            "active_ids": watch.active_ids(tree)}


def render_show(v):
    led = v["ledger"]
    print(f"広告の口座 {v['adaccount']}  act_{led['act_id']}  project {led['project']}")
    print(f"  台帳: 通貨 {led['currency'] or '未'}・時間帯 {led['timezone_name'] or '未'}・"
          f"ads_enabled {led['ads_enabled']}・production {led['production']}・上限 {led['ceilings']}"
          f"（{v['unit']}）")
    print(f"  書く操作: {'断る（' + led['writes_refused'] + '）' if led['writes_refused'] else '柵つき（boost は PAUSED・activate は 2 段）'}"
          f"・止め {led['stopped']['reason'] if led['stopped'] else 'なし'}")
    tok = v["token"]
    print(f"  鍵: {tok['state']}・{watch.token_text(tok)}")
    if v.get("account"):
        a = v["account"]
        print(f"  Meta: {a['name']}・通貨 {a['currency']}・時間帯 {a['timezone_name']}・"
              f"account_status {a['account_status']}・amount_spent {a['amount_spent']}・"
              f"spend_cap {a['spend_cap']}" + ("（0 = 上限なし。管理画面で入れる）" if a["spend_cap"] == 0 else ""))
        extras = v.get("account_extras") or {}
        print("  ほかの欄: " + "・".join(f"{k} {extras[k]}" for k in extras if k != "unavailable")
              + (f"（読めなかった: {','.join(extras['unavailable'])}）" if extras.get("unavailable") else ""))
        if v.get("ledger_mismatch"):
            print(f"  注意: 台帳と Meta が違う欄: {','.join(v['ledger_mismatch'])}")
        t = v["tree"]
        print(f"  木: {t['by_effective_status']}" + ("" if t["complete"] else "（全部は読めていない）"))
        _render_delivery(v.get("delivery") or {})
        _render_pixels(v)
    elif v.get("snapshot"):
        s = v["snapshot"]
        print(f"  最後の読み: {s.get('observed_at')}" + (f"（読めなかった: {s.get('error')}）" if s.get("error") else ""))
    for row in v["events"]:
        print(f"  要対応 {row['kind']}: {row['summary']}")
    _render_steps(v.get("next_steps"))


def cmd_show(args) -> int:
    now = jst.now_jst()
    try:
        cfg = ledger.load(args.name)
    except accounts.AccountError as exc:
        return _fail(_error_text(exc))
    value = {"adaccount": args.name, "ledger": _ledger_view(args.name, cfg),
             "token": watch.token_state(cfg, now=now), "unit": "最小単位の整数（JPY なら円）",
             "skill": guide.skill_ref(), "next_steps": [guide.skill_step()]}
    if args.offline:
        value["source"] = "snapshot"
        value["snapshot"] = watch.read_snapshot(args.name)
    else:
        try:
            _cfg, _token, reader = _reader(args.name)
            value["source"] = "meta"
            value["account"] = reader.account()
            value["account_extras"] = reader.account_extras()
            tree = reader.tree()
            value["tree"] = _tree_view(tree)
        except (accounts.AccountError, graphread.AdsReadError, meta_errors.RateLimited, ValueError) as exc:
            return _fail(_error_text(exc))
        value["delivery"] = watch.delivery_report(reader, tree, now=now)
        try:
            value["pixels"], pixel_error = reader.pixels(), None
        except (graphread.AdsReadError, meta_errors.RateLimited) as exc:
            value["pixels"], pixel_error = None, getattr(exc, "code", None) or type(exc).__name__
        value["pixel_ready"] = guide.pixel_ready(value["pixels"])
        value["next_steps"] = ([guide.skill_step()] if value["pixel_ready"] else
                               guide.pixel_steps(args.name, value["pixels"], landing_url=cfg.get("landing_url"),
                                                 error=pixel_error))
        mismatch = [key for key in ("currency", "timezone_name")
                    if cfg.get(key) is not None and value["account"].get(key) not in (None, cfg[key])]
        value["ledger_mismatch"] = mismatch
    # 要対応: 今読んだ値から（`--offline` は最後の snapshot から）。snapshot は書かない（書くのは collect）。
    basis = (value["snapshot"] if args.offline else
             {"account": value["account"], "tree": tree, "insights": (watch.read_snapshot(args.name) or {}).get("insights")})
    value["events"] = watch.events(args.name, cfg, basis, now=now,
                                   make=lambda kind, ref, summary, **extra: {"kind": kind, "summary": summary})

    _emit(value, args.json, render_show)
    return 0


def _render_pixels(v) -> None:
    pixels = v.get("pixels")
    if pixels is None:
        print("  ピクセル: 読めませんでした")
    elif not pixels:
        print("  ピクセル: ありません（サイトへの誘導の頁が開いた数は測れない）")
    else:
        print("  ピクセル: " + "・".join(f"{p['id']}（最後に動いた {p.get('last_fired_time') or 'まだ無い'}"
                                       + ("・使えない" if p.get("is_unavailable") else "") + "）" for p in pixels))


def _render_delivery(d) -> None:
    for row in d.get("adsets") or []:
        if row.get("error"):
            print(f"  配信の診断 {row['id']}: 読めませんでした（{row['error']}）")
            continue
        print(f"  配信の診断 {row['id']}: 予算 {row.get('budget')}（{row.get('budget_kind')}）・残り "
              f"{row.get('budget_remaining')}・表示 {row.get('impressions')}（全期間）・開始から "
              f"{row.get('hours_since_start')} 時間・見込み {row.get('delivery_estimate')}"
              + (f"・問題 {row['issues']}" if row.get("issues") else ""))
    for line in d.get("warnings") or []:
        print(f"  警告: {line}")


# ------------------------------------------------------------------ insights

def _excludes_today(cfg, preset, since, until) -> bool | None:
    """期間が今日（広告アカウントの時間帯の日）を含まないか。分からなければ None。"""
    if since is not None or until is not None:
        return (until or "") < watch.local_day(cfg, jst.now_jst())
    if preset in graphread.PRESETS_WITHOUT_TODAY:
        return True
    if preset in ("today", "maximum"):
        return False
    return None


ENGAGEMENT_NOTE = ("engagement は Meta の actions から: reactions=post_reaction・comments=comment・shares=post・"
                   "photo_views=photo_view・page_likes=like。行の無い種類は 0。行に actions の欄が無くても、同じ読みの"
                   "ほかの行に actions が返っていれば 0（Meta は反応が 1 件も無い区分では欄ごと返さない・"
                   "actions_absent_as_zero が true・site.why は actions_absent_as_zero）。読み全体で actions が 1 行も"
                   "返らない・読めないときは engagement が null で engagement_unknown に理由。site はサイトへの誘導: link_clicks=link_click・"
                   "outbound_clicks=outbound_clicks の outbound_click・landing_page_views=landing_page_view・"
                   "inline_link_clicks。site.why が値の訳（reported=Meta が返した・zero_reported_list=並びに行が無い"
                   "ので 0・no_link_clicks=リンクが押されていないので 0・not_measured_or_left=リンクは押されたのに頁が"
                   "開いた記録が無い＝頁の計測が無いか開く前に離れた・null・*_missing など=欄が無い）。clicks は広告の"
                   "どこかを押した数でリンクのクリックではない。cost_per は 1 件あたりの spend（spend と同じ普通の単位・"
                   "小数 2 桁の文字列・0 件や不明なら null）。actions・outbound_clicks_raw は Meta の生の行")


def render_insights(v):
    period = f"{v['since']}〜{v['until']}" if v["since"] else v["preset"]
    print(f"成績 {v['adaccount']}  {period}・{v['level']}（通貨 {v['currency'] or '未'}）"
          + (f"・内訳 {v['breakdown']}" if v.get("breakdown") else ""))
    if v.get("breakdown"):
        _render_breakdown(v)
        return
    for row in v["rows"]:
        head = f"{row.get('name') or row.get('id')}  " if v["level"] != "account" else ""
        print(f"  {head}spend {row['spend']}（最小単位 {row['spend_minor']}）・impressions {row['impressions']}・"
              f"clicks {row['clicks']}（広告のどこかを押した数・リンクとは限らない）・reach {row['reach']}"
              f"（{row['date_start']}〜{row['date_stop']}）")
        for line in graphread.engagement_text(row, v["currency"]):
            print(f"    {line}")
    if not v["rows"]:
        print("  行がありません（この期間に配信が無い）")
        if v.get("note"):
            print(f"  注意: {v['note']}")
    print(f"  成績の読みの使用率: {v['ads_insights_util_pct']}%")
    _render_steps(v.get("next_steps"))


def cmd_insights(args) -> int:
    since, until = args.since, args.until
    if until is not None and since is None:
        return _fail("ads_time_range_invalid: --until には --since も要ります（YYYY-MM-DD）")
    try:
        cfg, _token, reader = _reader(args.name)
        if since is not None and until is None:
            until = watch.local_day(cfg, jst.now_jst())
        rows = reader.insights(args.preset, level=args.level, since=since, until=until, breakdown=args.by)
    except (accounts.AccountError, graphread.AdsReadError, meta_errors.RateLimited, ValueError) as exc:
        return _fail(_error_text(exc))
    excludes = _excludes_today(cfg, args.preset, since, until)
    # 頁が開いた数が測れない（リンクは押されたのに記録が無い）ならピクセルを読み、次の一手を返す。
    steps = [guide.skill_step()]
    if any(((row.get("site") or {}).get("why") or {}).get("landing_page_views") == "not_measured_or_left"
           for row in rows):
        try:
            pixels, error = reader.pixels(), None
        except (graphread.AdsReadError, meta_errors.RateLimited) as exc:
            pixels, error = None, getattr(exc, "code", None) or type(exc).__name__
        steps = guide.pixel_steps(args.name, pixels, landing_url=cfg.get("landing_url"), error=error)
    value = {"adaccount": args.name, "preset": None if since else args.preset, "since": since, "until": until,
             "level": args.level, "breakdown": args.by,
             "breakdown_fields": graphread.BREAKDOWNS.get(args.by) if args.by else None,
             "currency": cfg.get("currency"), "excludes_today": excludes,
             "rows": rows, "ads_insights_util_pct": reader.insights_util_pct(),
             "unit": "spend は Meta の文字列（普通の単位）・spend_minor は最小単位の整数（JPY だけ直す）",
             "engagement_note": ENGAGEMENT_NOTE, "skill": guide.skill_ref(), "next_steps": steps}
    if args.by:
        value["breakdown_note"] = ("内訳つきの読みは spend・impressions・clicks・reach・actions・inline_link_clicks だけを"
                                   "頼む（効率・動画は内訳なしで読む）。hour では reach が 0 で返る（Meta の決まり）")
    if not rows and excludes:
        value["note"] = ("この期間は今日を含みません（Meta の last_7d などは昨日まで）。今日の分は --preset today か "
                         "--preset maximum で読む")

    _emit(value, args.json, render_insights)
    return 0


def _render_steps(steps) -> None:
    """次の一手（**先頭は skill**）。skill だけなら 1 行。"""
    for step in steps or []:
        print(f"次の一手: {step['text']}")


def _render_breakdown(v) -> None:
    """内訳は 1 区分 1 行（長くしない）。"""
    for row in v["rows"]:
        key = "・".join(str(x) for x in (row.get("breakdown") or {}).values())
        head = f"{row.get('name') or row.get('id')} " if v["level"] != "account" else ""
        eng = row.get("engagement") or {}
        site = row.get("site") or {}
        reactions = "不明" if row.get("engagement") is None else eng.get("reactions")
        links = "不明" if site.get("link_clicks") is None else site.get("link_clicks")
        print(f"  {head}{key}: spend {row['spend']}・表示 {row['impressions']}・リーチ {row['reach']}・"
              f"clicks {row['clicks']}・いいね・リアクション {reactions}・リンククリック {links}")
    if not v["rows"]:
        print("  行がありません（この期間に配信が無い）")
    print(f"  注意: {v['breakdown_note']}")
    print(f"  成績の読みの使用率: {v['ads_insights_util_pct']}%")
    _render_steps(v.get("next_steps"))


# ------------------------------------------------------------------ estimate・skill

def _targeting(text):
    """`--targeting` の JSON（`@ファイル` も可）。object でなければ ValueError。"""
    if text.startswith("@"):
        with open(text[1:], encoding="utf-8") as f:
            text = f.read()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("ads_targeting_invalid: --targeting は JSON の object です（例 "
                         '{"geo_locations":{"countries":["JP"]},"age_min":18}）')
    return value


def render_estimate(v):
    r, d = v["reach"], v["delivery"]
    print(f"見積もり {v['adaccount']}（読むだけ・目的 {v['goal']}）")
    if r.get("unavailable"):
        print(f"  狙う相手の大きさ: 読めませんでした（{r['unavailable']}）")
    else:
        print(f"  狙う相手の大きさ: {r.get('users_lower_bound')}〜{r.get('users_upper_bound')} 人")
    if d.get("unavailable"):
        print(f"  予算あたりの見込み: 読めませんでした（{d['unavailable']}）")
    elif not d.get("confident"):
        print("  予算あたりの見込み: Meta に確度の高い予測がありません")
    else:
        for point in d["daily_outcomes_curve"][:10]:
            print("  1 日 " + "・".join(f"{k} {point[k]}" for k in point))
    print(f"  注意: {v['note']}")
    _render_steps(v["next_steps"])


def cmd_estimate(args) -> int:
    """出す前の見積もり（読むだけ）: 狙う相手の大きさ（reachestimate）と予算あたりの見込み（delivery_estimate）。"""
    try:
        targeting = _targeting(args.targeting)
        _cfg, _token, reader = _reader(args.name)
    except (accounts.AccountError, graphread.AdsReadError, ValueError, OSError) as exc:
        return _fail(_error_text(exc))
    value = {"adaccount": args.name, "targeting": targeting, "goal": args.goal, "skill": guide.skill_ref()}
    for key, call in (("reach", lambda: reader.reach_estimate(targeting)),
                      ("delivery", lambda: reader.delivery_estimate(targeting, args.goal))):
        try:
            value[key] = call()
        except (graphread.AdsReadError, meta_errors.RateLimited) as exc:
            value[key] = {"unavailable": getattr(exc, "code", None) or type(exc).__name__}
    value["note"] = ("reach は狙う相手の人数の幅（-1 は出せない・配信が止まる意味ではない）。delivery の "
                     "daily_outcomes_curve は確度の高い予測があるときだけ中身がある（confident=false なら見込みなし）")
    value["next_steps"] = [guide.skill_step()]

    _emit(value, args.json, render_estimate)
    return 0


def cmd_skill(args) -> int:
    path = guide.skill_path()
    if path is None:
        print("skill thth-ads が見つかりません（skills/thth-ads/SKILL.md）", file=sys.stderr)
        return 2
    text = None
    if args.print_text or args.json:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    if args.json:
        print(json.dumps(dict(guide.skill_ref(), body=text), ensure_ascii=False))
    elif args.print_text:
        print(text, end="")
    else:
        print(guide.skill_line())
        print(path)
    return 0


# ------------------------------------------------------------------ collect

def cmd_collect(args) -> int:
    names = [args.name] if args.name else ledger.list_names()
    now = jst.now_jst()
    out, rc = [], 0
    for name in names:
        try:
            result = watch.collect(name, now=now, force=True)
        except (accounts.AccountError, OSError, ValueError) as exc:
            out.append({"adaccount": name, "error": _error_text(exc)})
            rc = 1
            continue
        snap = result["snapshot"]
        out.append({"adaccount": name, "observed_at": snap.get("observed_at"), "error": snap.get("error"),
                    "errors": snap.get("errors"), "active": len(watch.active_ids(snap.get("tree") or {}))})
        if snap.get("error"):
            rc = 1

    def render(v):
        if not v["adaccounts"]:
            print("広告の口座がありません（adaccounts/）")
        for row in v["adaccounts"]:
            print(f"  {row['adaccount']}: " + (f"読めなかった（{row['error']}）" if row.get("error")
                                               else f"読みました（動いている物 {row['active']}）"))
    _emit({"adaccounts": out}, args.json, render)
    return rc


# ------------------------------------------------------------------ doctor

def doctor_rows(name, *, now=None) -> list:
    """読むだけで確かめる（台帳・鍵・権限・アカウント・木・成績）。`ok` は True／False／None。"""
    now = now or jst.now_jst()
    rows = []

    def row(key, label, ok, detail, **extra):
        rows.append({"key": key, "label": label, "ok": ok, "detail": redact.redact(str(detail))[:400], **extra})

    try:
        cfg = ledger.load(name)
    except accounts.AccountError as exc:
        row("ledger", "台帳", False, exc)
        return rows
    refusal = ledger.write_refusal(cfg)
    row("ledger", "台帳", True,
        f"act_{cfg['act_id']}・通貨 {cfg.get('currency') or '未（thth ads auth で写す）'}・"
        f"時間帯 {cfg.get('timezone_name') or '未'}・上限 {ledger.ceilings(cfg)}・"
        f"ads_enabled {bool(cfg.get('ads_enabled'))}・production {bool(cfg.get('production'))}・"
        f"書く操作 {'断る（' + refusal + '）' if refusal else '柵つき（boost は PAUSED・activate は 2 段）'}")
    state = watch.token_state(cfg, now=now)
    hint = f"thth ads auth {name} --by <名前>"
    if state["state"] in (watch.NO_TOKEN, watch.UNREADABLE):
        row("token", "鍵", False, f"{state['state']}（{hint}）")
        return rows
    try:
        _cfg, token, reader = _reader(name)
    except (accounts.AccountError, graphread.AdsReadError, ValueError) as exc:
        row("reader", "読む口", False, exc)
        return rows
    debug = None
    try:
        from . import auth
        app_id, app_secret, _config = auth._app_env()
        debug = auth._debug(token["access_token"], app_id, app_secret)
        if debug is None:
            raise graphread.AdsReadError("debug_token_unavailable")
    except Exception as exc:  # noqa: BLE001 — 訊けなければ「確かめられなかった」
        debug = None
        debug_error = exc
    else:
        debug_error = None
        # C-2 の 2: 保存した値でなく今の値で、`.token` の期限の欄を書き直す（鍵の値は触らない）。
        try:
            ledger.refresh_token_expiry(cfg, debug)
        except OSError:
            pass
        state = watch.token_state(cfg, now=now)
    remaining = state["remaining_days"]
    row("token", "鍵の期限（鍵とデータに触れる期限の早いほう・残り 14 日で activate を断る）",
        None if remaining is None else remaining >= watch.TOKEN_WARN_DAYS,
        watch.token_text(state)
        + ("" if remaining is None or remaining >= watch.TOKEN_WARN_DAYS else f"・14 日を切りました。{hint}"),
        remaining_days=remaining, no_expiry=state.get("no_expiry"))
    if debug is None:
        row("debug_token", "鍵の権限（/debug_token）", None,
            f"確かめられませんでした（{_error_text(debug_error)[:120]}）")
    else:
        scopes = meta_tokens.scopes_of(debug)
        if scopes is None:
            row("debug_token", "鍵の権限（/debug_token）", None, "応答に scopes の一覧がありません")
        else:
            # 招待の口座は招待で求めた一覧と突き合わせる（`--review-scopes` の招待は未承認の権限まで）。
            expected = (meta_scopes.meta_invite_scopes(ledger.MEDIA, cfg.get("review_scopes") is True)
                        if accounts.is_invite_account(cfg) else EXPECTED_SCOPES)
            missing = [s for s in expected if s not in scopes]
            ok = "ads_read" in scopes and (debug or {}).get("is_valid") is not False
            row("debug_token", "鍵の権限（/debug_token）", ok,
                (f"NOT_GRANTED: {','.join(missing)}" if missing else "求めた権限はすべて有り")
                + ("（読むのに要るのは ads_read・ads_management は段 5）" if missing else ""),
                missing=missing)
    try:
        account = reader.account()
    except (graphread.AdsReadError, meta_errors.RateLimited) as exc:
        row("account", "広告アカウント（/act_<id>）", False, exc)
        return rows
    mismatch = [k for k in ("currency", "timezone_name")
                if cfg.get(k) is not None and account.get(k) not in (None, cfg[k])]
    cap = account.get("spend_cap")
    row("account", "広告アカウント（/act_<id>）", not mismatch and account.get("account_status") == 1,
        f"{account.get('name')}・通貨 {account.get('currency')}・時間帯 {account.get('timezone_name')}・"
        f"account_status {account.get('account_status')}・amount_spent {account.get('amount_spent')}・"
        f"spend_cap {cap}" + ("（0 = 上限なし。管理画面で入れる・THTH からは入れない）" if cap == 0 else "")
        + (f"・台帳と違う: {','.join(mismatch)}" if mismatch else ""))
    try:
        tree = reader.tree()
    except (graphread.AdsReadError, meta_errors.RateLimited) as exc:
        row("tree", "キャンペーン・広告セット・広告", False, exc)
    else:
        row("tree", "キャンペーン・広告セット・広告", True if tree["complete"] else None,
            f"{len(tree['campaigns'])}・{len(tree['adsets'])}・{len(tree['ads'])} 件・動いている物 "
            f"{len(watch.active_ids(tree))}" + ("" if tree["complete"] else "（全部は読めていない）"))
        report = watch.delivery_report(reader, tree, now=now)
        if report["adsets"]:
            row("delivery", "配信の診断（ACTIVE の広告セット）", None if report["warnings"] else True,
                "・".join(report["warnings"]) or f"{len(report['adsets'])} 件・止まって見える物なし",
                delivery=report)
    try:
        reader.insights("yesterday")
    except (graphread.AdsReadError, meta_errors.RateLimited) as exc:
        row("insights", "成績（/insights・昨日）", None if getattr(exc, "code", "") == "ads_insights_throttled"
            else False, exc)
    else:
        row("insights", "成績（/insights・昨日）", True, f"使用率 {reader.insights_util_pct()}%")
    try:
        pixels, pixel_error = reader.pixels(), None
    except (graphread.AdsReadError, meta_errors.RateLimited) as exc:
        pixels, pixel_error = None, getattr(exc, "code", None) or type(exc).__name__
    ready = guide.pixel_ready(pixels)
    # ピクセルはサイトへの誘導の頁が開いた数にだけ要る（反応の広告には要らない）ので、無くても × にしない（？）。
    row("pixel", "ピクセル（/adspixels・サイトへの誘導の頁が開いた数に要る）", True if ready else None,
        (f"読めませんでした（{pixel_error}）" if pixels is None else
         "ありません（イベントマネージャで作る・THTH からは作らない）" if not pixels else
         "・".join(f"{p['id']} 最後に動いた {p.get('last_fired_time') or 'まだ無い'}" for p in pixels)),
        pixels=pixels, next_steps=[guide.skill_step()] if ready else
        guide.pixel_steps(name, pixels, landing_url=cfg.get("landing_url"), error=pixel_error))
    usage = meta_usage.summary(None, path=ledger.usage_path_for(name), now=now)
    row("usage", "量の制限", usage["waiting"] is None,
        "待っていません" if usage["waiting"] is None else f"待っています（{usage['waiting'].get('until')} まで）")
    return rows


def render_doctor(v):
    print(f"広告の口座の点検 {v['adaccount']}（読むだけ）")
    for r in v["rows"]:
        mark = {True: "○", False: "×", None: "？"}[r["ok"]]
        print(f"  {mark} {r['label']}: {r['detail']}")
    _render_steps(v["next_steps"])


def cmd_doctor(args) -> int:
    rows = doctor_rows(args.name)
    bad = [r for r in rows if r["ok"] is False]

    steps = next((r["next_steps"] for r in rows if r.get("key") == "pixel"), None) or [guide.skill_step()]

    _emit({"adaccount": args.name, "ok": not bad, "rows": rows, "skill": guide.skill_ref(), "next_steps": steps},
          args.json, render_doctor)
    return 1 if bad else 0


# ------------------------------------------------------------------ 段 5: 書く

def _write_call(args, func, render, *, bad_rc=3):
    """書く操作を呼び、断りを 1 語＋文で返す。`AccountStopped` は止めた口座の断り（D-1 の 4）。"""
    from . import budget, ops
    try:
        value = func()
    except accounts.AccountStopped as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "code": str(exc), "skill": guide.skill_ref()}, ensure_ascii=False))
        return _fail(f"{exc}: この広告の口座は止まっています。作る・始める・延ばすは断ります"
                     "（止める・減らす・読むは通る）")
    except ops.OpsRefused as exc:
        text = f"{exc.code}: {exc.message}" if exc.message else exc.code
        if exc.hint:
            text += "\n" + exc.hint
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "code": exc.code, "message": exc.message, "hint": exc.hint,
                              "skill": guide.skill_ref()}, ensure_ascii=False))
        return _fail(text)
    except budget.BudgetRefused as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "code": exc.code, "message": exc.detail, "skill": guide.skill_ref()},
                             ensure_ascii=False))
        return _fail(f"{exc.code}: {exc.detail}")
    except (accounts.AccountError, graphread.AdsReadError, meta_errors.RateLimited, ValueError) as exc:
        return _fail(_error_text(exc))
    _emit(value, args.json, render)
    if isinstance(value, dict) and value.get("result") in ("failed", "unconfirmed"):
        return bad_rc
    return 0


def _amount_or_zero(text):
    try:
        value = int(text)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("整数です") from None
    if value < 0:
        raise argparse.ArgumentTypeError("0 以上の整数です")
    return value


def _amount(text):
    value = _amount_or_zero(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("金額は正の整数（最小単位・JPY なら円）です")
    return value


def render_boost(v):
    c = v["created"]
    print(f"作りました（全部 PAUSED・お金は動いていません）: キャンペーン {c['campaign_id']}・広告セット "
          f"{c['adset_id']}・クリエイティブ {c['creative_id']}・広告 {c['ad_id']}")
    p = v["plan"]
    print(f"  投稿 {p['object_story_id']}・通しの予算 {p['lifetime_minor']}（最小単位）・{p['start']} 〜 {p['end']}"
          f"・出し先 {','.join(p['platforms'])}・目的 {p['objective']}"
          + ("" if p.get("objective_verified") else "（この目的は本番未確認。管理画面で中身を見てから始める）"))
    if p.get("url_tags"):
        print(f"  リンクの印（url_tags）: {p['url_tags']}")
    _render_steps(v.get("next_steps"))
    print(f"次の一手: {v['next']}")


def cmd_boost(args) -> int:
    from . import ops
    platforms = tuple(p.strip() for p in (args.platforms or "facebook").split(",") if p.strip())

    return _write_call(args, lambda: ops.boost(
        args.name, by=args.by, via=args.via, post=args.post, object_story_id=args.object_story_id,
        lifetime_minor=args.lifetime, start=args.start, end=args.end, platforms=platforms,
        objective=args.objective, url_tags=args.url_tags, landing_url=args.landing_url,
        utm_campaign=args.utm_campaign), render_boost)


def _render_stage(v, verb):
    if v.get("stage") == 1:
        print(f"{verb} の 1 段目（まだ何も変えていません）: digest {v['digest']}")
        for key in ("amount_minor", "added_minor", "end_time", "spend_cap", "amount_spent"):
            if key in v:
                print(f"  {key}: {v[key]}")
        if v.get("change"):
            print(f"  変える値: {v['change']}")
        b = v.get("budget") or {}
        print(f"  今月の見積もり: 使った {b.get('used_minor')} / 月の上限 {b.get('monthly')}（予約の外の支出 "
              f"{b.get('outside_minor')}・最小単位・請求書ではない）")
        t = v.get("tree") or {}
        print(f"  木: キャンペーン {t.get('campaign')}")
        for row in t.get("adsets") or []:
            print(f"    広告セット {row}")
        for row in t.get("ads") or []:
            print(f"    広告 {row}")
        print(f"次の一手: {v['next']}")
    else:
        print(f"{verb}: {v.get('activated') or v.get('change')}（hub の番号 {v.get('approval')}・記録 {v['op_id']}）")
        if v.get("next"):
            print(f"次の一手: {v['next']}")


def cmd_activate(args) -> int:
    from . import ops
    return _write_call(args, lambda: ops.activate(args.name, args.campaign_id, by=args.by, via="cli",
                                                  confirm=args.confirm, approval=args.approval),
                       lambda v: _render_stage(v, "配信を始める"))


def cmd_extend(args) -> int:
    from . import ops
    return _write_call(args, lambda: ops.extend(args.name, args.adset_id, by=args.by, via="cli", end=args.end,
                                                lifetime_minor=args.lifetime, confirm=args.confirm,
                                                approval=args.approval),
                       lambda v: _render_stage(v, "延ばす"))


def render_pause(v):
    print(f"止めました（status=PAUSED）: {len(v['targets'])} 件" + (f"・失敗 {v['failed']}" if v["failed"] else ""))
    if v["failed"]:
        print("管理画面で止めてください（失敗した物があります）")


def cmd_pause(args) -> int:
    from . import ops
    if not args.all and not args.id:
        return _fail("ads_target_missing: --all か --id <id> が要ります")

    return _write_call(args, lambda: ops.pause(args.name, by=args.by, via=args.via, all_=args.all,
                                               ids=tuple(args.id or ())), render_pause)


def render_stop(v):
    if v["confirmed"]:
        print(f"止まりました（Meta から読み直して全部 PAUSED を確かめた）・記録 {v['op_id']}")
    else:
        print("止まったと確かめられません。管理画面で止めてください")
        print(f"hub に送る 1 行: {v['hub_line']}")
    for step, value in v["steps"].items():
        print(f"  {step}: {value}")


def cmd_stop(args) -> int:
    from . import ops

    return _write_call(args, lambda: ops.stop(args.name, by=args.by, via=args.via), render_stop)


def cmd_watch(args) -> int:
    from . import ops
    seen = {}

    def render(v):
        last = v.get("last") or {}
        text = {"stopped": "指定に達したので止めました（Meta から読み直して確かめた）",
                "stop_unconfirmed": "指定に達したので止めましたが、止まったと確かめられません。管理画面で止めてください",
                "delivering": "配信が始まりました（表示が 1 以上）",
                "until_reached": "--until に達したので見張りを終えました（止めていません）",
                "stopped_on_unreadable": "読めない回が続いたので止めました（止め損なうのを避ける側）",
                "unreadable": "読めない回が続いたか、1 回も読めませんでした（閾値が無いので止めていません）"}[v["result"]]
        print(f"{text}: キャンペーン {v['campaign_id']}")
        print(f"  最後の読み: spend {last.get('spend')}（最小単位 {last.get('spend_minor')}）・impressions "
              f"{last.get('impressions')}・{last.get('read_at')}（全期間・今日を含む）")
        if "engagement" in last:
            for line in graphread.engagement_text(last):
                print(f"  {line}")
        print(f"  読んだ回 {v['reads']}・読めなかった回 {v['failed_reads']}" + (f"（{v['errors']}）" if v["errors"] else ""))
        if v.get("hit"):
            print(f"  達した指定: {'・'.join(v['hit'])}")
        stop_row = v.get("stop") or {}
        if stop_row and not stop_row.get("confirmed"):
            print(f"hub に送る 1 行: {stop_row.get('hub_line')}")

    def run():
        value = ops.watch_campaign(
            args.name, args.campaign_id, by=args.by, via=args.via, stop_at_spend=args.stop_at_spend,
            stop_at_impressions=args.stop_at_impressions, interval=args.interval, until=args.until,
            log=None if args.json else (lambda line: print(line, file=sys.stderr)))
        seen["result"] = value["result"]
        seen["confirmed"] = (value.get("stop") or {}).get("confirmed")
        return value
    rc = _write_call(args, run, render)
    if rc == 0 and (seen.get("result") in ("stop_unconfirmed", "unreadable") or (
            seen.get("result") == "stopped_on_unreadable" and not seen.get("confirmed"))):
        return 3
    return rc


def cmd_resume(args) -> int:
    from . import ops

    def render(v):
        if v["stage"] == 1:
            s = v["stopped"]
            print(f"止めの印を外す 1 段目（まだ何も変えていません）: digest {v['digest']}")
            print(f"  今の印: 理由 {s.get('reason')}・止めた時刻 {s.get('stopped_at')}・止めた人 {s.get('actor')}")
            print(f"  口座の木: {v['tree']}・ACTIVE の物 {v['active_ids'] or 'なし'}")
            if v["active_ids"]:
                print("  注意: 止めた口座で ACTIVE の物があります（THTH の外で始まった配信かもしれません）")
            print(f"次の一手: {v['next']}")
        else:
            print(f"止めの印を外しました（hub の番号 {v['approval']}・記録 {v['op_id']}）。広告は PAUSED のままです")
            print(f"次の一手: {v['next']}")
    return _write_call(args, lambda: ops.resume(args.name, by=args.by, via="cli", confirm=args.confirm,
                                                approval=args.approval), render)


def cmd_end(args) -> int:
    from . import ops

    def render(v):
        print(f"終了日を早めました: {v['adset_id']} {v['before']} → {v['after']}（{v['result']}）")
    return _write_call(args, lambda: ops.end(args.name, args.adset_id, by=args.by, via=args.via, end_at=args.end),
                       render)


def cmd_budget(args) -> int:
    from . import budget, ops
    if args.budget_verb == "show":
        def show():
            cfg = ledger.load(args.name)
            snap = watch.read_snapshot(args.name) or {}
            insights = snap.get("insights") if isinstance(snap.get("insights"), dict) else {}
            return {"adaccount": args.name, "ceilings": ledger.ceilings(cfg),
                    "usage": budget.usage(args.name, cfg, now=jst.now_jst(), campaign_spend=None),
                    "month_spend_minor": (insights.get("this_month") or {}).get("spend_minor"),
                    "note": "予約と、最後の collect の今月の実支出（Meta の請求書ではない）"}

        def render(v):
            u = v["usage"]
            print(f"広告の口座 {v['adaccount']}  {u['month']}  上限 {v['ceilings']}（最小単位）")
            print(f"  終わっていない予約の残り {u['reserved_minor']}・今月の実支出（最後の collect）"
                  f"{v['month_spend_minor']}・月の上限 {u['monthly']}（{u['unit']}）")
        return _write_call(args, show, render)
    keys = [k for k in ("monthly", "lifetime_max", "days_max") if getattr(args, k) is not None]
    if args.adset:
        if keys:
            return _fail("ads_lower_ambiguous: --adset と台帳の上限は一緒に下げられません")

        def render(v):
            print(f"減らしました: {v['adset_id']} {v['before']} → {v['after']}（{v['result']}）")
        return _write_call(args, lambda: ops.lower_budget(args.name, args.adset, by=args.by, via=args.via,
                                                          lifetime_minor=args.lifetime, daily_minor=args.daily), render)
    if len(keys) != 1 or args.lifetime is not None or args.daily is not None:
        return _fail("ads_lower_target: --monthly・--lifetime-max・--days-max のどれか 1 つ、"
                     "または --adset <id> と --lifetime／--daily")
    key = keys[0]
    ledger_key = {"monthly": "monthly", "lifetime_max": "lifetime_budget_max", "days_max": "days_max"}[key]

    def render(v):
        print(f"下げました: ceilings.{ledger_key} {v['before']} → {v['after']}（上げるのは VM の台帳を直接書く）")
    return _write_call(args, lambda: ops.lower_ceiling(args.name, ledger_key, getattr(args, key), by=args.by,
                                                       via=args.via), render)


# ------------------------------------------------------------------ 登録

def register(sub) -> None:
    """`build_parser()` に `ads` を足す（cli.py に足すのはこの呼び出しだけ）。"""
    p = sub.add_parser("ads", help="広告の口座（読む・PAUSED で作る・2 段で始める・止める。上げる命令は無い）",
                       formatter_class=argparse.RawDescriptionHelpFormatter, description=__doc__)
    verbs = p.add_subparsers(dest="ads_verb", required=True)
    p_add = verbs.add_parser("add", help="広告の口座の台帳を 1 本書く（上限 0・旗は false）")
    p_add.add_argument("name")
    p_add.add_argument("--act-id", dest="act_id", required=True, help="広告マネージャの URL の act= の数字")
    p_add.add_argument("--project", required=True)
    p_add.add_argument("--page", action="append", help="結びつける Facebook の口座（何度でも）")
    p_add.add_argument("--ig", action="append", help="結びつける Instagram の口座（何度でも）")
    p_add.add_argument("--business-id", dest="business_id", default=None)
    p_add.add_argument("--by", required=True)
    p_add.add_argument("--json", action="store_true")
    p_add.set_defaults(func=cmd_add)
    p_leave = verbs.add_parser("leave", help="広告の口座の退出（止める→thth.me の鍵を無効に→台帳・鍵・状態と資格の行を消す）")
    p_leave.add_argument("name")
    p_leave.add_argument("--by", required=True)
    p_leave.add_argument("--json", action="store_true")
    p_leave.set_defaults(func=cmd_leave)
    p_auth = verbs.add_parser("auth", help="ユーザーの長い鍵を広告の口座の .token に（端末から・MCP からはできない）")
    p_auth.add_argument("name")
    p_auth.add_argument("--by", required=True)
    p_auth.add_argument("--code", default=None, help="戻り URL 全体（預かり所を使わないとき）")
    p_auth.add_argument("--exchange", action="store_true",
                        help="今の有効な鍵を再交換して期限が延びるかを 1 回試す（G-12）")
    p_auth.set_defaults(func=cmd_auth)
    for verb, func, text in (("accounts", cmd_accounts, "鍵で見える広告アカウントの一覧（読むだけ）"),
                             ("doctor", cmd_doctor, "台帳・鍵・権限・読めるかを確かめる（読むだけ）")):
        q = verbs.add_parser(verb, help=text)
        q.add_argument("name")
        q.add_argument("--json", action="store_true")
        q.set_defaults(func=func)
    p_show = verbs.add_parser("show", help="アカウント・spend_cap・amount_spent・動いている物（読むだけ）")
    p_show.add_argument("name")
    p_show.add_argument("--offline", action="store_true", help="Meta を呼ばず、最後の読み（snapshot）を出す")
    p_show.add_argument("--json", action="store_true")
    p_show.set_defaults(func=cmd_show)
    p_ins = verbs.add_parser("insights", help="成績（/insights・読むだけ）")
    p_ins.add_argument("name")
    p_ins.add_argument("--preset", default="last_30d", choices=graphread.PRESETS,
                       help="last_7d などは今日を含まない（Meta の決まり）。全期間は maximum")
    p_ins.add_argument("--since", default=None, help="YYYY-MM-DD（広告アカウントの時間帯の日・--preset より優先）")
    p_ins.add_argument("--until", default=None, help="YYYY-MM-DD（既定は今日）")
    p_ins.add_argument("--level", default="account", choices=graphread.LEVELS)
    p_ins.add_argument("--by", default=None, choices=tuple(graphread.BREAKDOWNS),
                       help="内訳: 年齢・性別 age-gender／地域 region／国 country／出た場所 placement／端末 device／時間帯 hour")
    p_ins.add_argument("--json", action="store_true")
    p_ins.set_defaults(func=cmd_insights)
    p_est = verbs.add_parser("estimate", help="出す前の見積もり（狙う相手の大きさ・予算あたりの見込み・読むだけ）")
    p_est.add_argument("name")
    p_est.add_argument("--targeting", required=True,
                       help='狙う相手の JSON か @ファイル（例 {"geo_locations":{"countries":["JP"]},"age_min":18}）')
    p_est.add_argument("--goal", default="LINK_CLICKS", choices=graphread.OPTIMIZATION_GOALS)
    p_est.add_argument("--json", action="store_true")
    p_est.set_defaults(func=cmd_estimate)
    p_skill = verbs.add_parser("skill", help="広告の手順の skill（thth-ads）の場所と全文")
    p_skill.add_argument("--print", dest="print_text", action="store_true", help="全文を出す")
    p_skill.add_argument("--json", action="store_true")
    p_skill.set_defaults(func=cmd_skill)
    p_col = verbs.add_parser("collect", help="読みを snapshot.json に残す（読むだけ・collect のたびにも回る）")
    p_col.add_argument("name", nargs="?", default=None)
    p_col.add_argument("--json", action="store_true")
    p_col.set_defaults(func=cmd_collect)
    p_cat = verbs.add_parser("catalog", help="商品カタログ（読むだけ・保存しない）")
    cat_verbs = p_cat.add_subparsers(dest="catalog_verb", required=True)
    c_list = cat_verbs.add_parser("list", help="ビジネスのカタログと商品数")
    c_list.add_argument("name", metavar="NAME")
    c_list.add_argument("--json", action="store_true")
    c_list.set_defaults(func=cmd_catalog)
    c_products = cat_verbs.add_parser("products", help="カタログの商品（先頭の 1 頁）")
    c_products.add_argument("name", metavar="NAME")
    c_products.add_argument("--catalog", required=True, metavar="ID", help="thth ads catalog list に出る id")
    c_products.add_argument("--limit", type=int, default=LIST_DEFAULT, choices=range(1, 101), metavar="1-100")
    c_products.add_argument("--json", action="store_true")
    c_products.set_defaults(func=cmd_catalog)
    p_leads = verbs.add_parser("leads", help="リードのフォームの一覧・フォームのリード（読むだけ・端末に出すだけで残さない）")
    p_leads.add_argument("name", metavar="NAME")
    p_leads.add_argument("--page", default=None, metavar="ID", help="ページの id（台帳のページが 1 つなら省ける）")
    p_leads.add_argument("--form", default=None, metavar="ID", help="このフォームのリードを読む（無ければフォームの一覧）")
    p_leads.add_argument("--limit", type=int, default=LIST_DEFAULT, choices=range(1, 101), metavar="1-100")
    p_leads.add_argument("--json", action="store_true")
    p_leads.set_defaults(func=cmd_leads)
    p_net = verbs.add_parser("network", help="Audience Network の数（読むだけ・保存しない）")
    p_net.add_argument("name", metavar="NAME")
    p_net.add_argument("--since", default=None, metavar="YYYY-MM-DD", help="既定は過去 7 日（同期の読みは 8 日まで）")
    p_net.add_argument("--until", default=None, metavar="YYYY-MM-DD")
    p_net.add_argument("--json", action="store_true")
    p_net.set_defaults(func=cmd_network)
    p_shop = verbs.add_parser("shop", help="ショップの設定（読むだけ・保存しない）")
    p_shop.add_argument("name", metavar="NAME")
    p_shop.add_argument("--json", action="store_true")
    p_shop.set_defaults(func=cmd_shop)

    def by_via(q):
        q.add_argument("--by", required=True)
        q.add_argument("--via", default="cli", choices=("cli", "mcp"), help=argparse.SUPPRESS)
        q.add_argument("--json", action="store_true")

    p_boost = verbs.add_parser("boost", help="投稿を広告に（全部 PAUSED で作る・お金は動かない）")
    p_boost.add_argument("name")
    target = p_boost.add_mutually_exclusive_group(required=True)
    target.add_argument("--post", default=None, help="THTH で出したページの投稿の post_id")
    target.add_argument("--object-story-id", dest="object_story_id", default=None,
                        help="<ページID>_<投稿の数字>（THTH で出していない投稿）")
    p_boost.add_argument("--lifetime", type=_amount, required=True, help="通しの予算（最小単位・JPY なら円）")
    p_boost.add_argument("--start", default="now", help="開始（ISO 8601 か now）")
    p_boost.add_argument("--end", required=True, help="終了（必須・ISO 8601）")
    p_boost.add_argument("--platforms", default="facebook", help="facebook[,instagram]")
    p_boost.add_argument("--objective", default="engagement", choices=tuple(graphwrite.OBJECTIVES),
                         help="engagement（本番で確かめた）・traffic・reach（この 2 つは本番未確認）")
    p_boost.add_argument("--url-tags", dest="url_tags", default=None,
                         help="リンクに付ける印（クリエイティブの url_tags・例 utm_source=facebook&utm_medium=paid）。"
                              "無ければ台帳の url_tags")
    p_boost.add_argument("--utm-campaign", dest="utm_campaign", default=None,
                         help="元の投稿の原稿のファイル名（拡張子なし）。--url-tags も台帳の url_tags も無ければ "
                              "utm_source=<出し先>&utm_medium=paid&utm_campaign=<これ> を付ける（有機の投稿と同じ campaign）")
    p_boost.add_argument("--landing-url", dest="landing_url", default=None,
                         help="誘導先の頁の URL（次の一手の文に入る・Meta には送らない）。無ければ台帳の landing_url")
    by_via(p_boost)
    p_boost.set_defaults(func=cmd_boost)

    p_act = verbs.add_parser("activate", help="配信を始める（2 段・CLI だけ・MCP には無い）")
    p_act.add_argument("name")
    p_act.add_argument("campaign_id")
    p_act.add_argument("--confirm", default=None, help="1 段目の digest")
    p_act.add_argument("--approval", "--gate", dest="approval", default=None, help="hub の承認の番号（記録）")
    p_act.add_argument("--by", required=True)
    p_act.add_argument("--json", action="store_true")
    p_act.set_defaults(func=cmd_activate)

    p_ext = verbs.add_parser("extend", help="期間を延ばす・通しの予算を増やす（2 段・CLI だけ）")
    p_ext.add_argument("name")
    p_ext.add_argument("adset_id")
    p_ext.add_argument("--end", default=None)
    p_ext.add_argument("--lifetime", type=_amount, default=None)
    p_ext.add_argument("--confirm", default=None)
    p_ext.add_argument("--approval", "--gate", dest="approval", default=None)
    p_ext.add_argument("--by", required=True)
    p_ext.add_argument("--json", action="store_true")
    p_ext.set_defaults(func=cmd_extend)

    p_pause = verbs.add_parser("pause", help="止める（status=PAUSED・承認なし・止めた口座でも・待たない）")
    p_pause.add_argument("name")
    p_pause.add_argument("--all", action="store_true")
    p_pause.add_argument("--id", action="append", help="キャンペーン・広告セット・広告の id（何度でも）")
    by_via(p_pause)
    p_pause.set_defaults(func=cmd_pause)

    p_stop = verbs.add_parser("stop", help="全部止めて Meta から読み直して確かめる（承認なし・待たない）")
    p_stop.add_argument("name")
    by_via(p_stop)
    p_stop.set_defaults(func=cmd_stop)

    p_end = verbs.add_parser("end", help="終了日を早める（承認なし・延ばすのは extend）")
    p_end.add_argument("name")
    p_end.add_argument("adset_id")
    p_end.add_argument("--end", required=True)
    by_via(p_end)
    p_end.set_defaults(func=cmd_end)

    p_watch = verbs.add_parser("watch", help="成績を読み続け、指定に達したら stop と同じに止める（承認なし・必ず終わる）")
    p_watch.add_argument("name")
    p_watch.add_argument("campaign_id")
    p_watch.add_argument("--stop-at-spend", dest="stop_at_spend", type=_amount, default=None,
                         help="全期間の spend（最小単位・JPY なら円）がこれ以上で止める")
    p_watch.add_argument("--stop-at-impressions", dest="stop_at_impressions", type=_amount, default=None,
                         help="全期間の表示がこれ以上で止める")
    p_watch.add_argument("--interval", type=int, default=300, help="読む間隔（秒・60 以上・既定 300）")
    p_watch.add_argument("--until", default=None, help="見張りを終える時刻（既定 6 時間後・48 時間まで）")
    by_via(p_watch)
    p_watch.set_defaults(func=cmd_watch)

    p_resume = verbs.add_parser("resume", help="THTH の止めの印を外す（2 段＋hub の番号・広告は始めない・CLI だけ）")
    p_resume.add_argument("name")
    p_resume.add_argument("--confirm", default=None, help="1 段目の digest")
    p_resume.add_argument("--approval", "--gate", dest="approval", default=None, help="hub の承認の番号（2 段目は必須）")
    p_resume.add_argument("--by", required=True)
    p_resume.add_argument("--json", action="store_true")
    p_resume.set_defaults(func=cmd_resume)

    p_budget = verbs.add_parser("budget", help="月の上限の見積もり・下げる（上げる命令は無い）")
    bverbs = p_budget.add_subparsers(dest="budget_verb", required=True)
    b_show = bverbs.add_parser("show", help="今月の消費の見積もり")
    b_show.add_argument("name")
    b_show.add_argument("--json", action="store_true")
    b_show.set_defaults(func=cmd_budget)
    b_lower = bverbs.add_parser("lower", help="台帳の上限か広告セットの予算を下げる（今より小さい値だけ）")
    b_lower.add_argument("name")
    b_lower.add_argument("--monthly", type=_amount_or_zero, default=None)
    b_lower.add_argument("--lifetime-max", dest="lifetime_max", type=_amount_or_zero, default=None)
    b_lower.add_argument("--days-max", dest="days_max", type=_amount_or_zero, default=None)
    b_lower.add_argument("--adset", default=None)
    b_lower.add_argument("--lifetime", type=_amount, default=None)
    b_lower.add_argument("--daily", type=_amount, default=None)
    by_via(b_lower)
    b_lower.set_defaults(func=cmd_budget)
