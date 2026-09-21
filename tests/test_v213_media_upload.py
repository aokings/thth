"""第9段: the invited user's upload — intent first, sanitize, then the repo."""
import hashlib
import io
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from thth import media_relay as relay, media_uploads as uploads, report_http, server_writes as writes
from thth.report_service import ReportServiceError
from tests.test_v212_server_writes import draft, env  # noqa: F401
from tests.test_v213_media_foundation import jpeg
from tests.test_v213_media_formats import box, exif, mp4, segment

GPS_JPEG = jpeg()[:2] + segment(0xe1, b'Exif\0\0' + exif()) + jpeg()[2:]
LOCATED_MP4 = mp4(box(b'udta', box(b'\xa9xyz', b'+00.0+00.0/')))


class Wire(urllib.request.Request):
    """What the fake transport receives instead of a signed Worker request."""

    def __init__(self, subject, operation, body):
        super().__init__('http://127.0.0.1/media/' + subject + '/' + operation)
        self.subject, self.operation, self.body = subject, operation, body


class Stream(io.BytesIO):
    status = 200


@pytest.fixture
def worker(env, monkeypatch):
    """A fake Worker boundary: real MediaRelay logic, no signature, no socket."""
    monkeypatch.setenv('THTH_MEDIA_BASE_URL', 'http://127.0.0.1:12345')
    state = {'objects': {}, 'data': {}, 'ops': [], 'fail': None, 'intent_seen': []}

    def fail_for(operation):
        code = state['fail'][1] if state['fail'] and state['fail'][0] == operation else None
        if code:
            raise urllib.error.HTTPError('', code, 'media_relay_http_error', None, None)

    def transport(account, request):
        subject, operation, body = request.subject, request.operation, request.body
        state['ops'].append((subject, operation))
        if operation == 'create':
            state['intent_seen'].append((uploads.directory(body['account']) / (subject + '.json')).exists())
            fail_for('create')
            state['objects'][subject] = dict(body, status='pending')
            return {'status': 'pending', 'size': body['size'], 'part_size': body['part_size'],
                    'expires_at': int(time.time() * 1000) + 600_000}
        row = state['objects'].get(subject)
        if (row is None or row['account'] != body.get('account') or row['actor'] != body.get('actor')
                or row['sha256'] != body.get('sha256')):
            raise urllib.error.HTTPError('', 404, 'media_relay_http_error', None, None)
        fail_for(operation)
        if operation == 'complete':
            if subject not in state['data'] or row['status'] != 'pending':
                raise urllib.error.HTTPError('', 409, 'media_relay_http_error', None, None)
            row['status'] = 'ready'
            return {'status': 'ready', 'sha256': row['sha256']}
        if operation == 'status':
            return {'status': row['status'], 'size': len(state['data'].get(subject, b'')),
                    'sha256': row['sha256'], 'expires_at': int(time.time() * 1000) + 600_000}
        if operation == 'ack':
            if row['status'] != 'ready':
                raise urllib.error.HTTPError('', 409, 'media_relay_http_error', None, None)
            row['status'] = 'retired'
            return {'status': 'retired'}
        raise AssertionError('unexpected operation ' + operation)

    class Opener:
        def open(self, request, timeout=None):
            state['ops'].append((request.subject, 'read'))
            row = state['objects'][request.subject]
            if row['status'] != 'ready':
                raise urllib.error.HTTPError('', 409, 'media_relay_http_error', None, None)
            return Stream(state['data'][request.subject])

    monkeypatch.setattr(relay, 'request_for', lambda subject, operation, body: Wire(subject, operation, body))
    monkeypatch.setattr(relay, '_json', transport)
    monkeypatch.setattr(relay.httpsafe, 'build_opener', lambda *a: Opener())
    return state


def ask(env, **values):
    return writes.execute(env['context'], dict(operation='media_upload_url', account='alpha', **values))


def put(worker, issued, data):
    """What the invited user does with the URL: one PUT of exactly those bytes."""
    worker['data'][issued['media_id']] = data


def upload(env, worker, data, *, kind='image', mime='image/jpeg'):
    issued = ask(env, kind=kind, mime=mime, size=len(data), sha256=hashlib.sha256(data).hexdigest())
    put(worker, issued, data)
    return issued


def finish(env, media_id, context=None):
    return writes.execute(context or env['context'], dict(operation='media_complete', account='alpha', media_id=media_id))


def record_of(media_id):
    return json.loads((uploads.directory('alpha') / (media_id + '.json')).read_text())


def repo_media(env):
    folder = Path(env['root']) / 'repos/_server/alpha/docs/sns/media'
    return sorted(p.name for p in folder.glob('*')) if folder.is_dir() else []


