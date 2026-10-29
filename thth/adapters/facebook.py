"""FacebookAdapter（Facebook ページ・設計 v2 §4.2 の境界・手順 `docs/手順_FBページと広告_2026-10-28.md` §4 の F2）。

**本物の Graph API は叩かない。** 宛先は `graph.facebook.com` だけを許し、試験は
`THTH_FACEBOOK_BASE_URL` で loopback の偽サーバに向ける（`graph_origin()`）。

証拠段階（この repo の外の主張には印を付ける・作法 7。**L2**＝Meta の公式の文書を
2026-10-29 に読んだ・**L3**＝そう読めるだけ・**未確認**＝文書に書かれていない）:

| 何 | 出典 | 印 |
|---|---|---|
| 版は `v26.0`（2026-07-29 公開・いまの最新） | developers.facebook.com/docs/graph-api/changelog | **L2** |
| 宛先は `graph.facebook.com`・版は path の頭に `v<番号>` | developers.facebook.com/docs/graph-api/overview | **L2** |
| 投稿 `POST /{page_id}/feed`（`message`・`link`）→ `{"id"}` | developers.facebook.com/docs/pages-api/posts | **L2** |
| 削除 `DELETE /{page_post_id}` → `{"success": true}` | 同上 | **L2** |
| 一覧 `GET /{page_id}/feed`（`created_time`・`message`・`id`） | 同上。THTH は自分の投稿だけの `/{page_id}/posts` を使う | **L2**（`/posts` の口と `fields`・`limit` の上限は**未確認**） |
| 画像 `POST /{page_id}/photos`（`url` か multipart・`caption`・`alt_text_custom`）→ `id`・`post_id` | developers.facebook.com/docs/graph-api/reference/page/photos/ | **L2** |
| 画像の形式は jpeg・bmp・png・gif・tiff、「4MB を超えられない」 | 同上 | **L2**（MB の単位は書かれていないので SI の 4,000,000 で数える） |
| 投稿の数 `GET /{post_id}/insights`。`post_media_view` は v26 で使える・`post_impressions*` は v25 以降で使えない | developers.facebook.com/docs/graph-api/reference/insights | **L2**（`post_media_view` を views と読むのは **L3**） |
| 数の権限は `read_insights` と `pages_read_engagement` | 同上 | **L2** |
| コメント `GET /{post_id}/comments`・返信 `POST /{object_id}/comments` | Graph API の Comments の参照 | **L3**（この版では読み直していない） |
| ページ投稿の id が `{page_id}_{post_id}` の形 | 応答の例は置き場の名前だけ | **L3**（形が違えば取り下げは loud に断る） |
| 権限系の error code（10・200〜299） | `threads.py` の `_PERMISSION_ERROR_CODES` と同じ Graph API 共通の一覧 | **L3** |
| 複数の写真: `POST /{page_id}/photos` に `published=false` で 1 枚ずつ → `POST /{page_id}/feed` の `attached_media[i]={"media_fbid": …}` | 開発者フォーラムの報告だけ（公式の手順は見つからない） | **二次**（段 1・本番の試しの 1 本で確かめる・枚数の上限 10 は THTH の線） |
| 投稿の編集 `POST /{page_post_id}`（`message`）。**そのアプリで作った投稿だけ** | developers.facebook.com/docs/pages-api/posts | **L2**（応答 `{"success": true}` は **L3**） |
| コメントの非表示 `POST /{comment_id}` に `is_hidden`・削除 `DELETE /{comment_id}` | developers.facebook.com/docs/graph-api/reference/comment・pages-api/comments-mentions | **L2**（応答 `{"success": true}` は **L3**） |
| ページの日次 `GET /{page_id}/insights?metric=…&period=day`（`read_insights`）。v26 で外れた指標はエラーになる | developers.facebook.com/docs/graph-api/reference/v26.0/insights・changelog v25.0 | **L2**（`PAGE_DAILY_METRICS` の 7 つが v26 で生きているかは**未確認**・doctor の probe で 1 つずつ見る） |

**呼び出しは Meta の共通層 `thth/meta/` に乗る**（段 2・10/29）: 宛先の検査・版・`_request` の中身は
`meta/graph.py`、エラーの読みと振り分けは `meta/errors.py`、量の制限のヘッダは `meta/usage.py`、
`/debug_token` とデータの期限の見立ては `meta/tokens.py`。この模組に残る名前（`GRAPH_VERSION`・
`graph_origin`・`graph_error`・`is_permission_error` など）は共通層の別名で、呼び手と試験は
今までどおりここから引ける。共通層が足したもの: `appsecret_proof`（app.env の `META_APP_SECRET`
が読めるときは全部の呼び出しに付く）・量の制限の待ち（`rate_limited`・`thth quota`）。

**鍵の載せ方**: 公式の文書の例は GET の `access_token` をクエリに載せる形だけ
（overview）。**ヘッダで渡す形は公式の文書に見当たらない**（未確認）ので、GET と
DELETE はクエリ、POST は**本文（form）**に載せる——URL に残る機会を減らせるのは
POST だけ。どの形でも鍵の値は `redact.register_secret()` で登録し、例外文には
URL を入れない（`_scrub()`）。

**Threads との違いは、ここ（アダプタ）に閉じる**（設計 v1 §3.4）。core は
「渡したものが 1 回投げられた／投げられなかった」しか知らない。
"""
from __future__ import annotations

import datetime
import json
import re
import urllib.error
import urllib.parse
import uuid

from .. import httpsafe
from .. import jst
from .. import redact as redact_mod
from . import base
from ..meta import errors as meta_errors
from ..meta import graph as meta_graph
from ..meta import tokens as meta_tokens
from ..meta import usage as meta_usage

MEDIUM = "facebook"
# **版は 1 か所**（`meta/graph.py`）。ここは別名。
GRAPH_VERSION = meta_graph.GRAPH_VERSION
DEFAULT_BASE_URL = meta_graph.DEFAULT_BASE_URL
# 試験だけが loopback へ向ける（本番は既定のまま）。
BASE_URL_ENV = meta_graph.BASE_URL_ENV
DEFAULT_TIMEOUT_SECONDS = meta_graph.DEFAULT_TIMEOUT_SECONDS
PUBLISH_TIMEOUT_SECONDS = 30.0
PHOTO_TIMEOUT_SECONDS = 120.0

# Facebook ログイン for Business に求める権限（手順 §0-2・§3-1 で「ページのすべてを
# 管理」に足したもの・10/28 masaru が管理画面で足した）。**構成（config_id）が無い
# ときにだけ** `scope` として送る。構成があるときは構成の側の一覧が効く。
SCOPES = [
    "pages_show_list",
    "pages_read_engagement",
    "pages_manage_posts",
    "pages_read_user_content",
    "pages_manage_engagement",
    "read_insights",
    "pages_manage_metadata",
    "business_management",
    "public_profile",
]

# この媒体が**持ちうる**指標（`insights()` の `available`）。**定数**（今回取れた
# 指標ではない・Mastodon の説明と同じ）。
AVAILABLE_METRICS = ("views", "likes", "replies", "reposts")

PAGE_ID = re.compile(r"[0-9]{1,32}")
# ページの投稿・コメントの id（L3: `{親の id}_{自分の id}`）。`/`・`?`・`..` を含む
# 値で別の口へ向かう筋を構造で無くす（Mastodon の `POST_ID` と同じ理由）。
OBJECT_ID = re.compile(r"[0-9]{1,32}_[0-9]{1,32}")

# 複数の写真の上限（**THTH の線**・公式の数は未確認・計画 段 1 の 1）。
CAROUSEL_MAX = 10

# ページ単位の日次（計画 段 1 の 2・`account_insights()`）。**名前は 1 か所**——v26 で外れた
# 指標を呼ぶとエラーになる（事実 §1-7）ので、ここに無い名前は呼ばず、ここの名前で取れなかった
# ものは 0 で埋めずに `unavailable` に書く。指標を足す・外すのはこの表だけを直す。
PAGE_DAILY_METRICS = (
    "page_follows",
    "page_daily_follows",
    "page_daily_unfollows_unique",
    "page_media_view",
    "page_total_media_view_unique",
    "page_post_engagements",
    "page_views_total",
)
# ほかの媒体の日次と同じ名前でも持つ指標（`collection_context` の `followers_count`）。
# `page_follows` は「ページのフォロワーの数」（L2: insights の参照）。
PAGE_DAILY_ALIASES = {"page_follows": "followers_count"}

