"""Pinned, owner-private storage for server jobs and managed drafts."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import secrets
import stat
from . import accounts


class UnsafeFile(ValueError):
    pass


@contextlib.contextmanager
def directory(path, *, create=False, private=False):
    root = Path(accounts.thth_root()).resolve()
    target = Path(path).absolute()
    try: parts = target.relative_to(root).parts
    except ValueError: raise UnsafeFile('outside_server_root') from None
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts:
            if part in ('', '.', '..'): raise UnsafeFile('unsafe_server_path')
            if create:
                try: os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError: pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = child
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or info.st_mode & 0o022:
                raise UnsafeFile('unsafe_server_directory')
        if private and os.fstat(fd).st_mode & 0o077:
            raise UnsafeFile('private_server_directory_required')
        yield fd
    finally: os.close(fd)


def regular(info, *, private=False):
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
            or info.st_mode & (0o077 if private else 0o022)):
        raise UnsafeFile('unsafe_server_file')


def read_at(fd, name, *, private=False, maximum=262144):
    leaf = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(leaf, 'rb') as stream:
        info = os.fstat(stream.fileno()); regular(info, private=private)
        if info.st_size > maximum: raise UnsafeFile('server_file_too_large')
        raw = stream.read(maximum+1)
        if len(raw) > maximum: raise UnsafeFile('server_file_too_large')
        return raw


def replace_at(fd, name, raw, *, expected=None, new=False, private=False):
    """Caller holds its resource lock. Check old leaf before atomic replacement."""
    try:
        old = read_at(fd, name, private=private)
        if new or expected is not None and old != expected: raise UnsafeFile('server_file_changed')
    except FileNotFoundError:
        if expected is not None: raise UnsafeFile('server_file_changed') from None
    temp = '.write-'+secrets.token_hex(16)
    leaf = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        with os.fdopen(leaf,'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.rename(temp, name, src_dir_fd=fd, dst_dir_fd=fd); os.fsync(fd)
    finally:
        try: os.unlink(temp, dir_fd=fd)
        except FileNotFoundError: pass


@contextlib.contextmanager
def lock_at(fd, name, *, private=True):
    # Same pinned create/open race handling as authclients; never follow links.
    leaf = None
    for attempt in range(3):
        try:
            leaf = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=fd); break
        except FileNotFoundError:
            if attempt == 2: raise
    try:
        regular(os.fstat(leaf), private=private)
        fcntl.flock(leaf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        if leaf is not None: os.close(leaf)


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n').encode()


@contextlib.contextmanager
def account_locks(account, cfg):
    from .lock import LockBusy
    repo=cfg.get('repo_dir')
    if repo is not None and not isinstance(repo,str):raise ValueError('invalid_repo')
    paths = ([Path(accounts.repo_lock_path_for(repo))] if repo else []) + [Path(accounts.account_lock_path_for(account))]
    try:
        with contextlib.ExitStack() as stack:
            for path in paths:
                fd = stack.enter_context(directory(path.parent, create=True))
                stack.enter_context(lock_at(fd, path.name, private=False))
            yield
    except BlockingIOError:
        raise LockBusy('server_resource_busy') from None
