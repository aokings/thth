"""承認の指紋の golden（設計 3.3.0 A3）。**指紋の計算の規律を機械で守る。**

既知の入力（`CASES`）から `select` と同じ道で指紋を計算し、`approval.FINGERPRINT_VERSION`
の golden（`GOLDEN`）と比べる。食い違えば `drift()` がその名前を返す。

指紋の計算を**意図して**変えるときは、`approval.FINGERPRINT_VERSION` を上げ、
`approval.FINGERPRINT_HISTORY` に道具の版を足し、ここの `GOLDEN` に新しい版の行を
足す（古い版の行は消さない——いつ何が変わったかの記録）。意図せず変わったとき
（タグの付け方を直したら指紋まで動いた、等）は試験が落ちて気づく。

入力は短い見本だけ（本文・account は架空）。秘密は入れない。
"""
from __future__ import annotations

from . import approval

# `select._validate_all()` と同じ道: 公開する本文（`effective_section()`）で取る。
CASES = (
    {"name": "threads_plain", "media": "threads", "section": "本文です。",
     "account": "golden-threads", "publish_at": "2026-09-09T08:00:00+09:00"},
    {"name": "threads_topic", "media": "threads", "section": "本文です。", "topic": "コーヒー",
     "account": "golden-threads", "publish_at": "2026-09-09T08:00:00+09:00", "hashtags": True},
    {"name": "bluesky_topic_tag", "media": "bluesky", "section": "今日の一杯。",
     "topic": "#コーヒー", "account": "golden-bluesky",
     "publish_at": "2026-09-09T08:00:00+09:00", "hashtags": True},
    {"name": "bluesky_existing_tag", "media": "bluesky", "section": "今日の一杯。 #コーヒー",
     "topic": "コーヒー", "account": "golden-bluesky",
     "publish_at": "2026-09-09T08:00:00+09:00", "hashtags": True},
    {"name": "mastodon_hashtags_off", "media": "mastodon", "section": "今日の一杯。",
     "topic": "コーヒー", "account": "golden-mastodon",
     "publish_at": "2026-09-09T08:00:00+09:00", "hashtags": False},
    {"name": "mastodon_topic_tag", "media": "mastodon", "section": "  今日の一杯。\n\n",
     "topic": "コーヒー", "account": "golden-mastodon",
     "publish_at": "2026-09-09T08:00:00+09:00", "hashtags": True},
    {"name": "reply_to_post_id", "media": "threads", "section": "返信です。",
     "account": "golden-threads", "publish_at": "2026-09-09T08:00:00+09:00",
     "front_matter": {"reply_to": "17900000000000001"}},
    {"name": "reply_to_file", "media": "threads", "section": "答えです。",
     "account": "golden-threads", "publish_at": "2026-09-10T08:00:00+09:00",
     "front_matter": {"reply_to_file": "q.md"}},
    {"name": "threads_options", "media": "threads", "section": "場所つき。",
     "account": "golden-threads", "publish_at": "2026-09-09T08:00:00+09:00",
     "front_matter": {"location_id": "123456", "share_to_instagram": "true"}},
    {"name": "bundle_bluesky", "media": "bluesky", "segments": ["一段目。", "二段目。"],
     "topic": "コーヒー", "account": "golden-bluesky",
     "publish_at": "2026-09-09T08:00:00+09:00",
     "continue_until": "2026-09-09T12:00:00+09:00", "hashtags": True},
)

# 版ごとの golden（`approval.FINGERPRINT_VERSION` → 見本の名前 → 指紋の先頭
# `PREFIX` 桁）。先頭だけで足りる（変化を検出する目的・秘密の検査に掛からない長さ）。
PREFIX = 16
GOLDEN = {
    2: {
        "threads_plain": "16d01580fbaba0f2",
        "threads_topic": "971c85566f4b8a98",
        "bluesky_topic_tag": "3ca0242e299ff878",
        "bluesky_existing_tag": "ce218739f9998d82",
        "mastodon_hashtags_off": "c6c08e89e9308d5c",
        "mastodon_topic_tag": "5cf0735fecbe962c",
        "reply_to_post_id": "21f78f893623c5e0",
        "reply_to_file": "e4d307a73438a5f1",
        "threads_options": "2150188e6b2af272",
        "bundle_bluesky": "e1e13b8e4197f403",
    },
}


def compute(case: dict) -> str:
    """見本 1 つの指紋を、`select`／`threadthrow` と同じ道で計算する。"""
    cfg = {"media": case["media"], "hashtags": case.get("hashtags", True)}
    topic = case.get("topic")
    if "segments" in case:
        from . import bundle
        return approval.compute_bundle_sha(
            segments=bundle.effective_segments(list(case["segments"]), cfg, topic),
            account=case["account"], topic=topic, publish_at=case["publish_at"],
            continue_until=case["continue_until"])
    fm = dict(case.get("front_matter") or {})
    return approval.compute_approved_sha(
        section=approval.effective_section(case["section"], cfg, topic),
        account=case["account"], reply_to=approval.reply_to_for_fingerprint(fm),
        topic=topic, publish_at=case["publish_at"], **approval.publish_options(fm))


def current() -> dict:
    """いまのコードで計算した見本の指紋（名前 → 指紋の先頭 `PREFIX` 桁）。"""
    return {case["name"]: compute(case)[:PREFIX] for case in CASES}


def drift() -> list:
    """golden と食い違う見本の名前（食い違いが無ければ空）。

    `FINGERPRINT_VERSION` の golden が無ければ `["no_golden_for_version"]`。見本が
    golden に無い・golden が見本に無いのも食い違いに数える（黙って比べ漏らさない）。
    """
    golden = GOLDEN.get(approval.FINGERPRINT_VERSION)
    if golden is None:
        return ["no_golden_for_version"]
    now = current()
    return sorted(name for name in set(now) | set(golden) if now.get(name) != golden.get(name))
