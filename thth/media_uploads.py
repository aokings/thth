"""Invited-user uploads: durable intent first, sanitize before the repo.

An invited user never sends bytes through MCP. `thth_media_upload_url` records
what the user promised (kind/mime/size/sha256) in private state *before* any
Worker request, then opens a one-shot upload session; `thth_media_complete`
closes it, reads the stored bytes back through the signed read, verifies the
declared SHA, applies the stage-1 sanitize and only then places the public
bytes in the managed repo. Nothing here reflects a path, a value or a capability
into a reason: every refusal is one of the static codes below.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import time
import urllib.error
from pathlib import Path

from . import accounts, managed_repo, redact, server_files, writeback
from .approval_relay import OPAQUE
from .report_service import ReportServiceError

SHA256 = re.compile(r'[0-9a-f]{64}\Z')
OPERATIONS = frozenset(('media_upload_url', 'media_complete'))

# One table for the invited口. `kind` is what the user declares, the mapping is
# the transport mime the Worker accepts (callback/src/media.js `MIME`) paired
# with the extension the public file gets, and the number is the size cap in
# bytes (provisional: image 8 MB is the Threads image ceiling, video 1 GB is the
# Worker's raw-source ceiling, audio 100 MB is the multipart threshold).
# `audio` carries the Worker's audio transport set (stage 10 follow-up): an
# invited user can now upload the audio Mastodon accepts. The cap is the
# multipart threshold, so an audio upload is always a single PUT.
KINDS = {
    'image': (8_000_000, {'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'image/gif': 'gif'}),
    'video': (1_000_000_000, {'video/mp4': 'mp4', 'video/quicktime': 'mov'}),
    'audio': (100_000_000, {'audio/mpeg': 'mp3', 'audio/flac': 'flac', 'audio/ogg': 'ogg',
                            'audio/wav': 'wav', 'audio/x-wav': 'wav', 'audio/webm': 'webm',
                            'audio/mp4': 'm4a'}),
}
# Inspected (kind, format) -> (accepted transport mimes, public extension). The
# declared mime is checked against the *inspected* pair, never trusted on its
# own, and the pair carries the kind because one container name means two
# things: an inspected `mp4` is video/mp4 or audio/mp4 depending on its tracks,
# and `webm` is audio here only because the video WebM is refused by policy.
FORMATS = {
    ('image', 'jpeg'): (('image/jpeg',), 'jpg'), ('image', 'png'): (('image/png',), 'png'),
    ('image', 'webp'): (('image/webp',), 'webp'), ('image', 'gif'): (('image/gif',), 'gif'),
    ('video', 'mp4'): (('video/mp4',), 'mp4'), ('video', 'mov'): (('video/quicktime',), 'mov'),
    ('audio', 'mp3'): (('audio/mpeg',), 'mp3'), ('audio', 'flac'): (('audio/flac',), 'flac'),
    ('audio', 'ogg'): (('audio/ogg',), 'ogg'), ('audio', 'ogg_vorbis'): (('audio/ogg',), 'ogg'),
    ('audio', 'wav'): (('audio/wav', 'audio/x-wav'), 'wav'),
    ('audio', 'webm'): (('audio/webm',), 'webm'), ('audio', 'mp4'): (('audio/mp4',), 'm4a'),
}


def extension(kind, fmt):
    row = FORMATS.get((kind, fmt))
    return row[1] if row else None


def derive_media_id(subject):
    """`media_id` は upload subject の**一方向の像**（第 10 段・第 9 段の直し）。

    第 9 段は `media_id` をそのまま subject（＝ PUT の URL の最後の一節）にして
    いた。すると **`media_id` を知る者は upload URL を組み立てられる**——応答に
    だけ出すはずの一回限りの URL が、`draft_put` に渡す識別子から復元できる。
    ここで切る: 外に出るのは `media_id` だけで、subject は控え（mode 600）の中
    にしか無い。sha256 は一方向なので、`media_id` から subject は作れない。
    """
    digest = hashlib.sha256(subject.encode()).digest()
    return 'm' + base64.urlsafe_b64encode(digest).decode().rstrip('=')[:42]
MEDIA_DIRECTORY = 'docs/sns/media'
TTL_MS = 600_000            # one upload session lives 10 minutes (design §3)
MULTIPART_THRESHOLD = 100_000_000
PART_SIZE = 5 * 1024 * 1024
GC_AGE_SECONDS = 86_400     # the lifecycle 24h backstop, mirrored on the VM
MAX_DRAFT_MEDIA = 8         # transport bound only; lint stays the policy authority
MAX_ALT_BYTES = 2000

STATES = ('pending', 'ready', 'rejected', 'unknown')
# Leading refusal codes produced by the stage-1 sanitize. Only these (and the
# generic fallback) become a caller-visible reason, so a future message change
# cannot turn into a new, unreviewed public string.
SANITIZE_REASONS = frozenset((
    'location_metadata_present', 'location_metadata_unverifiable', 'sanitize_unverifiable',
    'unsupported_attachment', 'unsupported_attachment_structure', 'invalid_attachment_structure',
    'media_limit_exceeded', 'media_sanitize_refused'))
REASONS = frozenset((
    'media_kind_unsupported', 'media_mime_unsupported', 'media_size_exceeded', 'media_unknown',
    'media_not_ready', 'media_owner_mismatch', 'media_already_completed', 'media_upload_failed',
    'media_upload_unknown', 'media_session_unavailable', 'media_intent_unavailable',
    'media_commit_unconfirmed', 'media_kind_mismatch', 'media_mime_mismatch')) | SANITIZE_REASONS


def error(reason):
    raise ReportServiceError(reason)


def directory(account):
    if not accounts.name_is_safe(account):
        raise ValueError('invalid_account')
    return Path(accounts.state_dir_for(account)) / 'media-uploads'


def _valid(value, media_id):
    """Private storage is fallible: validate shape and every redundant binding."""
    required = {'schema_version', 'media_id', 'subject', 'actor', 'account', 'kind', 'mime', 'size',
                'sha256', 'status', 'created_at', 'expires_at'}
    optional = {'reason', 'public_sha256', 'format', 'width', 'height', 'duration', 'warnings',
                'file', 'completed_at'}
    if (type(value) is not dict or not required <= value.keys() or value.keys() - required - optional
            or value['schema_version'] != 1 or value['media_id'] != media_id
            or not isinstance(media_id, str) or not OPAQUE.fullmatch(media_id)
            or not isinstance(value['subject'], str) or not OPAQUE.fullmatch(value['subject'])
            or derive_media_id(value['subject']) != media_id
            or not isinstance(value['account'], str) or not accounts.name_is_safe(value['account'])
            or not isinstance(value['actor'], str) or not accounts.name_is_safe(value['actor'])
            or value['kind'] not in KINDS or not isinstance(value['mime'], str)
            or type(value['size']) is not int or value['size'] < 1
            or not isinstance(value['sha256'], str) or not SHA256.fullmatch(value['sha256'])
            or value['status'] not in STATES
            or any(type(value[key]) is not int or value[key] <= 0 for key in ('created_at', 'expires_at'))):
        return False
    if 'reason' in value and value['reason'] not in REASONS:
        return False
    if value['status'] == 'ready':
        if not {'public_sha256', 'format', 'file', 'warnings'} <= value.keys():
            return False
        if (not isinstance(value['public_sha256'], str) or not SHA256.fullmatch(value['public_sha256'])
                or (value['kind'], value['format']) not in FORMATS
                or value['file'] != MEDIA_DIRECTORY + '/' + value['public_sha256'] + '.' + extension(value['kind'], value['format'])
                or type(value['warnings']) is not list or any(not isinstance(x, str) for x in value['warnings'])):
            return False
    return True


def _load(fd, media_id):
    if not isinstance(media_id, str) or not OPAQUE.fullmatch(media_id):
        error('media_unknown')
    try:
        value = json.loads(server_files.read_at(fd, media_id + '.json', private=True))
    except FileNotFoundError:
        error('media_unknown')
    except (OSError, ValueError, RecursionError):
        error('media_intent_unavailable')
    if not _valid(value, media_id):
        error('media_intent_unavailable')
    return value


def _save(fd, record):
    server_files.replace_at(fd, record['media_id'] + '.json', server_files.encode(record), private=True)


def _result(record):
    return {'media_id': record['media_id'], 'public_sha256': record['public_sha256'],
            'format': record['format'], 'kind': record['kind'], 'width': record.get('width'),
            'height': record.get('height'), 'duration': record.get('duration'),
            'warnings': list(record['warnings'])}


def _static_reason(exc):
    """Keep only the leading code of a sanitize refusal, never its detail."""
    code = str(exc).split(':', 1)[0].strip()
    return code if code in SANITIZE_REASONS else 'media_sanitize_refused'


def _owned(context, account, request, keys, required):
    """Reauthenticate the write capability, then check the request shape."""
    from . import server_writes
    if (type(request) is not dict or set(request) - set(keys) - {'operation', 'account'}
            or not set(required) | {'account', 'operation'} <= set(request)):
        error('invalid_request')
    cfg = server_writes.current(context, account, write=True)
    clone, _ = managed_repo.locations(account)
    if cfg['repo_dir'] != str(clone):
        error('managed_repo_required')
    return cfg


def upload_url(context, request, via):
    """Durable intent, then a one-shot session. The URL exists in this reply only."""
    account = request['account']
    _owned(context, account, request, ('kind', 'size', 'sha256', 'mime'), ('kind', 'size', 'sha256', 'mime'))
    kind, mime, size, sha = request['kind'], request['mime'], request['size'], request['sha256']
    if not isinstance(kind, str) or kind not in KINDS:
        error('media_kind_unsupported')
    cap, mimes = KINDS[kind]
    if not isinstance(mime, str) or mime not in mimes:
        error('media_mime_unsupported')
    if type(size) is not int or size < 1 or size > cap:
        error('media_size_exceeded')
    if not isinstance(sha, str) or not SHA256.fullmatch(sha):
        error('invalid_request')
    from .media_relay import MediaRelay, MediaRelayError, origin
    # The Worker's upload subject and the caller's media_id are different names
    # for the same session: the subject is the one-shot URL's last segment and
    # never leaves this process except inside that URL; the media_id is derived
    # from it and is the only handle the caller ever holds.
    subject = secrets.token_urlsafe(32)
    redact.register_secret(subject)
    media_id = derive_media_id(subject)
    now = int(time.time() * 1000)
    record = dict(schema_version=1, media_id=media_id, subject=subject, actor=context.actor,
                  account=account, kind=kind, mime=mime, size=size, sha256=sha, status='pending',
                  created_at=now, expires_at=now + TTL_MS)
    part_size = PART_SIZE if size > MULTIPART_THRESHOLD else None
    with server_files.directory(directory(account), create=True, private=True) as fd:
        with server_files.lock_at(fd, media_id + '.lock'):
            # The intent is durable before the Worker can hold any object for
            # this account: an unknown session always has a record to land on.
            server_files.replace_at(fd, media_id + '.json', server_files.encode(record), new=True, private=True)
            try:
                client = MediaRelay(account, context.actor)
                value = client.control(subject, 'create', {**client.binding(sha), 'size': size,
                                                          'mime': mime, 'kind': 'source', 'part_size': part_size})
                if value.get('status') != 'pending' or value.get('size') != size:
                    raise MediaRelayError('media_relay_response_invalid')
                if type(value.get('expires_at')) is int and 0 < value['expires_at'] < record['expires_at']:
                    record['expires_at'] = value['expires_at']
            except Exception:
                # No automatic retry: the caller asks for a new session, and the
                # abandoned one expires on the Worker's own clock.
                record.update(status='unknown', reason='media_session_unavailable')
                _save(fd, record)
                error('media_session_unavailable')
            _save(fd, record)
    url = origin() + '/media-upload/' + subject
    redact.register_secret(url)
    return {'media_id': media_id, 'upload_url': url, 'expires_at': record['expires_at'], 'part_size': part_size}


def _sanitize(cfg, snapshot, size):
    from . import media as media_mod, mediaformats
    info = mediaformats.inspect(snapshot.fileno(), size, **media_mod.MEDIUM_POLICY.get(cfg['media'], {}))
    public = info.public_bytes
    if public is not None:
        # Same stability check as the manuscript path: the public bytes must
        # re-inspect to themselves, or they are not a fixed public artefact.
        checked = getattr(mediaformats, info.format)(public)
        if (checked.public_bytes != public
                or (checked.width, checked.height, checked.duration, checked.orientation)
                != (info.width, info.height, info.duration, info.orientation)):
            raise media_mod.MediaError('media: public structure is not stable')
    return info, public


def _store(clone, relative, snapshot, size, public, public_sha, *, actor, via):
    """Write and commit the public file exactly like a draft: never half-written."""
    target = Path(clone) / MEDIA_DIRECTORY
    with server_files.directory(target, create=True) as fd:
        temp = '.write-' + secrets.token_hex(16)
        leaf = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            with os.fdopen(leaf, 'wb') as stream:
                if public is not None:
                    stream.write(public)
                else:
                    at = 0
                    while at < size:
                        chunk = os.pread(snapshot.fileno(), min(1024 * 1024, size - at), at)
                        if not chunk:
                            raise ValueError('media_source_truncated')
                        stream.write(chunk)
                        at += len(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            os.rename(temp, Path(relative).name, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            try:
                os.unlink(temp, dir_fd=fd)
            except FileNotFoundError:
                pass
    state = managed_repo.run(clone, ['status', '--porcelain', '--', relative], check=False)
    if state.returncode:
        raise ValueError('media_commit_unconfirmed')
    if not state.stdout.strip():
        return  # content-addressed and already committed: nothing to record
    def unchanged():
        digest = hashlib.sha256()
        with open(Path(clone) / relative, 'rb') as stream:
            while data := stream.read(1024 * 1024):
                digest.update(data)
        return digest.hexdigest() == public_sha
    ok, _ = writeback.commit_and_push(str(clone), rel_path=relative,
                                      message=f'media by={actor} via={via}', validate=unchanged)
    if not ok:
        raise ValueError('media_commit_unconfirmed')


def complete(context, request, via):
    """Close the session, verify the bytes, sanitize, then commit the public file."""
    from . import media as media_mod, mediaformats, server_writes
    from .media_relay import MediaRelay, MediaRelayError
    account = request['account']
    cfg = _owned(context, account, request, ('media_id',), ('media_id',))
    media_id = request['media_id']
    # Validated before it is ever used as a name: locks open by name, too.
    if not isinstance(media_id, str) or not OPAQUE.fullmatch(media_id):
        error('media_unknown')
    clone, _ = managed_repo.locations(account)
    with server_files.account_locks(account, cfg):
        cfg = server_writes.current(context, account, write=True)
        with server_files.directory(directory(account), create=True, private=True) as fd:
            with server_files.lock_at(fd, media_id + '.lock'):
                record = _load(fd, media_id)
                # Ownership is checked on the VM before any Worker call, not
                # only by the Worker's own owner binding.
                if record['account'] != account or record['actor'] != context.actor:
                    error('media_owner_mismatch')
                if record['status'] == 'ready':
                    return _result(record)
                if record['status'] == 'rejected':
                    error('media_already_completed')
                if record['status'] != 'pending':
                    error('media_upload_unknown')
                client = MediaRelay(account, record['actor'])
                try:
                    value = client.control(record['subject'], 'complete', client.binding(record['sha256']))
                    ready = value.get('status') == 'ready' and value.get('sha256') == record['sha256']
                except urllib.error.HTTPError as exc:
                    # A 4xx is a definite refusal; anything else stays unknown.
                    definite = 400 <= exc.code < 500
                    record.update(status='rejected' if definite else 'unknown',
                                  reason='media_upload_failed' if definite else 'media_upload_unknown')
                    _save(fd, record)
                    error(record['reason'])
                except Exception:
                    record.update(status='unknown', reason='media_upload_unknown')
                    _save(fd, record)
                    error('media_upload_unknown')
                if not ready:
                    record.update(status='unknown', reason='media_upload_unknown')
                    _save(fd, record)
                    error('media_upload_unknown')
                try:
                    # Reads the stored bytes back, verifies the declared SHA and
                    # retires the R2 source. Retirement happens once, right after
                    # the verified read: the VM holds the bytes from here on, and
                    # neither a refusal nor a success needs the source again.
                    with client.source_snapshot(record['subject'], record['sha256']) as snapshot:
                        size = os.fstat(snapshot.fileno()).st_size
                        if size != record['size']:
                            raise MediaRelayError('media_source_mismatch')
                        try:
                            info, public = _sanitize(cfg, snapshot, size)
                        except (mediaformats.FormatError, media_mod.MediaError) as exc:
                            reason = _static_reason(exc)
                            record.update(status='rejected', reason=reason)
                            _save(fd, record)
                            error(reason)
                        if info.kind != record['kind']:
                            record.update(status='rejected', reason='media_kind_mismatch')
                            _save(fd, record)
                            error('media_kind_mismatch')
                        if record['mime'] not in FORMATS.get((info.kind, info.format), ((), None))[0]:
                            record.update(status='rejected', reason='media_mime_mismatch')
                            _save(fd, record)
                            error('media_mime_mismatch')
                        public_size = len(public) if public is not None else size
                        if public_size > KINDS[record['kind']][0]:
                            record.update(status='rejected', reason='media_size_exceeded')
                            _save(fd, record)
                            error('media_size_exceeded')
                        public_sha = hashlib.sha256(public).hexdigest() if public is not None else record['sha256']
                        relative = MEDIA_DIRECTORY + '/' + public_sha + '.' + extension(info.kind, info.format)
                        managed_repo.initialize(account, cfg)
                        ok, _, _ = writeback.sync_repo(cfg['repo_dir'])
                        if not ok:
                            raise ValueError('media_commit_unconfirmed')
                        _store(clone, relative, snapshot, size, public, public_sha,
                               actor=record['actor'], via=via)
                except ReportServiceError:
                    raise
                except (MediaRelayError, OSError, ValueError) as exc:
                    # A refused sanitize has already marked the record; what is
                    # left here is transport or storage, never retried on its own.
                    reason = 'media_commit_unconfirmed' if str(exc) == 'media_commit_unconfirmed' else 'media_upload_unknown'
                    record.update(status='unknown', reason=reason)
                    _save(fd, record)
                    error(reason)
                record.update(status='ready', public_sha256=public_sha, format=info.format,
                              width=info.width, height=info.height, duration=info.duration,
                              warnings=sorted(info.metadata_notes), file=relative,
                              completed_at=int(time.time() * 1000))
                _save(fd, record)
                return _result(record)


def lookup(account, actor, media_id):
    """The one gate a draft's media_id passes: same actor, same account, ready."""
    try:
        with server_files.directory(directory(account), private=True) as fd:
            record = _load(fd, media_id)
    except FileNotFoundError:
        error('media_unknown')
    if record['account'] != account or record['actor'] != actor:
        error('media_owner_mismatch')
    if record['status'] != 'ready':
        error('media_not_ready')
    return record


