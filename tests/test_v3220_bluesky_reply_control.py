"""3.22.0・点検 §B-28: Bluesky の `reply_control`（threadgate を投稿と 1 つの applyWrites で作る）。"""
import json

import pytest

from thth import media
from thth.adapters import base, bluesky, bluesky_media
from tests.test_v213_bluesky_media import env, wire, invoke, calls  # noqa: F401


def _spy(monkeypatch, *, results=None):
    seen = []
    original = bluesky_media._json

    def fake(adapter, nsid, **kwargs):
        if nsid != 'com.atproto.repo.applyWrites':
            return original(adapter, nsid, **kwargs)
        request = json.loads(kwargs['data'])
        seen.append(request)
        if results is not None:
            return {'results': results}
        return {'results': [{'uri': f"at://{request['repo']}/{w['collection']}/{w['rkey']}",
                             'cid': 'c'} for w in request['writes']]}
    monkeypatch.setattr(bluesky_media, '_json', fake)
    return seen


@pytest.mark.parametrize('control,allow', [
    ('accounts_you_follow', [{'$type': 'app.bsky.feed.threadgate#followingRule'}]),
    ('mentioned_only', [{'$type': 'app.bsky.feed.threadgate#mentionRule'}]),
    ('followers_only', [{'$type': 'app.bsky.feed.threadgate#followerRule'}]),
    ('parent_post_author_only', []),
])
def test_返信の制限は投稿とthreadgateを1つの書き込みで作る(env, wire, monkeypatch, control, allow):
    seen = _spy(monkeypatch)
    result, journal, _ = invoke(env, fm={'post_options': {'reply_control': control}},
                                post=base.Post('本文'))
    assert result.failure == 'none' and journal['media']['phase'] == 'published'
    assert not calls(wire, 'createRecord')
    (request,) = seen
    post, gate = request['writes']
    assert post['$type'] == gate['$type'] == 'com.atproto.repo.applyWrites#create'
    assert post['collection'] == 'app.bsky.feed.post' and gate['collection'] == 'app.bsky.feed.threadgate'
    assert post['rkey'] == gate['rkey'] and len(post['rkey']) == 13
    assert post['value']['text'] == '本文'
    assert gate['value']['post'] == result.post_id == f"at://{request['repo']}/app.bsky.feed.post/{post['rkey']}"
    assert gate['value']['allow'] == allow and gate['value']['$type'] == 'app.bsky.feed.threadgate'


def test_everyoneはthreadgateを作らずcreateRecordのまま(env, wire, monkeypatch):
    seen = _spy(monkeypatch)
    result, _, _ = invoke(env, fm={'post_options': {'reply_control': 'everyone'}}, post=base.Post('本文'))
    assert result.post_id and seen == [] and len(calls(wire, 'createRecord')) == 1


def test_画像つきでも制限が付く(env, wire, monkeypatch):
    seen = _spy(monkeypatch)
    fm = {'media': [{'file': 'a.png', 'alt': 'alt'}], 'post_options': {'reply_control': 'mentioned_only'}}
    result, _, _ = invoke(env, fm=fm, post=base.Post('本文'))
    assert result.failure == 'none' and seen[0]['writes'][0]['value']['embed']['$type'] == 'app.bsky.embed.images'


def test_返信には付けず_何も送らずに断る(env, wire, monkeypatch):
    seen = _spy(monkeypatch)
    result, _, _ = invoke(env, fm={'post_options': {'reply_control': 'mentioned_only'}},
                          post=base.Post('本文', reply_to='at://did:plc:other/app.bsky.feed.post/parent'))
    assert result.error == 'unsupported_attachment: bluesky/reply_control_on_reply'
    assert result.failure != 'none' and seen == [] and not calls(wire, 'createRecord')


def test_応答のuriが要求と違えば出たと言わない(env, wire, monkeypatch):
    _spy(monkeypatch, results=[{'uri': 'at://did:plc:synthetic/app.bsky.feed.post/other', 'cid': 'c'},
                               {'uri': 'at://did:plc:synthetic/app.bsky.feed.threadgate/other', 'cid': 'c'}])
    result, _, _ = invoke(env, fm={'post_options': {'reply_control': 'followers_only'}}, post=base.Post('本文'))
    assert result.post_id is None and result.failure != 'none'


def test_知らない値は検査で断る(env):
    cfg, _, _ = env
    with pytest.raises(media.MediaError, match='invalid reply_control'):
        media.manifest_for({'post_options': {'reply_control': 'nobody'}}, cfg)


def test_TIDは仕様の形():
    tid = bluesky.new_tid(now_us=0, clock_id=0)
    assert tid == '2222222222222'
    tid = bluesky.new_tid()
    assert len(tid) == 13 and tid[0] in '234567abcdefghij'
    assert all(c in '234567abcdefghijklmnopqrstuvwxyz' for c in tid)
    assert bluesky.new_tid() > tid   # 単調に増える
