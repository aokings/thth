"""Facebook ページの認可（Facebook ログイン for Business・手順 `docs/手順_FBページと広告_2026-10-28.md` §4 の F1）。

流れ（`authflow.run` の上に乗る。中継は Threads・Mastodon・X と同じ `https://thth.me/callback/`）:

1. 認可の URL `https://www.facebook.com/<版>/dialog/oauth`（`client_id`・`redirect_uri`・
   `state`・`response_type=code`）に、構成の ID があれば `config_id`、無ければ `scope`。
2. 戻りの `code` → 短いユーザーの鍵（`GET /<版>/oauth/access_token`）。
3. 短い鍵 → 長く使えるユーザーの鍵（`grant_type=fb_exchange_token`）。
4. 長い鍵で `GET /<版>/me/accounts` → 台帳の `page_id` のページの鍵。**無ければ保存しない。**
5. `GET /<版>/debug_token`（呼ぶ側はアプリの鍵 `<app_id>|<app_secret>`）で、ページの鍵に
   付いた権限（`scopes`）と期限（`expires_at`）を記録する。
6. ページの鍵で `GET /<版>/me` → id が台帳の `page_id` と同じことを確かめてから保存。

**保存するのはページの鍵だけ**（ユーザーの鍵は保存しない——要るのはページへの投稿と
読み取りで、ユーザーの鍵を持てば持つだけ漏れたときの範囲が広い）。

出典（2026-10-29 に読んだ・**L2**。読めなかったこと・書かれていないことは**未確認**）:

- Facebook ログイン for Business: developers.facebook.com/docs/facebook-login/facebook-login-for-business
  ——「ユーザーのアクセストークン」の構成では `config_id` を渡し、`scope` は「まだ含められるが
  使わないことを勧める」。手動の流れでは構成の ID を「任意の引数」として足す。→ **構成の ID が
  あれば `config_id` だけを送り、無ければ `scope` を送る**（両方は送らない）。
- 手動の流れ: developers.facebook.com/docs/facebook-login/guides/advanced/manual-flow
  ——`https://www.facebook.com/v26.0/dialog/oauth`（必須 `client_id`・`redirect_uri`・`state`、
  任意 `response_type`（既定 `code`）・`scope`）。交換は
  `GET https://graph.facebook.com/v26.0/oauth/access_token`（`client_id`・`redirect_uri`・
  `client_secret`・`code`）で、サーバから呼ぶ。この文書に `config_id` は出てこない（上の
  for Business の文書で補う）。PKCE の記述は無い（使わない）。
- 長く使える鍵: developers.facebook.com/docs/facebook-login/guides/access-tokens/get-long-lived
  ——`GET /<版>/oauth/access_token?grant_type=fb_exchange_token&client_id&client_secret&fb_exchange_token`。
  「Long-lived Page access token do not have an expiration date」。ページの鍵は長い鍵から
  `GET {app-scoped-user-id}/accounts` で取る。
- ページ API の概要: developers.facebook.com/docs/pages-api/overview ——`/me/accounts` で
  ページの id とページの鍵を取る・利用者はそのページの持ち主か、そのページで Task を
  行える人でなければならない。
- `/debug_token`: developers.facebook.com/docs/graph-api/reference/debug_token ——呼ぶ側は
  アプリの鍵かアプリの開発者のユーザーの鍵。応答の `scopes`・`expires_at`・`is_valid`・
  `app_id`・`profile_id`（ページの鍵ならそのページの id）。**`expires_at` が 0 の意味は
  書かれていない**（未確認。0 なら「期限なし」と読むのは **L3**）。

**Meta の共通層 `thth/meta/` に乗る**（段 2・10/29）: 宛先の検査と版は `meta/graph.py`、エラーの
本文の読みは `meta/errors.py`、`/debug_token` の読みは `meta/tokens.py`。**`appsecret_proof`**
（`meta.graph.appsecret_proof`）を、鍵を持って呼ぶ 3 つ（`/me/accounts`・`/debug_token`・`/me`）に
付ける。鍵の交換（`/oauth/access_token`）は `client_secret` そのものを送る口なので付けない。

**秘密は 1 つも出さない。** app secret・code・鍵は受け取った時点で `redact` に登録し、
HTTP の失敗は静的な符丁（`facebook_auth_http_<番号>`）だけを返す——Graph の
エラー本文や URL（クエリに secret が入る）を反射しない。
"""
from __future__ import annotations

