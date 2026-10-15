"""3.22.0・点検 §B-19: Bluesky の取り下げ（getRecord で在るかを見てから deleteRecord を 1 回）。"""
import io
import json
import urllib.error

import pytest

from thth.adapters import base, bluesky

DID = 'did:plc:me'
URI = f'at://{DID}/app.bsky.feed.post/3kabc'


def _adapter(monkeypatch, *, get=None, delete=None):
    adapter = bluesky.BlueskyAdapter(identifier='me.example', app_password='FAKE_PASSWORD')
    monkeypatch.setattr(adapter, 'session', lambda: {'did': DID, 'handle': 'me.example',
                                                     'accessJwt': 'JWT'})
    calls = []

    def request(method, nsid, *, params=None, payload=None):
        calls.append((method, nsid, params or payload))
        if nsid == 'com.atproto.repo.getRecord':
            if get is not None:
                raise get
            return {'uri': f"at://{params['repo']}/{params['collection']}/{params['rkey']}",
                    'cid': 'bafy', 'value': {}}
        if nsid == 'com.atproto.repo.deleteRecord':
            if delete is not None and payload['collection'] == bluesky.POST_COLLECTION:
                raise delete
            return {}
        raise AssertionError(nsid)
    monkeypatch.setattr(adapter, '_request', request)
    return adapter, calls


def _http(code, name):
    return urllib.error.HTTPError('https://bsky.invalid', code, 'x', {},
                                  io.BytesIO(json.dumps({'error': name}).encode()))


def test_取り下げは確かめてから投稿とthreadgateを消す(monkeypatch):
    adapter, calls = _adapter(monkeypatch)
    assert adapter.delete_post(URI) == {'deleted': True, 'post_id': URI}
    assert [c[1] for c in calls] == ['com.atproto.repo.getRecord',
                                     'com.atproto.repo.deleteRecord',
                                     'com.atproto.repo.deleteRecord']
    where = {'repo': DID, 'collection': bluesky.POST_COLLECTION, 'rkey': '3kabc'}
    assert calls[1][2] == where
    assert calls[2][2] == {**where, 'collection': bluesky.THREADGATE_COLLECTION}


@pytest.mark.parametrize('bad', [
    'at://did:plc:other/app.bsky.feed.post/3kabc',          # 他人の投稿
    f'at://{DID}/app.bsky.feed.like/3kabc',                 # 別の collection
    f'at://{DID}/app.bsky.feed.post/../x',
    '3kabc', '',
])
def test_自分の投稿のuri以外は送らずに断る(monkeypatch, bad):
    adapter, calls = _adapter(monkeypatch)
    with pytest.raises(base.AdapterError, match='bluesky_post_id_invalid'):
        adapter.delete_post(bad)
    assert calls == []


def test_無い投稿はpost_missingでdeleteRecordを呼ばない(monkeypatch):
    adapter, calls = _adapter(monkeypatch, get=_http(400, 'RecordNotFound'))
    with pytest.raises(base.AdapterError, match='post_missing'):
        adapter.delete_post(URI)
    assert [c[1] for c in calls] == ['com.atproto.repo.getRecord']


def test_ほかの400は無いと言わない(monkeypatch):
    adapter, calls = _adapter(monkeypatch, get=_http(400, 'InvalidRequest'))
    with pytest.raises(base.AdapterError, match='bluesky_retract_http_400'):
        adapter.delete_post(URI)
    assert len(calls) == 1


def test_401はPermissionMissing(monkeypatch):
    adapter, _ = _adapter(monkeypatch, delete=_http(401, 'AuthRequired'))
    with pytest.raises(base.PermissionMissing):
        adapter.delete_post(URI)


def test_deleteRecordの失敗は取り下げたと言わない(monkeypatch):
    adapter, calls = _adapter(monkeypatch, delete=_http(500, 'InternalServerError'))
    with pytest.raises(base.AdapterError, match='取り下げたとは言えません'):
        adapter.delete_post(URI)
    assert [c[1] for c in calls].count('com.atproto.repo.deleteRecord') == 1


def test_retractの入口がBlueskyを断らない():
    from thth import adapters
    assert adapters.adapter_class('bluesky').DELETE_PERMISSION