RECENT_POSTS_MAX = 100
# コメントの頁を辿る上限（1 頁 100 件 × 50 頁）。越えたら**黙って切らず** loud に断る。
COMMENT_PAGE_LIMIT = 100
COMMENT_MAX_PAGES = 50

# 画像（L2: photos の参照）。
PHOTO_FORMATS = ("jpeg", "png", "gif")
MIME = {"jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif"}
# 「4MB を超えられない」（L2）。単位が書かれていないので**狭いほうの SI** で数える。
MAX_PHOTO_BYTES = 4_000_000

# 権限系の code・鍵の無効（`meta/errors.py` の別名）。
_PERMISSION_ERROR_CODES = meta_errors.PERMISSION_ERROR_CODES
_INVALID_TOKEN_CODE = meta_errors.INVALID_TOKEN_CODE

# 本文の中の URL（RFC 3986 の ASCII の文字だけ。和文の句読点や全角の文字で切る）。
_URL_IN_TEXT = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&*+,;=%]+")


# データに触れる期限の注意の線（`meta/tokens.py` の別名・`maintain.DATA_ACCESS_WARN_DAYS` と同じ 14 日）。
DATA_ACCESS_WARN_DAYS = meta_tokens.DATA_ACCESS_WARN_DAYS
# 名前の長さの上限（断り文が崩れないように。ページ名は人が付けるので長いことがある）。
PAGE_NAME_MAX = 80
# 断り文に並べるページの数の上限（許可されたページが多い人の画面を埋めない）。
PAGES_LISTED_MAX = 20
# 台帳の `page_id` の正しい引き方（10/29 段 0・`account add`・`from_account` の断り文で同じ文）。
PAGE_ID_HINT = ("ページの「基本データ → ページの透明性」に出る「ページID」を書きます"
                "（ページの URL の profile.php?id= の id は人の画面の id で、API のページ id ではありません）")


def page_label(name, page_id) -> str:
    """断り文に出すページの名乗り: 「名前 id」。**名前が無い・形が違えば id だけ**（落ちない）。

    名前は人が付けた文字列なので、制御文字を空白に替えて長さを切る（断り文の 1 行を崩さない）。
    """
    pid = str(page_id) if isinstance(page_id, (str, int)) and not isinstance(page_id, bool) else "?"
    if not isinstance(name, str):
        return pid
    clean = " ".join("".join(c if c.isprintable() else " " for c in name).split())
    if not clean:
        return pid
    if len(clean) > PAGE_NAME_MAX:
        clean = clean[:PAGE_NAME_MAX] + "…"
    return f"{clean} {pid}"


def graph_origin() -> str:
    """宛先の origin。**公式の HTTPS か、明示した loopback の試験先だけ**（`meta.graph.origin`）。"""
    return meta_graph.origin()


def _parse_time(value) -> datetime.datetime | None:
    """`created_time`（`2026-10-29T01:02:03+0000`）を aware datetime に。読めなければ None。"""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    text = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", text)
    try:
        dt = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


# HTTPError の本文の `error` を読む・権限系か（`meta/errors.py` の別名。共通層が先に読んだ本文は
# 例外に覚えさせてあるので、ここでもう一度呼んでも同じ値が返る）。
graph_error = meta_errors.graph_error
is_permission_error = meta_errors.is_permission_error


class AdapterError(base.AdapterError):
    def __init__(self, message, *, failure=None, http_status=None, code=None):
        super().__init__(message)
        self.failure = failure
        self.http_status = http_status
        self.code = code


def intent_error(manifest):
    """要求を 1 本も出す前に、この添付が Facebook ページに載るかだけを答える。

    **画像 1〜`CAROUSEL_MAX` 枚**（この版の口・2 枚以上は `publish_carousel()`）。動画・
    型つきの添付・字幕・投稿の任意項目は断る（`media_delivery.error_for()` が呼ぶ）。
    """
    if manifest["captions"]:
        return "unsupported_attachment: facebook/captions"
    if manifest["attachments"]:
        return "unsupported_attachment: facebook/typed_attachment"
    if manifest["post_options"]:
        return "unsupported_attachment: facebook/post_options"
    rows = manifest["files"]
    if not rows:
        return "unsupported_attachment: facebook/no_media"
    if len(rows) > CAROUSEL_MAX:
        return f"media_limit_exceeded: count (THTH の線 {CAROUSEL_MAX} 枚)"
    for row in rows:
        if row["role"] != "media":
            return "unsupported_attachment: facebook/thumbnail"
        if row["kind"] != "image" or row["format"] not in PHOTO_FORMATS:
            return "unsupported_attachment: facebook/" + str(row["format"])
        if type(row["public_size"]) is not int or row["public_size"] < 1:
            return "media_size_unavailable"
        if row["public_size"] > MAX_PHOTO_BYTES:
            return f"media_limit_exceeded: bytes (provisional SI {MAX_PHOTO_BYTES})"
    return None


def _multipart(fields: dict, payload: bytes, mime: str):
    boundary = "thth-" + uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(("--" + boundary + "\r\nContent-Disposition: form-data; name=\""
                      + name + "\"\r\n\r\n").encode("ascii") + str(value).encode("utf-8") + b"\r\n")
    parts.append(("--" + boundary + "\r\nContent-Disposition: form-data; name=\"source\"; "
                  "filename=\"photo\"\r\nContent-Type: " + mime + "\r\n\r\n").encode("ascii")
                 + payload + b"\r\n")
    parts.append(("--" + boundary + "--\r\n").encode("ascii"))
    return b"".join(parts), {"Content-Type": "multipart/form-data; boundary=" + boundary}