import hashlib
import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .. import httpsafe, jst, leave_gate
from ..authflow import AuthProfile, FlowError, secret
from . import base
from . import facebook as fb
from ..meta import errors as meta_errors
from ..meta import graph as meta_graph
from ..meta import tokens as meta_tokens

CALLBACK = "https://thth.me/callback/"
DIALOG = "https://www.facebook.com/" + meta_graph.GRAPH_VERSION + "/dialog/oauth"
SCOPES = list(fb.SCOPES)
# `/me/accounts` の頁を辿る上限（1 頁 100 件 × 20 頁）。越えたら黙って切らず断る。
ACCOUNTS_PAGE_LIMIT = 100
ACCOUNTS_MAX_PAGES = 20
SCOPES_SOURCE_RESPONSE = "response"
SCOPES_SOURCE_REQUESTED = "requested"
SCOPES_SOURCE_UNKNOWN = "unknown"


def _origin():
    try:
        return meta_graph.origin()
    except base.AdapterError as exc:
        raise FlowError(str(exc)) from None


def graph_get(path: str, params: dict) -> dict:
    """認可の段の GET を 1 回。**失敗は静的な符丁だけ**（本文・URL を反射しない）。"""
    url = meta_graph.endpoint(_origin(), path) + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with httpsafe.urlopen(request, timeout=10) as response:
            raw = response.read(262145)
        if len(raw) > 262144:
            raise ValueError
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except urllib.error.HTTPError as exc:
        status = exc.code
        err = meta_errors.graph_error(exc)
        code = err.get("code") if isinstance(err.get("code"), int) else None
        raise FlowError(f"facebook_auth_http_{status}"
                        + (f"_code_{code}" if code is not None else "")) from None
    except (OSError, ValueError, http.client.HTTPException):
        raise FlowError("facebook_auth_response_unavailable") from None


def _token_value(body, what):
    value = body.get("access_token") if isinstance(body, dict) else None
    if (not isinstance(value, str) or not value or len(value) > 16384
            or any(ord(c) < 33 or ord(c) == 127 for c in value)):
        raise FlowError(f"facebook_{what}_token_missing: 応答に鍵がありません")
    return secret(value)


