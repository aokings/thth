"""Bluesky/Mastodon tag preparation shared by preview, lint and publishing."""
from __future__ import annotations

import re
import unicodedata
from collections import namedtuple

TagSpan = namedtuple("TagSpan", "start end tag")
URL_RE = re.compile(r"https?://[^\s　]+")


def _is_word(ch: str) -> bool:
    return ch == "_" or unicodedata.category(ch)[0] in "LNM"


def _bluesky_tag_end(text: str, start: int) -> int:
    """Match Bluesky's rich-text tag body, including emoji and internal punctuation."""
    excluded = "\u00ad\u2060\u200a\u200b\u200c\u200d\u20e2"
    end = start
    while end < len(text) and not text[end].isspace() and text[end] not in excluded:
        end += 1
    while end > start and unicodedata.category(text[end - 1]).startswith("P"):
        end -= 1
    body = text[start:end]
    if not any(not ch.isdigit() and not unicodedata.category(ch).startswith("P") for ch in body):
        return start
    return end


def spans(text: str, topic: str | None = None, *, media: str = "bluesky") -> list[TagSpan]:
    """Platform-aware tag spans outside URLs; explicit topic wins overlapping matches."""
    urls = [(m.start(), m.end()) for m in URL_RE.finditer(text)]

    def in_url(start, end):
        return any(start < b and end > a for a, b in urls)

    selected = []
    if topic:
        for m in re.finditer(r"(?<!\w)[#＃]" + re.escape(topic) + r"(?!\w)", text):
            if not in_url(m.start(), m.end()):
                selected.append(TagSpan(m.start(), m.end(), topic))
    for i, ch in enumerate(text):
        if ch not in "#＃" or (i and not text[i - 1].isspace()):
            continue
        j = i + 1
        if media == "bluesky":
            j = _bluesky_tag_end(text, j)
        else:
            while j < len(text) and (text[j].isalnum() or text[j] == "_"):
                j += 1
            if text[i + 1:j].isdecimal():
                continue
        if j == i + 1 or in_url(i, j):
            continue
        if any(i < chosen.end and j > chosen.start for chosen in selected):
            continue
        selected.append(TagSpan(i, j, text[i + 1:j]))
    return sorted(selected, key=lambda span: span.start)


def topic_error(media: str, topic: str | None) -> str | None:
    if topic is None or media not in ("bluesky", "mastodon"):
        return None
    if not topic or any(ch.isspace() for ch in topic) or "#" in topic:
        return "topic_invalid_tag(空白または#)"
    if media == "bluesky":
        from .adapters.bluesky import count
        if count(topic) > 64:
            return f"topic_too_long({count(topic)})"
        if len(topic.encode("utf-8")) > 640:
            return f"topic_too_many_bytes({len(topic.encode('utf-8'))})"
    elif not all(ch.isalnum() or ch == "_" for ch in topic) or topic.isdecimal():
        return "topic_invalid_tag(文字)"
    return None


def prepared(media: str, text: str, topic: str | None, *, hashtags: bool) -> str:
    """Append one topic tag when enabled; an existing identical tag wins."""
    if media not in ("bluesky", "mastodon") or not hashtags or not topic:
        return text
    if any(span.tag == topic for span in spans(text, topic, media=media)):
        return text
    return text.rstrip() + "\n#" + topic


def errors(media: str, text: str, topic: str | None, cfg: dict | None) -> list[str]:
    cfg = cfg or {}
    allowed = bool(cfg.get("hashtags", True))
    out = []
    if media == "bluesky":
        nbytes = len(prepared(media, text, topic, hashtags=allowed).encode("utf-8"))
        if nbytes > 3000:
            out.append(f"length: bluesky は 3000 UTF-8 bytes 以内（{nbytes} bytes）")
    if media in ("bluesky", "mastodon"):
        maximum = cfg.get("max_hashtags", 3)
        if type(maximum) is not int or maximum < 0:
            out.append("max_hashtags: 0 以上の整数にしてください")
        elif allowed:
            effective = prepared(media, text, topic, hashtags=True)
            found = spans(effective, topic, media=media)
            if len(found) > maximum:
                out.append(f"hashtag: 上限 {maximum} 個を超えています（{len(found)} 個）")
            if media == "bluesky":
                from .adapters.bluesky import count
                for span in found:
                    if count(span.tag) > 64 or len(span.tag.encode("utf-8")) > 640:
                        out.append(f"hashtag: タグが Bluesky 上限を超えています（#{span.tag}）")
                        break
    if topic is not None and media in ("bluesky", "mastodon"):
        issue = topic_error(media, topic)
        if issue:
            out.append(issue)
        if not allowed:
            out.append("warning: この媒体では topic が効きません（hashtags: false）")
    return out
