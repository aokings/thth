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
from . import (accounts, admin_log, analytics_report, appenv, collection_status, doctor,
               incident, jst, oauth, operations_handoff, redact, report_details, runs)

FIELDS = ('production', 'hashtags', 'max_hashtags', 'min_interval_hours', 'quiet_hours',
          'collect_days', 'stale_days', 'scheduled')
TOKEN_FIELDS = ('mode_ok', 'obtained_at', 'expires_at', 'remaining_days', 'no_expiry',
                'user_id', 'username', 'scopes', 'scopes_source', 'handle_matches')
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


def _scrub(value):
    # Preserve report schema; only values (and untrusted object keys) are scrubbed.
    if isinstance(value, str):
        return admin_log.MAIL.sub('[redacted-email]', redact.redact(value))
    if isinstance(value, dict):
        return {_scrub(k): _scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    return value


def _register(cfg):
    """Register only configured secret files; never enumerate the secret directory."""
    try:
        token = accounts.load_token(cfg)
        if isinstance(token, dict):
            for key, value in token.items():
                if admin_log.SECRET.search(key) or key.lower().endswith('jwt'):
                    redact.register_secret(value)
    except (OSError, ValueError, TypeError):
        pass
    for path in (cfg.get('env'), appenv.default_path()):
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
        scopes = doctor.recorded_scopes(token)
        result.update(scopes=scopes['scopes'], scopes_source=scopes['source'])
        try:
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
        observed = doctor.diagnose(name)
        return dict(probed_at=jst.iso(), source='live_readonly_probe', result=observed)
    cached = _json(Path(accounts.state_dir_for(name)) / 'doctor.json')
    if cached and isinstance(cached.get('probes'), list) and jst.parse(cached.get('probed_at')):
        return dict(probed_at=cached['probed_at'], source='recorded', result={
            'probes': [{k: row.get(k) for k in ('key', 'ok', 'status', 'http_status', 'http', 'reason')}
                       for row in cached['probes'] if isinstance(row, dict)]})
    return dict(probed_at=None, source=None, result=None, reason='permissions_not_recorded')


def _defaults(cfg):
    from . import account_cli
    try:
        value = _json(account_cli.example_path(cfg.get('media')))
        return {key: (value or {}).get(key) for key in FIELDS}
    except (OSError, ValueError, TypeError):
        return dict.fromkeys(FIELDS)


def _account(name, now, probe=False, via='cli', detail=False, limit=20):
    cfg = None
    raw = _json(Path(accounts.accounts_dir()) / (name + '.json'))
    try:
        cfg = accounts.load_account(name)
    except (accounts.AccountError, OSError, ValueError, TypeError, KeyError):
        pass
    if cfg is None or raw is None:
        return dict(account=name, ledger='unreadable', cannot_say=['ledger_unreadable'])
    _register(cfg)
    row = {key: raw.get(key) for key in ('account', 'project', 'handle', 'instance')}
    row.update(account=name, medium=raw.get('media'), ledger='available', **{key: raw.get(key) for key in FIELDS})
    row['defaults'] = _defaults(cfg)
    row['repo'] = _repo(cfg)
    row['provenance'] = {key: (raw.get('provenance') or {}).get(key) for key in PROVENANCE_FIELDS}
    row['provenance_reason'] = None if raw.get('provenance') else 'recorded_before_2.9.0'
    row['token'] = _token(cfg, now)
    try:
        row['permissions'] = _permissions(name, probe)
    except (OSError, ValueError, TypeError):
        row['permissions'] = dict(probed_at=None, result=None, reason='permissions_unavailable')
    handoff = operations_handoff._account(name, cfg, now)
    row.update({key: handoff[key] for key in ('queue', 'inflight', 'last_post', 'sent_count')})
    if accounts.repo_state(cfg) == accounts.REPO_NONE:
        row.update(queue=None, inflight=None, last_post=None, sent_count=None)
    collection, reasons = collection_status.summarize(name, now)
    row['collection'] = collection
    row['last_reached_mark'] = None
    try:
        from . import measured
        observations = [obs for post in measured.load(name, observation_metadata=True)['posts']
                        for obs in post.get('observations', []) if isinstance(obs, dict)]
        eligible = [(jst.parse(obs.get('collected_at')), obs.get('marks')) for obs in observations
                    if jst.parse(obs.get('collected_at')) and jst.parse(obs.get('collected_at')) <= now and obs.get('marks')]
        row['last_reached_mark'] = max(eligible, key=lambda item: item[0])[1] if eligible else None
    except (OSError, ValueError, TypeError, KeyError):
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
            marks = analysis.get('posts', {}).get('marks_by_post', [])
            reached = [(obs.get('collected_at'), mark) for post in marks for mark, obs in post.get('marks', {}).items() if obs]
            row['last_reached_mark'] = max(reached)[1] if reached else None
        except (OSError, ValueError, TypeError, KeyError):
            row['analytics_collection'] = None
            row['last_reached_mark'] = None
            row['cannot_say'].append('analytics_collection_unavailable')
    return _scrub(row)


def timer(name, *, via='cli'):
    # Shared implementation is completed in §3.5. A missing observation is not a stopped timer.
    return dict(observed_at=None, units=None, timer_reason='timer_observation_unavailable')


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
    if operation == 'account' and account is None:
        raise ValueError('account_required')
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
            try: _register(accounts.load_account(name))
            except (accounts.AccountError, ValueError, TypeError): pass
        result['events'], result['broken'] = admin_log.read(since=since, account=account, event=event)
    elif operation in ('inventory', 'account'):
        names = [account] if account else accounts.list_account_names()
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
    except (OSError, ValueError, TypeError, accounts.AccountError):
        print('admin_report_unavailable: check account, options and local records', file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else render_markdown(payload))
    return 0


def register(sub):
    parser = sub.add_parser('admin', help='管理者用の読み取り専用レポート')
    commands = parser.add_subparsers(dest='admin_operation', required=True)
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
