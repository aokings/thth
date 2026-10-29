"""InstagramAdapter（Instagram のプロ口座・Facebook ログインの道・Meta 計画 段 3・B-2・10/29）。

**本物の Graph API は叩かない。** 宛先は `graph.facebook.com` だけ（`meta/graph.py` の `origin()`）。試験は
`THTH_FACEBOOK_BASE_URL` で loopback の偽サーバに向ける。鍵は**ページの鍵**（事実 §2-1）で、呼び出しは
Meta の共通層 `thth/meta/` に乗る（`appsecret_proof`・量の制限の待ち・使用量の写し）。HTTP の口
（`_request`・`_get`・`_rows`・`_after`・`_scrub`・`_write`・`_debug_token`）は `FacebookAdapter` から継ぐ
——同じ宛先・同じ鍵の形・同じエラーの読みで、写しを作らない。媒体の中身（id の形・口・指標）はここに置く。

証拠段階（**L2**＝Meta の公式の文書を 2026-10-29 に読んだ・**L3**＝そう読めるだけ・**未確認**＝書かれて
いない・**確**＝本番で読んだ。事実は `docs/調査_MetaAPIの事実_2026-10-29.md` §2）:

| 何 | 出典 | 印 |
|---|---|---|
| 公開は入れ物 `POST /{ig-id}/media`（`image_url`）→ `POST /{ig-id}/media_publish`（`creation_id`）→ 状態 `GET /{container}?fields=status_code` | content-publishing | **L2** |
| 状態の問い合わせは 1 分に 1 回・5 分まで。入れ物は 24 時間で切れる | 同上 | **L2** |
| 画像は JPEG だけ・8MB・4:5〜1.91:1・幅 320〜1440。カルーセル 20 件・子にキャプション不可 | 同上 | **L2** |
| 1 日の公開の上限 `GET /{ig-id}/content_publishing_limit`（`quota_usage`・`config.quota_total`） | content_publishing_limit の参照・本番 10/29 に 100 を読んだ | **確** |
| `is_ai_generated`（カルーセルは親だけ）・`alt_text`（画像だけ・1000 字まで） | content-publishing | **L2** |
| 最近の投稿 `GET /{ig-id}/media`（新しい順・ストーリーは入らない） | ig-user/media | **L2** |
| コメント `GET /{media}/comments`・返事 `GET /{comment}/replies`・返す `POST /{comment}/replies` | comment-moderation | **L2** |
| 隠す `POST /{comment}`（`hide`）・消す `DELETE /{comment}`・コメント欄 `POST /{media}`（`comment_enabled`） | 同上 | **L2**（応答 `{"success": true}` は **L3**） |
| 投稿の削除 `DELETE /{media}`（`instagram_manage_contents`） | 権限の参照に「投稿の削除」とあるだけ。口の形は**未確認** | **L3** |
| 投稿の数 `views`・`reach`・`likes`・`comments`・`shares`・`saved`・`total_interactions`（`impressions` は廃止） | instagram-media/insights | **L2** |
| 口座の日次 `reach`・`views`・`accounts_engaged`・`total_interactions`・`follows_and_unfollows`（`metric_type=total_value`）・`follower_demographics`（`period=lifetime`・`breakdown`） | instagram-user/insights | **L2**（引数の組み合わせは **L3**・取れなければ `unavailable`） |
| ページの `instagram_business_account` が口座の id | 公式の IG 文書の一般の手順・本番 10/29 に 17841437318552220 を読んだ | **確** |

**返信の口**: `reply_to` は**コメントの id** として `POST /{reply_to}/replies` に送る（投稿そのものへの
コメントは作らない。投稿の id とコメントの id はどちらも数字で、手元では見分けられないため・L3）。
"""
from __future__ import annotations

import re
import urllib.error

from .. import jst
from . import base
from . import facebook as fb
from ..meta import errors as meta_errors
from ..meta import graph as meta_graph
from ..meta import usage as meta_usage

MEDIUM = "instagram"

# Facebook ログインで求める権限（構成 `config_id` が無いときだけ `scope` として送る）。構成「THTH pages」には
# Instagram の 6 権限が入っている（手順 §0-2・`instagram_manage_engagement` はいいね・段 3 の後半）。
SCOPES = [
    "instagram_basic",
    "instagram_content_publish",
    "instagram_manage_comments",
    "instagram_manage_insights",
    "instagram_manage_contents",
    "pages_show_list",
    "pages_read_engagement",
    "business_management",
    "public_profile",
]

AVAILABLE_METRICS = ("views", "likes", "replies", "shares")
# 投稿ごとの数（`/{media}/insights` の名前）。**名前は 1 か所**——取れなかった指標は 0 で埋めず `unavailable`。
POST_INSIGHT_METRICS = ("views", "reach", "shares", "saved", "total_interactions")

# 口座の日次（`account_insights`）。(名前, 引数)。日の範囲（since・until）を付けるかは `period` で決める。
ACCOUNT_DAILY_SPECS = (
    ("reach", {"period": "day", "metric_type": "total_value"}),
    ("views", {"period": "day", "metric_type": "total_value"}),
    ("accounts_engaged", {"period": "day", "metric_type": "total_value"}),
    ("total_interactions", {"period": "day", "metric_type": "total_value"}),
    ("follows_and_unfollows", {"period": "day", "metric_type": "total_value", "breakdown": "follow_type"}),
    ("follower_demographics", {"period": "lifetime", "metric_type": "total_value", "breakdown": "country"}),
)
ACCOUNT_DAILY_METRICS = tuple(name for name, _ in ACCOUNT_DAILY_SPECS)

