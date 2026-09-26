"""利用者 scope の読む口（設計 3.14.0 §3.2・`thth/server_reads.py`）。

- 8 つの operation が CLI の `--json` と同じ形を返す（CLI の関数を呼ぶだけ）。
- 資格の範囲の外は `scope_unavailable`・止まった口座（安全装置）でも読める。
- 断りは静的な符丁。媒体の失敗は `upstream_unavailable` と短い理由（token・URL を含めない）。
- `collect` は口座ごとに 10 分に 1 回まで。
- MCP の道具に同じ名前で写り、`writes: false` の資格でも出る。
"""
from __future__ import annotations

import argparse
import datetime
import json
import stat

import pytest

from thth import adapters, guard, jst, server_reads
from thth.report_service import ReportServiceError, execute_report
from tests.test_v212_server_writes import env  # noqa: F401

OPS = ('posts', 'replies', 'measured', 'collect', 'mentions', 'topics_search', 'profile', 'location_search')
SECRET_URL = 'https://graph.threads.net/v1.0/me?access_token=SECRETSECRET'


def call(env, operation, **fields):
    return execute_report(env['context'], dict(operation=operation, account='alpha', **fields))


def refused(env, operation, **fields):
    with pytest.raises(ReportServiceError) as caught:
        call(env, operation, **fields)
    return caught.value


class FakeAdapter:
    fail = None

    def _maybe_fail(self):
        if self.fail is not None:
            raise self.fail

    def recent_posts(self, limit):
        self._maybe_fail()
        return [dict(post_id=str(1000 + i), timestamp='2026-09-26T10:00:00+0000', url=f'https://www.threads.net/@demo/post/{i}',
                     text=f'本文 {i}', topic=None) for i in range(limit)]

    def keyword_search(self, q, search_type, limit):
        self._maybe_fail()
        return [dict(message_id='77', username='someone', timestamp='2026-09-26T10:00:00+0000',
                     text='あ' * 200, permalink='https://www.threads.net/@someone/post/77', has_replies=True)]

    def mentions(self, since=None):
        self._maybe_fail()
        return [dict(message_id=str(i), username='u', timestamp='t', text='言及') for i in range(30)]

    def profile_lookup(self, username):
        self._maybe_fail()
        return {'username': username.lstrip('@'), 'follower_count': 3}

    def location_search(self, query, limit):
        self._maybe_fail()
        return [{'id': '1', 'name': query}]


@pytest.fixture
def media(env, monkeypatch):
    fake = FakeAdapter()
    monkeypatch.setattr(adapters, 'make_adapter', lambda *a, **k: fake)
    token = env['root'] / 'secrets' / 'alpha.json'
    value = json.loads(token.read_text())
    value['scopes'] += ['threads_keyword_search', 'threads_manage_mentions', 'threads_profile_discovery',
                        'threads_location_tagging', 'threads_read_replies', 'threads_manage_insights']
    token.write_text(json.dumps(value))
    return fake


# --------------------------------------------------------------------------
# 正常: CLI の `--json` と同じ形
# --------------------------------------------------------------------------

def _cli_json(capsys, func, **fields):
    rc = func(argparse.Namespace(json=True, **fields))
    return rc, json.loads(capsys.readouterr().out)


def test_postsはthth_postsのjsonと同じ形_limitは既定20で上限100(env, media, capsys):
    got = call(env, 'posts')
    assert len(got['posts']) == 20 and got['error'] is None
    from thth import cli
    rc, printed = _cli_json(capsys, cli.cmd_posts, account='alpha', limit=20)
    assert rc == 0 and printed == got
    assert len(call(env, 'posts', limit=3, refresh=True)['posts']) == 3
    for bad in (0, 101, '5', True):
        assert str(refused(env, 'posts', limit=bad)) in ('invalid_options', 'invalid_request')


def test_repliesとmeasuredは記録から読む(env, media, capsys, monkeypatch):
    from thth import cli
    replies = call(env, 'replies')
    rc, printed = _cli_json(capsys, cli.cmd_replies, account='alpha', post=None, refresh=False, wait=0)
    assert printed == replies and 'refresh' not in replies
    measured = call(env, 'measured')
    rc, printed = _cli_json(capsys, cli.cmd_measured, account='alpha', post=None)
    assert rc == 0 and printed == measured
    assert call(env, 'measured', post_id='123')['posts'] == []