class FacebookAdapter(base.Adapter):
    """Facebook ページ 1 つ（台帳の `page_id`）。鍵は**ページの鍵**（page access token）。

    台帳: `page_id`（必須・数字）。`.token`: `access_token`（ページの鍵）・`user_id`
    （＝ページの id）・`scopes`／`scopes_source`（`thth auth` が `/debug_token` で書く）。
    """

    # `recent_posts`（`GET /{page_id}/posts`）・`thread_read`（`fetch_post`）・
    # `views`（`post_media_view`・L3）・`link_preview`（本文の最初の URL を `link` に渡す）。
    # `inbox` は持たない——ページへの訪問者の投稿（利用者から始まった会話）を読む口は
    # この版では作っていない（`inbox()` は既定の空）。反応（repost・like）も持たない。
    # 段 1（10/29）: `account_insights`（ページの日次）・`carousel`（写真 2〜10 枚）・
    # `edit_post`（THTH で出した投稿の本文を直す）・`hide_reply`（コメントを隠す・戻す・消す）。
    # 段 3（10/29）: `quota`（`thth quota`・Meta の量の制限の使用量）。段 2 では試験の 1 行を変えない条件で
    # 足していなかった（THTH開発 第11 の許可で `tests/test_facebook_adapter.py` の能力の集合を 1 行直した）。
    CAPABILITIES: frozenset = frozenset({"recent_posts", "thread_read", "views", "link_preview",
                                         "account_insights", "carousel", "edit_post", "hide_reply",
                                         "quota"})
    UNSUPPORTED_REACTIONS = {
        "repost": "THTH の facebook の口はシェア（再投稿）を扱いません",
        "like": "THTH の facebook の口はいいねを扱いません",
    }
    TOKEN_KEYS = ("access_token",)
    TOKEN_SETUP_HINT = "thth auth"
    POST_ID_FORM_HINT = "Facebook の post_id は「数字_数字」の形です（例 123_456）。"
    # `thth auth` は Meta の app.env（`META_APP_ID`・`META_APP_SECRET`）を読む。
    AUTH_NEEDS_APP_ENV = True
    # **長く使えるユーザーの鍵から取ったページの鍵には期限が無い**（L2:
    # developers.facebook.com/docs/facebook-login/guides/access-tokens/get-long-lived
    # 「Long-lived Page access token do not have an expiration date」）。
    # `thth auth` は `/debug_token` の `expires_at` も記録し、期限が書いてあれば
    # `expires_in` を採る（`oauth.token_age_and_remaining()` は `expires_in` を優先）。
    TOKEN_NO_EXPIRY = True
    DELETE_PERMISSION = "pages_manage_posts"
    # 投稿の編集（L2: pages-api/posts）。
    EDIT_PERMISSION = "pages_manage_posts"
    # 訪問者のコメントを隠す・消す（L2: 権限の参照「訪問者のコメントを消すのは
    # pages_read_user_content の範囲・ページとしてのコメントは pages_manage_engagement」）。
    # 隠す・戻す・消すのどれにも **両方**が乗っていることを求める（どちらのコメントかを
    # 手元では見分けないため・狭いほうに倒さず両方）。
    MODERATE_PERMISSIONS = ("pages_manage_engagement", "pages_read_user_content")
    # 返信（コメント）に要る権限（L2: pages-api/posts の前提の一覧）。
    REPLY_PERMISSION = "pages_manage_engagement"
    DEBUG_TOKEN_KEY = "facebook_debug_token"
    DATA_ACCESS_KEY = "facebook_data_access"
    APPSECRET_PROOF_KEY = "facebook_appsecret_proof"
    # Meta の共通層（`thth/meta/`）に乗る印。timer が量の制限の待ちを見る（`core._meta_rate_limit_wait`）。
    META_GRAPH = True

    prepared_media_supported = True

    def __init__(self, *, base_url: str = DEFAULT_BASE_URL, access_token: str = "",
                 page_id: str = "", timeout: float = DEFAULT_TIMEOUT_SECONDS,
                 app_secret: str | None = None):
        self.base_url = httpsafe.validated_url(base_url, base=True)
        self.access_token = access_token
        redact_mod.register_secret(access_token)
        self.page_id = str(page_id or "")
        self.timeout = timeout
        # `appsecret_proof` の鍵（段 2・`from_account` が app.env から読む）。None なら付けない。
        self.app_secret = app_secret or None
        if self.app_secret:
            redact_mod.register_secret(self.app_secret)
        # 量の制限を残す口座（`from_account` が台帳の名前を入れる）。None なら残さず、待ちも見ない。
        self.usage_account = None
        self.granted_scopes = None
        self.auth_account = "<account>"
        # `.token` に保存したデータの期限（`thth auth` が書いた値・doctor が今の値と比べる）。
        self.recorded_data_access_expires_at = None

    @classmethod
    def from_account(cls, account_cfg: dict, token: dict):
        """台帳と `.token` から組み立てる。**`page_id` が無い・数字でなければ断る。**

        既定のページに落とすと、台帳を書き忘れた人が別のページに出す。
        """
        cfg = account_cfg or {}
        token = token or {}
        page_id = str(cfg.get("page_id") or "").strip()
        if not PAGE_ID.fullmatch(page_id):
            raise ValueError("台帳に page_id がありません（数字の id。例: \"page_id\": "
                             "\"1361492233719922\"。" + PAGE_ID_HINT + "）——どのページに出すかは"
                             "既定では決められません")
        recorded = token.get("user_id")
        if recorded and str(recorded) != page_id:
            # **取り違え防止**（Threads の handle の照合と同じ考え）。別ページの鍵を
            # この台帳で使わない。どのページ同士かを名前つきで言う（10/29 段 0）。
            raise ValueError(f"facebook_page_mismatch: .token のページ（{page_label(token.get('page_name'), recorded)}）"
                             f"と台帳の page_id {page_id} が違います。thth auth で認可し直してください")
        from .. import leave_gate
        adapter = cls(base_url=graph_origin(), access_token=token.get("access_token", ""),
                      page_id=page_id)
        scopes = token.get("scopes")
        if (token.get("scopes_source") == "response" and type(scopes) is list
                and all(type(v) is str for v in scopes)):
            adapter.granted_scopes = frozenset(scopes)
        adapter.auth_account = cfg.get("account") or "<account>"
        from .. import accounts as accounts_mod
        if accounts_mod.name_is_safe(cfg.get("account")):
            adapter.usage_account = cfg["account"]
        # `appsecret_proof`（段 2）: app.env の `META_APP_SECRET`。読めなければ付けずに今までどおり。
        secret = meta_graph.load_app_secret()
        if secret:
            adapter.app_secret = secret
        recorded_access = token.get("data_access_expires_at")
        if isinstance(recorded_access, int) and not isinstance(recorded_access, bool):
            adapter.recorded_data_access_expires_at = recorded_access
        return leave_gate.bind(adapter, account_cfg)

    @classmethod
    def is_post_id(cls, value) -> bool:
        return isinstance(value, str) and bool(OBJECT_ID.fullmatch(value.strip()))

    # ----- 秘密 ------------------------------------------------------------

    def _scrub(self, text) -> str:
        out = str(text)
        if self.access_token:
            out = out.replace(self.access_token, "***")
            out = out.replace(urllib.parse.quote(self.access_token, safe=""), "***")
        return redact_mod.redact(out) or ""

    # ----- HTTP ------------------------------------------------------------

    def _graph(self) -> meta_graph.Client:
        return meta_graph.Client(self.base_url, self.access_token, app_secret=self.app_secret,
                                 account=self.usage_account, timeout=self.timeout)

    def _auth_fields(self) -> dict:
        """鍵（と `appsecret_proof`）。multipart を自分で組む写真の口が本文に入れる。"""
        return self._graph().auth_fields()

    def _request(self, method: str, path: str, *, params: dict | None = None,
                 form: dict | None = None, body: bytes | None = None,
                 headers: dict | None = None, timeout: float | None = None,
                 urgent: bool = False):
        """1 回だけ投げて JSON を返す（`meta.graph.Client.request`）。**urllib の例外はそのまま
        上げる**（4xx と 5xx を呼び手が区別する）。量の制限は `meta_errors.RateLimited`。
        鍵は GET・DELETE はクエリ、POST は本文。"""
        return self._graph().request(method, path, params=params, form=form, body=body,
                                     headers=headers, timeout=timeout, urgent=urgent)

    def _fail(self, what: str, error: urllib.error.HTTPError, permission: str | None):
        """HTTPError を口の言葉に直す。権限系は `PermissionMissing`。"""
        err = graph_error(error)
        message = self._scrub(str(err.get("message") or error.reason or ""))[:170]
        code = err.get("code")
        if permission and error.code < 500 and is_permission_error(err):
            return base.PermissionMissing(permission, f"HTTP {error.code} {message}".strip())
        if code == _INVALID_TOKEN_CODE:
            return AdapterError(f"{what}: facebook_token_invalid（HTTP {error.code} {message}）。"
                                f"thth auth {self.auth_account} --by <名前> で認可し直してください",
                                failure="permission", http_status=error.code, code=code)
        return AdapterError(f"{what}: HTTP {error.code} {message}".strip(),
                            failure="permission" if is_permission_error(err) else None,
                            http_status=error.code, code=code)

    def _get(self, path: str, what: str, *, params: dict | None = None,
             permission: str | None = None) -> dict:
        """GET して object を返す。**取れなかったら失敗として上げる**（作法 5）。"""
        try:
            body = self._request("GET", path, params=params)
        except urllib.error.HTTPError as e:
            raise self._fail(what, e, permission) from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            # **確かめられなかった**（権限の有無については何も判っていない）。
            raise AdapterError(f"{what}: {self._scrub(e)}", failure="network") from None
        except ValueError as e:
            raise AdapterError(f"{what}: 応答が読めません（{self._scrub(e)}）") from None
        if not isinstance(body, dict):
            raise AdapterError(f"{what}: 応答が object ではありません（{type(body).__name__}）")
        if body.get("error"):
            err = body["error"] if isinstance(body["error"], dict) else {}
            if permission and is_permission_error(err):
                raise base.PermissionMissing(permission, self._scrub(err.get("message"))[:170])
            raise AdapterError(f"{what}: {self._scrub(err.get('message') or body['error'])[:200]}")
        return body

    def _rows(self, body: dict, what: str) -> list:
        """`data` が配列のときだけ「取れた」。無い・null・形違いは失敗（0 件と混ぜない）。"""
        rows = body.get("data")
        if not isinstance(rows, list):
            raise AdapterError(f"{what}: 応答に data の配列がありません。**取れて 0 件とは区別できません**")
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                raise AdapterError(f"{what}: data の {i} 番目が object ではありません")
        return rows

    @staticmethod
    def _after(body: dict):
        """次の頁の cursor（`paging.next` があるときだけ）。`next` の URL そのものは
        辿らない——鍵がクエリに入った絶対 URL で、宛先の検査を迂回するため。"""
        paging = body.get("paging")
        if not isinstance(paging, dict) or not paging.get("next"):
            return None
        cursors = paging.get("cursors")
        after = cursors.get("after") if isinstance(cursors, dict) else None
        if not isinstance(after, str) or not after:
            raise AdapterError("次の頁があると言われましたが cursor がありません（黙って切りません）")
        return after

    # ----- 投稿 ------------------------------------------------------------

    def _link_of(self, post: base.Post) -> str | None:
        if post.link:
            return post.link
        found = _URL_IN_TEXT.search(post.text or "")
        return found.group(0).rstrip(".,;:!?") if found else None

    def publish(self, post: base.Post, *, dry_run: bool, on_container_created=None,
                before_publish=None) -> base.PublishResult:
        """文（とリンク）は `POST /{page_id}/feed`、返信は `POST /{reply_to}/comments`、
        画像 1 枚は `POST /{page_id}/photos`（`publish_photo()`）。**再試行しない。**

        container の二段は無いので `on_container_created` は呼ばない。失敗の種類:
        権限系の 4xx →`permission`（出ていない）／ほかの 4xx →`publish_definite`／
        timeout・接続断・5xx・200 だが id 無し →`publish_ambiguous`。
        `post.topic` はこの媒体に無いので黙って無視する（設計 v2 §4.2）。
        """
        ts = jst.iso()
        if dry_run:
            return base.PublishResult(None, None, ts, error=None, failure="none")
        if post.media_manifest:
            # 2 枚以上は複数の写真の道（計画 段 1 の 1・原稿の添付の数で自動に決まる）。
            files = post.media_manifest.get("files") if isinstance(post.media_manifest, dict) else None
            if isinstance(files, list) and len(files) > 1:
                return publish_carousel(self, post, before_publish=before_publish)
            return publish_photo(self, post, before_publish=before_publish)
        if post.reply_to:
            target = str(post.reply_to).strip()
            if not (OBJECT_ID.fullmatch(target) or PAGE_ID.fullmatch(target)):
                return base.PublishResult(None, None, ts, error="facebook_reply_to_invalid: "
                                          + self.POST_ID_FORM_HINT, failure="publish_definite")
            path, form, permission = f"/{target}/comments", {"message": post.text}, self.REPLY_PERMISSION
        else:
            form = {"message": post.text}
            link = self._link_of(post)
            if link:
                form["link"] = link
            path, permission = f"/{self.page_id}/feed", "pages_manage_posts"
        if before_publish is not None:
            veto = before_publish()
            if veto:
                return base.PublishResult(None, None, ts, error=str(veto), failure="publish_vetoed")
        try:
            body = self._request("POST", path, form=form, timeout=PUBLISH_TIMEOUT_SECONDS)
        except meta_errors.RateLimited as e:
            # Meta が量の制限で断った・待ちの間で投げなかった。**出ていない**（段 2）。
            return base.PublishResult(None, None, ts, error=self._scrub(e)[:300],
                                      failure="publish_definite")
        except urllib.error.HTTPError as e:
            err = graph_error(e)
            message = self._scrub(err.get("message") or e.reason)[:170]
            if 400 <= e.code < 500 and (is_permission_error(err) or err.get("code") == _INVALID_TOKEN_CODE):
                return base.PublishResult(None, None, ts, failure="permission", error=self._scrub(
                    base.not_granted_message(permission, f"HTTP {e.code} {message}")
                    .replace("<account>", self.auth_account)))
            failure = "publish_definite" if 400 <= e.code < 500 else "publish_ambiguous"
            return base.PublishResult(None, None, ts, error=f"公開失敗: {e.code} {message}",
                                      failure=failure)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return base.PublishResult(None, None, ts, error=self._scrub(f"公開失敗: {e}"),
                                      failure="publish_ambiguous")
        except ValueError as e:
            return base.PublishResult(None, None, ts, error=self._scrub(f"公開失敗: 応答が読めません: {e}"),
                                      failure="publish_ambiguous")
        post_id = body.get("id") if isinstance(body, dict) else None
        if not isinstance(post_id, str) or not post_id:
            return base.PublishResult(None, None, ts, error="公開失敗: id無し",
                                      failure="publish_ambiguous")
        return base.PublishResult(post_id=post_id, url=post_url(post_id), ts=ts, error=None,
                                  failure="none")

    # ----- 取り下げ --------------------------------------------------------

    def delete_post(self, post_id: str) -> dict:
        """`DELETE /{page_post_id}` を 1 回（L2・応答は `{"success": true}`）。**再試行しない。**

        - 形は `数字_数字` だけ（L3）。
        - 権限系は `PermissionMissing`。
        - 消えている・見えない（L3: code 100・subcode 33）は `post_missing`。**消えたとは言わない。**
        - `success` が真のときだけ成功と言う。
        """
        value = post_id.strip() if isinstance(post_id, str) else ""
        if not OBJECT_ID.fullmatch(value):
            raise AdapterError("facebook_post_id_invalid: " + self.POST_ID_FORM_HINT + "取り下げません")
        try:
            body = self._request("DELETE", f"/{value}")
        except urllib.error.HTTPError as e:
            err = graph_error(e)
            if is_permission_error(err) or err.get("code") == _INVALID_TOKEN_CODE or e.code in (401, 403):
                raise base.PermissionMissing(self.DELETE_PERMISSION, f"HTTP {e.code}") from None
            if e.code == 404 or (err.get("code") == 100 and err.get("error_subcode") == 33):
                raise AdapterError(
                    "post_missing: 媒体にその投稿がありません（既に消えているか、この token "
                    "から見えません）。記録は変えていません。媒体の画面で確かめてください",
                    http_status=e.code) from None
            raise AdapterError(f"facebook_retract_http_{e.code}: 取り下げたとは言えません",
                               http_status=e.code) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            raise AdapterError("facebook_retract_failed: 取り下げたとは言えません（"
                               + self._scrub(e) + "）") from None
        if not isinstance(body, dict) or body.get("success") is not True:
            raise AdapterError("facebook_retract_unconfirmed: 応答に success: true がありません"
                               "（消えたとは言えません）")
        return {"deleted": True, "post_id": value}

    # ----- 直す・隠す・消す（段 1・10/29）-----------------------------------
    #
    # どれも **1 回だけ**投げ、再試行しない。失敗は `AdapterError.failure` で 2 つに分ける:
    # `definite`（4xx・Meta が断った＝変わっていない）／`ambiguous`（時間切れ・5xx・
    # `success: true` が無い＝変わったか分からない）。権限系は `PermissionMissing`。
    # 呼ぶのは `edit_cli.py` だけ（`tests/test_facebook_stage1.py` が source で縛る）。

    def _write(self, method: str, path: str, what: str, *, permission: str,
               form: dict | None = None) -> dict:
        try:
            if method == "POST":
                body = self._request("POST", path, form=form or {}, timeout=PUBLISH_TIMEOUT_SECONDS)
            else:
                body = self._request(method, path)
        except urllib.error.HTTPError as e:
            err = graph_error(e)
            message = self._scrub(err.get("message") or e.reason or "")[:170]
            if is_permission_error(err) or err.get("code") == _INVALID_TOKEN_CODE or e.code in (401, 403):
                raise base.PermissionMissing(permission, f"HTTP {e.code} {message}".strip()) from None
            if e.code == 404 or (err.get("code") == 100 and err.get("error_subcode") == 33):
                raise AdapterError(f"{what}: post_missing: 媒体にその id がありません（既に消えているか、"
                                   "この鍵から見えません）。何も変えていません",
                                   failure="definite", http_status=e.code, code=err.get("code")) from None
            raise AdapterError(f"{what}: HTTP {e.code} {message}".strip(),
                               failure="definite" if e.code < 500 else "ambiguous",
                               http_status=e.code, code=err.get("code")) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            raise AdapterError(f"{what}: {self._scrub(e)}（済んだかは分かりません）",
                               failure="ambiguous") from None
        if not isinstance(body, dict) or body.get("success") is not True:
            raise AdapterError(f"{what}: 応答に success: true がありません（済んだとは言えません）",
                               failure="ambiguous")
        return body

    def edit_post(self, post_id: str, text: str) -> dict:
        """出した投稿の本文を直す `POST /{page_post_id}`（`message`・L2）。

        **そのアプリで作った投稿しか直せない**（L2）。THTH で出した物に限るのは呼び手
        （`edit_cli`）が `sent/` の記録で見る。ここでは形だけを見る。
        """
        value = post_id.strip() if isinstance(post_id, str) else ""
        if not OBJECT_ID.fullmatch(value):
            raise AdapterError("facebook_post_id_invalid: " + self.POST_ID_FORM_HINT + "直しません",
                               failure="definite")
        if not isinstance(text, str) or not text.strip():
            raise AdapterError("投稿の編集: 本文が空です（直しません）", failure="definite")
        self._write("POST", f"/{value}", "投稿の編集", permission=self.EDIT_PERMISSION,
                    form={"message": text})
        return {"edited": True, "post_id": value}

    def fetch_reply(self, comment_id: str) -> dict:
        """コメントを 1 件読む（隠す・消す前に人に見せる・読むだけ）。"""
        value = str(comment_id or "").strip()
        if not OBJECT_ID.fullmatch(value):
            raise AdapterError("コメントの取得: Facebook のコメントの id は「数字_数字」の形です")
        row = self._get(f"/{value}", "コメントの取得",
                        params={"fields": "id,message,created_time,from{id,name},parent{id},"
                                          "is_hidden,can_hide,can_remove"},
                        permission="pages_read_user_content")
        out = self._message(row, value, None)
        author = row.get("from") if isinstance(row.get("from"), dict) else {}
        out["by_page"] = str(author.get("id") or "") == self.page_id
        for key in ("is_hidden", "can_hide", "can_remove"):
            out[key] = row.get(key) if isinstance(row.get(key), bool) else None
        return out

    def hide_reply(self, comment_id: str, *, hidden: bool = True) -> dict:
        """コメントを隠す・戻す `POST /{comment_id}` に `is_hidden`（L2・ページのコメントにだけ効く）。"""
        value = str(comment_id or "").strip()
        if not OBJECT_ID.fullmatch(value):
            raise AdapterError("コメントの非表示: Facebook のコメントの id は「数字_数字」の形です",
                               failure="definite")
        if type(hidden) is not bool:
            raise AdapterError("コメントの非表示: hidden は真か偽です", failure="definite")
        self._write("POST", f"/{value}", "コメントを隠す" if hidden else "コメントを戻す",
                    permission=self.MODERATE_PERMISSIONS[0],
                    form={"is_hidden": "true" if hidden else "false"})
        return {"hidden": hidden, "comment_id": value}

    def delete_reply(self, comment_id: str) -> dict:
        """コメントを消す `DELETE /{comment_id}`（L2）。**投稿の取り下げ（`delete_post`）とは別の口。**"""
        value = str(comment_id or "").strip()
        if not OBJECT_ID.fullmatch(value):
            raise AdapterError("コメントの削除: Facebook のコメントの id は「数字_数字」の形です",
                               failure="definite")
        self._write("DELETE", f"/{value}", "コメントの削除",
                    permission=self.MODERATE_PERMISSIONS[0])
        return {"deleted": True, "comment_id": value}

    # ----- 会話 ------------------------------------------------------------

    def _message(self, row: dict, root_post: str, parent_default: str) -> dict:
        message_id = row.get("id")
        if not isinstance(message_id, str) or not message_id:
            raise AdapterError("会話: コメントに id がありません（**件数として数えません**）")
        author = row.get("from") if isinstance(row.get("from"), dict) else {}
        author_id = author.get("id") if isinstance(author.get("id"), str) else None
        parent = row.get("parent") if isinstance(row.get("parent"), dict) else None
        return {
            "message_id": message_id,
            # 名前は返らないことがある（L3）。**分からないを空文字で埋めない。**
            "username": author.get("name") if isinstance(author.get("name"), str) else None,
            "text": row.get("message") if isinstance(row.get("message"), str) else "",
            "timestamp": row.get("created_time"),
            "replied_to": (parent or {}).get("id") or parent_default,
            "root_post": root_post,
            "medium": MEDIUM,
            # 身元はページごとに振られる利用者の id（名前は変えられる）。
            "author_key": base.author_key(MEDIUM, author_id),
            "reply_deadline": None,
        }

    def conversation(self, post_id: str, *, since: str | None = None) -> list:
        """`GET /{post_id}/comments?filter=stream`（全階層・時刻順・L3）を頁ごとに辿る。

        `COMMENT_MAX_PAGES` を越えたら**黙って切らず**断る。`since` より古いものは
        落とす（**読めない時刻は落とさない**・Mastodon と同じ）。
        """
        value = str(post_id or "").strip()
        if not OBJECT_ID.fullmatch(value):
            raise AdapterError("会話: " + self.POST_ID_FORM_HINT)
        params = {"filter": "stream", "order": "chronological", "limit": COMMENT_PAGE_LIMIT,
                  "fields": "id,message,created_time,from{id,name},parent{id}"}
        out = []
        after = None
        for _ in range(COMMENT_MAX_PAGES):
            page = dict(params)
            if after:
                page["after"] = after
            body = self._get(f"/{value}/comments", "会話", params=page,
                             permission="pages_read_user_content")
            out.extend(self._message(row, value, value) for row in self._rows(body, "会話"))
            after = self._after(body)
            if not after:
                break
        else:
            raise AdapterError(f"会話: {COMMENT_MAX_PAGES} 頁を越えました（黙って切りません）")
        floor = _parse_time(since) if since else None
        if floor is None:
            return out
        return [m for m in out if (_parse_time(m.get("timestamp")) or floor) >= floor]

    def fetch_post(self, post_id: str) -> dict:
        """根を 1 件（`GET /{post_id}`）。**自分のページの投稿だけ**を返す。"""
        value = str(post_id or "").strip()
        if not OBJECT_ID.fullmatch(value):
            raise AdapterError("投稿の取得: " + self.POST_ID_FORM_HINT)
        row = self._get(f"/{value}", "投稿の取得",
                        params={"fields": "id,message,created_time,permalink_url,from{id,name}"},
                        permission="pages_read_engagement")
        author = row.get("from") if isinstance(row.get("from"), dict) else {}
        if str(author.get("id") or "") != self.page_id:
            raise AdapterError("投稿の取得: このページの投稿ではありません")
        out = self._message(row, value, None)
        out["replied_to"] = None
        if isinstance(row.get("permalink_url"), str):
            out["permalink"] = row["permalink_url"]
        return out

    # ----- 実測 ------------------------------------------------------------

    def recent_posts(self, *, limit: int = 25) -> list:
        """`GET /{page_id}/posts`（ページ自身の投稿・新しい順・L3）。"""
        body = self._get(f"/{self.page_id}/posts", "直近の投稿",
                         params={"fields": "id,message,created_time,permalink_url",
                                 "limit": max(1, min(int(limit), RECENT_POSTS_MAX))},
                         permission="pages_read_engagement")
        out = []
        for row in self._rows(body, "直近の投稿"):
            if not isinstance(row.get("id"), str) or not row["id"]:
                continue
            out.append({"post_id": row["id"], "timestamp": row.get("created_time"),
                        "url": row.get("permalink_url"),
                        "text": row.get("message") if isinstance(row.get("message"), str) else "",
                        "topic": None})
        return out

    def insights(self, post_id: str) -> dict:
        """反応・コメント・シェアの数（`GET /{post_id}`）と表示（`post_media_view`）。

        - likes ← `reactions.summary.total_count`（いいね以外の反応も含む・L3）
        - replies ← `comments.summary.total_count`
        - reposts ← `shares.count`（**シェアが 0 のとき欄ごと返らない**ことがある——
          その回は入れない。**0 と混ぜない**・L3）
        - views ← `GET /{post_id}/insights?metric=post_media_view` の lifetime（L2 で
          v26 に在る指標・views と読むのは L3）。取れなければ入れず、理由を
          `unavailable` に書く（`metrics_of()` は読まない欄）。
        """
        value = str(post_id or "").strip()
        if not OBJECT_ID.fullmatch(value):
            raise AdapterError("投稿の数: " + self.POST_ID_FORM_HINT)
        body = self._get(f"/{value}", "投稿の数", params={
            "fields": "shares,comments.summary(true).limit(0),reactions.summary(true).limit(0)"},
            permission="pages_read_engagement")
        metrics: dict = {}
        unavailable: dict = {}

        def number(obj, *keys):
            for key in keys:
                obj = obj.get(key) if isinstance(obj, dict) else None
            return obj if isinstance(obj, int) and not isinstance(obj, bool) else None

        for name, keys in (("likes", ("reactions", "summary", "total_count")),
                           ("replies", ("comments", "summary", "total_count")),
                           ("reposts", ("shares", "count"))):
            n = number(body, *keys)
            if n is not None:
                metrics[name] = n
        try:
            data = self._rows(self._get(f"/{value}/insights", "投稿の表示",
                                        params={"metric": "post_media_view"},
                                        permission="read_insights"), "投稿の表示")
            row = next((r for r in data if r.get("name") == "post_media_view"), None)
            values = row.get("values") if isinstance(row, dict) else None
            views = number(values[-1], "value") if isinstance(values, list) and values and isinstance(values[-1], dict) else None
            if views is None:
                unavailable["views"] = "応答に post_media_view の値がありません"
            else:
                metrics["views"] = views
        except base.AdapterError as e:
            unavailable["views"] = self._scrub(e)[:200]
        result = {"metrics": metrics, "available": list(AVAILABLE_METRICS)}
        if unavailable:
            result["unavailable"] = unavailable
        return result

    def _page_metric_rows(self, metrics: tuple, what: str, *, since=None, until=None) -> dict:
        """`GET /{page_id}/insights?metric=…&period=day` → `{名前: 行}`。失敗は上げる。"""
        params = {"metric": ",".join(metrics), "period": "day"}
        if since is not None:
            params["since"] = since
        if until is not None:
            params["until"] = until
        body = self._get(f"/{self.page_id}/insights", what, params=params, permission="read_insights")
        return {row.get("name"): row for row in self._rows(body, what)
                if isinstance(row.get("name"), str)}

    @staticmethod
    def _unreachable(exc) -> bool:
        """確かめられなかった（網・5xx・量の制限）か。4xx と「200 だが error」は Meta が断った側。

        量の制限（段 2）は「その指標が無い」ではない——その日は記録せず、待ってから取り直す。
        """
        return (getattr(exc, "failure", None) in ("network", meta_errors.RATE_LIMITED)
                or (getattr(exc, "http_status", None) or 0) >= 500)

    def account_insights(self, user_id: str, *, since: str, until: str) -> dict:
        """ページ単位の日次（計画 段 1 の 2・`collect._collect_account_daily` が呼ぶ）。

        指標は `PAGE_DAILY_METRICS` だけ。まず 1 回でまとめて訊き、Meta が 4xx で断ったら
        （v26 で外れた名前が 1 つでも混ざるとまとめて断られる・L3）**1 つずつ訊き直す**。
        取れなかった指標は **0 で埋めず** `unavailable` に名前と理由を書く（投稿の数と同じ作法）。
        権限系は `PermissionMissing`、網・5xx は上げる（その日は記録せず次の採取で取り直す）。

        日の区切りは Meta の側の「日」（太平洋時間と読める・L3）。`since`・`until` は
        collect が渡す JST の前日の 0 時〜24 時で、返った値の**最後の 1 つ**を採る。
        """
        if str(user_id or "") != self.page_id:
            raise AdapterError("ページの日次: 台帳のページと違う id です（訊きません）")
        what = "ページの日次"
        metrics: dict = {}
        unavailable: dict = {}
        try:
            rows = self._page_metric_rows(PAGE_DAILY_METRICS, what, since=since, until=until)
        except base.PermissionMissing:
            raise
        except base.AdapterError as e:
            if self._unreachable(e):
                raise
            rows = None
        if rows is not None:
            for name in PAGE_DAILY_METRICS:
                value = _day_value(rows.get(name))
                if value is None:
                    unavailable[name] = "応答にこの指標の値がありません"
                else:
                    metrics[name] = value
        else:
            for name in PAGE_DAILY_METRICS:
                try:
                    one = self._page_metric_rows((name,), what, since=since, until=until)
                except base.PermissionMissing:
                    raise
                except base.AdapterError as e:
                    if self._unreachable(e):
                        raise
                    unavailable[name] = self._scrub(e)[:200]
                    continue
                value = _day_value(one.get(name))
                if value is None:
                    unavailable[name] = "応答にこの指標の値がありません"
                else:
                    metrics[name] = value
        for name, alias in PAGE_DAILY_ALIASES.items():
            if name in metrics:
                metrics[alias] = metrics[name]
        out = dict(metrics)
        if unavailable:
            out["unavailable"] = unavailable
        return out

    # ----- 素性・検査 ------------------------------------------------------

    def whoami(self) -> dict:
        """`GET /me?fields=id,name`（ページの鍵なら `/me` はそのページ）。

        **台帳の `page_id` と違えば断る**（取り違え防止・`thth token set` もここを通る）。
        `username` は**ページの id**（名前は変えられるので照合に使わない）。
        """
        body = self._get("/me", "ページの確認", params={"fields": "id,name"},
                         permission="pages_show_list")
        page_id = body.get("id")
        if not isinstance(page_id, str) or not PAGE_ID.fullmatch(page_id):
            raise AdapterError("ページの確認: 応答に id がありません")
        name = body.get("name") if isinstance(body.get("name"), str) else None
        if self.page_id and page_id != self.page_id:
            raise AdapterError(f"facebook_page_mismatch: この鍵のページは {page_label(name, page_id)} で、"
                               f"台帳の page_id {self.page_id} と別のページです")
        return {"user_id": page_id, "username": page_id, "name": name}

    def _debug_token(self) -> dict:
        """`GET /debug_token`（L2: 呼ぶ側の鍵は「アプリの鍵」か「アプリの開発者のユーザーの
        鍵」。**ページの鍵で訊けるかは文書に無い**＝未確認）。訊けなければ例外。"""
        body = self._get("/debug_token", "鍵の権限", params={"input_token": self.access_token})
        data = meta_tokens.debug_data(body)
        if data is None:
            raise AdapterError("鍵の権限: 応答に data がありません")
        return data

    def probe(self, *, get=None) -> list:
        """読み取りだけで能力を測る（`thth doctor`）。**投稿・返信・削除は呼ばない。**

        `get`（doctor の取得口）は使わない——叩く口は自前の `_get` だけで、宛先は
        `graph_origin()` の検査を通る。
        """
        results = []

        def row(key, label, permission, ok, detail, **extra):
            results.append({"name": key, "key": key, "label": label, "permission": permission,
                            "ok": ok, "detail": detail, "body": None, **extra})

        def failed(key, label, permission, exc):
            # 3 値（`threads.py` の probe と同じ物差し）: 権限系・4xx・形違いは ×、
            # 5xx・ネットワーク層は「確かめられなかった」（－）。
            unverified = self._unreachable(exc)
            ok = None if unverified else False
            row(key, label, permission, ok, self._scrub(exc)[:300],
                failure=getattr(exc, "failure", None) or (
                    "permission" if isinstance(exc, base.PermissionMissing) else None),
                http_status=getattr(exc, "http_status", None))

        try:
            me = self.whoami()
        except Exception as e:   # noqa: BLE001 — probe は落ちずに × を返す口
            failed("whoami", "ページの確認", "pages_show_list", e)
        else:
            row("whoami", "ページの確認", "pages_show_list", True,
                f"page_id={me['user_id']} name={me.get('name')}")

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
                expires = data.get("expires_at")
                detail = (f"{len(scopes)} 個・expires_at={expires}"
                          + (f"・足りない={','.join(missing)}" if missing else "・求めた権限はすべて有り"))
                row(self.DEBUG_TOKEN_KEY, "鍵の権限（/debug_token）", "（全権限）",
                    not missing, detail, scopes=sorted(scopes), missing=missing)
            else:
                row(self.DEBUG_TOKEN_KEY, "鍵の権限（/debug_token）", "（全権限）", None,
                    "応答に scopes の一覧がありません")
            row(self.DATA_ACCESS_KEY, "データに触れる期限（data_access_expires_at）", "（全権限）",
                *self._data_access_verdict(data.get("data_access_expires_at")))

        latest = None
        try:
            posts = self.recent_posts(limit=1)
        except Exception as e:   # noqa: BLE001
            failed("posts", "ページの投稿の一覧", "pages_read_engagement", e)
        else:
            latest = posts[0]["post_id"] if posts else None
            row("posts", "ページの投稿の一覧", "pages_read_engagement", True, f"{len(posts)} 件")

        for key, label, permission, call in (
                ("comments", "投稿へのコメント", "pages_read_user_content",
                 lambda pid: f"{len(self.conversation(pid))} 件"),
                ("insights", "投稿の数", "read_insights",
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

        # ページの日次の指標を 1 つずつ（計画 段 1 の本番の確かめ ①・G-7）。v26 で外れた名前は
        # Meta が 4xx で断るので ×（`PAGE_DAILY_METRICS` から外す候補）。値が無いだけ（ページの
        # 規模で数が出ないなど）は「―」。読むだけ。
        for name in PAGE_DAILY_METRICS:
            key, label = "page_insights." + name, "ページの日次 " + name
            try:
                value = _day_value(self._page_metric_rows((name,), label).get(name))
            except base.PermissionMissing as e:
                failed(key, label, "read_insights", e)
                continue
            except Exception as e:   # noqa: BLE001
                if self._unreachable(e):
                    failed(key, label, "read_insights", e)
                else:
                    row(key, label, "read_insights", False,
                        "unavailable: Meta がこの指標を断りました（" + self._scrub(e)[:200]
                        + "）。PAGE_DAILY_METRICS から外す候補です",
                        http_status=getattr(e, "http_status", None))
                continue
            if value is None:
                row(key, label, "read_insights", None,
                    "unavailable: 応答に値がありません（ページの規模で数が出ないことがあります）")
            else:
                row(key, label, "read_insights", True, f"直近の値 {value}")

        # 書き込みの権限は読み取りでは確かめられない。`/debug_token` が取れたときだけ言う。
        for key, label, permission in (("publish", "ページへの投稿", "pages_manage_posts"),
                                       ("reply", "コメントへの返事", "pages_manage_engagement")):
            if granted is None:
                row(key, label, permission, None, "読み取りでは確かめられません")
            else:
                row(key, label, permission, permission in granted,
                    "トークンに乗っています（/debug_token）" if permission in granted
                    else base.not_granted_message(permission).replace("<account>", self.auth_account))

        # `appsecret_proof`（段 2）。付けて上の読みが通れば、Meta がこの鍵とアプリの組を認めた。
        if self.app_secret:
            row(self.APPSECRET_PROOF_KEY, "appsecret_proof", "（全部の呼び出し）", True,
                "付けて呼んでいます（app.env の META_APP_SECRET）")
        else:
            row(self.APPSECRET_PROOF_KEY, "appsecret_proof", "（全部の呼び出し）", None,
                "付けていません（app.env の META_APP_ID・META_APP_SECRET が読めない）。"
                "付けないと Meta のアプリの設定「Require App Secret」を入れられません")
        return results

    def _data_access_verdict(self, value, *, now=None):
        """`/debug_token` の**今の** `data_access_expires_at` → `(ok, detail)`（10/29 段 0・計画 C-2 の 2）。

        見立ては `meta.tokens.data_access_verdict`（段 2 で抜き出した）。**`.token` は書き直さない**——
        doctor は読むだけの道具で、`.token` を書くと認可の世代（`doctor._credential_generation`）が
        変わってしまう。
        """
        return meta_tokens.data_access_verdict(
            value, recorded=self.recorded_data_access_expires_at,
            hint=f"thth auth {self.auth_account} --by <名前>", now=now)

    def quota(self):
        """Meta の量の制限の使用量（段 2・`thth quota`）。

        待ちの間でなければ、軽い読み `GET /{page_id}?fields=id` を 1 回投げて**今の**ヘッダを
        `state/<口座>/meta_usage.json` に残し、その中身を返す。待ちの間は投げず、残っている値と
        待ちを返す。投げて失敗したら上げる（取れなかったことを 0 と混ぜない）。
        """
        if not self.usage_account:
            raise AdapterError("使用量: 口座の台帳から組み立てた adapter でだけ訊けます")
        waiting = meta_usage.waiting(self.usage_account)
        fresh = False
        if waiting is None:
            self._get(f"/{self.page_id}", "使用量", params={"fields": "id"},
                      permission="pages_read_engagement")
            fresh = True
        out = meta_usage.summary(self.usage_account)
        out.update({"medium": MEDIUM, "source": "headers", "fresh": fresh,
                    "appsecret_proof": bool(self.app_secret)})
        return out

    def refresh_token(self, token):
        raise NotImplementedError("Facebook のページの鍵に期限は無い（TOKEN_NO_EXPIRY の説明）")


def _day_value(row):
    """insights の行（`values: [{value, end_time}]`）の**最後の**値。数か、数だけの dict。

    無い・形が違う・数でないは None（**0 で埋めない**）。
    """
    values = row.get("values") if isinstance(row, dict) else None
    if not isinstance(values, list) or not values or not isinstance(values[-1], dict):
        return None
    value = values[-1].get("value")
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if (isinstance(value, dict) and value
            and all(isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool)
                    for k, v in value.items())):
        return dict(value)
    return None


def post_url(post_id: str) -> str:
    """人が開ける URL（L3: `facebook.com/<id>` で開ける）。"""
    return "https://www.facebook.com/" + urllib.parse.quote(post_id, safe="_")


def publish_photo(adapter: FacebookAdapter, post: base.Post, *, before_publish=None):
    """画像 1 枚を `POST /{page_id}/photos`（multipart の `source`・`caption`・`alt_text_custom`）。

    **送るのは 1 回だけ**（upload と公開が同じ要求）。durable intent（`media_delivery`）
    に phase を落としてから投げる。結果不明は自動で投げ直さない。4xx は出ていない
    （`publish_definite`・権限系は `permission`）、timeout・5xx・id 無しは `media_ambiguous`。
    """
    from .. import accounts, media
    ts = jst.iso()
    progress = post.media_progress
    phase = "preflight"
    vetoed = False

    def record(value, **details):
        nonlocal phase
        phase = value
        progress(value, remote_ids=[], **details)

    try:
        if not callable(progress):
            raise media.MediaError("media_journal_required")
        reason = intent_error(post.media_manifest)
        if reason:
            raise media.MediaError(reason)
        if (len(post.media_files) != 1
                or post.media_files[0].manifest != post.media_manifest["files"][0]):
            raise media.MediaError("media_prepared_mismatch")
        if post.reply_to:
            raise media.MediaError("unsupported_attachment: facebook/reply_media")
        item = post.media_files[0]
        row = item.manifest
        if before_publish is not None:
            veto = before_publish()
            if veto:
                vetoed = True
                raise media.MediaError(str(veto))
        payload = b"".join(item.chunks())
        fields = {"caption": post.text, **adapter._auth_fields()}
        if isinstance(row.get("alt"), str) and row["alt"].strip():
            fields["alt_text_custom"] = row["alt"]
        body, headers = _multipart(fields, payload, MIME[row["format"]])
        record("publishing")
        value = adapter._request("POST", f"/{adapter.page_id}/photos", body=body,
                                 headers=headers, timeout=PHOTO_TIMEOUT_SECONDS)
        post_id = value.get("post_id") if isinstance(value, dict) else None
        photo_id = value.get("id") if isinstance(value, dict) else None
        if not isinstance(post_id, str) or not post_id:
            raise media.MediaError("media_response_invalid: post_id")
        record("published", post_id=post_id)
        return base.PublishResult(post_id, post_url(post_id), ts, media=[{
            "sha256": row["public_sha256"], "kind": row["kind"],
            "alt_present": base.alt_present(row),
            "remote_id": photo_id if isinstance(photo_id, str) else None}])
    except accounts.AccountStopped:
        return base.PublishResult(None, None, ts, error="account_stopped",
                                  failure="media_ambiguous" if phase != "preflight" else "publish_vetoed")
    except meta_errors.RateLimited as exc:
        # 量の制限（段 2）: Meta が断った・待ちの間で投げなかった。**出ていない。**
        try:
            if callable(progress):
                record("failed", reason="meta_rate_limited")
        except (OSError, ValueError, accounts.AccountStopped):
            pass
        return base.PublishResult(None, None, ts, error=adapter._scrub(exc)[:300],
                                  failure="publish_definite")
    except urllib.error.HTTPError as exc:
        err = graph_error(exc)
        definite = 400 <= exc.code < 500
        permission = definite and (is_permission_error(err) or err.get("code") == _INVALID_TOKEN_CODE)
        reason = f"media_{phase}_http_{exc.code}"
        try:
            record("failed" if definite else "unknown", reason=reason)
        except (OSError, ValueError, accounts.AccountStopped):
            pass
        if permission:
            return base.PublishResult(None, None, ts, failure="permission", error=adapter._scrub(
                base.not_granted_message("pages_manage_posts", f"HTTP {exc.code}")
                .replace("<account>", adapter.auth_account)))
        return base.PublishResult(None, None, ts, error=reason,
                                  failure="publish_definite" if definite else "media_ambiguous")
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
        uncertain = phase != "preflight"
        reason = (str(exc) if isinstance(exc, media.MediaError) else "media_" + phase + "_failed")
        try:
            if callable(progress):
                record("unknown" if uncertain else "failed", reason=reason)
        except (OSError, ValueError, accounts.AccountStopped):
            pass
        # 要求の前に止まったものは**出ていない**。直前の関門で止めたら `publish_vetoed`、
        # 添付が載らない・用意と食い違うなら `publish_definite`。
        return base.PublishResult(None, None, ts, error=adapter._scrub(reason),
                                  failure="media_ambiguous" if uncertain
                                  else "publish_vetoed" if vetoed else "publish_definite")


def publish_carousel(adapter: FacebookAdapter, post: base.Post, *, before_publish=None):
    """写真 2〜`CAROUSEL_MAX` 枚を 1 本に（計画 段 1 の 1・手順は**二次**の資料だけ）。

    1. 1 枚ずつ `POST /{page_id}/photos`（multipart・`published=false`・`alt_text_custom`）→ 写真の id。
       **未公開の写真は投稿ではない**ので、ここまでの失敗（4xx も時間切れも）は「出ていない」
       （`publish_definite`）。結果の分からない写真が残っても人の目には出ない（L3）。
    2. `POST /{page_id}/feed` に `message` と `attached_media[i]={"media_fbid": <id>}`（原稿の添付の順）。
       ここからが公開。4xx は出ていない（権限系は `permission`）、時間切れ・5xx・id 無しは
       `media_ambiguous`（自動で投げ直さない）。

    直前の関門（`before_publish`）は 1 の前と 2 の前で 2 回見る（上げている間に止められたら出さない）。
    """
    from .. import accounts, media
    ts = jst.iso()
    progress = post.media_progress
    phase = "preflight"
    vetoed = False
    uploaded: list = []

    def record(value, **details):
        nonlocal phase
        phase = value
        progress(value, remote_ids=list(uploaded), **details)

    def gate():
        nonlocal vetoed
        if before_publish is not None:
            veto = before_publish()
            if veto:
                vetoed = True
                raise media.MediaError(str(veto))

    try:
        if not callable(progress):
            raise media.MediaError("media_journal_required")
        reason = intent_error(post.media_manifest)
        if reason:
            raise media.MediaError(reason)
        rows = post.media_manifest["files"]
        if (len(rows) < 2 or len(post.media_files) != len(rows)
                or any(item.manifest != row for item, row in zip(post.media_files, rows))):
            raise media.MediaError("media_prepared_mismatch")
        if post.reply_to:
            raise media.MediaError("unsupported_attachment: facebook/reply_media")
        gate()
        for item in post.media_files:
            row = item.manifest
            fields = {"published": "false", **adapter._auth_fields()}
            if isinstance(row.get("alt"), str) and row["alt"].strip():
                fields["alt_text_custom"] = row["alt"]
            body, headers = _multipart(fields, b"".join(item.chunks()), MIME[row["format"]])
            record("uploading")
            value = adapter._request("POST", f"/{adapter.page_id}/photos", body=body,
                                     headers=headers, timeout=PHOTO_TIMEOUT_SECONDS)
            photo_id = value.get("id") if isinstance(value, dict) else None
            if not isinstance(photo_id, str) or not PAGE_ID.fullmatch(photo_id):
                raise media.MediaError("media_response_invalid: photo id")
            uploaded.append(photo_id)
        gate()
        form = {"message": post.text}
        for i, photo_id in enumerate(uploaded):
            form[f"attached_media[{i}]"] = json.dumps({"media_fbid": photo_id}, separators=(",", ":"))
        record("publishing")
        value = adapter._request("POST", f"/{adapter.page_id}/feed", form=form,
                                 timeout=PUBLISH_TIMEOUT_SECONDS)
        post_id = value.get("id") if isinstance(value, dict) else None
        if not isinstance(post_id, str) or not post_id:
            raise media.MediaError("media_response_invalid: post_id")
        record("published", post_id=post_id)
        return base.PublishResult(post_id, post_url(post_id), ts, media=[
            {"sha256": item.manifest["public_sha256"], "kind": item.manifest["kind"],
             "alt_present": base.alt_present(item.manifest), "remote_id": photo_id}
            for item, photo_id in zip(post.media_files, uploaded)])
    except accounts.AccountStopped:
        return base.PublishResult(None, None, ts, error="account_stopped",
                                  failure="media_ambiguous" if phase == "publishing" else "publish_vetoed")
    except meta_errors.RateLimited as exc:
        # 量の制限（段 2）: Meta が断った・待ちの間で投げなかった。**出ていない。**
        try:
            if callable(progress):
                record("failed", reason="meta_rate_limited")
        except (OSError, ValueError, accounts.AccountStopped):
            pass
        return base.PublishResult(None, None, ts, error=adapter._scrub(exc)[:300],
                                  failure="publish_definite")
    except urllib.error.HTTPError as exc:
        err = graph_error(exc)
        definite = 400 <= exc.code < 500
        # 未公開の写真を上げている間の失敗は、5xx でも「出ていない」。
        not_out = definite or phase != "publishing"
        permission = definite and (is_permission_error(err) or err.get("code") == _INVALID_TOKEN_CODE)
        reason = f"media_{phase}_http_{exc.code}"
        try:
            record("failed" if not_out else "unknown", reason=reason)
        except (OSError, ValueError, accounts.AccountStopped):
            pass
        if permission:
            return base.PublishResult(None, None, ts, failure="permission", error=adapter._scrub(
                base.not_granted_message("pages_manage_posts", f"HTTP {exc.code}")
                .replace("<account>", adapter.auth_account)))
        return base.PublishResult(None, None, ts, error=reason,
                                  failure="publish_definite" if not_out else "media_ambiguous")
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
        uncertain = phase == "publishing"
        reason = (str(exc) if isinstance(exc, media.MediaError) else "media_" + phase + "_failed")
        try:
            if callable(progress):
                record("unknown" if uncertain else "failed", reason=reason)
        except (OSError, ValueError, accounts.AccountStopped):
            pass
        return base.PublishResult(None, None, ts, error=adapter._scrub(reason),
                                  failure="media_ambiguous" if uncertain
                                  else "publish_vetoed" if vetoed else "publish_definite")
