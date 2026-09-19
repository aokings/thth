"""2.8.0 tag contracts against local fixtures only."""
from __future__ import annotations

import json

import pytest

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


def test_max_hashtags_counts_topic_and_body_with_default_three():
    assert not tags.errors("bluesky", "#茶 #珈琲", "苦味", {"hashtags": True})
    assert "上限 3 個" in tags.errors(
        "bluesky", "#茶 #珈琲 #山菜", "苦味", {"hashtags": True})[0]
    assert "上限 1 個" in tags.errors(
        "mastodon", "#茶", "苦味", {"hashtags": True, "max_hashtags": 1})[0]
    assert tags.errors("bluesky", "本文", None,
                       {"hashtags": True, "max_hashtags": True}) == [
                           "max_hashtags: 0 以上の整数にしてください"]


def test_tag_limits_do_not_treat_url_fragment_as_a_hashtag():
    text = "https://example.invalid/#url #茶"
    assert not tags.errors("bluesky", text, None,
                           {"hashtags": True, "max_hashtags": 1})
    assert "タグが Bluesky 上限" in tags.errors(
        "bluesky", "#" + "あ" * 65, None, {"hashtags": True})[0]


def test_bluesky_tag_search_paginates_and_keeps_only_aggregates(monkeypatch):
    adapter = bluesky.BlueskyAdapter(identifier="fixture", app_password="fixture")
    calls = []
    pages = [
        {"posts": [
            {"uri": "at://one", "author": {"did": "did:a", "handle": "secret-user"},
             "record": {"text": "secret-body", "createdAt": "2026-09-19T01:00:00Z",
                        "facets": [{"features": [{"$type": "app.bsky.richtext.facet#tag", "tag": "茶"},
                                                  {"$type": "app.bsky.richtext.facet#tag", "tag": "苦味"}]}]}},
            {"uri": "at://two", "author": {"did": "did:a", "handle": "secret-user"},
             "record": {"text": "secret-body", "createdAt": "2026-09-19T02:00:00Z",
                        "tags": ["茶", "旨味"]}}], "cursor": "next"},
        {"posts": [{"uri": "at://two"},
                   {"uri": "at://three", "author": {"did": "did:b", "handle": "secret-user"},
                    "record": {"text": "secret-body", "tags": ["茶", "苦味"]}}]},
    ]
    def get(method, nsid, *, params):
        calls.append(params)
        return pages[len(calls) - 1]
    monkeypatch.setattr(adapter, "_request", get)
    result = adapter.tag_search("茶", tags=["茶"], sort="latest", pages=4,
                                since="2026-09-18", until="2026-09-20")
    assert calls[0] == {"q": "茶", "tag": ["茶"], "sort": "latest", "limit": 100,
                        "since": "2026-09-18", "until": "2026-09-20"}
    assert calls[1]["cursor"] == "next"
    assert result["n"] == 3 and result["distinct_authors"] == 2
    assert result["tagged_n"] == 3 and result["tagged_share"] == 1
    assert result["co_tags"] == [{"tag": "苦味", "n": 2}, {"tag": "旨味", "n": 1}]
    serialized = json.dumps(result, ensure_ascii=False)
    assert "secret-body" not in serialized and "secret-user" not in serialized
    with pytest.raises(Exception, match="q は空"):
        adapter.tag_search("", tags=["茶"])


def test_mastodon_tag_observation_is_instance_scoped_and_aggregate(monkeypatch):
    adapter = mastodon.MastodonAdapter(instance="https://instance.example", access_token="fixture")
    requested = []
    def get_list(path, what):
        requested.append(path)
        if "/timelines/tag/" in path:
            return [{"id": "1", "visibility": "public", "content": "secret-body",
                     "created_at": "2026-09-19T01:00:00Z",
                     "account": {"id": "a", "acct": "secret-user"},
                     "tags": [{"name": "茶"}, {"name": "苦味"}]},
                    {"id": "2", "visibility": "private", "content": "secret-body"}]
        return [{"name": "茶", "history": [{"day": "1789776000", "uses": "7",
                                                "accounts": "4"}]}]
    def get_json(path, what):
        requested.append(path)
        return {"hashtags": [{"name": "茶", "history": []},
                              {"name": "茶道", "history": []}]}
    monkeypatch.setattr(adapter, "_get_list", get_list)
    monkeypatch.setattr(adapter, "_get_json", get_json)
    result = adapter.tag_observation("茶")
    assert any("/api/v1/timelines/tag/%E8%8C%B6" in path for path in requested)
    assert any("type=hashtags" in path for path in requested)
    assert any("/api/v1/trends/tags" in path for path in requested)
    assert result["n"] == result["distinct_authors"] == 1
    assert result["co_tags"] == [{"tag": "苦味", "n": 1}]
    assert result["history"] == [{"day": "1789776000", "uses": 7, "accounts": 4}]
    assert result["observed_from"] == "https://instance.example"
    serialized = json.dumps(result, ensure_ascii=False)
    assert "secret-body" not in serialized and "secret-user" not in serialized
