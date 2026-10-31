"""`thth ads …`（計画 段 4・段 5）。

    thth ads add <名前> --act-id <数字> --project <project> [--page <口座>]… [--ig <口座>]… --by <名前>
    thth ads auth <名前> --by <名前> [--code <戻り URL>] [--exchange]
    thth ads accounts <名前> [--json]          鍵で見える広告アカウントの一覧（/me/adaccounts）
    thth ads doctor <名前> [--json]            台帳・鍵・権限・読めるか（読むだけ・鍵の期限の欄は今の値で書き直す）
    thth ads show <名前> [--json] [--offline]  アカウント・spend_cap・amount_spent・動いている物
    thth ads insights <名前> [--preset last_30d] [--level account] [--json]
    thth ads collect [<名前>] [--json]         読みを snapshot.json に残す（collect のたびにも回る）

段 5（書く・お金が動くのは activate と extend だけ）:

    thth ads boost <名前> (--post <post_id> | --object-story-id <ページID>_<数字>) --lifetime <額>
                   --start <時刻|now> --end <時刻> [--platforms facebook[,instagram]] [--objective engagement] --by <名前>
                                                全部 PAUSED で作る（お金は動かない）
    thth ads activate <名前> <campaign_id> --by <名前>                       1 段目: digest を出す
    thth ads activate <名前> <campaign_id> --confirm <digest> --approval <hub の番号> --by <名前>
                                                2 段目: Meta から木を読み直して一致したら始める
    thth ads extend <名前> <adset_id> [--end <時刻>] [--lifetime <額>] --by <名前> [--confirm … --approval …]
    thth ads pause <名前> (--all | --id <id>…) --by <名前>        止める（承認なし・止めた口座でも・待たない）
    thth ads stop <名前> --by <名前>             全部止める → 読み直して確かめる → spend_cap を下げる → THTH を止める
    thth ads budget show <名前>                  今月の消費の見積もり（請求書ではない）
    thth ads budget lower <名前> (--monthly <額> | --lifetime-max <額> | --days-max <日>)   台帳の上限を下げる
    thth ads budget lower <名前> --adset <id> (--lifetime <額> | --daily <額>)              広告セットの予算を減らす
    thth ads end <名前> <adset_id> --end <時刻|now> --by <名前>   終了日を早める

**上げる命令は無い**: `spend_cap` を上げる・外す、月の上限・1 操作の上限・`ads_enabled`・`production` を
上げるのは THTH の外（`spend_cap` は管理画面で masaru、上限と旗は VM の台帳 `adaccounts/<名前>.json` を
`cp -a` で控えてから直接書く・D-1 の 3）。`budget lower` は今より小さい値だけを受ける。止めた口座を戻す命令も
無い（VM で `adaccount_state/<名前>/guard/stopped.json` を控えてから消す）。

MCP に出すのは show・boost（PAUSED まで）・pause・stop だけ。activate・extend は CLI で masaru か運用が押す。
金額は最小単位の整数（JPY なら円）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .. import accounts, admin_log, jst, redact
from ..meta import errors as meta_errors
from ..meta import tokens as meta_tokens
from ..meta import usage as meta_usage
from . import graphread, ledger, watch

# **命令の全部**（`tests/test_meta_stage5.py` がこの集合と parser の一致を縛る）。
READ_VERBS = ("accounts", "add", "auth", "doctor", "show", "insights", "collect")
# 段 5 の書く命令。Meta に書くのは `ops.py` の決まった関数だけ（`graphwrite.ALLOWED_CALLERS`）。
WRITE_VERBS = ("boost", "activate", "extend", "pause", "stop", "budget", "end")
VERBS = READ_VERBS + WRITE_VERBS
# MCP に出す道具（**activate・extend・budget・end は出さない**・段 5 の 7）。
MCP_TOOLS = ("thth_ads_show", "thth_ads_boost", "thth_ads_pause", "thth_ads_stop")
EXPECTED_SCOPES = ("ads_read", "ads_management", "business_management")


def _fail(reason, *, json_output=False) -> int:
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


def cmd_show(args) -> int:
    now = jst.now_jst()
    try:
        cfg = ledger.load(args.name)
    except accounts.AccountError as exc:
        return _fail(_error_text(exc))
    value = {"adaccount": args.name, "ledger": _ledger_view(args.name, cfg),
             "token": watch.token_state(cfg, now=now), "unit": "最小単位の整数（JPY なら円）"}
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
        mismatch = [key for key in ("currency", "timezone_name")
                    if cfg.get(key) is not None and value["account"].get(key) not in (None, cfg[key])]
        value["ledger_mismatch"] = mismatch
    # 要対応: 今読んだ値から（`--offline` は最後の snapshot から）。snapshot は書かない（書くのは collect）。
    basis = (value["snapshot"] if args.offline else
             {"account": value["account"], "tree": tree, "insights": (watch.read_snapshot(args.name) or {}).get("insights")})
    value["events"] = watch.events(args.name, cfg, basis, now=now,
                                   make=lambda kind, ref, summary, **extra: {"kind": kind, "summary": summary})

    def render(v):
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
        elif v.get("snapshot"):
            s = v["snapshot"]
            print(f"  最後の読み: {s.get('observed_at')}" + (f"（読めなかった: {s.get('error')}）" if s.get("error") else ""))
        for row in v["events"]:
            print(f"  要対応 {row['kind']}: {row['summary']}")
    _emit(value, args.json, render)
    return 0


# ------------------------------------------------------------------ insights

def cmd_insights(args) -> int:
    try:
        cfg, _token, reader = _reader(args.name)
        rows = reader.insights(args.preset, level=args.level)
    except (accounts.AccountError, graphread.AdsReadError, meta_errors.RateLimited, ValueError) as exc:
        return _fail(_error_text(exc))
    value = {"adaccount": args.name, "preset": args.preset, "level": args.level, "currency": cfg.get("currency"),
             "rows": rows, "ads_insights_util_pct": reader.insights_util_pct(),
             "unit": "spend は Meta の文字列（普通の単位）・spend_minor は最小単位の整数（JPY だけ直す）"}

    def render(v):
        print(f"成績 {v['adaccount']}  {v['preset']}・{v['level']}（通貨 {v['currency'] or '未'}）")
        for row in v["rows"]:
            head = f"{row.get('name') or row.get('id')}  " if v["level"] != "account" else ""
            print(f"  {head}spend {row['spend']}（最小単位 {row['spend_minor']}）・impressions {row['impressions']}・"
                  f"clicks {row['clicks']}・reach {row['reach']}（{row['date_start']}〜{row['date_stop']}）")
        if not v["rows"]:
            print("  行がありません（この期間に配信が無い）")
        print(f"  成績の読みの使用率: {v['ads_insights_util_pct']}%")
    _emit(value, args.json, render)
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
            missing = [s for s in EXPECTED_SCOPES if s not in scopes]
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
    try:
        reader.insights("yesterday")
    except (graphread.AdsReadError, meta_errors.RateLimited) as exc:
        row("insights", "成績（/insights・昨日）", None if getattr(exc, "code", "") == "ads_insights_throttled"
            else False, exc)
    else:
        row("insights", "成績（/insights・昨日）", True, f"使用率 {reader.insights_util_pct()}%")
    usage = meta_usage.summary(None, path=ledger.usage_path_for(name), now=now)
    row("usage", "量の制限", usage["waiting"] is None,
        "待っていません" if usage["waiting"] is None else f"待っています（{usage['waiting'].get('until')} まで）")
    return rows


def cmd_doctor(args) -> int:
    rows = doctor_rows(args.name)
    bad = [r for r in rows if r["ok"] is False]

    def render(v):
        print(f"広告の口座の点検 {v['adaccount']}（読むだけ）")
        for r in v["rows"]:
            mark = {True: "○", False: "×", None: "？"}[r["ok"]]
            print(f"  {mark} {r['label']}: {r['detail']}")
    _emit({"adaccount": args.name, "ok": not bad, "rows": rows}, args.json, render)
    return 1 if bad else 0


# ------------------------------------------------------------------ 段 5: 書く

def _write_call(args, func, render, *, bad_rc=3):
    """書く操作を呼び、断りを 1 語＋文で返す。`AccountStopped` は止めた口座の断り（D-1 の 4）。"""
    from . import budget, ops
    try:
        value = func()
    except accounts.AccountStopped as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "code": str(exc)}, ensure_ascii=False))
        return _fail(f"{exc}: この広告の口座は止まっています。作る・始める・延ばすは断ります"
                     "（止める・減らす・読むは通る）")
    except ops.OpsRefused as exc:
        text = f"{exc.code}: {exc.message}" if exc.message else exc.code
        if exc.hint:
            text += "\n" + exc.hint
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "code": exc.code, "message": exc.message, "hint": exc.hint},
                             ensure_ascii=False))
        return _fail(text)
    except budget.BudgetRefused as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "code": exc.code, "message": exc.detail}, ensure_ascii=False))
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


def cmd_boost(args) -> int:
    from . import ops
    platforms = tuple(p.strip() for p in (args.platforms or "facebook").split(",") if p.strip())

    def render(v):
        c = v["created"]
        print(f"作りました（全部 PAUSED・お金は動いていません）: キャンペーン {c['campaign_id']}・広告セット "
              f"{c['adset_id']}・クリエイティブ {c['creative_id']}・広告 {c['ad_id']}")
        p = v["plan"]
        print(f"  投稿 {p['object_story_id']}・通しの予算 {p['lifetime_minor']}（最小単位）・{p['start']} 〜 {p['end']}"
              f"・出し先 {','.join(p['platforms'])}")
        print(f"次の一手: {v['next']}")
    return _write_call(args, lambda: ops.boost(
        args.name, by=args.by, via=args.via, post=args.post, object_story_id=args.object_story_id,
        lifetime_minor=args.lifetime, start=args.start, end=args.end, platforms=platforms,
        objective=args.objective), render)


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


def cmd_pause(args) -> int:
    from . import ops
    if not args.all and not args.id:
        return _fail("ads_target_missing: --all か --id <id> が要ります")

    def render(v):
        print(f"止めました（status=PAUSED）: {len(v['targets'])} 件" + (f"・失敗 {v['failed']}" if v["failed"] else ""))
        if v["failed"]:
            print("管理画面で止めてください（失敗した物があります）")
    return _write_call(args, lambda: ops.pause(args.name, by=args.by, via=args.via, all_=args.all,
                                               ids=tuple(args.id or ())), render)


def cmd_stop(args) -> int:
    from . import ops

    def render(v):
        if v["confirmed"]:
            print(f"止まりました（Meta から読み直して全部 PAUSED を確かめた）・記録 {v['op_id']}")
        else:
            print("止まったと確かめられません。管理画面で止めてください")
            print(f"hub に送る 1 行: {v['hub_line']}")
        for step, value in v["steps"].items():
            print(f"  {step}: {value}")
    return _write_call(args, lambda: ops.stop(args.name, by=args.by, via=args.via), render)


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
    p_ins.add_argument("--preset", default="last_30d", choices=graphread.PRESETS)
    p_ins.add_argument("--level", default="account", choices=graphread.LEVELS)
    p_ins.add_argument("--json", action="store_true")
    p_ins.set_defaults(func=cmd_insights)
    p_col = verbs.add_parser("collect", help="読みを snapshot.json に残す（読むだけ・collect のたびにも回る）")
    p_col.add_argument("name", nargs="?", default=None)
    p_col.add_argument("--json", action="store_true")
    p_col.set_defaults(func=cmd_collect)

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
    p_boost.add_argument("--objective", default="engagement", choices=("engagement", "traffic"))
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
