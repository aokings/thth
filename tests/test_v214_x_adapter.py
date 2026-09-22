"""2.14.0: X の本文・返信・削除・一覧・429・月間投稿数の枠。

偽の X は loopback。**本物の api.x.com も本物の予算も触らない。**
"""
import io
import json
import urllib.error

import pytest

from thth import accounts, budget_x, budget_x_posts, media_delivery, server_files
from thth.adapters import base, x as x_adapter
from tests.helpers.fake_x import calls
from tests.test_v214_x_media import env, invoke, set_post_budget, wire  # noqa: F401
from tests.test_v213_media_foundation import png


def counts(account='alpha'):
    period = budget_x_posts.month(budget_x_posts.now())
    with budget_x_posts._counts_locked(account, period) as fd:
        return budget_x_posts.totals(budget_x_posts._read_counts(fd, account, period))


def set_read_budget(amount='10'):
    value = budget_x.empty()
    value['policy'] = budget_x.policy(amount)
    with server_files.directory(budget_x.folder(), create=True, private=True) as fd:
        budget_x._save(fd, value)


def text_post(env, post=None):
    return env[1].publish(post or base.Post('本文だけ'), dry_run=False)


# ----- 本文・返信 ------------------------------------------------------------

def test_a_text_post_sends_one_tweet_and_settles_one_slot(env, wire):
    result = text_post(env)
    assert result.post_id == wire['next_post'] and result.failure == 'none'
    assert result.url == 'https://x.com/demo/status/' + wire['next_post']
    assert wire['tweets'] == [{'text': '本文だけ'}]
    assert counts() == {'reserved': 0, 'dispatched': 0, 'settled': 1, 'unknown': 0, 'released': 0}


def test_a_reply_carries_the_reference_and_nothing_else(env, wire):
    result = text_post(env, base.Post('返事', reply_to='1900000000000000000'))
    assert result.post_id
    assert wire['tweets'] == [{'text': '返事',
                               'reply': {'in_reply_to_tweet_id': '1900000000000000000'}}]


def test_a_dry_run_touches_nothing(env, wire):
    result = env[1].publish(base.Post('下見'), dry_run=True)
    assert result.failure == 'none' and result.post_id is None
    assert wire['calls'] == [] and counts()['settled'] == 0


def test_an_empty_body_without_media_is_refused_before_the_request(env, wire):
    result = text_post(env, base.Post('   '))
    assert result.error == 'x_text_required' and result.failure == 'publish_vetoed'
    assert wire['calls'] == [] and counts() == {'reserved': 0, 'dispatched': 0, 'settled': 0,
                                                'unknown': 0, 'released': 1}


# ----- 数え方 ----------------------------------------------------------------

def test_the_body_is_counted_in_code_points_with_a_provisional_limit():
    from thth import adapters, queuefile
    assert adapters.count_text_for('x', 'ab🌿') == 3
    assert queuefile.limit_for('x') == x_adapter.MAX_TEXT_CODEPOINTS == 280
    assert x_adapter.LENGTH_NOTE == 'warning: x_length_weighting_unverified'


def test_lint_says_the_counting_rule_is_unverified(env):
    notes = media_delivery.lint_notes(env[0], {'media': [{'file': 'a.png', 'alt': '点'}]})
    assert x_adapter.LENGTH_NOTE in notes


# ----- 削除 ------------------------------------------------------------------

def test_retract_deletes_one_post(env, wire):
    assert env[1].delete_post('1900000000000000001') == {'deleted': True,
                                                         'post_id': '1900000000000000001'}
    assert calls(wire, 'DELETE', '/2/tweets/1900000000000000001')


def test_retract_refuses_a_post_id_of_the_wrong_shape(env, wire):
    with pytest.raises(base.AdapterError, match='x_post_id_invalid'):
        env[1].delete_post('at://not-a-number')
    assert wire['calls'] == []


# ----- 読む口（読取予算の中でだけ） ------------------------------------------

def test_whoami_and_posts_need_the_read_budget(env, wire):
    with pytest.raises(budget_x.BudgetError, match='budget_exhausted'):
        env[1].whoami()
    assert wire['calls'] == []
    set_read_budget()
    assert env[1].whoami() == {'user_id': '123', 'username': 'demo'}
    wire['posts'] = {'data': [{'id': '1900000000000000009', 'created_at': '2026-09-22T00:00:00Z',
                               'text': '外で出したもの'}],
                     'meta': {'result_count': 1}}
    rows = env[1].recent_posts(limit=5)
    assert rows == [{'post_id': '1900000000000000009', 'timestamp': '2026-09-22T00:00:00Z',
                     'url': 'https://x.com/demo/status/1900000000000000009',
                     'text': '外で出したもの', 'topic': None}]
    assert calls(wire, 'GET', '/2/users/123/tweets')
    # 一覧のために whoami をもう一度叩かない（1 回の一覧で 2 回課金しない）。
    assert len(calls(wire, 'GET', '/2/users/me')) == 1


def test_an_empty_page_is_not_a_failure_and_still_settles_the_read(env, wire):
    set_read_budget()
    assert env[1].recent_posts() == []
    report = budget_x.report()
    assert report['spent_estimate_usd'] == '0.010' and report['held_usd'] == '0'


