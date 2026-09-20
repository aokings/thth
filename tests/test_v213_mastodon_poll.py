"""Typed-only Mastodon polls: exact wire, limits, durable final gate; no real API."""
import copy
import json
from pathlib import Path
import urllib.parse
import pytest
from thth import accounts,approval,core,graphemes,inflight,lint,media,media_delivery
from thth.adapters import base,mastodon_media as mm
from tests.test_v213_mastodon_media import wire,env,posts


def declaration(**changes):
    return {'attachments':[{'type':'poll','options':['一つ 🌱','もう一つ'],'expires_in':300,'multiple':True,'hide_totals':False,**changes}]}


@pytest.fixture
def pollenv(env,wire):
    wire['caps']={'version':'4.5.0','configuration':{'polls':{'max_options':4,'max_characters_per_option':50,'min_expiration':300,'max_expiration':86400}}}
    env[1].granted_scopes=['write:statuses']
    return env


def publish(env,fm=None,*,text='問い',veto=None):
    cfg,adapter,_=env;fm=declaration() if fm is None else fm
    manifest=media.manifest_for(fm,cfg);state=accounts.state_dir_for('alpha')
    inflight.write(state,file='fixture',started='2030-01-01T00:00:00+09:00')
    result=media_delivery.publish(adapter,base.Post(text=text),cfg=cfg,fm=fm,manifest=manifest,state_dir=state,before_publish=veto)
    return result,inflight.read(state),manifest


def test_fileless_poll_wire_scopes_and_durable_publication(pollenv,wire):
    result,journal,manifest=publish(pollenv)
    assert result.post_id=='100' and result.failure=='none'
    assert manifest['files']==[] and journal['media']['phase']=='published'
    assert journal['media']['post_id']=='100' and not posts(wire,'/api/v2/media')
    sent=posts(wire,'/api/v1/statuses');assert len(sent)==1
    form=urllib.parse.parse_qs(sent[0][2].decode())
    assert form=={'status':['問い'],'visibility':['public'],'poll[options][]':['一つ 🌱','もう一つ'],'poll[expires_in]':['300'],'poll[multiple]':['true'],'poll[hide_totals]':['false']}
    assert sent[0][3]['Idempotency-Key']
    assert not (Path(accounts.state_dir_for('alpha'))/'media_capabilities.json').exists()


@pytest.mark.parametrize('key,value,reason',[
    ('max_options',None,'poll_capability_unavailable'),('max_options',True,'poll_capability_unavailable'),
    ('max_options',1,'poll_capability_unavailable'),('max_options','4','poll_capability_unavailable'),
    ('max_characters_per_option',0,'poll_capability_unavailable'),('max_expiration',299,'poll_capability_unavailable'),
    ('max_expiration',False,'poll_capability_unavailable'),('min_expiration',301,'poll_limit_exceeded: expires_in'),
    ('max_characters_per_option',1,'poll_limit_exceeded: option_characters'),
])
def test_unknown_or_exceeded_limits_never_post(pollenv,wire,key,value,reason):
    wire['caps']['configuration']['polls'][key]=value
    result,journal,_=publish(pollenv)
    assert result.post_id is None and reason in result.error
    assert not any(c[0]=='POST' for c in wire['calls'])
    assert journal['media']['phase']=='failed'


@pytest.mark.parametrize('changes,ok',[
    ({'expires_in':299},False),({'expires_in':300},True),({'expires_in':86400},True),({'expires_in':86401},False),
    ({'options':['a','b','c','d']},True),({'options':['a','b','c','d','e']},False),
    ({'options':['e\u0301'*50,'👩🏽\u200d💻'*50]},True),({'options':['e\u0301'*51,'b']},False),
    ({'options':['a',' a ']},False),
])
def test_poll_boundaries_and_provider_graphemes(pollenv,wire,changes,ok):
    result,_,_=publish(pollenv,declaration(**changes))
    assert bool(result.post_id)==ok
    assert len(posts(wire,'/api/v1/statuses'))==int(ok)


@pytest.mark.parametrize('body',['',' ','\n\t'])
def test_poll_requires_body_before_network(pollenv,wire,body):
    result,_,_=publish(pollenv,text=body)
    assert result.error=='poll_text_required: mastodon' and result.post_id is None
    assert wire['calls']==[]


