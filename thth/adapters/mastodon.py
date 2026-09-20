"""MastodonAdapter（設計 v2 §4.2「境界の拡張」・T-B3）。

**本物のインスタンスは叩かない。** `instance` がそのまま base URL なので、テストは
`http.server` の偽サーバにだけ向ける（`tests/test_mastodon_adapter.py`）。

証拠段階（この repo の外の主張には印を付ける・作法 7）:

| 何 | 出典 | 印 |
|---|---|---|
| `POST /api/v1/statuses`（`status`・`in_reply_to_id`・`visibility`） | docs.joinmastodon.org/methods/statuses/ | **L2** |
| `Idempotency-Key` ヘッダ（同じ本文の二重投稿を**サーバ側で**防ぐ・保持は 1 時間） | 同上（"Idempotency keys are stored for up to 1 hour"） | **L2** |
| `GET /api/v1/statuses/:id`（`favourites_count`・`replies_count`・`reblogs_count`） | 同上 | **L2** |
| Status の `quotes_count`（引用数） | 同上 | **L2**（**このアダプタは取っていない**・監査 2・2026-09-13） |
| `GET /api/v1/statuses/:id/context`（`ancestors`・`descendants`） | 同上 | **L2** |
| `GET /api/v1/accounts/verify_credentials`（`id`・`acct`） | docs.joinmastodon.org/methods/accounts/ | **L2** |
| `GET /api/v1/accounts/:id/statuses`（`limit` 既定 20・**最大 40**・`exclude_reblogs`） | 同上（2026-09-13 に読解） | **L2** |
| `GET /api/v2/instance` の `configuration.statuses.max_characters` | docs.joinmastodon.org/methods/instance/ | **L2** |
| rate limit ヘッダ `X-RateLimit-Limit`・`-Remaining`・`-Reset`（既定 300/5 分） | docs.joinmastodon.org/api/rate-limits/ | **L2** |
| 公開 API に views（表示回数）は無い | 上の一覧に無い、という**不在の証拠**なので | **L3** |
| `content` の HTML からタグを落とせば「人が読む本文」になる | 下の `strip_html()` の限界を参照 | **L3** |

**Threads との違いは、ここ（アダプタ）に閉じる**（設計 v1 §3.4）。core は
「渡したものが 1 回投げられた／投げられなかった」しか知らない。
"""
from __future__ import annotations

import datetime
import collections
import hashlib
import html.parser
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from .. import httpsafe
from .. import jst
from .. import tags as tags_mod
from .. import redact as redact_mod
from . import base

DEFAULT_INSTANCE = "https://mastodon.social"
# `GET /api/v1/accounts/:id/statuses` の `limit` の上限（**L2**: 既定 20・最大 40）。
RECENT_POSTS_MAX = 40
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_VISIBILITY = "public"
# 設計 v2 §4.2 の `MEDIA_LIMITS` の mastodon（インスタンスで違うので**既定**でしかない）。
DEFAULT_CHAR_LIMIT = 500
# **読み書きとも、ここだけを通す**（監査 P2-2・2026-09-16）。README.en の
# 「Never reads direct messages. It only ever touches public posts and public
# replies」をコードが保証していなかった——`recent_posts()`・`conversation()` は
# `visibility` を見ずに本文を返し、出す側も `private`・`direct` を許していた。
# `private`（フォロワー限定）と `direct`（DM）は**どちらも THTH の対象外**:
# 読み取りは fail-closed（`visibility` が無い行も落とす）、書き込みは明示的に断る。
READABLE_VISIBILITIES = ("public", "unlisted")
# 出す側で名指しに断る対象（`__init__` のエラー文の出し分けに使う）。
NON_PUBLIC_VISIBILITIES = ("private", "direct")

# この媒体が**持ちうる**指標（`insights()` の `available`・設計 v2 §4.2）。
# **views は無い**（**L3**——Mastodon の公開 API の一覧に表示回数が載っていない
# ことの裏返しで、実測ではない）。Bluesky・Threads と同じく**定数**にする:
# `available` は「この媒体が答えられる指標」であって「今回取れた指標」ではない。
# 取れたかどうかは `metrics` に鍵が在るかで言う（**0 と混ぜない**）。
AVAILABLE_METRICS = ("likes", "replies", "reposts")

MEDIUM = "mastodon"

# 台帳の項目名（設計 v2 §4.2「台帳と登録」）。**アダプタの中に閉じる**——core は
# `media` の名前すら見ない。
INSTANCE_ENV = "THTH_MASTODON_INSTANCE"

# T0（境界）が `base.AdapterError`（`RuntimeError` の子）を入れた。**まだ入って
# いない木でも動くように**ここで解決する（新旧どちらでも `except RuntimeError` の
# 網に入る）。T0 が main に入り切ったら `base.AdapterError` に直接書き換えてよい。
class AdapterError(base.AdapterError):
    def __init__(self, message, *, failure=None, http_status=None):
        super().__init__(message)
        self.failure = failure
        self.http_status = http_status



# --------------------------------------------------------------------------
# HTML → 本文
# --------------------------------------------------------------------------

