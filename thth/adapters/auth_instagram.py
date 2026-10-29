"""Instagram のプロ口座の認可（Meta 計画 段 3・B-2・10/29）。

**Facebook ログインの道**（事実 §2-1「Instagram API with Facebook Login」）。口の鍵は**ページの鍵**で、
流れは Facebook ページの認可（`auth_facebook.py`）と同じ——code → 短い鍵 → 長い鍵 → `/me/accounts` から
台帳の `page_id` のページの鍵 → `/debug_token` → `/me`。そのあとに 1 つ足す:

7. ページの鍵で `GET /<版>/{page_id}?fields=instagram_business_account{id,username},connected_instagram_account`
   （**L3**: 公式の IG 文書の一般の手順・計画 G-10。10/29 夜に本番で読んで、こぴちゃのページの
   `instagram_business_account` は 17841437318552220 と確かめた・`connected_instagram_account` も同じ id）。
   その id が台帳の `ig_user_id` と**同じときだけ**保存する（取り違え防止）。

**保存するのはページの鍵だけ**（ユーザーの鍵は保存しない・`auth_facebook.py` と同じ）。口座ごとに別の
`.token`（ページの口座を消しても IG の口座が生きる・「1 口座 1 鍵」を崩さない）。

`.token` の `user_id`・`username` は **ig_user_id**（名前は変えられるので照合に使わない）。名前は
`ig_username` に残す。`page_id`・`page_name` は親のページ。`data_access_expires_at` を書く——instagram の
権限は 90 日のデータの期限に当たる見込み（計画 C-1）で、`thth maintain` が残り 14 日で知らせる。

求める権限（構成 `config_id` が無いときだけ `scope` として送る）: `instagram.SCOPES`。構成「THTH pages」には
Instagram の 6 権限が入っている（手順 §0-2・【確】）。

**秘密は 1 つも出さない**（`auth_facebook.py` と同じ作法・失敗は静的な符丁だけ）。
"""
from __future__ import annotations

from .. import leave_gate
from ..authflow import FlowError
from . import auth_facebook
from . import facebook as fb
from . import instagram as ig


class InstagramAuthProfile(auth_facebook.FacebookAuthProfile):
    media = "instagram"
    ig_user_id = ""

    @classmethod
    def prepare(cls, cfg, *, redirect_uri=None, rehearse=False, resume=False, by=None):
        ig_user_id = str((cfg or {}).get("ig_user_id") or "").strip()
        if not ig.IG_ID.fullmatch(ig_user_id):
            raise FlowError("instagram_ig_user_id_required: 台帳に ig_user_id（数字）を入れてください。"
                            + ig.IG_USER_ID_HINT)
        profile = super().prepare(cfg, redirect_uri=redirect_uri, rehearse=rehearse, resume=resume, by=by)
        profile.ig_user_id = ig_user_id
        profile.scopes = list(ig.SCOPES)
        return profile

    @leave_gate.configured("account_cfg")
    def exchange(self, code_value, session, account_cfg, *, log):
        return self._exchange(code_value, session, account_cfg, log=log)

    def _exchange(self, code_value, session, account_cfg, *, log):
        from .. import oauth
        page_id = str(account_cfg.get("page_id") or "").strip()
        ig_user_id = str(account_cfg.get("ig_user_id") or "").strip()
        if page_id != self.page_id or ig_user_id != self.ig_user_id:
            raise FlowError("auth_account_changed")
        page, page_token, debug, me = self._page_grant(code_value, account_cfg, page_id, log=log)
        linked = auth_facebook.graph_get(f"/{page_id}", self._with_proof({
            "fields": "instagram_business_account{id,username},connected_instagram_account",
            "access_token": page_token}))
        business = linked.get("instagram_business_account")
        business = business if isinstance(business, dict) else {}
        found = str(business.get("id") or "")
        if not found:
            raise FlowError("instagram_account_not_linked: このページに Instagram のプロ口座がつながっていません"
                            "（ページの設定 → リンク済みのアカウント で Instagram をつなぐ）。保存しません")
        if found != ig_user_id:
            name = business.get("username") if isinstance(business.get("username"), str) else None
            oauth._out(f"保存しませんでした: ページ {fb.page_label(me.get('name'), page_id)} につながっている"
                       f" Instagram は {fb.page_label(name, found)} で、台帳 {account_cfg['account']} の"
                       f" ig_user_id {ig_user_id} と違います。", log=log)
            raise FlowError("instagram_account_mismatch: 台帳の ig_user_id がページの Instagram と違います。"
                            "台帳を直してから認可し直してください（" + ig.IG_USER_ID_HINT + "）")
        token = self._token_record(page_id, page, page_token, debug, me)
        token.update({
            "user_id": ig_user_id,
            "username": ig_user_id,
            "ig_username": business.get("username") if isinstance(business.get("username"), str) else None,
            "page_id": page_id,
        })
        return token

