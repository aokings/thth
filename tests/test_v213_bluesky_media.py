"""Local PDS wire verifies prepared bytes, CID binding and durable unknowns."""
import base64
import copy
import hashlib
import http.server
import json
import os
from pathlib import Path
import secrets
import threading
import urllib.parse
import pytest
from thth import accounts,core,inflight,media,media_delivery
from thth.adapters import base,bluesky,bluesky_media as bm
from tests.test_v213_media_foundation import png


def expected_cid(raw):return 'b'+base64.b32encode(bytes([1,85,18,32])+hashlib.sha256(raw).digest()).decode().lower().rstrip('=')


@pytest.fixture
def wire():
    state={'calls':[],'jwt':secrets.token_urlsafe(30),'password':secrets.token_urlsafe(25),'upload_status':200,'record_status':200,'mutate':None,'after_upload':None,'redirect':False,'records':{},'upload_count':0}
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def reply(self,code,body):
            raw=body if isinstance(body,bytes) else json.dumps(body).encode();self.send_response(code);self.send_header('Content-Length',str(len(raw)));self.end_headers()
            try:self.wfile.write(raw)
            except (BrokenPipeError,ConnectionResetError):pass
        def do_POST(self):
            raw=self.rfile.read(int(self.headers['Content-Length']));kind=self.path.rsplit('/',1)[-1];state['calls'].append((kind,raw,dict(self.headers)))
            if kind=='com.atproto.server.createSession':return self.reply(200,{'accessJwt':state['jwt'],'refreshJwt':secrets.token_urlsafe(30),'did':'did:plc:synthetic','handle':'demo.test'})
            if state['redirect']:
                self.send_response(307);self.send_header('Location','/xrpc/must-not-follow');self.end_headers();return
            if kind=='com.atproto.repo.uploadBlob':
                state['upload_count']+=1
                if state.get('disconnect'):
                    self.close_connection=True;return
                if state.get('second_error') and state['upload_count']==2:return self.reply(500,{})
                value={'blob':{'$type':'blob','ref':{'$link':expected_cid(raw)},'size':len(raw),'mimeType':self.headers['Content-Type']}}
                if state['mutate']:state['mutate'](value)
                if state['after_upload']:state['after_upload']()
                return self.reply(state['upload_status'],value)
            uri='at://did:plc:synthetic/app.bsky.feed.post/abc'+('2' if state['records'] else '')
            state['records'][uri]={'uri':uri,'cid':'record-cid','record':json.loads(raw)['record']}
            return self.reply(state['record_status'],state.get('record_body',{'uri':uri}))
        def do_GET(self):
            state['calls'].append(('getPosts',b'',dict(self.headers)))
            uri=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('uris',[''])[0]
            if uri in state['records']:return self.reply(200,{'posts':[state['records'][uri]]})
            return self.reply(200,{'posts':[{'uri':'at://did:plc:other/app.bsky.feed.post/parent','cid':'parent-cid','record':{'reply':{'root':{'uri':'at://did:plc:other/app.bsky.feed.post/root','cid':'root-cid'}}}}]})
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();state['url']='http://127.0.0.1:'+str(server.server_port)
    try:yield state
    finally:server.shutdown();server.server_close();thread.join(3)


@pytest.fixture
def env(tmp_path,monkeypatch,wire):
    root=tmp_path.resolve()/'root';root.mkdir(mode=0o700);repo=root/'repo';repo.mkdir();(repo/'a.png').write_bytes(png())
    monkeypatch.setenv('THTH_ROOT',str(root));monkeypatch.delenv('THTH_ACCOUNTS_DIR',raising=False)
    cfg={'account':'alpha','media':'bluesky','service':wire['url'],'repo_dir':str(repo),'production':True,'hashtags':False,'char_limit':300}
    monkeypatch.setattr(accounts,'load_account',lambda _:cfg)
    adapter=bluesky.BlueskyAdapter(service=wire['url'],identifier='demo.test',app_password=wire['password'])
    monkeypatch.setattr(accounts,'load_token',lambda _:{'identifier':'demo.test','app_password':wire['password']})
    monkeypatch.setattr(core,'_append_run',lambda *a,**kw:None)
    return cfg,adapter,repo


