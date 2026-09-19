"""Bluesky/Mastodon tag preparation shared by preview, lint and publishing."""
from __future__ import annotations

import re
import unicodedata
from collections import namedtuple

TagSpan = namedtuple("TagSpan", "start end tag")
URL_RE = re.compile(r"https?://[^\s　]+")


def _is_word(ch: str) -> bool:
    return ch == "_" or unicodedata.category(ch)[0] in "LNM"


def spans(text: str, topic: str | None = None) -> list[TagSpan]:
    """Tag spans outside URLs; a supplied topic takes precedence over a partial word match."""
    urls = [(m.start(), m.end()) for m in URL_RE.finditer(text)]

    def in_url(start, end):
        return any(start < b and end > a for a, b in urls)

    selected = []
    if topic:
        for m in re.finditer(r"(?<!\w)#" + re.escape(topic) + r"(?!\w)", text):
            if not in_url(m.start(), m.end()):
                selected.append(TagSpan(m.start(), m.end(), topic))
    for i, ch in enumerate(text):
        if ch != "#" or (i and _is_word(text[i - 1])):
            continue
        j = i + 1
        while j < len(text) and _is_word(text[j]):
            j += 1
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
    elif not all(ch.isalnum() or ch == "_" for ch in topic):
        return "topic_invalid_tag(文字)"
    return None


def prepared(media: str, text: str, topic: str | None, *, hashtags: bool) -> str:
    """Append one topic tag when enabled; an existing identical tag wins."""
    if media not in ("bluesky", "mastodon") or not hashtags or not topic:
        return text
    if any(span.tag == topic for span in spans(text, topic)):
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
    if topic is not None and media in ("bluesky", "mastodon"):
        issue = topic_error(media, topic)
        if issue:
            out.append(issue)
        if not allowed:
            out.append("warning: この媒体では topic が効きません（hashtags: false）")
    return out