class _TextExtractor(html.parser.HTMLParser):
    """`content`（HTML）から人が読む本文だけを取り出す。

    **限界（L3）**——これは「タグを落とす」であって「Mastodon の画面と同じ文字列を
    作る」ではない:

    - リンクは Mastodon が `<span class="invisible">https://</span>
      <span class="ellipsis">example.com/lo</span><span class="invisible">ng</span>`
      のように分けて出す。ここは**全部の span の文字を連結する**ので URL は完全な形で
      戻るが、**画面では省略記号つきで短く見えている**。見た目とは一致しない。
    - カスタム絵文字は `:shortcode:` のまま残る（画像に置き換えない）。
    - `<p>` を空行、`<br>` を改行に写すだけなので、リストや引用の見た目は落ちる。

    **落とすのは印だけで、文字は落とさない**（`&amp;` 等の実体参照は
    `convert_charrefs=True`（既定）が文字に戻す）。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "br":
            self._parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag == "br":
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("p", "div", "blockquote", "li"):
            self._parts.append("\n\n")

    def handle_data(self, data):
        self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def strip_html(content: str | None) -> str:
    """`content` の HTML を落として本文にする（限界は `_TextExtractor` の説明）。"""
    if not content:
        return ""
    parser = _TextExtractor()
    parser.feed(content)
    parser.close()
    text = parser.text()
    # 空行が 3 つ以上続くのは `</p></div>` のような入れ子の副産物なので 1 つにまとめる。
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# --------------------------------------------------------------------------
# 時刻
# --------------------------------------------------------------------------

def _parse_iso(value) -> datetime.datetime | None:
    """`created_at`（`2026-09-13T01:02:03.000Z`）を aware datetime にする。

    **読めなければ None を返す**（例外にしない）。`since` の絞り込みは
    「読めたものだけ落とす」——**読めない時刻を理由に返信を捨てない**。
    """
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


# --------------------------------------------------------------------------
# 文字数の上限（台帳の `char_limit` が無いときの既定に使う）
# --------------------------------------------------------------------------

def char_limit(instance: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> int:
    """`GET /api/v2/instance` の `configuration.statuses.max_characters`（**L2**）。

    **取れなければ例外を上げる**（`DEFAULT_CHAR_LIMIT` に黙って落とさない）。
    「インスタンスに聞いた 500」と「聞けなかったので既定の 500」は違う——
    上限 5000 のインスタンスで既定の 500 に丸めると、書けるはずの原稿が弾かれる。
    落とす先を決めるのは呼び出し側（台帳の `char_limit` → この関数 →
    `DEFAULT_CHAR_LIMIT` の順）。認可は要らない（OAuth: Public・**L2**）。
    """
    url = _instance_url(instance) + "/api/v2/instance"
    req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with httpsafe.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read() or b"{}")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise AdapterError(
            f"インスタンスの上限を読めません（{_instance_url(instance)}）: "
            f"{redact_mod.redact(str(e))}") from None
    if not isinstance(body, dict):
        raise AdapterError("インスタンスの上限を読めません: 応答が object ではありません")
    configuration = body.get("configuration")
    statuses = configuration.get("statuses") if isinstance(configuration, dict) else None
    value = statuses.get("max_characters") if isinstance(statuses, dict) else None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise AdapterError(
            "インスタンスの上限を読めません: "
            "configuration.statuses.max_characters がありません")
    return value


# クラスの中からはこちらを呼ぶ（`MastodonAdapter.char_limit` という**メソッド名と
# 綴りが同じ**なので、読む人が「どちらを呼んでいるか」を目で確かめられるように別名を置く。
# Python の名前解決ではメソッド名はクラスの外から見えないので動きは同じだが、
# **動くことと読めることは違う**）。
_read_char_limit = char_limit


def _instance_url(instance: str) -> str:
    """`instance` を `https://host` の形にそろえる。**scheme を勝手に補わない。**

    `mastodon.social` とだけ書かれた台帳を黙って https に直すと、**書いた人が
    意図した先と違うところに投げる**ことがありうる。名指しで断る（作法 5）。
    """
    return httpsafe.validated_url(instance,base=True)



class MastodonAdapter(base.Adapter):
    """Mastodon（ActivityPub 実装の 1 つ）。**インスタンスごとに別サーバ。**

    `instance` が base URL そのもの（`https://mastodon.social`）。認可は
    `Authorization: Bearer <access_token>` の**ヘッダだけ**——URL にもフォームにも
    トークンを載せない（載せると `urlopen` の例外文とアクセスログに残る）。
    """

    # 設計 v2 §4.2 の部分集合（`base.KNOWN_CAPABILITIES` の語だけを使う）。
    # `account_insights`（アカウント単位の日次）は無い——`collect` はここを見て
    # 呼ばずに済ませる（T0 の残件・2026-09-13）。`recent_posts` は在る
    # （`GET /api/v1/accounts/:id/statuses`・F2・2026-09-13）。
    # `thread_read`（T1-1）: `fetch_post()` が `GET /api/v1/statuses/:id` で
    # 根を 1 件引ける（C-1 の規律で public・unlisted 以外は断る）。
    # `keyword_search`（T2-1・設計「自分の泉」§2.3・§3）: `GET /api/v2/search`
    # で語による投稿の検索ができる（審査の壁が無い媒体。全文検索の可否は
    # インスタンス次第——`keyword_search()` の docstring・呼ぶ側の provenance 参照）。
    CAPABILITIES: frozenset = frozenset({"recent_posts", "thread_read", "keyword_search", "mentions"})

    # `.token` の鍵（`thth token set <account>` が書く形・設計 v2 §4.2）。
    TOKEN_KEYS = ("access_token",)
    POST_ID_FORM_HINT = "Mastodon の post_id は数字の id です。"
    KEYWORD_SEARCH_NOTE = (
        "Mastodon の投稿の全文検索はインスタンスの検索設定に依存します。"
        "0 件でも、該当投稿が無いとは限りません。")
    # access token に期限は無い（`.token` に `expires_in` を書かず
    # `no_expiry: true` を立てる。`maintain` が「期限を持たない」と言い分ける）。
    TOKEN_NO_EXPIRY = True

    prepared_media_supported = True

    def __init__(self, *, instance: str = DEFAULT_INSTANCE, access_token: str = "",
                 visibility: str = DEFAULT_VISIBILITY, account_id: str = "",
                 timeout: float = DEFAULT_TIMEOUT_SECONDS):
        self.instance = _instance_url(instance)
        self.access_token = access_token
        self.granted_scopes = None
        self.auth_account = "<account>"
        # adapter の局所 `_scrub()` を抜けた例外・ログでも値を消せるよう、秘密を
        # 得た時点で共通登録簿へ入れる（監査 D12・2026-09-17）。
        redact_mod.register_secret(access_token)
        # `.token` の `user_id`（`thth token set` が `verify_credentials` の `id` を
        # 書く）。無ければ `recent_posts()` がその場で `whoami()` を 1 回叩く。
        self.account_id = str(account_id or "")
        if visibility in NON_PUBLIC_VISIBILITIES:
            # **出す側でも名指しに断る**（監査 P2-2）。README.en の「THTH は
            # 公開の投稿しか出さない」約束を、init の時点で守る——`publish()` の
            # 直前まで持ち越すと、`private`・`direct` の台帳がそのまま投げられる。
            raise ValueError(
                f"THTH は公開の投稿しか出しません（visibility: {visibility}）")
        if visibility not in READABLE_VISIBILITIES:
            raise ValueError(
                f"visibility が未知です: {visibility!r}（使えるのは "
                f"{'・'.join(READABLE_VISIBILITIES)}）")
        self.visibility = visibility
        self.timeout = timeout
        # 直近の応答の rate limit ヘッダ（**L2**）。`quota()` は None を返す
        # （`capabilities()` に "quota" が無いので core は呼ばない）が、
        # `probe()` の detail に出せるように覚えておく。
        self.last_rate_limit: dict | None = None

    @classmethod
    def from_account(cls, account_cfg: dict, token: dict):
        """台帳と `.token` から組み立てる（`make_adapter()` が呼ぶ・設計 v2 §4.2）。

        読むのは台帳の `instance`（必須）と `visibility`（省略可）、`.token` の
        `access_token`（Threads と同じ鍵名）。**`instance` が無ければ名指しで断る**
        ——既定の `mastodon.social` に黙って落とすと、**台帳を書き忘れた人が
        知らないサーバに投げる**。
        """
        cfg = account_cfg or {}
        instance = os.environ.get(INSTANCE_ENV) or cfg.get("instance")
        if not instance:
            raise ValueError(
                f"台帳に instance がありません（例: \"instance\": \"{DEFAULT_INSTANCE}\"）"
                f"——Mastodon はインスタンスごとに別のサーバなので、既定では決められません")
        from .. import leave_gate
        adapter = cls(
            instance=instance,
            access_token=(token or {}).get("access_token", ""),
            visibility=cfg.get("visibility") or DEFAULT_VISIBILITY,
            account_id=(token or {}).get("user_id") or cfg.get("user_id") or "",
        )
        recorded=(token or {}).get('scopes')
        if ((token or {}).get('scopes_source')=='response' and type(recorded) is list
                and all(type(value) is str for value in recorded)):
            adapter.granted_scopes=frozenset(recorded)
        adapter.auth_account=account_cfg.get('account') or '<account>'
        return leave_gate.bind(adapter, account_cfg)

    # ----- 秘密 ------------------------------------------------------------

    def _scrub(self, text: str) -> str:
        """例外文・エラー欄に出す前に必ず通す。

        `redact.redact()` は `access_token=`・`Authorization:` の**綴りがある**
        ときに効く。ここではトークンを**ヘッダにしか載せない**ので、その綴りが
        無いまま値だけが例外文に紛れる筋を潰しておく（値そのものを先に消す）。
        """
        out = str(text)
        if self.access_token:
            out = out.replace(self.access_token, "***")
        return redact_mod.redact(out) or ""

    # ----- HTTP ------------------------------------------------------------

    def _headers(self, extra: dict | None = None) -> dict:
        headers = {"Accept": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        if extra:
            headers.update(extra)
        return headers

    def _remember_rate_limit(self, resp) -> None:
        try:
            headers = resp.headers
        except AttributeError:
            return
        seen = {k: headers.get(k) for k in
                ("X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset")
                if headers.get(k) is not None}
        if seen:
            self.last_rate_limit = seen

    def _request(self, method: str, path: str, *, data: dict | None = None,
                 headers: dict | None = None):
        """1 回だけ投げて JSON を返す。**urllib の例外はそのまま上げる**
        （4xx と 5xx を publish が区別するため。判定はここではなく publish に置く）。"""
        url = f"{self.instance}{path}"
        body = urllib.parse.urlencode(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(url, data=body, method=method,
                                     headers=self._headers(headers))
        with httpsafe.urlopen(req, timeout=self.timeout) as resp:
            self._remember_rate_limit(resp)
            raw = resp.read()
        return json.loads(raw) if raw else {}

    def _http_error(self, path, what, error):
        detail = f"{what}: HTTP {error.code} {self._scrub(error.reason)}"
        if error.code == 403:
            detail += '（permission: アクセスが拒否されました）'
            permission = ('read:search' if path.startswith('/api/v2/search') else
                          'read:notifications' if path.startswith('/api/v1/notifications') else None)
            if permission:
                detail += (f'。token の {permission} を確認してください。scope 不足のほか、'
                           'アカウント制限等でも拒否されます。アプリ設定で必要なら追加し '
                           'thth token set <account> --by … で再設定してください')
        return AdapterError(detail, failure='permission' if error.code == 403 else None,
                            http_status=error.code)

    def _get_json(self, path: str, what: str) -> dict:
        """GET して object を返す。**取れなかったら失敗として上げる**（作法 5）。

        `{}` を返して「取れて 0 件」に見せない——採取側は「例外なら記録を書かない」で
        成功と失敗を分けている（`thth/collect.py`・Threads と同じ流儀）。
        """
        try:
            body = self._request("GET", path)
        except urllib.error.HTTPError as e:
            raise self._http_error(path, what, e) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            raise AdapterError(f"{what}: {self._scrub(e)}") from None
        if not isinstance(body, dict):
            raise AdapterError(
                f"{what}: 応答が object ではありません（{type(body).__name__}）")
        if body.get("error"):
            raise AdapterError(f"{what}: {self._scrub(body['error'])[:200]}")
        return body

    def _get_list(self, path: str, what: str) -> list:
        """GET して**配列**を返す（`_get_json` の配列版）。

        Mastodon の一覧の口（`/statuses` 等）は object ではなく配列を返す。
        `[]` を「取れて 0 件」と読んでよいのは**配列が返ったとき**だけで、
        形が違えば失敗（`_get_json` と同じ規律・`threads.py::_rows` と同じ理由）。
        """
        try:
            body = self._request("GET", path)
        except urllib.error.HTTPError as e:
            raise self._http_error(path, what, e) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            raise AdapterError(f"{what}: {self._scrub(e)}") from None
        if isinstance(body, dict) and body.get("error"):
            raise AdapterError(f"{what}: {self._scrub(body['error'])[:200]}")
        if not isinstance(body, list):
            raise AdapterError(
                f"{what}: 応答が配列ではありません（{type(body).__name__}）。"
                f"**取れて 0 件とは区別できません**")
        return body

    # ----- 投稿 ------------------------------------------------------------

    def _idempotency_key(self, post: base.Post, visibility: str) -> str:
        """同じ本文・同じ返信先・同じ公開範囲なら**同じ鍵**（**L2**: サーバは 1 時間覚える）。

        THTH 側の二重投稿は inflight が防ぐ（設計 v1 §3.5）。**それでも防げないのが
        「公開に成功したが応答が失われた」**——`publish_ambiguous` で止めて人が
        `thth resolve` するまでのあいだに、誰かが同じ原稿をもう一度投げる筋が残る。
        この鍵があれば、1 時間以内の再送は**サーバ側で**同じ 1 本になる。
        鍵にトークンは混ぜない（鍵はヘッダとしてサーバのログに残る）。
        """
        material = "\x1f".join([
            "mastodon", self.instance, visibility,
            post.reply_to or "",
            tags_mod.prepared(MEDIUM, post.text, post.topic,
                              hashtags=post.hashtags_allowed) or "",
        ])
        if post.media_manifest:
            from .. import media
            material += "\x1fmedia:" + media.prepared_component(post.media_manifest)
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def publish(self, post: base.Post, *, dry_run: bool,
                on_container_created=None,
                before_publish=None) -> base.PublishResult:
        """`POST /api/v1/statuses` を 1 回（**L2**）。

        Mastodon には Threads の container に当たる二段階が無いので、
        **`on_container_created` は呼ばない**（container 段の失敗も存在しない）。
        `before_publish` は**送信要求の直前**——ここまで来て断られたら
        `publish_vetoed`（＝**出ていない**）。

        三分類は Threads と同じ規則（設計 v1 §3.5 の表）:
        4xx →`publish_definite`（出ていない）／timeout・接続断・5xx・200 だが id 無し
        →`publish_ambiguous`（分からない）。`post.topic` は Mastodon には無いので
        **黙って無視する**（1 つの queue を複数媒体が拾う形を壊さないため・設計 v2 §4.2）。
        """
        ts = jst.iso()
        if dry_run:
            return base.PublishResult(post_id=None, url=None, ts=ts, error=None,
                                      failure="none")

        if post.media_manifest:
            import dataclasses
            from . import mastodon_media
            prepared = dataclasses.replace(post, text=tags_mod.prepared(
                MEDIUM, post.text, post.topic, hashtags=post.hashtags_allowed))
            return mastodon_media.publish(self, prepared, before_publish=before_publish)

        visibility = self.visibility
        text = tags_mod.prepared(MEDIUM, post.text, post.topic,
                                 hashtags=post.hashtags_allowed)
        params = {"status": text, "visibility": visibility}
        if post.reply_to:
            params["in_reply_to_id"] = post.reply_to

        if before_publish is not None:
            veto = before_publish()
            if veto:
                return base.PublishResult(None, None, ts, error=str(veto),
                                          failure="publish_vetoed")

        headers = {
            "Idempotency-Key": self._idempotency_key(post, visibility),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        try:
            body = self._request("POST", "/api/v1/statuses", data=params, headers=headers)
        except urllib.error.HTTPError as e:
            failure = "publish_definite" if 400 <= e.code < 500 else "publish_ambiguous"
            return base.PublishResult(
                None, None, ts,
                error=self._scrub(f"公開失敗: {e.code} {e.reason}"), failure=failure)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return base.PublishResult(None, None, ts,
                                      error=self._scrub(f"公開失敗: {e}"),
                                      failure="publish_ambiguous")
        except ValueError as e:
            # 200 だが JSON として読めない＝**成立したかどうか分からない**。
            return base.PublishResult(None, None, ts,
                                      error=self._scrub(f"公開失敗: 応答が読めません: {e}"),
                                      failure="publish_ambiguous")

        post_id = body.get("id") if isinstance(body, dict) else None
        if not post_id:
            return base.PublishResult(None, None, ts, error="公開失敗: id無し",
                                      failure="publish_ambiguous")
        url = body.get("url") if isinstance(body, dict) else None
        return base.PublishResult(post_id=str(post_id), url=url, ts=ts, error=None,
                                  failure="none")

    # ----- 会話 ------------------------------------------------------------

    def _message(self, status: dict, root_post: str) -> dict:
        """1 件の Status を共通の `Message`（設計 v2 §4.2）に写す。"""
        message_id = status.get("id")
        if not message_id:
            raise AdapterError("会話: 返信に id がありません（**件数として数えません**）")
        account = status.get("account")
        account = account if isinstance(account, dict) else {}
        acct = account.get("acct")
        return {
            "message_id": str(message_id),
            "username": acct,
            "text": strip_html(status.get("content")),
            "timestamp": status.get("created_at"),
            "replied_to": status.get("in_reply_to_id"),
            # **根は引数の post_id**。Mastodon の Status は根を持たない
            # （`in_reply_to_id` で親を辿るだけ）ので、`/context` を引いた投稿を根とする。
            "root_post": root_post,
            "medium": MEDIUM,
            # 非可逆（誰かは戻せない・偏りは数えられる）。acct が無ければ None——
            # **「分からない」を空文字で埋めない。**
            "author_key": self.author_key(acct),
            # SNS に会話窓は無い（**WhatsApp の芽**・設計 v2 §4.2）。
            "reply_deadline": None,
        }

    def qualified_acct(self, acct) -> str | None:
        """`acct` を**必ずドメイン付き**にし、**綴りの揺れをそろえる**。

        Mastodon の `acct` は**自分のインスタンスの利用者だけドメインが落ちる**
        （よそから来た人は `name@other.example` のまま）。落ちたまま鍵にすると、
        `mastodon.social` の `aoking` と `fedibird.com` の `aoking` が
        **同一人物として数えられる**。**別インスタンスの同名を同じ人にしない。**

        **逆側もある**（独立監査 1・P3-7・2026-09-13）: 同じ人が別人に割れる。
        鍵は `sha256` なので 1 文字違えば別の鍵で、揺れの元が 2 つあった。

        - **台帳の `instance` の綴り**——`https://Mastodon.Social`・
          `https://mastodon.social:443`・末尾 `/`。どれも同じサーバを指すのに、
          `netloc` をそのまま使うと 4 通りの鍵ができる。台帳を書き直した日を
          境に、**同じ人の観測が別人として積まれる**。
        - **`acct` の大小**——Mastodon の利用者名は大小を区別しない（**L3**:
          `Aoking` でログインしても `aoking` に解決される）が、API の応答は
          その時々の綴りを返す。

        そろえ方は host を小文字化して既定 port（`https:443` / `http:80`）を
        落とし、acct 全体を `casefold()`。**戻せない鍵なので、後から直せない**
        ——集める前にそろえておくしかない。
        """
        acct = (acct or "").strip().lstrip("@")
        if not acct:
            return None
        if "@" not in acct:
            acct = f"{acct}@{self._instance_host()}"
        return acct.casefold()

    def _instance_host(self) -> str:
        """台帳の `instance` から、鍵に使う host（小文字・既定 port を落とす）。"""
        parts = urllib.parse.urlsplit(self.instance)
        netloc = parts.netloc or self.instance
        host = (parts.hostname or netloc).lower()
        port = None
        try:
            port = parts.port
        except ValueError:      # port が数字でない台帳（`_instance_url` は通す）
            port = None
        既定 = {"https": 443, "http": 80}.get((parts.scheme or "").lower())
        if port is not None and port != 既定:
            host = f"{host}:{port}"
        return host

    def author_key(self, acct) -> str | None:
        """投稿者の非可逆な識別子（設計 v2 §4.2 の `Message.author_key`）。

        **式は境界のもの**（`base.author_key(medium, identity)`・T3 の配線
        2026-09-13）。以前はここが `sha256("mastodon:" + instance + ":" + id)` を
        自前で作っていた——Threads・Bluesky と**同じ意味の欄に別の式**が入って
        いたので、泉に出たあとで突き合わせる根拠が実装の履歴に依存していた。

        身元は **ドメイン付きの `acct`**。数字の `account.id` を使わないのは、
        **id がインスタンスの中でしか意味を持たない**（引っ越すと変わる・
        よそのインスタンスの人はそのインスタンスの id を持たない）ため。
        """
        return base.author_key(MEDIUM, self.qualified_acct(acct))

    def conversation(self, post_id: str, *, since: str | None = None) -> list:
        """`GET /api/v1/statuses/:id/context` の `descendants`（**全階層**・**L2**）。

        `ancestors`（親側）は使わない——`post_id` は自分が出した根なので、その上は無い。
        `since` を渡すと、**時刻が読めたもののうち** `since` より古いものを落とす。

        **上限がある（L2・一次資料の原文）**: token 無しなら descendants 60 件・
        深さ 20 まで、**token 付きで 4,096 件・深さ無制限**。ここは必ず Bearer を
        送るので後者。**`/context` に頁分割は無い**ので、4,096 件を超える会話は
        **黙って切られる**（Threads の `/conversation` は `paging.next` を辿れたが、
        ここには辿る先が無い）。4,096 件の会話は現実にはまず無いが、**「全部取れた」
        と言い切れるのは上限までだ**と、ここに書いておく。
        """
        body = self._get_json(f"/api/v1/statuses/{urllib.parse.quote(str(post_id))}/context",
                              "会話")
        if "descendants" not in body:
            # **「取れて 0 件」と区別できない**ので失敗として扱う（Threads と同じ・
            # `threads.py::_rows` の理由をそのまま）。
            raise AdapterError("会話: 応答に descendants がありません")
        rows = body["descendants"]
        if rows is None:
            raise AdapterError("会話: descendants が null です（0 件とは違います）")
        if not isinstance(rows, list):
            raise AdapterError(
                f"会話: descendants が配列ではありません（{type(rows).__name__}）。"
                f"**件数として数えません**")
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                raise AdapterError(
                    f"会話: descendants の {i} 番目が object ではありません"
                    f"（{type(row).__name__}）。**件数として数えません**")

        # **公開でない status は読み取りから落とす**（監査 P2-2・fail-closed）。
        # `visibility` が読める行のうち public・unlisted だけを `_message()` に渡す
        # ——`private`（フォロワー限定）も `direct`（DM）も、**無い場合も含めて**
        # 落とす。落とした件数は返り値に混ぜない（本文はもちろんログにも出さない）。
        readable_rows = [row for row in rows if row.get("visibility") in READABLE_VISIBILITIES]
        messages = [self._message(row, str(post_id)) for row in readable_rows]
        floor = _parse_iso(since) if since else None
        if floor is None:
            return messages
        out = []
        for m in messages:
            at = _parse_iso(m.get("timestamp"))
            if at is None or at >= floor:
                out.append(m)     # **読めない時刻は落とさない**
        return out

    def fetch_post(self, post_id: str) -> dict:
        """`GET /api/v1/statuses/:id` で根を 1 件引く（T1-1・**L2**・`insights()` と同じ口）。

        **C-1 の規律**: `visibility` が `public`・`unlisted` 以外（無い場合も）
        なら `AdapterError`（本文を返さない・`conversation()`・`recent_posts()`
        と同じ fail-closed）。`replied_to` は常に `None`・`root_post` は
        自分自身の id にする（この口が返す行は`thread_read` が組む枝の根）。
        """
        if not isinstance(post_id, str) or not post_id.strip():
            raise AdapterError("post_id が空です")
        status = self._get_json(
            f"/api/v1/statuses/{urllib.parse.quote(post_id.strip())}", "投稿の取得")
        if status.get("visibility") not in READABLE_VISIBILITIES:
            raise AdapterError("公開の投稿ではありません")
        message_id = str(status.get("id")) if status.get("id") is not None else None
        out = self._message(status, message_id)
        out["replied_to"] = None
        url = status.get("url")
        if url:
            out["permalink"] = url
        return out

    # --- 語で検索（T2-1・設計「自分の泉」§2.3・§3） -------------------------
    # `GET /api/v2/search` の param（**L2**: docs.joinmastodon.org/methods/search/
    # 2026-09-16 に WebFetch で読解）: `q`（必須）・`type`（`accounts`／`hashtags`／
    # `statuses`）・`limit`（既定 20・**最大 40**・カテゴリごと）・
    # `resolve`・`following`・`account_id`・`exclude_unreviewed`・`max_id`・
    # `min_id`・`offset`。出力は `{"accounts": [...], "statuses": [...],
    # "hashtags": [...]}`——ここでは `statuses` だけを使う。
    #
    # **全文検索の限界（一次資料の原文・L2）**: 「the availability of results
    # depends on the specific backend search configuration of the server
    # being queried. By default, accounts and hashtags are always
    # searchable, while statuses depend on an ElasticSearch backend being
    # present and the API request being authenticated (full text search is
    # not available to unauthenticated users)」。**ElasticSearch が無いイン
    # スタンスでは検索対象が絞られる**——一次資料はその絞り方（自分の投稿・
    # 自分が触れた投稿だけ、等）までは明記していない（**L3**・慣行）。
    # **応答の件数だけからは「全文検索が効いているか」を区別できない**——
    # 0 件も少数件も両方の場合に起こりうる。呼ぶ側（`thth where`）は
    # provenance に必ずこの限界を書くこと（**ここで黙って隠さない**）。
    #
    # **並び順の指定は無い**（一次資料の param 一覧に sort 系が無い）。
    # `search_type` の `RECENT` を受け取っても**無視する**——嘘の並びを
    # 返すよりは、呼ぶ側に「無視した」と言わせる（設計「自分の泉」§5 規約 6）。
    SEARCH_MAX_LIMIT = 40

    def _search_row(self, status: dict) -> dict:
        """検索結果の 1 件を `Message` の形に写す（T2-1）。`_message()` と違い
        **根を持たない**（検索結果はどの枝の中かを返さない）ので `root_post`
        は `None`。`replied_to` は応答にあれば残す（`_message()` と同じ拾い方）。
        """
        message_id = status.get("id")
        if not message_id:
            raise AdapterError("検索: 投稿に id がありません（**件数として数えません**）")
        account = status.get("account") if isinstance(status.get("account"), dict) else {}
        acct = account.get("acct")
        out = {
            "message_id": str(message_id),
            "username": acct,
            "text": strip_html(status.get("content")),
            "timestamp": status.get("created_at"),
            "replied_to": status.get("in_reply_to_id"),
            "root_post": None,
            "medium": MEDIUM,
            "author_key": self.author_key(acct),
            "reply_deadline": None,
        }
        url = status.get("url")
        if url:
            out["permalink"] = url
        # `replies_count`（**L2**）。**数と真偽を分ける**（`threads_read_cli.
        # REPLY_COUNT_KEYS`・設計 v2 §4.4）——無ければどちらも入れない。
        replies_count = status.get("replies_count")
        if isinstance(replies_count, int) and not isinstance(replies_count, bool):
            out["replies_count"] = replies_count
            out["has_replies"] = replies_count > 0
        return out

    def keyword_search(self, q: str, *, search_type: str = "TOP",
                       limit: int = 25) -> list:
        """`GET /api/v2/search?q=&type=statuses&limit=`（**L2**・上の表・T2-1）。

        **全文検索の限界・`search_type` の扱いはクラス docstring のとおり**
        ——ここではその規約を実行するだけ。`visibility` は C-1 の規律
        （`conversation()`・`fetch_post()` と同じ fail-closed）: `public`・
        `unlisted` 以外（無い場合も含む）は落とす。
        """
        if not isinstance(q, str) or not q.strip():
            raise AdapterError("検索の語が空です")
        if search_type not in ("TOP", "RECENT"):
            raise AdapterError(
                f"search_type は TOP / RECENT のどちらかです（{search_type!r}）")
        if (not isinstance(limit, int) or isinstance(limit, bool)
                or limit < 1 or limit > self.SEARCH_MAX_LIMIT):
            raise AdapterError(f"limit は 1〜{self.SEARCH_MAX_LIMIT} です（{limit!r}）")
        params = urllib.parse.urlencode({"q": q.strip(), "type": "statuses",
                                         "limit": limit})
        body = self._get_json(f"/api/v2/search?{params}", "投稿の検索")
        statuses = body.get("statuses")
        if not isinstance(statuses, list):
            raise AdapterError(
                "検索: 応答に statuses の配列がありません"
                f"（{type(statuses).__name__}）。取れて 0 件とは区別できません")
        readable = [s for s in statuses if isinstance(s, dict)
                   and s.get("visibility") in READABLE_VISIBILITIES]
        return [self._search_row(row) for row in readable]

    @staticmethod
    def _tag_history(tag: dict) -> list[dict]:
        rows = tag.get("history") if isinstance(tag, dict) else None
        out = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            try:
                uses, accounts = int(row["uses"]), int(row["accounts"])
            except (KeyError, TypeError, ValueError):
                continue
            if uses >= 0 and accounts >= 0:
                out.append({"day": row.get("day"), "uses": uses, "accounts": accounts})
        return out

    def tag_observation(self, tag: str, *, limit: int = 40,
                        since: str | None = None) -> dict:
        """One instance's public tag timeline, search candidates and trends."""
        if not isinstance(tag, str) or not tag or tag.startswith("#") or "/" in tag:
            raise AdapterError("tag は # と / を含まない非空文字列です")
        if type(limit) is not int or not 1 <= limit <= 40:
            raise AdapterError("limit は 1〜40 です")
        timeline_path = ("/api/v1/timelines/tag/" + urllib.parse.quote(tag, safe="")
                         + "?" + urllib.parse.urlencode({"limit": limit}))
        statuses = self._get_list(timeline_path, "タグの公開タイムライン")
        candidates = self._get_json("/api/v2/search?" + urllib.parse.urlencode(
            {"q": tag, "type": "hashtags", "limit": 40}), "タグ候補")
        candidate_rows = candidates.get("hashtags")
        if not isinstance(candidate_rows, list):
            raise AdapterError("タグ候補: hashtags の配列がありません")
        trends = self._get_list("/api/v1/trends/tags?limit=20", "トレンドタグ")
        trend_rows = [row for row in trends if isinstance(row, dict)]
        authors, co_tags = set(), collections.Counter()
        latest_at = None
        latest_dt = None
        n = 0
        for status in statuses:
            if not isinstance(status, dict) or status.get("visibility") != "public":
                continue
            stamp = status.get("created_at")
            parsed = _parse_iso(stamp)
            if since:
                since_dt = _parse_iso(since)
                if since_dt is None:
                    raise AdapterError("since は ISO 時刻にしてください")
                if parsed is None or parsed < since_dt:
                    continue
            n += 1
            if parsed is not None and (latest_dt is None or parsed > latest_dt):
                latest_dt, latest_at = parsed, stamp
            account = status.get("account") if isinstance(status.get("account"), dict) else {}
            author = account.get("id") or account.get("acct")
            if author:
                authors.add(str(author))
            for item in status.get("tags", []) if isinstance(status.get("tags"), list) else []:
                name = item.get("name") if isinstance(item, dict) else None
                if isinstance(name, str) and name and name.casefold() != tag.casefold():
                    co_tags[name] += 1
        candidate_summary = [{"tag": row.get("name"), "history": self._tag_history(row)}
                             for row in candidate_rows if isinstance(row, dict)
                             and isinstance(row.get("name"), str)][:5]
        selected = next((row for row in trend_rows + candidate_rows
                         if isinstance(row, dict) and isinstance(row.get("name"), str)
                         and row["name"].casefold() == tag.casefold()), None)
        return {"tag": tag, "n": n, "distinct_authors": len(authors),
                "latest_at": latest_at,
                "co_tags": [{"tag": name, "n": count} for name, count in
                            sorted(co_tags.items(), key=lambda item: (-item[1], item[0]))[:5]],
                "observed_from": self.instance, "window": {"since": since, "limit": limit,
                                                             "scope": "instance_public_timeline"},
                "tagged_n": n, "tagged_share": 1 if n else None,
                "history": self._tag_history(selected) if selected else None,
                "history_scope": "instance_view", "candidates": candidate_summary,
                "trending_tags": [{"tag": row.get("name"), "history": self._tag_history(row)}
                                  for row in trend_rows if isinstance(row.get("name"), str)]}

    def observed_tags(self, post_id: str) -> list[str]:
        """Tags actually returned on the status at collection time."""
        status = self._get_json("/api/v1/statuses/" + urllib.parse.quote(str(post_id), safe=""),
                                "投稿のタグ")
        if status.get("visibility") not in READABLE_VISIBILITIES:
            raise AdapterError("投稿のタグ: 公開範囲を確認できません")
        raw = status.get("tags")
        if not isinstance(raw, list):
            raise AdapterError("投稿のタグ: tags の配列がありません")
        return sorted({row["name"] for row in raw if isinstance(row, dict)
                       and isinstance(row.get("name"), str) and row["name"]})

    def mentions(self, *, since=None) -> list:
        """Notification ids paginate; status ids identify the displayed posts."""
        from ..read_window import cutoff
        try:
            floor = cutoff(since)
        except ValueError as exc:
            raise AdapterError(str(exc)) from None
        out, seen, cursors = [], set(), set()
        cursor = None
        for _ in range(100):
            params = {'types[]': 'mention', 'limit': 80}
            if cursor:
                params['max_id'] = cursor
            rows = self._get_list('/api/v1/notifications?' + urllib.parse.urlencode(params), '言及')
            if not rows:
                return out
            for notification in rows:
                if not isinstance(notification, dict) or notification.get('type') != 'mention':
                    continue
                status = notification.get('status')
                if not isinstance(status, dict) or not status.get('id') or status.get('visibility') not in READABLE_VISIBILITIES:
                    continue
                if str(status['id']) in seen:
                    continue
                stamp = notification.get('created_at')
                at = _parse_iso(stamp)
                if floor and at and at < floor:
                    continue
                seen.add(str(status['id']))
                row = self._message(status, None)
                row.update(kind='mention', timestamp=stamp, permalink=status.get('url'))
                out.append(row)
            last = rows[-1]
            cursor = str(last.get('id') or '') if isinstance(last, dict) else ''
            if not cursor.isdigit() or cursor in cursors:
                raise AdapterError('言及: 通知のページが進みません')
            cursors.add(cursor)
        raise AdapterError('言及: 100 頁を超えたため全件を確認できません')

    def inbox(self, *, since: str | None = None) -> list:
        """利用者から始まった会話（**WhatsApp の芽**・設計 v2 §4.2）。

        Mastodon は push 型ではないので既定どおり空。`capabilities()` に "inbox" が
        無いので `collect` はここを見ない。
        """
        return []

    # ----- 実測 ------------------------------------------------------------

    # 指標の名前 → 応答の field。**名前の側は `AVAILABLE_METRICS` と同じ順・
    # 同じ顔ぶれ**（下の assert で固定する）。
    _METRICS = (("likes", "favourites_count"),
                ("replies", "replies_count"),
                ("reposts", "reblogs_count"))

    def recent_posts(self, *, limit: int = 25) -> list:
        """`GET /api/v1/accounts/:id/statuses`（**L2**——2026-09-13 に一次資料を読解）。

        `limit` は既定 20・**最大 40**。`exclude_reblogs` は既定 false なので
        **明示して落とす**——ブースト（他人の投稿の再投稿）は「自分が書いた投稿」
        ではない（`thth posts` は「THTH を通していない**自分の投稿**」を数える口）。
        `exclude_replies` は**立てない**: THTH が出した返信も `post_id` で
        突き合わせたい。

        `:id` は**数字の account id**（`acct` ではない）。`.token` の `user_id`
        （`thth token set` が `verify_credentials` の `id` を書く）を使い、無ければ
        ここで 1 回だけ `whoami()` を叩く。
        """
        account_id = self.account_id or str(self.whoami().get("user_id") or "")
        if not account_id:
            raise AdapterError("account id が判らないので直近の投稿を引けません")
        params = urllib.parse.urlencode({
            "limit": max(1, min(int(limit), RECENT_POSTS_MAX)),
            "exclude_reblogs": "true"})
        rows = self._get_list(
            f"/api/v1/accounts/{urllib.parse.quote(account_id)}/statuses?{params}",
            "直近の投稿")
        out = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            # **公開でない status は読み取りから落とす**（監査 P2-2・fail-closed・
            # `conversation()` と同じ規律）。`visibility` が無い行も落とす。
            if row.get("visibility") not in READABLE_VISIBILITIES:
                continue
            out.append({
                "post_id": str(row["id"]),
                "timestamp": row.get("created_at"),
                "url": row.get("url"),
                # `content` は HTML（`strip_html()` の限界は同関数の docstring）。
                "text": strip_html(row.get("content")),
                # **語（Threads の topic_tag）に当たるものが無い**媒体。
                "topic": None,
            })
        return out

    def insights(self, post_id: str) -> dict:
        """`GET /api/v1/statuses/:id` の 3 つ（**L2**）。**views は無い。**

        Mastodon の公開 API に表示回数は無い（**L3**——「無い」は一覧に載っていない
        ことの裏返しなので、実測ではない）。**無い指標を 0 で埋めない**——
        `views: 0` と書けば「見られなかった」に読めてしまう。`available` に
        載らないことで「この媒体は答えられない」と分かる（設計 v2 §4.2・
        `comparable_views` はこれを理由に除外する）。

        **`available` は「この媒体が持ちうる指標」**（定数・Bluesky と Threads と
        同じ）。以前はここだけ「**今回取れた指標**」を返していたので、応答から
        `favourites_count` が落ちた回に `available` からも `likes` が消え、
        **「この媒体にいいねは無い」と読めた**——「今回取れなかった」と「媒体に
        無い」を混ぜないための鍵なのに、その 2 つを混ぜていた（引継ぎ
        2026-09-15 §3-D）。取れたかどうかは `metrics` の鍵の有無で言う。
        """
        body = self._get_json(f"/api/v1/statuses/{urllib.parse.quote(str(post_id))}",
                              "投稿の数")
        metrics: dict = {}
        for name, field in self._METRICS:
            value = body.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue        # 取れなかった指標は入れない（**0 と混ぜない**）
            metrics[name] = value
        return {"metrics": metrics, "available": list(AVAILABLE_METRICS)}

    # ----- 素性・検査 ------------------------------------------------------

    def whoami(self) -> dict:
        """`GET /api/v1/accounts/verify_credentials`（**L2**）。`thth token set` の検証。"""
        body = self._get_json("/api/v1/accounts/verify_credentials", "アカウントの確認")
        user_id = body.get("id")
        if not user_id:
            raise AdapterError("アカウントの確認: 応答に id がありません")
        return {"user_id": str(user_id), "username": body.get("acct")}

    def probe(self, *, get=None) -> list:
        """読み取りだけで能力を測る（`thth doctor`）。**秘密は detail に出さない。**

        1 行は doctor がそのまま描ける形（`name`・`label`・`permission`・`key`・
        `ok`・`detail`）。`ok` は 3 値（True ○ / False × / None －）。

        `get` は取得口の差し替え（doctor が「書き込みの口を持たない」ことを自分の
        source への検査で担保しているため・T0 の `base.Adapter.probe` 参照）。
        **Mastodon 側は使わない**——ここで叩く口は GET だけで、doctor の
        `_get(base_url, path, params, token)`（`access_token` を**クエリに載せる**
        Threads 向けの形）とは認可の載せ方が違う。**トークンをクエリに移してまで
        口を共有しない。**

        **投稿・返信・削除は絶対に呼ばない**（doctor から引き継ぐ約束）。
        """
        results = []
        try:
            me = self.whoami()
        except Exception as e:      # noqa: BLE001 — probe は落ちずに × を返す口
            results.append({"name": "verify_credentials", "label": "本人の確認",
                            "permission": "read:accounts", "key": "whoami",
                            "ok": False, "detail": self._scrub(e)[:200], "body": None})
        else:
            detail = f"acct={me.get('username')} id={me.get('user_id')}"
            if self.last_rate_limit:
                detail += f"・残量 {self.last_rate_limit}"
            results.append({"name": "verify_credentials", "label": "本人の確認",
                            "permission": "read:accounts", "key": "whoami",
                            "ok": True, "detail": detail, "body": None})
        try:
            limit = _read_char_limit(self.instance, timeout=self.timeout)
        except Exception as e:      # noqa: BLE001
            results.append({"name": "instance", "label": "インスタンスの上限",
                            "permission": "（認可不要）", "key": "instance",
                            "ok": False, "detail": self._scrub(e)[:200], "body": None})
        else:
            results.append({"name": "instance", "label": "インスタンスの上限",
                            "permission": "（認可不要）", "key": "instance",
                            "ok": True, "detail": f"max_characters={limit}",
                            "body": None})
        for key, label, permission, path, is_list in (
            ('search', '投稿の検索', 'read:search', '/api/v2/search?type=statuses&q=thth&limit=1', False),
            ('notifications', '通知の取得', 'read:notifications', '/api/v1/notifications?limit=1', True),
        ):
            try:
                (self._get_list if is_list else self._get_json)(path, label)
            except Exception as exc:
                results.append(dict(name=key, key=key, label=label, permission=permission,
                                    ok=False, detail=self._scrub(exc)[:500], body=None,
                                    failure=getattr(exc, 'failure', None),
                                    http_status=getattr(exc, 'http_status', None)))
            else:
                results.append(dict(name=key, key=key, label=label, permission=permission,
                                    ok=True, detail='GET 到達（scope の推定）', body=None,
                                    failure=None, http_status=200))
        return results

    def char_limit(self) -> int:
        """このインスタンスの上限（台帳の `char_limit` が無いときの既定）。"""
        return _read_char_limit(self.instance, timeout=self.timeout)

    @classmethod
    def capabilities(cls) -> set:
        """topic 無し・views 無し・inbox 無し・quota 無し・refresh 無し（設計 v2 §4.2）。

        **実体を作らずに引ける**（classmethod・T0 の `base.Adapter` と同じ形）——
        `select` がトピック検査をするかどうかを、トークンを読まずに決められる。

        `link_preview` も入れない——Mastodon は本文中の URL を勝手にリンクにするので
        **アダプタが何かをする余地が無い**。「できる」ではなく「アダプタが担う」を数える。
        """
        return set(cls.CAPABILITIES)

    def quota(self):
        """rate limit ヘッダ（**L2**）は `last_rate_limit` に覚えるが、**枠としては返さない**。

        Threads の `threads_publishing_limit` は「今日あと何本投げられるか」だが、
        `X-RateLimit-Remaining` は「直近 5 分の API 呼び出しの残り」で**意味が違う**。
        同じ名前で返すと、core が「あと 290 本投げられる」と読み違える。
        """
        return None

    def refresh_token(self, token):
        raise NotImplementedError(
            "Mastodon の access token に期限は無い（設計 v2 §4.2「認可とトークン」）")
