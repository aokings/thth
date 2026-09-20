"""Private per-provider clients. No environment expansion or project-local files."""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid
from . import accounts, redact
from .authflow import FlowError


def path_for(media, origin, cfg):
    directory = Path(os.environ.get('THTH_APPS_DIR') or Path.home()/'.config/thth/apps').absolute()
    forbidden = [Path(accounts.thth_root()).resolve(), Path(__file__).resolve().parents[1]]
    if cfg.get('repo_dir'):
        forbidden.append(Path(cfg['repo_dir']).resolve())
    resolved = directory.resolve()
    if any(resolved == parent or parent in resolved.parents for parent in forbidden):
        raise FlowError('auth_client_store_must_be_outside_project')
    name = media + ('.' + hashlib.sha256(origin.encode()).hexdigest() if origin else '') + '.env'
    return directory/name


def _directory(path, create=False):
    # Pin every component; neither reads nor writes follow untrusted symlinks.
    directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parent.parts[1:]:
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            except FileNotFoundError:
                if not create:
                    raise
                try: os.mkdir(part, 0o700, dir_fd=directory)
                except FileExistsError: pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory);directory=child
        return directory
    except BaseException:
        os.close(directory);raise


def read(path):
    directory=None
    try:
        directory=_directory(path)
        fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
        with os.fdopen(fd,'rb') as f:
            info=os.fstat(f.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o600:
                raise FlowError('auth_client_store_unreadable')
            raw=f.read(262145)
        if len(raw)>262144:raise FlowError('auth_client_store_unreadable')
        data={}
        for line in raw.decode().splitlines():
            key,separator,value=line.partition('=')
            if not separator or key in data:raise FlowError('auth_client_store_unreadable')
            data[key]=json.loads(value)
        for key in ('client_id','client_secret'):
            redact.register_secret(data.get(key))
        return data
    except FileNotFoundError:
        return None
    finally:
        if directory is not None:os.close(directory)


def write(path, data):
    directory=_directory(path,create=True);temporary='.client-'+uuid.uuid4().hex
    try:
        try:
            info=os.stat(path.name,dir_fd=directory,follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise FlowError('auth_client_store_unreadable')
        except FileNotFoundError:pass
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
        with os.fdopen(fd,'w') as f:
            for key,value in data.items():f.write(key+'='+json.dumps(value,ensure_ascii=False,allow_nan=False)+'\n')
            f.flush();os.fsync(f.fileno())
        os.replace(temporary,path.name,src_dir_fd=directory,dst_dir_fd=directory)
    finally:
        try:os.unlink(temporary,dir_fd=directory)
        except FileNotFoundError:pass
        os.close(directory)


@contextlib.contextmanager
def registration_lock(path):
    directory=_directory(path,create=True);fd=None
    try:
        fd=os.open(path.name+'.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600,dir_fd=directory)
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o600:
            raise FlowError('auth_client_lock_unreadable')
        fcntl.flock(fd,fcntl.LOCK_EX)
        yield
    finally:
        if fd is not None:os.close(fd)
        os.close(directory)
