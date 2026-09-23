"""Read-only administrator reports assembled from the existing report sources."""
from __future__ import annotations
import collections
import datetime
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
from . import (accounts, account_report, admin_log, analytics_report, appenv, collection_status, doctor,
               incident, jst, oauth, operations_handoff, redact, report_details, runs, timer_cleanup)

FIELDS = ('production', 'hashtags', 'max_hashtags', 'min_interval_hours', 'quiet_hours',
          'collect_days', 'stale_days', 'scheduled')
TOKEN_FIELDS = ('mode_ok', 'obtained_at', 'expires_at', 'remaining_days', 'no_expiry',
                'user_id', 'username', 'scopes', 'scopes_source', 'handle_matches', 'auth_via')
PROVENANCE_FIELDS = ('created_at', 'created_by', 'created_via', 'created_host')
OPERATIONS = ('inventory', 'account', 'log', 'tokens', 'timers', 'release', 'diff')


def _json(path):
    try:
        path = Path(path)
        if path.is_symlink():
            return None
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, UnicodeError):
        return None



def _admin_json(filename):
    from . import handoff_cursor
    return handoff_cursor.read_snapshot('_admin', filename)


def _scrub(value):
    # Preserve report schema; only values (and untrusted object keys) are scrubbed.
    if isinstance(value, str):
        value = redact.redact(value)
        stem = value.removesuffix('.timer')
        prefix, separator, name = stem.partition('@')
        if separator and accounts.name_is_safe(name) and prefix in ('thth', 'thth-collect') and value.endswith('.timer'):
            return value  # A generated systemd instance name is not an email address.
        if timer_cleanup.instance_of(value):
            return value  # thth-collect@<name>.service（3.5.1 件 1）も同じ。
        command = value.removeprefix(timer_cleanup.stop_command(()))
        if command != value and command and all(timer_cleanup.instance_of(u) for u in command.split(' ')):
            return value  # orphan_unit の止める命令（unit 名だけで組んだ 1 行）。
        return admin_log.MAIL.sub('[redacted-email]', value)
    if isinstance(value, dict):
        return {_scrub(k): ('[redacted]' if any(word in k.lower() for word in ('password', 'secret', 'access_token', 'refresh_token', 'accessjwt', 'refreshjwt', 'authorization', 'email')) else _scrub(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    return value


def _register(cfg, *, via="cli"):
    admin_log.register_account_secrets(cfg, via=via)


def _file_meta(path):
    result = {'path': path, 'present': False, 'mode_ok': None}
    if path:
        try:
            info = os.stat(path)
            result.update(present=stat.S_ISREG(info.st_mode), mode_ok=stat.S_IMODE(info.st_mode) == 0o600)
        except OSError:
            pass
    return result


def _token(cfg, now):
    result = dict.fromkeys(TOKEN_FIELDS)
    result['present'] = accounts.token_exists(cfg)
    if not result['present']:
        return result
    result['mode_ok'] = _file_meta(cfg.get('token'))['mode_ok']
    try:
        token = accounts.load_token(cfg)
        if not isinstance(token, dict):
            raise ValueError('token_unreadable')
        for key in ('obtained_at', 'no_expiry', 'user_id', 'username'):
            result[key] = token.get(key)
        result['auth_via'] = token.get('auth_via') if token.get('auth_via') in ('relay','paste','token_set') else None
        scopes = doctor.recorded_scopes(token)
        result.update(scopes=scopes['scopes'], scopes_source=scopes['source'])
        try:
            if cfg.get('media') == 'x':
                from .adapters.auth_x import remaining as x_remaining
                seconds = x_remaining(token, now)
                remaining = seconds / 86400 if seconds is not None else None
            else:
                _, remaining = oauth.token_age_and_remaining(token, now)
            result['remaining_days'] = remaining
            result['expires_at'] = jst.iso(now + datetime.timedelta(days=remaining)) if remaining is not None else None
        except (ValueError, TypeError, OverflowError, oauth.OAuthError):
            result['reason'] = 'token_expiry_unavailable'
        handle = cfg.get('handle')
        username = token.get('username') or token.get('handle')
        result['handle_matches'] = oauth.handle_matches(handle, username, media=cfg.get('media'))
    except (OSError, ValueError, TypeError):
        result['reason'] = 'token_unreadable'
    return result


def _repo(cfg):
    state = accounts.repo_state(cfg)
    result = dict(state=state, dir=accounts.resolved_repo_dir(cfg), origin=None, ahead=None, behind=None, dirty=None)
    if state != accounts.REPO_OK:
        result['repo_reason'] = 'repo_' + state
        return result
    def git(*args):
        proc = subprocess.run(['git', '-C', result['dir'], *args], capture_output=True, text=True, timeout=10)
        if proc.returncode:
            raise ValueError('git_unavailable')
        return proc.stdout.strip()
    try:
        result['origin'] = bool(git('remote', 'get-url', 'origin'))
    except (OSError, ValueError, subprocess.SubprocessError):
        result['repo_reason'] = 'repo_origin_unavailable'
    try:
        result['dirty'] = bool(git('status', '--porcelain', '--', 'data/sns'))
        behind, ahead = git('rev-list', '--left-right', '--count', '@{u}...HEAD').split()
        result.update(ahead=int(ahead), behind=int(behind))
    except (OSError, ValueError, subprocess.SubprocessError):
        result['repo_reason'] = 'repo_git_unavailable'
    return result


def _permissions(name, probe):
    if probe:
        cfg = accounts.load_account(name)
        if cfg.get('media') == 'x':
            return dict(probed_at=None, source=None, result=doctor.diagnose(name), reason='auth_only_probe_unsupported')
        try: generation = doctor._credential_generation(cfg)
        except (OSError, ValueError): generation = None
        observed = doctor.diagnose(name)
        at = jst.iso()
        result = dict(probed_at=at, source='live_readonly_probe', result=observed)
        try:
            doctor.record_observation(name, observed, probed_at=at, credential_generation=generation)
        except (OSError, ValueError, TypeError):
            result['reason'] = 'permissions_recording_unavailable'
        return result
    cached = doctor.read_observation(name)
    if cached:
        return dict(probed_at=cached['probed_at'], source='recorded',
                    result={key: cached[key] for key in ('probes', 'error')},
                    **{key:cached.get(key) for key in ('auth_via','auth_observed_at','auth_current_credentials','probe_current_credentials')})
    return dict(probed_at=None, source=None, result=None, reason='permissions_not_recorded')


def _defaults(cfg):
    from . import account_cli
    try:
        value = _json(account_cli.example_path(cfg.get('media')))
        return {key: (value or {}).get(key) for key in FIELDS}
    except (OSError, ValueError, TypeError):
        return dict.fromkeys(FIELDS)


def _admin_names():
    from . import leave
    return sorted(set(accounts.list_account_names())|set(leave.names()))


def _stopped_row(name):
    from . import leave,leave_gate
    if not leave_gate.stopped(name):return None
    try:state=leave.public(leave.read(name))
    except (OSError,ValueError,TypeError,KeyError):state={'phase':'unreadable'}
    log,broken=admin_log.read(account=name)
    return dict(account=name,ledger='removed' if state.get('phase')=='completed' else 'stopped',
                leave=state,last_change=log[-1] if log else None,change_log=log,
                cannot_say=['account_stopped']+(['admin_log_incomplete'] if broken else []))


def _account(name, now, probe=False, via='cli', detail=False, limit=20):
    stopped=_stopped_row(name)
    if stopped is not None:return stopped
    from .report_isolation import read_registry_ledger
    cfg = None
    try:
        raw = read_registry_ledger(Path(accounts.accounts_dir()) / (name + '.json'))
    except (OSError, ValueError):
        raw = None
    if raw is None or raw.get('project') is not None and (not isinstance(raw['project'], str) or not raw['project'].strip()):
        return dict(account=name, ledger='unreadable', cannot_say=['ledger_unreadable'])
    try:
        cfg = accounts.load_account(name)
    except (accounts.AccountError, OSError, ValueError, TypeError, KeyError):
        pass
    if raw is None or raw.get('project') is not None and (not isinstance(raw['project'], str) or not raw['project'].strip()):
        return dict(account=name, ledger='unreadable', cannot_say=['ledger_unreadable'])
    missing_fields = cfg is None
    if cfg is None:
        cfg = dict(raw)
        for key in ('repo_dir','env','token'):
            cfg[key] = accounts._expand(raw.get(key))
    if not isinstance(raw.get('media'), str) or any(cfg.get(k) is not None and not isinstance(cfg.get(k),str) for k in ('token','env','repo_dir','queue_dir','replies_dir')):
        return dict(account=name, ledger='unreadable', cannot_say=['ledger_unreadable'])
    _register(cfg, via=via)
    row = {key: raw.get(key) for key in ('account', 'project', 'handle', 'instance')}
    row.update(account=name, medium=raw.get('media'), ledger='available', **{key: raw.get(key) for key in FIELDS})
    row['defaults'] = _defaults(cfg)
    row['repo'] = _repo(cfg)
    row['provenance'] = {key: (raw.get('provenance') if isinstance(raw.get('provenance'), dict) else {}).get(key) for key in PROVENANCE_FIELDS}
    row['provenance_reason'] = None if isinstance(raw.get('provenance'), dict) else 'provenance_unreadable' if raw.get('provenance') is not None else 'recorded_before_2.9.0'
    row['token'] = _token(cfg, now)
    try:
        row['permissions'] = _permissions(name, probe)
    except (OSError, ValueError, TypeError, accounts.AccountError):
        row['permissions'] = dict(probed_at=None, result=None, reason='permissions_unavailable')
    try:
        handoff = operations_handoff._account(name, cfg, now)
    except (OSError, ValueError, TypeError, KeyError):
        handoff = dict(queue=None, inflight=None, last_post=None, sent_count=None, notifications={}, last_run_notification=None)
        missing_fields = True
    row.update({key: handoff[key] for key in ('queue', 'inflight', 'last_post', 'sent_count')})
    if accounts.repo_state(cfg) == accounts.REPO_NONE:
        row.update(queue=None, inflight=None, last_post=None, sent_count=None)
    collection, reasons = collection_status.summarize(name, now)
    row['collection'] = collection
    row['last_reached_mark'] = None
    try:
        from . import measured
        observations = [obs for post in measured.load(name, observation_metadata=True)['posts']
                        for obs in post.get('rows', []) if isinstance(obs, dict)]
        eligible = [(jst.parse(obs.get('collected_at')), obs.get('marks')) for obs in observations
                    if jst.parse(obs.get('collected_at')) and jst.parse(obs.get('collected_at')) <= now and obs.get('marks')]
        row['last_reached_mark'] = max(eligible, key=lambda item: item[0])[1] if eligible else None
    except (OSError, ValueError, TypeError, KeyError, accounts.AccountError):
        pass
    row['collection_reason'] = reasons[0] if reasons else None
    row['notifications'] = dict(handoff['notifications'])
    row['notifications']['run'] = handoff['last_run_notification']
    try:
        ready = incident.readiness(cfg)
    except (OSError, ValueError, TypeError):
        ready = {}
    row['notifications']['recipient_present'] = {key: ready.get(key) for key in ('user_configured', 'admin_configured')}
    row['timer'] = timer(name, via=via)
    log, broken = admin_log.read(account=name)
    row['last_change'] = log[-1] if log else None
    row['cannot_say'] = reasons + [r for r in (row['provenance_reason'], row['timer'].get('timer_reason'),
                                             row['repo'].get('repo_reason')) if r]
    if missing_fields:
        row['cannot_say'].append('ledger_fields_unavailable')
    if broken:
        row['cannot_say'].append('admin_log_incomplete')
    if detail:
        row['ledger_fields'] = {key: _file_meta(cfg.get(key)) if key in ('token', 'env') else
                                {'present': bool(value)} if admin_log.SECRET.search(key) else admin_log.clean(value)
                                for key, value in raw.items()}
        try:
            history = runs.read_runs(accounts.state_dir_for(name))
            row['runs'] = [{key: run.get(key) for key in ('run_id', 'action', 'status', 'collected', 'error', 'refreshed')}
                           for run in history if isinstance(run, dict) and run.get('account') == name][-limit:]
        except (OSError, ValueError, TypeError):
            row['runs'] = None
            row['cannot_say'].append('runs_unreadable')
        outbox = _json(Path(accounts.state_dir_for(name)) / incident.STATE_FILE)
        row['notification_events'] = None
        try:
            incident._validate(outbox)
            row['notification_events'] = [{key: event.get(key) for key in ('id', 'at', 'state', 'reason', 'repo')}
                                           for event in outbox['events']]
        except (ValueError, TypeError):
            pass
        row['change_log'] = log
        try:
            analysis = analytics_report.answer(name, now=now)['by_account'][name]
            row['analytics_collection'] = analysis['collection']
        except (OSError, ValueError, TypeError, KeyError, accounts.AccountError):
            row['analytics_collection'] = None
            row['cannot_say'].append('analytics_collection_unavailable')
    return _scrub(row)


def _summary(rows):
    valid = [row for row in rows.values() if row.get('ledger') == 'available']
    return dict(accounts=len(rows), by_medium=dict(collections.Counter(row['medium'] for row in valid)),
                production=sum(row['production'] is True for row in valid),
                token_within_seven_days=sum(isinstance(row['token'].get('remaining_days'), (int, float)) and
                                           row['token']['remaining_days'] <= 7 for row in valid),
                inflight=sum(bool(row['inflight']) for row in valid),
                malformed=sum(bool(((row.get('queue') or {}).get('counts') or {}).get('malformed') or
                                   ((row.get('queue') or {}).get('counts') or {}).get('unattributed_malformed')) for row in valid),
                timer_stopped=sum(any(unit.get('active') == 'inactive' for unit in (row['timer'].get('units') or [])) for row in valid))


def answer(operation='inventory', *, account=None, probe=False, via='cli', now=None,
           since=None, event=None, limit=20, since_last_read=False, mark_read=False, by=None):
    if operation not in OPERATIONS or via not in ('cli', 'http') or type(probe) is not bool:
        raise ValueError('invalid_admin_request')
    if account is not None and not accounts.name_is_safe(account):
        raise ValueError('invalid_account')
    if operation == 'account':
        if account is None:
            raise ValueError('account_required')
        try:
            (Path(accounts.accounts_dir()) / (account + '.json')).lstat()
        except FileNotFoundError:
            if _stopped_row(account) is None:raise ValueError('ledger_missing') from None
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('invalid_limit')
    if via == 'http' and (probe or mark_read):
        raise ValueError('invalid_request')
    now = now or jst.now_jst()
    result = dict(schema_version=1, report_type='admin_' + operation, generated_at=jst.iso(now),
                  observed_from={'host': socket.gethostname(), 'via': via}, cannot_say=[])
    if operation == 'log':
        # Register secrets before reading arbitrary historical diff text.
        for name in accounts.list_account_names():
            try: _register(accounts.load_account(name), via=via)
            except (accounts.AccountError, ValueError, TypeError): pass
        result['events'], result['broken'] = admin_log.read(since=since, account=account, event=event)
    elif operation in ('inventory', 'account'):
        names = [account] if account else _admin_names()
        result['by_account'] = {name: _account(name, now, probe=probe, via=via,
                                              detail=operation == 'account', limit=limit) for name in names}
        result['summary'] = _summary(result['by_account'])
    else:
        result.update(extra(operation, account=account, via=via, now=now,
                            since_last_read=since_last_read, mark_read=mark_read, by=by))
    return report_details.attach(_scrub(result))


def render_markdown(payload):
    return '# ' + payload['report_type'] + '\n\n' + '\n'.join(
        '    ' + line for line in json.dumps(payload, ensure_ascii=False, indent=2).splitlines()) + '\n'


def command(args):
    try:
        payload = answer(args.admin_operation, **{key: getattr(args, key) for key in
            ('account', 'probe', 'since', 'event', 'limit', 'since_last_read', 'mark_read', 'by') if hasattr(args, key)})
    except (OSError, ValueError, TypeError, accounts.AccountError) as error:
        reason = str(error) if str(error) in ('ledger_missing', 'invalid_options') else 'admin_report_unavailable'
        print(reason + ': check account, options and local records', file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else render_markdown(payload))
    return 0


def register(sub):
    parser = sub.add_parser('admin', help='管理者用レポートと管理者 CLI 操作')
    commands = parser.add_subparsers(dest='admin_operation', required=True)
    from . import approval_relay
    approval_relay.register(commands)
    from . import deletion
    deletion.register(commands)
    from . import media_cleanup
    media_cleanup.register(commands)
    from . import budget_x
    budget_x.register(commands)
    # 監視語（設計 3.1.0 §3）。**語は管理者が入れる**——口の中身は
    # `thth/watch_cli.py` に閉じる（ここに足すのはこの 2 行だけ）。
    from . import watch_cli
    watch_cli.register(commands)
    # 報告の口（設計 3.1.2）。口の中身は `thth/report_inbox.py` に閉じる。
    from . import report_inbox
    report_inbox.register_admin(commands)
    # 施策の広場（設計 3.4.0）。口の中身は `thth/plaza_cli.py` に閉じる。
    from . import plaza_cli
    plaza_cli.register_admin(commands)
    # 観測の地図（設計 3.5.0）。点と線は人が足す——口の中身は `thth/map_cli.py` に閉じる。
    from . import map_cli
    map_cli.register_admin(commands)
    for name in OPERATIONS:
        p = commands.add_parser(name)
        p.add_argument('--json', action='store_true')
        if name == 'account':
            p.add_argument('account')
            p.add_argument('--limit', type=int, default=20)
        if name in ('inventory', 'account'):
            p.add_argument('--probe', action='store_true')
        if name == 'log':
            p.add_argument('--account')
            p.add_argument('--since')
            p.add_argument('--event', choices=sorted(admin_log.EVENTS))
        if name == 'diff':
            p.add_argument('--since-last-read', action='store_true', required=True)
            p.add_argument('--mark-read', action='store_true')
            p.add_argument('--by')
        p.set_defaults(func=command)


def timer(name, *, via='cli'):
    unavailable = dict(observed_at=None, units=None, timer_reason='timer_observation_unavailable')
    if via == 'http':
        cached = _admin_json('timers.json')
        nodes = (cached or {}).get('by_account')
        value = nodes.get(name) if isinstance(nodes, dict) else None
        if isinstance(value, dict) and jst.parse(value.get('observed_at')) and isinstance(value.get('units'), list):
            return value
        return unavailable
    if not Path('/run/systemd/system').is_dir():
        return {**unavailable, 'timer_reason': 'systemd_unavailable'}
    units = []
    try:
        for unit in account_report.timer_units(name):
            proc = subprocess.run(['systemctl', 'show', unit,
                '--property=Id,LoadState,ActiveState,LastTriggerUSec,NextElapseUSecRealtime'],
                capture_output=True, text=True, timeout=10)
            if proc.returncode:
                raise ValueError('systemctl_failed')
            fields = dict(line.split('=', 1) for line in proc.stdout.splitlines() if '=' in line)
            if fields.get('LoadState') != 'loaded':
                units.append(dict(unit=unit,active=None,last_trigger=None,next_trigger=None,
                                  exec_main_status=None, reason='timer_not_loaded'))
                continue
            service = subprocess.run(['systemctl', 'show', unit.removesuffix('.timer') + '.service', '--property=ExecMainStatus'],
                                     capture_output=True, text=True, timeout=10)
            status = dict(line.split('=',1) for line in service.stdout.splitlines() if '=' in line)
            units.append(dict(unit=unit,active=fields.get('ActiveState'),
                last_trigger=fields.get('LastTriggerUSec') or None,
                next_trigger=fields.get('NextElapseUSecRealtime') or None,
                exec_main_status=int(status['ExecMainStatus']) if service.returncode == 0 and status.get('ExecMainStatus','').isdigit() else None))
    except (OSError, ValueError, subprocess.SubprocessError):
        return {**unavailable, 'timer_reason': 'systemctl_unavailable'}
    return dict(observed_at=jst.iso(), units=units, timer_reason=None)


def _release(via='cli'):
    from . import report, __version__
    env_path = Path(appenv.default_path())
    allowed = via == 'cli' or env_path.resolve().is_relative_to(Path(accounts.thth_root()).resolve())
    env = appenv.describe() if allowed else dict(exists=None, keys_present=None, mode_ok=None, reason='app_env_outside_report_root')
    return dict(version=__version__, **report.release_summary(), app_env=env)


def _snapshot(value, *, via="cli"):
    # Drop observation timestamps; diff records changes in facts, not read times.
    rows = {}
    for name, row in value['by_account'].items():
        if row.get('ledger') != 'available':
            rows[name] = {'ledger': 'unreadable'}
            continue
        token = row['token']
        remaining = token.get('remaining_days')
        rows[name] = dict(production=row['production'], token_obtained_at=token['obtained_at'],
                         token_present=token['present'], token_expires_at=token['expires_at'],
                         token_expiring=remaining <= 7 if isinstance(remaining,(int,float)) else None,
                         inflight=row['inflight'], queue_counts=(row.get('queue') or {}).get('counts'),
                         timers={u['unit']:u['active'] for u in row['timer'].get('units') or []})
    release = _release(via)
    return dict(inventory=value, by_account=rows, release={key:release.get(key) for key in ('version','head')})


def _diff(current, now, *, mark_read, by):
    from . import handoff_cursor
    if mark_read:
        admin_log.actor(by)
    elif by is not None:
        raise ValueError('by_requires_mark_read')
    previous = None
    reason = 'no_previous_session_cursor'
    directory = None
    try:
        directory = handoff_cursor._directory('_admin')
        fd = os.open('admin_cursor.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('cursor_unreadable')
            data = stream.read(4*1024*1024+1)
        if len(data)>4*1024*1024:
            raise ValueError('cursor_unreadable')
        value = json.loads(data, object_pairs_hook=handoff_cursor._pairs)
        if (value.get('schema_version') != 1 or not jst.parse(value.get('read_at')) or
            jst.parse(value['read_at']) > now or not isinstance(value.get('snapshot'),dict) or
            not isinstance(value['snapshot'].get('by_account'),dict) or not isinstance(value['snapshot'].get('release'),dict)):
            raise ValueError('cursor_unreadable')
        admin_log.actor(value.get('by'))
        for name, node in value['snapshot']['by_account'].items():
            if not accounts.name_is_safe(name) or not isinstance(node, dict):
                raise ValueError('cursor_unreadable')
            allowed = {'production','token_obtained_at','token_present','token_expires_at','token_expiring','inflight','queue_counts','timers'}
            if node != {'ledger':'unreadable'} and set(node) != allowed:
                raise ValueError('cursor_unreadable')
            if node == {'ledger':'unreadable'}:
                continue
            if any(node[k] is not None and type(node[k]) is not bool for k in ('production','token_present','token_expiring')):
                raise ValueError('cursor_unreadable')
            if any(node[k] is not None and (not isinstance(node[k],str) or not jst.parse(node[k])) for k in ('token_obtained_at','token_expires_at')):
                raise ValueError('cursor_unreadable')
            if node['inflight'] is not None and (not isinstance(node['inflight'],dict) or set(node['inflight']) != {'since','reason_code','next_action_code'}):
                raise ValueError('cursor_unreadable')
            counts=node['queue_counts']
            if counts is not None and (not isinstance(counts,dict) or any(type(v) is not int or v < 0 for v in counts.values())):
                raise ValueError('cursor_unreadable')
            if not isinstance(node['timers'],dict) or any(not isinstance(k,str) or v is not None and not isinstance(v,str) for k,v in node['timers'].items()):
                raise ValueError('cursor_unreadable')
        if set(value['snapshot']['release']) != {'version','head'} or any(v is not None and not isinstance(v,str) for v in value['snapshot']['release'].values()):
            raise ValueError('cursor_unreadable')
        previous, reason = value, None
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError, AttributeError):
        reason = 'cursor_unreadable'
    finally:
        if directory is not None: os.close(directory)
    changes=[]
    if previous:
        old=previous['snapshot']
        for name in sorted(set(old['by_account']) | set(current['by_account'])):
            before,after=old['by_account'].get(name),current['by_account'].get(name)
            if before is None or after is None:
                changes.append(dict(account=name, field='account', previous=before, current=after))
            else:
                for key in sorted(set(before)|set(after)):
                    if before.get(key)!=after.get(key):
                        changes.append(dict(account=name,field=key,previous=before.get(key),current=after.get(key)))
        if old['release']!=current['release']:
            changes.append(dict(account=None,field='release',previous=old['release'],current=current['release']))
    if mark_read:
        handoff_cursor.write_snapshot('_admin','admin_cursor.json',dict(schema_version=1,read_at=jst.iso(now),
                                                                       by=by,snapshot=current))
    return dict(changes=changes, read_at=previous['read_at'] if previous else None,
                by=previous['by'] if previous else None, cannot_say=[reason] if reason else [], snapshot=current)


def extra(operation, *, account, via, now, since_last_read=False, mark_read=False, by=None):
    if operation=='release':
        return {'release':_release(via)}
    names = [account] if account else _admin_names()
    if operation=='timers':
        from . import leave
        # 台帳の無い account（退出を終えた等）の unit は `orphan_unit` として別に出す
        # （3.5.1 件 1）。by_account と timers.json には台帳のある account だけを置く
        # ——退出した account の記録は次の更新で消える。
        ledger = set(accounts.list_account_names())
        rows = {name: timer(name, via=via) for name in names if name in ledger}
        leave_rows = {}
        for name in leave.names():
            try: row = leave.read(name)
            except (OSError, ValueError, TypeError, KeyError): continue
            if row and isinstance(row.get('timers'), dict): leave_rows[name] = row['timers']
        # 生の systemctl は CLI だけ（HTTP・MCP は前回の記録だけを読む）。
        found = timer_cleanup.orphans(ledger, cached=_admin_json('timers.json'), leave_rows=leave_rows,
                                      live=via == 'cli', only=account)
        # Only the explicit CLI timers command records observations. Inventory,
        # HTTP and MCP remain read-only; a non-systemd host preserves the last cache.
        if via == 'cli' and any(row['observed_at'] is not None for row in rows.values()):
            from . import handoff_cursor
            try:
                handoff_cursor.write_snapshot('_admin', 'timers.json',
                                              _scrub(dict(schema_version=1, by_account=rows)))
            except (OSError, ValueError, TypeError):
                raise ValueError('timer_snapshot_unavailable') from None
        return {'by_account': rows, **found}
    if operation=='tokens':
        from . import scopes
        rows=[]
        for name in names:
            stopped=_stopped_row(name)
            if stopped is not None:
                rows.append(dict(account=name,present=None,reason='account_stopped',leave=stopped['leave']));continue
            try:
                cfg=accounts.load_account(name);_register(cfg, via=via)
                token=_token(cfg,now)
                from .adapters import auth_x
                expected=(scopes.DEFAULT_SCOPES if cfg.get('media')=='threads' else auth_x.SCOPES if cfg.get('media')=='x' else None)
                actual=token['scopes']
                token.update(account=name,default_scopes=expected,
                             missing_scopes=sorted(set(expected)-set(actual)) if expected is not None and actual is not None and token['scopes_source']=='response' else None)
                if cfg.get('media') == 'mastodon':
                    observation = doctor.read_observation(name)
                    token['default_scopes'] = scopes.MASTODON_SCOPES
                    token['recorded_scopes_source'] = token['scopes_source']
                    if token['scopes_source'] == 'response' and isinstance(actual, list):
                        token.update(missing_scopes=sorted(set(scopes.MASTODON_SCOPES)-set(actual)),
                                     missing_scopes_inferred=False, unknown_scopes=[], scopes_observed_at=token['obtained_at'])
                    else:
                        current = observation if observation and observation.get('probed_at') and observation.get('probe_current_credentials') is not False else None
                        token['scopes_source'] = 'probe' if current else None
                        token['scopes_observed_at'] = current['probed_at'] if current else None
                        token['missing_scopes_inferred'] = True
                        probes = current['probes'] if current else []
                        relevant = [p for p in probes if p.get('permission') in scopes.MASTODON_SCOPES]
                        missing = sorted({p['permission'] for p in relevant if p.get('failure') == 'permission'})
                        reached = {p['permission'] for p in relevant if p.get('ok') is True}
                        token['missing_scopes'] = missing if missing or reached else None
                        token['unknown_scopes'] = sorted(set(scopes.MASTODON_SCOPES) - reached - set(missing))
                history=runs.read_runs(accounts.state_dir_for(name))
                maintenance=[r for r in history if isinstance(r,dict) and r.get('account')==name and r.get('action')=='maintain' and (r.get('refreshed') is True or r.get('error')=='refresh_failed')]
                last=maintenance[-1] if maintenance else None
                token['last_refresh']={key:last.get(key) for key in ('run_id','status','refreshed','error')} if last else None
                rows.append(token)
            except (OSError, ValueError, TypeError, accounts.AccountError):
                rows.append(dict(account=name,remaining_days=None,reason='token_records_unavailable'))
        rows.sort(key=lambda r:(r.get('remaining_days') is None,r.get('remaining_days') or 0,r['account']))
        return {'tokens':rows}
    if operation=='diff':
        if not since_last_read:
            raise ValueError('since_last_read_required')
        value=answer('inventory',via=via,now=now)
        return _diff(_snapshot(value, via=via),now,mark_read=mark_read,by=by)
    raise ValueError('unsupported_operation')
