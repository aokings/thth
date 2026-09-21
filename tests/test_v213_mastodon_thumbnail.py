"""C14 parent thumbnail: own prepared bytes, shared upload, no invented alt field."""
import copy,email.parser,email.policy,hashlib,json,zlib
import pytest
from thth import media,mediaformats
from tests.test_v213_mastodon_media import env,wire,invoke,posts,video_timing
from tests.test_v213_mastodon_flac import flac
from tests.test_v213_media_foundation import png,jpeg
from tests.test_v213_media_formats import exif,segment


def configure(env,wire,kind='audio',thumb=None):
    raw=flac() if kind=='audio' else video_timing([(30,1000)],scale=30000)
    name='main.flac' if kind=='audio' else 'main.mp4';(env[2]/name).write_bytes(raw)
    thumb=png() if thumb is None else thumb;(env[2]/'thumb.bin').write_bytes(thumb)
    caps=wire['caps']['configuration'];caps['statuses']['max_media_attachments']=1
    caps['media_attachments'].update(supported_mime_types=['audio/flac','video/mp4','image/png','image/jpeg'],image_size_limit=100000,image_matrix_limit=1000000,video_size_limit=1000000,video_matrix_limit=1000000,video_frame_rate_limit=30,description_limit=4)
    wire['upload']=[(200,{'id':'7','type':kind,'url':'https://instance.invalid/asset'})]
    return {'media':[{'file':name,'alt':'main','thumbnail_file':'thumb.bin','thumbnail_alt':'PRIVATE thumbnail description'}]}


def parts(call):
    msg=email.parser.BytesParser(policy=email.policy.default).parsebytes(('Content-Type: '+call[3]['Content-Type']+'\r\n\r\n').encode()+call[2])
    return list(msg.iter_parts())


@pytest.mark.parametrize('kind',['audio','video'])
@pytest.mark.parametrize('fmt',['png','jpeg'])
def test_actual_parent_upload_sanitized_thumb_alt_not_wire_count_one(env,wire,kind,fmt):
    raw=png()[:-12]+mediaformats._chunk(b'eXIf',exif())+png()[-12:] if fmt=='png' else jpeg()[:2]+segment(0xe1,b'Exif\0\0'+exif())+segment(0xfe,b'PRIVATECOMMENT')+jpeg()[2:]
    fm=configure(env,wire,kind,raw);result,journal,m=invoke(env,fm)
    assert result.post_id and journal['media']['phase']=='published';assert len(result.media)==1
    main,thumb=m['files'];assert main['role']=='media' and thumb['role']=='thumbnail' and thumb['parent']==thumb['index']==1
    assert thumb['source_sha256']!=thumb['public_sha256'] and thumb['alt']==fm['media'][0]['thumbnail_alt']
    assert 'approval-only' in media.display(m) and 'parent: 1' in media.display(m)
    calls=posts(wire,'/api/v2/media');assert len(calls)==1;call=calls[0];fields=parts(call)
    assert [p.get_param('name',header='content-disposition') for p in fields]==['description','file','thumbnail']
    assert fields[0].get_payload(decode=True)==b'main' and fields[1].get_payload(decode=True)==(env[2]/fm['media'][0]['file']).read_bytes()
    public=fields[2].get_payload(decode=True);assert hashlib.sha256(public).hexdigest()==thumb['public_sha256'] and b'PRIVATE' not in public
    assert b'PRIVATE thumbnail description' not in call[2] and int(call[3]['Content-Length'])==len(call[2])
    assert (env[2]/'thumb.bin').read_bytes()==raw


@pytest.mark.parametrize('mutation',['file_only','alt_only','empty_alt','unknown','path','duplicate_main','othermedium','image_parent','video_child'])
def test_invalid_parent_declarations_before_network(env,wire,mutation):
    fm=configure(env,wire);cfg=dict(env[0]);row=fm['media'][0]
    if mutation=='file_only':row.pop('thumbnail_alt')
    elif mutation=='alt_only':row.pop('thumbnail_file')
    elif mutation=='empty_alt':row['thumbnail_alt']=' '
    elif mutation=='unknown':row['thumbnail_uri']='https://never.invalid/a'
    elif mutation=='path':row['thumbnail_file']='../outside'
    elif mutation=='duplicate_main':row['thumbnail_file']=row['file']
    elif mutation=='othermedium':cfg['media']='bluesky'
    elif mutation=='image_parent':row['file']='a.png'
    elif mutation=='video_child':row['thumbnail_file']='child.mp4';(env[2]/'child.mp4').write_bytes(video_timing([(30,1000)],scale=30000))
    with pytest.raises(media.MediaError):media.manifest_for(fm,cfg)
    assert not wire['calls']


