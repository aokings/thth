"""ThreadsAdapter（graph.threads.net 相当・設計 §2.2・§3.4）。

T1 では本物の Threads API を叩かない。`base_url` を差し替え可能にしてあるので、
テストは `http.server` の偽 API にだけ向ける（`THTH_THREADS_BASE_URL` 環境変数
または直接のコンストラクタ引数で上書きする）。`replies`・`refresh_token` は
T2 以降の範囲なので実装しない（呼ばれたら NotImplementedError）。
"""
from __future__ import annotations

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
    def __init__(self, label: str, permission: str, path: str, params: dict,
                 key: str | None = None):
        self.label = label
        self.permission = permission
        self.path = path
        self.params = params
        self.key = key


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
    try:
        body = get(base_url, probe.path, probe.params, token)
        return row(True, _summarize(body), body)
    except urllib.error.HTTPError as e:
        message = ""
        try:
            message = (json.loads(e.read() or b"{}").get("error", {})
                       .get("message", ""))[:170]
        except Exception:
            pass
        return row(False, redact_mod.redact(f"HTTP {e.code} {message}".strip()))
    except ProbeBodyError as e:
        # **HTTP は 200 だが本文が error**。メッセージは `_get()` で既に
        # redact 済みなので、そのまま出してよい。
        return row(False, str(e)[:220])
    except Exception as e:  # ネットワーク層。例外文にトークンが混じらないよう型名だけ。
        return row(False, type(e).__name__)


class ThreadsAdapter(base.Adapter):
    # 設計 v2 §4.2。`inbox` は持たない（Threads は push 型ではない）。
    # `account_insights`（アカウント単位の日次）を**持つのは Threads だけ**
    # （T3 の配線 2026-09-13）。`collect._collect_account_daily()` はこの語を見て
    # 呼ぶかどうかを決める——無い媒体で毎回 `errors` に積むのをやめるため。
    CAPABILITIES = frozenset({"topic", "link_preview", "views", "quota", "refresh",
                              "recent_posts", "account_insights"})

    # `thth auth`（OAuth の往復）に Meta の app.env が要る **唯一の媒体**。
    AUTH_NEEDS_APP_ENV = True

    def __init__(self, *, base_url: str = DEFAULT_BASE_URL, access_token: str = "",
                 user_id: str = "", wait_seconds: float = DEFAULT_WAIT_SECONDS,
                 timeout: float = DEFAULT_TIMEOUT_SECONDS):
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self.user_id = user_id
        self.wait_seconds = wait_seconds
        self.timeout = timeout

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
        return cls(
            base_url=base_url,
            access_token=(token or {}).get("access_token", ""),
            user_id=(token or {}).get("user_id") or (account_cfg or {}).get("user_id", ""),
            wait_seconds=wait_seconds,
        )

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

        # コンテナ作成の失敗はどんな理由でも「公開の呼び出しに到達していない」＝
        # 出ていない（設計 §3.5 の表）。
        try:
            create = self._post(f"/{self.user_id}/threads", params)
        except urllib.error.HTTPError as e:
            return base.PublishResult(None, None, ts,
                                       error=redact_mod.redact(f"container作成失敗: {e.code} {e.reason}"),
                                       failure="container")
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
            failure = "publish_definite" if 400 <= e.code < 500 else "publish_ambiguous"
            return base.PublishResult(None, None, ts,
                                       error=redact_mod.redact(f"公開失敗: {e.code} {e.reason}"),
                                       failure=failure)
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
        return results

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