IG_ID = re.compile(r"[0-9]{1,32}")
IG_USER_ID_HINT = ("ig_user_id は Instagram のプロ口座の id（数字）。つながっているページで "
                   "GET /{page-id}?fields=instagram_business_account を読むと出ます（@ の名前ではありません）")

RECENT_POSTS_MAX = 100
COMMENT_PAGE_LIMIT = 50
COMMENT_MAX_PAGES = 50
# 1 本の投稿で返事を辿るコメントの上限（越えたら黙って切らず断る）。
REPLY_THREADS_MAX = 500

# 1 日の公開の上限（`content_publishing_limit` が読めないときの見立てにだけ使う・本番 10/29 に 100）。
PUBLISH_LIMIT_OBSERVED = 100

UNSUPPORTED = {
    "repost": "Instagram に再投稿の口はありません",
    "like": "THTH の instagram の口はまだいいねを扱いません（段 3 の後半）",
}


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def metric_value(row):
    """insights の行の値。`total_value.value`（数）・`total_value.breakdowns`（`{次元: 数}`）・`values[-1]`。

    無い・形が違えば None（**0 で埋めない**）。
    """
    if not isinstance(row, dict):
        return None
    total = row.get("total_value")
    if isinstance(total, dict):
        breakdowns = total.get("breakdowns")
        if isinstance(breakdowns, list) and breakdowns and isinstance(breakdowns[0], dict):
            results = breakdowns[0].get("results")
            if isinstance(results, list):
                out = {}
                for item in results:
                    if not isinstance(item, dict):
                        return None
                    dims = item.get("dimension_values")
                    value = _number(item.get("value"))
                    if (not isinstance(dims, list) or not dims or value is None
                            or not all(isinstance(d, str) for d in dims)):
                        return None
                    out["/".join(dims)] = value
                return out or None
        value = _number(total.get("value"))
        if value is not None:
            return value
    return fb._day_value(row)