@pytest.mark.parametrize('field,value', [('image_size_limit',1),('image_size_limit',None),('supported_mime_types',['audio/flac'])])
def test_thumbnail_latest_caps_refuse_before_upload(env,wire,field,value):
    fm=configure(env,wire);wire['caps']['configuration']['media_attachments'][field]=value
    result,_,_=invoke(env,fm);assert result.error and not posts(wire,'/api/v2/media') and not posts(wire,'/api/v1/statuses')


def test_thumbnail_exact_cap_and_internal_alt(env,wire):
    fm=configure(env,wire);wire['caps']['configuration']['media_attachments'].update(image_size_limit=len(png())+1,image_matrix_limit=1)
    assert invoke(env,fm)[0].post_id


def test_parent_and_alt_change_digest_and_thumbnail_source_stale(env,wire):
    from thth import core
    fm=configure(env,wire);first=media.manifest_for(fm,env[0]);key=media.prepared_component(first)
    fm['media'][0]['thumbnail_alt']='different';assert media.prepared_component(media.manifest_for(fm,env[0]))!=key
    _,adapter,_=env;dry=core.send_once('alpha',text='',media_rows=fm['media'],production_flag=False,adapter_factory=lambda *a:adapter,log=lambda _:None)
    (env[2]/'thumb.bin').write_bytes(png()[:-12]+mediaformats._chunk(b'tEXt',b'a\0changed')+png()[-12:])
    stale=core.send_once('alpha',text='',media_rows=fm['media'],production_flag=True,confirm=dry.digest,adapter_factory=lambda *a:adapter,log=lambda _:None)
    assert stale.action=='approval_stale' and not posts(wire,'/api/v2/media')


def test_thumbnail_changed_after_upload_keeps_id_and_no_status(env,wire):
    fm=configure(env,wire);wire['upload']=[(202,{'id':'7','type':'audio','url':None})];wire['poll']=[(200,{'id':'7','type':'audio','url':'https://instance.invalid/a'})]
    wire['on_poll']=lambda:(env[2]/'thumb.bin').write_bytes(png()+b'changed')
    result,journal,_=invoke(env,fm);assert result.failure=='media_held' and journal['media']['remote_ids']==['7'] and not posts(wire,'/api/v1/statuses')


def test_same_normal_file_manifest_has_no_parent(env,wire):
    fm=configure(env,wire);fm['media'][0].pop('thumbnail_file');fm['media'][0].pop('thumbnail_alt')
    m=media.manifest_for(fm,env[0]);assert len(m['files'])==1 and 'parent' not in m['files'][0]
    assert m['attachments']==[] and m['post_options']=={} and m['captions']==[]


def test_parser_keeps_fields_and_writeback(tmp_path):
    from thth import queuefile
    raw='---\naccount: alpha\nstatus: draft\nmedia:\n  - file: main.flac\n    alt: main\n    thumbnail_file: thumb.png\n    thumbnail_alt: internal\n---\n## mastodon\n本文\n'
    path=tmp_path/'a.md';path.write_text(raw);q=queuefile.parse(str(path))
    assert q.front_matter['media']==[{'file':'main.flac','alt':'main','thumbnail_file':'thumb.png','thumbnail_alt':'internal'}]


@pytest.mark.parametrize('bad',[0,2,True,'1',None])
def test_prepared_parent_binding_rejects_bad_identity(env,wire,bad):
    m=media.manifest_for(configure(env,wire),env[0]);m['files'][1]['parent']=bad
    with pytest.raises(media.MediaError,match='thumbnail parent'):media.prepared_component(m)


def test_prepared_parent_binding_rejects_duplicate_and_nonmapping(env,wire):
    m=media.manifest_for(configure(env,wire),env[0]);m['files'].append(copy.deepcopy(m['files'][1]))
    with pytest.raises(media.MediaError,match='thumbnail parent'):media.prepared_component(m)
    m['files'][-1]=None
    with pytest.raises(media.MediaError):media.prepared_component(m)


