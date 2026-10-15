"""3.22.0・点検 §B-15: Bluesky・Mastodon の言及も `inbox` に保存する（それまで Threads だけ）。"""
import datetime
import json
import os
import urllib.parse

from thth import collect, jst
from thth.adapters import bluesky, mastodon

NOW = datetime.datetime(2026, 10, 15, 12, 0, tzinfo=jst.JST)


def _bsky(monkeypatch):
    adapter = bluesky.BlueskyAdapter(identifier='reader', app_password='FAKE_PASSWORD')

    def request(method, nsid, params):
        assert nsid == 'app.bsky.notification.listNotifications'
        return {'notifications': [
            {'uri': f'at://did:plc:a/app.bsky.feed.post/{reason}', 'reason': reason,
             'record': {'text': f'BODY {reason}',
                        'reply': {'parent': {'uri': 'at://did:plc:me/app.bsky.feed.post/p'},
                                  'root': {'uri': 'at://did:plc:me/app.bsky.feed.post/r'}}
                        if reason == 'mention' else None},
             'author': {'did': 'did:plc:a', 'handle': 'a.example'},
             'indexedAt': '2026-10-15T01:00:00Z'}
            for reason in ('reply', 'mention', 'quote', 'like')]}
    monkeypatch.setattr(adapter, '_request', request)
    return adapter


def test_blueskyはinboxを持ち_言及と引用だけを流す(monkeypatch):
    adapter = _bsky(monkeypatch)
    assert 'inbox' in adapter.capabilities()
    seen = {}
    original = adapter.mentions

    def spy(*, since=None):
        seen['since'] = since
        return original(since=since)
    monkeypatch.setattr(adapter, 'mentions', spy)
    rows = adapter.inbox()
    # 返信（reply）は返信の台帳に入るので inbox には置かない。いいねは言及ではない。
    assert [r['mention_kind'] for r in rows] == ['mention', 'quote']
    assert 'kind' not in rows[0]
    assert seen['since'] == bluesky.BlueskyAdapter.INBOX_WINDOW
    assert rows[0]['replied_to'] == 'at://did:plc:me/app.bsky.feed.post/p'
    assert rows[0]['root_post'] == 'at://did:plc:me/app.bsky.feed.post/r'
    # 返信でない投稿の根は自分自身。
    assert rows[1]['root_post'] == rows[1]['message_id']
    assert rows[0]['text'] == 'BODY mention'


def test_mastodonはinboxで公開の言及を流す(monkeypatch):
    adapter = mastodon.MastodonAdapter(instance='https://fixture.invalid')
    assert 'inbox' in adapter.capabilities()

    def request(method, path, **kwargs):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
        if 'max_id' in query:
            return []
        return [{'id': str(100 - i), 'type': 'mention', 'created_at': jst.iso(NOW),
                 'status': {'id': str(200 - i), 'visibility': vis, 'content': '<p>hi</p>',
                            'account': {'acct': 'other'}, 'url': 'https://fixture.invalid/p'}}
                for i, vis in enumerate(('public', 'direct'))]
    monkeypatch.setattr(adapter, '_request', request)
    rows = adapter.inbox()
    assert [r['message_id'] for r in rows] == ['200']   # direct は読まない（B-27 のまま）
    assert rows[0]['mention_kind'] == 'mention' and rows[0]['text'] == 'hi'


def test_collectがblueskyの言及をinboxに冪等に書く(monkeypatch, tmp_path):
    adapter = _bsky(monkeypatch)
    errors, logs = [], []
    inbox_dir = str(tmp_path / 'inbox')
    os.makedirs(inbox_dir)
    for _ in range(2):
        touched, state = collect._collect_inbox({}, adapter, now=NOW, errors=errors,
                                                log=logs.append, inbox_dir=inbox_dir)
        assert state['state'] == collect.INBOX_STATE_OK
    assert errors == []
    path = os.path.join(inbox_dir, '2026-10.ndjson')
    rows = [json.loads(line) for line in open(path, encoding='utf-8')]
    assert [r['mention_kind'] for r in rows] == ['mention', 'quote']   # 2 度目は増えない
    assert all(r['kind'] == 'inbox' and r['medium'] == 'bluesky' for r in rows)
