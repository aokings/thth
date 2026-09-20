"""Media source foundation; provider/public preparation deliberately unavailable."""
import hashlib
import json
import os
from pathlib import Path
import struct
import zlib

import pytest

from thth import approval, bundle, lint, media, queuefile, writeback


def png():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00')) + chunk(b'IEND', b''))


def jpeg():
    # One grey baseline pixel: DC=0, EOB, one-bit canonical Huffman codes.
    def segment(marker, data):
        return b'\xff' + bytes([marker]) + struct.pack('>H', len(data) + 2) + data
    counts = b'\x01' + bytes(15)
    return (b'\xff\xd8' + segment(0xdb, b'\x00' + bytes([1]) * 64)
            + segment(0xc0, bytes([8, 0, 1, 0, 1, 1, 1, 0x11, 0]))
            + segment(0xc4, b'\x00' + counts + b'\x00' + b'\x10' + counts + b'\x00')
            + segment(0xda, bytes([1, 1, 0, 0, 63, 0])) + b'\x3f\xff\xd9')


def v1(block='media:\n  - file: docs/red.png\n    alt: 赤い点'):
    return f'---\nthth: 1\naccount: demo\npublish_at: 2030-01-01T12:00:00+09:00\nstatus: draft\n{block}\n---\n## threads\n本文\n'


def v2():
    return ('---\nthth: 2\naccount: demo\nposts:\n  - index: 1\n    media:\n'
            '      - file: docs/red.png\n        alt: 赤い点\n  - index: 2\n'
            '    media:\n      - file: docs/grey.jpg\n        alt: 灰色\n'
            'status: draft\n---\n## threads\n本文\n<!-- thth: 2/2 -->\n続き\n')


def test_parse_and_writeback_keep_attachment_rows():
    q = queuefile.parse_text(v1(), 'q.md')
    assert not q.malformed
    assert q.get('media') == [{'file': 'docs/red.png', 'alt': '赤い点'}]
    assert queuefile.parse_text(writeback.front_matter_text(v1(), {'status': 'approved', 'alt': 'top'}), 'q').get('media') == q.get('media')
    b = bundle.parse_text(v2(), 'q.md')
    assert not b.malformed and len(b.posts) == 2
    changed = bundle.set_post_fields(v2(), 2, {'post_id': '123'})
    new = bundle.parse_text(changed, 'q.md')
    assert new.posts[1]['post_id'] == '123' and 'post_id' not in new.posts[0]
    assert [p['media'] for p in new.posts] == [p['media'] for p in b.posts]
    assert changed.split('---\n')[-1] == v2().split('---\n')[-1]


@pytest.mark.parametrize('block', [
    'media: arbitrary', 'media:',
    'media :\n  - file: docs/red.png\n    alt: a', 'media:\n  - file: docs/red.png',
    'media:\n  - file: docs/red.png\n    alt: ""',
    'media:\n  - file: docs/red.png\n    alt: a\n    alt: b',
    'media:\n  - file: docs/red.png\n    alt: a\n    status: approved',
    'media:\n  - file: docs/red.png\n    alt: a\n  - file: docs/red.png\n    alt: b',
    'media:\n - file: docs/red.png\n   alt: a',
    'media:\n\t- file: docs/red.png\n\t  alt: a',
    ' media:\n   - file: docs/red.png\n     alt: a',
    'media:\n  - file: docs/red.png\n    alt: "a\\nb"',
    'media: []\nmedia: []',
])
def test_invalid_media_is_malformed(block):
    assert queuefile.parse_text(v1(block), 'q').malformed


@pytest.mark.parametrize('path', ['/tmp/a', '../a', 'docs/../a', 'docs//a', './a', 'a\\b', '.git/objects/a', 'C:/a', 'a\x00b'])
def test_paths_reject_before_open(path, monkeypatch):
    monkeypatch.setattr(os, 'open', lambda *a, **kw: pytest.fail('must not open'))
    with pytest.raises(media.MediaError):
        with media.pin_sources('/unused', [{'file': path, 'alt': 'description'}]):
            pytest.fail('entered')


@pytest.fixture
def repo(tmp_path):
    # pytest's macOS /var alias is resolved before strict no-symlink traversal.
    root = tmp_path.resolve() / 'repo'
    (root / 'docs').mkdir(parents=True)
    (root / 'docs/red.png').write_bytes(png())
    (root / 'docs/grey.jpg').write_bytes(jpeg())
    return root


def rows():
    return [{'file': 'docs/red.png', 'alt': '赤い点'}, {'file': 'docs/grey.jpg', 'alt': '灰色'}]


def test_pinned_exact_generated_bytes_and_no_write(repo):
    before = {p: p.read_bytes() for p in repo.rglob('*') if p.is_file()}
    with media.pin_sources(repo, rows()) as sources:
        for source, expected in zip(sources, [png(), jpeg()]):
            assert os.read(source.fd, source.size) == expected
            assert source.source_sha256 == hashlib.sha256(expected).hexdigest()
            assert source.fingerprint()['size'] == len(expected)
    assert before == {p: p.read_bytes() for p in repo.rglob('*') if p.is_file()}