def test_two_parents_order_and_focus_match_children(env,wire):
    fm=configure(env,wire);(env[2]/'second.flac').write_bytes(flac());(env[2]/'second.png').write_bytes(png()[:33]+mediaformats._chunk(b'IDAT',zlib.compress(b'\0\0\xff\0'))+png()[-12:])
    fm['media'].append({'file':'second.flac','alt':'two','thumbnail_file':'second.png','thumbnail_alt':'SECOND PRIVATE'})
    fm['post_options']={'focus':[[.1,.2],[.3,.4]]};wire['caps']['configuration']['statuses']['max_media_attachments']=2
    wire['upload']=[(200,{'id':str(i),'type':'audio','url':'https://instance.invalid/a'}) for i in [7,8]]
    before=media.manifest_for(fm,env[0]);swapped=copy.deepcopy(fm);swapped['media'].reverse()
    assert media.prepared_component(before)!=media.prepared_component(media.manifest_for(swapped,env[0]))
    result,_,_=invoke(env,fm);assert result.post_id and len(result.media)==2
    calls=posts(wire,'/api/v2/media');assert len(calls)==2
    for i,call in enumerate(calls):
        fields={p.get_param('name',header='content-disposition'):p.get_payload(decode=True) for p in parts(call)}
        assert fields['thumbnail']==(env[2]/fm['media'][i]['thumbnail_file']).read_bytes()
        assert fields['description']==fm['media'][i]['alt'].encode()
        assert fields['focus']==(','.join(str(x) for x in fm['post_options']['focus'][i])).encode()


@pytest.mark.parametrize('leaf',['symlink','hardlink','fifo'])
def test_thumbnail_file_safety_before_http(env,wire,leaf):
    import os
    fm=configure(env,wire);p=env[2]/'thumb.bin';p.unlink()
    if leaf=='symlink':p.symlink_to(env[2]/'a.png')
    elif leaf=='hardlink':os.link(env[2]/'a.png',p)
    else:os.mkfifo(p)
    with pytest.raises(media.MediaError):media.manifest_for(fm,env[0])
    assert not wire['calls']


def test_v2_parent_manifest_and_alt_digest(env,wire,monkeypatch):
    from thth import cli,bundle,threadrun
    configure(env,wire);repo=env[2]
    raw='---\nthth: 2\naccount: alpha\npublish_at: 2030-01-01T12:00:00+09:00\ncontinue_until: 2030-01-01T13:00:00+09:00\nstatus: draft\nposts:\n  - index: 1\n    media:\n      - file: main.flac\n        alt: main\n        thumbnail_file: thumb.bin\n        thumbnail_alt: internal\n  - index: 2\n---\n## mastodon\n音声\n<!-- thth: 2/2 -->\n続き\n'
    p=repo/'bundle.md';p.write_text(raw);monkeypatch.setattr(threadrun,'unreadable_runs',lambda:[]);monkeypatch.setattr(threadrun,'find_latest',lambda *a:None)
    assert not bundle.parse(str(p)).malformed
    first,why=cli._prepare_one(str(p));assert why is None and first['media_manifests'][0]['files'][1]['parent']==1
    p.write_text(raw.replace('thumbnail_alt: internal','thumbnail_alt: different'))
    second,why=cli._prepare_one(str(p));assert why is None and first['approved_sha']!=second['approved_sha']
    assert not posts(wire,'/api/v2/media')


