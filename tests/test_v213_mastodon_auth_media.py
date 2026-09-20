"""Scope-generation migration uses synthetic local clients and preserves old bytes."""
import copy
import hashlib
import json
from pathlib import Path
import secrets
import urllib.parse
import pytest
from thth import accounts,admin_log,authclients,authflow,jst,oauth,scopes
from thth.adapters import auth_mastodon as masto
from tests.test_v211_mastodon_auth import env,run,client_path,snapshot


def legacy(env):
    cfg=env['cfg'];base=env['base'];path=authclients.path_for('mastodon',base,cfg)
    metadata={'issuer':base,**{k:base+v for k,v in masto.ENDPOINTS.items()},'code_challenge_methods_supported':['S256'],
        'grant_types_supported':['authorization_code'],'response_types_supported':['code'],
        'scopes_supported':list(masto.LEGACY_SCOPES),'token_endpoint_auth_methods_supported':['client_secret_post']}
    data=dict(client_id=secrets.token_urlsafe(20),client_secret=secrets.token_urlsafe(30),instance=base,
        redirect_uri=masto.CALLBACK,scopes=list(masto.LEGACY_SCOPES),metadata=metadata,created_at=jst.iso())
    authclients.write(path,data)
    token=Path(cfg['token']);token.parent.mkdir(parents=True,exist_ok=True)
    token.write_text(json.dumps({'access_token':secrets.token_urlsafe(30),'scopes':list(masto.LEGACY_SCOPES),'scopes_source':'response'}));token.chmod(0o600)
    return path,data,token


def test_scope_generation_canonical_and_non_destructive_registration(env):
    path,data,token=legacy(env);old=path.read_bytes();previous=token.read_bytes()
    canonical=json.dumps(sorted(set(scopes.MASTODON_SCOPES)),ensure_ascii=True,separators=(',',':'))
    expected='mastodon.'+hashlib.sha256(env['base'].encode()).hexdigest()+'.'+hashlib.sha256(canonical.encode()).hexdigest()[:8]+'.env'
    assert client_path(env).name==expected and client_path(env)!=path
    assert authclients.path_for('mastodon',env['base'],env['cfg'],required_scopes=list(reversed(masto.SCOPES))+masto.SCOPES)==client_path(env)
    assert run(env)==0
    assert path.read_bytes()==old and token.read_bytes()!=previous
    assert authclients.read(client_path(env))['scopes']==scopes.MASTODON_SCOPES
    assert env['auth_queries'][0]['scope']==[' '.join(scopes.MASTODON_SCOPES)]
    assert 'write:media' in json.loads(token.read_text())['scopes']
    events=admin_log.read()[0];assert sum(e['event']=='app_set' for e in events)==1
    assert data['client_secret'] not in json.dumps(events)


@pytest.mark.parametrize('failure',['register','scope','identity'])
def test_upgrade_failure_keeps_old_client_and_token(env,failure):
    path,_,token=legacy(env);before=(path.read_bytes(),token.read_bytes())
    if failure=='register':env['behavior']['app_status']=500
    elif failure=='scope':env['behavior']['token']={'scope':' '.join(masto.LEGACY_SCOPES)}
    else:env['behavior']['me']={'acct':'someone-else'}
    assert run(env)==2
    assert (path.read_bytes(),token.read_bytes())==before


def test_legacy_pending_requires_restart_without_http_or_token_change(env):
    path,data,token=legacy(env);profile=masto.MastodonAuthProfile(data['client_id'],data['client_secret'],masto.CALLBACK,list(masto.LEGACY_SCOPES))
    profile.instance=env['base'];profile.client_path=path
    session=authflow.begin('alpha',env['cfg'],profile);before=snapshot(env['root'].parent);lines=[]
    assert oauth.run_auth('alpha',by='operator',code='irrelevant',log=lines.append,human_output=lambda _:pytest.fail('URL'))==2
    assert 'auth_restart_required' in ' '.join(lines) and env['calls']==[]
    assert snapshot(env['root'].parent)==before
    # A new-generation client does not cause that pending old session to migrate.
    masto.MastodonAuthProfile.prepare(env['cfg'],by='operator');env['calls'].clear();before=snapshot(env['root'].parent);lines=[]
    assert oauth.run_auth('alpha',by='operator',code='irrelevant',log=lines.append,human_output=lambda _:None)==2
    assert 'auth_restart_required' in ' '.join(lines) and env['calls']==[] and snapshot(env['root'].parent)==before