def draft_rows(account, actor, rows):
    """`[{media_id, alt}]` -> the front matter's `[{file, alt}]`. Alt is required."""
    from . import server_writes
    if type(rows) is not list or not rows or len(rows) > MAX_DRAFT_MEDIA:
        error('invalid_request')
    out = []
    for row in rows:
        if type(row) is not dict or set(row) != {'media_id', 'alt'} or not isinstance(row['media_id'], str):
            error('invalid_request')
        alt = row['alt']
        if (not isinstance(alt, str) or not alt.strip() or writeback.has_control_chars(alt)
                or len(alt.encode()) > MAX_ALT_BYTES):
            server_writes.error('invalid_draft', 'alt_required')
        out.append({'file': lookup(account, actor, row['media_id'])['file'], 'alt': alt})
    return out


def execute(context, request, via):
    if request['operation'] == 'media_upload_url':
        return upload_url(context, request, via)
    return complete(context, request, via)


def _referenced(clone):
    """Every media file name mentioned by a tracked non-media file.

    Deliberately textual and deliberately generous: a name that appears in any
    manuscript, bundle or note counts as referenced. Over-keeping costs a file;
    over-deleting costs an approved attachment.
    """
    listed = managed_repo.run(clone, ['ls-files', '-z'], check=False)
    if listed.returncode:
        raise ValueError('managed_repo_unreadable')
    names = set()
    for path in listed.stdout.split('\0'):
        if not path or path.startswith(MEDIA_DIRECTORY + '/'):
            continue
        leaf = Path(clone) / path
        try:
            if leaf.is_symlink() or not leaf.is_file() or leaf.stat().st_size > 4 * 1024 * 1024:
                continue
            text = leaf.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        for found in re.findall(r'[0-9a-f]{64}\.[a-z0-9]{2,4}', text):
            names.add(found)
    return names


