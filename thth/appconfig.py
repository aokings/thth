"""Audited private app clients. Human input and registration HTTP stay outside locks."""
import hashlib
import json
from pathlib import Path
import sys
from . import accounts, admin_log, leave_gate, appenv, authclients, authflow, handoff_cursor, secrets_fs
from .authflow import FlowError, secret


def subject(media, origin=None):
    return 'app-'+media+('-'+hashlib.sha256(origin.encode()).hexdigest() if origin else '')


def validate_location(path, cfg=None):
    resolved=Path(path).resolve()
    forbidden=[Path(accounts.thth_root()).resolve(),Path(__file__).resolve().parents[1]]
    if cfg and cfg.get('repo_dir'):forbidden.append(Path(cfg['repo_dir']).resolve())
    if any(resolved==p or p in resolved.parents for p in forbidden):
        raise FlowError('auth_client_store_must_be_outside_project')


def snapshot(path):
    return authflow._token_snapshot(Path(path))


def save(media,path,data,*,by,expected,origin=None):
    """Caller captures expected before input/network; commit refuses a newer client."""
    admin_log.actor(by);validate_location(path)
    path=Path(path);changed=False;old=None
    def rollback():
        if changed:
            try:
                if old is None:path.unlink(missing_ok=True)
                else:secrets_fs.atomic_write_text(str(path),old[0].decode(),mode=old[1])
            except (OSError,ValueError):raise FlowError('app_set_rollback_failed_outcome_uncertain') from None
    with leave_gate.credentials(), admin_log.transaction(rollback=rollback):
        old=snapshot(path)
        if authflow._generation(old)!=authflow._generation(expected):raise FlowError('app_client_changed: 保存しません')
        before = {'client_id': False, 'client_secret': False}
        if old:
            if media == 'threads':
                parsed = appenv.parse_app_env(old[0].decode())
                before = {'client_id':bool(parsed.get('THREADS_APP_ID')), 'client_secret':bool(parsed.get('THREADS_APP_SECRET'))}
            else:
                parsed = authclients.read(path)
                before = {key:bool(parsed.get(key)) for key in before}
        changed=True
        if media=='threads':
            # Threads auth/rehearse still use the established app.env schema.
            secrets_fs.atomic_write_text(str(path),appenv.render_app_env(data['client_id'],data['client_secret']),mode=0o600)
        else:authclients.write(path,data)
        admin_log.append('app_set',subject(media,origin),{'media':media},by=by,
                         diff={'client_id':['present' if before['client_id'] else 'absent','present'],
                               'client_secret':['present' if before['client_secret'] else 'absent','present']})


def run(media='threads',*,app_id=None,secret_stdin=False,stdin=False,by=None,input_func=None,path=None,log=print):
    try:
        admin_log.actor(by)
        if admin_log._active_fd.get() is not None:raise FlowError('app_set_nested_transaction_refused')
        if media not in ('threads','x'):raise FlowError('app_set_medium_unsupported: Mastodon は auth が自動登録します')
        if stdin and (app_id is not None or secret_stdin):raise FlowError('app_set_input_options_conflict')
        if not stdin and media!='threads':raise FlowError('app_set_x_requires_json_stdin')
        if path is None and media=='x':
            # 2.14: `media.write` を足したので client は scope 集合ごとの世代に書く
            # （旧無印は read-only・`auth_x.legacy_client_path`）。
            from .adapters.auth_x import client_path as x_client_path
            path=x_client_path({})
        path=Path(path or appenv.default_path())
        validate_location(path)
        Path(accounts.thth_root()).mkdir(parents=True,exist_ok=True)
        with admin_log.transaction():expected=snapshot(path)
        if stdin:
            raw=input_func() if input_func else sys.stdin.read(65537)
            if not isinstance(raw,str) or len(raw)>65536:raise FlowError('app_set_invalid_json')
            try:data=json.loads(raw,object_pairs_hook=handoff_cursor._pairs)
            except ValueError:raise FlowError('app_set_invalid_json') from None
            if not isinstance(data,dict):raise FlowError('app_set_invalid_json')
        else:
            if not isinstance(app_id,str) or not app_id.strip():raise FlowError('--app-id が空です。書きませんでした')
            data={'client_id':app_id.strip(),'client_secret':appenv._read_secret(stdin=secret_stdin,input_func=input_func)}
            if not data['client_secret'].strip():raise FlowError('App Secret が空です。書きませんでした')
        for key in ('client_id','client_secret'):
            value=data.get(key)
            if not isinstance(value,str) or not value.strip() or len(value)>8192 or any(ord(c)<32 or ord(c)==127 for c in value.strip()):
                raise FlowError('app_set_client_missing_or_invalid: client ID / Secret が空か不正です')
            data[key]=secret(value.strip())
        if media=='threads':
            if set(data)!={'client_id','client_secret'}:raise FlowError('app_set_invalid_json_keys')
        else:
            from .adapters.auth_x import client
            client(data)
        save(media,path,data,by=by,expected=expected)
        label = 'app.env' if media == 'threads' else 'x app'
        log(f'{label} を書きました: {path}（600・値は表示しません）');return 0
    except admin_log.AdminLogError as exc:
        log('admin_change_recorded_durability_unconfirmed' if exc.complete else
            'admin_change_partially_recorded_outcome_uncertain' if exc.appended else 'admin_change_refused');return 2
    except appenv.AppEnvError as exc:
        log(str(exc));return 2
    except (OSError,ValueError,EOFError) as exc:
        log(str(exc) if isinstance(exc,FlowError) else 'app_set_failed: --by と private client store を確認してください');return 2
