"""ThreadsAdapter（graph.threads.net 相当・設計 §2.2・§3.4）。

T1 では本物の Threads API を叩かない。`base_url` を差し替え可能にしてあるので、
テストは `http.server` の偽 API にだけ向ける（`THTH_THREADS_BASE_URL` 環境変数
または直接のコンストラクタ引数で上書きする）。`replies`・`refresh_token` は
T2 以降の範囲なので実装しない（呼ばれたら NotImplementedError）。
"""
from __future__ import annotations

from .. import api_diagnostic

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from .. import httpsafe
from .. import jst
from .. import redact as redact_mod
from . import base

DEFAULT_BASE_URL = "https://graph.threads.net"
DEFAULT_WAIT_SECONDS = 30.0
DEFAULT_TIMEOUT_SECONDS = 10.0

# この媒体の名前（`Message.medium`・`author_key()`・台帳の `media`）。
MEDIUM = "threads"

# 投稿 1 本について**この媒体が持っている**指標（設計 v2 §4.2 の `available`）。
# **取れなかった指標**（`metrics` に無い）と**そもそも媒体に無い指標**
# （ここに無い）を混ぜないための一覧。
POST_METRICS = ("views", "likes", "replies", "reposts", "quotes", "shares")
# `recent_posts()` で引く field（**L2**: `GET /{user_id}/threads` の `fields`）。
# `topic_tag` は**付与の主体が未確認**（`account_report.API_TOPIC` の但し書き）。
RECENT_POST_FIELDS = "id,permalink,timestamp,text,topic_tag"

# doctor の probe が本文から表示してよい鍵だけ（本文や個人情報を垂れ流さない）。
PROBE_KEEP = ("name", "id", "username", "total_value", "quota_usage", "config",
              "reply_quota_usage", "reply_config", "values", "timestamp", "permalink")


class ProbeBodyError(base.AdapterError):
    """HTTP 200 だが本文に `error` が入っている（`_get()` と同じ考え方——
    200 でも「取れた」ことにしない）。`thth/doctor.py` の `_get()` が投げる。"""


class _Probe:
    # **`key` はラベルの表示文言とは独立**（監査 2026-09-11・掃討で検出。
    # 運用セッションが VM の `thth doctor` 出力で再現を確認）。以前は判定側が
    # `label == "自分の投稿一覧"` と文字列で突き合わせていたが、ラベルに注記を
    # 足した（2026-09-10）ときに直し忘れ、`==` が永久に偽になって「返信の取得」
    # probe が一度も HTTP を叩かなくなった。表示用のラベルは今後も変わりうるので、
    # 突き合わせには変わらない `key` を使う。
    #
    # **`unverifiable` が入っている probe は HTTP を叩かない**（2026-09-14）。
    # `threads_delete`（DELETE にしか使われない）のように**読み取りの口が無い**
    # 権限は、叩いて確かめる術が無い。それでも 11 権限が 1 行ずつ並ぶように、
    # 「読み取りでは確かめられません」という理由を持った行として出す
    # （`ok=None`）。**× にはしない**——判らないことを「駄目」と言わない。
    def __init__(self, label: str, permission: str, path: str, params: dict,
                 key: str | None = None, unverifiable: str | None = None):
        self.label = label
        self.permission = permission
        self.path = path
        self.params = params
        self.key = key
        self.unverifiable = unverifiable


# **「乗っていない」と「確かめられない」を混ぜない**（2026-09-14）。
#
# probe の失敗は 3 通りに読み分ける:
#   - 権限不足（`ok=False`）: Meta の `error.message` に `permission` が出る、
#     または Graph API の権限系 code（10・200〜299）。**この権限はトークンに
#     乗っていない**、と言ってよい。
#   - その他の 4xx・HTTP 200 の `error`（`ok=False`）: API が断った。理由は本文。
#   - 5xx・ネットワーク層（`ok=None`）: **確かめられなかった**。権限の有無に
#     ついては何も判っていないので、× にしない。
#
# code の意味は Graph API 共通の一覧に基づく（**L3**——Threads の一次資料には
# 権限不足のときの応答の形が書かれていない。`message` の `permission` を主に
# 見て、code は補助）。
_PERMISSION_ERROR_CODES = frozenset({10} | set(range(200, 300)))
NOT_GRANTED = "この権限がトークンに乗っていません"
COULD_NOT_VERIFY = "確かめられませんでした"
UNVERIFIABLE_BY_READ = "読み取りでは確かめられません"
# 読み取りの口が無い 3 権限の行の `key`。`/debug_token` が取れたときだけ、
# この 3 行を「乗っている／乗っていない」に格上げする（`probe()`）。
UNVERIFIABLE_KEYS = frozenset({"manage_replies", "delete", "share_to_instagram"})
GRANTED_BY_DEBUG_TOKEN = ("トークンに乗っています（/debug_token の scopes に有り。"
                          "読み取りの口は無いので、この口は叩いていません）")


def _is_permission_error(err: dict | None, message: str) -> bool:
    if "permission" in (message or "").lower():
        return True
    if isinstance(err, dict):
        code = err.get("code")
        if isinstance(code, int) and code in _PERMISSION_ERROR_CODES:
            return True
    return False


def _read_error(e: urllib.error.HTTPError) -> tuple:
    """HTTPError の本文から Meta の `error` と `message` を読む（読めなければ空）。"""
    message = ""
    err = None
    try:
        err = json.loads(e.read() or b"{}").get("error")
        if isinstance(err, dict):
            message = str(err.get("message", ""))[:170]
    except Exception:
        pass
    return err, message


def _raise_if_permission(e: urllib.error.HTTPError, permission: str) -> None:
    """権限系の 4xx なら `PermissionMissing`（doctor と同じ物差し）。それ以外は何もしない。"""
    err, message = _read_error(e)
    if _is_permission_error(err, message):
        detail = redact_mod.redact(f"HTTP {e.code} {message}".strip())
        raise base.PermissionMissing(permission, detail) from e


def _summarize(body: dict) -> str:
    rows = body.get("data")
    if isinstance(rows, list):
        trimmed = [{k: v for k, v in row.items() if k in PROBE_KEEP} for row in rows[:3]]
        return f"{len(rows)} 件 " + json.dumps(trimmed, ensure_ascii=False)[:220]
    kept = {k: v for k, v in body.items() if k in PROBE_KEEP}
    return json.dumps(kept or body, ensure_ascii=False)[:220]


def _run_probe(get, base_url: str, probe: _Probe, token: str) -> dict:
    def row(ok, detail, body=None):
        return {"name": probe.key or probe.label, "label": probe.label,
                "permission": probe.permission, "key": probe.key,
                "ok": ok, "detail": detail, "body": body}
    if probe.unverifiable:
        # 読み取りの口が無い権限。**叩かない**（副作用のある口しか無い）。
        return row(None, f"{UNVERIFIABLE_BY_READ}（{probe.unverifiable}）")
    try:
        body = get(base_url, probe.path, probe.params, token)
        return row(True, _summarize(body), body)
    except urllib.error.HTTPError as e:
        message = ""
        err = None
        try:
            err = json.loads(e.read() or b"{}").get("error")
            if isinstance(err, dict):
                message = str(err.get("message", ""))[:170]
        except Exception:
            pass
        http = redact_mod.redact(f"HTTP {e.code} {message}".strip())
        if _is_permission_error(err, message):
            return row(False, f"{NOT_GRANTED}（{http}）")
        if e.code >= 500:
            # サーバ側の失敗。権限の有無は何も判っていない。
            return row(None, f"{COULD_NOT_VERIFY}（{http}）")
        return row(False, http)
    except ProbeBodyError as e:
        # **HTTP は 200 だが本文が error**。メッセージは `_get()` で既に
        # redact 済みなので、そのまま出してよい。
        text = str(e)[:220]
        if _is_permission_error(None, text):
            return row(False, f"{NOT_GRANTED}（{text}）")
        return row(False, text)
    except Exception as e:  # ネットワーク層。例外文にトークンが混じらないよう型名だけ。
        return row(None, f"{COULD_NOT_VERIFY}（{type(e).__name__}）")


