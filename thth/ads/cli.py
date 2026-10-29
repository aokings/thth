"""`thth ads accounts|add|auth|doctor|show|insights|collect`（計画 段 4）。**読むだけ。**

    thth ads add <名前> --act-id <数字> --project <project> [--page <口座>]… [--ig <口座>]… --by <名前>
    thth ads auth <名前> --by <名前> [--code <戻り URL>] [--exchange]
    thth ads accounts <名前> [--json]          鍵で見える広告アカウントの一覧（/me/adaccounts）
    thth ads doctor <名前> [--json]            台帳・鍵・権限・読めるか（読むだけ）
    thth ads show <名前> [--json] [--offline]  アカウント・spend_cap・amount_spent・動いている物
    thth ads insights <名前> [--preset last_30d] [--level account] [--json]
    thth ads collect [<名前>] [--json]         読みを snapshot.json に残す（collect のたびにも回る）

**この段に Meta に書く命令は無い**（作る・配信・予算・`spend_cap`・止める以外の変更。止める `pause`・
`stop` も段 5）。`spend_cap` と THTH の上限（`ceilings`）・`ads_enabled`・`production` を上げる命令は
**段 5 になっても持たない**（D-1）。上げるのは VM の台帳 `adaccounts/<名前>.json` を直接書く。
`add` が書くのは THTH の台帳だけで、上限は 0・旗は false に決め打ち（引数で上げる口は無い）。

どれも運営者の CLI だけ（MCP の道具・thth.me には出さない）。金額は最小単位の整数で出す。
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

# **この段の命令の全部**（`tests/test_meta_stage4.py` がこの集合と parser の一致を縛る）。
VERBS = ("accounts", "add", "auth", "doctor", "show", "insights", "collect")
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
        print(f"  書く操作: {'断る（' + led['writes_refused'] + '）' if led['writes_refused'] else '段 5 で柵つき'}"
              f"・止め {led['stopped']['reason'] if led['stopped'] else 'なし'}")
        tok = v["token"]
        print(f"  鍵: {tok['state']}" + (f"（残り {tok['remaining_days']:.1f} 日・{tok['expires_at']}）"
                                         if tok["remaining_days"] is not None else ""))
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
        f"書く操作 {'断る（' + refusal + '）' if refusal else '段 5 で柵つき'}")
    state = watch.token_state(cfg, now=now)
    hint = f"thth ads auth {name} --by <名前>"
    if state["state"] in (watch.NO_TOKEN, watch.UNREADABLE):
        row("token", "鍵", False, f"{state['state']}（{hint}）")
        return rows
    remaining = state["remaining_days"]
    row("token", "鍵の期限（ユーザーの長い鍵・約 60 日）",
        None if remaining is None else remaining >= watch.TOKEN_WARN_DAYS,
        "期限が分かりません" if remaining is None else
        f"{state['expires_at']}（残り {remaining:.1f} 日）"
        + ("" if remaining >= watch.TOKEN_WARN_DAYS else f"・14 日を切りました。{hint}"),
        remaining_days=remaining)
    try:
        _cfg, token, reader = _reader(name)
    except (accounts.AccountError, graphread.AdsReadError, ValueError) as exc:
        row("reader", "読む口", False, exc)
        return rows
    try:
        from . import auth
        app_id, app_secret, _config = auth._app_env()
        debug = auth._debug(token["access_token"], app_id, app_secret)
        if debug is None:
            raise graphread.AdsReadError("debug_token_unavailable")
    except Exception as exc:  # noqa: BLE001 — 訊けなければ「確かめられなかった」
        row("debug_token", "鍵の権限（/debug_token）", None, f"確かめられませんでした（{_error_text(exc)[:120]}）")
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


# ------------------------------------------------------------------ 登録

def register(sub) -> None:
    """`build_parser()` に `ads` を足す（cli.py に足すのはこの呼び出しだけ）。"""
    p = sub.add_parser("ads", help="広告の口座を読む（段 4・Meta に書く命令は無い・CLI だけ）",
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