@pytest.mark.parametrize('kind', ['symlink', 'directory', 'fifo', 'hardlink', 'parent_symlink'])
def test_unsafe_sources_reject(repo, kind):
    target = repo / 'docs/red.png'
    outside = repo.parent / 'outside.png'
    outside.write_bytes(png())
    target.unlink()
    if kind == 'symlink': target.symlink_to(outside)
    elif kind == 'directory': target.mkdir()
    elif kind == 'fifo': os.mkfifo(target)
    elif kind == 'hardlink': os.link(outside, target)
    else:
        (repo / 'docs/grey.jpg').unlink()
        (repo / 'docs').rmdir()
        (repo / 'docs').symlink_to(repo.parent, target_is_directory=True)
    with pytest.raises(media.MediaError):
        with media.pin_sources(repo, rows()):
            pytest.fail('unsafe pin accepted')
    assert outside.read_bytes() == png()


@pytest.mark.parametrize('change', ['replace', 'mutate'])
def test_change_detected_while_pin_retained(repo, change):
    with pytest.raises(media.MediaError, match='source_changed'):
        with media.pin_sources(repo, rows()) as sources:
            p = repo / 'docs/red.png'
            if change == 'replace': p.unlink()
            p.write_bytes(jpeg())
            sources[0].verify()


def args():
    return dict(section='本文', account='demo', reply_to=None, topic=None, publish_at='2030-01-01T12:00:00+09:00')


def test_legacy_hash_bytes_and_media_binding(repo):
    legacy = '本文\x1fdemo\x1f\x1f\x1f2030-01-01T12:00:00+09:00'
    expected = hashlib.sha256(legacy.encode()).hexdigest()
    assert approval.compute_approved_sha(**args()) == expected
    assert approval.compute_approved_sha(**args(), media_sources=[]) == expected
    with media.pin_sources(repo, rows()) as sources:
        manifest = [s.fingerprint() for s in sources]
    bound = approval.compute_approved_sha(**args(), media_sources=manifest)
    assert bound != expected
    for field, value in [('alt', '別の説明'), ('source_sha256', '0' * 64), ('size', 999), ('file', 'docs/new.png')]:
        changed = json.loads(json.dumps(manifest))
        changed[0][field] = value
        assert approval.compute_approved_sha(**args(), media_sources=changed) != bound
    assert approval.compute_approved_sha(**args(), media_sources=list(reversed(manifest))) != bound
    send = dict(text='本文', account='demo', reply_to=None, topic=None)
    assert approval.compute_send_digest(**send) == hashlib.sha256('本文\x1fdemo\x1f\x1f'.encode()).hexdigest()[:12]
    assert approval.compute_send_digest(**send, media_sources=manifest) != approval.compute_send_digest(**send)
    ba = dict(segments=['a', 'b'], account='demo', topic=None, publish_at=args()['publish_at'], continue_until=args()['publish_at'])
    assert approval.compute_bundle_sha(**ba, media_sources=[[], []]) == approval.compute_bundle_sha(**ba)
    assert approval.compute_bundle_sha(**ba, media_sources=[manifest, []]) != approval.compute_bundle_sha(**ba, media_sources=[[], manifest])


def test_stage1_prepares_and_approves_but_provider_is_unavailable(repo, monkeypatch):
    from thth import cli, select, accounts
    cfg = {'account': 'demo', 'media': 'threads', 'repo_dir': str(repo)}
    monkeypatch.setattr(accounts, 'load_account', lambda name: cfg)
    p = repo / 'q.md'
    p.write_text(v1())
    assert not [e for e in lint.lint_file(str(p)) if not lint.is_warning(e)]
    prepared, reason = cli._prepare_one(str(p))
    assert reason is None and prepared['media_manifest']['files'][0]['public_sha256']
    q = queuefile.parse_text(writeback.front_matter_text(v1(), {'status': 'approved', 'approved_sha': prepared['approved_sha']}), str(p))
    q.verified = True
    result = select._validate_all([q], account_name='demo', account_cfg=cfg, recent_texts=set())
    assert not result[0] and result[1][0].reason == 'media_provider_unavailable'


@pytest.mark.parametrize('old,new', [
    ('        alt: 赤い点', '        alt: 赤い点\n        alt: 重複'),
    ('        alt: 赤い点', '        status: approved'),
    ('    media:', '   media:'),
    ('      - file: docs/red.png', '      - file: ../outside.png'),
])
def test_bundle_media_malformed_never_becomes_another_post(old, new):
    assert bundle.parse_text(v2().replace(old, new, 1), 'q').malformed


def test_legacy_nondefault_posts_indent_writeback():
    original = '---\nthth: 2\nposts:\n - index: 1\n - index: 2\n---\nbody'
    changed = bundle.set_post_fields(original, 2, {'post_id': '123'})
    b = bundle.parse_text(changed, 'q')
    assert not b.malformed and len(b.posts) == 2
    assert b.posts == [{'index': '1'}, {'index': '2', 'post_id': '123'}]


