"""Instagram のストーリーを `thth send --story` で出す口（11/24・masaru「両方とも作りましょう」）。

11/24 に本物で分かったこと: Threads の `crossreshare_to_ig`（`--share-to-instagram`）は媒体が SUCCESS と
返してもストーリーズが作られないことがある。IG の口座から直接（`post_options: {story: true}`・入れ物の
`media_type=STORIES`）なら出た。だからストーリーは IG の口座から直接出す。

ここは旗の組み合わせを見て、写真か本文を「送る絵」に決める。絵そのものは `thth/story_image.py`
（Pillow を使う唯一の所・入れていない機械では写真は注意を言うだけ、文字のカードは断る）。
断りの符丁は下の `STORY_REFUSALS`（目録 `thth/refusal_catalog_part5.py`）。詳細の語（`reason`）ごとに
通すにはともっと良くを分ける。
"""
from __future__ import annotations

import os

# 断りの符丁（目録の柵 `tests/test_refusal_catalog.py` がここから拾う）。
STORY_REFUSALS = ("story_unavailable", "story_card_unavailable")

# 詳細の語 → 1 行目（符丁のあとの、何が当たったかの文）。
LINES = {
    "medium": "ストーリーは Instagram の口座だけです（この口座は {medium}）",
    "reply": "返信はストーリーにできません（--reply-to と --story は一緒に使えません）",
    "paid_partnership": "ストーリーにはタイアップの印を付けません（--paid-partnership・--sponsor と --story は一緒に使えません）",
    "other_flags": "ストーリーでは {flags} を使えません",
    "media_count": "ストーリーは写真 1 枚だけです（--media が {count} 枚）",
    "text_and_photo": "ストーリーには字幕が無いので、写真と本文を一緒には渡せません",
    "empty": "ストーリーに出す物がありません（--media の写真か、--text の本文が要ります）",
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
    return None


# ---------------------------------------------------------------- 写真（縦横の比）

def fit_photo(account_cfg: dict, declaration: dict) -> tuple[dict, list[str]]:
    """写真のストーリーで送る添付（宣言, 経過の行）。

    9:16 でない写真は「上下か左右が切れます」と言い、絵を作る部品があれば余白を足した 1080×1920 の絵に
    替える（元の写真は変えない）。JPEG でない写真も、部品があれば JPEG に直す（Instagram は JPEG だけ）。
    読めない・形の分からない写真はそのまま返す（断りは送る道の検査が今までの文で言う）。
    """
    from . import media as media_mod
    from . import story_image
    repo_dir = (account_cfg or {}).get("repo_dir")
    if not repo_dir:
        return declaration, []
    try:
        manifest = media_mod.manifest_for({"media": [declaration]}, account_cfg)
    except (media_mod.MediaError, OSError):
        return declaration, []
    row = (manifest or {}).get("files", [{}])[0]
    width, height = row.get("width"), row.get("height")
    if row.get("orientation") in (5, 6, 7, 8):
        width, height = height, width
    if row.get("kind") != "image" or type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        return declaration, []
    shaped = story_image.is_story_shape(width, height)
    jpeg = row.get("format") == "jpeg"
    if shaped and jpeg:
        return declaration, []
    notes = []
    if not shaped:
        notes.append(f"ストーリーは 9:16（{story_image.WIDTH}×{story_image.HEIGHT}）で、この写真は {width}×{height} "
                     f"なので{story_image.cut_sides(width, height)}が切れます")
    if not story_image.available():
        if not shaped:
            notes.append("この機械には絵を作る部品（Pillow・pip install 'thth[image]'）が無いので、写真はそのまま出します"
                         "（9:16 の写真に替えると切れずに届きます）")
        return declaration, notes
    try:
        with media_mod.pin_sources(repo_dir, [declaration]) as sources:
            source = sources[0]
            data = os.pread(source.fd, source.size, 0)
            name = story_image.key("pad", source.source_sha256)
            relative = story_image.save(repo_dir, name, lambda: story_image.pad(data))
    except (media_mod.MediaError, OSError, ValueError) as exc:
        notes.append(f"余白を足した絵を作れなかったので、写真はそのまま出します（{type(exc).__name__}）")
        return declaration, notes
    if shaped:
        notes.append(f"Instagram は JPEG だけなので、JPEG に直した絵を出します: {relative}（元の写真は変えません）")
    else:
        notes.append(f"{story_image.pad_sides(width, height)}に余白を足して {story_image.WIDTH}×{story_image.HEIGHT} "
                     f"にした絵を出します: {relative}（元の写真は変えません・digest はこの絵で決まります）")
    return {"file": relative, "alt": declaration["alt"]}, notes


# ---------------------------------------------------------------- 本文のカード

class CardRefused(Exception):
    """カードを作れない（`detail` は目録の `story_card_unavailable` の詳細の語）。"""

    def __init__(self, detail: str, line: str):
        super().__init__(line)
        self.detail, self.line = detail, line


CARD_LINES = {
    "image_tool": "この機械には絵を作る部品（Pillow）が無いので、本文のカードを作れません",
    "font": "この機械に日本語の字の書体が見つからないので、本文のカードを作れません",
    "too_long": "本文が長すぎて 1 枚のカードに収まりません（{count} 字）",
}


def _label(account_cfg: dict, account: str) -> str:
    handle = str((account_cfg or {}).get("handle") or "").strip().lstrip("@")
    return f"@{handle}" if handle else account


def make_card(account_cfg: dict, account: str, text: str) -> tuple[dict, list[str]]:
    """本文を 1080×1920 のカードの絵にして口座の repo に置き、送る添付（宣言, 経過の行）を返す。"""
    from . import media as media_mod
    from . import story_image
    body = story_image.clean_text(text)
    if not story_image.available():
        raise CardRefused("image_tool", CARD_LINES["image_tool"])
    font = story_image.find_font()
    if font is None:
        raise CardRefused("font", CARD_LINES["font"])
    if len(body) > story_image.MAX_CHARS:
        raise CardRefused("too_long", CARD_LINES["too_long"].format(count=len(body)))
    fitted = story_image.layout(body, font)
    if fitted.lines is None:
        raise CardRefused("too_long", CARD_LINES["too_long"].format(count=len(body)))
    repo_dir = (account_cfg or {}).get("repo_dir")
    if not repo_dir:
        raise media_mod.MediaError("media: account repo_dir required")
    label = _label(account_cfg, account)
    name = story_image.key("card", body, label, os.path.basename(font), str(os.path.getsize(font)))
    relative = story_image.save(repo_dir, name, lambda: story_image.card(body, label, font, fitted))
    first = next((line for line in body.split("\n") if line.strip()), body)
    alt = ("本文のカード: " + first.strip())[:200]
    notes = [f"本文を {story_image.WIDTH}×{story_image.HEIGHT} のカードの絵にして出します: {relative}"
             f"（{label}・字 {fitted.size}px で {len(fitted.lines)} 行・書体 {os.path.basename(font)}"
             "・digest はこの絵で決まります）"]
    return {"file": relative, "alt": alt}, notes
