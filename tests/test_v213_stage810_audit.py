"""第 8-10 段の独立監査の直し（VM 側）: 送る前に断る境界と、後片付けの原子性。"""
import contextlib
import json
import subprocess
from pathlib import Path

import pytest

from thth import approval_jobs as jobs, approval_relay as relay
from thth import media as media_mod, media_uploads, server_writes as writes
from thth.report_service import ReportServiceError
from tests.test_v212_server_writes import draft, env, remote  # noqa: F401
from tests.test_v213_approval_attachments import REQUEST, body_of, relay_client, stage  # noqa: F401
from tests.test_v213_media_foundation import png


def widen(env, limit):
    """台帳の char_limit を広げる（大きな本文で body の境界を試すため）。"""
    path = Path(env['root']) / 'accounts/alpha.json'
    value = json.loads(path.read_text())
    value['char_limit'] = limit
    path.write_text(json.dumps(value))


def images(env, count, alt, body=None):
    if body is not None:
        widen(env, len(body) + 1000)
    rows = [(f'docs/sns/media/{n}.png', alt) for n in range(count)]
    files = {name: png() for name, _ in rows}
    one = stage(env, rows, files)
    if body is not None:
        path = next((Path(env['root']) / 'repos/_server/alpha/queue').glob('*.md'))
        path.write_text(path.read_text().replace('投稿本文', body, 1))
        git = ['git', '-C', str(Path(env['root']) / 'repos/_server/alpha'),
               '-c', 'user.name=t', '-c', 'user.email=t@invalid']
        for args in (['add', '-A'], ['commit', '-m', 'body'], ['push', 'origin', 'HEAD']):
            subprocess.run(git + args, check=True, capture_output=True)
    return one


def only_job(account='alpha'):
    names = list(jobs.directory(account).glob('*.json'))
    assert len(names) == 1
    return json.loads(names[0].read_text())


def test_alt_has_one_ceiling_for_the_manuscript_and_the_invited_mouth():
    assert media_mod.MAX_ALT_BYTES == media_uploads.MAX_ALT_BYTES == 2000
    media_mod.validate([{'file': 'a.png', 'alt': 'x' * 2000}])
    with pytest.raises(media_mod.MediaError) as caught:
        media_mod.validate([{'file': 'a.png', 'alt': 'x' * 2001}])
    assert str(caught.value) == 'media: alt_too_long'
    # 数えるのは byte（招待者の口と同じ）。
    with pytest.raises(media_mod.MediaError):
        media_mod.validate([{'file': 'a.png', 'alt': 'あ' * 667}])
    # 代表画像の alt も同じ関門を通る。
    with pytest.raises(media_mod.MediaError):
        media_mod.validate([{'file': 'a.png', 'alt': 'ok',
                             'thumbnail_file': 'b.png', 'thumbnail_alt': 'x' * 2001}])


class Row:
    """A prepared row without a repo: lint refuses 21 Threads 添付 long before
    this backstop, so the transport bound is exercised at its own function."""

    def __init__(self, index, role='media', kind='image', alt='点'):
        self.manifest = {'index': index, 'role': role, 'kind': kind, 'format': 'png',
                         'public_sha256': 'ab' * 32, 'public_size': 100, 'width': 1,
                         'height': 1, 'duration': None, 'alt': alt}


def rows_reach_attachments(monkeypatch, rows):
    """Call `_attachments` with a fake prepare and a relay that must not be used."""
    calls = []

    class Relay:
        def __init__(self, account, actor): pass
        def upload_prepared(self, item): calls.append('upload'); return 'u' * 43
        def grant(self, item, source, *, purpose='provider', expires_at=None):
            calls.append('grant'); return {'subject': 'p' * 43}

    @contextlib.contextmanager
    def prepare(repo_dir, fm, medium):
        yield ({'files': []}, rows)

    monkeypatch.setattr('thth.media_relay.MediaRelay', Relay)
    monkeypatch.setattr(media_mod, 'prepare', prepare)
    monkeypatch.setattr(media_mod, 'prepared_component', lambda manifest: 'same')
    media = {'manifest': {'attachments': [], 'post_options': {}, 'captions': []},
             'repo_dir': '/nowhere', 'front_matter': {}, 'medium': 'threads'}
    payload = lambda display, typed: {'attachments': display, 'typed': typed}
    return calls, lambda: jobs._attachments('alpha', 'person', media, {}, payload)


