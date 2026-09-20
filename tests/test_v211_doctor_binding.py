"""A permission result belongs to the observed ledger and credential together."""
import json
from pathlib import Path
import pytest
from thth import accounts,admin_report,doctor,handoff_cursor,jst
from tests.test_v211_authflow import env,opaque
from tests.test_v211_auth_integration import cfg_media,put_token


@pytest.mark.parametrize('entry',['doctor','admin'])
@pytest.mark.parametrize('when',['during','after'])
@pytest.mark.parametrize('field',['instance','handle','media','token'])
def test_changed_ledger_with_same_token_bytes_never_inherits_probe(env,monkeypatch,entry,when,field):
    cfg=cfg_media(env,'mastodon');cfg['instance']='https://before.example'
    env['ledger'].write_text(json.dumps(cfg));env['cfg']=accounts.load_account('alpha')
    put_token(env,no_expiry=True);old_bytes=Path(env['cfg']['token']).read_bytes()
    seen=[]
    def change():
        changed=json.loads(env['ledger'].read_text())
        if field=='instance':changed[field]='https://after.example'
        elif field=='handle':changed[field]='different'
        elif field=='media':changed[field]='threads'
        else:
            dest=env['root']/'different.token';dest.write_bytes(old_bytes);dest.chmod(0o600);changed[field]=str(dest)
        env['ledger'].write_text(json.dumps(changed))
    class Adapter:
        def probe(self,**kwargs):
            if when=='during':change()
            return [dict(key='search',permission='read:search',label='search',detail='denied',ok=False,failure='permission')]
    def make(cfg,token):seen.append(dict(cfg));return Adapter()
    monkeypatch.setattr('thth.adapters.make_adapter',make)
    if entry=='doctor':assert doctor.run_doctor('alpha',as_json=True,log=lambda _:None)==1
    else:admin_report._permissions('alpha',True)
    assert seen[0]['instance']=='https://before.example' and seen[0]['media']=='mastodon'
    if when=='after':
        assert doctor.read_observation('alpha')['probe_current_credentials'] is True
        change()
    current=accounts.load_account('alpha');assert Path(current['token']).read_bytes()==old_bytes
    cache=doctor.read_observation('alpha')
    assert cache['probed_at']==jst.iso() and cache['probes'][0]['failure']=='permission'
    assert cache['probe_current_credentials'] is False
    permissions=admin_report._permissions('alpha',False)
    assert permissions['probe_current_credentials'] is False
    row=admin_report.answer('tokens',account='alpha',via='http')['tokens'][0]
    assert row['missing_scopes'] is None
    assert row['scopes_source']!='probe'
    raw=handoff_cursor.read_snapshot('alpha','doctor.json')
    for value in (cache,permissions,row):
        assert raw['probe_credential_generation'] not in json.dumps(value)
        assert 'credential_generation' not in json.dumps(value)


def test_missing_token_stays_unknown_despite_available_ledger(env):
    assert doctor._credential_generation(env['cfg']) is None
    doctor.record_observation('alpha',{'probes':[],'error':'no token'},credential_generation=None)
    assert handoff_cursor.read_snapshot('alpha','doctor.json')['probe_credential_generation'] is None
    assert doctor.read_observation('alpha')['probe_current_credentials'] is False


def test_legacy_schema1_remains_unbound_history(env):
    put_token(env)
    handoff_cursor.write_snapshot('alpha','doctor.json',dict(schema_version=1,account='alpha',probed_at=jst.iso(),probes=[],error=None))
    assert doctor.read_observation('alpha')['probe_current_credentials'] is None


def test_token_only_schema2_receipt_is_not_reinterpreted_as_bound(env):
    import hashlib
    put_token(env)
    old=hashlib.sha256(Path(env['cfg']['token']).read_bytes()).hexdigest()
    handoff_cursor.write_snapshot('alpha','doctor.json',dict(schema_version=2,account='alpha',probed_at=jst.iso(),probes=[],error=None,probe_credential_generation=old))
    assert doctor.read_observation('alpha')['probe_current_credentials'] is False
