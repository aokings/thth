"""連動 段 1: Instagram のストーリーの口（`post_options: {story: true}`・`thth/adapters/instagram_media.py`）。

**本物の Graph API は叩かない。** 偽の Graph と試験の中の偽の relay だけ（`tests/test_meta_stage3.py` の `wire`）。
縛ること: 入れ物は `media_type=STORIES` で字幕も alt も送らない・1 枚だけ・画像の無いストーリーは媒体に何も投げずに断る・
`thth send` の道（`core.send_once` の `post_options`）でも digest に入り、同じ 2 段で出る。
"""
from __future__ import annotations

import pytest

from thth import core, media
from thth.adapters import instagram_media as igm
from tests.test_meta_stage3 import IG, _calls, _manifest, adapter, invoke, wire  # noqa: F401


def _story(n=1, **over):
    m = _manifest(n, **over)
    m["post_options"] = {"story": True}
    return m


def test_ストーリーは1枚だけ_縦長でも通し_字幕のほかの任意項目は断る():
    assert igm.intent_error(_story()) is None
    assert igm.intent_error(_story(width=1080, height=1920)) is None        # 9:16（フィードなら断る形）
    assert igm.intent_error(_manifest(width=1080, height=1920)) == "media_limit_exceeded: aspect_ratio (4:5〜1.91:1)"
    assert igm.intent_error(_story(2)) == "media_limit_exceeded: count (story 1)"
    assert igm.intent_error(_story(format="png")) == "unsupported_attachment: instagram/png"
    m = _story()
    m["post_options"]["is_ai_generated"] = True
    assert igm.intent_error(m) == "unsupported_attachment: instagram/story_options"
    m = _story()
    m["files"] = []
    assert igm.intent_error(m) == "unsupported_attachment: instagram/story_no_media"


def test_原稿のpost_optionsのstoryはinstagramだけで真偽():
    media.validate_declarations({"media": [{"file": "a.jpg", "alt": "a"}], "post_options": {"story": True}}, "instagram")
    with pytest.raises(media.MediaError):
        media.validate_declarations({"media": [{"file": "a.jpg", "alt": "a"}], "post_options": {"story": "yes"}},
                                    "instagram")
    with pytest.raises(media.MediaError):
        media.validate_declarations({"media": [{"file": "a.jpg", "alt": "a"}], "post_options": {"story": True}},
                                    "threads")


def test_ストーリーは入れ物をSTORIESで作り_字幕もaltも送らず_同じ2段で公開する(wire):
    result, journal = invoke(wire, {"media": [{"file": "a.jpg", "alt": "告知の画像"}], "post_options": {"story": True}})
    assert result.failure == "none" and result.post_id == "18000000000000001"
    assert journal["media"]["phase"] == "published"
    [create] = [c["form"] for c in _calls(wire, "POST", f"/{IG}/media")]
    assert create["media_type"] == ["STORIES"] and create["image_url"] == [wire["grants"][0]["url"]]
    assert "caption" not in create and "alt_text" not in create and "is_carousel_item" not in create
    assert _calls(wire, "POST", "/media_publish")[0]["form"]["creation_id"] == ["9001"]


def test_画像の無いストーリーは媒体に何も投げない(wire):
    fm = {"media": [], "post_options": {"story": True}}
    result, _ = invoke(wire, fm)
    assert result.failure == "publish_definite" and result.error == "unsupported_attachment: instagram/story_no_media"
    assert wire["calls"] == []


def test_sendの道でもストーリーはdigestに入り_確認の値で出る(wire, monkeypatch):
    monkeypatch.setattr(core, "_append_run", lambda *a, **k: None)
    a = adapter(wire["url"])
    factory = lambda *args: a  # noqa: E731
    rows = [{"file": "a.jpg", "alt": "告知の画像"}]
    feed = core.send_once("alpha", text="臨時のお休み", media_rows=rows, production_flag=False, adapter_factory=factory)
    story = core.send_once("alpha", text="臨時のお休み", media_rows=rows, production_flag=False, adapter_factory=factory,
                           post_options={"story": True})
    assert feed.digest and story.digest and feed.digest != story.digest
    assert wire["calls"] == []
    result = core.send_once("alpha", text="臨時のお休み", media_rows=rows, production_flag=True, confirm=story.digest,
                            adapter_factory=factory, post_options={"story": True})
    assert result.action == "post" and result.post_id == "18000000000000001", result
    [create] = [c["form"] for c in _calls(wire, "POST", f"/{IG}/media")]
    assert create["media_type"] == ["STORIES"] and "caption" not in create
