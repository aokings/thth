"""One explicit local read receipt; report readers never call write()."""
import json
import errno
import os
from pathlib import Path
import stat
import uuid
from . import accounts, jst

KEYS = {'queue_counts','inflight','notification_last_event_id','notification_recorded_state',
        'run_last_attempt_at','run_recorded_state','last_post_observed_at','sent_count'}
QUEUE_KEYS = {'draft','approved_waiting','overdue','malformed','unattributed_malformed'}

class CursorDirectoryUnavailable(OSError, ValueError):
    """The configured state directory cannot be opened safely."""


def _directory(name, create=False):
    # Resolve only the trusted configured root; components below it stay pinned.
    configured_root = Path(accounts.thth_root()).absolute()
    configured_path = Path(accounts.state_dir_for(name)).absolute()
    expected = configured_root / "state" / name
    # Keep compatibility with callers/tests supplying an explicit state path;
    # production accounts.state_dir_for follows the configured root above.
    if configured_path == expected:
        root = Path(os.path.realpath(configured_root))
        path = root / "state" / name
    else:
        root = Path(os.path.realpath(configured_path.parents[1]))
        path = root / configured_path.relative_to(configured_path.parents[1])
    # Keep each opened ancestor pinned. Checking a path and then reopening the
    # absolute name would permit a symlink swap between those two operations.
    if ".." in path.parts or not path.is_absolute():
        raise CursorDirectoryUnavailable('cursor_directory_unavailable')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        directory = os.open(str(root), flags)
    except OSError as exc:
        raise CursorDirectoryUnavailable('cursor_directory_unavailable') from exc
    try:
        for component in path.relative_to(root).parts:
            try:
                child = os.open(component, flags, dir_fd=directory)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(component, mode=0o700, dir_fd=directory)
                except FileExistsError:
                    # Another creator may have won; never follow what it made.
                    pass
                child = os.open(component, flags, dir_fd=directory)
            os.close(directory)
            directory = child
        return directory
    except BaseException as exc:
        os.close(directory)
        if isinstance(exc, CursorDirectoryUnavailable):
            raise
        if isinstance(exc, OSError) and exc.errno == errno.ELOOP:
            raise ValueError('cursor_unreadable') from exc
        if isinstance(exc, OSError):
            raise CursorDirectoryUnavailable('cursor_directory_unavailable') from exc
        raise


def snapshot(node):
    counts = node['queue']['counts']
    present = node['evidence']['inflight']['availability']
    return {'queue_counts': {k:counts[k] if counts is not None else None for k in sorted(QUEUE_KEYS)},
        'inflight': {'present': True if present=='available' else False if present=='missing' else None,
                     'since': (node['inflight'] or {}).get('since')},
        'notification_last_event_id':node['notifications'].get('last_event_id'),
        'notification_recorded_state':node['notifications']['recorded_state'],
        'run_last_attempt_at':node['last_run_notification']['recorded_at'],
        'run_recorded_state':node['last_run_notification']['recorded_state'],
        'last_post_observed_at':node['last_post']['observed_at'], 'sent_count':node['sent_count']}


def _validate(value, now):
    if not isinstance(value, dict) or set(value)!={'schema_version','read_at','by','snapshot'}:
        raise ValueError('cursor_unreadable')
    at=jst.parse(value['read_at'])
    if type(value['schema_version']) is not int or value['schema_version']!=1 or at is None or at>now:
        raise ValueError('cursor_unreadable')
    if not isinstance(value['by'],str) or not value['by'].strip() or len(value['by'])>256:
        raise ValueError('cursor_unreadable')
    s=value['snapshot']
    if not isinstance(s,dict) or set(s)!=KEYS or not isinstance(s['queue_counts'],dict) or set(s['queue_counts'])!=QUEUE_KEYS:
        raise ValueError('cursor_unreadable')
    for count in [*s['queue_counts'].values(),s['sent_count']]:
        if count is not None and (type(count) is not int or count<0):raise ValueError('cursor_unreadable')
    i=s['inflight']
    if not isinstance(i,dict) or set(i)!={'present','since'} or (i['present'] is not None and type(i['present']) is not bool):
        raise ValueError('cursor_unreadable')
    for key in KEYS-{'queue_counts','inflight','sent_count'}:
        if s[key] is not None and (not isinstance(s[key],str) or len(s[key])>2048):raise ValueError('cursor_unreadable')
    if i['since'] is not None and not isinstance(i['since'],str):raise ValueError('cursor_unreadable')
    return value


def _pairs(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('cursor_unreadable')
        result[key]=value
    return result


def read(name, now):
    directory=None
    try:
        directory=_directory(name)
        fd=os.open('handoff_cursor.json',os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW,dir_fd=directory)
        with os.fdopen(fd,'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise ValueError('cursor_unreadable')
            data=stream.read(65537)
        if len(data)>65536:raise ValueError('cursor_unreadable')
        return _validate(json.loads(data,object_pairs_hook=_pairs),now),None
    except CursorDirectoryUnavailable:
        # A symlink below the trusted root is an unreadable cursor location;
        # an absent/unopenable root is a directory availability failure.
        root = Path(os.path.realpath(accounts.thth_root()))
        if (root / 'state').is_symlink() or (root / 'state' / name).is_symlink():
            return None,'cursor_unreadable'
        return None,'cursor_directory_unavailable'
    except FileNotFoundError:
        return None,'no_previous_session_cursor'
    except (OSError,ValueError,TypeError,OverflowError,RecursionError):
        return None,'cursor_unreadable'
    finally:
        if directory is not None:os.close(directory)


def changes(previous, current):
    result=[]
    for key in sorted(KEYS):
        before,after=previous[key],current[key]
        if key in ('queue_counts','inflight'):
            pairs=[(key+'.'+k,before[k],after[k]) for k in sorted(before)]
        else:pairs=[(key,before,after)]
        for field,old,new in pairs:
            if old!=new:
                result.append({'field':field,'previous':old,'current':new,
                    'delta':new-old if type(old) is int and type(new) is int else None})
    return result


def write(name, node, by, now):
    if not isinstance(by,str) or not by.strip() or len(by)>256:raise ValueError('cursor_by_required')
    value={'schema_version':1,'read_at':jst.iso(now),'by':by,'snapshot':snapshot(node)}
    _validate(value,now)
    write_snapshot(name, 'handoff_cursor.json', value)


def write_snapshot(name, filename, value):
    if filename not in ('handoff_cursor.json', 'admin_cursor.json', 'admin_notifications.json', 'timers.json'):
        raise ValueError('invalid_cursor_filename')
    directory=_directory(name,create=True)
    temporary='.handoff-cursor-'+uuid.uuid4().hex
    try:
        try:
            info=os.stat(filename,dir_fd=directory,follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):raise ValueError('cursor_unreadable')
        except FileNotFoundError:pass
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
        with os.fdopen(fd,'w') as stream:
            json.dump(value,stream,ensure_ascii=False,allow_nan=False)
            stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,filename,src_dir_fd=directory,dst_dir_fd=directory)
    finally:
        try:os.unlink(temporary,dir_fd=directory)
        except FileNotFoundError:pass
        os.close(directory)