class InstagramAdapter(fb.FacebookAdapter):
    """Instagram のプロ口座 1 つ（台帳の `ig_user_id`）。鍵は親のページの鍵。

    台帳: `ig_user_id`（必須・数字）・`page_id`（親のページ・`thth auth` が照合）。`.token`: `access_token`
    （ページの鍵）・`user_id`（＝ig_user_id）・`ig_username`・`page_id`・`scopes`・`data_access_expires_at`。
    """

    # `recent_posts`・`thread_read`（`fetch_post`）・`views`・`account_insights`（口座の日次）・`carousel`
    # （JPEG 2〜20 枚）・`hide_reply`（コメントを隠す・戻す・消す）・`comment_control`（コメント欄の開け閉め）・
    # `quota`（1 日の公開の上限と使用量）。`edit_post` は持たない（キャプションを直す口が公式に無い・L3）。
    CAPABILITIES: frozenset = frozenset({"recent_posts", "thread_read", "views", "account_insights",
                                         "carousel", "hide_reply", "comment_control", "quota"})
    UNSUPPORTED_REACTIONS = UNSUPPORTED
    TOKEN_KEYS = ("access_token",)
    TOKEN_SETUP_HINT = "thth auth"
    POST_ID_FORM_HINT = "Instagram の post_id は数字です（例 17895695668004550）。"
    AUTH_NEEDS_APP_ENV = True
    # 長い鍵から取ったページの鍵に期限は無い（L2・`FacebookAdapter` と同じ）。**データの期限（90 日）は別の時計**
    # で、instagram の権限に当たる見込み（計画 C-1）——`thth maintain` が `data_access_expiring` で知らせる。
    TOKEN_NO_EXPIRY = True
    DELETE_PERMISSION = "instagram_manage_contents"
    PUBLISH_PERMISSION = "instagram_content_publish"
    REPLY_PERMISSION = "instagram_manage_comments"
    MODERATE_PERMISSIONS = ("instagram_manage_comments",)
    COMMENT_CONTROL_PERMISSION = "instagram_manage_comments"
    INSIGHTS_PERMISSION = "instagram_manage_insights"
    READ_PERMISSION = "instagram_basic"
    DEBUG_TOKEN_KEY = "instagram_debug_token"
    DATA_ACCESS_KEY = "instagram_data_access"
    APPSECRET_PROOF_KEY = "instagram_appsecret_proof"
    META_GRAPH = True

    prepared_media_supported = True

    # 状態の問い合わせ（L2: 1 分に 1 回・5 分まで）。試験は `poll_interval` を 0 にする。
    poll_interval = 60.0
    poll_total = 300.0

    def __init__(self, *, base_url: str = fb.DEFAULT_BASE_URL, access_token: str = "",
                 ig_user_id: str = "", page_id: str = "", timeout: float = fb.DEFAULT_TIMEOUT_SECONDS,
                 app_secret: str | None = None):
        super().__init__(base_url=base_url, access_token=access_token, page_id=page_id,
                         timeout=timeout, app_secret=app_secret)
        self.ig_user_id = str(ig_user_id or "")
        # threads_media と同じ名で持つ（`media_delivery` の journal の instance は base_url）。
        self.user_id = self.ig_user_id

    # IG のコメントの Webhook は Advanced Access が要る（計画 段 6）。審査まで巡回が正本・間隔も変えない。
    WEBHOOK_REPLIES = False

    def _webhook_messages(self) -> list:
        """IG の Webhook（`mentions`・段 6 で試す・G-8）は読む口（inbox・mentions）に出さない。

        親のページの Webhook の記録を IG の行に化けさせない（`FacebookAdapter` の版はページの id で引く）。
        届いたかは `thth webhook status` の口座ごとの数で見る。購読（`subscribe_webhooks`）は親のページの鍵で同じ口。
        """
        return []

    @classmethod
    def from_account(cls, account_cfg: dict, token: dict):
        """台帳と `.token` から組み立てる。**`ig_user_id` が無い・数字でなければ断る。**"""
        cfg = account_cfg or {}
        token = token or {}
        ig_user_id = str(cfg.get("ig_user_id") or "").strip()
        if not IG_ID.fullmatch(ig_user_id):
            raise ValueError("台帳に ig_user_id がありません（数字の id。例: \"ig_user_id\": \"17841437318552220\"。"
                             + IG_USER_ID_HINT + "）——どの口座に出すかは既定では決められません")
        page_id = str(cfg.get("page_id") or "").strip()
        recorded = token.get("user_id")
        if recorded and str(recorded) != ig_user_id:
            raise ValueError(f"instagram_account_mismatch: .token の口座（{fb.page_label(token.get('ig_username'), recorded)}）"
                             f"と台帳の ig_user_id {ig_user_id} が違います。thth auth で認可し直してください")
        recorded_page = token.get("page_id")
        if recorded_page and page_id and str(recorded_page) != page_id:
            raise ValueError(f"instagram_page_mismatch: .token の親のページ {recorded_page} と台帳の page_id {page_id} が"
                             "違います。thth auth で認可し直してください")
        from .. import leave_gate
        adapter = cls(base_url=fb.graph_origin(), access_token=token.get("access_token", ""),
                      ig_user_id=ig_user_id, page_id=page_id)
        scopes = token.get("scopes")
        if (token.get("scopes_source") == "response" and type(scopes) is list
                and all(type(v) is str for v in scopes)):
            adapter.granted_scopes = frozenset(scopes)
        adapter.auth_account = cfg.get("account") or "<account>"
        from .. import accounts as accounts_mod
        if accounts_mod.name_is_safe(cfg.get("account")):
            adapter.usage_account = cfg["account"]
        secret = meta_graph.load_app_secret()
        if secret:
            adapter.app_secret = secret
        recorded_access = token.get("data_access_expires_at")
        if isinstance(recorded_access, int) and not isinstance(recorded_access, bool):
            adapter.recorded_data_access_expires_at = recorded_access
        return leave_gate.bind(adapter, account_cfg)

    @classmethod
    def is_post_id(cls, value) -> bool:
        return isinstance(value, str) and bool(IG_ID.fullmatch(value.strip()))

    # ----- 投稿 ------------------------------------------------------------

    def publish(self, post: base.Post, *, dry_run: bool, on_container_created=None,
                before_publish=None) -> base.PublishResult:
        """画像（1〜20 枚）は入れ物の 2 段（`instagram_media.publish`）。返信は `POST /{comment}/replies`。

        **画像の無い投稿は出せない**（Instagram に文だけの投稿は無い・L2）。**再試行しない。**
        """
        ts = jst.iso()
        if dry_run:
            return base.PublishResult(None, None, ts, error=None, failure="none")
        if post.media_manifest:
            from . import instagram_media
            return instagram_media.publish(self, post, before_publish=before_publish,
                                           on_container_created=on_container_created)
        if not post.reply_to:
            return base.PublishResult(None, None, ts, failure="publish_definite",
                                      error="unsupported_attachment: instagram/no_media"
                                            "（Instagram は画像の無い投稿を出せません。media: に JPEG を付ける）")
        target = str(post.reply_to).strip()
        if not IG_ID.fullmatch(target):
            return base.PublishResult(None, None, ts, failure="publish_definite",
                                      error="instagram_reply_to_invalid: 返す先はコメントの id（数字）です")
        if before_publish is not None:
            veto = before_publish()
            if veto:
                return base.PublishResult(None, None, ts, error=str(veto), failure="publish_vetoed")
        try:
            body = self._request("POST", f"/{target}/replies", form={"message": post.text},
                                 timeout=fb.PUBLISH_TIMEOUT_SECONDS)
        except meta_errors.RateLimited as e:
            return base.PublishResult(None, None, ts, error=self._scrub(e)[:300], failure="publish_definite")
        except urllib.error.HTTPError as e:
            err = fb.graph_error(e)
            message = self._scrub(err.get("message") or e.reason)[:170]
            if 400 <= e.code < 500 and (fb.is_permission_error(err) or err.get("code") == fb._INVALID_TOKEN_CODE):
                return base.PublishResult(None, None, ts, failure="permission", error=self._scrub(
                    base.not_granted_message(self.REPLY_PERMISSION, f"HTTP {e.code} {message}")
                    .replace("<account>", self.auth_account)))
            return base.PublishResult(None, None, ts, error=f"公開失敗: {e.code} {message}",
                                      failure="publish_definite" if 400 <= e.code < 500 else "publish_ambiguous")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            return base.PublishResult(None, None, ts, error=self._scrub(f"公開失敗: {e}"),
                                      failure="publish_ambiguous")
        reply_id = body.get("id") if isinstance(body, dict) else None
        if not isinstance(reply_id, str) or not IG_ID.fullmatch(reply_id):
            return base.PublishResult(None, None, ts, error="公開失敗: id無し", failure="publish_ambiguous")
        return base.PublishResult(post_id=reply_id, url=None, ts=ts, error=None, failure="none")

    def publishing_limit(self) -> dict:
        """1 日の公開の上限と使用量（`GET /{ig}/content_publishing_limit?fields=quota_usage,config`・**確**）。

        `{"usage", "total", "duration"}`。形が違えば上げる（**読めない値で出してよいと言わない**）。
        """
        body = self._get(f"/{self.ig_user_id}/content_publishing_limit", "公開の上限",
                         params={"fields": "quota_usage,config"}, permission=self.PUBLISH_PERMISSION)
        rows = self._rows(body, "公開の上限")
        row = rows[0] if rows else {}
        config = row.get("config") if isinstance(row.get("config"), dict) else {}
        usage = row.get("quota_usage")
        total = config.get("quota_total")
        duration = config.get("quota_duration")
        if (not isinstance(usage, int) or isinstance(usage, bool) or not isinstance(total, int)
                or isinstance(total, bool) or total < 0 or usage < 0):
            raise fb.AdapterError("公開の上限: 応答に quota_usage・config.quota_total の数がありません")
        return {"usage": usage, "total": total,
                "duration": duration if isinstance(duration, int) and not isinstance(duration, bool) else None}

    # ----- 取り下げ --------------------------------------------------------

    def delete_post(self, post_id: str) -> dict:
        """`DELETE /{ig-media-id}` を 1 回（`instagram_manage_contents`・口の形は **L3**）。**再試行しない。**

        `success` が真のときだけ成功と言う。消えている・見えないは `post_missing`（消えたとは言わない）。
        """
        value = post_id.strip() if isinstance(post_id, str) else ""
        if not IG_ID.fullmatch(value):
            raise fb.AdapterError("instagram_post_id_invalid: " + self.POST_ID_FORM_HINT + "取り下げません")
        try:
            body = self._request("DELETE", f"/{value}")
        except urllib.error.HTTPError as e:
            err = fb.graph_error(e)
            if fb.is_permission_error(err) or err.get("code") == fb._INVALID_TOKEN_CODE or e.code in (401, 403):
                raise base.PermissionMissing(self.DELETE_PERMISSION, f"HTTP {e.code}") from None
            if e.code == 404 or (err.get("code") == 100 and err.get("error_subcode") == 33):
                raise fb.AdapterError(
                    "post_missing: 媒体にその投稿がありません（既に消えているか、この token "
                    "から見えません）。記録は変えていません。Instagram のアプリで確かめてください",
                    http_status=e.code) from None
            raise fb.AdapterError(f"instagram_retract_http_{e.code}: 取り下げたとは言えません",
                                  http_status=e.code) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            raise fb.AdapterError("instagram_retract_failed: 取り下げたとは言えません（"
                                  + self._scrub(e) + "）") from None
        if not isinstance(body, dict) or body.get("success") is not True:
            raise fb.AdapterError("instagram_retract_unconfirmed: 応答に success: true がありません"
                                  "（消えたとは言えません）")
        return {"deleted": True, "post_id": value}

    # ----- 直す・隠す・消す・コメント欄 -------------------------------------

    def edit_post(self, post_id: str, text: str) -> dict:
        raise fb.AdapterError("Instagram の投稿のキャプションを直す口は THTH にありません（capability edit_post が無い）",
                              failure="definite")

    def fetch_reply(self, comment_id: str) -> dict:
        """コメントを 1 件読む（隠す・消す前に人に見せる・読むだけ）。"""
        value = str(comment_id or "").strip()
        if not IG_ID.fullmatch(value):
            raise fb.AdapterError("コメントの取得: Instagram のコメントの id は数字です")
        row = self._get(f"/{value}", "コメントの取得",
                        params={"fields": "id,text,timestamp,username,from,hidden,parent_id,media{id}"},
                        permission=self.REPLY_PERMISSION)
        media = row.get("media") if isinstance(row.get("media"), dict) else {}
        out = self._comment(row, str(media.get("id") or ""), None)
        author = row.get("from") if isinstance(row.get("from"), dict) else {}
        out["by_page"] = str(author.get("id") or "") == self.ig_user_id
        out["is_hidden"] = row.get("hidden") if isinstance(row.get("hidden"), bool) else None
        # Instagram は「隠せるか・消せるか」を返さない（Facebook の can_hide・can_remove に当たる欄が無い）。
        out["can_hide"] = None
        out["can_remove"] = None
        return out

    def hide_reply(self, comment_id: str, *, hidden: bool = True) -> dict:
        """コメントを隠す・戻す `POST /{comment_id}` に `hide`（L2）。"""
        value = str(comment_id or "").strip()
        if not IG_ID.fullmatch(value):
            raise fb.AdapterError("コメントの非表示: Instagram のコメントの id は数字です", failure="definite")
        if type(hidden) is not bool:
            raise fb.AdapterError("コメントの非表示: hidden は真か偽です", failure="definite")
        self._write("POST", f"/{value}", "コメントを隠す" if hidden else "コメントを戻す",
                    permission=self.MODERATE_PERMISSIONS[0], form={"hide": "true" if hidden else "false"})
        return {"hidden": hidden, "comment_id": value}

    def delete_reply(self, comment_id: str) -> dict:
        """コメントを消す `DELETE /{comment_id}`（L2）。"""
        value = str(comment_id or "").strip()
        if not IG_ID.fullmatch(value):
            raise fb.AdapterError("コメントの削除: Instagram のコメントの id は数字です", failure="definite")
        self._write("DELETE", f"/{value}", "コメントの削除", permission=self.MODERATE_PERMISSIONS[0])
        return {"deleted": True, "comment_id": value}

    def set_comments_enabled(self, post_id: str, enabled: bool) -> dict:
        """投稿のコメント欄を開ける・閉める `POST /{media_id}` に `comment_enabled`（L2）。"""
        value = str(post_id or "").strip()
        if not IG_ID.fullmatch(value):
            raise fb.AdapterError("コメント欄: " + self.POST_ID_FORM_HINT, failure="definite")
        if type(enabled) is not bool:
            raise fb.AdapterError("コメント欄: enabled は真か偽です", failure="definite")
        self._write("POST", f"/{value}", "コメント欄を開ける" if enabled else "コメント欄を閉じる",
                    permission=self.COMMENT_CONTROL_PERMISSION,
                    form={"comment_enabled": "true" if enabled else "false"})
        return {"comment_enabled": enabled, "post_id": value}

    # ----- 会話 ------------------------------------------------------------

    def _comment(self, row: dict, root_post: str, parent_default) -> dict:
        message_id = row.get("id")
        if not isinstance(message_id, str) or not message_id:
            raise fb.AdapterError("会話: コメントに id がありません（**件数として数えません**）")
        author = row.get("from") if isinstance(row.get("from"), dict) else {}
        author_id = author.get("id") if isinstance(author.get("id"), str) else None
        username = row.get("username") if isinstance(row.get("username"), str) else (
            author.get("username") if isinstance(author.get("username"), str) else None)
        parent = row.get("parent_id") if isinstance(row.get("parent_id"), str) else None
        return {
            "message_id": message_id,
            "username": username,
            "text": row.get("text") if isinstance(row.get("text"), str) else "",
            "timestamp": row.get("timestamp"),
            "replied_to": parent or parent_default,
            "root_post": root_post,
            "medium": MEDIUM,
            # 身元はアプリごとの利用者の id（無ければ名前・名前は変えられる）。
            "author_key": base.author_key(MEDIUM, author_id or (("@" + username) if username else None)),
            "reply_deadline": None,
        }

    def _pages_of(self, path: str, what: str, *, params: dict, permission: str) -> list:
        out = []
        after = None
        for _ in range(COMMENT_MAX_PAGES):
            page = dict(params)
            if after:
                page["after"] = after
            body = self._get(path, what, params=page, permission=permission)
            out.extend(self._rows(body, what))
            after = self._after(body)
            if not after:
                return out
        raise fb.AdapterError(f"{what}: {COMMENT_MAX_PAGES} 頁を越えました（黙って切りません）")

    def conversation(self, post_id: str, *, since: str | None = None) -> list:
        """`GET /{media}/comments` を頁ごとに辿り、コメントごとに `GET /{comment}/replies`（全階層・L2）。"""
        value = str(post_id or "").strip()
        if not IG_ID.fullmatch(value):
            raise fb.AdapterError("会話: " + self.POST_ID_FORM_HINT)
        fields = "id,text,timestamp,username,from,parent_id"
        rows = self._pages_of(f"/{value}/comments", "会話",
                              params={"fields": fields, "limit": COMMENT_PAGE_LIMIT},
                              permission=self.REPLY_PERMISSION)
        if len(rows) > REPLY_THREADS_MAX:
            raise fb.AdapterError(f"会話: コメントが {REPLY_THREADS_MAX} 件を越えました（黙って切りません）")
        out = []
        for row in rows:
            comment = self._comment(row, value, value)
            out.append(comment)
            replies = self._pages_of(f"/{comment['message_id']}/replies", "会話（返事）",
                                     params={"fields": fields, "limit": COMMENT_PAGE_LIMIT},
                                     permission=self.REPLY_PERMISSION)
            out.extend(self._comment(r, value, comment["message_id"]) for r in replies)
        floor = fb._parse_time(since) if since else None
        if floor is None:
            return out
        return [m for m in out if (fb._parse_time(m.get("timestamp")) or floor) >= floor]

    def fetch_post(self, post_id: str) -> dict:
        """根を 1 件（`GET /{media}`）。**自分の口座の投稿だけ**を返す（`owner.id` で見る）。"""
        value = str(post_id or "").strip()
        if not IG_ID.fullmatch(value):
            raise fb.AdapterError("投稿の取得: " + self.POST_ID_FORM_HINT)
        row = self._get(f"/{value}", "投稿の取得",
                        params={"fields": "id,caption,timestamp,permalink,username,owner"},
                        permission=self.READ_PERMISSION)
        owner = row.get("owner") if isinstance(row.get("owner"), dict) else {}
        if str(owner.get("id") or "") != self.ig_user_id:
            raise fb.AdapterError("投稿の取得: この口座の投稿ではありません")
        out = {
            "message_id": value,
            "username": row.get("username") if isinstance(row.get("username"), str) else None,
            "text": row.get("caption") if isinstance(row.get("caption"), str) else "",
            "timestamp": row.get("timestamp"),
            "replied_to": None,
            "root_post": value,
            "medium": MEDIUM,
            "author_key": base.author_key(MEDIUM, self.ig_user_id),
            "reply_deadline": None,
        }
        if isinstance(row.get("permalink"), str):
            out["permalink"] = row["permalink"]
        return out

    # ----- 実測 ------------------------------------------------------------

    def recent_posts(self, *, limit: int = 25) -> list:
        """`GET /{ig}/media`（新しい順・ストーリーは入らない・L2）。"""
        body = self._get(f"/{self.ig_user_id}/media", "直近の投稿",
                         params={"fields": "id,caption,timestamp,permalink,media_type,media_product_type",
                                 "limit": max(1, min(int(limit), RECENT_POSTS_MAX))},
                         permission=self.READ_PERMISSION)
        out = []
        for row in self._rows(body, "直近の投稿"):
            if not isinstance(row.get("id"), str) or not row["id"]:
                continue
            out.append({"post_id": row["id"], "timestamp": row.get("timestamp"),
                        "url": row.get("permalink") if isinstance(row.get("permalink"), str) else None,
                        "text": row.get("caption") if isinstance(row.get("caption"), str) else "",
                        "topic": None})
        return out

    def insights(self, post_id: str) -> dict:
        """いいね・コメントの数（`GET /{media}?fields=like_count,comments_count`）と、
        `GET /{media}/insights?metric=views,reach,shares,saved,total_interactions`。

        取れなかった指標は入れず、理由を `unavailable` に書く（**0 で埋めない**）。まとめて断られたら 1 つずつ。
        カルーセルの子には数が無い（L2）——THTH は親の id だけを持つ。
        """
        value = str(post_id or "").strip()
        if not IG_ID.fullmatch(value):
            raise fb.AdapterError("投稿の数: " + self.POST_ID_FORM_HINT)
        body = self._get(f"/{value}", "投稿の数", params={"fields": "like_count,comments_count"},
                         permission=self.READ_PERMISSION)
        metrics: dict = {}
        unavailable: dict = {}
        for name, key in (("likes", "like_count"), ("replies", "comments_count")):
            n = _number(body.get(key))
            if n is not None:
                metrics[name] = n
            else:
                unavailable[name] = f"応答に {key} がありません"
        rows = None
        try:
            rows = self._insight_rows(f"/{value}/insights", POST_INSIGHT_METRICS, {}, "投稿の数（insights）")
        except base.PermissionMissing:
            raise
        except base.AdapterError as e:
            if self._unreachable(e):
                raise
        if rows is None:
            rows = {}
            for name in POST_INSIGHT_METRICS:
                try:
                    rows.update(self._insight_rows(f"/{value}/insights", (name,), {}, "投稿の数（insights）"))
                except base.PermissionMissing:
                    raise
                except base.AdapterError as e:
                    if self._unreachable(e):
                        raise
                    unavailable[name] = self._scrub(e)[:200]
        for name in POST_INSIGHT_METRICS:
            if name in unavailable:
                continue
            n = metric_value(rows.get(name))
            if isinstance(n, (int, float)):
                metrics[name] = n
            else:
                unavailable[name] = "応答にこの指標の値がありません"
        result = {"metrics": metrics, "available": list(AVAILABLE_METRICS)}
        if unavailable:
            result["unavailable"] = unavailable
        return result

    def _insight_rows(self, path: str, metrics: tuple, params: dict, what: str) -> dict:
        body = self._get(path, what, params={"metric": ",".join(metrics), **params},
                         permission=self.INSIGHTS_PERMISSION)
        return {row.get("name"): row for row in self._rows(body, what) if isinstance(row.get("name"), str)}

    def followers_count(self) -> int | None:
        body = self._get(f"/{self.ig_user_id}", "口座の数", params={"fields": "followers_count,media_count"},
                         permission=self.READ_PERMISSION)
        return _number(body.get("followers_count"))

    def account_insights(self, user_id: str, *, since: str, until: str) -> dict:
        """口座の日次（`collect._collect_account_daily` が呼ぶ）。指標は `ACCOUNT_DAILY_SPECS` だけ、1 つずつ。

        取れなかった指標は **0 で埋めず** `unavailable` に名前と理由。権限系は `PermissionMissing`、
        網・5xx・量の制限は上げる（その日は記録せず次の採取で取り直す）。フォロワー数はプロフィールの
        `followers_count`（日の数ではなく採った時点の数）。
        """
        if str(user_id or "") != self.ig_user_id:
            raise fb.AdapterError("口座の日次: 台帳の口座と違う id です（訊きません）")
        what = "口座の日次"
        metrics: dict = {}
        unavailable: dict = {}
        for name, spec in ACCOUNT_DAILY_SPECS:
            params = dict(spec)
            if spec.get("period") == "day":
                params.update({"since": since, "until": until})
            try:
                rows = self._insight_rows(f"/{self.ig_user_id}/insights", (name,), params, what)
            except base.PermissionMissing:
                raise
            except base.AdapterError as e:
                if self._unreachable(e):
                    raise
                unavailable[name] = self._scrub(e)[:200]
                continue
            value = metric_value(rows.get(name))
            if value is None:
                unavailable[name] = "応答にこの指標の値がありません"
            else:
                metrics[name] = value
        try:
            followers = self.followers_count()
        except base.PermissionMissing:
            raise
        except base.AdapterError as e:
            if self._unreachable(e):
                raise
            unavailable["followers_count"] = self._scrub(e)[:200]
        else:
            if followers is None:
                unavailable["followers_count"] = "応答に followers_count がありません"
            else:
                metrics["followers_count"] = followers
        out = dict(metrics)
        if unavailable:
            out["unavailable"] = unavailable
        return out

    # ----- 素性・検査 ------------------------------------------------------

    def whoami(self) -> dict:
        """`GET /{ig_user_id}?fields=id,username`。**台帳の `ig_user_id` と違えば断る。**"""
        body = self._get(f"/{self.ig_user_id}", "口座の確認", params={"fields": "id,username"},
                         permission=self.READ_PERMISSION)
        found = body.get("id")
        if not isinstance(found, str) or not IG_ID.fullmatch(found):
            raise fb.AdapterError("口座の確認: 応答に id がありません")
        name = body.get("username") if isinstance(body.get("username"), str) else None
        if found != self.ig_user_id:
            raise fb.AdapterError(f"instagram_account_mismatch: この鍵の口座は {fb.page_label(name, found)} で、"
                                  f"台帳の ig_user_id {self.ig_user_id} と別の口座です")
        return {"user_id": found, "username": found, "name": name}

    def quota(self):
        """1 日の公開の上限（`content_publishing_limit`）と、Meta の量の制限の使用量（段 2 の `meta_usage`）。"""
        if not self.usage_account:
            raise fb.AdapterError("使用量: 口座の台帳から組み立てた adapter でだけ訊けます")
        waiting = meta_usage.waiting(self.usage_account)
        fresh = False
        publishing = None
        if waiting is None:
            publishing = self.publishing_limit()
            fresh = True
        out = meta_usage.summary(self.usage_account)
        out.update({"medium": MEDIUM, "source": "headers", "fresh": fresh,
                    "appsecret_proof": bool(self.app_secret), "publishing": publishing})
        return out

    def probe(self, *, get=None) -> list:
        """読み取りだけで能力を測る（`thth doctor`）。**投稿・返信・削除は呼ばない。**"""
        results = []

        def row(key, label, permission, ok, detail, **extra):
            results.append({"name": key, "key": key, "label": label, "permission": permission,
                            "ok": ok, "detail": detail, "body": None, **extra})

        def failed(key, label, permission, exc):
            ok = None if self._unreachable(exc) else False
            row(key, label, permission, ok, self._scrub(exc)[:300],
                failure=getattr(exc, "failure", None) or (
                    "permission" if isinstance(exc, base.PermissionMissing) else None),
                http_status=getattr(exc, "http_status", None))

        try:
            me = self.whoami()
        except Exception as e:   # noqa: BLE001 — probe は落ちずに × を返す口
            failed("whoami", "口座の確認", self.READ_PERMISSION, e)
        else:
            row("whoami", "口座の確認", self.READ_PERMISSION, True,
                f"ig_user_id={me['user_id']} username={me.get('name')}")

        granted = None
        try:
            data = self._debug_token()
        except Exception as e:   # noqa: BLE001
            row(self.DEBUG_TOKEN_KEY, "鍵の権限（/debug_token）", "（アプリの鍵が要る場合あり）",
                None, "確かめられませんでした（" + self._scrub(e)[:200] + "）")
        else:
            scopes = data.get("scopes")
            if isinstance(scopes, list) and all(isinstance(s, str) for s in scopes):
                granted = set(scopes)
                missing = [s for s in SCOPES if s not in granted]
                detail = (f"{len(scopes)} 個・expires_at={data.get('expires_at')}"
                          + (f"・足りない={','.join(missing)}" if missing else "・求めた権限はすべて有り"))
                row(self.DEBUG_TOKEN_KEY, "鍵の権限（/debug_token）", "（全権限）",
                    not missing, detail, scopes=sorted(scopes), missing=missing)
            else:
                row(self.DEBUG_TOKEN_KEY, "鍵の権限（/debug_token）", "（全権限）", None,
                    "応答に scopes の一覧がありません")
            row(self.DATA_ACCESS_KEY, "データに触れる期限（data_access_expires_at）", "（全権限）",
                *self._data_access_verdict(data.get("data_access_expires_at")))

        try:
            limit = self.publishing_limit()
        except Exception as e:   # noqa: BLE001
            failed("publishing_limit", "1 日の公開の上限（content_publishing_limit）", self.PUBLISH_PERMISSION, e)
        else:
            daily = thth_post_daily()
            inside = daily is None or daily <= limit["total"]
            row("publishing_limit", "1 日の公開の上限（content_publishing_limit）", self.PUBLISH_PERMISSION,
                inside, f"使用 {limit['usage']} / 上限 {limit['total']}（{limit['duration']} 秒の窓）・"
                        f"THTH の post.daily {daily}" + ("（内側）" if inside else "（上限を超えています・柵の数を下げる）"))

        latest = None
        try:
            posts = self.recent_posts(limit=1)
        except Exception as e:   # noqa: BLE001
            failed("posts", "口座の投稿の一覧", self.READ_PERMISSION, e)
        else:
            latest = posts[0]["post_id"] if posts else None
            row("posts", "口座の投稿の一覧", self.READ_PERMISSION, True, f"{len(posts)} 件")

        for key, label, permission, call in (
                ("comments", "投稿へのコメント", self.REPLY_PERMISSION,
                 lambda pid: f"{len(self.conversation(pid))} 件"),
                ("insights", "投稿の数", self.INSIGHTS_PERMISSION,
                 lambda pid: "views=" + str(self.insights(pid)["metrics"].get("views")))):
            if latest is None:
                row(key, label, permission, None, "投稿が無いので検査できません")
                continue
            try:
                detail = call(latest)
            except Exception as e:   # noqa: BLE001
                failed(key, label, permission, e)
            else:
                row(key, label, permission, True, detail)

        now = jst.now_jst()
        since = str(int(now.timestamp()) - 2 * 86400)
        until = str(int(now.timestamp()) - 86400)
        for name, spec in ACCOUNT_DAILY_SPECS:
            key, label = "account_insights." + name, "口座の日次 " + name
            params = dict(spec)
            if spec.get("period") == "day":
                params.update({"since": since, "until": until})
            try:
                value = metric_value(self._insight_rows(f"/{self.ig_user_id}/insights", (name,), params,
                                                        label).get(name))
            except base.PermissionMissing as e:
                failed(key, label, self.INSIGHTS_PERMISSION, e)
                continue
            except Exception as e:   # noqa: BLE001
                if self._unreachable(e):
                    failed(key, label, self.INSIGHTS_PERMISSION, e)
                else:
                    row(key, label, self.INSIGHTS_PERMISSION, False,
                        "unavailable: Meta がこの指標を断りました（" + self._scrub(e)[:200]
                        + "）。ACCOUNT_DAILY_SPECS から外すか引数を直す候補です",
                        http_status=getattr(e, "http_status", None))
                continue
            if value is None:
                row(key, label, self.INSIGHTS_PERMISSION, None,
                    "unavailable: 応答に値がありません（口座の規模で数が出ないことがあります）")
            else:
                row(key, label, self.INSIGHTS_PERMISSION, True, f"直近の値 {value}")

        # Meta 計画 段 4 の 5: Threads 広告の下地（読むだけ）。`GET /{ig-user-id}/connected_threads_user
        # ?fields=threads_user_id`（権限 `threads_business_basic`・L3: 公式の頁は検索の抜粋で読んだだけ）。
        # **Threads 広告の `threads_user_id` を得る以外に使わない**（事実 §2-7）。数字の id だけを言う。
        try:
            body = self._get(f"/{self.ig_user_id}/connected_threads_user", "Threads の口座（connected_threads_user）",
                             params={"fields": "threads_user_id"}, permission="threads_business_basic")
        except Exception as e:   # noqa: BLE001 — 下地の読み。× にせず「確かめられなかった」（doctor を赤にしない）
            row("connected_threads_user", "Threads 広告の下地（connected_threads_user）", "threads_business_basic",
                None, "読めませんでした（" + self._scrub(e)[:200] + "）", http_status=getattr(e, "http_status", None))
        else:
            rows = body.get("data") if isinstance(body.get("data"), list) else [body]
            ids = [str(r.get("threads_user_id")) for r in rows
                   if isinstance(r, dict) and str(r.get("threads_user_id") or "").isdigit()]
            row("connected_threads_user", "Threads 広告の下地（connected_threads_user）", "threads_business_basic",
                True if ids else None, f"threads_user_id={ids[0]}" if ids else "結びついた Threads の口座がありません")

        for key, label, permission in (("publish", "Instagram への投稿", self.PUBLISH_PERMISSION),
                                       ("reply", "コメントへの返事・隠す・消す", self.REPLY_PERMISSION),
                                       ("retract", "投稿の削除", self.DELETE_PERMISSION)):
            if granted is None:
                row(key, label, permission, None, "読み取りでは確かめられません")
            else:
                row(key, label, permission, permission in granted,
                    "トークンに乗っています（/debug_token）" if permission in granted
                    else base.not_granted_message(permission).replace("<account>", self.auth_account))

        if self.app_secret:
            row(self.APPSECRET_PROOF_KEY, "appsecret_proof", "（全部の呼び出し）", True,
                "付けて呼んでいます（app.env の META_APP_SECRET）")
        else:
            row(self.APPSECRET_PROOF_KEY, "appsecret_proof", "（全部の呼び出し）", None,
                "付けていません（app.env の META_APP_ID・META_APP_SECRET が読めない）")
        return results


def thth_post_daily() -> int | None:
    """THTH の `post.daily` の既定（`media_traits/instagram.json`）。読めなければ None。"""
    try:
        from .. import media_traits
        for trait in media_traits.load(MEDIUM)["traits"]:
            row = (trait.get("defaults") or {}).get("post.daily")
            if isinstance(row, dict) and isinstance(row.get("count"), int):
                return row["count"]
    except Exception:   # noqa: BLE001 — 読めないなら比べない
        return None
    return None


def post_url(permalink) -> str | None:
    return permalink if isinstance(permalink, str) and permalink.startswith("https://") else None

