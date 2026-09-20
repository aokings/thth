"""Video-service/PDS separate origins, optimized blobs and no blind POST retry."""
import copy
import hashlib
import http.server
import json
import secrets
import threading
import urllib.parse
import pytest
from thth import accounts,core,inflight,media,media_delivery
from thth.adapters import base,bluesky,bluesky_media,bluesky_video as bv
from tests.test_v213_media_formats import mp4

class Wire(dict):
    def __repr__(self):return '<synthetic video/PDS wire; credentials omitted>'


DID='did:plc:synthetic'
PDS_DOC={'id':DID,'service':[{'id':'#atproto_pds','type':'AtprotoPersonalDataServer','serviceEndpoint':'https://pds.example.invalid'}]}


@pytest.fixture
def wire(monkeypatch):
    state=Wire({'calls':[],'access':secrets.token_urlsafe(30),'password':secrets.token_urlsafe(30),'tokens':{},'limits':{'canUpload':True},
           'jobs':[{'jobId':'fixture-job','did':DID,'state':'JOB_STATE_COMPLETED'}],'upload_status':200,'wrapped':True,'records':[]})
    optimized=b'optimized fixture bytes';state['blob']={'$type':'blob','ref':{'$link':bluesky_media.blob_cid(hashlib.sha256(optimized).hexdigest())},'size':len(optimized),'mimeType':'video/mp4'}
    def handler(role):
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def send(self,status,value):
                raw=json.dumps(value).encode();self.send_response(status);self.send_header('Content-Length',str(len(raw)));self.end_headers()
                try:self.wfile.write(raw)
                except (BrokenPipeError,ConnectionResetError):pass
            def capture(self,raw=b''):
                parts=urllib.parse.urlsplit(self.path);nsid=parts.path.rsplit('/',1)[-1];query=urllib.parse.parse_qs(parts.query)
                state['calls'].append({'role':role,'nsid':nsid,'query':query,'body':raw,'headers':dict(self.headers)})
                return nsid,query
            def job(self):
                value=copy.deepcopy(state['jobs'][0])
                if len(state['jobs'])>1:state['jobs'].pop(0)
                if value.get('state')=='JOB_STATE_COMPLETED' or value.get('error')=='already_exists':value.setdefault('blob',state['blob'])
                return {'jobStatus':value} if state['wrapped'] else value
            def do_GET(self):
                nsid,query=self.capture()
                if nsid=='com.atproto.server.getServiceAuth':
                    token=secrets.token_urlsafe(40);state['tokens'][query['lxm'][0]]=token
                    return self.send(200,{'token':token})
                if nsid=='app.bsky.video.getUploadLimits':return self.send(200,state['limits'])
                if nsid=='app.bsky.video.getJobStatus':
                    if state.get('after_poll'):state['after_poll']()
                    return self.send(state.get('poll_status',200),self.job())
                return self.send(404,{})
            def do_POST(self):
                raw=self.rfile.read(int(self.headers['Content-Length']));nsid,query=self.capture(raw)
                if nsid=='com.atproto.server.createSession':return self.send(200,{'accessJwt':state['access'],'refreshJwt':secrets.token_urlsafe(30),'did':DID,'handle':'demo.test','didDoc':state.get('doc',PDS_DOC)})
                if nsid=='app.bsky.video.uploadVideo':
                    if state.get('redirect'):
                        self.send_response(307);self.send_header('Location','/xrpc/must-not-follow');self.end_headers();return
                    if state.get('disconnect'):
                        self.close_connection=True;return
                    if state.get('after_upload'):state['after_upload']()
                    return self.send(state['upload_status'],self.job())
                if nsid=='com.atproto.repo.uploadBlob':
                    if state.get('caption_disconnect'):
                        self.close_connection=True;return
                    return self.send(200,{'blob':{'$type':'blob','ref':{'$link':bluesky_media.blob_cid(hashlib.sha256(raw).hexdigest())},'size':len(raw),'mimeType':'text/vtt'}})
                if nsid=='com.atproto.repo.createRecord':
                    state['records'].append(json.loads(raw))
                    if state.get('record_disconnect'):
                        self.close_connection=True;return
                    return self.send(200,{'uri':'at://'+DID+'/app.bsky.feed.post/created'})
                return self.send(404,{})
        return Handler
    servers=[];threads=[]
    for role in ('pds','video'):
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),handler(role));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        servers.append(server);threads.append(thread);state[role+'_url']='http://127.0.0.1:'+str(server.server_port)
    monkeypatch.setenv('THTH_TEST_BLUESKY_VIDEO_URL',state['video_url'])
    try:yield state
    finally:
        for s in servers:s.shutdown();s.server_close()
        for t in threads:t.join(3)