def test_repliesのrefreshは取り直しを添え_自由文からpathとURLを落とす(env, media, monkeypatch):
    from thth import collect
    seen = {}

    def refresh(account, *, post_id=None, wait=0, log=print):
        seen.update(account=account, post_id=post_id)
        log('人向けの行')
        return {'account': account, 'requested': 1, 'fetched': 0, 'new_replies': 0, 'skipped': None,
                'saved': False, 'remote': 'unknown', 'checked_at': 'x',
                'failed': [{'post_id': '1', 'reason': 'HTTP 500 ' + SECRET_URL}],
                'errors': ['repo を同期できません: /srv/thth/repos/_server/alpha fatal ' + SECRET_URL]}
    monkeypatch.setattr(collect, 'refresh_replies', refresh)
    got = call(env, 'replies', refresh=True, post_id='12345')
    assert seen == {'account': 'alpha', 'post_id': '12345'}
    text = json.dumps(got, ensure_ascii=False)
    assert 'SECRETSECRET' not in text and 'graph.threads.net' not in text and '/srv/thth' not in text
    assert got['refresh']['errors'][0].startswith('repo を同期できません') and got['refresh']['failed'][0]['post_id'] == '1'


def test_mentionsはthth_mentionsのjsonと同じ形でrunsに残す(env, media, capsys):
    from thth import threads_read_cli
    got = call(env, 'mentions')
    assert got['account'] == 'alpha' and got['n'] == 20 and len(got['mentions']) == 20
    rc, printed = _cli_json(capsys, threads_read_cli.cmd_mentions, account='alpha', since=None)
    assert rc == 0 and printed['mentions'][:20] == got['mentions'] and printed['n'] == 30
    assert call(env, 'mentions', limit=100, refresh=True)['n'] == 30
    runs = [json.loads(line) for path in (env['root'] / 'state/alpha').glob('runs-*.ndjson')
            for line in path.read_text().splitlines()]
    assert sum(1 for row in runs if row.get('action') == 'mentions') == 3


def test_topics_searchは集計と指す先だけで本文を返さない(env, media, capsys):
    from thth import threads_read_cli
    got = call(env, 'topics_search', query='レアアース')
    assert got['q'] == 'レアアース' and got['n'] == 1 and got['posts'][0]['post_id'] == '77'
    assert 'あ' * 61 not in json.dumps(got, ensure_ascii=False)
    rc, printed = _cli_json(capsys, threads_read_cli.cmd_topics_search, account='alpha', account_flag=None,
                            search='レアアース', recent=False, limit=20)
    assert rc == 0
    for key in ('q', 'n', 'authors', 'posts', 'tagged'):
        assert printed[key] == got[key]
    assert str(refused(env, 'topics_search')) == 'invalid_request'
    assert str(refused(env, 'topics_search', query='  ')) == 'invalid_request'


def test_profileとlocation_search(env, media, capsys):
    from thth import retract_cli, threads_read_cli
    got = call(env, 'profile', username='@meta')
    rc, printed = _cli_json(capsys, threads_read_cli.cmd_profile, account='alpha', username='@meta')
    assert rc == 0 and printed == got == {'account': 'alpha', 'profile': {'username': 'meta', 'follower_count': 3}}
    got = call(env, 'location_search', query='渋谷駅')
    rc, printed = _cli_json(capsys, retract_cli.cmd_location, account='alpha', query='渋谷駅', action='search')
    assert rc == 0 and printed == got and got['locations'] == [{'id': '1', 'name': '渋谷駅'}]


# --------------------------------------------------------------------------
# 範囲・止まった口座・符丁
# --------------------------------------------------------------------------

@pytest.mark.parametrize('operation', OPS)
def test_範囲外の口座はscope_unavailable(env, media, operation):
    fields = {'query': '語'} if operation in ('topics_search', 'location_search') else \
        {'username': 'x'} if operation == 'profile' else {}
    with pytest.raises(ReportServiceError) as caught:
        execute_report(env['context'], dict(operation=operation, account='beta', **fields))
    assert str(caught.value) == 'scope_unavailable'


