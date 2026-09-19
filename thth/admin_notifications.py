"""Durable admin-only change notices using the existing incident SMTP transport."""
import hashlib
import json
from pathlib import Path
from . import accounts, admin_log, handoff_cursor, incident

SELECTED = {'account_added', 'production_enabled', 'token_set', 'token_revoked'}
FILENAME = 'admin_notifications.json'


def notify(event, cfg):
    if event['event'] not in SELECTED or cfg.get('notify_admin_on_change') is False:
        return 'disabled'
    path = Path(accounts.thth_root()) / 'state/_admin' / FILENAME
    state = incident._load(path, {'schema_version':1, 'events':[]})
    if not isinstance(state,dict) or state.get('schema_version')!=1 or not isinstance(state.get('events'),list):
        raise ValueError('admin_notification_outbox_unreadable')
    text = json.dumps(event, ensure_ascii=False, sort_keys=True)
    identity = hashlib.sha256(text.encode()).hexdigest()
    if not any(row.get('id')==identity for row in state['events']):
        state['events'].append({'id':identity,'event':event,'status':'pending'})
    def save(): handoff_cursor.write_snapshot('_admin',FILENAME,state)
    save()
    ready=incident.readiness(cfg)
    if not ready['admin_configured'] or not ready['smtp_configured']:
        return 'not_configured'
    settings=incident.settings(cfg)
    for row in state['events']:
        if row.get('status') in ('accepted','disabled'): continue
        data=row['event']
        try:
            current=accounts.load_account(data['account'])
        except (accounts.AccountError, ValueError, TypeError):
            continue
        if current.get('notify_admin_on_change') is False:
            row['status']='disabled';save();continue
        notice=dict(id=row['id'],state='admin_change',at=data['at'],reason='healthy',repo='no_source',admin_event=data)
        try:
            accepted=incident._send(settings,settings['admin'],notice,data['account'])
        except Exception:
            accepted=False
        row['status']='accepted' if accepted else 'pending'
        save()
        if not accepted: break
    return 'processed'