def calls(wire,method):return [v for v in wire['calls'] if v[0]=='com.atproto.repo.'+method]


def invoke(env,*,fm=None,post=None,veto=None):
    cfg,adapter,repo=env;fm=fm or {'media':[{'file':'a.png','alt':'一枚目 🌿'}]};manifest=media.manifest_for(fm,cfg);state=accounts.state_dir_for('alpha')
    inflight.write(state,file='fixture',started='2030-01-01T00:00:00+09:00')
    result=media_delivery.publish(adapter,post or base.Post('body'),cfg=cfg,fm=fm,manifest=manifest,state_dir=state,before_publish=veto)
    return result,inflight.read(state),manifest


def test_same_public_bytes_alt_order_aspect_and_reply(env,wire):
    cfg,adapter,repo=env;(repo/'b.png').write_bytes(png())
    fm={'media':[{'file':'a.png','alt':'first 日本語'},{'file':'b.png','alt':'second 🌿'}]}
    result,journal,manifest=invoke(env,fm=fm,post=base.Post('body https://example.invalid',reply_to='at://did:plc:other/app.bsky.feed.post/parent'))
    assert result.post_id=='at://did:plc:synthetic/app.bsky.feed.post/abc' and journal['media']['phase']=='published'
    assert journal['media']['instance']==wire['url'] and os.stat(accounts.state_dir_for('alpha')+'/inflight.json').st_mode&0o777==0o600
    uploaded=calls(wire,'uploadBlob');assert len(uploaded)==2
    for call,row in zip(uploaded,manifest['files']):
        assert hashlib.sha256(call[1]).hexdigest()==row['public_sha256']
        assert int(call[2]['Content-Length'])==row['public_size'] and call[2]['Content-Type']=='image/png'
        assert call[2]['Authorization']=='Bearer '+wire['jwt']
    record=json.loads(calls(wire,'createRecord')[0][1])['record']
    assert record['embed']['$type']=='app.bsky.embed.images'
    assert [v['alt'] for v in record['embed']['images']]==['first 日本語','second 🌿']
    assert all(v['aspectRatio']=={'width':1,'height':1} for v in record['embed']['images'])
    assert record['reply']['root']['cid']=='root-cid' and record['reply']['parent']['cid']=='parent-cid' and record['facets']
    assert wire['jwt'] not in json.dumps(journal) and wire['password'] not in json.dumps(journal)


@pytest.mark.parametrize('count,gallery,ok',[(4,False,True),(5,False,False),(10,True,True),(20,True,True),(21,True,False)])
def test_images_gallery_schema_limits(env,wire,count,gallery,ok):
    cfg,_,repo=env;fm={'media':[],'post_options':{'gallery':gallery}}
    for i in range(count):(repo/f'{i}.png').write_bytes(png());fm['media'].append({'file':f'{i}.png','alt':str(i)})
    result,_,_=invoke(env,fm=fm);assert bool(result.post_id)==ok
    if ok:
        embed=json.loads(calls(wire,'createRecord')[0][1])['record']['embed'];entries=embed['items' if gallery else 'images'];assert len(entries)==count
        if gallery:assert all(e['$type']=='app.bsky.embed.gallery#image' for e in entries)
    else:assert result.error=='media_limit_exceeded: count' and not wire['calls']


@pytest.mark.parametrize('size,ok',[(2_000_000,True),(2_000_001,False)])
def test_public_byte_exact_limit(env,size,ok):
    cfg,_,_=env;m=media.manifest_for({'media':[{'file':'a.png','alt':'alt'}]},cfg);m['files'][0]['public_size']=size
    assert (bm.intent_error(m) is None)==ok


@pytest.mark.parametrize('field,value',[('size',1),('size',True),('mimeType','image/jpeg'),('ref',{'$link':'bafk-invalid'}),('$type','not-blob')])
def test_invalid_blob_never_creates_record_and_retains_unknown(env,wire,field,value):
    wire['mutate']=lambda v:v['blob'].__setitem__(field,value)
    result,journal,_=invoke(env);assert result.failure=='media_ambiguous' and journal['media']['phase']=='unknown'
    assert not calls(wire,'createRecord')


