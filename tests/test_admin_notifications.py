import json
from pathlib import Path
import pytest
from thth import account_cli, admin_notifications, admin_log, incident
from tests.test_admin_log import args,root


def test_selected_admin_only_deduplicated_and_secret_free(root,monkeypatch):
    delivered=[]
    monkeypatch.setattr(incident,'readiness',lambda cfg:dict(admin_configured=True,user_configured=True,smtp_configured=True))
    monkeypatch.setattr(incident,'settings',lambda cfg:dict(admin='private-admin@example.test'))
    monkeypatch.setattr(incident,'_send',lambda settings,recipient,event,account: delivered.append((recipient,event)) or True)
    assert account_cli.cmd_add(args())==0
    assert len(delivered)==1 and delivered[0][0]=='private-admin@example.test'
    event=delivered[0][1]['admin_event'];cfg=json.loads((root/'accounts/test-threads.json').read_text())
    admin_notifications.notify(event,cfg)
    assert len(delivered)==1
    assert event['event']=='account_added'
    assert 'private-admin@example.test' not in json.dumps(event)
    assert admin_notifications.notify({**event,'event':'token_refreshed'},cfg)=='disabled'
    assert admin_notifications.notify({**event,'event':'token_set'},dict(cfg,notify_admin_on_change=False))=='disabled'

def test_transport_failure_preserves_pending_without_affecting_ledger(root,monkeypatch):
    monkeypatch.setattr(incident,'readiness',lambda cfg:dict(admin_configured=True,user_configured=False,smtp_configured=True))
    monkeypatch.setattr(incident,'settings',lambda cfg:dict(admin='private-admin@example.test'))
    monkeypatch.setattr(incident,'_send',lambda *a:False)
    assert account_cli.cmd_add(args())==0
    state=json.loads((root/'state/_admin/admin_notifications.json').read_text())
    assert state['events'][0]['status']=='pending'
    assert (root/'accounts/test-threads.json').is_file()
