"""Threads images use immutable R2 grants, ordered Graph wire, and durable phases."""
import copy
import hashlib
import http.server
import json
import secrets
import threading
import time
import urllib.parse
import pytest
from pathlib import Path
from thth import accounts,inflight,media,media_delivery,media_relay
from thth.adapters import base,threads,threads_media as tm
from tests.test_v213_media_foundation import png,jpeg
from tests.test_v213_media_formats import exif,segment


@pytest.fixture
def wire(monkeypatch):
    state={'calls':[],'images':{},'fetched':[],'poll':[],'create':None,'publish':None,'on_create':None,'on_poll':None}
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def reply(self,code,body):
            raw=json.dumps(body).encode();self.send_response(code);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            state['calls'].append(('GET',self.path,{}))
            if state['on_poll']:state['on_poll']()
            self.reply(*(state['poll'].pop(0) if state['poll'] else (200,{'status':'FINISHED'})))
        def do_POST(self):
            body=urllib.parse.parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode(),keep_blank_values=True);state['calls'].append(('POST',self.path,body))
            if self.path.endswith('/threads'):
                for key in ('image_url','video_url'):
                    if key in body:state['fetched'].append(state['images'][body[key][0]])
                if state['on_create']:state['on_create']()
                self.reply(*(state['create'] or (200,{'id':str(10+len(posts(state,'/threads')))})))
            elif self.path.endswith('/threads_publish'):self.reply(*(state['publish'] or (200,{'id':'100'})))
            else:self.reply(404,{})
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();state['url']='http://127.0.0.1:'+str(server.server_port)
    monkeypatch.setattr(tm,'POLL_INTERVAL',0)
    try:yield state
    finally:server.shutdown();server.server_close();thread.join(3)


def posts(wire,suffix):return [c for c in wire['calls'] if c[0]=='POST' and c[1].endswith(suffix)]


@pytest.fixture
def env(tmp_path,monkeypatch,wire):
    root=tmp_path.resolve()/'root';root.mkdir(mode=0o700);repo=root/'repo';repo.mkdir();(repo/'a.png').write_bytes(png())
    monkeypatch.setenv('THTH_ROOT',str(root));monkeypatch.delenv('THTH_ACCOUNTS_DIR',raising=False)
    cfg={'account':'alpha','media':'threads','repo_dir':str(repo),'production':True,'hashtags':False,'char_limit':500}
    monkeypatch.setattr(accounts,'load_account',lambda n:cfg)
    client={'upload':[],'grants':[],'results':[],'fail':None}
    class Relay:
        def __init__(self,account,actor):assert (account,actor)==('alpha','operator')
        def upload_prepared(self,item):
            row=inflight.read(accounts.state_dir_for('alpha'))['media'];assert row['phase']=='uploading'
            data=b''.join(item.chunks());assert hashlib.sha256(data).hexdigest()==item.manifest['public_sha256'];client['upload'].append(data)
            return secrets.token_urlsafe(32)
        def grant(self,item,source):
            url='https://media.invalid/m/'+secrets.token_urlsafe(32);wire['images'][url]=client['upload'][-1]
            value={'url':url,'expires_at':int(time.time()*1000)+(1800000 if item.manifest['kind']=='video' else 600000)};client['grants'].append(value);return value
        def result(self,grant,*,published):
            client['results'].append(published)
            if client['fail']:raise media_relay.MediaRelayError('media_relay_outcome_unknown')
            return {'status':'acknowledged' if published else 'retired'}
    monkeypatch.setattr(media_relay,'MediaRelay',Relay)
    adapter=threads.ThreadsAdapter(base_url=wire['url'],access_token=secrets.token_urlsafe(30),user_id='123',wait_seconds=0)
    return cfg,adapter,repo,client