@pytest.fixture
def env(tmp_path,monkeypatch,wire):
    root=tmp_path.resolve()/'root';root.mkdir(mode=0o700);repo=root/'repo';repo.mkdir();(repo/'v.mp4').write_bytes(mp4())
    cfg={'account':'alpha','media':'bluesky','service':wire['pds_url'],'repo_dir':str(repo),'production':True,'hashtags':False,'char_limit':300}
    monkeypatch.setenv('THTH_ROOT',str(root));monkeypatch.delenv('THTH_ACCOUNTS_DIR',raising=False)
    monkeypatch.setattr(accounts,'load_account',lambda _:cfg);monkeypatch.setattr(accounts,'load_token',lambda _:{'identifier':'demo.test','app_password':wire['password']})
    monkeypatch.setattr(core,'_append_run',lambda *a,**kw:None)
    adapter=bluesky.BlueskyAdapter(service=wire['pds_url'],identifier='demo.test',app_password=wire['password'])
    return cfg,adapter,repo


def calls(wire,nsid):return [c for c in wire['calls'] if c['nsid']==nsid]


def invoke(env,fm=None,veto=None):
    cfg,adapter,repo=env;fm=fm or {'media':[{'file':'v.mp4','alt':'動画 🌿'}]};manifest=media.manifest_for(fm,cfg);state=accounts.state_dir_for('alpha')
    inflight.write(state,file='fixture',started='2030-01-01T00:00:00+09:00')
    result=media_delivery.publish(adapter,base.Post('body'),cfg=cfg,fm=fm,manifest=manifest,state_dir=state,before_publish=veto)
    return result,inflight.read(state),manifest


@pytest.mark.parametrize('wrapped',[True,False])
def test_processed_blob_differs_from_approved_input_and_tokens_are_separate(env,wire,wrapped):
    wire['wrapped']=wrapped
    result,journal,manifest=invoke(env)
    assert result.post_id and result.failure=='none' and journal['media']['phase']=='published'
    upload=calls(wire,'app.bsky.video.uploadVideo');assert len(upload)==1
    row=manifest['files'][0]
    assert upload[0]['body']==mp4() and hashlib.sha256(upload[0]['body']).hexdigest()==row['public_sha256']
    assert upload[0]['headers']['Content-Type']=='video/mp4' and int(upload[0]['headers']['Content-Length'])==row['public_size']
    grants=calls(wire,'com.atproto.server.getServiceAuth');assert len(grants)==2
    assert grants[0]['query']['aud']==['did:web:video.bsky.app'] and grants[0]['query']['lxm']==['app.bsky.video.getUploadLimits']
    assert grants[1]['query']['aud']==['did:web:pds.example.invalid'] and grants[1]['query']['lxm']==['com.atproto.repo.uploadBlob']
    assert all(c['headers']['Authorization']=='Bearer '+wire['access'] for c in grants)
    assert all(c['query']['exp'][0].isdigit() and 1790<=int(c['query']['exp'][0])-int(bv.time.time())<=1800 for c in grants)
    assert upload[0]['headers']['Authorization']=='Bearer '+wire['tokens']['com.atproto.repo.uploadBlob']
    assert calls(wire,'app.bsky.video.getUploadLimits')[0]['headers']['Authorization']=='Bearer '+wire['tokens']['app.bsky.video.getUploadLimits']
    embed=wire['records'][0]['record']['embed'];assert embed['video']==wire['blob'] and embed['alt']=='動画 🌿' and embed['aspectRatio']=={'width':640,'height':480}
    assert embed['video']['ref']['$link']!=bluesky_media.blob_cid(row['public_sha256'])
    assert result.media==[{'sha256':row['public_sha256'],'kind':'video','alt_present':True,'remote_id':wire['blob']['ref']['$link']}]
    assert journal['media']['video_job_id']=='fixture-job' and journal['media']['video_did']==DID
    for value in [wire['access'],wire['password'],*wire['tokens'].values()]:assert value not in json.dumps(journal) and value not in repr(result)