class FacebookAuthProfile(AuthProfile):
    media = "facebook"
    pkce = False
    # 構成の ID（無ければ None）。binding に入れる——認可の途中で構成を替えたら保存しない。
    config_id = None
    page_id = ""

    @classmethod
    @leave_gate.configured("cfg")
    def prepare(cls, cfg, *, redirect_uri=None, rehearse=False, resume=False, by=None):
        from .. import admin_log, appenv
        if not rehearse and not resume:
            admin_log.actor(by)
        page_id = str(cfg.get("page_id") or "").strip()
        if not fb.PAGE_ID.fullmatch(page_id):
            raise FlowError("facebook_page_id_required: 台帳に page_id（数字）を入れてください")
        if redirect_uri not in (None, CALLBACK) or cfg.get("redirect_uri") not in (None, "", CALLBACK):
            raise FlowError("facebook_callback_must_be_relay: 戻り先は " + CALLBACK + " だけです")
        try:
            app_id, app_secret, config_id = appenv.load_meta_app_env(log=lambda _line: None)
        except appenv.AppEnvError as exc:
            raise FlowError("facebook_app_env_unavailable: " + str(exc)) from None
        secret(app_secret)
        profile = cls(app_id, app_secret, CALLBACK, list(SCOPES))
        profile.config_id = config_id
        profile.page_id = page_id
        return profile

    def validate(self):
        _origin()

    def current_client(self):
        from .. import appenv
        app_id, app_secret, _ = appenv.load_meta_app_env(log=lambda _line: None)
        return app_id, app_secret

    def _current_config(self):
        from .. import appenv
        return appenv.load_meta_app_env(log=lambda _line: None)[2]

    def binding(self, cfg, *, current=False):
        value = super().binding(cfg, current=current)
        config = self._current_config() if current else self.config_id
        return hashlib.sha256((value + "\x1f" + (config or "")).encode()).hexdigest()

    def authorize(self, session):
        params = {"client_id": self.client_id, "redirect_uri": CALLBACK,
                  "state": session["state"], "response_type": "code"}
        if self.config_id:
            params["config_id"] = self.config_id
        else:
            params["scope"] = ",".join(self.scopes)
        return DIALOG + "?" + urllib.parse.urlencode(meta_tokens.dialog_params(params))

    def _app_token(self):
        # アプリの鍵（L2: debug_token の呼ぶ側に使える）。値は secret を含むので登録する。
        return secret(self.client_id + "|" + self.client_secret)

    def _with_proof(self, params: dict) -> dict:
        """鍵を持って呼ぶ引数に `appsecret_proof` を足す（段 2・`meta.graph.appsecret_proof`）。"""
        token = params.get("access_token")
        if not (isinstance(token, str) and token and self.client_secret):
            return params
        return {**params, "appsecret_proof": secret(meta_graph.appsecret_proof(token, self.client_secret))}

    def _pages(self, user_token, log):
        out = []
        after = None
        for _ in range(ACCOUNTS_MAX_PAGES):
            params = {"fields": "id,name,access_token,tasks", "limit": ACCOUNTS_PAGE_LIMIT,
                      "access_token": user_token}
            if after:
                params["after"] = after
            body = graph_get("/me/accounts", self._with_proof(params))
            rows = body.get("data")
            if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
                raise FlowError("facebook_accounts_invalid: /me/accounts の応答に data の配列がありません")
            for row in rows:
                if isinstance(row.get("access_token"), str):
                    secret(row["access_token"])
            out.extend(rows)
            paging = body.get("paging") if isinstance(body.get("paging"), dict) else {}
            if not paging.get("next"):
                return out
            cursors = paging.get("cursors") if isinstance(paging.get("cursors"), dict) else {}
            after = cursors.get("after")
            if not isinstance(after, str) or not after:
                raise FlowError("facebook_accounts_paging_invalid: 次の頁の cursor がありません")
        raise FlowError("facebook_accounts_too_many: ページが多すぎて辿り切れません")

    def _debug(self, token):
        """`/debug_token` の `data`。**訊けなければ None**（記録を「不明」に落とす）。"""
        try:
            body = graph_get("/debug_token", self._with_proof({"input_token": token,
                                                               "access_token": self._app_token()}))
        except FlowError:
            return None
        return meta_tokens.debug_data(body)

    @leave_gate.configured("account_cfg")
    def exchange(self, code_value, session, account_cfg, *, log):
        from .. import oauth
        page_id = str(account_cfg.get("page_id") or "").strip()
        if page_id != self.page_id:
            raise FlowError("auth_account_changed")
        short = graph_get("/oauth/access_token", {
            "client_id": self.client_id, "redirect_uri": CALLBACK,
            "client_secret": self.client_secret, "code": secret(code_value)})
        short_token = _token_value(short, "short")
        long_ = graph_get("/oauth/access_token", {
            "grant_type": "fb_exchange_token", "client_id": self.client_id,
            "client_secret": self.client_secret, "fb_exchange_token": short_token})
        long_token = _token_value(long_, "long")

        pages = self._pages(long_token, log)
        if not pages:
            raise FlowError("facebook_no_pages: 許可の画面でページが 1 つも選ばれていません。"
                            "ページに印を付けて認可し直してください。既存のトークンはそのままです")
        page = next((row for row in pages if str(row.get("id") or "") == page_id), None)
        if page is None:
            # **取り違え防止**（Threads の handle の照合と同じ考え）。id は秘密ではない。
            # 名前を添える（10/29 段 0: id だけでは、どれが目当てのページか人に分からなかった）。
            listed = [fb.page_label(row.get("name"), row.get("id")) for row in pages
                      if isinstance(row.get("id"), str)]
            seen = "・".join(listed[:fb.PAGES_LISTED_MAX]) + (
                f"・ほか {len(listed) - fb.PAGES_LISTED_MAX} ページ" if len(listed) > fb.PAGES_LISTED_MAX else "")
            oauth._out(f"保存しませんでした: 台帳 {account_cfg['account']} の page_id {page_id} は許可された"
                       f"ページに無い。許可されたページ: {seen or '（id の読めるページが無い）'}。", log=log)
            raise FlowError("facebook_page_not_granted: 台帳のページが許可されていません。"
                            "許可の画面でそのページに印を付けるか、台帳の page_id を直してください（"
                            + fb.PAGE_ID_HINT + "）")
        page_token = _token_value(page, "page")

        debug = self._debug(page_token)
        if debug is not None:
            if debug.get("is_valid") is False:
                raise FlowError("facebook_page_token_invalid: /debug_token が無効と言いました")
            if debug.get("app_id") not in (None, self.client_id):
                raise FlowError("facebook_page_token_app_mismatch: 別のアプリの鍵です")
            if debug.get("profile_id") not in (None, page_id):
                raise FlowError("facebook_page_token_page_mismatch: 別のページの鍵です")

        me = graph_get("/me", self._with_proof({"fields": "id,name", "access_token": page_token}))
        if str(me.get("id") or "") != page_id:
            raise FlowError("facebook_identity_mismatch: ページの鍵が台帳の page_id を指していません")

        scopes = meta_tokens.scopes_of(debug)
        if scopes is not None:
            scopes_recorded, scopes_source = scopes, SCOPES_SOURCE_RESPONSE
        elif self.config_id:
            # 構成のときは何を求めたかがこちらに無い（構成の側の一覧）。**嘘の一覧を書かない。**
            scopes_recorded, scopes_source = None, SCOPES_SOURCE_UNKNOWN
        else:
            scopes_recorded, scopes_source = list(self.scopes), SCOPES_SOURCE_REQUESTED

        tasks = page.get("tasks")
        token = {
            "access_token": page_token,
            "obtained_at": jst.iso(),
            "user_id": page_id,
            # 照合の鍵はページの id（名前は変えられる）。`FacebookAdapter.whoami()` と同じ。
            "username": page_id,
            "page_name": me.get("name") if isinstance(me.get("name"), str) else None,
            "page_tasks": [t for t in tasks if isinstance(t, str)] if isinstance(tasks, list) else None,
            "scopes": scopes_recorded,
            "scopes_source": scopes_source,
            "login_mode": "config_id" if self.config_id else "scope",
        }
        expires_at = debug.get("expires_at") if debug else None
        if isinstance(expires_at, int) and not isinstance(expires_at, bool) and expires_at > 0:
            # **期限が書いてあれば期限を採る**（「期限なし」と言い切らない）。
            token["expires_in"] = max(0, expires_at - int(time.time()))
            token["expiry_source"] = "debug_token"
        else:
            # 0（L3: 期限なし）か、訊けなかった（L2: 長い鍵から取ったページの鍵に期限は無い）。
            token["no_expiry"] = True
            token["expiry_source"] = "debug_token" if expires_at == 0 else "documented"
        data_access = debug.get("data_access_expires_at") if debug else None
        if isinstance(data_access, int) and not isinstance(data_access, bool):
            token["data_access_expires_at"] = data_access
        return token
