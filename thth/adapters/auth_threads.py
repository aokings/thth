"""Threads provider: existing exchange helpers; Meta does not support PKCE."""
from .. import leave_gate

from ..authflow import AuthProfile
from .. import jst

class ThreadsAuthProfile(AuthProfile):
    media = "threads"
    pkce = False
    # 招待（3.10.0）は台帳の handle を先に持たない。認可した人から取る（下の照合を飛ばし、
    # 呼ぶ側が /me の username を台帳に書く）。既定の `thth auth` は従前どおり照合する。
    identity_from_token = False

    def validate(self):
        from .. import oauth
        oauth._auth_graph_base_url()

    def authorize(self, session):
        from .. import oauth
        return oauth.build_authorize_url(self.client_id, self.redirect_uri, self.scopes,
                                         state=session["state"])

    def current_client(self):
        from .. import appenv
        return appenv.load_app_env(log=lambda _: None)

    @leave_gate.configured("account_cfg")
    def exchange(self, code_value, session, account_cfg, *, log):
        from .. import oauth
        app_id, app_secret, redirect_uri, scope_list = self.client_id, self.client_secret, self.redirect_uri, self.scopes
        account_name = account_cfg["account"]
        try:
            short = oauth.exchange_short_lived_token(app_id, app_secret, redirect_uri, code_value)
        except oauth.OAuthError as e:
            oauth._out(str(e), log=log)
            raise oauth.OAuthError("auth_exchange_failed")
        if not isinstance(short, dict):
            raise oauth.OAuthError("auth_invalid_token_response")
        short_token = short.get("access_token")
        if not isinstance(short_token, str) or not short_token:
            oauth._out("短期トークンの取得に失敗しました（応答に access_token が無い）", log=log)
            raise oauth.OAuthError("auth_exchange_failed")

        oauth.redact_mod.register_secret(short_token)
        try:
            long_ = oauth.exchange_long_lived_token(app_secret, short_token)
        except oauth.OAuthError as e:
            oauth._out(str(e), log=log)
            raise oauth.OAuthError("auth_exchange_failed")
        if not isinstance(long_, dict):
            raise oauth.OAuthError("auth_invalid_token_response")
        long_token = long_.get("access_token")
        if not isinstance(long_token, str) or not long_token:
            oauth._out("長期トークンの交換に失敗しました（応答に access_token が無い）", log=log)
            raise oauth.OAuthError("auth_exchange_failed")
        oauth.redact_mod.register_secret(long_token)
        expires_in = long_.get("expires_in", oauth.DEFAULT_TOKEN_LIFETIME_SECONDS)

        try:
            me = oauth.fetch_me(long_token)
        except oauth.OAuthError as e:
            oauth._out(str(e), log=log)
            raise oauth.OAuthError("auth_exchange_failed")
        if not isinstance(me, dict):
            raise oauth.OAuthError("auth_identity_unavailable")
        user_id = me.get("id", "")
        username = me.get("username", "")

        # **本人確認ができなければ保存しない**（セキュリティ監査 2026-09-16・
        # P2-1）。下の取り違え防止は `if handle and username and ...` なので、
        # `username` が空だと**照合そのものを飛ばして保存していた**——`user_id`
        # も `username` も空のトークンが 600 で書かれる筋があった。`user_id`・
        # `username` の**どちらか**が空でも、本人が誰かを確かめられていないので
        # 保存しない。**既存の `.token` には触らない**（読みも書きもしない）。
        if not isinstance(user_id, str) or not user_id or not isinstance(username, str) or not username:
            oauth._out("本人確認ができないので保存しません（/me が id・username を"
                 "返しませんでした）。既存のトークンはそのままです。", log=log)
            raise oauth.OAuthError("auth_exchange_failed")

        # **取り違え防止**（セキュリティ監査 2026-09-14・P2-4）。`thth token set` は
        # 前からこれを見ていたが、`thth auth` には無かった——**同じ危険の同じ守りが
        # 片方にしか無い**。台帳の handle と、トークンが実際に指しているアカウントが
        # 食い違ったら保存しない。通してしまうと、そのアカウントの queue の本文が
        # 別のアカウントから出る（取り消せない公開行為）。
        if not self.identity_from_token:
            handle = account_cfg.get("handle")
            if not isinstance(handle, str) or not handle.strip():
                raise oauth.OAuthError("auth_account_handle_required")
            handle = handle.strip()
            if oauth.handle_matches(handle, username) is False:
                oauth._out(f"保存しませんでした: 台帳 {account_name} の handle は {handle} ですが、"
                     f"このトークンは {username} のものです。", log=log)
                oauth._out("正しいアカウントで認可し直すか、台帳の handle を直してください。", log=log)
                raise oauth.OAuthError("auth_exchange_failed")

        # **認可の範囲を記録する**（2026-09-14・`fetch_token_scopes` の説明）。
        # `/debug_token` が言った一覧なら `"response"`、訊けなければ要求した一覧を
        # `"requested"` として書く。**どちらを書いたかを残す。**
        granted = oauth.fetch_token_scopes(long_token)
        if granted is not None:
            scopes_recorded, scopes_source = granted, oauth.SCOPES_SOURCE_RESPONSE
        else:
            scopes_recorded, scopes_source = list(scope_list), oauth.SCOPES_SOURCE_REQUESTED

        token_data = {
            "access_token": long_token,
            "obtained_at": jst.iso(),
            "expires_in": expires_in,
            "user_id": user_id,
            "username": username,
            "scopes": scopes_recorded,
            "scopes_source": scopes_source,
        }
        return token_data


class ThreadsInviteAuthProfile(ThreadsAuthProfile):
    """招待リンク（3.10.0）の認可。台帳の handle は認可した人の username から作る。"""
    identity_from_token = True