@pytest.mark.parametrize('field,value,reason',[
    ('public_size',300000001,'video_bytes'),('public_size',300000000,None),('duration',600,None),('duration',600.001,'video_duration'),('duration',None,'duration_unavailable'),('duration',0,'video_duration'),('width',None,'dimensions'),('format','mov','bluesky/mov'),
])
def test_video_preflight_limits_do_not_use_image_limits(env,field,value,reason):
    m=media.manifest_for({'media':[{'file':'v.mp4','alt':'v'}]},env[0]);m['files'][0][field]=value
    error=bluesky_media.intent_error(m)
    assert error is None if reason is None else reason in error


@pytest.mark.parametrize('limits,reason',[
    ({'canUpload':False},'video_upload_not_allowed'),({},'video_limits_unavailable'),({'canUpload':1},'video_limits_unavailable'),
    ({'canUpload':True,'remainingDailyVideos':0},'video_upload_limit_exceeded'),({'canUpload':True,'remainingDailyBytes':1},'video_upload_limit_exceeded'),
    ({'canUpload':True,'remainingDailyVideos':False},'video_limits_unavailable'),({'canUpload':True,'remainingDailyBytes':-1},'video_limits_unavailable'),
])
def test_limits_refuse_before_video_post(env,wire,limits,reason):
    wire['limits']=limits;result,journal,_=invoke(env)
    assert result.error==reason and result.failure=='publish_definite'
    assert not calls(wire,'app.bsky.video.uploadVideo') and not wire['records']


def test_processing_unknown_state_then_complete_tokenless_get(env,wire,monkeypatch):
    wire['jobs']=[{'jobId':'fixture-job','did':DID,'state':'FUTURE_STATE'}, {'jobId':'fixture-job','did':DID,'state':'JOB_STATE_COMPLETED'}]
    monkeypatch.setattr(bv,'POLL_INTERVAL',0)
    result,_,_=invoke(env);assert result.post_id
    polls=calls(wire,'app.bsky.video.getJobStatus');assert len(polls)==1 and 'Authorization' not in polls[0]['headers']


@pytest.mark.parametrize('field,value',[('did','did:plc:other'),('jobId',[]),('state',None)])
def test_bad_upload_job_never_creates_record(env,wire,field,value):
    wire['jobs'][0][field]=value;result,journal,_=invoke(env)
    assert result.error and not result.post_id and not wire['records'] and journal['media']['phase']=='unknown'


def test_processing_changed_job_binding_refuses(env,wire,monkeypatch):
    wire['jobs']=[{'jobId':'fixture-job','did':DID,'state':'PENDING'},{'jobId':'different','did':DID,'state':'JOB_STATE_COMPLETED'}]
    monkeypatch.setattr(bv,'POLL_INTERVAL',0);result,journal,_=invoke(env)
    assert result.error=='video_job_identity_mismatch' and result.failure=='media_held' and journal['media']['video_job_id']=='fixture-job' and not wire['records']


@pytest.mark.parametrize('valid',[True,False])
def test_already_exists_requires_bound_job_and_valid_blob(env,wire,valid):
    wire['wrapped']=False;wire['upload_status']=409;wire['jobs'][0]['error']='already_exists'
    if not valid:wire['blob']['mimeType']='image/png'
    result,_,_=invoke(env);assert bool(result.post_id)==valid
    assert len(calls(wire,'app.bsky.video.uploadVideo'))==1


@pytest.mark.parametrize('phase',['upload','caption'])
def test_post_response_loss_remains_unknown_and_restart_never_posts(env,wire,phase):
    fm=None
    if phase=='upload':wire['disconnect']=True
    else:
        wire['caption_disconnect']=True;(env[2]/'c.vtt').write_text('WEBVTT\n\n')
        fm={'media':[{'file':'v.mp4','alt':'v'}],'captions':[{'media_index':1,'file':'c.vtt','lang':'ja'}]}
    result,journal,_=invoke(env,fm);assert result.failure=='media_ambiguous' and journal['media']['phase']=='unknown' and not wire['records']
    before=len(wire['calls']);again=core.send_once('alpha',text='body',production_flag=True,confirm='irrelevant',adapter_factory=lambda *a:env[1],log=lambda _:None,media_rows=[{'file':'v.mp4','alt':'動画 🌿'}])
    assert again.action=='inflight' and len(wire['calls'])==before


