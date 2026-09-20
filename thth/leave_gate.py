"""Account-bound stop authority and request leases, outside deletable state."""
import contextlib
import contextvars
import fcntl
import functools
import inspect
import os
import urllib.error
from pathlib import Path
from . import accounts, server_files

_current = contextvars.ContextVar('thth_network_account',default=None)
_held = contextvars.ContextVar('thth_account_leases',default=frozenset())
_recovering = contextvars.ContextVar('thth_leave_recovery',default=None)
_credentials = contextvars.ContextVar('thth_credential_lease',default=None)


def name_for(cfg):
    value=getattr(cfg,'_thth_account_name',None) or (cfg or {}).get('account')
    return value if accounts.name_is_safe(value) else None


def location():
    return Path(accounts.thth_root()).resolve()/'state'/'_leave'


def stopped(account):
    if not accounts.name_is_safe(account):raise accounts.AccountStopped('account_stopped')
    try:
        with server_files.directory(location(),private=True) as fd:
            try:os.stat(account+'.json',dir_fd=fd,follow_symlinks=False)
            except FileNotFoundError:return False
            return True
    except FileNotFoundError:return False
    except (OSError,ValueError):raise accounts.AccountStopped('account_stop_state_unreadable') from None


def require_active(account):
    if account and _recovering.get()!=account and stopped(account):
        raise accounts.AccountStopped('account_stopped')


def check_config(cfg):
    require_active(name_for(cfg))


@contextlib.contextmanager
def recovery(account):
    """Private leave reader/revoker only; never set from HTTP/MCP input."""
    if not accounts.name_is_safe(account):raise ValueError('invalid_account')
    reset=_recovering.set(account)
    try:yield
    finally:_recovering.reset(reset)


@contextlib.contextmanager
def scope(account):
    if isinstance(account,dict):account=name_for(account)
    require_active(account)
    reset=_current.set(account)
    try:yield
    finally:_current.reset(reset)


def scoped(function):
    """Bind account-aware command helpers without locking their input waits."""
    @functools.wraps(function)
    def call(account,*args,**kwargs):
        reset=_current.set(name_for(account) if isinstance(account,dict) else account)
        try:return function(account,*args,**kwargs)
        finally:_current.reset(reset)
    return call


def leased(function):
    """Keep an already-entered local collection batch ahead of final cleanup."""
    @functools.wraps(function)
    def call(account,*args,**kwargs):
        with scope(account),lease(account):return function(account,*args,**kwargs)
    return call


def configured(parameter):
    def decorate(function):
        signature=inspect.signature(function)
        @functools.wraps(function)
        def call(*args,**kwargs):
            cfg=signature.bind(*args,**kwargs).arguments[parameter]
            with scope(cfg):return function(*args,**kwargs)
        return call
    return decorate