@pytest.mark.parametrize('state',['legacy','new','broken_new','none'])
def test_rehearse_generation_readonly_no_fallback(env,state):
    if state!='none':path,data,token=legacy(env)
    if state=='new':masto.MastodonAuthProfile.prepare(env['cfg'],by='operator')
    if state=='broken_new':authclients.write(client_path(env),{'bad':True})
    env['calls'].clear();before=snapshot(env['root'].parent)
    if state in ('none','broken_new'):
        with pytest.raises(authflow.FlowError,match='auth_client_invalid' if state=='broken_new' else 'not_registered'):
            masto.MastodonAuthProfile.prepare(env['cfg'],rehearse=True)
    else:
        profile=masto.MastodonAuthProfile.prepare(env['cfg'],rehearse=True)
        expected=masto.LEGACY_SCOPES if state=='legacy' else masto.SCOPES
        assert profile.scopes==expected
        query=urllib.parse.parse_qs(urllib.parse.urlsplit(profile.authorize({'state':secrets.token_urlsafe(20),'code_verifier':secrets.token_urlsafe(30)})).query)
        assert query['scope']==[' '.join(expected)]
    assert env['calls']==[] and snapshot(env['root'].parent)==before


@pytest.mark.parametrize('generation',['legacy','new','unknown','null'])
def test_revoke_uses_issuing_client_generation_without_destroying_shared_client(env,monkeypatch,generation):
    from thth import leave
    path,data,tokenpath=legacy(env)
    if generation=='new':assert run(env)==0
    token=json.loads(tokenpath.read_text())
    if generation in ('unknown','null'):token['client_scope_generation']=None if generation=='null' else 'not-a-known-generation'
    before=snapshot(env['root'].parent);calls=[];progress=[];saved=[]
    def request(base,route,**kw):calls.append((base,route,kw));return {}
    monkeypatch.setattr(masto,'request',request)
    if generation in ('unknown','null'):
        with pytest.raises(authflow.FlowError,match='generation_unknown'):leave.revoke(env['cfg'],token,progress,lambda:saved.append(True))
        assert not calls and not saved
    else:
        assert leave.revoke(env['cfg'],token,progress,lambda:saved.append(True))=='confirmed'
        expected=data if generation=='legacy' else authclients.read(client_path(env))
        assert calls[0][2]['data']['client_id']==expected['client_id']
        assert calls[0][2]['data']['token']==token['access_token'] and progress==['access'] and saved==[True]
    assert snapshot(env['root'].parent)==before


def test_doctor_and_admin_required_scope_observations_share_definition(env,monkeypatch):
    from thth import doctor,admin_report,adapters
    path,data,tokenpath=legacy(env)
    token=json.loads(tokenpath.read_text());token.update(user_id='123',username='demo');tokenpath.write_text(json.dumps(token));tokenpath.chmod(0o600)
    class Adapter:
        def probe(self,**kw):return []
    monkeypatch.setattr(adapters,'make_adapter',lambda *a:Adapter())
    value=doctor.diagnose('alpha')
    assert value['required_scopes']==scopes.MASTODON_SCOPES
    assert value['missing_scopes_recorded']==['write:media']
    row=admin_report.answer('tokens',account='alpha',via='http')['tokens'][0]
    assert row['default_scopes']==scopes.MASTODON_SCOPES and row['missing_scopes']==['write:media']
