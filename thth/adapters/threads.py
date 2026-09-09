"""ThreadsAdapter（graph.threads.net 相当・設計 §2.2・§3.4）。

T1 では本物の Threads API を叩かない。`base_url` を差し替え可能にしてあるので、
テストは `http.server` の偽 API にだけ向ける（`THTH_THREADS_BASE_URL` 環境変数
または直接のコンストラクタ引数で上書きする）。`replies`・`refresh_token` は
T2 以降の範囲なので実装しない（呼ばれたら NotImplementedError）。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .. import jst
from .. import redact as redact_mod
from . import base

DEFAULT_BASE_URL = "https://graph.threads.net"
DEFAULT_WAIT_SECONDS = 30.0
DEFAULT_TIMEOUT_SECONDS = 10.0


class ThreadsAdapter(base.Adapter):
    def __init__(self, *, base_url: str = DEFAULT_BASE_URL, access_token: str = "",
                 user_id: str = "", wait_seconds: float = DEFAULT_WAIT_SECONDS,
                 timeout: float = DEFAULT_TIMEOUT_SECONDS):
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self.user_id = user_id
        self.wait_seconds = wait_seconds
        self.timeout = timeout

    def _post(self, path: str, params: dict) -> dict:
        url = f"{self.base_url}{path}"
        data = urllib.parse.urlencode(params).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = resp.read()
            return json.loads(body) if body else {}

    def publish(self, post: base.Post, *, dry_run: bool,
                on_container_created=None) -> base.PublishResult:
        """コンテナ作成 → `on_container_created(creation_id)`（inflight 書き込み用）→
        30 秒待つ → 公開。dry_run なら何も叩かず終わる（core.py 側で既にモード分岐
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

    def replies(self, post_id, *, since=None):
        raise NotImplementedError("T1 の範囲外（T3 で実装）")

    def quota(self):
        return None

    def refresh_token(self, token):
        raise NotImplementedError("T1 の範囲外（T2 で実装）")
