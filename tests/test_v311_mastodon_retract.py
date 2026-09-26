"""3.1.1 件 2: Mastodon の取り下げ（`DELETE /api/v1/statuses/:id`）。

**本物のインスタンスは叩かない。** loopback の偽 Mastodon が受けた DELETE の回数と
形を数える。確かめること:
- adapter: 200 で `id` 一致のときだけ成功／401・403 は `PermissionMissing`／404 は
  `post_missing`／`id` 不一致は成功と言わない／5xx でも再試行しない／数字以外の
  `post_id` は 1 回も投げない。
- CLI（`thth retract`）の二段承認: 一段目は DELETE 0 回、二段目で 1 回・`retracted_at`。
- 承認の口（`retract_request` → 人の承認 → worker）で同じ adapter が削除する。
- scope に `write:statuses` が無い token は DELETE を投げずに断る。
"""
import argparse
import http.server
import json
import secrets
import socket
import threading
import pytest
from thth import accounts, approval, retract_cli, scopes, sent as sent_mod
from thth import server_writes as writes
from thth.report_service import ReportServiceError
from thth.adapters import base as adapter_base, mastodon as mastodon_mod
from tests.test_v212_server_writes import env, remote

POST_ID = '113000000000000001'


class Fake:
    """偽 Mastodon。DELETE の応答（status・body）を試験ごとに差し替える。"""
    def __init__(self):
        self.calls = []
        self.delete_status = 200
        self.delete_body = None  # None なら要求した id の Status を返す

    def status_for(self, post_id):
        return {'id': post_id, 'text': '取り下げる本文', 'content': '<p>取り下げる本文</p>',
                'visibility': 'public', 'url': 'https://example.invalid/@demo/' + post_id,
                'created_at': '2026-09-23T00:00:00.000Z',
                'account': {'id': '9000', 'acct': 'demo', 'username': 'demo'}}


@pytest.fixture
def fake(monkeypatch):
    state = Fake()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def reply(self, status, body):
            raw = json.dumps(body).encode()
            self.send_response(status); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)

        def do_GET(self):
            state.calls.append(('GET', self.path, self.headers.get('Authorization')))
            if self.path.startswith('/api/v2/instance'):
                return self.reply(200, {'configuration': {'statuses': {'max_characters': 500}}})
            if self.path.startswith('/api/v1/accounts/verify_credentials'):
                return self.reply(200, {'id': '9000', 'acct': 'demo', 'username': 'demo'})
            if '/statuses' in self.path and self.path.startswith('/api/v1/accounts/'):
                return self.reply(200, [])
            return self.reply(404, {'error': 'Record not found'})

        def do_DELETE(self):
            state.calls.append(('DELETE', self.path, self.headers.get('Authorization')))
            if state.delete_status != 200:
                return self.reply(state.delete_status, {'error': 'synthetic'})
            post_id = self.path.rsplit('/', 1)[-1]
            return self.reply(200, state.delete_body if state.delete_body is not None else state.status_for(post_id))

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    state.base = 'http://127.0.0.1:' + str(server.server_port)
    try: yield state
    finally: server.shutdown(); server.server_close(); thread.join()


def deletes(fake): return [c for c in fake.calls if c[0] == 'DELETE']


def adapter(fake, token='synthetic-' + secrets.token_urlsafe(16)):
    return mastodon_mod.MastodonAdapter(instance=fake.base, access_token=token, timeout=2.0), token


# ---------------------------------------------------------------- adapter

def test_scope_for_delete_is_already_in_the_requested_mastodon_scopes():
    # 再認可なしで取り下げられる前提（依頼 件 2）。無ければこの試験が落ちる。
    assert mastodon_mod.MastodonAdapter.DELETE_PERMISSION == 'write:statuses'
    assert 'write:statuses' in scopes.MASTODON_SCOPES


def test_delete_200_with_matching_id_is_one_delete_with_bearer_header_only(fake):
    a, token = adapter(fake)
    assert a.delete_post(POST_ID) == {'deleted': True, 'post_id': POST_ID}
    assert deletes(fake) == [('DELETE', '/api/v1/statuses/' + POST_ID, 'Bearer ' + token)]
    assert all(token not in path for _, path, _ in fake.calls)


@pytest.mark.parametrize('status', [401, 403])
def test_delete_401_403_is_permission_missing_without_retry(fake, status):
    fake.delete_status = status
    a, token = adapter(fake)
    with pytest.raises(adapter_base.PermissionMissing) as e: a.delete_post(POST_ID)
    assert e.value.permission == 'write:statuses' and token not in str(e.value)
    assert len(deletes(fake)) == 1


def test_delete_404_is_static_post_missing(fake):
    fake.delete_status = 404
    a, _ = adapter(fake)
    with pytest.raises(adapter_base.AdapterError) as e: a.delete_post(POST_ID)
    assert str(e.value).startswith('post_missing:') and not isinstance(e.value, adapter_base.PermissionMissing)
    assert len(deletes(fake)) == 1


def test_delete_response_with_other_id_is_not_success(fake):
    fake.delete_body = fake.status_for('113000000000000999')
    a, _ = adapter(fake)
    with pytest.raises(adapter_base.AdapterError, match='mastodon_retract_unconfirmed'): a.delete_post(POST_ID)
    fake.delete_body = {'text': 'id の無い応答'}
    with pytest.raises(adapter_base.AdapterError, match='mastodon_retract_unconfirmed'): a.delete_post(POST_ID)