def test_intent_is_durable_before_the_worker_sees_create(env, worker):
    data = jpeg()
    issued = ask(env, kind='image', mime='image/jpeg', size=len(data), sha256=hashlib.sha256(data).hexdigest())
    assert worker['intent_seen'] == [True], 'the Worker was asked before the intent was durable'
    assert [op for _, op in worker['ops']] == ['create']
    assert issued['upload_url'] == 'http://127.0.0.1:12345/media-upload/' + issued['media_id']
    assert issued['part_size'] is None and issued['expires_at'] > int(time.time() * 1000)
    path = uploads.directory('alpha') / (issued['media_id'] + '.json')
    assert os.stat(path).st_mode & 0o777 == 0o600
    stored = record_of(issued['media_id'])
    assert stored['status'] == 'pending' and stored['actor'] == 'person' and stored['account'] == 'alpha'
    assert stored['kind'] == 'image' and stored['mime'] == 'image/jpeg' and stored['size'] == len(data)
    assert stored['sha256'] == hashlib.sha256(data).hexdigest()
    assert stored['expires_at'] - stored['created_at'] <= 600_000


def test_the_url_appears_in_the_reply_and_nowhere_else(env, worker, capsys):
    issued = ask(env, kind='image', mime='image/jpeg', size=10, sha256='0' * 64)
    again = ask(env, kind='image', mime='image/jpeg', size=10, sha256='0' * 64)
    assert again['media_id'] != issued['media_id'] and again['upload_url'] != issued['upload_url']
    leaked = []
    for folder, _, names in os.walk(env['root']):
        for name in names:
            leaf = Path(folder) / name
            try:
                raw = leaf.read_bytes()
            except OSError:
                continue
            if issued['upload_url'].encode() in raw:
                leaked.append(str(leaf))
    assert leaked == [], leaked
    captured = capsys.readouterr()
    assert issued['upload_url'] not in captured.out + captured.err


@pytest.mark.parametrize('values,reason', [
    (dict(kind='image', mime='image/jpeg', size=8_000_001), 'media_size_exceeded'),
    (dict(kind='video', mime='video/mp4', size=1_000_000_001), 'media_size_exceeded'),
    (dict(kind='image', mime='image/jpeg', size=0), 'media_size_exceeded'),
    (dict(kind='document', mime='image/jpeg', size=10), 'media_kind_unsupported'),
    (dict(kind='image', mime='image/svg+xml', size=10), 'media_mime_unsupported'),
    (dict(kind='image', mime='video/mp4', size=10), 'media_mime_unsupported'),
    (dict(kind='audio', mime='audio/mpeg', size=10), 'media_mime_unsupported'),
])
def test_declaration_is_refused_before_any_worker_call(env, worker, values, reason):
    with pytest.raises(ReportServiceError) as caught:
        ask(env, sha256='a' * 64, **values)
    assert str(caught.value) == reason
    assert worker['ops'] == [] and not list(uploads.directory('alpha').glob('*.json'))


def test_clean_image_is_verified_sanitized_and_committed(env, worker):
    data = jpeg()
    issued = upload(env, worker, data)
    result = finish(env, issued['media_id'])
    assert [op for _, op in worker['ops']] == ['create', 'complete', 'status', 'read', 'ack']
    assert result['format'] == 'jpeg' and result['kind'] == 'image'
    assert (result['width'], result['height']) == (1, 1) and result['duration'] is None
    assert result['warnings'] == [] and result['media_id'] == issued['media_id']
    public = result['public_sha256']
    assert repo_media(env) == [public + '.jpg']
    repo = Path(env['root']) / 'repos/_server/alpha'
    blob = (repo / 'docs/sns/media' / (public + '.jpg')).read_bytes()
    assert hashlib.sha256(blob).hexdigest() == public
    tracked = subprocess.check_output(['git', '-C', str(repo), 'ls-files', 'docs/sns/media'], text=True)
    assert tracked.strip() == 'docs/sns/media/' + public + '.jpg'
    assert subprocess.check_output(['git', '-C', str(repo), 'status', '--porcelain'], text=True).strip() == ''
    assert b'by=person via=http' in subprocess.check_output(['git', '-C', str(repo), 'log', '-1', '--format=%B'])
    stored = record_of(issued['media_id'])
    assert stored['status'] == 'ready' and stored['file'] == 'docs/sns/media/' + public + '.jpg'
    assert stored['public_sha256'] == public and 'reason' not in stored


def test_second_complete_is_idempotent_without_touching_the_worker(env, worker):
    issued = upload(env, worker, jpeg())
    first = finish(env, issued['media_id'])
    before = list(worker['ops'])
    assert finish(env, issued['media_id']) == first
    assert worker['ops'] == before


