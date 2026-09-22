"""2.13.2 実機直し: container の作成は Meta の取り込みを待つ（10 秒では足りない）。

実機 2026-09-22（masaru-threads・`--media docs/media/photo.jpg`）: `POST
/{user}/threads` に `image_url` を渡すと、Meta はその POST を開いたまま
thth.me/R2 から画像を取りに行く。2.13.0 の同じ添付は通ったのに 2.13.1 は
`media_creating_failed`（phase `unknown`・`remote_ids=[]`）で落ちた——違いは
Meta の取り込みにかかった時間で、こちらの 10 秒（`adapter.timeout`）が短い。
"""
import time
import pytest
from thth.adapters import threads_media as tm
from tests.test_v213_threads_media import env, wire, invoke, posts


@pytest.fixture
def clock(monkeypatch):
    """凍った時計。`time.sleep` と偽 Graph の遅れだけが進める。"""
    state = {'now': 0.0}
    monkeypatch.setattr(tm.time, 'monotonic', lambda: state['now'])
    monkeypatch.setattr(tm.time, 'sleep', lambda seconds: state.update(now=state['now'] + seconds))
    return state


def sockets(monkeypatch):
    """各 HTTP 呼び出しに渡った socket の締切（方式・パス・秒）を並べる。"""
    seen = []
    original = tm._transport.open
    def open_request(request, timeout=None, **kwargs):
        seen.append((request.get_method(), request.full_url.split('?')[0], timeout))
        return original(request, timeout=timeout, **kwargs)
    monkeypatch.setattr(tm._transport, 'open', open_request)
    return seen


def test_a_creation_slower_than_the_adapter_timeout_still_lands(env, wire):
    # 実機の再現: 作成だけが `adapter.timeout` より長くかかる。
    env[1].timeout = 0.3
    wire['on_create'] = lambda: time.sleep(1.0)
    result, journal, _ = invoke(env)
    assert result.post_id == '100' and journal['media']['phase'] == 'published'
    assert len(posts(wire, '/threads_publish')) == 1


def test_only_the_creation_gets_the_long_budget(env, wire, monkeypatch):
    seen = sockets(monkeypatch)
    assert invoke(env)[0].post_id == '100'
    # 作成は 90 秒。状態の問い合わせも公開も adapter の 10 秒のまま。
    assert [t for m, u, t in seen if m == 'POST' and u.endswith('/threads')] == [tm.CREATE_TIMEOUT_SECONDS]
    assert [t for m, u, t in seen if m == 'GET'] == [env[1].timeout]
    assert [t for m, u, t in seen if u.endswith('/threads_publish')] == [env[1].timeout]


@pytest.mark.parametrize('raw,seconds', [
    (None, 90.0), ('120', 120.0), ('90.5', 90.5),
    ('10', 10.0), ('9', 10.0), ('0', 10.0), ('-5', 10.0),
    ('600', 600.0), ('601', 600.0), ('86400', 600.0),
    ('', 90.0), ('abc', 90.0), ('inf', 90.0), ('nan', 90.0),
])
def test_the_override_is_read_and_clamped(monkeypatch, raw, seconds):
    monkeypatch.delenv('THTH_THREADS_CREATE_TIMEOUT_SECONDS', raising=False)
    if raw is not None:
        monkeypatch.setenv('THTH_THREADS_CREATE_TIMEOUT_SECONDS', raw)
    assert tm.create_timeout() == seconds


def test_the_override_reaches_the_creation_socket(env, wire, monkeypatch):
    monkeypatch.setenv('THTH_THREADS_CREATE_TIMEOUT_SECONDS', '900')
    seen = sockets(monkeypatch)
    assert invoke(env)[0].post_id == '100'
    assert [t for m, u, t in seen if m == 'POST' and u.endswith('/threads')] == [600.0]


def test_the_poll_deadline_counts_the_time_the_creation_took(env, wire, monkeypatch, clock):
    # 画像の持ち時間は 120 秒。作成に 40 秒使ったら、問い合わせに残るのは 80 秒。
    monkeypatch.setattr(tm, 'POLL_INTERVAL', 10.0)
    wire['poll'] = [(200, {'status': 'IN_PROGRESS'})] * 60
    wire['on_create'] = lambda: clock.update(now=clock['now'] + 40.0)
    result, journal, _ = invoke(env)
    assert result.error == 'media_processing_timeout' and result.failure == 'media_held'
    # 作成を数えなければ 160 秒・12 回になる。
    assert clock['now'] == tm.POLL_SECONDS
    assert len([c for c in wire['calls'] if c[0] == 'GET']) == 8
    assert not posts(wire, '/threads_publish') and env[3]['results'] == [False]

