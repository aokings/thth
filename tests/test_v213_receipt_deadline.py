"""A relay consume delay can reach receipt expiry after the outer check."""
import json
from thth import approval_jobs as jobs, approval_relay as relay, server_writes as writes
from tests.test_v212_server_writes import env, remote, draft


def test_consume_crossing_deadline_rejected_before_ready_or_effect(env, remote, monkeypatch):
    one = draft(env)
    response = writes.execute(env['context'], {'operation': 'approval_request',
                              'account': 'alpha', 'draft_id': one['draft_id']})
    path = jobs.directory('alpha') / (response['job_id'] + '.json')
    expires = json.loads(path.read_text())['expires_at']
    clock = [(expires - 1000) / 1000]
    monkeypatch.setattr(jobs.time, 'time', lambda: clock[0])
    remote.approve()
    original_save = jobs._save
    saved = []
    effects = []
    def save(fd, value):
        saved.append(value['status'])
        return original_save(fd, value)
    def delayed(kind, subject, operation, body):
        value = remote(kind, subject, operation, body)
        if operation == 'consume':
            clock[0] = (expires + 1000) / 1000
        return value
    def forbidden(*args):
        effects.append('called')
        raise AssertionError('expired receipt must not reach effects')
    monkeypatch.setattr(relay, 'signed_request', delayed)
    monkeypatch.setattr(jobs, '_save', save)
    monkeypatch.setattr(jobs, '_perform', forbidden)
    jobs.run_once(env['path'])
    assert remote.consumes == 1
    assert saved == ['consuming', 'unknown']
    assert not effects
    observed = jobs.status(env['context'], 'alpha', response['job_id'])
    assert observed['status'] == 'unknown' and observed['reason'] == 'operation_outcome_unknown'
    jobs.run_once(env['path'])
    assert remote.consumes == 1 and not effects
    assert 'status: draft' in next((env['root'] / 'repos/_server/alpha/queue').glob('*.md')).read_text()
