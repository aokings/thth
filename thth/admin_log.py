"""Append-only, bounded administration events. Never store credentials."""
import json
import os
import re
import socket
import stat
from . import accounts, handoff_cursor, jst, redact

class AdminLogError(Exception):
    def __init__(self, message, *, appended=False, complete=False):
        super().__init__(message)
        self.appended = appended
        self.complete = complete


def _emit(fd, data):
    written = 0
    try:
        written = os.write(fd, data)
        if written != len(data):
            raise OSError('admin_log_short_write')
        os.fsync(fd)
    except OSError as exc:
        raise AdminLogError('admin_log_write_failed', appended=written > 0, complete=written == len(data)) from exc


EVENTS = frozenset(('account_added', 'account_updated', 'account_removed', 'token_set',
                   'token_refreshed', 'token_revoked', 'production_enabled', 'production_disabled'))
SECRET = re.compile(r'token|secret|password|jwt|env|email|notification|smtp|ping', re.I)
MAIL = re.compile(r'[^\s<>"@]+@[^\s<>"@]+\.[^\s<>"@]+')


def register_account_secrets(cfg, *, via="cli"):
    """Register only configured secret files; never enumerate the secret directory."""
    from . import appenv, incident
    try:
        token = accounts.load_token(cfg)
        if isinstance(token, dict):
            for key, value in token.items():
                if SECRET.search(key) or key.lower().endswith('jwt'):
                    redact.register_secret(value)
    except (OSError, ValueError, TypeError):
        pass
    for path in (cfg.get('env'), appenv.default_path() if via == 'cli' else None):
        if path:
            try:
                for value in appenv._parse_env_file(path).values():
                    redact.register_secret(value)
            except (OSError, ValueError):
                pass
    try:
        incident.settings(cfg)  # registers configured recipients and SMTP secrets
    except (OSError, ValueError, TypeError):
        pass


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
    if _active_fd.get() is not None:
        data = (json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n').encode()
        _active_events.get().append(data)
        return row
    directory = handoff_cursor._directory('_admin', create=True)
    try:
        fd = os.open('accounts.ndjson', os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=directory)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
                raise ValueError('admin_log_unreadable')
            data = (json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n').encode()
            _emit(fd, data)
        finally:
            os.close(fd)
    finally:
        os.close(directory)
    return row


def read(*, since=None, account=None, event=None):
    if event is not None and (not isinstance(event, str) or event not in EVENTS):
        raise ValueError('invalid_options')
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
                    if any(not isinstance(k, str) or not isinstance(v, list) or len(v) != 2 for k, v in row['diff'].items()):
                        raise ValueError('broken_diff')
                except (ValueError, TypeError):
                    broken += 1
                    continue
                if cutoff and jst.parse(row['at']) < cutoff or account and row['account'] != account or event and row['event'] != event:
                    continue
                safe = {k: row.get(k) for k in ('at', 'by', 'via', 'host', 'event', 'account', 'medium', 'diff', 'run_id')}
                safe['diff'] = {k: (['present' if x not in (None, False, '', 'absent') else 'absent' for x in v]
                               if SECRET.search(k) and isinstance(v, list) else clean(v)) for k, v in row['diff'].items()}
                for key in ('by', 'host', 'medium', 'run_id'):
                    safe[key] = clean(safe[key])
                rows.append(safe)
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        broken += 1
    finally:
        if directory is not None:
            os.close(directory)
    return rows, broken


# Pin and lock the validated append destination before any account/token mutation.
# This prevents a bad log path from allowing an unrecorded credential change.
import contextlib
import contextvars
import functools
import fcntl
_active_fd = contextvars.ContextVar('admin_event_fd', default=None)
_active_events = contextvars.ContextVar('admin_event_buffer', default=None)


@contextlib.contextmanager
def transaction():
    if _active_fd.get() is not None:
        yield
        return
    directory = handoff_cursor._directory('_admin', create=True)
    fd = None
    try:
        fd = os.open('accounts.ndjson', os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=directory)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
            raise ValueError('admin_log_unreadable')
        fcntl.flock(fd, fcntl.LOCK_EX)
        reset = _active_fd.set(fd)
        events = []
        reset_events = _active_events.set(events)
        try:
            yield
            if events:
                _emit(fd, b''.join(events))
                from . import admin_notifications
                for data in events:
                    event = json.loads(data)
                    try:
                        cfg = accounts.load_account(event['account'])
                        admin_notifications.notify(event, cfg)
                    except (OSError, ValueError, TypeError, accounts.AccountError):
                        # The committed audit event remains the durable retry evidence.
                        import sys
                        print('admin_notification_pending', file=sys.stderr)
        finally:
            _active_events.reset(reset_events)
            _active_fd.reset(reset)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(directory)


def guarded(function):
    @functools.wraps(function)
    def call(*args, **kwargs):
        if kwargs.get('check'):
            return function(*args, **kwargs)
        by = kwargs.get('by') if function.__name__ != 'cmd_add' else getattr(args[0], 'by', None)
        if function.__name__ == 'run_refresh':
            by = 'thth-refresh'
        snapshot = None
        path = None
        mutated = False
        try:
            actor(by)
            from pathlib import Path
            if function.__name__ == 'cmd_add':
                from . import account_cli
                if accounts.name_is_safe(args[0].name):
                    path = Path(account_cli.target_accounts_dir()) / (args[0].name + '.json')
            else:
                cfg = accounts.load_account(args[0])
                path = Path(cfg['token']) if cfg.get('token') else None
            if path is not None:
                if path.is_symlink():
                    raise ValueError('unsafe_mutation_path')
                if path.exists():
                    snapshot = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
            Path(accounts.thth_root()).mkdir(parents=True, exist_ok=True)
            with transaction():
                mutated = True
                return function(*args, **kwargs)
        except (OSError, ValueError, AdminLogError) as exc:
            if isinstance(exc, AdminLogError) and exc.appended:
                import sys
                print(("admin_change_recorded_durability_unconfirmed" if exc.complete else "admin_change_partially_recorded_outcome_uncertain") + ": change retained; inspect account and log before retry", file=sys.stderr)
                return 2
            # A late append/fsync failure must not leave an unlogged mutation.
            # Restoration uses a private atomic replacement and retains exact bytes/mode.
            if mutated and path is not None:
                if snapshot is None:
                    path.unlink(missing_ok=True)
                else:
                    from . import secrets_fs
                    secrets_fs.atomic_write_text(str(path), snapshot[0].decode('utf-8'), mode=snapshot[1])
            import sys
            print('admin_change_refused: --by and a private writable event log are required', file=sys.stderr)
            return 2
        except accounts.AccountError:
            # Preserve the original bounded account diagnostic from the operation.
            return function(*args, **kwargs)
    return call
