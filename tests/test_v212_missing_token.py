"""Missing credentials remain unknown; diagnosis does not become an outage."""
import json
import socket
import pytest
from thth import accounts, admin_report, cli, doctor, handoff_cursor
from tests.test_v211_authflow import env


@pytest.mark.parametrize('cfg',[{}, {'token':None}, {'token':''}])
def test_missing_token_generation_is_unknown(cfg):
    assert doctor._credential_generation(cfg) is None


@pytest.mark.parametrize('value',[None,''])
@pytest.mark.parametrize('entry',['direct','admin_cli','doctor_cli'])
def test_missing_token_can_be_diagnosed_without_network(env,monkeypatch,capsys,value,entry):
    cfg=dict(env['cfg'],token=value)
    env['ledger'].write_text(json.dumps(cfg))
    monkeypatch.setattr(socket.socket,'connect',lambda *args:pytest.fail('missing token must not call API'))
    if entry=='direct':
        payload=admin_report._permissions('alpha',True)
        assert payload['result']['probes']==[] and payload['result']['error']
        assert 'reason' not in payload # Recording succeeded; token is what is missing.
    elif entry=='admin_cli':
        other=dict(cfg,account='beta');env['ledger'].with_name('beta.json').write_text(json.dumps(other))
        assert cli.main(['admin','inventory','--probe','--json'])==0
        payload=json.loads(capsys.readouterr().out)
        assert set(payload['by_account'])=={'alpha','beta'}
        for row in payload['by_account'].values():
            assert row['token']['present'] is False
            assert row['token']['remaining_days'] is None
            assert row['permissions']['result']['probes']==[]
            assert row['permissions']['result']['error']
    else:
        assert cli.main(['doctor','alpha','--json'])!=0
        payload=json.loads(capsys.readouterr().out)
        assert payload['probes']==[] and payload['error']
    assert doctor._credential_generation(accounts.load_account('alpha')) is None
    raw=handoff_cursor.read_snapshot('alpha','doctor.json')
    assert raw['probe_credential_generation'] is None
    assert doctor.read_observation('alpha')['probe_current_credentials'] is False
