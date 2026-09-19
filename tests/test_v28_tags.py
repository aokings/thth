"""2.8.0 tag contracts against local fixtures only."""
from __future__ import annotations

from thth import cli, lint, tags
from thth.adapters import base, bluesky, mastodon
from tests.test_bluesky_adapter import _adapter, fake_bluesky


def test_topic_is_the_same_sent_text_and_utf8_facet():
    text = "日本語の本文"
    with fake_bluesky() as service:
        post = base.Post(text=text, topic="苦味", hashtags_allowed=True)
        result = _adapter(service).publish(post, dry_run=False)
        record = service.handler_cls.created[0]["payload"]["record"]
    assert result.error is None
    assert record["text"] == tags.prepared("bluesky", text, "苦味", hashtags=True)
    facet = next(f for f in record["facets"] if f["features"][0]["$type"].endswith("#tag"))
    assert facet["features"] == [{"$type": "app.bsky.richtext.facet#tag", "tag": "苦味"}]
    index = facet["index"]
    assert record["text"].encode("utf-8")[index["byteStart"]:index["byteEnd"]] == "#苦味".encode("utf-8")
    assert index["byteStart"] == len((text + "\n").encode("utf-8"))


def test_topic_already_present_is_not_appended_twice():
    text = "#苦味 の話"
    assert tags.prepared("bluesky", text, "苦味", hashtags=True) == text
    facet = bluesky.build_facets(text, topic="苦味")[0]
    assert facet["index"] == {"byteStart": 0, "byteEnd": len("#苦味".encode())}


def test_all_body_tags_have_non_overlapping_byte_facets():
    text = "日本語 #苦味 と #旨味 https://example.invalid/#url_fragment"
    facets = bluesky.build_facets(text, include_tags=True)
    tags_found = [f for f in facets if f["features"][0]["$type"].endswith("#tag")]
    assert [f["features"][0]["tag"] for f in tags_found] == ["苦味", "旨味"]
    for facet, wanted in zip(tags_found, ("#苦味", "#旨味")):
        index = facet["index"]
        assert text.encode()[index["byteStart"]:index["byteEnd"]] == wanted.encode()
    assert len(facets) == 3  # one URL link, two tags


def test_url_fragment_does_not_suppress_topic_append():
    text = "記事 https://example.invalid/#個人開発"
    effective = tags.prepared("bluesky", text, "個人開発", hashtags=True)
    assert effective.endswith("\n#個人開発")
    facets = bluesky.build_facets(effective, topic="個人開発", include_tags=True)
    tag_facets = [f for f in facets if f["features"][0]["$type"].endswith("#tag")]
    assert len(tag_facets) == 1
    assert effective.encode()[tag_facets[0]["index"]["byteStart"]:] == "#個人開発".encode()


def test_punctuation_topic_and_combining_mark_are_single_facets():
    text = "#a.b と #か\u3099"
    assert tags.prepared("bluesky", text, "a.b", hashtags=True) == text
    facets = bluesky.build_facets(text, topic="a.b", include_tags=True)
    tag_facets = [f for f in facets if f["features"][0]["$type"].endswith("#tag")]
    assert [f["features"][0]["tag"] for f in tag_facets] == ["a.b", "か\u3099"]
    assert tags.topic_error("bluesky", "あ" * 64) is None
    assert tags.topic_error("bluesky", "あ" * 65) == "topic_too_long(65)"


def test_mastodon_topic_uses_one_final_line(monkeypatch):
    seen = []
    adapter = mastodon.MastodonAdapter(instance="https://example.invalid", access_token="fixture")
    monkeypatch.setattr(adapter, "_request", lambda method, path, **kwargs: seen.append(kwargs) or
                        {"id": "123", "url": "https://example.invalid/@u/123"})
    result = adapter.publish(base.Post(text="本文", topic="茶", hashtags_allowed=True), dry_run=False)
    assert result.error is None
    assert seen[0]["data"]["status"] == "本文\n#茶"


def test_topic_false_warns_and_does_not_append():
    assert tags.prepared("bluesky", "本文", "茶", hashtags=False) == "本文"
    assert tags.errors("bluesky", "本文", "茶", {"hashtags": False}) == [
        "warning: この媒体では topic が効きません（hashtags: false）"]


def test_topic_limit_and_length_include_appended_tag(tmp_path, monkeypatch):
    path = tmp_path / "draft.md"
    path.write_text("---\nthth: 1\naccount: sample\npublish_at: 2026-09-20T09:00:00+09:00\n"
                    "topic: 苦味\n---\n## bluesky\n" + "あ" * 298)
    monkeypatch.setattr(lint, "_account_cfg_or_none", lambda _: {"media": "bluesky", "hashtags": True})
    assert any("length:" in error for error in lint.lint_file(str(path)))
    assert lint.preview_file(str(path)).endswith("\n#苦味")
    assert tags.topic_error("bluesky", "あ" * 65) == "topic_too_long(65)"
    assert tags.topic_error("mastodon", "two words") is not None
    assert tags.errors("bluesky", "🫠" * 800, None, {"hashtags": True})[0].startswith("length:")
    path.write_text(path.read_text().replace("あ" * 298, "本文"))
    monkeypatch.setattr(cli.accounts_mod, "load_account", lambda _: {"media": "bluesky", "hashtags": True})
    one, error = cli._prepare_one(str(path))
    assert error is None
    assert one["text"] == lint.preview_file(str(path))
    assert one["text"].endswith("\n#苦味")
