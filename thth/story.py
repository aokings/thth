"""Instagram のストーリーを `thth send --story` で出す口（11/24・masaru「両方とも作りましょう」）。

11/24 に本物で分かったこと: Threads の `crossreshare_to_ig`（`--share-to-instagram`）は媒体が SUCCESS と
返してもストーリーズが作られないことがある。IG の口座から直接（`post_options: {story: true}`・入れ物の
`media_type=STORIES`）なら出た。だからストーリーは IG の口座から直接出す。

ここは旗の組み合わせを見るだけで、絵は作らない（絵は `thth/story_image.py`・Pillow を使う唯一の所）。
断りの符丁は下の `STORY_REFUSALS`（目録 `thth/refusal_catalog_part5.py`）。詳細の語（`reason`）ごとに
通すにはともっと良くを分ける。
"""
from __future__ import annotations

# 断りの符丁（目録の柵 `tests/test_refusal_catalog.py` がここから拾う）。
STORY_REFUSALS = ("story_unavailable",)

# 詳細の語 → 1 行目（符丁のあとの、何が当たったかの文）。
LINES = {
    "medium": "ストーリーは Instagram の口座だけです（この口座は {medium}）",
    "reply": "返信はストーリーにできません（--reply-to と --story は一緒に使えません）",
    "paid_partnership": "ストーリーにはタイアップの印を付けません（--paid-partnership・--sponsor と --story は一緒に使えません）",
    "other_flags": "ストーリーでは {flags} を使えません",
    "media_count": "ストーリーは写真 1 枚だけです（--media が {count} 枚）",
    "text_and_photo": "ストーリーには字幕が無いので、写真と本文を一緒には渡せません",
    "empty": "ストーリーに出す物がありません（--media の写真か、--text の本文が要ります）",
    "text_only": "文字だけのストーリー（本文をカードの絵にする）はまだ作っていません",
}


def problem(account_cfg: dict, args, *, text: str, declarations: list) -> tuple[str, str] | None:
    """`--story` の組み合わせの誤り（詳細の語, 1 行目の文）。通るなら None。"""
    medium = (account_cfg or {}).get("media")
    if medium != "instagram":
        return "medium", LINES["medium"].format(medium=medium or "不明")
    if getattr(args, "reply_to", None):
        return "reply", LINES["reply"]
    if getattr(args, "paid_partnership", False) or getattr(args, "sponsor_ids", None):
        return "paid_partnership", LINES["paid_partnership"]
    others = [spelled for dest, spelled in (("community_id", "--community"),
                                            ("share_to_instagram", "--share-to-instagram"),
                                            ("topic", "--topic"))
              if getattr(args, dest, None) not in (None, False, "", [])]
    if others:
        return "other_flags", LINES["other_flags"].format(flags="・".join(others))
    if len(declarations) > 1:
        return "media_count", LINES["media_count"].format(count=len(declarations))
    has_text = bool((text or "").strip())
    if declarations and has_text:
        return "text_and_photo", LINES["text_and_photo"]
    if not declarations and not has_text:
        return "empty", LINES["empty"]
    if not declarations:
        return "text_only", LINES["text_only"]
    return None
