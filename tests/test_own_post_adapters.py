"""設計 持ち主の依頼で消す §3: adapter の `own_post`（id 1 件を読み、持ち主を比べる）。媒体ごと。

**網は叩かない**——各 adapter の読む口（`_get`・`_get_json`・`_owned_get`・`_request`・`session`）を
差し替え、返す行の持ち主だけを変える。柵: 自分の物でなければ `PostNotOwn`、読めなければ
`PostUnreadable`（呼ぶ側の `thth retract` はどちらでも DELETE を呼ばない）。
"""
from __future__ import annotations

import urllib.error

import pytest

from thth.adapters import base
from thth.adapters import bluesky, facebook, instagram, mastodon, threads, x


# --------------------------------------------------------------------------
# 組み立て（読む口を差し替える）
# --------------------------------------------------------------------------

def _threads(row=None, error=None):
    a = threads.ThreadsAdapter(base_url="http://127.0.0.1:9", access_token="t", user_id="12345",
                               scopes=["threads_basic"], scopes_source="response")
    a.handle = "me"

    def get(path, params):
        if error:
            raise error
        return row
    a._get = get
    return a


def _x(row=None, error=None):
    a = x.XAdapter(base_url="http://127.0.0.1:9", access_token="t", user_id="111", username="me")

    def owned(chunk, *, private=False, fields=None):
        assert fields == x.XAdapter.OWN_POST_FIELDS and chunk == ["1800000000000000001"]
        if error:
            raise error
        return row
    a._owned_get = owned
    return a


def _mastodon(row=None, error=None):
    a = mastodon.MastodonAdapter(instance="http://127.0.0.1:9", access_token="t", account_id="9000")

    def get_json(path, what):
        if error:
            raise error
        return row
    a._get_json = get_json
    return a


def _bluesky(row=None, error=None):
    a = bluesky.BlueskyAdapter(service="http://127.0.0.1:9", identifier="me.bsky.social", app_password="p")
    a.session = lambda: {"did": "did:plc:me", "handle": "me.bsky.social"}

    def request(method, name, params=None, payload=None):
        assert method == "GET" and name == "com.atproto.repo.getRecord"
        if error:
            raise error
        return row
    a._request = request
    return a


def _facebook(row=None, error=None):
    a = facebook.FacebookAdapter(base_url="http://127.0.0.1:9", access_token="t", page_id="500")

    def get(path, what, *, params=None, permission=None):
        if error:
            raise error
        return row
    a._get = get
    return a


def _instagram(row=None, error=None, stories=()):
    a = instagram.InstagramAdapter(base_url="http://127.0.0.1:9", access_token="t", ig_user_id="1784",
                                   page_id="500")

    def get(path, what, *, params=None, permission=None):
        if error:
            raise error
        return row
    a._get = get
    a.recent_stories = lambda limit=25: list(stories)
    return a


BSKY_ID = "at://did:plc:me/app.bsky.feed.post/3kabc"
CASES = {
    # 媒体: (作る関数, id, 自分の行, 他人の行)
    "threads": (_threads, "777",
                {"id": "777", "owner": {"id": "12345"}, "text": "本文", "timestamp": "t", "permalink": "u"},
                {"id": "777", "owner": {"id": "99999"}, "text": "他人", "timestamp": "t"}),
    "x": (_x, "1800000000000000001",
          {"data": [{"id": "1800000000000000001", "author_id": "111", "text": "本文", "created_at": "t"}]},
          {"data": [{"id": "1800000000000000001", "author_id": "222", "text": "他人", "created_at": "t"}]}),
    "mastodon": (_mastodon, "113000000000000001",
                 {"id": "113000000000000001", "account": {"id": "9000"}, "content": "<p>本文</p>",
                  "visibility": "private", "created_at": "t", "url": "u"},
                 {"id": "113000000000000001", "account": {"id": "1"}, "content": "<p>他人</p>",
                  "visibility": "public", "created_at": "t"}),
    "facebook": (_facebook, "500_1",
                 {"id": "500_1", "message": "本文", "created_time": "t", "from": {"id": "500"}},
                 {"id": "500_1", "message": "ページに書かれた他人の投稿", "created_time": "t",
                  "from": {"id": "42"}}),
    "instagram": (_instagram, "17890000000000001",
                  {"id": "17890000000000001", "caption": "本文", "timestamp": "t", "owner": {"id": "1784"}},
                  {"id": "17890000000000001", "caption": "他人", "timestamp": "t", "owner": {"id": "9"}}),
}