def test_typeless_lint_uses_fresh_poll_limits_without_token_or_media_caps(pollenv,wire,monkeypatch):
    def forbidden(*a,**k):raise AssertionError('lint read token')
    monkeypatch.setattr(accounts,'load_token',forbidden)
    notes=media_delivery.lint_notes(pollenv[0],declaration(),text='問い')
    assert len(notes)==1 and notes[0].startswith('warning: poll limits: latest instance')
    wire['caps']['configuration']['polls']['max_expiration']=300
    assert media_delivery.lint_notes(pollenv[0],declaration(expires_in=301),text='問い')==['poll_limit_exceeded: expires_in']
    assert not any(c[0]=='POST' for c in wire['calls'])


def test_public_v1_lint_and_empty_poll_text(pollenv,wire):
    file=pollenv[2]/'poll.md'
    head='---\nthth: 1\naccount: alpha\npublish_at: 2030-01-01T00:00:00+09:00\nstatus: draft\nattachments: '+json.dumps(declaration()['attachments'],ensure_ascii=False)+'\n---\n## mastodon\n'
    file.write_text(head+'問い\n')
    assert not [x for x in lint.lint_file(str(file)) if not lint.is_warning(x)]
    file.write_text(head+'\n')
    assert 'poll_text_required: mastodon' in lint.lint_file(str(file))


def test_poll_intent_changes_digest_and_idempotency(pollenv):
    cfg,adapter,_=pollenv;fm=declaration();original=media.manifest_for(fm,cfg)
    baseline=adapter._idempotency_key(base.Post(text='問い',media_manifest=original),'public')
    for field,value in [('options',['another','choice']),('expires_in',400),('multiple',False),('hide_totals',True)]:
        changed=media.manifest_for(declaration(**{field:value}),cfg)
        assert media.prepared_component(changed)!=media.prepared_component(original)
        assert adapter._idempotency_key(base.Post(text='問い',media_manifest=changed),'public')!=baseline


def test_fileless_final_veto_prevents_post(pollenv,wire):
    result,_,_=publish(pollenv,veto=lambda:'approval_stale')
    assert result.post_id is None and 'approval_stale' in result.error
    assert not posts(wire,'/api/v1/statuses')


@pytest.mark.parametrize('phase',['publishing','published'])
def test_fileless_durable_write_fault_never_claims_success(pollenv,wire,monkeypatch,phase):
    original=media_delivery._save
    def save(name,data):
        if data['media']['phase']==phase:raise OSError('fake fault')
        return original(name,data)
    monkeypatch.setattr(media_delivery,'_save',save)
    result,journal,_=publish(pollenv)
    assert result.post_id is None and result.failure=='media_ambiguous'
    assert journal['media']['phase']=='unknown'
    assert len(posts(wire,'/api/v1/statuses'))==int(phase=='published')


@pytest.mark.parametrize('code',[403,422,500])
def test_status_http_failure_is_bounded(pollenv,wire,code):
    wire['status']=(code,{'error':'untrusted response'})
    result,journal,_=publish(pollenv)
    assert result.post_id is None and len(posts(wire,'/api/v1/statuses'))==1
    assert result.failure==('publish_definite' if code<500 else 'media_ambiguous')
    assert journal['media']['phase']==('failed' if code<500 else 'unknown')
    if code==403:assert 'write:statuses' in result.error and 'write:media' not in result.error
    assert 'untrusted response' not in str(result)


def test_poll_and_file_rejected_before_effect(pollenv,wire):
    fm={**declaration(),'media':[{'file':'a.png','alt':'点'}]}
    with pytest.raises(media.MediaError,match='poll/media'):media.manifest_for(fm,pollenv[0])
    assert wire['calls']==[]


def test_blank_option_rejected_in_prepare(pollenv,wire):
    with pytest.raises(media.MediaError,match='invalid poll option'):
        media.manifest_for(declaration(options=[' ','b']),pollenv[0])
    assert wire['calls']==[]