@contextlib.contextmanager
def credentials(*, exclusive=False):
    """Order: account lease -> credential registry lease -> short admin flock.

    Saves take shared; a provider revoke takes exclusive through its response.
    This serializes supported mutations, not same-UID out-of-band file edits.
    """
    held=_credentials.get()
    if held is not None:
        if exclusive and held!='exclusive':raise RuntimeError('credential_lease_upgrade_forbidden')
        yield;return
    Path(accounts.thth_root()).mkdir(parents=True,exist_ok=True,mode=0o700)
    with server_files.directory(location()/'coordination',create=True,private=True) as fd:
        for attempt in range(3):
            try:
                leaf=os.open('credentials.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600,dir_fd=fd);break
            except FileNotFoundError:
                if attempt==2:raise
        try:
            server_files.regular(os.fstat(leaf),private=True)
            fcntl.flock(leaf,fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            reset=_credentials.set('exclusive' if exclusive else 'shared')
            try:yield
            finally:_credentials.reset(reset)
        finally:os.close(leaf)


def protect_migration(function):
    @functools.wraps(function)
    def call(args):
        if getattr(args,'dry_run',False):return function(args)
        with credentials():return function(args)
    return call


def protect_account_add(function):
    @functools.wraps(function)
    def call(args):
        from . import admin_log
        try:admin_log.actor(getattr(args,'by',None))
        except ValueError:return function(args)
        name=getattr(args,'name',None)
        if not accounts.name_is_safe(name):return function(args)
        try:
            with scope(name),lease(name),credentials():return function(args)
        except accounts.AccountStopped:
            import sys
            print('account_stopped: 退出した account 名は再利用できません',file=sys.stderr);return 2
    return call


def protect_admin_change(function):
    """Legacy refresh/revoke acquire the lease before their existing admin lock."""
    @functools.wraps(function)
    def call(account,*args,**kwargs):
        from . import admin_log
        if kwargs.get('check'):return function(account,*args,**kwargs)
        try:admin_log.actor('thth-refresh' if function.__name__=='run_refresh' else kwargs.get('by'))
        except ValueError:return function(account,*args,**kwargs)
        try:
            with scope(account),lease(account),credentials():return function(account,*args,**kwargs)
        except accounts.AccountStopped:
            kwargs.get('log',print)('account_stopped');return 2
    return call


@contextlib.contextmanager
def lease(account=None, *, exclusive=False):
    account=account or _current.get()
    if account is None:yield;return  # anonymous helpers have no inferred identity
    if not accounts.name_is_safe(account):raise accounts.AccountStopped('invalid_account')
    if account in _held.get():
        if exclusive:raise RuntimeError('account_lease_upgrade_forbidden')
        require_active(account);yield;return
    Path(accounts.thth_root()).mkdir(parents=True,exist_ok=True,mode=0o700)
    with server_files.directory(location(),create=True,private=True) as fd:
        for attempt in range(3):
            try:
                leaf=os.open(account+'.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600,dir_fd=fd);break
            except FileNotFoundError:
                if attempt==2:raise
        try:
            server_files.regular(os.fstat(leaf),private=True)
            fcntl.flock(leaf,fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            reset=_held.set(_held.get()|{account})
            try:
                if not exclusive:require_active(account)
                yield
            finally:_held.reset(reset)
        finally:os.close(leaf)


class LeasedResponse:
    def __init__(self,response,manager):self.response=response;self.manager=manager
    def __getattr__(self,name):return getattr(self.response,name)
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
    def close(self):
        if self.manager is None:return
        manager,self.manager=self.manager,None
        try:self.response.close()
        finally:manager.__exit__(None,None,None)


def urlopen(open_request,req,*,timeout):
    if _current.get() is None:return open_request(req,timeout=timeout)
    manager=lease();manager.__enter__()
    try:
        return LeasedResponse(open_request(req,timeout=timeout),manager)
    except urllib.error.HTTPError as exc:
        # Drain a bounded diagnostic body while the transport lease is held.
        # Callers may later read/ignore the error; neither keeps a live socket.
        import io
        try:
            raw=exc.read(65537)
            replacement=urllib.error.HTTPError(exc.url,exc.code,exc.msg,exc.headers,io.BytesIO(raw if len(raw)<=65536 else b''))
        finally:
            exc.close();manager.__exit__(None,None,None)
        raise replacement from None
    except BaseException:
        manager.__exit__(None,None,None);raise


def bind(adapter,cfg):
    """Keep the concrete adapter type; bind its methods to the loaded account."""
    account=name_for(cfg)
    if not account or getattr(adapter,'_thth_account_bound',None)==account:return adapter
    require_active(account)
    # Native helpers lease each request (Threads waits stay outside the lease).
    # Unknown injected adapters have no common transport, so their call is leased.
    from .adapters import ThreadsAdapter, BlueskyAdapter, MastodonAdapter
    native=type(adapter) in (ThreadsAdapter,BlueskyAdapter,MastodonAdapter)
    methods={'publish','conversation','fetch_post','inbox','recent_posts','insights','whoami','probe',
             'quota','refresh_token','location_search','delete_post','session','keyword_search','mentions',
             'tag_search','tag_observation','observed_tags','profile_lookup','account_insights','granted_scopes',
             'char_limit','_post','_get','_request'}
    for name in methods:
        original=getattr(adapter,name,None)
        if not callable(original):continue
        network_helper=name in ('_post','_get','_request','delete_post','probe','char_limit')
        def wrapped(*args,_call=original,_lease=not native or network_helper,_publish=name=='publish',**kwargs):
            try:
                with scope(account):
                    with lease(account) if _lease else contextlib.nullcontext():
                        return _call(*args,**kwargs)
            except accounts.AccountStopped:
                if not _publish:raise
                from .adapters.base import PublishResult
                from . import jst
                return PublishResult(None,None,jst.iso(),error='account_stopped',failure='publish_vetoed')
        setattr(adapter,name,functools.wraps(original)(wrapped))
    adapter._thth_account_bound=account
    return adapter