def test_direct_core_guard_never_constructs_adapter():
    from thth import core
    q = queuefile.parse_text(v1(), 'q')
    result = core._throw_chosen('demo', {'media': 'threads'}, '/unused', 'run',
                                'production', q, '本文', None,
                                lambda *args: None,
                                lambda *args: pytest.fail('adapter must not be constructed'))
    assert result.exit_code == 2 and result.action == 'media_provider_unavailable'


@pytest.mark.parametrize('bad', [False, {}, 'ignored', [{'file': 'a', 'alt': 'a', 'source_sha256': '0'*64, 'size': True}],
                                [{'file': 'a', 'alt': 'a', 'source_sha256': '0'*64, 'size': 1, 'unknown': 1}]])
def test_bad_manifest_loud_rejection(bad):
    with pytest.raises(media.MediaError):
        approval.compute_approved_sha(**args(), media_sources=bad)



def test_fstat_fault_closes_leaf(repo, monkeypatch):
    import errno
    real_open, real_fstat = os.open, os.fstat
    opened = []
    def track(path, *args, **kwargs):
        fd = real_open(path, *args, **kwargs)
        if path == 'red.png':
            opened.append(fd)
        return fd
    def fail_leaf(fd):
        if fd in opened:
            raise OSError(errno.EIO, 'synthetic fstat fault')
        return real_fstat(fd)
    monkeypatch.setattr(os, 'open', track)
    monkeypatch.setattr(os, 'fstat', fail_leaf)
    with pytest.raises(media.MediaError, match='source_unreadable'):
        with media.pin_sources(repo, rows()):
            pytest.fail('fault accepted')
    assert len(opened) == 1
    leaked = []
    for fd in opened:
        try:
            real_fstat(fd)
            leaked.append(fd)
        except OSError as exc:
            assert exc.errno == errno.EBADF
    for fd in leaked:
        os.close(fd)
    assert leaked == [], 'leaf fd must close on fstat failure'


@pytest.mark.parametrize('value', ['', 'null', '[]', '{}'])
def test_first_post_media_field_is_explicitly_rejected(value, tmp_path, monkeypatch):
    from thth import cli
    raw = v2().replace('  - index: 1\n    media:\n      - file: docs/red.png\n        alt: 赤い点',
                       '  - media: ' + value + '\n    index: 1')
    b = bundle.parse_text(raw, 'q')
    assert b.malformed
    assert 'media:' in bundle.why_malformed(raw)
    p = tmp_path / 'q.md'
    p.write_text(raw)
    monkeypatch.setattr(cli.accounts_mod, 'load_account', lambda n: {'media': 'threads', 'hashtags': False})
    prepared, reason = cli._prepare_one(str(p))
    assert prepared is None and 'thth: 2' in reason


@pytest.mark.parametrize('value', ['', 'null', '{}'])
def test_empty_media_declaration_not_empty_array_in_either_version(value):
    assert queuefile.parse_text(v1('media: ' + value), 'q').malformed
    raw = v2().replace('    media:\n      - file: docs/red.png\n        alt: 赤い点',
                       '    media: ' + value)
    assert bundle.parse_text(raw, 'q').malformed


def test_regular_empty_array_has_no_attachment():
    q = queuefile.parse_text(v1('media: []'), 'q')
    assert not q.malformed and q.get('media') == []
    raw = v2().replace('    media:\n      - file: docs/red.png\n        alt: 赤い点', '    media: []')
    b = bundle.parse_text(raw, 'q')
    assert not b.malformed and b.posts[0]['media'] == []



def test_empty_first_media_cannot_prepare_plaintext_approval(tmp_path, monkeypatch):
    from thth import cli, adapters
    monkeypatch.setattr(adapters, 'make_adapter', lambda *a, **k: pytest.fail('provider access forbidden'))
    valid = v2().replace('account: demo\n', 'account: demo\npublish_at: 2030-01-01T12:00:00+09:00\ncontinue_until: 2030-01-02T12:00:00+09:00\n')
    valid = valid.replace('    media:\n      - file: docs/red.png\n        alt: 赤い点', '    media: []')
    valid = valid.replace('    media:\n      - file: docs/grey.jpg\n        alt: 灰色', '    media: []')
    p = tmp_path / 'q.md'
    monkeypatch.setattr(cli.accounts_mod, 'load_account', lambda n: {'media': 'threads', 'hashtags': False})
    p.write_text(valid)
    prepared, reason = cli._prepare_one(str(p))
    assert prepared is not None and reason is None  # real, otherwise valid approval
    invalid = valid.replace('  - index: 1\n    media: []', '  - media:\n    index: 1')
    p.write_text(invalid)
    prepared, reason = cli._prepare_one(str(p))
    assert prepared is None and 'thth: 2' in reason
    assert p.read_text() == invalid
