"""2.13.2 実機直し: 作成の時間切れは「出ていないこと」ではない。

Meta は `POST /{user}/threads` を開いたまま媒体を取りに行くので、こちらが
socket を諦めたあとに向こうで container ができていることがある。phase は
`unknown` のまま——そのうえで理由を `media_creating_timeout` と名指し、
`thth send` が「確かめてから消す」と言えるようにする。
"""
import threading
import urllib.error
import pytest
from thth import media_delivery
from thth.adapters import threads_media as tm
from tests.test_v213_media_foundation import png
from tests.test_v213_threads_media import env, wire, invoke, posts

STEP = '次の一歩: 媒体が画像/動画を取り込むのに時間がかかっています。しばらくして inflight を確認し、投稿が無ければ消してから再実行'


@pytest.fixture
def stall():
    """偽 Graph を黙らせる掛け金。試験が終わったら必ず外す（daemon を残さない）。"""
    gate = threading.Event()
    try:
        yield lambda: gate.wait(10)
    finally:
        gate.set()


@pytest.mark.parametrize('error,timed_out', [
    (TimeoutError('slow'), True),
    (urllib.error.URLError(TimeoutError('slow')), True),
    (urllib.error.URLError(ConnectionResetError('reset')), False),
    (OSError('broken'), False),
    (urllib.error.HTTPError('http://x.invalid', 408, 'Request Timeout', None, None), False),
])
def test_only_a_socket_timeout_counts_as_one(error, timed_out):
    assert tm.socket_timed_out(error) is timed_out


def test_a_creation_that_never_answers_is_unknown_and_named_as_a_timeout(env, wire, monkeypatch, stall):
    monkeypatch.setattr(tm, 'create_timeout', lambda: 0.3)
    wire['on_create'] = stall
    result, journal, _ = invoke(env)
    assert result.post_id is None and result.failure == 'media_ambiguous'
    assert result.error == 'media_creating_timeout'
    assert journal['media']['phase'] == 'unknown' and journal['media']['reason'] == 'media_creating_timeout'
    # container ができたかどうかは分からない: 覚えている id は無く、公開もしない。
    assert journal['media']['remote_ids'] == [] and not posts(wire, '/threads_publish')
    # 付与も引き上げない（引き上げれば向こうにできた container が確実に死ぬ）。
    assert env[3]['results'] == []
    assert media_delivery.next_step(result.error) == STEP


def test_the_carousel_parent_timeout_is_named_the_same_way(env, wire, monkeypatch, stall):
    (env[2] / 'b.png').write_bytes(png())
    monkeypatch.setattr(tm, 'create_timeout', lambda: 0.3)
    created = []
    def on_create():
        created.append(1)
        if len(created) == 3:
            stall()
    wire['on_create'] = on_create
    fm = {'media': [{'file': 'a.png', 'alt': '点'}, {'file': 'b.png', 'alt': '別の点'}]}
    result, journal, _ = invoke(env, fm)
    assert result.error == 'media_creating_carousel_timeout' and result.failure == 'media_ambiguous'
    assert journal['media']['phase'] == 'unknown' and journal['media']['remote_ids'] == ['11', '12']
    assert not posts(wire, '/threads_publish') and env[3]['results'] == []
    assert media_delivery.next_step(result.error) == STEP


def test_other_creation_failures_keep_the_generic_reason(env, wire, monkeypatch):
    original = tm._transport.open
    def open_request(request, timeout=None, **kwargs):
        if request.get_method() == 'POST' and request.full_url.endswith('/threads'):
            raise urllib.error.URLError(ConnectionResetError('reset'))
        return original(request, timeout=timeout, **kwargs)
    monkeypatch.setattr(tm._transport, 'open', open_request)
    result, journal, _ = invoke(env)
    assert result.error == 'media_creating_failed' and result.failure == 'media_ambiguous'
    assert journal['media']['phase'] == 'unknown'
    assert media_delivery.next_step(result.error) is None


def test_a_slow_poll_is_not_a_creation_timeout(env, wire, monkeypatch, stall):
    # 時間切れを名指すのは作成だけ。問い合わせの時間切れは既存の扱いのまま。
    env[1].timeout = 0.3
    wire['on_poll'] = stall
    result, journal, _ = invoke(env)
    assert result.error == 'media_processing_failed' and result.failure == 'media_held'
    assert journal['media']['phase'] == 'held' and journal['media']['remote_ids'] == ['11']