def invoke(env,fm=None,veto=None,post=None,on_container=None):
    cfg,adapter,repo,_=env;fm=fm or {'media':[{'file':'a.png','alt':'赤い点 🌿'}]};manifest=media.manifest_for(fm,cfg)
    state=accounts.state_dir_for('alpha');inflight.write(state,file='fixture',started='2030-01-01T00:00:00+09:00')
    result=media_delivery.publish(adapter,post or base.Post('本文'),cfg=cfg,fm=fm,manifest=manifest,state_dir=state,before_publish=veto,on_container_created=on_container)
    return result,inflight.read(state),manifest


def test_single_and_carousel_same_public_bytes_alt_order_and_options(env,wire):
    raw=jpeg();raw=raw[:2]+segment(0xe1,b'Exif\0\0'+exif())+segment(0xfe,b'PRIVATE')+raw[2:];(env[2]/'b.jpg').write_bytes(raw)
    fm={'media':[{'file':'b.jpg','alt':'回転した画像'},{'file':'a.png','alt':'点'}]}
    result,journal,manifest=invoke(env,fm,post=base.Post('本文',reply_to='777',topic='話題',location_id='888',share_to_instagram=True))
    assert result.post_id=='100' and journal['media']['phase']=='published'
    assert env[3]['results']==[True,True] and wire['fetched']==env[3]['upload']
    assert wire['fetched'][0]!=raw and b'PRIVATE' not in wire['fetched'][0]
    create=posts(wire,'/threads');assert len(create)==3
    assert [c[2]['alt_text'][0] for c in create[:2]]==['回転した画像','点']
    assert all(c[2]['is_carousel_item']==['true'] for c in create[:2])
    assert create[2][2]['children']==['11,12'] and create[2][2]['media_type']==['CAROUSEL']
    assert create[2][2]['reply_to_id']==['777'] and create[2][2]['topic_tag']==['話題'] and create[2][2]['location_id']==['888'] and create[2][2]['crossreshare_to_ig']==['true']
    assert len(posts(wire,'/threads_publish'))==1
    serialized=json.dumps(journal,ensure_ascii=False)
    assert all(g['url'] not in serialized for g in env[3]['grants']) and env[1].access_token not in serialized


def test_single_empty_text_and_scaling_note(env,wire):
    result,_,_=invoke(env,post=base.Post(''));assert result.post_id=='100'
    create=posts(wire,'/threads');assert len(create)==1 and create[0][2]['text']==['']
    assert create[0][2]['media_type']==['IMAGE'] and 'is_carousel_item' not in create[0][2]
    assert any('scales' in n for n in media_delivery.lint_notes(env[0],{'media':[{'file':'a.png','alt':'dot'}]}))


@pytest.mark.parametrize('key,value,ok',[('public_size',8000000,True),('public_size',8000001,False),('width',10,True),('width',11,False),('height',10,True),('height',11,False),('width',None,False),('format','gif',False),('format','webp',False)])
def test_image_limits_preflight(env,key,value,ok):
    row=media.manifest_for({'media':[{'file':'a.png','alt':'dot'}]},env[0]);row['files'][0][key]=value
    assert (tm.intent_error(row) is None)==ok


@pytest.mark.parametrize('n,ok',[(1,True),(2,True),(20,True),(21,False)])
def test_carousel_count_limits(env,n,ok):
    row=media.manifest_for({'media':[{'file':'a.png','alt':'dot'}]},env[0]);row['files']*=n
    assert (tm.intent_error(row) is None)==ok


@pytest.mark.parametrize('status',['ERROR','EXPIRED','PUBLISHED','other'])
def test_processing_failure_holds_ids_and_retires_provider_grant(env,wire,status):
    wire['poll']=[(200,{'status':status})];result,journal,_=invoke(env)
    assert result.failure=='media_held' and journal['media']['remote_ids']==['11']
    assert not posts(wire,'/threads_publish') and env[3]['results']==[False]


def test_waiting_then_finished_is_only_publication(env,wire):
    wire['poll']=[(200,{'status':'IN_PROGRESS'}),(200,{'status':'FINISHED'})]
    result,_,_=invoke(env);assert result.post_id=='100' and len([c for c in wire['calls'] if c[0]=='GET'])==2