def test_location_metadata_refuses_and_leaves_no_repo_file(env, worker):
    issued = upload(env, worker, LOCATED_MP4, kind='video', mime='video/mp4')
    with pytest.raises(ReportServiceError) as caught:
        finish(env, issued['media_id'])
    assert str(caught.value) == 'location_metadata_present'
    assert [op for _, op in worker['ops']].count('ack') == 1
    assert repo_media(env) == []
    stored = record_of(issued['media_id'])
    assert stored['status'] == 'rejected' and stored['reason'] == 'location_metadata_present'
    with pytest.raises(ReportServiceError) as again:
        finish(env, issued['media_id'])
    assert str(again.value) == 'media_already_completed'


def test_image_metadata_is_stripped_before_the_repo(env, worker):
    """A located JPEG is not refused: stage 1 drops it, and the repo shows that."""
    issued = upload(env, worker, GPS_JPEG)
    result = finish(env, issued['media_id'])
    assert result['public_sha256'] != hashlib.sha256(GPS_JPEG).hexdigest()
    blob = (Path(env['root']) / 'repos/_server/alpha/docs/sns/media' /
            (result['public_sha256'] + '.jpg')).read_bytes()
    assert b'PRIVATE' not in blob and len(blob) < len(GPS_JPEG)


def test_video_keeps_its_bytes_and_uses_the_declared_extension(env, worker):
    data = mp4()
    issued = upload(env, worker, data, kind='video', mime='video/mp4')
    result = finish(env, issued['media_id'])
    assert result['public_sha256'] == hashlib.sha256(data).hexdigest()
    assert (result['kind'], result['format'], result['duration']) == ('video', 'mp4', 2.5)
    assert repo_media(env) == [result['public_sha256'] + '.mp4']


@pytest.mark.parametrize('kind,mime,data', [('video', 'video/mp4', jpeg()), ('image', 'image/png', jpeg())])
def test_declared_kind_and_mime_must_match_the_inspected_bytes(env, worker, kind, mime, data):
    issued = upload(env, worker, data, kind=kind, mime=mime)
    with pytest.raises(ReportServiceError) as caught:
        finish(env, issued['media_id'])
    assert str(caught.value) in ('media_kind_mismatch', 'media_mime_mismatch')
    assert repo_media(env) == [] and record_of(issued['media_id'])['status'] == 'rejected'


def test_a_different_actor_cannot_complete_and_reaches_no_worker(env, worker):
    issued = upload(env, worker, jpeg())
    seen = list(worker['ops'])
    env['value']['credentials'][0]['actor'] = 'different'
    env['path'].write_text(json.dumps(env['value']))
    other = report_http.load_credentials(env['path'])[1][0][3]
    with pytest.raises(ReportServiceError) as caught:
        finish(env, issued['media_id'], context=other)
    assert str(caught.value) == 'media_owner_mismatch'
    assert worker['ops'] == seen and repo_media(env) == []
    assert record_of(issued['media_id'])['status'] == 'pending'


def test_unknown_completion_is_never_completed_twice(env, worker):
    issued = upload(env, worker, jpeg())
    worker['fail'] = ('complete', 503)
    with pytest.raises(ReportServiceError) as caught:
        finish(env, issued['media_id'])
    assert str(caught.value) == 'media_upload_unknown'
    assert record_of(issued['media_id'])['status'] == 'unknown'
    worker['fail'] = None
    before = list(worker['ops'])
    with pytest.raises(ReportServiceError) as again:
        finish(env, issued['media_id'])
    assert str(again.value) == 'media_upload_unknown'
    assert worker['ops'] == before, 'an unknown outcome was retried'
    assert repo_media(env) == []


def test_definite_completion_failure_is_not_unknown(env, worker):
    issued = ask(env, kind='image', mime='image/jpeg', size=10, sha256='b' * 64)
    with pytest.raises(ReportServiceError) as caught:  # nothing was ever PUT
        finish(env, issued['media_id'])
    assert str(caught.value) == 'media_upload_failed'
    assert record_of(issued['media_id'])['status'] == 'rejected'


