"""第8段: attachments reach the approval page, or no session is registered at all."""
import hashlib
import json
import secrets
import subprocess
import time
from pathlib import Path

import pytest

from thth import approval_jobs as jobs, media_relay, server_writes as writes
from thth.report_service import ReportServiceError, execute_report
from tests.test_v212_server_writes import draft, env, remote  # noqa: F401
from tests.test_v213_media_foundation import jpeg, png
from tests.test_v213_media_formats import mp4

REQUEST = dict(operation='approval_request', account='alpha')
ATTACHMENT_KEYS = {'index','role','kind','format','public_sha256','public_size','width','height','duration','alt','preview'}


@pytest.fixture
def relay_client(monkeypatch):
    """A fake Worker boundary: no signature, no socket, exact prepared bytes."""
    state = {'uploads': [], 'grants': [], 'fail': None, 'owners': []}

    class Relay:
        def __init__(self, account, actor):
            state['owners'].append((account, actor))

        def upload_prepared(self, item):
            if state['fail'] == 'upload': raise media_relay.MediaRelayError('media_relay_outcome_unknown')
            data = b''.join(item.chunks())
            assert hashlib.sha256(data).hexdigest() == item.manifest['public_sha256'], 'public bytes differ'
            assert item.manifest['kind'] == 'image', 'only images are uploaded for the page'
            subject = secrets.token_urlsafe(32)
            state['uploads'].append((subject, item.manifest['public_sha256']))
            return subject

        def grant(self, item, source, *, purpose='provider', expires_at=None):
            if state['fail'] == 'grant': raise media_relay.MediaRelayError('media_relay_outcome_unknown')
            assert purpose == 'preview' and source in [row[0] for row in state['uploads']]
            subject = secrets.token_urlsafe(32)
            state['grants'].append({'subject': subject, 'source': source, 'sha256': item.manifest['public_sha256']})
            return {'subject': subject, 'url': 'http://127.0.0.1/m/' + subject, 'generation': secrets.token_urlsafe(32),
                    'purpose': purpose, 'media_id': item.manifest['source_sha256'],
                    'sha256': item.manifest['public_sha256'], 'expires_at': int(time.time() * 1000) + 600000}

    monkeypatch.setattr(media_relay, 'MediaRelay', Relay)
    return state


def stage(env, rows, files, extra=''):
    """A verified managed-repo draft that declares attachments."""
    repo = Path(env['root']) / 'repos/_server/alpha'
    one = draft(env)
    path = next((repo / 'queue').glob('*.md'))
    for name, data in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    block = 'media:\n' + ''.join(f'  - file: {name}\n    alt: {alt}\n' for name, alt in rows) + extra
    path.write_text(path.read_text().replace('status: draft\n', 'status: draft\n' + block, 1))
    git = ['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@invalid']
    for args in (['add', '-A'], ['commit', '-m', 'media'], ['push', 'origin', 'HEAD']):
        subprocess.run(git + args, check=True, capture_output=True)
    return one


def three(env):
    return stage(env, [('docs/sns/media/a.png', '赤い点'), ('docs/sns/media/b.jpg', '灰色の点'),
                       ('docs/sns/media/c.mp4', '湯を注ぐ')],
                 {'docs/sns/media/a.png': png(), 'docs/sns/media/b.jpg': jpeg(), 'docs/sns/media/c.mp4': mp4()})


def body_of(remote):
    assert len(remote.rows) == 1
    return next(iter(remote.rows.values()))


def test_two_images_and_one_video_reach_create_with_previews_only_for_images(env, remote, relay_client):
    one = three(env)
    result = writes.execute(env['context'], dict(REQUEST, draft_id=one['draft_id']))
    body = body_of(remote)
    rows = body['attachments']
    assert [row['kind'] for row in rows] == ['image', 'image', 'video']
    assert all(set(row) == ATTACHMENT_KEYS for row in rows)
    assert [row['index'] for row in rows] == [1, 2, 3] and {row['role'] for row in rows} == {'media'}
    assert [row['format'] for row in rows] == ['png', 'jpeg', 'mp4']
    assert [row['preview'] is None for row in rows] == [False, False, True]
    granted = [grant['subject'] for grant in relay_client['grants']]
    assert [row['preview'] for row in rows[:2]] == granted and len(relay_client['uploads']) == 2
    assert rows[2]['duration'] == 2.5 and (rows[2]['width'], rows[2]['height']) == (640, 480)
    assert [row['alt'] for row in rows] == ['赤い点', '灰色の点', '湯を注ぐ']
    assert all(len(row['public_sha256']) == 64 and row['public_size'] > 0 for row in rows)
    assert relay_client['owners'] == [('alpha', 'person')]
    # Repo paths and source hashes stay on the VM.
    assert 'docs/sns/media' not in json.dumps(body, ensure_ascii=False)
    assert 'typed' not in body and result['job_id']