def test_source_change_final_veto_holds_and_never_publishes(env,wire):
    wire['on_create']=lambda:(env[2]/'a.png').write_bytes(png()+b'changed')
    result,journal,_=invoke(env);assert result.failure=='media_held' and journal['media']['phase']=='held'
    assert not posts(wire,'/threads_publish') and env[3]['results']==[False]


def test_source_change_after_ready_callback_final_veto(env,wire):
    # Earlier polling vetoes have all passed. The persisted container callback
    # races a source replacement immediately before the final POST gate.
    result,journal,_=invoke(env,on_container=lambda _: (env[2]/'a.png').write_bytes(png()+b'changed'))
    assert result.failure=='media_held' and journal['media']['phase']=='held'
    assert not posts(wire,'/threads_publish') and env[3]['results']==[False]


@pytest.mark.parametrize('stage,code,expected',[('create',400,'media_held'),('create',500,'media_ambiguous'),('publish',400,'media_held'),('publish',500,'media_ambiguous')])
def test_definite_failure_invalidates_but_unknown_never_renews(env,wire,stage,code,expected):
    wire[stage]=(code,{'error':'provider error'});result,journal,_=invoke(env)
    assert result.failure==expected and journal['media']['phase']==('held' if expected=='media_held' else 'unknown')
    assert env[3]['results']==([False] if expected=='media_held' else [])


def test_poll_late_response_no_publication(env,wire,monkeypatch):
    clock=[0.0];monkeypatch.setattr(tm.time,'monotonic',lambda:clock[0]);monkeypatch.setattr(tm,'POLL_SECONDS',1)
    wire['on_poll']=lambda:clock.__setitem__(0,2.0)
    result,_,_=invoke(env);assert result.failure=='media_held' and result.error=='media_processing_timeout'
    assert not posts(wire,'/threads_publish')


def test_ack_failure_cannot_erase_known_publication_or_retry(env,wire):
    env[3]['fail']=True;result,journal,_=invoke(env)
    assert result.post_id=='100' and journal['media']['publication_ack']=='unconfirmed'
    assert env[3]['results']==[True] and len(posts(wire,'/threads_publish'))==1


def test_publication_record_failure_is_unknown(env,wire,monkeypatch):
    original=media_delivery._progress
    def fail(name,manifest,origin,phase,**kw):
        if phase=='published':raise OSError('synthetic fsync')
        return original(name,manifest,origin,phase,**kw)
    monkeypatch.setattr(media_delivery,'_progress',fail)
    result,journal,_=invoke(env);assert result.failure=='media_ambiguous' and journal['media']['phase']=='unknown'
    assert env[3]['results']==[] and len(posts(wire,'/threads_publish'))==1


@pytest.mark.parametrize('phase',['prepared','uploading','granting','creating','processing','ready','publishing','published'])
def test_journal_fault_preserves_intent_and_restart_never_reposts(env,wire,monkeypatch,phase):
    from thth import core
    monkeypatch.setattr(accounts,'load_token',lambda cfg:{'access_token':env[1].access_token,'user_id':'123'})
    monkeypatch.setattr(core,'_append_run',lambda *a,**k:None)
    original=media_delivery._save;hit=[]
    def fail(name,data):
        if data['media']['phase']==phase:hit.append(phase);raise OSError('synthetic persistence failure')
        return original(name,data)
    monkeypatch.setattr(media_delivery,'_save',fail)
    result,_,_=invoke(env);assert hit and not result.post_id
    assert len(posts(wire,'/threads_publish'))==int(phase=='published')
    count=len(wire['calls']);uploads=len(env[3]['upload'])
    again=core.send_once('alpha',text='',media_rows=[{'file':'a.png','alt':'dot'}],production_flag=True,confirm='unused',adapter_factory=lambda *a:env[1],log=lambda _:None)
    assert again.action=='inflight' and len(wire['calls'])==count and len(env[3]['upload'])==uploads


