"""3.12.0 段 1〜2 と 3.13.0 段 1: 承認なしの道（唯一の道）・安全装置（`thth/guard.py`）。

見るのは:
  - 安全装置の数値の検証（知らない値は断る）と既定。
  - 台帳に 3.12.0 の `approval` が残っていても読まない（どの値でも、招待の口座でも、その場で行う）。
  - 公開・削除・予約はその場。`approval_request` は受け付けない。
  - 直接の公開・削除・予約の記録に via と資格の id（credential_digest の先頭 12 文字）が残る。
  - 最短間隔・1 日の上限・削除の上限で断り、次に出せる時刻を添える。
  - burst を超える依頼は口座を止めて断り、止まった口座は公開・削除・予約を断る。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import stat

import pytest

from thth import accounts, guard, jst, runs, sent, server_writes as writes
from thth.report_service import ReportServiceError
from tests.test_v212_server_writes import env, remote  # noqa: F401


@pytest.fixture
def publisher(monkeypatch):
    """公開の時刻は今（上限と間隔は sent/ の sent_at で数えるので、固定の過去の時刻にしない）。"""
    from thth.adapters.base import PublishResult
    from thth import adapters
    calls = []

    class Adapter:
        def publish(self, post, **kwargs):
            calls.append(('publish', post.text))
            return PublishResult('123456' if len([c for c in calls if c[0] == 'publish']) == 1
                                 else f'12345{len(calls)}0', None, jst.iso())

        def delete_post(self, post_id):
            calls.append(('delete', post_id))
            return {'deleted_id': post_id}
    monkeypatch.setattr(adapters, 'make_adapter', lambda *a: Adapter())
    return calls


def draft(env, body='投稿本文', publish_at='2030-01-01T12:00:00+09:00'):
    return writes.execute(env['context'], dict(operation='draft_put', account='alpha', body=body, publish_at=publish_at))


def ledger(env, **values):
    path = env['root'] / 'accounts/alpha.json'
    data = json.loads(path.read_text())
    for key, value in values.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    path.write_text(json.dumps(data))


def credential_id(env):
    return hashlib.sha256(env['bearer'].encode()).hexdigest()[:12]


def call(env, **request):
    return writes.execute(env['context'], dict(account='alpha', **request), via='mcp')


def refused(env, **request):
    with pytest.raises(ReportServiceError) as caught:
        call(env, **request)
    return caught.value


def sent_rows(env):
    return sent.records(str(env['root'] / 'state/alpha'))


# --------------------------------------------------------------------------
# 台帳
# --------------------------------------------------------------------------

def test_approvalの設定は無い_安全装置の既定():
    assert not hasattr(accounts, 'approval_mode') and not hasattr(accounts, 'APPROVAL_VALUES')
    assert accounts.IGNORED_FIELDS == ('approval',)
    assert accounts.guard_limits({}) == {'daily_max_posts': 8, 'daily_max_retracts': 5,
                                         'burst': {'count': 3, 'minutes': 10}, 'hold_minutes': 0}
    assert accounts.guard_limits({'daily_max_posts': 20})['daily_max_posts'] == 20


@pytest.mark.parametrize('key,value', [
    ('daily_max_posts', -1), ('daily_max_posts', True),
    ('daily_max_posts', '8'), ('daily_max_retracts', 1.5), ('hold_minutes', 100000),
    ('burst', 3), ('burst', {'count': 0, 'minutes': 10}), ('burst', {'count': 3}),
    ('burst', {'count': 3, 'minutes': 10, 'extra': 1})])
def test_知らない値は台帳の読み込みで断る(env, key, value):
    ledger(env, **{key: value})
    with pytest.raises(accounts.AccountError) as caught:
        accounts.load_account('alpha')
    assert 'invalid_guard_limits' in str(caught.value)


@pytest.mark.parametrize('value', ['none', 'publish', 'all', 'sometimes', True])
def test_台帳に残ったapprovalは読まずに通す(env, value):
    ledger(env, approval=value)
    assert accounts.load_account('alpha')['production'] is True


def test_招待の口座の新しい台帳にapprovalを書かない(monkeypatch):
    from thth import invites
    data = invites._ledger({'account': 'inv-x-000001', 'project': 'x', 'production': False,
                            'invite_id': 'a' * 16, 'by': 'masaru'})
    assert 'approval' not in data and data['provenance']['created_via'] == 'invite'


# --------------------------------------------------------------------------
# その場で行う（承認ページは無い）
# --------------------------------------------------------------------------

def test_今すぐの公開をその場で出し_viaと資格のidを残す(env, remote, publisher):
    result = call(env, operation='send_request', body='直接の本文')
    assert result['status'] == 'published' and result['post_id'] == '123456' and result['via'] == 'mcp'
    assert 'permalink' in result and 'job_id' not in result
    assert publisher == [('publish', '直接の本文')] and remote.rows == {}
    (row,) = sent_rows(env)
    assert row['via'] == 'mcp' and row['credential'] == credential_id(env)
    assert env['bearer'] not in json.dumps(row)
    posted = [r for r in runs.read_runs(str(env['root'] / 'state/alpha')) if r.get('post_id') == '123456']
    assert posted and posted[-1]['via'] == 'mcp' and posted[-1]['credential'] == credential_id(env)
    assert not (env['root'] / 'state/alpha/approval-jobs').exists() or not list((env['root'] / 'state/alpha/approval-jobs').glob('*.json'))


def test_削除をその場で行い_記録にviaと資格のidを足す(env, remote, publisher):
    call(env, operation='send_request', body='消す本文')
    result = call(env, operation='retract_request', post_id='123456', reason='訂正')
    assert result['status'] == 'retracted' and result['post_id'] == '123456'
    assert publisher[-1] == ('delete', '123456') and remote.rows == {}
    (row,) = sent_rows(env)
    assert row['retracted_by'] == 'person' and row['retracted_via'] == 'mcp'
    assert row['retracted_credential'] == credential_id(env)


def test_予約をその場でqueueに刻む_thth_approveと同じ形(env, remote):
    one = draft(env)
    result = call(env, operation='schedule_request', draft_id=one['draft_id'])
    assert result['status'] == 'approved' and result['publish_at'] == '2030-01-01T12:00:00+09:00'
    assert remote.rows == {}
    text = next((env['root'] / 'repos/_server/alpha/queue').glob('*.md')).read_text()
    assert 'status: approved' in text and 'approved_by: person' in text and 'approved_sha: ' in text
    assert 'approved_via: mcp' in text and f'approved_credential: {credential_id(env)}' in text
    import subprocess
    log = subprocess.check_output(['git', '-C', str(env['root'] / 'repos/_server/alpha'), 'log', '-1', '--format=%B'])
    assert b'schedule by=person via=mcp' in log


@pytest.mark.parametrize('extra', [{'approval': 'publish'}, {'approval': 'all'},
                                   {'approval': None, 'provenance': {'created_at': '2026-09-20T00:00:00+09:00',
                                    'created_by': 'masaru', 'created_via': 'invite', 'created_host': 'vm'}}])
def test_3_12の承認の設定が残っていても公開と削除はその場(env, remote, publisher, extra):
    ledger(env, **extra)
    result = call(env, operation='send_request', body='その場の本文')
    assert result['status'] == 'published' and 'approval_url' not in result and 'job_id' not in result
    one = draft(env)
    assert call(env, operation='schedule_request', draft_id=one['draft_id'])['status'] == 'approved'
    assert call(env, operation='retract_request', post_id='123456', reason='訂正')['status'] == 'retracted'
    assert publisher == [('publish', 'その場の本文'), ('delete', '123456')] and remote.rows == {}


def test_approval_requestは受け付けない(env, remote):
    one = draft(env)
    exc = refused(env, operation='approval_request', draft_id=one['draft_id'])
    assert str(exc) == 'unsupported_operation'
    text = next((env['root'] / 'repos/_server/alpha/queue').glob('*.md')).read_text()
    assert 'status: draft' in text and 'approved_sha' not in text and remote.rows == {}
    assert not (env['root'] / 'state/alpha/approval-jobs').exists()


def test_hold_minutesがあれば今すぐもその分先の予約になる(env, remote, publisher):
    ledger(env, hold_minutes=15)
    before = jst.now_jst()
    result = call(env, operation='send_request', body='少し待つ本文')
    assert result['status'] == 'held' and result['hold_minutes'] == 15 and publisher == []
    at = jst.parse(result['publish_at'])
    assert before + datetime.timedelta(minutes=14) < at < before + datetime.timedelta(minutes=16)
    text = next((env['root'] / 'repos/_server/alpha/queue').glob('*.md')).read_text()
    assert 'status: approved' in text and '少し待つ本文' in text


# --------------------------------------------------------------------------
# 安全装置
# --------------------------------------------------------------------------

def test_最短間隔で断り_次に出せる時刻を添える(env, publisher):
    ledger(env, min_interval_hours=6)
    call(env, operation='send_request', body='一本目')
    exc = refused(env, operation='send_request', body='二本目')
    assert str(exc) == 'too_soon' and exc.next_at
    assert jst.parse(exc.next_at) > jst.now_jst() + datetime.timedelta(hours=5)
    assert len(publisher) == 1


def test_1日の公開の上限で断る(env, publisher):
    ledger(env, daily_max_posts=1)
    call(env, operation='send_request', body='一本目')
    exc = refused(env, operation='send_request', body='二本目')
    assert str(exc) == 'daily_limit' and jst.parse(exc.next_at).time() == datetime.time(0, 0)
    assert len(publisher) == 1


def test_予約も同じ日の上限を数える(env):
    ledger(env, daily_max_posts=1)
    first, second = draft(env), draft(env, body='二本目の下書き')
    call(env, operation='schedule_request', draft_id=first['draft_id'])
    exc = refused(env, operation='schedule_request', draft_id=second['draft_id'])
    assert str(exc) == 'daily_limit' and exc.next_at.startswith('2030-01-02T00:00:00')


def test_1日の削除の上限で断る(env, publisher):
    ledger(env, daily_max_retracts=0)
    call(env, operation='send_request', body='一本目')
    exc = refused(env, operation='retract_request', post_id='123456', reason='訂正')
    assert str(exc) == 'retract_limit' and exc.next_at
    assert ('delete', '123456') not in publisher


def test_burstを超える依頼は口座を止めて断り_止まった口座は全部断る(env, remote, publisher, monkeypatch):
    ledger(env, burst={'count': 2, 'minutes': 10})
    state = env['root'] / 'state/alpha'
    for n in range(2):
        sent.write(str(state), post_id=f'90{n}', text=f'前の投稿{n}', body_hash='x', sent_at=jst.iso())
    exc = refused(env, operation='send_request', body='三本目')
    assert str(exc) == 'account_stopped' and exc.reason == 'burst'
    assert publisher == []
    halted = guard.stopped('alpha')
    assert halted['reason'] == 'burst'
    path = state / 'guard' / 'stopped.json'
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(state / 'guard').st_mode) == 0o700
    assert halted['credential'] == credential_id(env) and env['bearer'] not in path.read_text()
    # 止まった口座: 削除も予約も断る。
    assert str(refused(env, operation='retract_request', post_id='900', reason='訂正')) == 'account_stopped'
    one = draft(env)
    assert str(refused(env, operation='schedule_request', draft_id=one['draft_id'])) == 'account_stopped'
    assert remote.rows == {} and publisher == []
    # 数え方は間隔の外に出ても変わらない: 持ち主が戻すまで止まったまま。
    ledger(env, burst={'count': 100, 'minutes': 10})
    assert str(refused(env, operation='send_request', body='四本目')) == 'account_stopped'
    assert guard.resume('alpha', by='owner') is True and guard.stopped('alpha') is None
    assert call(env, operation='send_request', body='戻したあと')['status'] == 'published'


def test_guardの時刻の計算(env):
    cfg = {'quiet_hours': ['22:00', '07:00'], 'min_interval_hours': 0}
    night = datetime.datetime(2026, 9, 26, 23, 30, tzinfo=jst.JST)
    with pytest.raises(guard.GuardRefused) as caught:
        guard.check('beta', cfg, 'publish', now=night)
    assert str(caught.value) == 'quiet_hours' and caught.value.next_at == '2026-09-27T07:00:00+09:00'


def test_mcpの断りに次の一手が付く(env, monkeypatch, publisher):
    from tests.test_mcp import _load_server_module
    server = _load_server_module()
    monkeypatch.setenv('THTH_REPORT_CREDENTIALS', str(env['path']))
    monkeypatch.setenv('THTH_REPORT_TOKEN', env['bearer'])
    ledger(env, min_interval_hours=6)
    names = {x['name'] for x in server._handle_request({'id': 1, 'method': 'tools/list'})['result']['tools']}
    assert 'thth_schedule_request' in names
    ok = server.call_tool('thth_send_request', {'account': 'alpha', 'body': '一本目'})
    assert not ok.get('isError') and json.loads(ok['content'][0]['text'])['post_id'] == '123456'
    denied = server.call_tool('thth_send_request', {'account': 'alpha', 'body': '二本目'})
    assert denied['isError'] and denied['content'][0]['text'].startswith('too_soon: next_at=20')
    guard.stop('alpha', 'burst')
    denied = server.call_tool('thth_send_request', {'account': 'alpha', 'body': '三本目'})
    assert denied['content'][0]['text'].startswith('account_stopped: burst: ')
    assert env['bearer'] not in json.dumps(denied)