def test_draft_put_writes_the_public_path_and_the_digest_follows(env, worker):
    from thth import cli as cli_mod
    issued = upload(env, worker, jpeg())
    public = finish(env, issued['media_id'])['public_sha256']
    one = writes.execute(env['context'], dict(operation='draft_put', account='alpha', body='湯を注ぐ',
                                              publish_at='2030-01-01T12:00:00+09:00',
                                              media=[{'media_id': issued['media_id'], 'alt': '湯呑み: 淡い緑。'}]))
    path = next((Path(env['root']) / 'repos/_server/alpha/queue').glob('*.md'))
    text = path.read_text()
    assert 'media:\n  - file: docs/sns/media/' + public + '.jpg\n    alt: "湯呑み: 淡い緑。"\n' in text
    value, problem = cli_mod._prepare_one(str(path))
    assert problem is None and value['media_manifest']['files'][0]['public_sha256'] == public
    assert value['media_manifest']['files'][0]['alt'] == '湯呑み: 淡い緑。'
    plain = draft(env)
    other = next(p for p in (Path(env['root']) / 'repos/_server/alpha/queue').glob('*.md') if p != path)
    bare, _ = cli_mod._prepare_one(str(other))
    assert plain['draft_id'] != one['draft_id'] and bare['approved_sha'] != value['approved_sha']


@pytest.mark.parametrize('bad,reason', [
    ('pending', 'media_not_ready'),
    ('unknown_id', 'media_unknown'),
    ('other_actor', 'media_owner_mismatch'),
    ('no_alt', 'invalid_draft'),
])
def test_draft_put_refuses_an_attachment_that_is_not_owned_and_ready(env, worker, bad, reason):
    issued = upload(env, worker, jpeg())
    row = {'media_id': issued['media_id'], 'alt': '湯呑み'}
    if bad == 'unknown_id':
        row['media_id'] = 'x' * 43
    if bad == 'no_alt':
        row['alt'] = '  '
    if bad == 'other_actor':
        finish(env, issued['media_id'])
        stored = record_of(issued['media_id'])
        stored['actor'] = 'another'
        (uploads.directory('alpha') / (issued['media_id'] + '.json')).write_text(json.dumps(stored))
    request = dict(operation='draft_put', account='alpha', body='本文',
                   publish_at='2030-01-01T12:00:00+09:00', media=[row])
    with pytest.raises(ReportServiceError) as caught:
        writes.execute(env['context'], request)
    assert str(caught.value) == reason
    assert not list((Path(env['root']) / 'repos/_server/alpha/queue').glob('*.md'))


def test_gc_removes_only_unreferenced_files_older_than_a_day(env, worker):
    keep = upload(env, worker, jpeg())
    kept = finish(env, keep['media_id'])['public_sha256']
    writes.execute(env['context'], dict(operation='draft_put', account='alpha', body='本文',
                                        publish_at='2030-01-01T12:00:00+09:00',
                                        media=[{'media_id': keep['media_id'], 'alt': '湯呑み'}]))
    drop = upload(env, worker, GPS_JPEG)
    dropped = finish(env, drop['media_id'])['public_sha256']
    fresh = upload(env, worker, mp4(), kind='video', mime='video/mp4')
    recent = finish(env, fresh['media_id'])['public_sha256']
    folder = Path(env['root']) / 'repos/_server/alpha/docs/sns/media'
    old = time.time() - 2 * 86_400
    for name in (kept + '.jpg', dropped + '.jpg'):
        os.utime(folder / name, (old, old))
    result = uploads.gc('alpha', by='operator')
    assert result['removed_count'] == 1 and result['kept_count'] == 2 and result['reason'] is None
    assert repo_media(env) == sorted([kept + '.jpg', recent + '.mp4'])
    repo = Path(env['root']) / 'repos/_server/alpha'
    assert b'media gc by=operator' in subprocess.check_output(['git', '-C', str(repo), 'log', '-1', '--format=%B'])
    assert subprocess.check_output(['git', '-C', str(repo), 'status', '--porcelain'], text=True).strip() == ''
    assert uploads.gc('alpha', by='operator')['removed_count'] == 0


def test_mcp_exposes_both_tools_only_with_write_credentials(env, monkeypatch):
    from tests.test_mcp import _load_server_module
    monkeypatch.setenv('THTH_REPORT_CREDENTIALS', str(env['path']))
    monkeypatch.setenv('THTH_REPORT_TOKEN', env['bearer'])
    server = _load_server_module()
    tools = {tool['name']: tool for tool in server.server_tools(env['context'])}
    assert {'thth_media_upload_url', 'thth_media_complete'} <= set(tools)
    assert tools['thth_media_upload_url']['inputSchema']['properties']['size'] == {'type': 'integer'}
    assert tools['thth_draft_put']['inputSchema']['properties']['media'] == {'type': 'array'}
    assert all('一回限り' in tools['thth_media_upload_url']['description'] for _ in (0,))
    env['value']['credentials'][0]['writes'] = False
    env['path'].write_text(json.dumps(env['value']))
    reader = report_http.load_credentials(env['path'])[1][0][3]
    assert not {'thth_media_upload_url', 'thth_media_complete'} & {t['name'] for t in server.server_tools(reader)}
    assert server.call_tool('thth_media_upload_url', {'account': 'alpha'}).get('isError')