@pytest.mark.parametrize('outcome',['success','stale','unknown'])
def test_actual_approval_and_poll_publication_or_no_replay(tmp_path,isolated_account_factory,wire,monkeypatch,outcome):
    from tests.conftest import init_git_pair,make_queue_text,run_git,approve_via_cli
    from thth import queuefile
    from thth.adapters.mastodon import MastodonAdapter
    import secrets
    raw=make_queue_text({'account':'alpha','status':'draft','approved_sha':'','topic':''},body='## mastodon\n\n問い。\n',media='mastodon')
    raw=raw.replace('\n---\n','\nattachments: '+json.dumps(declaration()['attachments'],ensure_ascii=False)+'\n---\n',1)
    pair=init_git_pair(tmp_path,seed_content=raw);path=Path(pair['queue_dir'])/'a.md'
    cfg=isolated_account_factory(name='alpha',repo_dir=pair['work'],media='mastodon',instance=wire['url'],production=True,hashtags=False,char_limit=500,quiet_hours=None,min_interval_hours=0)
    wire['caps']={'version':'4.5.0','configuration':{'polls':{'max_options':4,'max_characters_per_option':50,'min_expiration':300,'max_expiration':86400}}}
    approval_result=approve_via_cli(path);assert approval_result.returncode==0,approval_result.stderr
    token=secrets.token_urlsafe(32);monkeypatch.setattr(accounts,'load_token',lambda _: {'access_token':token})
    adapter=MastodonAdapter(instance=wire['url'],access_token=token);adapter.granted_scopes=['write:statuses']
    original=mm._json
    def request(*args,**kwargs):
        result=original(*args,**kwargs)
        if outcome=='stale' and args[1:3]==('GET','/api/v2/instance'):
            path.write_text(path.read_text().replace('一つ 🌱','変えた選択肢'))
        return result
    monkeypatch.setattr(mm,'_json',request)
    if outcome=='unknown':wire['status']=(500,{})
    result=core.throw_once('alpha',production_flag=True,adapter_factory=lambda *a:adapter,log=lambda _:None,bypass_pace=True)
    assert not posts(wire,'/api/v2/media')
    if outcome=='success':
        assert result.action=='post' and queuefile.parse(str(path)).get('status')=='posted'
        assert len(posts(wire,'/api/v1/statuses'))==1
    else:
        assert result.exit_code != 0
        if outcome=='stale':
            assert result.error=='approval_stale' and queuefile.parse(str(path)).get('status')=='approved'
        else:assert result.action=='inflight'
        assert len(posts(wire,'/api/v1/statuses'))==int(outcome=='unknown')
        before=len(wire['calls'])
        again=core.throw_once('alpha',production_flag=True,adapter_factory=lambda *a:adapter,log=lambda _:None,bypass_pace=True)
        if outcome=='unknown':assert again.action=='inflight'
        assert len(wire['calls'])==before and queuefile.parse(str(path)).get('status')=='approved'


def test_poll_focus_is_not_silently_dropped(pollenv,wire):
    result,_,_=publish(pollenv,{**declaration(),'post_options':{'focus':[]}})
    assert result.error=='unsupported_attachment: mastodon/poll_focus'
    assert wire['calls']==[]


def test_v2_poll_limits_apply_to_each_segment(pollenv,wire,monkeypatch):
    from thth import bundle,threadrun
    monkeypatch.setattr(threadrun,'unreadable_runs',lambda:[])
    monkeypatch.setattr(threadrun,'find_latest',lambda *a:None)
    q=pollenv[2]/'bundle.md'
    raw='---\nthth: 2\naccount: alpha\npublish_at: 2030-01-01T12:00:00+09:00\ncontinue_until: 2030-01-01T13:00:00+09:00\nstatus: draft\nposts:\n'
    raw+='  - index: 1\n    attachments: '+json.dumps(declaration()['attachments'],ensure_ascii=False)+'\n'
    raw+='  - index: 2\n    attachments: '+json.dumps(declaration(expires_in=301)['attachments'],ensure_ascii=False)+'\n'
    raw+='---\n## mastodon\n一つ目\n<!-- thth: 2/2 -->\n二つ目\n'
    q.write_text(raw)
    assert not bundle.parse(str(q)).malformed
    assert not [v for v in lint.lint_file(str(q)) if not lint.is_warning(v)]
    wire['caps']['configuration']['polls']['max_expiration']=300
    assert lint.lint_file(str(q)).count('poll_limit_exceeded: expires_in')==1
    assert not any(call[0]=='POST' for call in wire['calls'])