def test_知らない欄と型はinvalid_request(env, media):
    assert str(refused(env, 'posts', post_id='1')) == 'invalid_request'
    assert str(refused(env, 'replies', refresh='yes')) == 'invalid_request'
    assert str(refused(env, 'collect', limit=5)) == 'invalid_request'
    with pytest.raises(ReportServiceError) as caught:
        execute_report(env['context'], dict(operation='posts', account=3))
    assert str(caught.value) == 'scope_unavailable'


def test_止まった口座でも読める(env, media):
    guard.stop('alpha', 'burst')
    assert call(env, 'posts', limit=1)['posts']
    assert call(env, 'profile', username='meta')['profile']['username'] == 'meta'


def test_媒体の失敗はupstream_unavailableと短い理由_秘密とURLを含めない(env, media):
    from thth.adapters import base
    media.fail = base.AdapterError('HTTP 400: bad request ' + SECRET_URL)
    for operation, fields in (('posts', {}), ('mentions', {}), ('topics_search', {'query': '語'}),
                              ('profile', {'username': 'x'}), ('location_search', {'query': '語'})):
        exc = refused(env, operation, **fields)
        assert str(exc) == 'upstream_unavailable', operation
        assert 'SECRETSECRET' not in (exc.reason or '') and 'graph.threads.net' not in (exc.reason or '')
        assert 'HTTP 400' in exc.reason and len(exc.reason) <= server_reads.REASON_CHARS


def test_権限が無ければpermission_unavailable(env, media):
    from thth.adapters import base
    token = env['root'] / 'secrets' / 'alpha.json'
    value = json.loads(token.read_text())
    value['scopes'] = ['threads_basic']
    token.write_text(json.dumps(value))
    assert str(refused(env, 'location_search', query='語')) == 'permission_unavailable'
    media.fail = base.PermissionMissing('threads_profile_discovery', 'not granted')
    assert str(refused(env, 'profile', username='x')) == 'permission_unavailable'


def test_媒体に口が無ければunsupported_operation(env, media, monkeypatch):
    monkeypatch.setattr(adapters, 'capabilities_for', lambda m: set())
    assert str(refused(env, 'mentions')) == 'unsupported_operation'
    assert str(refused(env, 'topics_search', query='語')) == 'unsupported_operation'


def test_符丁はSAFE_ERRORSに載る():
    from thth.server_writes import SAFE_ERRORS
    assert server_reads.REASONS <= SAFE_ERRORS
    assert {'scope_unavailable', 'permission_unavailable', 'unsupported_operation', 'account_busy'} <= SAFE_ERRORS


# --------------------------------------------------------------------------
# collect: 口座ごとに 10 分に 1 回まで
# --------------------------------------------------------------------------

@pytest.fixture
def collector(monkeypatch):
    from thth import collect
    class Calls(list):
        rc = 0
    calls = Calls()

    def run_collect(account, *, log=print, trigger=None, **kwargs):
        calls.append((account, trigger))
        print('人向けの行（stdout に出してはいけない）')
        log('採取しました')
        return calls.rc
    monkeypatch.setattr(collect, 'run_collect', run_collect)
    return calls


def test_collectは採ってからmeasuredと同じ形_10分に1回まで(env, media, collector, monkeypatch, capsys):
    now = jst.now_jst()
    monkeypatch.setattr(server_reads, '_now', lambda: now)
    got = call(env, 'collect')
    assert collector == [('alpha', 'manual')]
    assert got == call(env, 'measured')
    assert 'stdout に出して' not in capsys.readouterr().out
    marker = env['root'] / 'state/alpha/reads/collect.json'
    assert stat.S_IMODE(marker.stat().st_mode) == 0o600
    assert stat.S_IMODE(marker.parent.stat().st_mode) == 0o700
    monkeypatch.setattr(server_reads, '_now', lambda: now + datetime.timedelta(minutes=9))
    exc = refused(env, 'collect')
    assert str(exc) == 'collect_too_soon' and exc.next_at == jst.iso(now + datetime.timedelta(minutes=10))
    assert len(collector) == 1
    monkeypatch.setattr(server_reads, '_now', lambda: now + datetime.timedelta(minutes=10))
    call(env, 'collect')
    assert len(collector) == 2