def test_two_stage_cli_media_confirm_and_compact_receipt(env,wire,monkeypatch,capsys):
    from thth import core,cli,read_coordination,sent
    monkeypatch.setattr(accounts,'load_token',lambda cfg:{'access_token':env[1].access_token,'user_id':'123'})
    monkeypatch.setattr(core,'_append_run',lambda *a,**k:None)
    monkeypatch.setattr(core,'_default_adapter_factory',lambda *a:env[1])
    monkeypatch.setattr(read_coordination,'invoke',lambda args,name,fn=None:fn() if fn else args.func(args))
    argv=['send','alpha','--media','a.png','--alt','点']
    assert cli.main(argv)==0;shown=capsys.readouterr().out
    digest=next(line.split(': ',1)[1] for line in shown.splitlines() if line.startswith('digest: '))
    assert not wire['calls'] and not env[3]['upload']
    assert cli.main(argv+['--production','--confirm',digest])==0
    receipt=sent.read(accounts.state_dir_for('alpha'),'100')
    assert receipt['media']==[{'sha256':hashlib.sha256(png()).hexdigest(),'kind':'image','alt_present':True,'remote_id':'11'}]
    assert all(g['url'] not in json.dumps(receipt) for g in env[3]['grants'])


def test_unsupported_intent_is_not_silently_discarded(env,wire):
    fm={'media':[{'file':'a.png','alt':'dot'}],'post_options':{'reply_control':'everyone'}}
    result,_,_=invoke(env,fm);assert result.error=='unsupported_attachment: threads/image_post_options'
    assert not wire['calls'] and not env[3]['upload']


@pytest.mark.parametrize('nth',[1,2])
@pytest.mark.parametrize('ack_failed',[False,True])
def test_ack_detail_save_failure_preserves_durable_publication(env,wire,monkeypatch,nth,ack_failed):
    from thth import core
    original=media_delivery._save;published_attempts=[];persisted=[]
    def fail(name,data):
        detail=data.get('media',{})
        if detail.get('phase')=='published':
            published_attempts.append(detail['publication_ack'])
            if len(published_attempts)==nth:raise OSError('synthetic detail save failure')
        result=original(name,data);persisted.append(copy.deepcopy(detail));return result
    monkeypatch.setattr(media_delivery,'_save',fail)
    env[3]['fail']=ack_failed
    result,journal,_=invoke(env)
    assert len(posts(wire,'/threads_publish'))==1
    if nth==1:
        assert result.post_id is None and result.failure=='media_ambiguous'
        assert journal['media']['phase']=='unknown' and env[3]['results']==[]
        assert not any(row.get('phase')=='published' for row in persisted)
    else:
        assert result.post_id=='100' and result.failure=='none'
        assert journal['media']['phase']=='published' and journal['media']['post_id']=='100'
        assert journal['media']['publication_ack']=='pending'
        assert any(row.get('phase')=='published' and row.get('publication_ack')=='pending' for row in persisted)
        assert published_attempts==['pending','unconfirmed' if ack_failed else 'acknowledged']
        assert env[3]['results']==[True]  # Exactly one ACK attempt, never renewal.
    before=(len(wire['calls']),len(env[3]['upload']),len(env[3]['grants']),len(env[3]['results']))
    monkeypatch.setattr(accounts,'load_token',lambda cfg:{'access_token':env[1].access_token,'user_id':'123'})
    monkeypatch.setattr(core,'_append_run',lambda *a,**k:None)
    again=core.send_once('alpha',text='',media_rows=[{'file':'a.png','alt':'dot'}],production_flag=True,confirm='unused',adapter_factory=lambda *a:pytest.fail('restart provider entered'),log=lambda _:None)
    assert again.action=='inflight'
    assert before==(len(wire['calls']),len(env[3]['upload']),len(env[3]['grants']),len(env[3]['results']))