def gc(account, *, by, now=None):
    """Drop unreferenced public files older than a day, in one managed commit."""
    from . import admin_log
    if not accounts.name_is_safe(account):
        raise ValueError('invalid_account')
    admin_log.actor(by)
    cfg = accounts.load_account(account)
    clone, _ = managed_repo.locations(account)
    if cfg.get('repo_dir') != str(clone):
        raise ValueError('managed_repo_required')
    moment = time.time() if now is None else now
    removed = kept = intents = 0
    with server_files.account_locks(account, cfg):
        ok, _, _ = writeback.sync_repo(cfg['repo_dir'])
        if not ok:
            raise ValueError('managed_repo_unsynced')
        names = _referenced(clone)
        folder = Path(clone) / MEDIA_DIRECTORY
        drop = []
        for leaf in sorted(folder.glob('*')) if folder.is_dir() else []:
            if leaf.is_symlink() or not leaf.is_file():
                continue
            if leaf.name in names or moment - leaf.stat().st_mtime <= GC_AGE_SECONDS:
                kept += 1
                continue
            drop.append(MEDIA_DIRECTORY + '/' + leaf.name)
        for path in drop:
            os.unlink(Path(clone) / path)
        if drop:
            ok, _ = writeback.commit_and_push(str(clone), rel_path=drop, message=f'media gc by={by}')
            if not ok:
                raise ValueError('media_gc_unconfirmed')
            removed = len(drop)
        gone = {Path(path).name for path in drop}
        try:
            with server_files.directory(directory(account), private=True) as fd:
                for name in sorted(os.listdir(fd)):
                    if not name.endswith('.json'):
                        continue
                    try:
                        record = _load(fd, name[:-5])
                    except ReportServiceError:
                        continue
                    stale = moment * 1000 - record['created_at'] > GC_AGE_SECONDS * 1000
                    held = record['status'] == 'ready' and Path(record['file']).name not in gone
                    if stale and not held:
                        os.unlink(name, dir_fd=fd)
                        intents += 1
        except FileNotFoundError:
            pass
    return {'account': account, 'removed_count': removed, 'kept_count': kept,
            'intents_removed': intents, 'reason': None}
