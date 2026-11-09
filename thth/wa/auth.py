"""`thth wa auth <名前>`（`thth ads auth` と同じ流れ・鍵の保存の仕方も同じ）。

Facebook ログイン for Business の同じ流れ（中継は `https://thth.me/callback/`）で**ユーザーの長い鍵**を取り、
台帳の `.token`（0600）に保存する:

1. 認可の URL。`app.env` に `META_WA_CONFIG_ID` があればその構成（`config_id`）、無ければ `scope`。
   **広告・ページの構成（`META_LOGIN_CONFIG_ID`）は使わない**（その構成に WhatsApp の権限は入っていない）。
   構成は Dashboard で作る（masaru の手）。
2. 戻りの `code` → 短い鍵 → 長い鍵。
3. `GET /{waba-id}` が**見えなければ保存しない**（許可の画面で WABA に印が付いていない）。
4. `GET /debug_token` で scopes と期限。
5. `.token` に保存。

Meta への要求は**全部 GET**。**秘密は出さない**（app secret・code・鍵は受け取った時点で `redact` に登録し、失敗は
静的な符丁だけ）。権限の綴りは公式の参照（whats-app-business-account の頁・2026-11-09）による。
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
from ..ads import auth as ads_auth
from ..meta import graph as meta_graph
from ..meta import tokens as meta_tokens
from . import ledger

CALLBACK = afb.CALLBACK
DIALOG = afb.DIALOG
# `thth wa auth` が求める権限（外の人の招待の scope `META_INVITE_EXTRA` には足さない・Dashboard の構成が先）。
SCOPES = ["whatsapp_business_management", "whatsapp_business_messaging", "business_management", "public_profile"]
CONFIG_KEY = "META_WA_CONFIG_ID"
SESSION_FILE = "auth_state.json"


def _session_path(name):
    return os.path.join(ledger.state_dir_for(name), SESSION_FILE)


def _binding(cfg, app_id, config_id) -> str:
    raw = json.dumps([cfg.get("waba"), cfg.get("waba_id"), app_id, config_id], sort_keys=True)
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
    """`(app_id, app_secret, WhatsApp 用の config_id か None)`。"""
    try:
        app_id, app_secret, _config = appenv.load_meta_app_env(log=lambda _line: None)
        data = appenv._parse_env_file(appenv.default_path())
    except appenv.AppEnvError as exc:
        raise FlowError("facebook_app_env_unavailable: " + str(exc)) from None
    secret(app_secret)
    return app_id, app_secret, (data.get(CONFIG_KEY) or "").strip() or None


def authorize_url(app_id, config_id, state) -> str:
    params = {"client_id": app_id, "redirect_uri": CALLBACK, "state": state, "response_type": "code"}
    if config_id:
        params["config_id"] = config_id
    else:
        params["scope"] = ",".join(SCOPES)
    return DIALOG + "?" + urllib.parse.urlencode(meta_tokens.dialog_params(params))


def _check_waba(cfg, long_token, app_secret, log):
    """`GET /{waba-id}` が見えること。見えなければ保存しない。"""
    params = ads_auth._with_proof({"access_token": long_token}, app_secret)
    try:
        body = afb.graph_get("/" + cfg["waba_id"], params)
    except FlowError:
        oauth._out(f"保存しませんでした: 台帳 {cfg['waba']} の WABA（{cfg['waba_id']}）は、この鍵で見えません。許可の画面で"
                   "その WhatsApp アカウントに印を付けるか、台帳の waba_id を直してください。", log=log)
        raise FlowError("wa_waba_not_granted: 台帳の WABA が許可されていません。WhatsApp Manager の URL の "
                        "business_id・asset_id から WABA の id を確かめてください") from None
    if str(body.get("id") or "") != cfg["waba_id"]:
        raise FlowError("wa_waba_mismatch: 返った WABA の id が台帳と違います。保存しません")


def _token_record(cfg, long_body, long_token, debug, *, config_id, via) -> dict:
    scopes = meta_tokens.scopes_of(debug)
    now = int(time.time())
    token = {"access_token": long_token, "token_type": "user", "obtained_at": jst.iso(),
             "waba_id": cfg["waba_id"],
             "user_id": debug.get("user_id") if debug and isinstance(debug.get("user_id"), str) else None,
             "scopes": scopes, "scopes_source": "response" if scopes is not None else (
                 "unknown" if config_id else "requested"),
             "login_mode": "config_id" if config_id else "scope", "auth_via": via}
    if scopes is None and not config_id:
        token["scopes"] = list(SCOPES)
    expires_at = meta_tokens.int_field(debug, "expires_at")
    if expires_at == 0:
        token["no_expiry"] = True
        token["expiry_source"] = "debug_token"
    elif expires_at and expires_at > 0:
        token["expires_at"] = expires_at
        token["expires_in"] = max(0, expires_at - now)
        token["expiry_source"] = "debug_token"
    else:
        seconds = long_body.get("expires_in") if isinstance(long_body, dict) else None
        if isinstance(seconds, int) and not isinstance(seconds, bool) and seconds > 0:
            token["expires_in"], token["expires_at"] = seconds, now + seconds
            token["expiry_source"] = "exchange_response"
        else:
            token["expiry_source"] = "unknown"
    data_access = meta_tokens.int_field(debug, "data_access_expires_at")
    if data_access:
        token["data_access_expires_at"] = data_access
    return token


def _when(value) -> str:
    if not value:
        return "不明"
    import datetime
    return jst.iso(datetime.datetime.fromtimestamp(value, jst.JST))


def run_auth(name, *, by, code=None, input_func=None, log=print, human_output=print) -> int:
    """`thth wa auth <名前> --by <名前> [--code <戻り URL>]`。"""
    try:
        admin_log.actor(by)
        cfg = ledger.load(name)
        app_id, app_secret, config_id = _app_env()
        binding = _binding(cfg, app_id, config_id)
        if code is not None:
            session = _read_session(name)
            if not session or session.get("binding") != binding or not isinstance(session.get("state"), str):
                raise FlowError("auth_session_missing: 前回の state がありません。thth wa auth をやり直してください")
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
        long_body, long_token = ads_auth._long_token(code_value, app_id, app_secret)
        _check_waba(cfg, long_token, app_secret, log)
        debug = ads_auth._checked_debug(long_token, app_id, app_secret)
        token = _token_record(cfg, long_body, long_token, debug, config_id=config_id, via=via)
        before = ledger.load_token(cfg)
        ledger.write_token(cfg, token)
        try:
            admin_log.append("waba_auth", name, {"media": ledger.MEDIA}, by=by,
                             diff={"token": ["present" if before else "absent", "present"], "auth_via": [None, via]})
        except (OSError, ValueError, admin_log.AdminLogError):
            log("admin_change_unrecorded: 変更ログに残せませんでした（鍵は保存しました）")
        _clear_session(name)
        remaining = ledger.token_remaining_days(token, jst.now_jst())
        head = ("期限なし（expires_at=0）・データに触れる期限 " + _when(token.get("data_access_expires_at"))
                if ledger.token_no_expiry(token) else _when(token.get("expires_at")))
        oauth._out(f"保存しました: {cfg['token']}（600）・鍵の期限 {head}"
                   + (f"（残り {remaining:.1f} 日）" if remaining is not None else ""), log=log)
        missing = [s for s in SCOPES[:2] if isinstance(token.get("scopes"), list) and s not in token["scopes"]]
        if missing:
            oauth._out("注意: 権限が足りません: " + ",".join(missing) + "（許可の画面で WhatsApp アカウントに印を付ける）",
                       log=log)
        return 0
    except EOFError:
        log("戻り URL を読めませんでした。thth wa auth --code で戻り URL 全体を渡してください")
        return 2
    except (OSError, ValueError, accounts.AccountError, appenv.AppEnvError) as exc:
        log(redact.redact(str(exc)) if isinstance(exc, (FlowError, ledger.WabaError))
            else "wa_auth_failed: 保存しませんでした")
        return 2