# ----- 429・不明 --------------------------------------------------------------

def test_429_on_the_text_path_is_static_and_keeps_the_reset(env, wire, monkeypatch):
    original = x_adapter.request

    def request(adapter, method, path, **kwargs):
        if path == '/2/tweets':
            raise urllib.error.HTTPError(adapter.base_url + path, 429, 'Too Many Requests',
                                         {'x-rate-limit-reset': '1790000000'}, io.BytesIO(b'{}'))
        return original(adapter, method, path, **kwargs)
    monkeypatch.setattr(x_adapter, 'request', request)
    result = text_post(env)
    assert result.error == 'provider_rate_limited' and result.failure == 'publish_definite'
    assert result.api_diagnostic == {'rate_limit_reset': 1790000000}
    # 発射したので枠は返らない（向こうに出ている可能性を 0 と見なさない）。
    assert counts()['unknown'] == 1 and counts()['settled'] == 0


def test_an_unknown_text_outcome_is_counted_as_unknown(env, wire, monkeypatch):
    original = x_adapter.request

    def request(adapter, method, path, **kwargs):
        if path == '/2/tweets':
            raise urllib.error.HTTPError(adapter.base_url + path, 503, 'Unavailable', {},
                                         io.BytesIO(b'{}'))
        return original(adapter, method, path, **kwargs)
    monkeypatch.setattr(x_adapter, 'request', request)
    result = text_post(env)
    assert result.failure == 'publish_ambiguous' and result.error == 'x_publish_http_503'
    assert counts()['unknown'] == 1


# ----- 月間投稿数の枠 ---------------------------------------------------------

def test_the_default_zero_budget_refuses_before_any_request(env, wire):
    set_post_budget(0)
    result = text_post(env)
    assert result.error == budget_x_posts.EXHAUSTED and result.failure == 'publish_vetoed'
    assert wire['calls'] == [] and counts()['settled'] == 0
    assert budget_x_posts.report()['post_refusal'] == 'post_budget_exhausted'
    assert 'post_budget_exhausted' in budget_x_posts.report()['cannot_say']


def test_the_cap_stops_the_next_post_and_the_attachment_path_too(env, wire):
    set_post_budget(1)
    assert text_post(env).post_id
    second = text_post(env)
    assert second.error == budget_x_posts.EXHAUSTED and len(wire['tweets']) == 1
    result, _, _ = invoke(env)
    assert result.error == budget_x_posts.EXHAUSTED and result.failure == 'publish_vetoed'
    assert len(wire['tweets']) == 1 and not calls(wire, 'POST', '/initialize')


def test_an_unknown_outcome_keeps_using_the_cap(env, wire, monkeypatch):
    set_post_budget(1)
    original = x_adapter.request
    broken = [True]

    def request(adapter, method, path, **kwargs):
        if path == '/2/tweets' and broken[0]:
            raise urllib.error.HTTPError(adapter.base_url + path, 500, 'oops', {}, io.BytesIO(b'{}'))
        return original(adapter, method, path, **kwargs)
    monkeypatch.setattr(x_adapter, 'request', request)
    assert text_post(env).failure == 'publish_ambiguous'
    broken[0] = False
    # 結果が分からない 1 本は枠を使ったまま。**自動で投げ直さない**のと表裏。
    assert text_post(env).error == budget_x_posts.EXHAUSTED
    row = budget_x_posts.report('alpha')['accounts']['alpha']
    assert row == {'posted': 0, 'unknown': 1, 'held': 0, 'used': 1, 'remaining': 0}


def test_the_counts_file_is_private_and_named_by_the_utc_month(env):
    text_post(env)
    period = budget_x_posts.month(budget_x_posts.now())
    path = (accounts.state_dir_for('alpha') + '/x-posts-' + period + '.json')
    import os
    import stat
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    value = json.loads(open(path).read())
    assert value['media'] == 'x' and value['month_utc'] == period
    assert [row['state'] for row in value['entries'].values()] == ['settled']


# ----- 不明のあとは、次の run が 1 本も投げない -------------------------------

def test_after_an_unknown_the_next_send_refuses_without_touching_the_provider(
        env, wire, monkeypatch, capsys):
    from thth import cli, core, inflight, media, read_coordination
    monkeypatch.setattr(core, '_append_run', lambda *a, **k: None)
    monkeypatch.setattr(read_coordination, 'invoke',
                        lambda args, name, fn=None: fn() if fn else args.func(args))
    state = accounts.state_dir_for('alpha')
    manifest = media.manifest_for({'media': [{'file': 'a.png', 'alt': '説明'}]}, env[0])
    inflight.write(state, file='(send)', started='2030-01-01T00:00:00+09:00')
    media_delivery._progress('alpha', manifest, env[1].base_url, 'unknown',
                             remote_ids=['101'], reason='media_publishing_http_503')
    before = inflight.read(state)
    assert cli.main(['send', 'alpha', '--media', 'a.png', '--alt', '説明']) == 1
    output = capsys.readouterr()
    assert 'inflight が残っています' in output.out + output.err
    assert wire['calls'] == [] and inflight.read(state) == before