def test_caption_bytes_language_presentation_and_order(env,wire):
    fm={'media':[{'file':'v.mp4','alt':'v'}],'captions':[],'post_options':{'presentation':'future-hint'}}
    for i,lang in enumerate(['ja','en']):
        name=f'c{i}.vtt';(env[2]/name).write_text('WEBVTT\n\n'+str(i));fm['captions'].append({'media_index':1,'file':name,'lang':lang})
    result,_,manifest=invoke(env,fm);assert result.post_id
    embed=wire['records'][0]['record']['embed'];assert embed['presentation']=='future-hint' and [c['lang'] for c in embed['captions']]==['ja','en']
    uploaded=calls(wire,'com.atproto.repo.uploadBlob');assert len(uploaded)==2
    for c,row in zip(uploaded,manifest['files'][1:]):assert hashlib.sha256(c['body']).hexdigest()==row['public_sha256']


def test_source_final_veto_holds_known_job(env,wire):
    result,journal,_=invoke(env,veto=lambda:'approval_stale' if calls(wire,'app.bsky.video.uploadVideo') else None)
    assert result.error=='approval_stale' and result.failure=='media_held' and not wire['records']
    assert journal['media']['video_job_id']=='fixture-job'


@pytest.mark.parametrize('where',['sleep','get'])
def test_deadline_after_wait_or_response_never_publishes(env,wire,monkeypatch,where):
    clock=[0.0];wire['jobs']=[{'jobId':'fixture-job','did':DID,'state':'PENDING'}, {'jobId':'fixture-job','did':DID,'state':'JOB_STATE_COMPLETED'}]
    monkeypatch.setattr(bv,'POLL_SECONDS',1.0);monkeypatch.setattr(bv.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(bv.time,'sleep',lambda _:clock.__setitem__(0,1.0 if where=='sleep' else .4))
    if where=='get':wire['after_poll']=lambda:clock.__setitem__(0,1.0)
    original=bv._request;timeouts=[]
    def request(url,**kwargs):
        if 'getJobStatus' in url:timeouts.append(kwargs['timeout'])
        return original(url,**kwargs)
    monkeypatch.setattr(bv,'_request',request)
    result,journal,_=invoke(env)
    assert result.failure=='media_held' and result.error=='video_processing_timeout'
    assert journal['media']['video_job_id']=='fixture-job' and not wire['records']
    assert timeouts==([] if where=='sleep' else [.6])


@pytest.mark.parametrize('endpoint',['http://pds.example.invalid','https://u:p@pds.example.invalid','https://pds.example.invalid:bad','https://pds.example.invalid/path','https://pds.example.invalid?q=1','file:///tmp/no','https://'])
def test_pds_document_never_selects_unsafe_authority(endpoint):
    doc=copy.deepcopy(PDS_DOC);doc['service'][0]['serviceEndpoint']=endpoint
    with pytest.raises(media.MediaError,match='video_pds_unavailable'):bv.pds_audience({'did':DID,'didDoc':doc})


def test_pds_first_matching_service_and_document_identity():
    doc=copy.deepcopy(PDS_DOC);doc['service'].append({**doc['service'][0],'serviceEndpoint':'https://later.invalid'})
    assert bv.pds_audience({'did':DID,'didDoc':doc})=='did:web:pds.example.invalid'
    doc['service'][0]['id']=DID+'#atproto_pds';assert bv.pds_audience({'did':DID,'didDoc':doc})=='did:web:pds.example.invalid'
    doc['id']='did:plc:other'
    with pytest.raises(media.MediaError):bv.pds_audience({'did':DID,'didDoc':doc})


@pytest.mark.parametrize('value',[None,{},[],{'id':DID,'service':[]}])
def test_missing_pds_document_stops_before_service_auth(env,wire,value):
    wire['doc']=value;result,_,_=invoke(env)
    assert result.failure=='publish_definite' and not calls(wire,'com.atproto.server.getServiceAuth') and not calls(wire,'app.bsky.video.uploadVideo')


@pytest.mark.parametrize('field,value',[('mimeType','image/png'),('size',0),('size',True),('size',300000001),('ref',{'$link':'not-a-cid'}),('$type','other')])
def test_blob_validation_holds_job_without_publishing(env,wire,field,value):
    wire['blob'][field]=value;result,journal,_=invoke(env)
    assert result.error=='video_blob_invalid' and result.failure=='media_held' and not wire['records'] and journal['media']['video_job_id']=='fixture-job'


def test_failed_job_does_not_echo_arbitrary_message(env,wire):
    secret=secrets.token_urlsafe(24);wire['jobs'][0].update(state='JOB_STATE_FAILED',error=secret,message=secret)
    result,journal,_=invoke(env)
    assert result.error=='video_processing_failed' and result.failure=='media_held' and not wire['records']
    assert secret not in repr(result) and secret not in json.dumps(journal)


@pytest.mark.parametrize('phase',['uploading','processing','ready','publishing','published'])
def test_durable_journal_fault_never_claims_saved_publication(env,wire,monkeypatch,phase):
    real=media_delivery._save;fired=[]
    def save(name,data):
        if data.get('media',{}).get('phase')==phase and not fired:
            fired.append(phase);raise OSError('synthetic fault')
        return real(name,data)
    monkeypatch.setattr(media_delivery,'_save',save)
    result,journal,_=invoke(env)
    assert fired and not result.post_id and result.failure=='media_ambiguous'
    assert journal['media']['phase']=='unknown'
    assert len(wire['records'])==(1 if phase=='published' else 0)
    assert len(calls(wire,'app.bsky.video.uploadVideo'))==(0 if phase=='uploading' else 1)


@pytest.mark.parametrize('count,size,reason',[(20,20000,None),(21,20,'caption_count'),(1,20001,'caption_bytes')])
def test_caption_lexicon_boundaries(count,size,reason):
    row={'role':'media','kind':'video','format':'mp4','public_size':100,'duration':1,'width':640,'height':480}
    m={'attachments':[],'post_options':{},'files':[row]+[{'role':'caption','format':'vtt','public_size':size} for _ in range(count)],'captions':[{'media_index':1} for _ in range(count)]}
    error=bv.intent_error(m);assert error is None if reason is None else reason in error


@pytest.mark.parametrize('token',[None,'','bad\ntoken',[],42])
def test_service_token_shape_refuses_without_echo(monkeypatch,token):
    from types import SimpleNamespace
    adapter=SimpleNamespace(service='https://pds.invalid',timeout=2,session=lambda:{'accessJwt':secrets.token_urlsafe(20)})
    monkeypatch.setattr(bv,'_request',lambda *a,**k:{'token':token})
    with pytest.raises(media.MediaError,match='^video_service_auth_invalid$'):bv._service_token(adapter,'did:web:pds.invalid','com.atproto.repo.uploadBlob')


@pytest.mark.parametrize('failure,expected,reason',[
    ('refused','publish_definite','media_connection_refused'),('endpoint','publish_definite','media_endpoint_rejected'),
    ('redirect','publish_definite','media_redirect_refused'),('reset','media_ambiguous','video_uploading_failed'),('timeout','media_ambiguous','video_uploading_failed'),
])
def test_transport_failure_classification_never_retries(env,wire,monkeypatch,failure,expected,reason):
    import errno,io,urllib.error
    from thth import httpsafe
    real=bv._request;attempts=[]
    def request(url,**kwargs):
        if 'uploadVideo' not in url:return real(url,**kwargs)
        attempts.append(url)
        if failure=='refused':raise urllib.error.URLError(ConnectionRefusedError(errno.ECONNREFUSED,'fake'))
        if failure=='endpoint':raise httpsafe.EndpointRejected('endpoint_invalid')
        if failure=='redirect':raise httpsafe.RedirectBlocked(url,307,'no follow',{},io.BytesIO())
        if failure=='reset':raise urllib.error.URLError(ConnectionResetError(errno.ECONNRESET,'fake'))
        raise urllib.error.URLError(TimeoutError('fake'))
    monkeypatch.setattr(bv,'_request',request)
    result,_,_=invoke(env)
    assert result.failure==expected and result.error==reason and len(attempts)==1 and not wire['records']


def test_cli_two_stage_video_empty_body_and_compact_sent(env,wire,monkeypatch,capsys):
    from thth import cli,read_coordination,sent
    monkeypatch.setattr(core,'_default_adapter_factory',lambda *a:env[1]);monkeypatch.setattr(read_coordination,'invoke',lambda args,name,fn=None:fn() if fn else args.func(args))
    argv=['send','alpha','--media','v.mp4','--alt','動画説明']
    assert cli.main(argv)==0;shown=capsys.readouterr().out
    digest=next(s.split(': ',1)[1] for s in shown.splitlines() if s.startswith('digest: '))
    assert not wire['calls'] and '2.5秒' in shown and 'public SHA256:' in shown
    assert cli.main(argv+['--production','--confirm',digest])==0
    output=capsys.readouterr().out;assert wire['records'][0]['record']['text']==''
    stored=sent.read(accounts.state_dir_for('alpha'),'at://'+DID+'/app.bsky.feed.post/created')
    assert stored['media']==[{'sha256':hashlib.sha256(mp4()).hexdigest(),'kind':'video','alt_present':True,'remote_id':wire['blob']['ref']['$link']}]
    assert inflight.read(accounts.state_dir_for('alpha')) is None
    for value in [wire['access'],wire['password'],*wire['tokens'].values()]:assert value not in output


@pytest.mark.parametrize('drift',['none','body','revoke'])
def test_git_approved_video_final_gate(tmp_path,isolated_account_factory,wire,monkeypatch,drift):
    from pathlib import Path
    from tests.conftest import init_git_pair,make_queue_text,run_git,approve_via_cli
    from thth import queuefile
    raw=make_queue_text({'account':'alpha','status':'draft','approved_sha':'','topic':''},body='## bluesky\n\n本文。\n',media='bluesky')
    raw=raw.replace('\n---\n','\nmedia:\n  - file: v.mp4\n    alt: 動画\n---\n',1)
    pair=init_git_pair(tmp_path,seed_content=raw);repo=Path(pair['work']);path=Path(pair['queue_dir'])/'a.md';(repo/'v.mp4').write_bytes(mp4())
    run_git(str(repo),['add','.']);run_git(str(repo),['commit','-m','synthetic']);run_git(str(repo),['push'])
    isolated_account_factory(name='alpha',repo_dir=str(repo),media='bluesky',service=wire['pds_url'],production=True,hashtags=False,char_limit=300,quiet_hours=None,min_interval_hours=0)
    approved=approve_via_cli(path);assert approved.returncode==0,approved.stderr
    monkeypatch.setattr(accounts,'load_token',lambda _:{'identifier':'demo.test','app_password':wire['password']})
    adapter=bluesky.BlueskyAdapter(service=wire['pds_url'],identifier='demo.test',app_password=wire['password'])
    def mutate():
        if drift!='none':
            text=path.read_text();text=text.replace('status: approved','status: draft') if drift=='revoke' else text.replace('本文。','変更本文。');path.write_text(text)
    wire['after_upload']=mutate
    result=core.throw_once('alpha',production_flag=True,adapter_factory=lambda *a:adapter,log=lambda _:None,bypass_pace=True)
    if drift=='none':assert result.action=='post' and queuefile.parse(str(path)).get('status')=='posted'
    else:assert result.action=='inflight' and not wire['records']


def test_publication_saved_then_source_exit_drift_keeps_result(env,wire,monkeypatch):
    real=media_delivery._save
    def save(name,data):
        real(name,data)
        if data.get('media',{}).get('phase')=='published':(env[2]/'v.mp4').write_bytes(mp4()+b'changed')
    monkeypatch.setattr(media_delivery,'_save',save)
    result,journal,_=invoke(env)
    assert result.post_id and result.failure=='none' and journal['media']['phase']=='published'
    assert len(wire['records'])==1


def test_source_change_before_final_publish_holds_without_changed_upload(env,wire):
    wire['after_upload']=lambda:(env[2]/'v.mp4').write_bytes(mp4()+b'changed')
    result,journal,_=invoke(env)
    assert result.failure=='media_held' and not wire['records']
    assert calls(wire,'app.bsky.video.uploadVideo')[0]['body']==mp4()
    assert journal['media']['phase']=='held'


def test_real_redirect_does_not_follow_or_publish(env,wire):
    wire['redirect']=True;result,journal,_=invoke(env)
    assert result.failure=='publish_definite' and result.error=='media_redirect_refused'
    assert len(calls(wire,'app.bsky.video.uploadVideo'))==1 and not calls(wire,'must-not-follow') and not wire['records']


def test_record_response_loss_remains_unknown(env,wire):
    wire['record_disconnect']=True;result,journal,_=invoke(env)
    assert result.failure=='media_ambiguous' and journal['media']['phase']=='unknown' and not result.post_id
    assert len(wire['records'])==1 and journal['media']['video_job_id']=='fixture-job'
    count=len(wire['calls']);again=core.send_once('alpha',text='body',production_flag=True,confirm='irrelevant',adapter_factory=lambda *a:env[1],log=lambda _:None,media_rows=[{'file':'v.mp4','alt':'動画 🌿'}])
    assert again.action=='inflight' and len(wire['calls'])==count


@pytest.mark.parametrize('override,flag',[
    ('http://127.0.0.1:1234',False),('http://127.1:1234',True),('http://localhost.example:1234',True),
    ('https://example.invalid',True),('file:///tmp/fake',True),('http://127.0.0.1:1234/path',True),
])
def test_video_service_override_is_only_explicit_exact_loopback(monkeypatch,override,flag):
    monkeypatch.setenv('THTH_TEST_BLUESKY_VIDEO_URL',override)
    if flag:monkeypatch.setenv('THTH_TEST_ALLOW_HTTP','1')
    else:monkeypatch.delenv('THTH_TEST_ALLOW_HTTP',raising=False)
    with pytest.raises(ValueError):bv.service_url()


def test_source_stream_is_snapshot_even_when_source_changes_during_upload(env,wire,monkeypatch):
    from thth import media as md
    original=md.Prepared.chunks
    def chunks(self,*a,**k):
        for part in original(self,*a,**k):
            (env[2]/'v.mp4').write_bytes(mp4()+b'changed during stream')
            yield part
    monkeypatch.setattr(md.Prepared,'chunks',chunks)
    result,journal,_=invoke(env)
    assert not result.post_id and not wire['records']
    assert calls(wire,'app.bsky.video.uploadVideo')[0]['body']==mp4()
    assert result.failure=='media_ambiguous' and journal['media']['phase']=='unknown'


def test_final_gate_after_ready_prevents_create_record(env,wire,monkeypatch):
    ready=[False];real=media_delivery._save
    def save(name,data):
        real(name,data)
        if data.get('media',{}).get('phase')=='ready':ready[0]=True
    monkeypatch.setattr(media_delivery,'_save',save)
    result,journal,_=invoke(env,veto=lambda:'approval_stale' if ready[0] else None)
    assert ready[0] and result.error=='approval_stale' and result.failure=='media_held'
    assert journal['media']['phase']=='held' and not wire['records']


def test_leave_after_upload_blocks_poll_and_post_without_state_recreation(env,wire):
    from thth import leave_gate
    cfg,adapter,repo=env;adapter=leave_gate.bind(adapter,cfg)
    def stop():
        directory=leave_gate.location();directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        marker=directory/'alpha.json';marker.write_text('{}');marker.chmod(0o600)
    wire['after_upload']=stop;wire['jobs'][0]['state']='PENDING'
    result,journal,_=invoke((cfg,adapter,repo))
    assert result.error=='account_stopped' and not calls(wire,'app.bsky.video.getJobStatus') and not wire['records']
    assert journal['media']['phase']=='uploading'


def test_poll_already_exists_uses_only_same_bound_job(env,wire,monkeypatch):
    wire['wrapped']=False;wire['poll_status']=409
    wire['jobs']=[{'jobId':'fixture-job','did':DID,'state':'PENDING'}, {'jobId':'fixture-job','did':DID,'state':'JOB_STATE_COMPLETED','error':'already_exists'}]
    monkeypatch.setattr(bv,'POLL_INTERVAL',0)
    result,journal,_=invoke(env)
    assert result.post_id and journal['media']['phase']=='published'
    assert len(calls(wire,'app.bsky.video.uploadVideo'))==1 and len(calls(wire,'app.bsky.video.getJobStatus'))==1 and len(wire['records'])==1
