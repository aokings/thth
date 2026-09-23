"""inflight を道具が自分で解く（設計 3.3.1 §2・§3）。

**約束は 1 行**: 結果が分からないまま止まるのは、媒体に問い合わせても分からない
ときだけ。問い合わせで決まるなら道具が決めて進む。決まらないなら止まって人に
知らせる（`thth inflight <account> resolve` で人が決める）。

ここに置くのは媒体をまたぐ部品（本文の指紋・自分の最近の投稿との照合）と、
毎 run の最初の自己解決（§3）。Threads の container の status を読むのは
アダプタ（`ThreadsAdapter.container_status()`）で、core は媒体名を知らない。

**2 度出す経路を作らない。** ここで決めるのは「出た（post_id が 1 件に決まった）」
「出ていない（container が ERROR・EXPIRED・FINISHED）」だけで、ここから公開の
要求は 1 度も飛ばない。出ていないと決めたら inflight を解き、次の公開は select
から（承認の検査を全部通って）やり直す。
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import os
import re
import unicodedata

from . import jst

# 自分の最近の投稿を、公開した時刻の前後この幅で見る（設計 §2・§3）。
LOCATE_WINDOW = datetime.timedelta(minutes=10)
# 引く件数。10 分の窓に 25 本を越えて出すアカウントは無い（max_per_run 既定 1）。
LOCATE_LIMIT = 25

# container の status（一次資料 troubleshooting・2026-09-23 参照）。
CONTAINER_STATUSES = frozenset({"EXPIRED", "ERROR", "FINISHED", "IN_PROGRESS", "PUBLISHED"})

# inflight に書く「最後の問い合わせ結果」（静的な語だけ）。
REMOTE_STATES = frozenset({
    "finished", "published", "error", "expired", "in_progress", "query_failed",
    "published_unlocated", "retry_failed", "retried", "published_located",
    "listing_none", "listing_many", "listing_failed", "listing_located",
    "unsupported",
})


def normalize(text) -> str:
    """指紋に掛ける前の形。媒体が改行や空白を詰め直しても同じ本文を同じと見る。

    NFC に揃え、空白の並び（改行を含む）を 1 つの空白にして前後を落とす。
    **語を足したり削ったりはしない**——Mastodon の HTML を剥がした本文のように
    媒体側で文字そのものが変わっていれば一致しない（その時は解かない側に倒れる）。
    """
    value = unicodedata.normalize("NFC", text if isinstance(text, str) else "")
    return re.sub(r"\s+", " ", value).strip()


def text_fingerprint(text) -> str:
    """送る本文の指紋（sha256）。inflight に本文そのものは書かない（指紋だけ）。"""
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


@dataclasses.dataclass(frozen=True)
class Located:
    """自分の最近の投稿との照合の結果。`state` は静的な語。"""
    state: str                 # listing_located | listing_none | listing_many | listing_failed
    post_id: str | None = None
    timestamp: str | None = None


def locate(adapter, fingerprint: str | None, around) -> Located:
    """公開の時刻（`around`）の前後 10 分に、本文の指紋が一致する投稿が**ちょうど
    1 件**あるか。

    0 件なら `listing_none`（出ていないとは言い切らない——媒体が本文を変えて
    返すことがある）、2 件以上なら `listing_many`（どれか決められない）。
    引けなければ `listing_failed`。どれも「解かない」側の答え。
    """
    if not fingerprint or around is None:
        return Located("listing_failed")
    try:
        rows = adapter.recent_posts(limit=LOCATE_LIMIT)
    except Exception:  # noqa: BLE001 — 媒体の例外の形は問わない。引けなかった、だけを言う
        return Located("listing_failed")
    if not isinstance(rows, list):
        return Located("listing_failed")
    hits = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        when = jst.parse(row.get("timestamp"))
        if when is None or abs(when - around) > LOCATE_WINDOW:
            continue
        if text_fingerprint(row.get("text")) != fingerprint:
            continue
        post_id = row.get("post_id")
        if isinstance(post_id, str) and post_id:
            hits.append((post_id, jst.iso(jst.to_jst(when))))
    if len(hits) == 1:
        return Located("listing_located", hits[0][0], hits[0][1])
    return Located("listing_many" if hits else "listing_none")


def is_self_resolvable(record) -> bool:
    """道具が自分で解いてよい inflight か。

    **出たことが判っている inflight は触らない**（post_id が入っている・指紋の
    食い違い・媒体が返した post_id が書けない形）。それは「出たか分からない」
    ではなく「出たが記録できない」で、人が直す。添付の inflight（`media`）は
    journal が別の約束で動いているので対象外。
    """
    if not isinstance(record, dict):
        return False
    if record.get("media") or record.get("post_id") or record.get("mismatch_fields"):
        return False
    return isinstance(record.get("file"), str) and bool(record.get("file"))


def display_file(record) -> str | None:
    raw = (record or {}).get("file")
    if not isinstance(raw, str) or not raw:
        return None
    return raw if raw == "(send)" else os.path.basename(raw)

