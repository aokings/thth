"""403 is a permission refusal; only permission probes imply missing scopes."""
import json
import urllib.error
from pathlib import Path
import pytest
from thth import accounts, admin_report, doctor, jst, scopes
from thth.adapters import mastodon


@pytest.mark.parametrize('status', [403,404,422,500])
@pytest.mark.parametrize('method', ['_get_json','_get_list'])
def test_http_failure_code_and_scope_guidance(status,method,monkeypatch):
    adapter=mastodon.MastodonAdapter(instance='https://fixture.invalid',access_token='FAKE_TOKEN_SECRET')
    def request(*a,**k):raise urllib.error.HTTPError('url',status,'FAKE_TOKEN_SECRET',{},None)
    monkeypatch.setattr(adapter,'_request',request)
    with pytest.raises(mastodon.AdapterError) as exc:
        getattr(adapter,method)('/api/v2/search?type=statuses','検索')
    assert exc.value.failure==('permission' if status==403 else None)
    assert exc.value.http_status==status
    assert ('read:search' in str(exc.value)) is (status==403)
    assert ('token set' in str(exc.value)) is (status==403)
    assert 'FAKE_TOKEN_SECRET' not in str(exc.value)


@pytest.mark.parametrize('status,missing', [(200,[]),(403,['read:search']),(500,[])])
def test_doctor_cache_to_admin_permission_inference(status,missing,isolated_account_factory,monkeypatch):
    isolated_account_factory('scope-one',media='mastodon',handle='scope',instance='https://fixture.invalid')
    cfg=accounts.load_account('scope-one');Path(cfg['token']).write_text(json.dumps({'access_token':'FAKE_SCOPE_SECRET'}))
    called=[]
    def request(self,method,path,**kwargs):
        assert method=='GET';called.append(path)
        if path.startswith('/api/v1/accounts/verify_credentials'):return {'id':'1','acct':'scope'}
        if path.startswith('/api/v2/search'):
            if status!=200:raise urllib.error.HTTPError('url',status,'fixture',{},None)
            return {'statuses':[]}
        if path=='/api/v1/notifications?limit=1':return []
        pytest.fail(path)
    monkeypatch.setattr(mastodon.MastodonAdapter,'_request',request)
    monkeypatch.setattr(mastodon,'_read_char_limit',lambda *a,**k:500)
    report=doctor.diagnose('scope-one')
    assert '/api/v2/search?type=statuses&q=thth&limit=1' in called
    assert '/api/v1/notifications?limit=1' in called
    doctor.record_observation('scope-one',report)
    cache=doctor.read_observation('scope-one')
    search=next(p for p in cache['probes'] if p['key']=='search')
    assert search['http_status']==status
    monkeypatch.setattr(mastodon.MastodonAdapter,'_request',lambda *a,**k:pytest.fail('admin performed probe'))
    row=admin_report.answer('tokens',account='scope-one',via='http')['tokens'][0]
    assert row['missing_scopes']==missing
    assert row['scopes_source']=='probe' and row['missing_scopes_inferred'] is True
    assert row['scopes_observed_at']==jst.iso()
    assert row['default_scopes']==scopes.MASTODON_SCOPES
    assert 'write:statuses' in row['unknown_scopes']
    if status==500:assert 'read:search' in row['unknown_scopes']
    assert 'FAKE_SCOPE_SECRET' not in json.dumps(row)


def test_network_failure_is_not_permission(monkeypatch):
    adapter=mastodon.MastodonAdapter(instance='https://fixture.invalid')
    monkeypatch.setattr(adapter,'_request',lambda *a,**k:(_ for _ in ()).throw(urllib.error.URLError('offline')))
    with pytest.raises(mastodon.AdapterError) as exc:adapter._get_json('/api/v2/search','検索')
    assert exc.value.failure is None
    assert 'read:search' not in str(exc.value)


def test_add_and_token_set_scope_guidance_is_stderr(thth_root, capsys, monkeypatch):
    from thth import cli, oauth
    args=['account','add','new-masto','--media','mastodon','--project','scope-project',
          '--handle','reader','--instance','https://fixture.invalid','--by','tester','--json']
    assert cli.main(args)==0
    output=capsys.readouterr()
    assert json.loads(output.out)['account']['media']=='mastodon'
    assert all(scope in output.err for scope in scopes.MASTODON_SCOPES)
    cfg=accounts.load_account('new-masto')
    Path(cfg['token']).write_text('{}')
    assert oauth.run_token_set('new-masto',by='tester')==1
    output=capsys.readouterr()
    assert all(scope in output.err for scope in scopes.MASTODON_SCOPES)