def test_unknown_status_new_process_does_not_reupload_thumbnail(env,wire):
    import subprocess,sys
    from thth import core
    cfg,adapter,_=env;fm=configure(env,wire);factory=lambda *a:adapter
    dry=core.send_once('alpha',text='',media_rows=fm['media'],production_flag=False,adapter_factory=factory,log=lambda _:None)
    wire['status']=(500,{})
    sent=core.send_once('alpha',text='',media_rows=fm['media'],production_flag=True,confirm=dry.digest,adapter_factory=factory,log=lambda _:None)
    assert sent.action=='inflight';before=len(wire['calls'])
    script="""from thth import core,accounts
from thth.adapters.mastodon import MastodonAdapter
import sys,json
accounts.load_account=lambda _:json.loads(sys.argv[3])
accounts.load_token=lambda _:{'access_token':'synthetic-token'}
a=MastodonAdapter(instance=sys.argv[1],access_token='synthetic-token')
r=core.send_once('alpha',text='',media_rows=json.loads(sys.argv[4]),production_flag=True,confirm=sys.argv[2],adapter_factory=lambda *x:a,log=lambda _:None)
assert r.action=='inflight'
"""
    for _ in range(2):
        child=subprocess.run([sys.executable,'-B','-c',script,wire['url'],dry.digest,json.dumps(cfg),json.dumps(fm['media'])],capture_output=True,timeout=15)
        assert child.returncode==0,child.stderr
    assert len(wire['calls'])==before


@pytest.mark.parametrize('matrix',[None,1])
def test_thumbnail_does_not_invent_parent_image_matrix_cap(env,wire,matrix):
    fm=configure(env,wire);wire['caps']['configuration']['media_attachments']['image_matrix_limit']=matrix
    assert invoke(env,fm)[0].post_id


def test_actual_git_child_source_change_needs_reapproval(tmp_path,isolated_account_factory,wire,monkeypatch):
    from pathlib import Path
    from thth import accounts,core
    from thth.adapters.mastodon import MastodonAdapter
    from tests.conftest import init_git_pair,make_queue_text,run_git,approve_via_cli
    raw=make_queue_text({'account':'alpha','status':'draft','approved_sha':'','topic':''},body='## mastodon\n\n本文。\n',media='mastodon')
    raw=raw.replace('\n---\n','\nmedia:\n  - file: main.flac\n    alt: main\n    thumbnail_file: thumb.png\n    thumbnail_alt: internal\n---\n',1)
    pair=init_git_pair(tmp_path,seed_content=raw);repo=Path(pair['work']);p=Path(pair['queue_dir'])/'a.md'
    (repo/'main.flac').write_bytes(flac());(repo/'thumb.png').write_bytes(png());run_git(str(repo),['add','.']);run_git(str(repo),['commit','-m','synthetic parent thumbnail']);run_git(str(repo),['push'])
    isolated_account_factory(name='alpha',repo_dir=str(repo),media='mastodon',instance=wire['url'],production=True,hashtags=False,char_limit=500,quiet_hours=None,min_interval_hours=0)
    wire['caps']['configuration']['media_attachments']['supported_mime_types']=['audio/flac','image/png']
    assert approve_via_cli(p).returncode==0
    monkeypatch.setattr(accounts,'load_token',lambda _: {'access_token':'synthetic-token'});adapter=MastodonAdapter(instance=wire['url'],access_token='synthetic-token')
    (repo/'thumb.png').write_bytes(png()[:-12]+mediaformats._chunk(b'tEXt',b'title\0changed')+png()[-12:]);run_git(str(repo),['add','.']);run_git(str(repo),['commit','-m','changed thumbnail original']);run_git(str(repo),['push'])
    core.throw_once('alpha',production_flag=True,adapter_factory=lambda *a:adapter,log=lambda _:None,bypass_pace=True)
    assert not posts(wire,'/api/v2/media')
    assert approve_via_cli(p).returncode==0
    wire['upload']=[(200,{'id':'7','type':'audio','url':'https://instance.invalid/a'})]
    core.throw_once('alpha',production_flag=True,adapter_factory=lambda *a:adapter,log=lambda _:None,bypass_pace=True)
    assert len(posts(wire,'/api/v2/media'))==len(posts(wire,'/api/v1/statuses'))==1
    assert parts(posts(wire,'/api/v2/media')[0])[-1].get_payload(decode=True)==png()


@pytest.mark.parametrize('difference',[-1,0,1])
def test_thumbnail_less_than_model_limit(env,wire,difference):
    fm=configure(env,wire);wire['caps']['configuration']['media_attachments']['image_size_limit']=len(png())-difference
    result,_,_=invoke(env,fm)
    if difference<0:assert result.post_id and len(posts(wire,'/api/v2/media'))==1
    else:assert result.error=='media_limit_exceeded: bytes' and not posts(wire,'/api/v2/media') and not posts(wire,'/api/v1/statuses')
