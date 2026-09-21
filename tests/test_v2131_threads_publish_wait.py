"""2.13.1 実機直し: FINISHED の直後に出さない。4xx は Graph の番号だけ名指す。"""
import json
import pytest
from thth.adapters import threads, threads_media as tm
from tests.test_v213_threads_media import env, wire, invoke, posts


@pytest.fixture
def clock(monkeypatch):
    """凍った時計。`time.sleep` だけが進める——実機の揺れを試験に入れない。"""
    state = {'now': 0.0, 'slept': []}
    monkeypatch.setattr(tm.time, 'monotonic', lambda: state['now'])
    def sleep(seconds):
        state['slept'].append(seconds)
        state['now'] += seconds
    monkeypatch.setattr(tm.time, 'sleep', sleep)
    return state


def timeline(monkeypatch, clock):
    seen = []
    original = tm._json
    def record(adapter, method, path, params, **kwargs):
        seen.append((method, path, clock['now']))
        return original(adapter, method, path, params, **kwargs)
    monkeypatch.setattr(tm, '_json', record)
    return seen


def test_publish_waits_the_same_wait_seconds_after_finished(env, wire, monkeypatch, clock):
    env[1].wait_seconds = 30.0
    seen = timeline(monkeypatch, clock)
    result, journal, _ = invoke(env)
    assert result.post_id == '100' and journal['media']['phase'] == 'published'
    # FINISHED を読んだのは 0 秒。公開は 30 秒待ったあと 1 回だけ。
    assert [row[2] for row in seen if row[0] == 'GET'] == [0.0]
    assert [row[2] for row in seen if row[1].endswith('/threads_publish')] == [30.0]
    assert clock['slept'] == [30.0]


def test_the_wait_never_outlives_the_grant(env, wire, clock):
    # 画像の付与は 600 秒。1800 秒待てば URL が死ぬので、待たずに出す。
    env[1].wait_seconds = 1800.0
    result, _, _ = invoke(env)
    assert result.post_id == '100' and clock['slept'] == []


def test_the_veto_after_the_wait_is_honoured(env, wire, clock):
    env[1].wait_seconds = 30.0
    asked = []
    def veto():
        asked.append(clock['now'])
        return 'stopped_after_wait' if clock['now'] >= 30.0 else None
    result, journal, _ = invoke(env, veto=veto)
    assert max(asked) >= 30.0 and clock['slept'] == [30.0]
    assert result.error == 'stopped_after_wait' and result.failure == 'media_held'
    assert not posts(wire, '/threads_publish')
    assert journal['media']['phase'] == 'held' and journal['media']['reason'] == 'stopped_after_wait'


def test_publish_4xx_names_the_graph_numbers_and_nothing_else(env, wire):
    wire['publish'] = (400, {'error': {'message': 'Media ID is not available',
                                       'type': 'OAuthException', 'code': 100,
                                       'error_subcode': 2207032, 'fbtrace_id': 'AsEcReT'}})
    result, journal, _ = invoke(env)
    assert result.post_id is None and result.failure == 'media_held'
    assert result.error == 'media_publishing_http_400_code_100_subcode_2207032'
    assert journal['media']['phase'] == 'held' and journal['media']['reason'] == result.error
    blob = json.dumps(journal, ensure_ascii=False) + str(result.error)
    for secret in ('Media ID', 'OAuthException', 'AsEcReT'):
        assert secret not in blob


@pytest.mark.parametrize('body', [{}, {'error': {'message': 'nope'}}, {'error': 'nope'}])
def test_a_4xx_without_numbers_keeps_the_plain_reason(env, wire, body):
    wire['publish'] = (400, body)
    result, _, _ = invoke(env)
    assert result.error == 'media_publishing_http_400'


def test_the_env_override_reaches_the_attachment_path(monkeypatch):
    monkeypatch.setenv('THTH_THREADS_WAIT_SECONDS', '7')
    adapter = threads.ThreadsAdapter.from_account({'account': 'alpha', 'media': 'threads'}, {})
    assert adapter.wait_seconds == 7.0