def test_typed_attachments_travel_as_one_canonical_json_string(env, remote, relay_client):
    one = stage(env, [('a.png', '赤い点')], {'a.png': png()}, extra='post_options: {"media_spoiler": true}\n')
    writes.execute(env['context'], dict(REQUEST, draft_id=one['draft_id']))
    typed = body_of(remote)['typed']
    assert isinstance(typed, str) and len(typed.encode()) <= 16384
    assert json.loads(typed) == {'attachments': [], 'post_options': {'media_spoiler': True}, 'captions': []}
    assert typed == json.dumps(json.loads(typed), ensure_ascii=False, sort_keys=True, separators=(',', ':'))


@pytest.mark.parametrize('fault', ['upload', 'grant'])
def test_preview_failure_registers_no_session_and_fails_the_job(env, remote, relay_client, fault):
    one = three(env)
    relay_client['fail'] = fault
    with pytest.raises(ReportServiceError) as caught:
        writes.execute(env['context'], dict(REQUEST, draft_id=one['draft_id']))
    assert str(caught.value) == 'media_preview_unavailable'
    assert remote.rows == {}, 'a session was registered without its images'
    names = [p for p in jobs.directory('alpha').glob('*.json')]
    assert len(names) == 1
    job = json.loads(names[0].read_text())
    assert job['status'] == 'failed' and job['reason'] == 'media_preview_unavailable'
    observed = jobs.status(env['context'], 'alpha', job['job_id'])
    assert observed['status'] == 'failed' and observed['reason'] == 'media_preview_unavailable'
    jobs.run_once(env['path'])
    assert json.loads(names[0].read_text())['status'] == 'failed' and remote.rows == {}


def test_no_preview_capability_appears_in_status_or_stdout(env, remote, relay_client, capsys):
    one = three(env)
    result = writes.execute(env['context'], dict(REQUEST, draft_id=one['draft_id']))
    previews = [grant['subject'] for grant in relay_client['grants']]
    stored = jobs.directory('alpha').joinpath(result['job_id'] + '.json').read_text()
    assert previews and all(value not in stored for value in previews)
    # The upload subject is kept for the later provider grant; it is never public.
    subjects = json.loads(stored)['media_subjects']
    assert set(subjects) == {'0', '1'} and set(subjects.values()) == {row[0] for row in relay_client['uploads']}
    jobs.run_once(env['path'])
    public = json.dumps(jobs.status(env['context'], 'alpha', result['job_id']), ensure_ascii=False)
    reported = json.dumps(execute_report(env['context'], dict(operation='request_status', account='alpha',
                                                              job_id=result['job_id'])), ensure_ascii=False)
    surface = public + reported + json.dumps(result, ensure_ascii=False) + capsys.readouterr().out
    for value in previews + [row[0] for row in relay_client['uploads']]:
        assert value not in surface, 'capability left the approval page'
    assert 'attachments' not in public and 'media_subjects' not in public


def test_text_only_request_body_is_unchanged(env, remote, relay_client):
    one = draft(env)
    writes.execute(env['context'], dict(REQUEST, draft_id=one['draft_id']))
    body = dict(body_of(remote))
    for key in ('status', 'expires_at'): body.pop(key)
    assert set(body) == {'person', 'job_id', 'digest', 'account', 'kind', 'text', 'context', 'read_key_hash'}
    assert body['person'] == 'person' and body['account'] == 'alpha' and body['kind'] == 'approve'
    assert body['text'].strip() == '投稿本文'
    assert body['context'] == {'media': 'threads', 'reply_to': None, 'publish_at': '2030-01-01T12:00:00+09:00',
                               'target': None, 'reason': None, 'topic': None,
                               'options': json.dumps({'location': None, 'location_id': None,
                                                      'share_to_instagram': False}, ensure_ascii=False, sort_keys=True)}
    assert relay_client['uploads'] == [] and relay_client['grants'] == []
