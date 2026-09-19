"""Bluesky/Mastodon tag preparation shared by preview, lint and publishing."""
from __future__ import annotations

import re

from . import queuefile

# A hashtag starts at a word boundary; URL fragments and email addresses are not tags.
TAG_RE = re.compile(r"(?<!\w)#([\w]+)", re.UNICODE)


def matches(text: str):
    return list(TAG_RE.finditer(text))


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
    if re.search(r"(?<!\w)#" + re.escape(topic) + r"(?!\w)", text):
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