@pytest.mark.parametrize("media", sorted(CASES))
def test_自分の投稿は読めて形がそろう(media):
    make, post_id, mine, _other = CASES[media]
    row = make(mine).own_post(post_id)
    assert row["post_id"] == post_id and row["kind"] in ("post", "story", "repost")
    assert "本文" in row["text"]


@pytest.mark.parametrize("media", sorted(CASES))
def test_他人の投稿はPostNotOwn(media):
    """柵: 持ち主の比べを外すと赤（他人の投稿を自分の物として返してしまう）。"""
    make, post_id, _mine, other = CASES[media]
    with pytest.raises(base.PostNotOwn):
        make(other).own_post(post_id)


@pytest.mark.parametrize("media", sorted(CASES))
def test_読めなければPostUnreadable(media):
    make, post_id, _mine, _other = CASES[media]
    with pytest.raises(base.PostUnreadable):
        make(error=TimeoutError("timed out")).own_post(post_id)


def test_Facebookはページに書かれた他人の投稿をPostNotOwn():
    """ページの鍵はページに書かれた他人の投稿も消せることがある（設計 §3）。読みの比べが本線。"""
    make, post_id, _mine, other = CASES["facebook"]
    with pytest.raises(base.PostNotOwn):
        make(other).own_post(post_id)
    no_from = dict(other)
    no_from.pop("from")
    with pytest.raises(base.PostNotOwn):
        make(no_from).own_post(post_id)


def test_Blueskyは他人のDIDの_uriをPostNotOwn():
    a = _bluesky({"uri": BSKY_ID, "value": {"text": "本文", "createdAt": "t"}})
    with pytest.raises(base.PostNotOwn):
        a.own_post("at://did:plc:other/app.bsky.feed.post/3kabc")
    row = a.own_post(BSKY_ID)
    assert row["text"] == "本文" and row["url"] == "https://bsky.app/profile/me.bsky.social/post/3kabc"
    with pytest.raises(base.PostUnreadable):
        _bluesky(error=urllib.error.HTTPError("http://127.0.0.1:9", 500, "boom", {}, None)).own_post(BSKY_ID)


def test_Mastodonはフォロワー限定も本人に見せdirectは見せない():
    make, post_id, mine, _other = CASES["mastodon"]
    assert make(mine).own_post(post_id)["text"] == "本文"
    with pytest.raises(base.PostUnreadable):
        make(dict(mine, visibility="direct")).own_post(post_id)


def test_Instagramはownerが無ければ今のストーリーズに探す():
    sid = "17890000000000002"
    story = {"story_id": sid, "timestamp": "t", "url": "u", "media_type": "IMAGE"}
    a = _instagram({"id": sid, "timestamp": "t"}, stories=[story])
    assert a.own_post(sid)["kind"] == "story"
    with pytest.raises(base.PostNotOwn):
        _instagram({"id": sid, "timestamp": "t"}, stories=[]).own_post(sid)
    with pytest.raises(base.PostUnreadable):
        _instagram(error=TimeoutError("x"), stories=[]).own_post(sid)
    assert _instagram(error=TimeoutError("x"), stories=[story]).own_post(sid)["kind"] == "story"


def test_Threadsの再投稿はkind_repost():
    make, post_id, mine, _other = CASES["threads"]
    assert make(dict(mine, media_type="REPOST_FACADE")).own_post(post_id)["kind"] == "repost"


def test_Xは自分の投稿の読みの台帳に載せる():
    """X の読みは有償（Owned Reads・1 本 0.001 USD）。`_owned_get` を通すことを source で固定する。"""
    import inspect
    assert "_owned_get(" in inspect.getsource(x.XAdapter.own_post)


def test_持たない媒体の既定はPostUnreadable():
    class Bare(base.Adapter):
        pass
    with pytest.raises(base.PostUnreadable):
        Bare().own_post("1")


def test_Threadsの消えたidは権限の不足でなく見つからないと言う(monkeypatch):
    """11/23 本物: 消した直後の id を読むと Meta は 400「does not exist … missing permissions」。"""
    from thth.adapters import base as adapter_base
    from thth.adapters import threads as threads_mod
    ad = threads_mod.ThreadsAdapter(base_url="http://127.0.0.1:9", access_token="x",
                                    user_id="12345", wait_seconds=0, timeout=1.0)

    def gone(*_a, **_k):
        raise adapter_base.PermissionMissing("threads_basic", "HTTP 400 Unsupported get request. Object with ID '1' does not exist, cannot be loaded due to missing permissions")
    monkeypatch.setattr(ad, "_read", gone)
    import pytest
    with pytest.raises(adapter_base.PostUnreadable) as caught:
        ad.own_post("1")
    assert "見つかりません" in str(caught.value)
