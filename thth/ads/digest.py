"""広告の承認の digest `thth-ads-1`（計画 D-1 の 7・D-2・段 5 の 6・監査 A-5）。

**何から作るか**: Meta から読んだキャンペーンの木の全体（`graphread.Reader.campaign_tree`）。THTH の
`ops/` の記録からは作らない。`thth ads activate`・`extend` は 1 段目でも 2 段目でも、その時点で Meta から
読み直した木で digest を取り直す。2 段目の値が 1 段目に見せた値（`--confirm`）と違えば `ads_tree_changed`
で断る（確定までの間に管理画面・公式 MCP・ほかの人が広告セットを足す・予算を変える、を防ぐ）。

入れる物（D-2）: 版の語・操作（activate／extend）・`act_id`・通貨・キャンペーン（id・目的・状態・
`effective_status`・予算・期間）・**その下の全部の**広告セット（id・状態・予算・`start_time`／`end_time`・
`targeting` の sha・`publisher_platforms`）と広告（id・広告セット・状態・`object_story_id`／
`source_instagram_media_id`）・延ばす操作なら変える値。金額は最小単位の整数。

本文の指紋（`approval.py` の `FINGERPRINT_VERSION`）とは別の版（はてなと同じ扱い）。並びを変えるときは
版の語を上げ、golden（`tests/test_meta_stage5.py`）を直す。
"""
from __future__ import annotations

import hashlib
import json

VERSION = "thth-ads-1"
LENGTH = 12

_CAMPAIGN_KEYS = ("id", "objective", "status", "effective_status", "lifetime_budget", "daily_budget",
                  "start_time", "stop_time")
_ADSET_KEYS = ("id", "campaign_id", "status", "effective_status", "lifetime_budget", "daily_budget",
               "start_time", "end_time", "targeting_sha", "publisher_platforms")
_AD_KEYS = ("id", "adset_id", "status", "effective_status")
OPS = ("activate", "extend")


def _pick(row: dict, keys) -> dict:
    return {key: row.get(key) for key in keys}


def components(*, op: str, act_id: str, currency: str | None, tree: dict, change: dict | None = None) -> dict:
    """digest の材料（並びを決めた形）。`tree` は `campaign_tree()` の答え。"""
    if op not in OPS:
        raise ValueError("ads_digest_op_invalid")
    ads = []
    for row in tree.get("ads") or []:
        item = _pick(row, _AD_KEYS)
        creative = row.get("creative") if isinstance(row.get("creative"), dict) else {}
        item["object_story_id"] = creative.get("object_story_id")
        item["source_instagram_media_id"] = creative.get("source_instagram_media_id")
        ads.append(item)
    return {
        "version": VERSION,
        "op": op,
        "act_id": act_id,
        "currency": currency,
        "campaign": _pick(tree.get("campaign") or {}, _CAMPAIGN_KEYS),
        "adsets": sorted((_pick(row, _ADSET_KEYS) for row in tree.get("adsets") or []),
                         key=lambda r: str(r.get("id"))),
        "ads": sorted(ads, key=lambda r: str(r.get("id"))),
        "change": dict(sorted((change or {}).items())),
    }


def compute_ads_digest(*, op: str, act_id: str, currency: str | None, tree: dict, change: dict | None = None,
                       length: int = LENGTH) -> str:
    """`components()` を並びを決めた JSON にして sha256 の先頭 `length` 桁。"""
    raw = json.dumps(components(op=op, act_id=act_id, currency=currency, tree=tree, change=change),
                     ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]
