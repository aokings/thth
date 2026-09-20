"""Account-owned local Git clones. Never migrate an existing project implicitly."""
import configparser
import os
from pathlib import Path
import subprocess
import stat
from . import accounts, server_files


def locations(account):
    if not accounts.name_is_safe(account): raise ValueError('invalid_account')
    root = Path(accounts.thth_root()).resolve()
    return root/'repos'/'_server'/account, root/'state'/account/'git-origin.git'


def account_for(repo):
    if not isinstance(repo, (str, os.PathLike)): return None
    root = Path(accounts.thth_root()).resolve()/'repos'/'_server'
    path = Path(repo).absolute()
    return path.name if path.parent == root and accounts.name_is_safe(path.name) else None


def _config(path, *, bare, origin):
    with server_files.directory(path.parent) as fd:
        raw = server_files.read_at(fd, path.name).decode()
    parser = configparser.RawConfigParser(strict=True)
    parser.read_string(raw)
    expected = {'core': {'repositoryformatversion','filemode','bare','logallrefupdates','ignorecase','precomposeunicode'}}
    if not bare:
        expected.update({'remote "origin"': {'url','fetch'}, 'branch "main"': {'remote','merge'}})
    for section in parser.sections():
        if section not in expected or set(parser[section])-expected[section]: raise ValueError('managed_git_config_unsafe')
    if parser.get('core','bare',fallback='') != str(bare).lower(): raise ValueError('managed_git_config_unsafe')
    if not bare and (parser.get('remote "origin"','url',fallback='') != str(origin)
            or parser.get('remote "origin"','fetch',fallback='') != '+refs/heads/*:refs/remotes/origin/*'
            or parser.get('branch "main"','remote',fallback='') != 'origin'
            or parser.get('branch "main"','merge',fallback='') != 'refs/heads/main'):
        raise ValueError('managed_git_origin_changed')


def _storage_tree(fd, *, depth=0, remaining=None):
    """Check every Git administrative leaf without following filesystem links.

    Git itself opens these paths after this check; this rejects pre-existing
    redirects, not arbitrary concurrent replacement by the same OS user.
    """
    if remaining is None: remaining = [100000]
    if depth > 64: raise ValueError('managed_git_store_too_large')
    for name in os.listdir(fd):
        remaining[0] -= 1
        if remaining[0] < 0: raise ValueError('managed_git_store_too_large')
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                opened = os.fstat(child)
                if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino) or opened.st_uid != os.getuid() or opened.st_mode & 0o022:
                    raise ValueError('managed_git_store_unsafe')
                _storage_tree(child, depth=depth+1, remaining=remaining)
            finally: os.close(child)
        else:
            server_files.regular(info)


def validate(repo):
    account = account_for(repo)
    if account is None: raise ValueError('managed_repo_required')
    clone, origin = locations(account)
    with server_files.directory(clone/'.git'): pass
    with server_files.directory(origin): pass
    for gitdir in (clone/'.git', origin):
        with server_files.directory(gitdir) as fd: _storage_tree(fd)
        for special in ('commondir','objects/info/alternates','objects/info/http-alternates'):
            if (gitdir/special).exists() or (gitdir/special).is_symlink():
                raise ValueError('managed_git_external_store')
    _config(clone/'.git'/'config', bare=False, origin=origin)
    _config(origin/'config', bare=True, origin=origin)
    return clone, origin


def environment():
    # No inherited git config, template, credential helper, hooks, filters or signing.
    env = {key:value for key,value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null', GIT_ALLOW_PROTOCOL='file',
               GIT_TERMINAL_PROMPT='0', GIT_PAGER='cat', GIT_AUTHOR_NAME='THTH server',
               GIT_AUTHOR_EMAIL='server@thth.invalid', GIT_COMMITTER_NAME='THTH server',
               GIT_COMMITTER_EMAIL='server@thth.invalid')
    return env


CONFIG = ['-c','core.hooksPath=/dev/null','-c','core.fsmonitor=false','-c','commit.gpgsign=false',
          '-c','tag.gpgsign=false','-c','credential.helper=','-c','init.templateDir=',
          '-c','protocol.allow=never','-c','protocol.file.allow=always',
          '-c','remote.origin.uploadpack=git-upload-pack','-c','remote.origin.receivepack=git-receive-pack']


def run(repo, arguments, *, text=True, check=True):
    if check: validate(repo)
    return subprocess.run(['/usr/bin/git',*CONFIG,'-C',str(repo),*arguments],capture_output=True,text=text,env=environment())


def initialize(account, cfg):
    clone, origin = locations(account)
    if cfg.get('repo_dir') != str(clone): raise ValueError('managed_repo_required')
    if clone.exists() or clone.is_symlink():
        validate(clone); return str(clone)
    if origin.exists() or origin.is_symlink(): raise ValueError('managed_initialization_incomplete')
    # Parent components are pinned/NOFOLLOW before Git can run. A partial init is
    # explicit and never repoints or overwrites a repository on automatic retry.
    with server_files.directory(origin, create=True): pass
    with server_files.directory(clone, create=True): pass
    commands = [(origin,['init','--bare','--initial-branch=main','--template=']),
                (clone,['init','--initial-branch=main','--template=']),
                (clone,['remote','add','origin',str(origin)]),
                (clone,['commit','--allow-empty','-m','Initialize account-owned server drafts']),
                (clone,['push','--set-upstream','origin','main'])]
    for path, command in commands:
        if run(path,command,check=False).returncode: raise ValueError('managed_initialization_failed')
    queue=Path(cfg['queue_dir'])
    if queue.is_absolute() or not queue.parts or any(p.startswith('.') for p in queue.parts):
        raise ValueError('unsafe_queue_directory')
    with server_files.directory(clone/queue, create=True): pass
    validate(clone)
    return str(clone)