@pytest.mark.parametrize('status,phase',[(400,'failed'),(403,'failed'),(500,'unknown'),(202,'unknown')])
def test_upload_error_classification(env,wire,status,phase):
    wire['upload_status']=status;result,journal,_=invoke(env)
    assert result.error and journal['media']['phase']==phase and not calls(wire,'createRecord')


def test_final_stale_holds_known_blob(env,wire):
    result,journal,_=invoke(env,veto=lambda:'approval_stale' if calls(wire,'uploadBlob') else None)
    assert result.error=='approval_stale' and result.failure=='media_held' and journal['media']['phase']=='held'
    assert len(journal['media']['remote_ids'])==1 and not calls(wire,'createRecord')


def test_source_change_after_upload_is_held(env,wire):
    wire['after_upload']=lambda:(env[2]/'a.png').write_bytes(png()+b'changed')
    result,journal,_=invoke(env);assert result.error=='media_source_changed' or 'source_changed' in result.error
    assert result.failure in ('media_held','media_ambiguous') and not calls(wire,'createRecord') and journal['media']['remote_ids']


@pytest.mark.parametrize('status,phase',[(400,'held'),(500,'unknown')])
def test_create_record_error_never_clear_media(env,wire,status,phase):
    wire['record_status']=status;result,journal,_=invoke(env);assert result.error and journal['media']['phase']==phase and len(calls(wire,'createRecord'))==1


def test_redirect_not_followed(env,wire):
    wire['redirect']=True;result,journal,_=invoke(env);assert result.error and not any(c[0]=='must-not-follow' for c in wire['calls'])


@pytest.mark.parametrize('option,value',[('languages',['ja']),('labels',['porn']),('presentation','default'),('facets',[])])
def test_unsupported_options_loud_before_http(env,wire,option,value):
    result,_,_=invoke(env,fm={'media':[{'file':'a.png','alt':'a'}],'post_options':{option:value}})
    assert result.error=='unsupported_attachment: bluesky/image_post_options' and not wire['calls']


def test_typed_quote_not_dropped(env,wire):
    result,_,_=invoke(env,fm={'media':[{'file':'a.png','alt':'a'}],'attachments':[{'type':'quote','uri':'at://did:plc:other/app.bsky.feed.post/x','cid':'c'}]})
    assert result.error=='unsupported_attachment: bluesky/typed_attachment_pending' and not wire['calls']


def test_lint_local_count_limit_no_http(env,wire):
    cfg,_,repo=env;fm={'media':[]}
    for i in range(5):(repo/f'{i}.png').write_bytes(png());fm['media'].append({'file':f'{i}.png','alt':'a'})
    assert media_delivery.lint_notes(cfg,fm)==['media_limit_exceeded: count'] and not wire['calls']


def test_progress_save_failure_no_upload(env,wire,monkeypatch):
    monkeypatch.setattr(media_delivery,'_save',lambda *a:(_ for _ in ()).throw(OSError('fake')))
    result,_,_=invoke(env);assert result.error=='media_journal_unavailable' and not wire['calls']


@pytest.mark.parametrize('phase',['prepared','uploading','ready','publishing','published'])
def test_journal_failure_stops_next_effect_and_restart(env,wire,monkeypatch,phase):
    original=media_delivery._save;failed=[]
    def save(name,data):
        if data['media']['phase']==phase:failed.append(phase);raise OSError('synthetic')
        return original(name,data)
    monkeypatch.setattr(media_delivery,'_save',save)
    result,_,_=invoke(env);assert failed and result.error and not result.post_id
    assert len(calls(wire,'uploadBlob'))==int(phase not in ('prepared','uploading'))
    assert len(calls(wire,'createRecord'))==int(phase=='published')
    count=len(wire['calls']);again=core.send_once('alpha',text='body',production_flag=True,confirm='irrelevant',adapter_factory=lambda *a:env[1],log=lambda _:None,media_rows=[{'file':'a.png','alt':'一枚目 🌿'}])
    assert again.action=='inflight' and len(wire['calls'])==count