def test_twenty_one_attachment_rows_are_refused_before_any_upload(monkeypatch):
    # 代表画像・字幕も 1 行として数える（F10）。
    rows = ([Row(n) for n in range(1, 19)]
            + [Row(1, role='thumbnail'), Row(1, role='caption', kind='caption', alt='ja')]
            + [Row(19)])
    calls, run = rows_reach_attachments(monkeypatch, rows)
    with pytest.raises(jobs.BoundsRefused) as caught:
        run()
    assert str(caught.value) == 'approval_attachments_too_many'
    assert calls == [], 'bytes were uploaded for a request the Worker refuses'
    calls, run = rows_reach_attachments(monkeypatch, rows[:20])
    assert len(run()[0]) == 20 and calls.count('upload') == 19


def test_twenty_attachment_rows_still_pass(env, remote, relay_client):
    one = images(env, 20, '点')
    writes.execute(env['context'], dict(REQUEST, draft_id=one['draft_id']))
    assert len(body_of(remote)['attachments']) == 20 and len(relay_client['uploads']) == 20


def test_an_assembled_body_over_90_kib_is_refused_before_any_upload(env, remote, relay_client):
    one = images(env, 20, 'x' * 2000, body='本' * 17000)
    with pytest.raises(ReportServiceError) as caught:
        writes.execute(env['context'], dict(REQUEST, draft_id=one['draft_id']))
    assert str(caught.value) == 'approval_request_too_large'
    assert relay_client['uploads'] == [] and relay_client['grants'] == []
    assert remote.rows == {}
    job = only_job()
    assert job['status'] == 'failed' and job['reason'] == 'approval_request_too_large'


def test_a_body_just_under_the_bound_is_sent_and_is_byte_exact(env, remote, relay_client):
    one = images(env, 20, 'x' * 2000, body='x' * 40000)
    writes.execute(env['context'], dict(REQUEST, draft_id=one['draft_id']))
    sent = json.dumps(body_of(remote), ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    assert len(sent) <= jobs.MAX_CREATE_BODY
    # 下見は placeholder で測るが、capability は 43 文字なので送る byte 数と同じ。
    assert len(sent) > 70 * 1024
    assert all(len(row['preview']) == len(jobs.PREVIEW_PLACEHOLDER)
               for row in body_of(remote)['attachments'] if row['kind'] == 'image')


@pytest.mark.parametrize('status,expected', [(400, 'approval_registration_rejected'),
                                             (409, 'approval_registration_rejected'),
                                             (429, 'approval_registration_rejected'),
                                             (500, 'approval_registration_unknown'),
                                             (None, 'approval_registration_unknown')])
def test_worker_refusal_on_create_is_definite_and_5xx_stays_unknown(env, monkeypatch, status, expected):
    one = draft(env)

    def answer(kind, subject, operation, body):
        if kind == 'person':
            return {'active': True, 'locked': False, 'generation': 'g' * 43}
        raise relay.RelayError('approval_relay_outcome_unknown', status=status)

    monkeypatch.setattr(relay, 'signed_request', answer)
    with pytest.raises(ReportServiceError) as caught:
        writes.execute(env['context'], dict(REQUEST, draft_id=one['draft_id']))
    assert str(caught.value) == expected
    job = only_job()
    assert job['reason'] == expected
    assert job['status'] == ('failed' if expected.endswith('rejected') else 'unknown')
