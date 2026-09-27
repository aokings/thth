"""3.14.3 穴 3: 前の公開の結果が分かっていない（inflight）ときの遠くの道。

- `send_request` の rehearsal が inflight で止まったら `publication_unconfirmed`（`reason: inflight`）。
  `invalid_draft` に丸めない。遠くの道の結果（`api_requests`）と HTTP の口にも理由が載る。
- `inflight_status`（見る）と `inflight_resolve`（解く・手元と同じ digest の二段確認）。解いた人は資格の actor。
- CLI の `thth inflight <口座> [resolve …]` が遠くの道で同じ operation に届き、断りに次の一手が付く。
"""
from __future__ import annotations

import json

import pytest

from thth import accounts, admin_log, api_requests, inflight as inflight_mod, inflight_cli, jst, server_writes as writes
from thth.report_service import ReportServiceError
from tests.test_v212_server_writes import env, remote  # noqa: F401
from tests.test_v3120_guard import publisher  # noqa: F401
from tests.test_v3140_remote_cli import ACCOUNT, home, worker, run  # noqa: F401


def serve(env, **request):
    return writes.serve(env['context'], dict(account='alpha', **request), via='api')


def refused(env, **request):
    with pytest.raises(ReportServiceError) as caught:
        serve(env, **request)
    return caught.value


def leave_inflight(env):
    state = accounts.state_dir_for('alpha')
    inflight_mod.write(state, file='(send)', started=jst.iso(), container_id='12345',
                       text_fingerprint='f' * 64)
    return state


def test_inflightが残っていればsendはpublication_unconfirmed_理由はinflight(env, publisher):
    leave_inflight(env)
    exc = refused(env, operation='send_request', body='次の本文')
    assert str(exc) == 'publication_unconfirmed' and exc.reason == 'inflight'
    assert api_requests._failure(exc) == {'error': 'publication_unconfirmed', 'reason': 'inflight'}
    assert publisher == []
    assert {'publication_unconfirmed', 'no_inflight', 'confirm_mismatch'} <= writes.SAFE_ERRORS


def test_inflightを見る_本文とcontainerのidは出さない(env):
    assert serve(env, operation='inflight_status') == {'account': 'alpha', 'inflight': None}
    leave_inflight(env)
    got = serve(env, operation='inflight_status')
    assert got['inflight']['file'] == '(send)' and got['inflight']['container_id_present'] is True
    assert 'thth inflight alpha resolve' in got['next']
    assert '12345' not in json.dumps(got)


def test_解くのは二段_digestが違えば断り_解いたら次のsendが出る(env, publisher):
    state = leave_inflight(env)
    first = serve(env, operation='inflight_resolve', decision='not_published')
    assert first['resolved'] is False and first['stage'] == 1
    assert first['digest'] == inflight_cli.digest('alpha', inflight_mod.read(state), 'not_published', None)
    assert inflight_mod.read(state) is not None
    assert str(refused(env, operation='inflight_resolve', decision='not_published', confirm='0' * 12)) == 'confirm_mismatch'
    done = serve(env, operation='inflight_resolve', decision='not_published', confirm=first['digest'])
    assert done['resolved'] is True and done['resolution'] == inflight_cli.HUMAN_NOT_PUBLISHED
    assert inflight_mod.read(state) is None
    row = admin_log.read(event='inflight_resolved')[0][-1]
    assert row['by'] == 'person' and row['via'] == 'api'
    assert serve(env, operation='send_request', body='解いたあとの本文')['status'] == 'published'


def test_出ていたなら_post_idで解く_無ければ断る(env):
    state = leave_inflight(env)
    assert str(refused(env, operation='inflight_resolve', decision='published')) == 'post_id_invalid'
    first = serve(env, operation='inflight_resolve', decision='published', post_id='98765')
    done = serve(env, operation='inflight_resolve', decision='published', post_id='98765', confirm=first['digest'])
    assert done['resolved'] is True and done['post_id'] == '98765'
    assert inflight_mod.read(state) is None


def test_解くものが無い_知らない決め方は断る(env):
    assert str(refused(env, operation='inflight_resolve', decision='not_published')) == 'no_inflight'
    assert str(refused(env, operation='inflight_resolve', decision='maybe')) == 'decision_required'
    assert str(refused(env, operation='inflight_resolve', decision='not_published', by='someone')) == 'invalid_request'


def test_読むだけの鍵では解けない(env):
    leave_inflight(env)
    env['value']['credentials'][0]['writes'] = False
    env['path'].write_text(json.dumps(env['value']))
    from thth import report_http
    env['context'] = report_http.load_credentials(env['path'])[1][0][3]
    assert serve(env, operation='inflight_status')['inflight'] is not None
    assert str(refused(env, operation='inflight_resolve', decision='not_published')) == 'writes_not_allowed'


# ---------------------------------------------------------------- CLI（遠くの道）

def test_CLIのinflightは遠くの道でinflight_statusに届く(worker, capsys):
    worker.push(200, {'account': ACCOUNT, 'inflight': None})
    rc, out, err = run(capsys, 'inflight', ACCOUNT)
    assert rc == 0 and worker.calls[-1]['path'] == '/api/v1/inflight_status'
    assert 'inflight はありません' in out


def test_CLIのresolveは一段目でdigestと打つ命令を出しrc1(worker, capsys):
    worker.push(200, {'account': ACCOUNT, 'resolved': False, 'stage': 1, 'digest': 'abcdef123456',
                      'decision': 'not_published', 'post_id': None,
                      'inflight': {'file': '(send)', 'started': '2026-09-27T15:00:00+09:00'}})
    rc, out, err = run(capsys, 'inflight', ACCOUNT, 'resolve', '--not-published')
    assert rc == 1
    assert worker.calls[-1]['body'] == {'account': ACCOUNT, 'decision': 'not_published'}
    assert f'thth inflight {ACCOUNT} resolve --not-published --confirm abcdef123456' in out
    worker.push(200, {'account': ACCOUNT, 'resolved': True, 'resolution': 'human_not_published', 'post_id': None})
    rc, out, err = run(capsys, 'inflight', ACCOUNT, 'resolve', '--not-published', '--confirm', 'abcdef123456')
    assert rc == 0 and worker.calls[-1]['body']['confirm'] == 'abcdef123456'
    assert '解きました' in out


def test_CLIのresolveは決め方が無ければ送らない(worker, capsys):
    rc, out, err = run(capsys, 'inflight', ACCOUNT, 'resolve')
    assert rc == 1 and err.startswith('decision_required') and worker.calls == []


def test_publication_unconfirmedの断りに次の一手が付く(worker, capsys):
    worker.push(200, {'error': 'publication_unconfirmed', 'reason': 'inflight'})
    rc, out, err = run(capsys, 'send', ACCOUNT, '--text', 'こんにちは')
    assert rc == 1 and err.startswith('publication_unconfirmed: inflight')
    assert 'thth inflight <口座>' in err and 'resolve' in err