def test_cli_two_stage_empty_body_media_and_compact_sent(env,wire,monkeypatch,capsys):
    from thth import cli,read_coordination,sent
    monkeypatch.setattr(core,'_default_adapter_factory',lambda *a:env[1]);monkeypatch.setattr(read_coordination,'invoke',lambda args,name,fn=None:fn() if fn else args.func(args))
    argv=['send','alpha','--media','a.png','--alt','説明']
    assert cli.main(argv)==0;shown=capsys.readouterr().out;digest=next(s.split(': ',1)[1] for s in shown.splitlines() if s.startswith('digest: '))
    assert not wire['calls']
    assert cli.main(argv+['--production','--confirm',digest])==0
    record=json.loads(calls(wire,'createRecord')[0][1])['record'];assert record['text']==''
    stored=sent.read(accounts.state_dir_for('alpha'),'at://did:plc:synthetic/app.bsky.feed.post/abc')
    assert stored['media']==[{'sha256':hashlib.sha256(png()).hexdigest(),'kind':'image','alt_present':True,'remote_id':expected_cid(png())}]
    assert inflight.read(accounts.state_dir_for('alpha')) is None


def test_orientation_aspect_and_sanitized_bytes(env,wire):
    import struct,zlib
    from thth import mediaformats
    from tests.test_v213_media_formats import exif
    raw=b'\x89PNG\r\n\x1a\n'+mediaformats._chunk(b'IHDR',struct.pack('>IIBBBBB',2,1,8,2,0,0,0))+mediaformats._chunk(b'eXIf',exif(6))+mediaformats._chunk(b'IDAT',zlib.compress(b'\0'+b'\x11\x22\x33'*2))+mediaformats._chunk(b'IEND',b'')
    (env[2]/'a.png').write_bytes(raw);result,_,m=invoke(env);assert result.post_id
    assert m['files'][0]['source_sha256']!=m['files'][0]['public_sha256']
    assert b'PRIVATE' not in calls(wire,'uploadBlob')[0][1]
    image=json.loads(calls(wire,'createRecord')[0][1])['record']['embed']['images'][0]
    assert image['aspectRatio']=={'width':1,'height':2}


@pytest.mark.parametrize('drift',['none','alt','body','revoke'])
def test_v1_git_approval_and_final_veto(tmp_path,isolated_account_factory,wire,monkeypatch,drift):
    from tests.conftest import init_git_pair,make_queue_text,run_git,approve_via_cli
    from thth import queuefile
    raw=make_queue_text({'account':'alpha','status':'draft','approved_sha':'','topic':''},body='## bluesky\n\n本文。\n',media='bluesky')
    raw=raw.replace('\n---\n','\nmedia:\n  - file: a.png\n    alt: 点A\n---\n',1)
    pair=init_git_pair(tmp_path,seed_content=raw);repo=Path(pair['work']);path=Path(pair['queue_dir'])/'a.md';(repo/'a.png').write_bytes(png())
    run_git(str(repo),['add','.']);run_git(str(repo),['commit','-m','synthetic']);run_git(str(repo),['push'])
    isolated_account_factory(name='alpha',repo_dir=str(repo),media='bluesky',service=wire['url'],production=True,hashtags=False,char_limit=300,quiet_hours=None,min_interval_hours=0)
    approved=approve_via_cli(path);assert approved.returncode==0,approved.stderr
    monkeypatch.setattr(accounts,'load_token',lambda _:{'identifier':'demo.test','app_password':wire['password']})
    adapter=bluesky.BlueskyAdapter(service=wire['url'],identifier='demo.test',app_password=wire['password'])
    def mutate():
        if drift!='none':
            text=path.read_text();text=text.replace('status: approved','status: draft') if drift=='revoke' else text.replace('点A','変更alt') if drift=='alt' else text.replace('本文。','変更本文。');path.write_text(text)
    wire['after_upload']=mutate
    result=core.throw_once('alpha',production_flag=True,adapter_factory=lambda *a:adapter,log=lambda _:None,bypass_pace=True)
    if drift=='none':assert result.action=='post' and queuefile.parse(str(path)).get('status')=='posted'
    else:assert result.action=='inflight' and not calls(wire,'createRecord')


