"""Append-only, bounded administration events. Never store credentials."""
import json
import os
import re
import socket
import stat
from . import accounts, handoff_cursor, jst, redact

EVENTS = frozenset(('account_added', 'account_updated', 'account_removed', 'token_set',
                   'token_refreshed', 'token_revoked', 'production_enabled', 'production_disabled'))
SECRET = re.compile(r'token|secret|password|env|email|notification|smtp|ping', re.I)
MAIL = re.compile(r'[^\s<>"@]+@[^\s<>"@]+\.[^\s<>"@]+')


def actor(by):
    if not isinstance(by, str) or not by.strip() or len(by) > 256 or any(ord(c) < 32 for c in by):
        raise ValueError('admin_by_required: --by is required')
    return by


def clean(value):
    if isinstance(value, str):
        return MAIL.sub('[redacted-email]', redact.redact(value))
    if isinstance(value, dict):
        return {k: ('present' if v else 'absent') if SECRET.search(k) else clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value


def difference(before, after):
    result = {}
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if old != new:
            result[key] = (['present' if old else 'absent', 'present' if new else 'absent']
                           if SECRET.search(key) else [clean(old), clean(new)])
    return result


def provenance(by, via='cli'):
    return dict(created_at=jst.iso(), created_by=actor(by), created_via=via, created_host=socket.gethostname())


def append(event, account, cfg, *, by, via='cli', diff=None, run_id=None):
    actor(by)
    if event not in EVENTS or not accounts.name_is_safe(account) or via not in ('cli', 'mcp'):
        raise ValueError('invalid_admin_event')
    row = dict(at=jst.iso(), by=clean(by), via=via, host=socket.gethostname(), event=event,
               account=account, medium=cfg.get('media'), diff=diff or {}, run_id=run_id)
    # Diff is produced here as well as at call sites; no raw private values can pass.
    row['diff'] = {k: (['present' if x not in (None, False, '', 'absent') else 'absent' for x in v]
                      if SECRET.search(k) else clean(v)) for k, v in row['diff'].items()}
    directory = handoff_cursor._directory('_admin', create=True)
    try:
        fd = os.open('accounts.ndjson', os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=directory)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
                raise ValueError('admin_log_unreadable')
            data = (json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n').encode()
            if os.write(fd, data) != len(data):
                raise OSError('admin_log_short_write')
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        os.close(directory)
    return row


def read(*, since=None, account=None, event=None):
    cutoff = jst.parse(since) if since is not None else None
    if since is not None and cutoff is None:
        raise ValueError('invalid_since')
    rows, broken = [], 0
    directory = None
    try:
        directory = handoff_cursor._directory('_admin')
        fd = os.open('accounts.ndjson', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd) as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('admin_log_unreadable')
            for line in stream:
                try:
                    row = json.loads(line)
                    if (not isinstance(row, dict) or row.get('event') not in EVENTS or not jst.parse(row.get('at'))
                            or not accounts.name_is_safe(row.get('account')) or not isinstance(row.get('diff'), dict)):
                        raise ValueError('broken')
                    actor(row.get('by'))
                except (ValueError, TypeError):
                    broken += 1
                    continue
                if cutoff and jst.parse(row['at']) < cutoff or account and row['account'] != account or event and row['event'] != event:
                    continue
                safe = {k: row.get(k) for k in ('at', 'by', 'via', 'host', 'event', 'account', 'medium', 'diff', 'run_id')}
                safe['diff'] = {k: (['present' if x not in (None, False, '', 'absent') else 'absent' for x in v]
                               if SECRET.search(k) and isinstance(v, list) else clean(v)) for k, v in row['diff'].items()}
                rows.append(safe)
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        broken += 1
    finally:
        if directory is not None:
            os.close(directory)
    return rows, broken
