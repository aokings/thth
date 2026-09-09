"""アダプタの境界（差し替え可能な interface。設計 §3.4）。

礼儀（静かな時間帯・最短間隔・1 件だけ）は Adapter の外側の `select` が守る。
アダプタは「渡されたものを 1 回投げる」しかしない。
"""
from __future__ import annotations

import dataclasses


@dataclasses.dataclass
class Post:
    text: str                       # 媒体節の本文（検査済み）
    reply_to: str | None = None     # post_id | None
    link: str | None = None


@dataclasses.dataclass
class PublishResult:
    post_id: str | None
    url: str | None
    ts: str                         # JST ISO
    error: str | None = None        # 伏字済み
    # 失敗の種類（設計 §3.5・T1 検収 2026-09-09 で追加）。core はこれだけを見て
    # inflight を消すか残すかを決める（HTTP の状態番号は core が解釈しない・§3.4）。
    #   "none"              成功
    #   "container"         コンテナ作成の失敗（全部）→ 出ていない
    #   "publish_definite"  公開が HTTP 4xx → 出ていない
    #   "publish_ambiguous" 公開が timeout・接続断・5xx・200 だが id 無し → 分からない
    failure: str = "none"


@dataclasses.dataclass
class Reply:
    reply_id: str
    username: str
    text: str
    timestamp: str
    replied_to: str | None
    root_post: str | None


class Adapter:
    """媒体ごとの実装（ThreadsAdapter が最初。T6 で XAdapter が保留のまま並ぶ想定）。"""

    def publish(self, post: Post, *, dry_run: bool) -> PublishResult:
        raise NotImplementedError

    def replies(self, post_id: str, *, since: str | None = None) -> list:
        raise NotImplementedError

    def quota(self) -> dict | None:
        return None

    def refresh_token(self, token: dict) -> dict:
        raise NotImplementedError