def test_connection_loss_is_unknown_no_retry(env,wire):
    wire['disconnect']=True;result,journal,_=invoke(env)
    assert result.failure=='media_ambiguous' and journal['media']['phase']=='unknown'
    assert len(calls(wire,'uploadBlob'))==1 and not calls(wire,'createRecord')


def test_second_upload_unknown_keeps_first_id(env,wire):
    (env[2]/'b.png').write_bytes(png());wire['second_error']=True
    result,journal,_=invoke(env,fm={'media':[{'file':'a.png','alt':'a'},{'file':'b.png','alt':'b'}]})
    assert result.failure=='media_ambiguous' and journal['media']['phase']=='unknown' and journal['media']['remote_ids']==[expected_cid(png())]
    assert len(calls(wire,'uploadBlob'))==2 and not calls(wire,'createRecord')


def test_stop_between_upload_and_create_does_not_recreate_state(env,wire):
    from thth import leave_gate
    cfg,adapter,repo=env;adapter=leave_gate.bind(adapter,cfg)
    def stop():
        directory=leave_gate.location();directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        p=directory/'alpha.json';p.write_text('{}');p.chmod(0o600)
    wire['after_upload']=stop;result,journal,_=invoke((cfg,adapter,repo))
    assert result.error=='account_stopped' and result.failure in ('media_held','media_ambiguous')
    assert not calls(wire,'createRecord') and journal['media']['phase']=='uploading'


@pytest.mark.parametrize('failure',[False,True])
def test_v2_each_segment_and_reply_parent(tmp_path,isolated_account_factory,wire,monkeypatch,failure):
    import datetime
    from tests.conftest import init_git_pair,run_git,approve_via_cli
    from tests.test_thread_publish import bundle_text,REL
    from thth import threadthrow,threadrun,jst
    now=jst.now_jst();raw=bundle_text(account='alpha',segments=['一段目','二段目'],status='draft',topic='',publish_at=now.isoformat(),continue_until=(now+datetime.timedelta(hours=1)).isoformat()).replace('## threads','## bluesky')
    raw=raw.replace('  - index: 1','  - index: 1\n    media:\n      - file: a.png\n        alt: 一').replace('  - index: 2','  - index: 2\n    media:\n      - file: b.png\n        alt: 二')
    pair=init_git_pair(tmp_path,seed_content=raw,seed_name='thread.md');repo=Path(pair['work'])
    for name in ('a.png','b.png'):(repo/name).write_bytes(png())
    run_git(str(repo),['add','.']);run_git(str(repo),['commit','-m','synthetic']);run_git(str(repo),['push'])
    isolated_account_factory(name='alpha',repo_dir=str(repo),media='bluesky',service=wire['url'],production=True,hashtags=False,char_limit=300,quiet_hours=None,min_interval_hours=0)
    approved=approve_via_cli(repo/REL);assert approved.returncode==0,approved.stderr
    monkeypatch.setattr(accounts,'load_token',lambda _:{'identifier':'demo.test','app_password':wire['password']})
    adapter=bluesky.BlueskyAdapter(service=wire['url'],identifier='demo.test',app_password=wire['password']);wire['second_error']=failure
    result=threadthrow.publish_bundle('alpha',REL,adapter_factory=lambda *a:adapter,now=now,log=lambda _:None)
    if failure:
        assert len(calls(wire,'createRecord'))==1 and inflight.read(accounts.state_dir_for('alpha')) is not None
        count=len(wire['calls']);threadthrow.publish_bundle('alpha',REL,adapter_factory=lambda *a:adapter,now=now,log=lambda _:None)
        assert len(wire['calls'])==count
    else:
        assert [r.action for r in result]==['published','published'],result
        record=json.loads(calls(wire,'createRecord')[1][1])['record']
        assert record['reply']['parent']['uri']==record['reply']['root']['uri']=='at://did:plc:synthetic/app.bsky.feed.post/abc'
        assert [r['media'][0]['remote_id'] for r in threadrun.load(result[0].run_id)['posts']]==[expected_cid(png())]*2