def test_collectの失敗は1回に数えupstream_unavailable(env, media, collector, monkeypatch):
    now = jst.now_jst()
    monkeypatch.setattr(server_reads, '_now', lambda: now)
    collector.rc = 2
    exc = refused(env, 'collect')
    assert str(exc) == 'upstream_unavailable' and exc.reason == '採取しました'
    assert str(refused(env, 'collect')) == 'collect_too_soon'


def test_collectの印は口座ごと(env, media, collector, monkeypatch):
    env['value']['credentials'][0]['accounts'] = {'alpha': 'alpha', 'beta': 'beta'}
    env['path'].write_text(json.dumps(env['value']))
    from thth import report_http
    context = report_http.load_credentials(env['path'])[1][0][3]
    execute_report(context, dict(operation='collect', account='alpha'))
    execute_report(context, dict(operation='collect', account='beta'))
    assert collector == [('alpha', 'manual'), ('beta', 'manual')]


# --------------------------------------------------------------------------
# MCP
# --------------------------------------------------------------------------

def _mcp(env, monkeypatch):
    from tests.test_mcp import _load_server_module
    server = _load_server_module()
    monkeypatch.setenv('THTH_REPORT_CREDENTIALS', str(env['path']))
    monkeypatch.setenv('THTH_REPORT_TOKEN', env['bearer'])
    return server


def _tools(server):
    return {tool['name']: tool for tool in server._handle_request({'id': 1, 'method': 'tools/list'})['result']['tools']}


@pytest.mark.parametrize('writes', [True, False])
def test_mcpに同じ名前で写る_writesがfalseでも出る(env, monkeypatch, writes):
    if not writes:
        env['value']['credentials'][0]['writes'] = False
        env['value']['credentials'][0].pop('actor')
        env['path'].write_text(json.dumps(env['value']))
    tools = _tools(_mcp(env, monkeypatch))
    for operation in OPS:
        assert 'thth_' + operation in tools, operation
    assert ('thth_send_request' in tools) is writes
    assert tools['thth_posts']['inputSchema']['properties']['limit']['type'] == 'integer'
    assert tools['thth_replies']['inputSchema']['properties']['refresh']['type'] == 'boolean'
    assert tools['thth_topics_search']['inputSchema']['required'] == ['account', 'query']


def test_mcpから呼べて断りは符丁(env, media, collector, monkeypatch):
    server = _mcp(env, monkeypatch)
    ok = server.call_tool('thth_posts', {'account': 'alpha', 'limit': 2})
    assert not ok.get('isError') and len(json.loads(ok['content'][0]['text'])['posts']) == 2
    assert server.call_tool('thth_posts', {'account': 'beta'})['content'][0]['text'] == 'scope_unavailable'
    assert server.call_tool('thth_posts', {'account': 'alpha', 'limit': '2'})['content'][0]['text'] == 'invalid_request'
    assert not server.call_tool('thth_collect', {'account': 'alpha'}).get('isError')
    again = server.call_tool('thth_collect', {'account': 'alpha'})
    assert again['isError'] and again['content'][0]['text'].startswith('collect_too_soon: next_at=')
    from thth.adapters import base
    media.fail = base.AdapterError('HTTP 400 ' + SECRET_URL)
    failed = server.call_tool('thth_profile', {'account': 'alpha', 'username': 'x'})
    assert failed['isError'] and failed['content'][0]['text'].startswith('upstream_unavailable: ')
    assert 'SECRETSECRET' not in json.dumps(failed) and env['bearer'] not in json.dumps(failed)


def test_mcpで止まった口座を読むと成功に理由が添う(env, media, monkeypatch):
    server = _mcp(env, monkeypatch)
    guard.stop('alpha', 'burst')
    got = server.call_tool('thth_profile', {'account': 'alpha', 'username': 'meta'})
    assert not got.get('isError') and got['content'][-1]['text'].startswith('account_stopped: burst')
