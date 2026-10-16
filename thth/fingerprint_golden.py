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
    # 3.19.0: X のコミュニティ（値があるときだけ末尾に足す任意項目・版は上げない）。
    {"name": "x_community", "media": "x", "section": "コミュニティへ。",
     "account": "golden-x", "publish_at": "2026-10-14T08:00:00+09:00", "hashtags": False,
     "front_matter": {"community_id": "1493446837214187523"}},
    # 4.0 S4 §9.3: 告知が結ぶ配信 id（値があるときだけ末尾に足す任意項目・版は上げない）。
    # 指紋は置き換えの前の本文（`{article_url}` のまま）で取る。
    {"name": "threads_announces", "media": "threads", "section": "記事を書きました。{article_url}",
     "account": "golden-threads", "publish_at": "2026-10-20T08:30:00+09:00",
     "front_matter": {"announces": "dl_3f9c2a7e1b4d"}},
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
        "x_community": "d305d1c3e5cab9cc",
        "threads_announces": "ecb7d08a9bf25a68",
        "bundle_bluesky": "e1e13b8e4197f403",
    },
    # 4.0 S1（設計 §6.1・§7.2）: はてなの承認の指紋。**SNS の v1 とは別の版の数え方**
    # （`approval.HATENA_ENTRY_VERSION`／`HATENA_BOOKMARK_VERSION` の末尾の数）で、
    # `FINGERPRINT_VERSION` は上げない。鍵は種類の名前 → 版 → 見本の名前 → 指紋の先頭。
    "hatena_entry": {
        1: {
            "entry_create": "61a9f764b6c4f2f2",
            "entry_update": "e09b5d896e8cb798",
            "entry_delete": "35a354158d34f168",
        },
    },
    "hatena_bookmark": {
        1: {
            "bookmark_add": "69be00ab5be98c16",
            "bookmark_delete": "c5c9d175ac229bf3",
            # 4.0 S4 §6.2: 参照先（`delivery: ""`）。今まで断っていた入力なので既存の指紋と衝突しない。
            "bookmark_add_external": "df42d7898bcb25fe",
        },
    },
}

# はてなの見本（本文・口座・URL は架空）。`approval.compute_hatena_*_sha()` にそのまま渡す。
HATENA_CASES = {
    "hatena_entry": (
        {"name": "entry_create", "args": {
            "account": "golden-hatenablog", "blog_id": "Golden.hatenablog.com", "op": "create",
            "entry_id": None, "base_readback_sha": None, "title": "  見本の  題  ",
            "categories": ["価格", "コーヒー", " "], "body": "本文です。\r\n\n二段落目。  \n\n",
            "images": [{"path": "images/b.jpg", "source_sha256": "b" * 64},
                       {"path": "images/a.jpg", "source_sha256": "a" * 64}],
            "article": "golden/2026-10-sample", "form": "full",
            "publish_at": "2026-10-20T08:00:00+09:00"}},
        {"name": "entry_update", "args": {
            "account": "golden-hatenablog", "blog_id": "golden.hatenablog.com", "op": "update",
            "entry_id": "tag:blog.hatena.ne.jp,2013:blog-golden-1-2", "base_readback_sha": "e" * 64,
            "title": "見本の題（直し）", "categories": [], "body": "直した本文。",
            "images": [], "article": "golden/2026-10-sample", "form": "full",
            "publish_at": "2026-10-21T08:00:00+09:00"}},
        {"name": "entry_delete", "args": {
            "account": "golden-hatenablog", "blog_id": "golden.hatenablog.com", "op": "delete",
            "entry_id": "tag:blog.hatena.ne.jp,2013:blog-golden-1-2", "base_readback_sha": "f" * 64,
            "title": "見本の題", "categories": None, "body": "", "images": None,
            "article": "golden/2026-10-sample", "form": "summary",
            "publish_at": "2026-10-22T08:00:00+09:00"}},
    ),
    "hatena_bookmark": (
        {"name": "bookmark_add", "args": {
            "account": "golden-hatenablog", "principal": "golden-principal", "op": "add",
            "url": " https://golden.hatenablog.com/entry/2026/10/20/080000 ",
            "delivery": "dl_0123456789ab", "comment": "運営者の記事です。\n見本。",
            "tags": ["コーヒー", "価格"]}},
        {"name": "bookmark_delete", "args": {
            "account": "golden-hatenablog", "principal": "golden-principal", "op": "delete",
            "url": "https://golden.hatenablog.com/entry/2026/10/20/080000",
            "delivery": "dl_0123456789ab", "comment": "", "tags": None}},
        {"name": "bookmark_add_external", "args": {
            "account": "golden-hatenablog", "principal": "golden-principal", "op": "add",
            "url": " https://example.com/coffee/arabica-2026?page=2#top ", "delivery": "",
            "comment": "参照した相場の頁。", "tags": ["相場", "コーヒー"]}},
    ),
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
        return ["no_golden_for_version"] + hatena_drift()
    now = current()
    return sorted(name for name in set(now) | set(golden)
                  if now.get(name) != golden.get(name)) + hatena_drift()


# ------------------------------------------------------------------ はてな（4.0 S1）

_HATENA_VERSION_WORD = {"hatena_entry": "HATENA_ENTRY_VERSION",
                        "hatena_bookmark": "HATENA_BOOKMARK_VERSION"}
_HATENA_COMPUTE = {"hatena_entry": "compute_hatena_entry_sha",
                   "hatena_bookmark": "compute_hatena_bookmark_sha"}


def hatena_version(kind: str) -> int:
    """`thth-hatena-entry-1` の末尾の数（版の語が golden の鍵）。"""
    return int(getattr(approval, _HATENA_VERSION_WORD[kind]).rsplit("-", 1)[1])


def hatena_current(kind: str) -> dict:
    compute_sha = getattr(approval, _HATENA_COMPUTE[kind])
    return {case["name"]: compute_sha(**case["args"])[:PREFIX] for case in HATENA_CASES[kind]}


def hatena_drift() -> list:
    """はてなの見本の食い違い（`<種類>:<見本の名前>`。版の golden が無ければ `<種類>:no_golden_for_version`）。"""
    out = []
    for kind in sorted(HATENA_CASES):
        golden = GOLDEN.get(kind, {}).get(hatena_version(kind))
        if golden is None:
            out.append(f"{kind}:no_golden_for_version")
            continue
        now = hatena_current(kind)
        out += sorted(f"{kind}:{name}" for name in set(now) | set(golden)
                      if now.get(name) != golden.get(name))
    return out
