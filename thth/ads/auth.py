"""`thth ads auth <名前>`（計画 段 4 の 2・7・C-1・C-3）。

Facebook ログイン for Business の同じ流れ（`adapters/auth_facebook.py`・中継は `https://thth.me/callback/`）
で**ユーザーの長い鍵（約 60 日）**を取り、広告の口座の `.token` に保存する:

1. 認可の URL（`config_id` があれば `config_id`、無ければ `scope`）。
2. 戻りの `code` → 短い鍵 → 長い鍵（`grant_type=fb_exchange_token`・`expires_in`）。
3. `GET /me/adaccounts?fields=id,account_id,name,currency,timezone_name,account_status` に台帳の
   `act_<id>` が**無ければ保存しない**（ページの照合と同じ作法・見えた一覧を名前つきで言う）。
4. `GET /debug_token`（呼ぶ側はアプリの鍵）で `scopes`・`expires_at`・`data_access_expires_at`。
5. `.token`（0600）に書き、台帳に `currency`・`timezone_name`（無いときだけ・違えば断る）と、
   通貨が JPY 以外なら書く操作を断る印（B-13）を写す。**上限・旗は触らない。**

**広告の鍵は広告の口座の `.token` にだけ**（ページの口座の鍵とは分ける・計画 G の危ないところ）。

`--exchange`（G-12・**口だけ**。本物での試しは masaru）: 今の**有効な**長い鍵を `fb_exchange_token` で
もう一度交換し、`/debug_token` の `expires_at` が延びたかを `exchange_trials.ndjson` に残す。延びて、
同じ利用者・同じアプリの鍵なら保存する（短くなる鍵は保存しない）。延びるなら段 5 の前に `refresh` を持つ。

Meta への要求は**全部 GET**（鍵の交換も GET・`auth_facebook.graph_get`）。書く口はここにも無い。
**秘密は出さない**: app secret・code・鍵は受け取った時点で `redact` に登録し、失敗は静的な符丁だけ。
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets as secrets_mod
import time
import urllib.parse

from .. import accounts, admin_log, appenv, authflow, jst, oauth, redact, secrets_fs
from ..adapters import auth_facebook as afb
from ..authflow import FlowError, secret
from ..meta import graph as meta_graph
from ..meta import tokens as meta_tokens
from . import graphread, ledger

CALLBACK = afb.CALLBACK
DIALOG = afb.DIALOG
# 構成（config_id）が無いときにだけ `scope` で求める権限。**読むのは `ads_read`**。`ads_management` は
# 段 5 の書く口が使う（この段では使わない・求めておけば D のやり直しが 1 回で済む）。
SCOPES = ["ads_read", "ads_management", "business_management", "public_profile"]
READ_SCOPE = "ads_read"
SESSION_FILE = "auth_state.json"
TRIALS_FILE = "exchange_trials.ndjson"
SESSION_TTL = authflow.TTL


def _session_path(name):
    return os.path.join(ledger.state_dir_for(name), SESSION_FILE)


def _binding(cfg, app_id, config_id) -> str:
    raw = json.dumps([cfg.get("adaccount"), cfg.get("act_id"), app_id, config_id], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _read_session(name):
    try:
        with open(_session_path(name), encoding="utf-8") as f:
            value = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise FlowError("auth_session_unreadable") from None
    if not isinstance(value, dict):
        raise FlowError("auth_session_unreadable")
    for key in ("state", "read_key"):
        if isinstance(value.get(key), str):
            secret(value[key])
    return value


def _write_session(name, value):
    ledger.ensure_state_dir(name)
    secrets_fs.atomic_write_json(_session_path(name), value, mode=0o600)


def _clear_session(name):
    try:
        os.unlink(_session_path(name))
    except FileNotFoundError:
        pass


def _app_env():
    try:
        app_id, app_secret, config_id = appenv.load_meta_app_env(log=lambda _line: None)
    except appenv.AppEnvError as exc:
        raise FlowError("facebook_app_env_unavailable: " + str(exc)) from None
    secret(app_secret)
    return app_id, app_secret, config_id


def authorize_url(app_id, config_id, state) -> str:
    params = {"client_id": app_id, "redirect_uri": CALLBACK, "state": state, "response_type": "code"}
    if config_id:
        params["config_id"] = config_id
    else:
        params["scope"] = ",".join(SCOPES)
    return DIALOG + "?" + urllib.parse.urlencode(meta_tokens.dialog_params(params))


def _with_proof(params: dict, app_secret: str) -> dict:
    token = params.get("access_token")
    if not (isinstance(token, str) and token and app_secret):
        return params
    return {**params, "appsecret_proof": secret(meta_graph.appsecret_proof(token, app_secret))}


def _adaccounts(user_token, app_secret) -> list:
    out, after = [], None
    for _ in range(afb.ACCOUNTS_MAX_PAGES):
        params = {"fields": graphread.ADACCOUNTS_FIELDS, "limit": afb.ACCOUNTS_PAGE_LIMIT,
                  "access_token": user_token}
        if after:
            params["after"] = after
        body = afb.graph_get("/me/adaccounts", _with_proof(params, app_secret))
        rows = body.get("data")
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise FlowError("ads_adaccounts_invalid: /me/adaccounts の応答に data の配列がありません")
        out.extend(graphread.adaccount_row(r) for r in rows)
        paging = body.get("paging") if isinstance(body.get("paging"), dict) else {}
        if not paging.get("next"):
            return out
        cursors = paging.get("cursors") if isinstance(paging.get("cursors"), dict) else {}
        after = cursors.get("after")
        if not isinstance(after, str) or not after:
            raise FlowError("ads_adaccounts_paging_invalid: 次の頁の cursor がありません")
    raise FlowError("ads_adaccounts_too_many: 広告アカウントが多すぎて辿り切れません")


def _debug(token, app_id, app_secret):
    try:
        body = afb.graph_get("/debug_token", _with_proof(
            {"input_token": token, "access_token": secret(app_id + "|" + app_secret)}, app_secret))
    except FlowError:
        return None
    return meta_tokens.debug_data(body)


def _label(row) -> str:
    return f"{row.get('name') or '（名前なし）'} act_{row.get('act_id') or '?'}（{row.get('currency') or '?'}）"


def _token_record(cfg, long_body, long_token, debug, account_row, *, config_id, via) -> dict:
    scopes = meta_tokens.scopes_of(debug)
    now = int(time.time())
    token = {
        "access_token": long_token,
        "token_type": "user",
        "obtained_at": jst.iso(),
        "act_id": cfg["act_id"],
        "user_id": debug.get("user_id") if debug and isinstance(debug.get("user_id"), str) else None,
        "scopes": scopes,
        "scopes_source": "response" if scopes is not None else ("unknown" if config_id else "requested"),
        "login_mode": "config_id" if config_id else "scope",
        "currency": account_row.get("currency"),
        "timezone_name": account_row.get("timezone_name"),
        "auth_via": via,
    }
    if scopes is None and not config_id:
        token["scopes"] = list(SCOPES)
    expires_at = meta_tokens.int_field(debug, "expires_at")
    if expires_at and expires_at > 0:
        token["expires_at"] = expires_at
        token["expires_in"] = max(0, expires_at - now)
        token["expiry_source"] = "debug_token"
    else:
        seconds = long_body.get("expires_in") if isinstance(long_body, dict) else None
        if isinstance(seconds, int) and not isinstance(seconds, bool) and seconds > 0:
            token["expires_in"] = seconds
            token["expires_at"] = now + seconds
            token["expiry_source"] = "exchange_response"
        else:
            # ユーザーの長い鍵は約 60 日【確・事実 §4】。分からないときは安全側に 60 日と書かない（期限不明）。
            token["expiry_source"] = "unknown"
    data_access = meta_tokens.int_field(debug, "data_access_expires_at")
    if data_access:
        token["data_access_expires_at"] = data_access
    return token


def _grant(cfg, code_value, app_id, app_secret, log):
    """code → 短い鍵 → 長い鍵 → `/me/adaccounts` の照合 → `/debug_token`。"""
    short = afb.graph_get("/oauth/access_token", {
        "client_id": app_id, "redirect_uri": CALLBACK, "client_secret": app_secret, "code": secret(code_value)})
    short_token = afb._token_value(short, "short")
    long_body = afb.graph_get("/oauth/access_token", {
        "grant_type": "fb_exchange_token", "client_id": app_id, "client_secret": app_secret,
        "fb_exchange_token": short_token})
    long_token = afb._token_value(long_body, "long")
    rows = _adaccounts(long_token, app_secret)
    target = next((r for r in rows if r.get("act_id") == cfg["act_id"]
                   or r.get("id") == "act_" + cfg["act_id"]), None)
    if target is None:
        seen = "・".join(_label(r) for r in rows[:afb.fb.PAGES_LISTED_MAX]) + (
            f"・ほか {len(rows) - afb.fb.PAGES_LISTED_MAX} 件" if len(rows) > afb.fb.PAGES_LISTED_MAX else "")
        oauth._out(f"保存しませんでした: 台帳 {cfg['adaccount']} の act_{cfg['act_id']} は、この鍵で見える"
                   f"広告アカウントに無い。見えた広告アカウント: {seen or '（無い）'}。", log=log)
        raise FlowError("ads_adaccount_not_granted: 台帳の広告アカウントが許可されていません。許可の画面で"
                        "その広告アカウントに印を付けるか、台帳の act_id を直してください（広告マネージャの URL の"
                        " act= の数字）")
    debug = _debug(long_token, app_id, app_secret)
    if debug is not None:
        if debug.get("is_valid") is False:
            raise FlowError("ads_token_invalid: /debug_token が無効と言いました")
        if debug.get("app_id") not in (None, app_id):
            raise FlowError("ads_token_app_mismatch: 別のアプリの鍵です")
    return long_body, long_token, debug, target


def _save(name, cfg, token, target, *, by, via, log) -> dict:
    before = ledger.load_token(cfg)
    ledger.mirror_account_facts(name, currency=target.get("currency"),
                                timezone_name=target.get("timezone_name"))
    ledger.write_token(cfg, token)
    try:
        admin_log.append("adaccount_auth", name, {"media": ledger.MEDIA}, by=by,
                         diff={"token": ["present" if before else "absent", "present"], "auth_via": [None, via],
                               "currency": [cfg.get("currency"), target.get("currency")]})
    except (OSError, ValueError, admin_log.AdminLogError):
        log("admin_change_unrecorded: 変更ログに残せませんでした（鍵は保存しました）")
    return ledger.load(name)


def _report(cfg, token, log):
    remaining = ledger.token_remaining_days(token, jst.now_jst())
    oauth._out(f"保存しました: {cfg['token']}（600）・鍵の期限 {_when(token.get('expires_at'))}"
               + (f"（残り {remaining:.1f} 日）" if remaining is not None else ""), log=log)
    missing = [s for s in (READ_SCOPE,) if isinstance(token.get("scopes"), list) and s not in token["scopes"]]
    if missing:
        oauth._out("注意: 権限が足りません: " + ",".join(missing) + "（許可の画面で広告アカウントに印を付ける）",
                   log=log)
    if cfg.get("writes_refused"):
        oauth._out(f"台帳に書く操作を断る印を残しました: {cfg['writes_refused']}（通貨 {cfg.get('currency')}）",
                   log=log)


def run_auth(name, *, by, code=None, input_func=None, log=print, human_output=print) -> int:
    """`thth ads auth <名前> --by <名前> [--code <戻り URL>]`。"""
    try:
        admin_log.actor(by)
        cfg = ledger.load(name)
        app_id, app_secret, config_id = _app_env()
        binding = _binding(cfg, app_id, config_id)
        if code is not None:
            session = _read_session(name)
            if (not session or session.get("binding") != binding
                    or not isinstance(session.get("state"), str)):
                raise FlowError("auth_session_missing: 前回の state がありません。thth ads auth をやり直してください")
            authflow._remaining(session)
        else:
            session = {"state": secret(oauth._new_state()), "read_key": secret(secrets_mod.token_urlsafe(32)),
                       "created_at": jst.iso(), "binding": binding}
            _write_session(name, session)
        via = "paste"
        if code is None:
            use_relay = input_func is None
            if use_relay:
                try:
                    status, _ = authflow.relay_request(session, register=True)
                    use_relay = status == 201
                except FlowError:
                    use_relay = False
            human_output(authorize_url(app_id, config_id, session["state"]))
            if use_relay:
                try:
                    code_value = authflow.poll(session)
                    via = "relay"
                except FlowError as exc:
                    if not str(exc).startswith("relay_unavailable"):
                        raise
                    log("預かり所を使えません。戻り URL 全体を貼ってください")
                    code_value = authflow.paste((input_func or input)(), session)
            else:
                log("承認後の戻り URL 全体を貼ってください（code と state の両方が要ります）")
                code_value = authflow.paste((input_func or input)(), session)
        else:
            code_value = authflow.paste(code, session)
        long_body, long_token, debug, target = _grant(cfg, code_value, app_id, app_secret, log)
        token = _token_record(cfg, long_body, long_token, debug, target, config_id=config_id, via=via)
        cfg = _save(name, cfg, token, target, by=by, via=via, log=log)
        _clear_session(name)
        _report(cfg, token, log)
        return 0
    except EOFError:
        log("戻り URL を読めませんでした。thth ads auth --code で戻り URL 全体を渡してください")
        return 2
    except (OSError, ValueError, accounts.AccountError, appenv.AppEnvError) as exc:
        log(redact.redact(str(exc)) if isinstance(exc, (FlowError, ledger.AdAccountError))
            else "ads_auth_failed: 保存しませんでした")
        return 2


def run_exchange(name, *, by, log=print, now=None) -> int:
    """`thth ads auth <名前> --exchange --by <名前>`: 有効な鍵の再交換で延びるかを 1 回試す（G-12）。"""
    try:
        admin_log.actor(by)
        cfg = ledger.load(name)
        current = ledger.load_token(cfg)
        if not current or current.get("unreadable") or not isinstance(current.get("access_token"), str):
            raise FlowError("ads_token_missing: 鍵がありません。先に thth ads auth をしてください")
        now = now or jst.now_jst()
        old_at = ledger.token_expires_at(current)
        if old_at is not None and old_at <= now.timestamp():
            raise FlowError("ads_token_expired: 期限切れの鍵からは交換できません（thth ads auth をやり直す）")
        app_id, app_secret, _config = _app_env()
        body = afb.graph_get("/oauth/access_token", {
            "grant_type": "fb_exchange_token", "client_id": app_id, "client_secret": app_secret,
            "fb_exchange_token": secret(current["access_token"])})
        new_token = afb._token_value(body, "long")
        debug = _debug(new_token, app_id, app_secret)
        if debug is not None and debug.get("is_valid") is False:
            raise FlowError("ads_token_invalid: /debug_token が無効と言いました")
        if debug is not None and debug.get("app_id") not in (None, app_id):
            raise FlowError("ads_token_app_mismatch: 別のアプリの鍵です")
        same_user = (debug is None or current.get("user_id") is None
                     or debug.get("user_id") in (None, current.get("user_id")))
        if not same_user:
            raise FlowError("ads_token_user_mismatch: 別の利用者の鍵が返りました。保存しません")
        new_at = meta_tokens.int_field(debug, "expires_at")
        if not new_at:
            seconds = body.get("expires_in")
            if isinstance(seconds, int) and not isinstance(seconds, bool) and seconds > 0:
                new_at = int(time.time()) + seconds
        extended = bool(new_at and old_at and new_at > old_at)
        same = new_token == current["access_token"]
        trial = {"at": jst.iso(now), "old_expires_at": old_at, "new_expires_at": new_at,
                 "extended": extended, "same_token": same}
        ledger.ensure_state_dir(name)
        fd = os.open(os.path.join(ledger.state_dir_for(name), TRIALS_FILE),
                     os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps(trial, ensure_ascii=False) + "\n")
        if extended:
            token = dict(current, access_token=new_token, obtained_at=jst.iso(now), expires_at=new_at,
                         expires_in=max(0, new_at - int(time.time())), expiry_source="exchange",
                         auth_via="exchange")
            if debug is not None and meta_tokens.int_field(debug, "data_access_expires_at"):
                token["data_access_expires_at"] = meta_tokens.int_field(debug, "data_access_expires_at")
            ledger.write_token(cfg, token)
            try:
                admin_log.append("adaccount_auth", name, {"media": ledger.MEDIA}, by=by,
                                 diff={"token": ["present", "present"], "auth_via": [None, "exchange"]})
            except (OSError, ValueError, admin_log.AdminLogError):
                log("admin_change_unrecorded: 変更ログに残せませんでした（鍵は保存しました）")
            oauth._out(f"延びました: 期限 {_when(old_at)} → {_when(new_at)}。新しい鍵を保存しました（"
                       f"{cfg['token']}・600）。延びるので段 5 の前に refresh を持てます（G-12）", log=log)
        else:
            oauth._out(f"延びませんでした: 期限 {_when(old_at)} → {_when(new_at)}。鍵は前のまま（保存していません）。"
                       "60 日ごとに thth ads auth のやり直しが要ります（G-12）", log=log)
        oauth._out(f"試しの記録: {os.path.join(ledger.state_dir_for(name), TRIALS_FILE)}", log=log)
        return 0
    except (OSError, ValueError, accounts.AccountError, appenv.AppEnvError) as exc:
        log(redact.redact(str(exc)) if isinstance(exc, (FlowError, ledger.AdAccountError))
            else "ads_exchange_failed: 保存しませんでした")
        return 2


def _when(value) -> str:
    if not value:
        return "不明"
    import datetime
    return jst.iso(datetime.datetime.fromtimestamp(value, jst.JST))