class ThreadsAdapter(base.Adapter):
    # 設計 v2 §4.2。`inbox` は持たない（Threads は push 型ではない）。
    # `account_insights`（アカウント単位の日次）を**持つのは Threads だけ**
    # （T3 の配線 2026-09-13）。`collect._collect_account_daily()` はこの語を見て
    # 呼ぶかどうかを決める——無い媒体で毎回 `errors` に積むのをやめるため。
    CAPABILITIES = frozenset({"topic", "link_preview", "views", "quota", "refresh",
                              "recent_posts", "account_insights"})
    # **v2.1-A（設計 v2 §4.3・2026-09-14）: 読み取りの口 3 つと `inbox`。**
    # 上の「`inbox` は持たない」はこの日まで。`threads_manage_mentions` の
    # 言及（`mentions()`）は「利用者から始まった会話」そのものなので、`inbox()`
    # がそれを返し、`collect` の v2-3 の配管（`data/sns/inbox/<YYYY-MM>.ndjson`）
    # がそのまま受け皿になる。**足す形**（既存の行を変えない・並行 Track と
    # 同じ行を触らないため）。
    CAPABILITIES = CAPABILITIES | frozenset({"keyword_search", "mentions",
                                             "profile_lookup", "inbox"})
    # `thread_read`（T1-1）: `fetch_post()` が `GET /{id}`（`threads_basic`）で
    # 根を 1 件引ける。
    CAPABILITIES = CAPABILITIES | frozenset({"thread_read"})

    # `thth auth`（OAuth の往復）に Meta の app.env が要る **唯一の媒体**。
    AUTH_NEEDS_APP_ENV = True
    POST_ID_FORM_HINT = "Threads の post_id は数字の id です。"

    # **承認を通る書き込みの口に要る権限**（設計 v2 §4.3・v2.1-B）。
    # **L2** create-posts/location-tagging: 「`threads_location_tagging` — Required
    # for making GET calls to the location search endpoint and for making POST
    # calls to the publishing endpoints with a location tag」。
    # **L2** create-posts/share-to-ig-stories: `threads_share_to_instagram`。
    # **L2** posts/delete-posts: 「`threads_delete` — Required for making any
    # delete calls」。
    OPTION_PERMISSIONS = {
        "location_id": "threads_location_tagging",
        "share_to_instagram": "threads_share_to_instagram",
    }
    LOCATION_PERMISSION = "threads_location_tagging"
    DELETE_PERMISSION = "threads_delete"

    # `thth/oauth.py::SCOPES_SOURCE_REQUESTED` と同じ綴り（`oauth` を import
    # すると循環になるので、値だけをここにも持つ）。`/debug_token` に訊けな
    # かったので**要求した一覧をそのまま書いた**——「トークンに乗っている」を
    # 一度も確かめていない。**付与済みとして使ってはいけない**（セキュリティ
    # 監査 2026-09-16・P3-1）。
    SCOPES_SOURCE_REQUESTED = "requested"

    def __init__(self, *, base_url: str = DEFAULT_BASE_URL, access_token: str = "",
                 user_id: str = "", wait_seconds: float = DEFAULT_WAIT_SECONDS,
                 timeout: float = DEFAULT_TIMEOUT_SECONDS, scopes=None,
                 scopes_source: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        # **値そのものを登録**（セキュリティ監査 2026-09-16・P1-1）。サーバが
        # キー名なしで値を反射しても（`rejected credential <値>`）、`redact()`
        # が正規表現の綴りに関わらずこの値を消せるようにする。
        redact_mod.register_secret(access_token)
        self.user_id = user_id
        self.wait_seconds = wait_seconds
        self.timeout = timeout
        # **`.token` の `scopes`**（一覧なら「乗っている権限」・それ以外は不明）。
        # `granted_scopes()` が読む。**書かない**（`.token` は読むだけ）。
        #
        # **`scopes_source == "requested"` の一覧は「付与済み」として使わない**
        # （P3-1）。`thth auth` は `/debug_token` に訊けなかったとき、**要求した
        # 一覧をそのまま** `scopes` に書く（`oauth.py` の
        # `SCOPES_SOURCE_REQUESTED` 分岐）。それをここで一覧として受け取ると、
        # `granted_scopes()` が「訊いた答え」のふりをして返し、`_require_scope()`
        # が本当は確かめていない権限を「乗っていない」と言い切ったり、
        # `threads_read_cli._granted_source()` が「トークンに乗っています」と
        # 言い切ったりする——**要求しただけ**なのに。`requested` なら一覧を
        # 捨てて `None`（不明）に戻し、`granted_scopes()` が従来どおり
        # `/debug_token` を 1 回だけ引くようにする。
        token_scopes = list(scopes) if isinstance(scopes, list) else None
        if scopes_source == self.SCOPES_SOURCE_REQUESTED:
            token_scopes = None
        self._token_scopes = token_scopes
        # `/debug_token` の答え（**プロセス内で 1 回だけ**引く・`granted_scopes()`）。
        self._debug_scopes = None
        self._debug_scopes_asked = False

    @classmethod
    def from_account(cls, account_cfg: dict, token: dict):
        """台帳と `.token` から組み立てる（旧 `core._default_adapter_factory`）。

        `user_id` は `.token`（`thth auth` / `thth token set` が書く）を優先し、
        無ければ台帳 `accounts/<account>.json` を見る。同じ値の置き場が 2 つ
        あるとずれるので、台帳側は空でも動く（統括の検収 T2a 指摘・T2b で解消）。
        """
        base_url = os.environ.get("THTH_THREADS_BASE_URL", DEFAULT_BASE_URL)
        wait_seconds = float(os.environ.get("THTH_THREADS_WAIT_SECONDS",
                                            str(DEFAULT_WAIT_SECONDS)))
        token = token or {}
        from .. import leave_gate
        adapter = cls(
            base_url=base_url,
            access_token=token.get("access_token", ""),
            user_id=token.get("user_id") or (account_cfg or {}).get("user_id", ""),
            wait_seconds=wait_seconds,
            # `.token` の `scopes`（`thth auth` が書く一覧・`thth token set` は null）。
            scopes=token.get("scopes"),
            # `.token` の `scopes_source`（P3-1・`__init__` が `"requested"` を
            # 「付与済み」として使わないよう読み分ける）。
            scopes_source=token.get("scopes_source"),
        )
        return leave_gate.bind(adapter, account_cfg)

    def _post(self, path: str, params: dict) -> dict:
        url = f"{self.base_url}{path}"
        data = urllib.parse.urlencode(params).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        with httpsafe.urlopen(req, timeout=self.timeout) as resp:
            body = resp.read()
            return json.loads(body) if body else {}

    def publish(self, post: base.Post, *, dry_run: bool,
                on_container_created=None,
                before_publish=None) -> base.PublishResult:
        """コンテナ作成 → `on_container_created(creation_id)`（inflight 書き込み用）→
        30 秒待つ → **`before_publish()` で最後の確認** → 公開。
        dry_run なら何も叩かず終わる（core.py 側で既にモード分岐
        しているので、ここに来るのは基本 production のときだけ想定だが、念のため
        dry_run にも対応しておく）。"""
        ts = jst.iso()
        if dry_run:
            return base.PublishResult(post_id=None, url=None, ts=ts, error=None, failure="none")

        params = {
            "media_type": "TEXT",
            "text": post.text,
            "access_token": self.access_token,
        }
        if post.reply_to:
            params["reply_to_id"] = post.reply_to
        # トピック（`topic_tag`）。空・None のときは params に入れない（空文字を
        # 送らない・設計 §2.2・masaru 裁定 2026-09-09）。
        if post.topic:
            params["topic_tag"] = post.topic
        # 場所（**L2** create-posts/location-tagging: 投稿作成の `location_id`）と
        # Instagram のストーリーズへの共有（**L2** create-posts/share-to-ig-stories:
        # `crossreshare_to_ig=true`）。どちらも承認の指紋に入っている値だけが
        # ここに来る（`core._throw_chosen()`）。無ければ params に入れない。
        if post.location_id:
            params["location_id"] = post.location_id
        if post.share_to_instagram:
            params["crossreshare_to_ig"] = "true"
        # この投稿の任意項目に要る権限（無ければ空）。コンテナ作成が権限系エラーで
        # 断られたとき、**どの権限が足りないか**を言うために先に控える。
        needed = self.permissions_for_post(post)

        # コンテナ作成の失敗はどんな理由でも「公開の呼び出しに到達していない」＝
        # 出ていない（設計 §3.5 の表）。
        try:
            create = self._post(f"/{self.user_id}/threads", params)
        except urllib.error.HTTPError as e:
            detail_data, err, message = api_diagnostic.read_http_error(e)
            if needed and _is_permission_error(err, message):
                # 場所・Instagram 共有の権限がトークンに乗っていない（doctor と同じ
                # 物差し）。**出ていない**。core は inflight を消して rc=2。
                detail = f"HTTP {e.code}" + api_diagnostic.suffix(detail_data)
                return base.PublishResult(
                    None, None, ts,
                    error=base.not_granted_message(needed[0], detail),
                    failure="permission", api_diagnostic=detail_data)
            return base.PublishResult(None, None, ts,
                                       error=f"container作成失敗: HTTP {e.code}" + api_diagnostic.suffix(detail_data),
                                       failure="container", api_diagnostic=detail_data)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return base.PublishResult(None, None, ts,
                                       error=redact_mod.redact(f"container作成失敗: {e}"),
                                       failure="container")

        creation_id = create.get("id")
        if not creation_id:
            return base.PublishResult(None, None, ts, error="container作成失敗: id無し",
                                       failure="container")

        if on_container_created:
            on_container_created(creation_id)

        if self.wait_seconds:
            time.sleep(self.wait_seconds)

        # **実際の公開要求の直前に、もう一度確かめる**（独立検収 2026-09-11・P1-3）。
        # 統括は「公開要求の直前」と書きながら `adapter.publish()` を呼ぶ前に
        # 検査していた。**その中に container 作成と 30 秒の待機がある**ので、
        # 待機のあいだに継続期限を越えても公開していた。**待ったあとに見る。**
        if before_publish is not None:
            veto = before_publish()
            if veto:
                return base.PublishResult(None, None, ts, error=str(veto),
                                           failure="publish_vetoed")

        # 公開の失敗は「出ていない」（HTTP 4xx）と「分からない」（timeout・接続断・
        # 5xx・200 だが id 無し）に分かれる（設計 §3.5 の表）。この判定は媒体固有の
        # 知識（HTTP 状態番号の意味）なので、ここ（アダプタ）に閉じる（§3.4）。
        try:
            publish = self._post(f"/{self.user_id}/threads_publish", {
                "creation_id": creation_id,
                "access_token": self.access_token,
            })
        except urllib.error.HTTPError as e:
            detail_data, _, _ = api_diagnostic.read_http_error(e)
            failure = "publish_definite" if 400 <= e.code < 500 else "publish_ambiguous"
            return base.PublishResult(None, None, ts,
                                       error=f"公開失敗: HTTP {e.code}" + api_diagnostic.suffix(detail_data),
                                       failure=failure, api_diagnostic=detail_data)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return base.PublishResult(None, None, ts,
                                       error=redact_mod.redact(f"公開失敗: {e}"),
                                       failure="publish_ambiguous")

        post_id = publish.get("id")
        if not post_id:
            # 200 だが id が無い＝サーバ側で成立した可能性を排除できない＝分からない。
            return base.PublishResult(None, None, ts, error="公開失敗: id無し",
                                       failure="publish_ambiguous")
        return base.PublishResult(post_id=post_id, url=None, ts=ts, error=None, failure="none")

    # ---- 承認を通る書き込みの口（設計 v2 §4.3・v2.1-B）----------------------

    # **L2** https://developers.facebook.com/docs/threads/create-posts/location-tagging
    # 「`GET /location_search`」・引数は `q`（本文の例）または `latitude`+`longitude`。
    # 返る field: `id`・`name`・`address`・`city`・`country`・`latitude`・
    # `longitude`・`postal_code`。**「If your app has not been approved for the
    # `threads_location_tagging` permission, the search will be performed only on
    # the query 'Menlo Park'」**。同じ口の reference（reference/location-search）は
    # 引数名を `query` と書いていて食い違う——例が載っている本文の `q` に合わせる
    # （doctor の probe と同じ判断）。
    LOCATION_SEARCH_FIELDS = "id,name,address,city,country"

    def location_search(self, query: str, *, limit: int = 5) -> list:
        """場所を語で検索して `[{"id", "name", "address", "city", "country"}, …]`。

        読み取りだけ。`limit` 件まで（既定 5）。権限が乗っていなければ
        `PermissionMissing`（rc=2 の断り）。
        """
        query = (query or "").strip()
        if not query:
            raise base.AdapterError("場所の語が空です")
        self._require_scope(self.LOCATION_PERMISSION)
        try:
            body = self._get("/v1.0/location_search",
                              {"q": query, "fields": self.LOCATION_SEARCH_FIELDS})
        except urllib.error.HTTPError as e:
            _raise_if_permission(e, self.LOCATION_PERMISSION)
            self._raise_if_500_without_scope(e, self.LOCATION_PERMISSION)
            raise base.AdapterError(redact_mod.redact(
                f"場所の検索に失敗: HTTP {e.code} {e.reason}")) from e
        except base.AdapterError as e:
            if _is_permission_error(None, str(e)):
                raise base.PermissionMissing(self.LOCATION_PERMISSION, str(e)[:170]) from e
            raise
        rows = self._rows(body, "場所の検索")
        out = []
        for row in rows[:max(0, int(limit))]:
            out.append({
                "id": str(row.get("id")) if row.get("id") is not None else None,
                "name": row.get("name"),
                "address": row.get("address"),
                "city": row.get("city"),
                "country": row.get("country"),
            })
        return out

    # **L2** https://developers.facebook.com/docs/threads/posts/delete-posts
    # 「`DELETE /v1.0/{threads-media-id}`」・応答 `{"success": true, "deleted_id":
    # "…"}`・「delete a Threads post that was created by the authenticated user」・
    # 「rate limit of 100 deletes per day per account」。
    def delete_post(self, post_id: str) -> dict:
        """公開済みの投稿 1 本を取り下げる（**DELETE を 1 回**・再試行しない）。

        **ここは `thth retract` の二段目からしか呼ばれない**（`retract_cli`）。
        `post_id` は Threads の数字の id だけを受ける——`/` や `?` を含む値で
        別の口へ向かう筋を構造で無くす。`success: true` 以外は成功と言わない。
        """
        post_id = (post_id or "").strip()
        if not post_id.isdigit():
            raise base.AdapterError(
                f"Threads の post_id は数字だけです（{post_id!r}）。取り下げません")
        url = (f"{self.base_url}/v1.0/{post_id}?"
               + urllib.parse.urlencode({"access_token": self.access_token}))
        req = urllib.request.Request(url, method="DELETE")
        try:
            with httpsafe.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            _raise_if_permission(e, self.DELETE_PERMISSION)
            raise base.AdapterError(redact_mod.redact(
                f"取り下げに失敗: HTTP {e.code} {e.reason}")) from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise base.AdapterError(redact_mod.redact(f"取り下げに失敗: {e}")) from e
        try:
            body = json.loads(raw) if raw else {}
        except ValueError as e:
            raise base.AdapterError("取り下げの応答が JSON ではありません") from e
        if not isinstance(body, dict) or body.get("success") is not True:
            raise base.AdapterError(
                "取り下げの応答に success: true がありません（消えたとは言えません）: "
                + redact_mod.redact(json.dumps(body, ensure_ascii=False)[:200]))
        return {"success": True,
                "deleted_id": str(body.get("deleted_id") or post_id)}

    def _get(self, path: str, params: dict, *, absolute_url: str | None = None) -> dict:
        p = dict(params)
        p["access_token"] = self.access_token
        if absolute_url is not None:
            # **次の頁は API が URL ごと返す。** 組み立て直さない（cursor の
            # 組み立てを自前でやると、仕様が変わったときに黙って 1 頁で止まる）。
            url = absolute_url
            if "access_token=" not in url:
                sep = "&" if "?" in url else "?"
                url = f"{url}{sep}access_token={urllib.parse.quote(self.access_token)}"
        else:
            url = f"{self.base_url.rstrip('/')}{path}?" + urllib.parse.urlencode(p)
        with httpsafe.urlopen(url, timeout=self.timeout) as resp:
            body = json.loads(resp.read() or b"{}")
        # **200 で返ってきた `error` を、取れたことにしない**（監査 2026-09-11）。
        # 失敗すれば `urlopen` が上げるので、採取側は「例外なら記録を書かない」
        # で成功と失敗を分けている。**だが 200 のまま `error` が入っていると、
        # `body.get("data") or []` が静かに 0 件になり、「取れて 0 件」として
        # 台帳に残る。** 返信が 10 回とも 0 件だったときに、それが事実なのか
        # 取れていないのかを、あとから区別できなくなる。
        if isinstance(body, dict) and body.get("error"):
            raise RuntimeError(
                f"API が error を返しました（HTTP 200）: "
                f"{redact_mod.redact(str(body['error']))[:200]}")
        return body

    @staticmethod
    def _metric_value(row: dict):
        """Insights の 1 行から数を取り出す。

        Threads は指標によって `values: [{value: N}]` と `total_value: {value: N}` の
        2 つの形を返す。**どちらか片方だけを見ると黙って None になる**ので両方見る。
        """
        total = row.get("total_value")
        if isinstance(total, dict) and "value" in total:
            return total["value"]
        values = row.get("values")
        if isinstance(values, list) and values and isinstance(values[0], dict):
            return values[0].get("value")
        # **3 つめの形がある**（外部調査 2026-09-11 Codex・独立に再現済み）。
        # `clicks` は `link_total_values: [{value, link_url}]` で返る。
        # ここを見ていなかったので `None` になり、`account_insights()` が
        # None の指標を落とすため、**clicks は一度も記録されていなかった可能性が
        # 高い**（本番で何件失われたかは未確認）。
        links = row.get("link_total_values")
        if isinstance(links, list) and links:
            numbers = [x.get("value") for x in links
                       if isinstance(x, dict) and isinstance(x.get("value"), (int, float))]
            if numbers:
                return sum(numbers)
        return None

    @staticmethod
    def _link_values(row: dict) -> list:
        """URL ごとの内訳（`clicks` だけが持つ）。

        **投稿ごとの clicks は取れない**が、**URL ごとの内訳は取れる**
        （外部調査 2026-09-11 で判明。こちらの資料には「どの URL かも分からない」と
        書いていたが誤りだった）。ただし**URL と投稿は一対一とは限らない**——
        同じ URL を複数の投稿で使えば、どの投稿の分かは決まらない。
        **合算して投稿へ割り当てない。**
        """
        links = row.get("link_total_values")
        if not isinstance(links, list):
            return []
        return [{"link_url": x.get("link_url"), "value": x.get("value")}
                for x in links if isinstance(x, dict)]

    @staticmethod
    def _rows(body: dict, what: str) -> list:
        """応答から `data` の配列を取り出す。**取れなかったら失敗として上げる。**

        逆監査 2026-09-11（別セッションに反証を取りに行かせた結果）。
        `body.get("data") or []` と書いていたので、次のどれも**静かに 0 件**に
        なっていた——**「取れて 0 件」と区別できない。**

        - `{}`（`data` がそもそも無い）
        - `{"data": null}`
        - 空の body

        さらに `{"data": "文字列"}` のときは**文字列がそのまま返り**、採取側の
        `for row in replies` が 1 文字ずつ回って落ちる（`len()` がそのまま件数に
        なる筋もある）。**形が違うものを、件数として数えない。**

        200 が返っていても、**中身が期待した形でなければ「取れていない」。**
        """
        if not isinstance(body, dict) or "data" not in body:
            raise RuntimeError(
                f"{what}: 応答に data がありません。**取れて 0 件とは区別できない**"
                f"ので、失敗として扱います")
        rows = body["data"]
        if rows is None:
            raise RuntimeError(f"{what}: data が null です（0 件とは違います）")
        if not isinstance(rows, list):
            raise RuntimeError(
                f"{what}: data が配列ではありません（{type(rows).__name__}）。"
                f"**件数として数えません**")
        # **配列という外形だけでなく、要素も見る**（外部レビュー再々判定 N5・
        # 2026-09-12）。`{"data": [null]}` は「配列」を通ってしまい、以前は
        # `[None]` をそのまま返信一覧として返していた。呼び出し側（`collect.py`）
        # の `row.get(...)` が `AttributeError` で落ち、**その例外は 1 投稿分の
        # try/except の外**（リスト内包表記の外）で起きるので、当該投稿どころか
        # 後続の投稿の採取まで止まっていた。ここで弾けば `collect.py` の
        # 「例外は `errors` に積んで次の投稿へ進む」がそのまま効く。
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                raise RuntimeError(
                    f"{what}: data の {i} 番目が object ではありません"
                    f"（{type(row).__name__}）。**件数として数えません**")
        return rows

    def recent_posts(self, *, limit: int = 25) -> list:
        """`GET /{user_id}/threads`（**L2**: developers.facebook.com の Threads API）。

        **`account_report.fetch_posts()` から移した**（F2・2026-09-13）。あちらは
        `graph.threads.net` の URL を直に組み立てており、`access_token` を**クエリに
        載せて**いた。Mastodon の `.token` も鍵が `access_token` なので、媒体を見ずに
        通すと**宛先違いに秘密が出る**——媒体名で止めるのではなく、**宛先を媒体が
        決める**形にした。

        **切り詰めない**（`thth doctor` の 220 字要約を投稿一覧の代わりに使わせて
        いたのが間違いだった・nigamilab セッション 2026-09-10）。
        """
        if not self.user_id:
            raise base.AdapterError("user_id が判らないので直近の投稿を引けません")
        body = self._get(f"/v1.0/{self.user_id}/threads",
                          {"fields": RECENT_POST_FIELDS, "limit": limit})
        return [{"post_id": row.get("id"),
                 "timestamp": row.get("timestamp"),
                 "url": row.get("permalink"),
                 "text": row.get("text"),
                 "topic": row.get("topic_tag")}
                for row in self._rows(body, "直近の投稿")]

    def insights(self, post_id: str) -> dict:
        """投稿 1 本の数（`views`・`likes`・`replies`・`reposts`・`quotes`・`shares`）。

        **読んだ時点の累計**で、since/until は使えない（設計 §4.5）。だから
        「いつ読んだか」と「投稿からの経過時間」を必ず添えて記録する
        （`thth.collect`）。取れなかった指標は入れない——**0 と混ぜない**。

        戻りは `{"metrics": {...}, "available": [...]}`（設計 v2 §4.2）。
        `available` は**この媒体が持っている**指標の名前で、`metrics` の有無とは
        別——「取れなかった」と「媒体に無い」を混ぜないための欄。
        """
        body = self._get(f"/v1.0/{post_id}/insights",
                          {"metric": ",".join(POST_METRICS)})
        out = {}
        for row in self._rows(body, "投稿の数"):
            name = row.get("name")
            value = self._metric_value(row)
            if name and value is not None:
                out[name] = value
        return {"metrics": out, "available": list(POST_METRICS)}

    def conversation(self, post_id, *, since=None):
        """その投稿の**会話全体**（`threads_read_replies`）。**全階層。**

        **名前を変えた**（`replies` → `conversation`・2026-09-12）。見る先が
        `/{post_id}/replies`（**上位 1 階層**）から `/{post_id}/conversation`
        （**全階層**）に変わったので、**古い読み手を黙って通さない**（規約 5）。

        **設計にはもともとこう書いてあった**（設計 §5「`GET /{post_id}/conversation`
        （全階層）を取り」）。実装が `/replies` を呼んでいたのは**逸脱**で、
        新しい設計判断ではない。

        **見つかり方**: masaru が 2026-09-12 08:25〜08:40 に kopicha の投稿への
        返信 2 件に**返信した**（＝2 段目）。運用セッションがスクリーンショットで
        現物を見ていたのに、`thth replies` に 1 件も出なかった。
        **うちの側の発言が台帳に残らず、会話の片側しか記録されない。**

        **`replied_to` と `root_post` を取る**ので、階層の形も残る。
        """
        params = {"fields": "id,text,username,timestamp,permalink,is_reply,"
                             "replied_to,root_post,has_replies"}
        rows = self._all_pages(f"/v1.0/{post_id}/conversation", params, "会話")
        # **`medium` と `author_key` を足す**（設計 v2 §4.2）。**既存の鍵は
        # 変えない**——返信の台帳（ndjson）は追記専用で、過去の行と同じ鍵で
        # 読めなくなると `thth replies` も `measured` も黙って壊れる。
        # 足すだけなら、古い行は「まだ足す前の行」として読める。
        for row in rows:
            row["medium"] = MEDIUM
            row["author_key"] = base.author_key(MEDIUM, row.get("username"))
        return rows

    def whoami(self) -> dict:
        """`{"user_id", "username"}`（Threads の `me` を包む・設計 v2 §4.2）。

        `thth token set` の実在確認と `thth doctor` の本人確認がここを通る。
        **トークンの値は返り値にも例外文にも出さない。**
        """
        try:
            body = self._get("/v1.0/me", {"fields": "id,username"})
        except urllib.error.HTTPError as e:
            raise base.AdapterError(
                redact_mod.redact(f"user_id の取得に失敗しました: HTTP {e.code} {e.reason}")) from e
        except base.AdapterError:
            raise
        except Exception as e:
            raise base.AdapterError(
                redact_mod.redact(f"user_id の取得に失敗しました: {e}")) from e
        if not isinstance(body, dict):
            raise base.AdapterError("user_id の取得に失敗しました: 応答が object ではありません")
        return {"user_id": body.get("id", ""), "username": body.get("username", "")}

    def probe(self, *, get=None) -> list:
        """読み取りだけで能力を測る（`thth doctor` の Threads 固有の probe）。

        **doctor から移した**（設計 v2 §4.2「`doctor` の probe を媒体側へ」）。
        どの口を叩けばどの権限が確かめられるかは媒体の知識で、doctor の知識では
        ない。**出力は現行と同じ**（T-B5）。

        守ること（doctor から引き継ぐ）:
          - **読み取りだけ。投稿・返信・削除は絶対に呼ばない。**
          - **トークンの値を出力に出さない。** エラー文も `redact()` を通す。
        """
        if get is None:
            def get(base_url, path, params, token):
                return self._get(path, params)

        now = int(time.time())
        since = now - 7 * 86400
        user_id = self.user_id

        probes = [
            _Probe("本人の確認", "threads_basic", "/v1.0/me",
                   {"fields": "id,username"}),
            # **「直近 3 件まで」と名前に書く**（kopicha セッション指摘 2026-09-10）。
            # 「3 件」と返ったのを投稿総数だと読まれ、masaru に「3 本しか投稿して
            # いない」と報告しかけた、という報告を受けた。実際は 4 件あった。
            # 全部を読む口は `thth posts`（切り詰めない）。
            _Probe("自分の投稿一覧（直近 3 件まで・総数ではありません→ thth posts）",
                   "threads_basic", f"/v1.0/{user_id}/threads",
                   {"fields": "id,permalink,timestamp", "limit": 3}, key="my_posts"),
            _Probe("投稿の残量", "threads_content_publish",
                   f"/v1.0/{user_id}/threads_publishing_limit",
                   {"fields": "quota_usage,config,reply_quota_usage,reply_config"}),
            _Probe("数（views・likes・followers）", "threads_manage_insights",
                   f"/v1.0/{user_id}/threads_insights",
                   {"metric": "views,likes,followers_count",
                    "since": since, "until": now}),
            _Probe("数（リンクのクリック）", "threads_manage_insights",
                   f"/v1.0/{user_id}/threads_insights",
                   {"metric": "clicks", "since": since, "until": now}),
        ]

        results = [_run_probe(get, self.base_url, p, self.access_token) for p in probes]

        # 返信の取得は投稿が 1 本要る。上で拾えた最初の投稿で試す（無ければ飛ばす）。
        first_post_id = None
        my_posts_ok = None
        for r in results:
            if r["key"] == "my_posts":
                my_posts_ok = r["ok"]
                if r["ok"] and r.get("body"):
                    rows = r["body"].get("data") or []
                    if rows:
                        first_post_id = rows[0].get("id")
                break
        if first_post_id:
            results.append(_run_probe(get, self.base_url, _Probe(
                # **採取が実際に叩く口を probe する**（2026-09-12）。`/replies` を
                # probe していたが、採取は `/conversation`（全階層）を叩く。**違う口の
                # 疎通を確かめて「返信の取得は通る」と言っていた。**
                # **件数の意味を書く**（外部レビュー C・2026-09-12）。**選んだ 1 投稿の
                # 先頭ページ・要求上限 3 件**であって、全投稿でも全返信数でもない。
                # `limit` は**要求値**で、総数でも完全性の証明でもない。
                "返信の取得（疎通確認・**選んだ 1 投稿の先頭ページ・要求上限 3 件**。"
                "**全返信数ではありません**）",
                "threads_read_replies",
                f"/v1.0/{first_post_id}/conversation",
                {"fields": "id,username,timestamp", "limit": 3}, key="replies"),
                self.access_token))
        elif my_posts_ok is False:
            # **「投稿がまだ無いので試せない」は、投稿一覧が実際に取れたときだけ
            # 言ってよい**（外部レビュー再々判定 N7・2026-09-12）。投稿一覧の
            # 取得そのものが失敗していたら、理由はそちらであって「未投稿」ではない
            # ——事実と違う表示を作らない。`ok: False` にして失敗数にも数える。
            results.append({"name": "replies", "label": "返信の取得", "key": "replies",
                            "permission": "threads_read_replies",
                            "ok": False,
                            "detail": "投稿一覧の取得に失敗したため試せていません"
                                      "（上の「自分の投稿一覧」参照）",
                            "body": None})
        else:
            results.append({"name": "replies", "label": "返信の取得", "key": "replies",
                            "permission": "threads_read_replies",
                            "ok": None, "detail": "投稿がまだ無いので試せない",
                            "body": None})

        # **残り 7 権限**（2026-09-14）。`DEFAULT_SCOPES` は 11 権限を要求する
        # （設計 §8-14「権限は例外なく全部」）のに、doctor が確かめていたのは
        # 4 権限だけだった——残りは「乗っているはずだが確かめていない」。
        # 読み取りの口がある 4 つは叩き、無い 3 つは理由つきの `ok=None` で並べる
        # （**`scopes.DEFAULT_SCOPES` の 11 権限すべてが少なくとも 1 行**に出る——
        # `tests/test_doctor_all_permissions.py` が固定する）。
        results.extend(_run_probe(get, self.base_url, p, self.access_token)
                       for p in self._extra_probes(user_id))

        # **`/debug_token` で、乗っている権限の一覧を訊く**（2026-09-14・監査後の
        # 追加）。読み取りだけ。取れたら、読み取りの口が無い 3 権限の行を
        # 「乗っている／乗っていない」に格上げする。取れなければ従来の None のまま。
        debug = self._debug_token_probe(get)
        granted = debug.get("scopes")
        if isinstance(granted, list):
            for r in results:
                if r["key"] in UNVERIFIABLE_KEYS:
                    if r["permission"] in granted:
                        r["ok"] = True
                        r["detail"] = GRANTED_BY_DEBUG_TOKEN
                    else:
                        r["ok"] = False
                        r["detail"] = f"{NOT_GRANTED}（/debug_token の scopes に無い）"
        results.append(debug)
        return results

    # **L2** https://developers.facebook.com/docs/threads/troubleshooting/debug-access-token
    # `GET /v1.0/debug_token?access_token=<tester のユーザートークン>&input_token=<同じ>`
    # の `data.scopes` が「the user has granted for the app in this access token」
    # の一覧。app access token は要らない（tester のユーザートークンでよい）。
    # 資料はこの口に要る権限を挙げていないので、行の permission は「全部の口に
    # 要る」`threads_basic` にしてある。
    DEBUG_TOKEN_KEY = "debug_token"

    def _debug_token_probe(self, get) -> dict:
        from .. import scopes as scopes_mod
        row = _run_probe(get, self.base_url, _Probe(
            "トークンに乗っている権限の一覧（/debug_token・読み取り）", "threads_basic",
            "/v1.0/debug_token", {"input_token": self.access_token},
            key=self.DEBUG_TOKEN_KEY), self.access_token)
        row["scopes"] = None
        row["missing"] = None
        row["extra"] = None
        if row["ok"] is not True:
            return row
        data = (row.get("body") or {}).get("data")
        scopes = data.get("scopes") if isinstance(data, dict) else None
        if not isinstance(scopes, list) or not all(isinstance(x, str) for x in scopes):
            # 200 だが形が違う。**嘘の一覧を作らない**——「取れなかった」にする。
            row["ok"] = None
            row["detail"] = f"{COULD_NOT_VERIFY}（応答に data.scopes の一覧が無い）"
            return row
        wanted = list(scopes_mod.DEFAULT_SCOPES)
        row["scopes"] = list(scopes)
        row["missing"] = [x for x in wanted if x not in scopes]
        row["extra"] = [x for x in scopes if x not in wanted]
        row["detail"] = f"{len(scopes)} 個: " + ", ".join(scopes)
        return row

    # 検索の語は**固定・無害**で各 1 回だけ叩く。**投稿しない・書かない。**
    #
    # - keyword_search（**L2** keyword-search）: `q` は必須。承認前は「自分の
    #   投稿だけ」が検索対象になるので 0 件でもよい（0 件の検索は上限に数えない）。
    #   語は `お茶`。
    # - location_search（**L2** create-posts/location-tagging）: 「承認前は
    #   `Menlo Park` という語しか検索できない」と明記されている。承認の前後
    #   どちらでも通る語はそれしか無いので、**`Menlo Park` に固定**する。
    #   （同じ資料の本文は引数名を `q`、reference/location-search は `query` と
    #   書いていて食い違う。例が載っている本文の `q` に合わせる。）
    KEYWORD_SEARCH_QUERY = "お茶"
    LOCATION_SEARCH_QUERY = "Menlo Park"
    # profile_lookup（**L2** threads-profiles）: 「標準アクセスでは Meta の公式
    # アカウント（@meta・@threads・@instagram・@facebook）しか引けない」
    # 「公開かつフォロワー 100 以上のプロフィールだけ返す」。**自分の handle を
    # 引くと、権限が乗っていても標準アクセスの制限で失敗する**ので、権限の有無を
    # 見分けられない。資料が名指しする公式アカウントのうち `threads` を 1 回
    # 引く（Meta 自身の公開プロフィール・第三者ではない）。
    PROFILE_LOOKUP_USERNAME = "threads"

    def _extra_probes(self, user_id: str) -> list:
        return [
            # **L2** https://developers.facebook.com/docs/threads/keyword-search
            _Probe("投稿の検索（固定語 1 回・読み取りだけ）", "threads_keyword_search",
                   "/v1.0/keyword_search",
                   {"q": self.KEYWORD_SEARCH_QUERY, "search_type": "TOP",
                    "fields": "id", "limit": 1}, key="keyword_search"),
            # **L2** https://developers.facebook.com/docs/threads/threads-mentions
            # `GET /{threads-user-id}/mentions`・`threads_manage_mentions`
            _Probe("自分への言及の取得（要求上限 3 件）", "threads_manage_mentions",
                   f"/v1.0/{user_id}/mentions",
                   {"fields": "id", "limit": 3}, key="mentions"),
            # **L2** https://developers.facebook.com/docs/threads/threads-profiles
            # `GET /profile_lookup?username=…`・`threads_profile_discovery`。
            # 返る field に `id` は無いので `username` を引く。
            _Probe("公開プロフィールの参照（Meta 公式 @threads を 1 回）",
                   "threads_profile_discovery", "/v1.0/profile_lookup",
                   {"username": self.PROFILE_LOOKUP_USERNAME, "fields": "username"},
                   key="profile_lookup"),
            # **L2** https://developers.facebook.com/docs/threads/create-posts/location-tagging
            # `GET /location_search`・`threads_location_tagging`（GET と、投稿時の
            # location_id の両方に要る。ここで叩くのは GET だけ）。
            _Probe("場所の検索（固定語 1 回・読み取りだけ）", "threads_location_tagging",
                   "/v1.0/location_search",
                   {"q": self.LOCATION_SEARCH_QUERY, "fields": "id"},
                   key="location_search"),
            # **L2** https://developers.facebook.com/docs/threads/retrieve-and-manage-replies
            # 「threads_manage_replies — Required for making POST calls to reply
            # endpoints」「threads_read_replies — Required for making GET calls to
            # reply endpoints」。GET の返信取得は read_replies の行で確かめている
            # ので、この権限に読み取りの口は無い。
            _Probe("返信の作成・非表示", "threads_manage_replies", "", {},
                   key="manage_replies",
                   unverifiable="返信の作成・非表示（POST）にしか使われない権限です。"
                                "返信の取得（GET）は threads_read_replies の行"),
            # **L2** https://developers.facebook.com/docs/threads/posts/delete-posts
            # 「threads_delete — Required for making any delete calls」。口は
            # `DELETE /{threads-media-id}` だけ。**削除は絶対に呼ばない。**
            _Probe("投稿の削除", "threads_delete", "", {}, key="delete",
                   unverifiable="削除（DELETE）にしか使われない権限で、"
                                "doctor は削除を呼びません"),
            # **L2** https://developers.facebook.com/docs/threads/create-posts/share-to-ig-stories
            # 「threads_share_to_instagram — Required for cross-sharing the Threads
            # post to the user's linked Instagram account as a Story」。投稿作成
            # （POST）の `crossreshare_to_ig` にしか使われず、読み取りの口は無い。
            _Probe("Instagram ストーリーズへの共有", "threads_share_to_instagram",
                   "", {}, key="share_to_instagram",
                   unverifiable="投稿の作成（POST）の crossreshare_to_ig にしか"
                                "使われない権限で、doctor は投稿しません"),
        ]

    # **頁を最後まで辿る**（外部レビュー C1・P2・2026-09-12）。
    #
    # `/conversation` は**頁分割された一覧**で、1 応答で会話全件は返らない。
    # **1 頁目だけ取って「取れた」と記録していた**ので、2 頁目にあった返信への
    # 返信は永久に入らない（**同じ刻みで再実行しても、取得済の印が付いている**）。
    # **「会話全体を残す」という目的そのものを外していた。**
    #
    # **途中で失敗したら、部分を成功として返さない**——例外を上げる。採取側は
    # 「例外なら記録を書かない」で成功と失敗を分けているので、次の刻みでやり直せる。
    _PAGE_LIMIT = 50

    def _all_pages(self, path: str, params: dict, what: str) -> list:
        out: list = []
        seen_urls: set = set()
        body = self._get(path, params)
        for _ in range(self._PAGE_LIMIT):
            out.extend(self._rows(body, what))
            paging = body.get("paging") if isinstance(body, dict) else None
            if paging is None:
                return out                      # **正常な終端**（頁の情報が無い）
            if not isinstance(paging, dict):
                raise RuntimeError(f"{what}: `paging` の形が違います（{type(paging).__name__}）")
            if "next" not in paging or paging["next"] is None:
                return out                      # **正常な終端**
            nxt = paging["next"]
            # **「正常な終端」と「型が違う」を分ける**（外部レビュー・2026-09-12）。
            # 文字列でなければ終端として `return` していたので、**数値・配列・辞書が
            # 来ると部分結果を成功として返していた**——取得済の印が付き、次の刻みで
            # やり直せない。**読めない形は、終わりではない。**
            if not isinstance(nxt, str) or not nxt:
                raise RuntimeError(
                    f"{what}: 次の頁の指し先が読めません"
                    f"（{type(nxt).__name__}）。**途中までを取れたことにしません**")
            # **次の頁の指し先は「同じサーバの https」だけ**（セキュリティ監査
            # 2026-09-14・P1-2）。`paging.next` は**サーバが自由に書ける文字列**
            # で、`_get(absolute_url=...)` はそれに `access_token` を付けて叩いて
            # いた——`{"paging": {"next": "https://attacker.example/x"}}` を 1 度
            # 返すだけで、**アクセストークンが第三者のログに載る**。
            #
            # 読めない形（型が違う）と同じ扱いにする（`RuntimeError`・**途中まで
            # を取れたことにしない**）。追わずに黙って終端にすると、取得済の印が
            # 付いて次の刻みでやり直せなくなる。
            parts = urllib.parse.urlsplit(nxt)
            base_netloc = urllib.parse.urlsplit(self.base_url).netloc
            if parts.scheme != "https" or parts.netloc != base_netloc:
                # **何が違うのかを言う**（監査 2 回目・P3-8）。`http://` への
                # 格下げでも「別のホストを指しています」と出ていたので、
                # **同じホストなのにホストが違うと言われる**——読んだ人は
                # 綴りを疑って原因に辿り着けない。
                違い = ("ホストが違います" if parts.netloc != base_netloc
                        else "scheme が違います（https でないと追いません）")
                raise RuntimeError(
                    f"{what}: 次の頁の指し先で{違い}"
                    f"（{parts.scheme}://{parts.netloc} ≠ https://{base_netloc}）。"
                    f"**追いません**（access_token を外へ出さないため）")
            if nxt in seen_urls:
                # **同じ頁を指し続ける**（API 側の不具合・cursor の取り違え）。
                # **黙って回り続けない。**
                raise RuntimeError(f"{what}: 次の頁が同じ URL を指しています（循環）")
            seen_urls.add(nxt)
            body = self._get(path, params, absolute_url=nxt)
        raise RuntimeError(
            f"{what}: 頁が {self._PAGE_LIMIT} を超えました"
            f"（**途中までを取れたことにしません**）")

    def account_insights(self, user_id: str, *, since: str, until: str) -> dict:
        """アカウント単位の日次（`clicks` はここでしか取れない・設計 §4.5・§8-13）。"""
        body = self._get(f"/v1.0/{user_id}/threads_insights", {
            "metric": "views,likes,replies,reposts,quotes,followers_count,clicks",
            "since": since, "until": until})
        out = {}
        for row in self._rows(body, "アカウントの日次"):
            name = row.get("name")
            value = self._metric_value(row)
            if name and value is not None:
                out[name] = value
            # **URL ごとの内訳を捨てない。** 投稿へ割り当てはしないが、
            # 「どのリンクが踏まれたか」は記事への導線を見るのに要る。
            links = self._link_values(row)
            if name and links:
                out[f"{name}_by_url"] = links
        return out

    def quota(self):
        return None

    def refresh_token(self, token):
        raise NotImplementedError("T1 の範囲外（T2 で実装）")

    # ------------------------------------------------------------------
    # **11 権限を使う読み取りの口**（設計 v2 §4.3・v2.1-A・2026-09-14）。
    #
    # 3 つとも GET だけ。**権限不足は `base.PermissionMissing` で loud に上げる**
    # （受け入れ (c)・判定は doctor と同じ `_is_permission_error()`）。他の失敗は
    # `AdapterError`（伏字済み）。**黙って 0 件にしない・500 を黙って返さない。**
    #
    # 一次資料（2026-09-14 に WebFetch で読んだ・**L2**）:
    #   - keyword-search: `GET /keyword_search?q=…&search_type=TOP|RECENT&limit=≤100`
    #     返る field は id・text・media_type・permalink・timestamp・username・
    #     has_replies・is_quote_post・is_reply。**上限は利用者あたり 24 時間で
    #     2,200 回**（0 件の検索は数えない）。**未承認だと「自分の投稿だけ」が
    #     検索対象**（"the search will be performed only on posts owned by the
    #     authenticated user"）。
    #   - threads-mentions: `GET /{user_id}/mentions?fields=…[&since&until]`。
    #     「非公開の利用者の投稿は返らない」「承認前は tester による言及だけ」。
    #   - threads-profiles: `GET /profile_lookup?username=…`。返る field は
    #     username・name・profile_picture_url・biography・follower_count・
    #     likes_count・quotes_count・reposts_count・views_count・is_verified。
    #     **標準アクセスでは @meta・@threads・@instagram・@facebook だけ**・
    #     「公開かつフォロワー 100 以上」・利用者あたり 24 時間で 1,000 回。
    #
    # `topic_tag` は keyword-search の資料の一覧に**無い**（Media の field 一覧に
    # あり、`recent_posts()` は `/{user_id}/threads` で実際に取れている・L1）。
    # 検索の応答に付くかは**L3**——付かなければ「タグ付きの割合」は「判らない」。

    KEYWORD_SEARCH_FIELDS = ("id,text,username,timestamp,permalink,media_type,"
                             "is_reply,is_quote_post,has_replies,topic_tag")
    MENTIONS_FIELDS = ("id,text,username,timestamp,permalink,media_type,"
                       "is_reply,has_replies")
    PROFILE_FIELDS = ("username,name,profile_picture_url,biography,follower_count,"
                      "likes_count,quotes_count,reposts_count,views_count,is_verified")
    # **L2**（keyword-search「maximum 100」）。
    KEYWORD_SEARCH_MAX_LIMIT = 100
    SEARCH_TYPES = ("TOP", "RECENT")

    # ------------------------------------------------------------------
    # **権限が無いと分かっているトークンでは叩かない**（本番 P1・2026-09-14）。
    #
    # v2.1.0 で `inbox` が言及（`threads_manage_mentions`）を流すようになり、
    # `collect` が 10 分ごとに `GET /{user_id}/mentions` を叩くようになった。
    # **5 権限のトークン**（`thth token set` の管理画面発行・`.token` の
    # `scopes` は null）では Meta が **HTTP 500** を返す（4xx ではない・doctor
    # v2.0.2 の実測でも同じ）。`_read()` は 500 を権限不足と読まないので
    # `AdapterError` → `collect` の `errors` に毎 run 積まれ、`thth run` が
    # 「採取は完全ではありません」を 10 分ごとに出していた。
    #
    # 直し方は 2 段。**「無いと分かっている」ときだけ**断り、**不明なら叩く**
    # （判らないことを「無い」にしない・本物のサーバ障害と混ぜない）。
    #   1. 口を叩く前に `_require_scope()`——`granted_scopes()` に無ければ HTTP を
    #      叩かず `PermissionMissing`。
    #   2. 叩いて 500 が返ったとき（`_raise_if_500_without_scope()`）——
    #      `granted_scopes()` に無いと分かっていれば `PermissionMissing`、
    #      不明なら従来どおり `AdapterError`。
    #
    # `granted_scopes()` の答えは `.token` の `scopes`（一覧のとき）→ 無ければ
    # `/debug_token`（**プロセス内で 1 回だけ**・取れなければ None＝不明）。
    # **`.token` には書かない**（読むだけ）。

    SCOPES_FROM_TOKEN = ".token の scopes"
    SCOPES_FROM_DEBUG_TOKEN = "/debug_token の scopes"

    def granted_scopes(self) -> list | None:
        """トークンに乗っている権限の一覧。**判らなければ None**（嘘の一覧を作らない）。

        `.token` の `scopes` が一覧ならそれ。null なら `GET /v1.0/debug_token` を
        **この実体で 1 回だけ**引いて覚える（失敗・形違いも「1 回聞いた」に数え、
        None を覚える——毎 run 叩き直さない）。
        """
        if isinstance(self._token_scopes, list):
            return list(self._token_scopes)
        if not self._debug_scopes_asked:
            self._debug_scopes_asked = True
            self._debug_scopes = self._fetch_debug_scopes()
        return list(self._debug_scopes) if isinstance(self._debug_scopes, list) else None

    def _fetch_debug_scopes(self) -> list | None:
        """`/debug_token` の `data.scopes`（**L2**・doctor の `_debug_token_probe()` と同じ口）。

        取れなければ None（4xx/5xx・ネットワーク・形違いのどれでも）。ここで
        例外を上げない——**権限の一覧が取れないことは、口を叩けない理由にならない。**
        """
        try:
            body = self._get("/v1.0/debug_token", {"input_token": self.access_token})
        except Exception:
            return None
        data = body.get("data") if isinstance(body, dict) else None
        scopes = data.get("scopes") if isinstance(data, dict) else None
        if not isinstance(scopes, list) or not all(isinstance(x, str) for x in scopes):
            return None
        return list(scopes)

    def _scopes_source(self) -> str:
        return (self.SCOPES_FROM_TOKEN if isinstance(self._token_scopes, list)
                else self.SCOPES_FROM_DEBUG_TOKEN)

    def scopes_source(self) -> str | None:
        """`granted_scopes()` が答えられたときの**出どころ**（判らなければ None）。

        読み口の CLI（`thth profile` 等）が「権限は乗っているのに API が断った」
        を言い分けるために使う。**None は「一覧が取れなかった」**——乗っている
        とも乗っていないとも言わない（`granted_scopes()` の但し書きと同じ）。
        """
        return None if self.granted_scopes() is None else self._scopes_source()

    def _scope_known_missing(self, permission: str) -> bool:
        """`permission` が **無いと分かっている**か（不明は False）。"""
        granted = self.granted_scopes()
        return isinstance(granted, list) and permission not in granted

    def _require_scope(self, permission: str) -> None:
        """無いと分かっている権限の口は **HTTP を叩かずに** `PermissionMissing`。"""
        if self._scope_known_missing(permission):
            raise base.PermissionMissing(
                permission, f"{self._scopes_source()}に無いので叩いていません")

    def _raise_if_500_without_scope(self, e: urllib.error.HTTPError,
                                    permission: str) -> None:
        """500 系で、かつ権限が無いと分かっているときだけ `PermissionMissing`。

        **500 を無条件に権限不足にしない**——一覧が不明なら何もしない（呼び手が
        従来どおり `AdapterError` にする）。
        """
        if e.code >= 500 and self._scope_known_missing(permission):
            raise base.PermissionMissing(
                permission,
                redact_mod.redact(f"HTTP {e.code}・{self._scopes_source()}に無い")) from e

    def _read(self, fetch, *, permission: str, what: str):
        """`fetch()` を呼び、失敗を **`PermissionMissing` / `AdapterError`** に読み分ける。

        `_get()`・`_all_pages()` は `urllib.error.HTTPError`（4xx/5xx）と
        `RuntimeError`（200 だが `error`・形が違う）を上げる。doctor の
        `_run_probe()` と同じ判定で「権限が乗っていない」を切り出す。

        **叩く前に `_require_scope()`**——無いと分かっている権限なら HTTP を
        叩かない。500 は `_raise_if_500_without_scope()` で読み分ける（不明なら
        `AdapterError` のまま）。
        """
        self._require_scope(permission)
        try:
            return fetch()
        except urllib.error.HTTPError as e:
            message = ""
            err = None
            try:
                err = json.loads(e.read() or b"{}").get("error")
                if isinstance(err, dict):
                    message = str(err.get("message", ""))[:170]
            except Exception:
                pass
            http = redact_mod.redact(f"HTTP {e.code} {message}".strip())
            if _is_permission_error(err, message):
                raise base.PermissionMissing(permission, http) from e
            self._raise_if_500_without_scope(e, permission)
            raise base.AdapterError(f"{what}: {http}") from e
        except base.AdapterError:
            raise
        except RuntimeError as e:
            # `_get()` の「200 だが error」・`_rows()`/`_all_pages()` の形の違い。
            # 文面は既に伏字済み。
            text = str(e)[:220]
            if _is_permission_error(None, text):
                raise base.PermissionMissing(permission, text) from e
            raise base.AdapterError(f"{what}: {text}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise base.AdapterError(
                redact_mod.redact(f"{what}: {type(e).__name__}: {e}")) from e

    def _message_row(self, row: dict) -> dict:
        """媒体の 1 行を `Message` の形（設計 v2 §4.2）に写す。**既存の鍵は残す。**

        `replied_to`・`root_post` は**要求していない**（keyword-search・mentions の
        資料の field 一覧に無い）。応答に付いていればそのまま写し、無ければ None。
        """
        username = row.get("username")
        out = {
            "message_id": row.get("id"),
            "username": username,
            "text": row.get("text"),
            "timestamp": row.get("timestamp"),
            "replied_to": row.get("replied_to"),
            "root_post": row.get("root_post"),
            "medium": MEDIUM,
            "author_key": base.author_key(MEDIUM, username),
            # SNS なので会話窓は無い（設計 v2 §4.1）。
            "reply_deadline": None,
        }
        for key in ("permalink", "media_type", "is_reply", "is_quote_post",
                    "has_replies", "topic_tag"):
            if key in row:
                out[key] = row[key]
        return out

    def keyword_search(self, q: str, *, search_type: str = "TOP",
                       limit: int = 25) -> list:
        """`GET /keyword_search`（`threads_keyword_search`・**L2**）。**1 頁だけ。**

        返るのは `Message` の形の行（`_message_row()`）。**本文はここで返すだけ**
        ——どこにも書かない（保存しないのは呼ぶ側の規律でもある・§4.3「入れないもの」）。
        """
        if not isinstance(q, str) or not q.strip():
            raise base.AdapterError("検索の語が空です")
        if search_type not in self.SEARCH_TYPES:
            raise base.AdapterError(
                f"search_type は {' / '.join(self.SEARCH_TYPES)} のどちらかです"
                f"（{search_type!r}）")
        if not isinstance(limit, int) or limit < 1 or limit > self.KEYWORD_SEARCH_MAX_LIMIT:
            raise base.AdapterError(
                f"limit は 1〜{self.KEYWORD_SEARCH_MAX_LIMIT} です（{limit!r}）")
        params = {"q": q.strip(), "search_type": search_type,
                  "fields": self.KEYWORD_SEARCH_FIELDS, "limit": limit}
        rows = self._read(
            lambda: self._rows(self._get("/v1.0/keyword_search", params), "投稿の検索"),
            permission="threads_keyword_search", what="投稿の検索")
        return [self._message_row(r) for r in rows]

    def mentions(self, *, since=None) -> list:
        """`GET /{user_id}/mentions`（`threads_manage_mentions`・**L2**）。**全頁。**

        `since` は資料どおり Unix 時刻か読める日付（そのまま渡す）。
        """
        if not self.user_id:
            raise base.AdapterError("user_id が判らないので言及を引けません")
        params = {"fields": self.MENTIONS_FIELDS}
        if since is not None:
            params["since"] = since
        rows = self._read(
            lambda: self._all_pages(f"/v1.0/{self.user_id}/mentions", params, "言及"),
            permission="threads_manage_mentions", what="言及の取得")
        return [self._message_row(r) for r in rows]

    # **L2**（threads-media・2026-09-16 に WebFetch で読解）: `GET /{id}` の
    # field 一覧に `id`・`username`・`text`・`timestamp`・`permalink` はあるが、
    # `replied_to`・`root_post`・`is_reply` は**無い**（それらは会話・返信の
    # 一次資料（reply-management）の field で、単体投稿の一覧には出てこない）。
    # **無い field は落とす**——ここでは確認できた field だけを要求する。
    # 「他人の投稿は `threads_basic` の advanced access 承認後に取得可能」
    # （**L2**）なので、未承認・404・その他の 4xx は `_read()` の読み分けで
    # `PermissionMissing` か `AdapterError` に化ける——**「無い」に化かさない**。
    FETCH_POST_FIELDS = "id,username,text,timestamp,permalink"

    def fetch_post(self, post_id: str) -> dict:
        """根を 1 件だけ引く（T1-1・設計「自分の泉」§2.1）。`GET /{id}`（`threads_basic`）。

        **この口が返す行は枝の根**として使うので、`replied_to` は常に
        `None`・`root_post` は自分自身の id にする（単体投稿の一覧に
        `replied_to`・`root_post` は無いので、そもそも読めない）。
        """
        if not isinstance(post_id, str) or not post_id.strip():
            raise base.AdapterError("post_id が空です")
        post_id = post_id.strip()
        body = self._read(
            lambda: self._get(f"/v1.0/{post_id}", {"fields": self.FETCH_POST_FIELDS}),
            permission="threads_basic", what="投稿の取得")
        if not isinstance(body, dict) or not body.get("id"):
            raise base.AdapterError("投稿の取得: 応答に id がありません")
        username = body.get("username")
        out = {
            "message_id": body.get("id"),
            "username": username,
            "text": body.get("text"),
            "timestamp": body.get("timestamp"),
            "replied_to": None,
            "root_post": body.get("id"),
            "medium": MEDIUM,
            "author_key": base.author_key(MEDIUM, username),
            "reply_deadline": None,
        }
        if body.get("permalink"):
            out["permalink"] = body["permalink"]
        return out

    def inbox(self, *, since=None) -> list:
        """**言及を `inbox` に流す**（設計 v2 §4.3「v2-3 の芽がそのまま受け皿」）。

        `collect` は `capabilities()` に `inbox` を見て呼び、
        `data/sns/inbox/<YYYY-MM>.ndjson` に `message_id` で冪等に追記する。
        権限が無ければ `PermissionMissing` が上がる（**無いと分かっていれば
        HTTP を叩かずに**・`granted_scopes()`）。`collect` はそれを `errors` に
        積まず `inbox_state: permission_missing` として残して続行する（投稿は
        止めない・本番 P1 2026-09-14）。
        """
        return self.mentions(since=since)

    def profile_lookup(self, username: str) -> dict:
        """`GET /profile_lookup?username=…`（`threads_profile_discovery`・**L2**）。

        返るのは資料の field をそのまま持つ dict（無い field は入れない）に
        `medium`・`author_key` を添えたもの。**標準アクセスでは Meta 公式の
        4 アカウントしか引けない**（それは権限の有無とは別の断り方で返る）。
        """
        if not isinstance(username, str) or not username.strip():
            raise base.AdapterError("username が空です")
        handle = username.strip().lstrip("@")
        body = self._read(
            lambda: self._get("/v1.0/profile_lookup",
                              {"username": handle, "fields": self.PROFILE_FIELDS}),
            permission="threads_profile_discovery", what="プロフィールの参照")
        if not isinstance(body, dict):
            raise base.AdapterError("プロフィールの参照: 応答が object ではありません")
        out = {k: body[k] for k in self.PROFILE_FIELDS.split(",") if k in body}
        if "username" not in out:
            raise base.AdapterError(
                "プロフィールの参照: 応答に username がありません（取れたことにしません）")
        out["medium"] = MEDIUM
        out["author_key"] = base.author_key(MEDIUM, out["username"])
        return out