def test_delete_5xx_is_not_retried(fake):
    fake.delete_status = 503
    a, _ = adapter(fake)
    with pytest.raises(adapter_base.AdapterError, match='mastodon_retract_http_503'): a.delete_post(POST_ID)
    assert len(deletes(fake)) == 1


@pytest.mark.parametrize('value', ['../1', '1?x=2', '1/context', '١٢٣', '', None, 123])
def test_post_id_must_be_ascii_digits_and_nothing_is_sent_otherwise(fake, value):
    a, _ = adapter(fake)
    with pytest.raises(adapter_base.AdapterError, match='mastodon_post_id_invalid'): a.delete_post(value)
    assert fake.calls == []


# ---------------------------------------------------------------- CLI・承認の口

def as_mastodon(env, fake, monkeypatch, *, granted=None):
    def connect(sock, address):
        assert address[0] == '127.0.0.1'
        return env['connect'](sock, address)
    monkeypatch.setattr(socket.socket, 'connect', connect)
    monkeypatch.delenv(mastodon_mod.INSTANCE_ENV, raising=False)
    path = env['root'] / 'accounts/alpha.json'; cfg = json.loads(path.read_text())
    cfg.update(media='mastodon', instance=fake.base); path.write_text(json.dumps(cfg))
    tokenpath = env['root'] / 'secrets/alpha.json'; token = json.loads(tokenpath.read_text())
    token.update(scopes=list(scopes.MASTODON_SCOPES if granted is None else granted), scopes_source='response', user_id='9000')
    tokenpath.write_text(json.dumps(token))
    cfg = accounts.load_account('alpha')
    sent_mod.write(accounts.state_dir_for('alpha'), post_id=POST_ID, text='取り下げる本文',
                   body_hash=approval.compute_body_hash('取り下げる本文'), sent_at='2026-09-23T09:00:00+09:00')
    return cfg


def ns(**kw):
    base = dict(account='alpha', post_id=POST_ID, reason='訂正', by='masaru', confirm=None, json=False, wait=0)
    base.update(kw); return argparse.Namespace(**base)


def digest():
    return approval.compute_retract_digest(post_id=POST_ID, account='alpha', reason='訂正')


def retracted_at():
    return sent_mod.read(accounts.state_dir_for('alpha'), POST_ID).get('retracted_at')


def test_cli_two_stage_retract_deletes_once_and_records_retracted_at(env, fake, monkeypatch, capsys):
    as_mastodon(env, fake, monkeypatch)
    assert retract_cli.cmd_retract(ns()) == 1
    assert deletes(fake) == [] and not retracted_at()
    assert 'digest: ' + digest() in capsys.readouterr().out
    assert retract_cli.cmd_retract(ns(confirm=digest())) == 0
    assert [c[1] for c in deletes(fake)] == ['/api/v1/statuses/' + POST_ID]
    row = sent_mod.read(accounts.state_dir_for('alpha'), POST_ID)
    assert row['retracted_at'] and row['retracted_by'] == 'masaru' and row['retract_reason'] == '訂正'
    # 二度目は記録を見て DELETE を投げない。
    assert retract_cli.cmd_retract(ns(confirm=digest())) == 1 and len(deletes(fake)) == 1


@pytest.mark.parametrize('status,rc,word', [(403, 2, 'write:statuses'), (404, 1, 'post_missing')])
def test_cli_refusals_keep_record_unchanged(env, fake, monkeypatch, capsys, status, rc, word):
    as_mastodon(env, fake, monkeypatch); fake.delete_status = status
    assert retract_cli.cmd_retract(ns(confirm=digest())) == rc
    assert word in capsys.readouterr().err and not retracted_at() and len(deletes(fake)) == 1


def test_cli_mismatched_id_is_not_recorded_as_retracted(env, fake, monkeypatch):
    as_mastodon(env, fake, monkeypatch); fake.delete_body = fake.status_for('113000000000000999')
    assert retract_cli.cmd_retract(ns(confirm=digest())) == 1
    assert not retracted_at() and len(deletes(fake)) == 1


def test_token_without_write_statuses_is_refused_before_any_delete(env, fake, monkeypatch, capsys, remote):
    granted = [s for s in scopes.MASTODON_SCOPES if s != 'write:statuses']
    cfg = as_mastodon(env, fake, monkeypatch, granted=granted)
    cls = mastodon_mod.MastodonAdapter
    assert cls.missing_permissions(accounts.load_token(cfg), [cls.DELETE_PERMISSION]) == ['write:statuses']
    assert retract_cli.cmd_retract(ns(confirm=digest())) == 2
    assert '`write:statuses` がトークンに乗っていません' in capsys.readouterr().err
    # サーバの口（3.13.0 はその場で消す）でも permission_unavailable として断り DELETE しない。
    with pytest.raises(ReportServiceError) as caught:
        writes.execute(env['context'], dict(operation='retract_request', account='alpha', post_id=POST_ID, reason='訂正'))
    assert str(caught.value) == 'permission_unavailable'
    assert deletes(fake) == [] and not retracted_at()


def test_retract_request_deletes_on_mastodon_directly(env, fake, monkeypatch, remote):
    as_mastodon(env, fake, monkeypatch)
    result = writes.execute(env['context'], dict(operation='retract_request', account='alpha', post_id=POST_ID, reason='訂正'))
    assert result['status'] == 'retracted' and 'job_id' not in result
    assert [c[1] for c in deletes(fake)] == ['/api/v1/statuses/' + POST_ID]
    assert retracted_at()
