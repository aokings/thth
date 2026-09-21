"""Synthetic EBML/VINT/Opus/Vorbis and actual fake HTTP, no PCM codec tool."""
import email.parser,email.policy,hashlib,os,struct,zlib
import pytest
from thth import media,mediaformats
from tests.test_v213_mastodon_media import env,wire,invoke,posts
from tests.test_v213_mastodon_ogg_opus import head as opus_head
from tests.test_v213_mastodon_ogg_vorbis import header as vorbis_head,setup as vorbis_setup
from tests.test_v213_mastodon_flac import comments
from tests.test_v213_media_foundation import png
from tests.test_v213_media_formats import exif


def vint(value,width=None):
    n=width or next(n for n in range(1,9) if value<(1<<(7*n))-1)
    assert value<(1<<(7*n));return ((1<<(7*n))|value).to_bytes(n,'big')


def element(tag,data):return tag.to_bytes((tag.bit_length()+7)//8,'big')+vint(len(data))+data

def uint(tag,value):return element(tag,value.to_bytes(max(1,(value.bit_length()+7)//8),'big'))

def text(tag,value):return element(tag,value.encode())

def master(tag,*children):return element(tag,b''.join(children))

def block(*packets,lace=0,number=1,simple=True):
    flags=0x80 if simple else 0;extra=b''
    if lace:
        flags|=lace<<1;extra=bytes([len(packets)-1])
        if lace==1:
            for p in packets[:-1]:extra+=b'\xff'*(len(p)//255)+bytes([len(p)%255])
        elif lace==3:
            if len(packets)>1:extra+=vint(len(packets[0]))
            for i in range(1,len(packets)-1):extra+=vint(len(packets[i])-len(packets[i-1])+63,1)
    return vint(number)+b'\0\0'+bytes([flags])+extra+b''.join(packets)


def webm(*,codec='A_OPUS',extra=b'',track_extra=b'',info_extra=b'',data=None,duration=1.0,unknown=False,private=None):
    h=master(0x1a45dfa3,uint(0x4286,1),uint(0x42f7,1),uint(0x42f2,4),uint(0x42f3,8),text(0x4282,'webm'),uint(0x4287,4),uint(0x4285,2))
    if private is None:
        if codec=='A_OPUS':private=opus_head()
        else:
            a=vorbis_head();b=b'\x03vorbis'+comments()+b'\x01';c=vorbis_setup();private=b'\x02'+bytes([len(a),len(b)])+a+b+c
    info=master(0x1549a966,uint(0x2ad7b1,1000000),b'' if duration is None else element(0x4489,struct.pack('>d',duration)),text(0x4d80,'synthetic'),text(0x5741,'fixture'),info_extra)
    track=master(0x1654ae6b,master(0xae,uint(0xd7,1),uint(0x73c5,1),uint(0x83,2),text(0x86,codec),element(0x63a2,private),master(0xe1,element(0xb5,struct.pack('>d',48000)),uint(0x9f,1)),track_extra))
    sample=b'\x80x' if codec=='A_OPUS' else b'\0'
    cluster=master(0x1f43b675,uint(0xe7,0),element(0xa3,block(sample)) if data is None else data)
    body=info+track+cluster+extra
    return h+(b'\x18\x53\x80\x67\xff'+body if unknown else element(0x18538067,body))


def inspect(tmp_path,raw):
    path=tmp_path/'sound.webm';path.write_bytes(raw);fd=os.open(path,os.O_RDONLY)
    try:return mediaformats.inspect(fd,len(raw))
    finally:os.close(fd)


def configure(env,wire,raw):
    (env[2]/'sound.webm').write_bytes(raw);wire['caps']['configuration']['media_attachments'].update(supported_mime_types=['audio/webm'],image_size_limit=1,image_matrix_limit=None,video_size_limit=len(raw),video_matrix_limit=None,video_frame_rate_limit=None)
    wire['upload']=[(200,{'id':'7','type':'audio','url':'https://instance.invalid/audio'})]
    return {'media':[{'file':'sound.webm','alt':'音声'}]}


@pytest.mark.parametrize('codec',['A_OPUS','A_VORBIS'])
def test_actual_wire_same_bytes_and_declared_duration(env,wire,codec):
    raw=webm(codec=codec);fm=configure(env,wire,raw);result,journal,m=invoke(env,fm)
    assert result.post_id and journal['media']['phase']=='published';row=m['files'][0]
    assert row['format']=='webm' and row['kind']=='audio' and row['duration']==.001 and row['width'] is row['height'] is None
    assert row['source_sha256']==row['public_sha256']==hashlib.sha256(raw).hexdigest()
    call=posts(wire,'/api/v2/media')[0];message=email.parser.BytesParser(policy=email.policy.default).parsebytes(('Content-Type: '+call[3]['Content-Type']+'\r\n\r\n').encode()+call[2]);parts={p.get_param('name',header='content-disposition'):p for p in message.iter_parts()}
    assert parts['file'].get_payload(decode=True)==raw and parts['file'].get_content_type()=='audio/webm'
    assert parts['description'].get_payload(decode=True).decode()=='音声'


@pytest.mark.parametrize('lace',[0,1,2,3])
def test_block_lacing_and_group(tmp_path,lace):
    data=block(*([b'\x80x'] if not lace else [b'\x80x',b'\x80y',b'\x80z']),lace=lace,simple=False)
    raw=webm(data=master(0xa0,element(0xa1,data),uint(0x9b,3),element(0x75a2,b'\0')))
    assert inspect(tmp_path,raw).kind=='audio'


def test_ebml_lace_allones_value_is_not_unknown_size(tmp_path):
    payload=block(b'\x80'+bytes(126),b'\x80'+bytes(126),lace=3).replace(b'\x86\x01\x40\x7f',b'\x86\x01\xff',1)
    assert inspect(tmp_path,webm(data=element(0xa3,payload))).kind=='audio'


def test_unknown_segment_size_and_missing_duration(tmp_path):
    assert inspect(tmp_path,webm(unknown=True,duration=None)).duration is None


def tags(name,value):return master(0x1254c367,master(0x7373,master(0x63c0),master(0x67c8,text(0x45a3,name),text(0x4487,value))))


def cover(raw,mime='image/png'):
    return master(0x1941a469,master(0x61a7,text(0x466e,'cover.png'),text(0x4660,mime),element(0x465c,raw),uint(0x46ae,1)))


def test_scalar_comment_cover_retained_private(env,wire):
    raw=webm(extra=tags('TITLE','private title')+cover(png()));fm=configure(env,wire,raw);m=media.manifest_for(fm,env[0]);row=m['files'][0]
    assert row['source_sha256']==row['public_sha256'] and 'private title' not in media.display(m)
    assert 'embedded_cover_retained' in media.display(m) and not wire['calls']


@pytest.mark.parametrize('extra,reason',[
    (tags('GPSLatitude','1'),'location_metadata_present'),(tags('LOCATION','2'),'location_metadata_present'),
    (tags('XMP','opaque'),'location_metadata_unverifiable'),
    (master(0x1254c367,master(0x7373,master(0x63c0),master(0x67c8,text(0x45a3,'TITLE'),element(0x4485,b'opaque')))),'location_metadata_unverifiable'),
    (cover(png()[:-12]+mediaformats._chunk(b'eXIf',exif())+png()[-12:]),'location_metadata_present'),
    (cover(b'https://private.invalid/a','-->'),'location_metadata_unverifiable'),
    (element(0xec,b'GPS'),'location_metadata_unverifiable'),(element(0x1a45dfa3,b''),'location_metadata_unverifiable'),
])
def test_privacy_before_http(env,wire,extra,reason):
    raw=webm(extra=extra);fm=configure(env,wire,raw)
    with pytest.raises(media.MediaError,match=reason):media.manifest_for(fm,env[0])
    assert not wire['calls'] and (env[2]/'sound.webm').read_bytes()==raw


def test_zero_void_and_crc(tmp_path):
    from thth.ebmlaudio import WebM
    from thth.audioformats import Reader
    raw=text(0x4d80,'fixture')+text(0x5741,'fixture')+element(0xec,bytes(8));raw+=element(0xbf,zlib.crc32(raw).to_bytes(4,'little'))
    path=tmp_path/'crc';path.write_bytes(raw);fd=os.open(path,os.O_RDONLY)
    try:assert WebM(Reader(fd,len(raw))).fields(0,len(raw),'info')[0x4d80]==['__other__']
    finally:os.close(fd)
    path.write_bytes(raw[:-1]+bytes([raw[-1]^1]));fd=os.open(path,os.O_RDONLY)
    try:
        with pytest.raises(mediaformats.FormatError):WebM(Reader(fd,len(raw))).fields(0,len(raw),'info')
    finally:os.close(fd)


@pytest.mark.parametrize('case', ['bad_id','truncated','duplicate_info','unknown_cluster','lace_disabled','track_missing','bad_flags','unknown_codec','zero_duration','nan_duration','private_extension','channels','missing_private','cover_uid','cover_name','missing_mux','timestamp_missing','unknown_track_field'])
def test_structural_and_uninspected_boundaries(env,wire,case):
    raw=webm()
    if case=='bad_id':raw=b'\0'+raw[1:]
    elif case=='truncated':raw=raw[:-1]
    elif case=='duplicate_info':raw=webm(extra=master(0x1549a966))
    elif case=='unknown_cluster':raw=webm(extra=b'\x1f\x43\xb6\x75\xff')
    elif case=='lace_disabled':raw=webm(track_extra=uint(0x9c,0),data=element(0xa3,block(b'\x80x',b'\x80y',lace=1)))
    elif case=='track_missing':raw=webm(data=element(0xa3,block(b'\x80x',number=2)))
    elif case=='bad_flags':raw=webm(data=element(0xa3,b'\x81\0\0\x90\x80x'))
    elif case=='unknown_codec':raw=webm(codec='A_UNKNOWN')
    elif case=='zero_duration':raw=webm(duration=0)
    elif case=='nan_duration':raw=webm(duration=float('nan'))
    elif case=='private_extension':raw=webm(private=opus_head()+b'GPS')
    elif case=='channels':raw=webm(private=opus_head(channels=2))
    elif case=='missing_private':raw=webm(private=b'')
    elif case=='cover_uid':raw=webm(extra=master(0x1941a469,master(0x61a7,text(0x466e,'cover'),text(0x4660,'image/png'),element(0x465c,png()))))
    elif case=='cover_name':raw=webm(extra=master(0x1941a469,master(0x61a7,uint(0x46ae,1),text(0x4660,'image/png'),element(0x465c,png()))))
    elif case=='missing_mux':raw=raw.replace(text(0x4d80,'synthetic'),element(0xec,bytes(len(text(0x4d80,'synthetic'))-2)),1)
    elif case=='timestamp_missing':raw=raw.replace(uint(0xe7,0),element(0xec,b'\0'),1)
    elif case=='unknown_track_field':raw=webm(track_extra=element(0x6d80,b'private compression'))
    fm=configure(env,wire,raw)
    with pytest.raises(media.MediaError):media.manifest_for(fm,env[0])
    assert not wire['calls']


@pytest.mark.parametrize('raw',[b'\x81\0\0\x82\x01\x10x',b'\x81\0\0\x84\x01abc',b'\x81\0\0\x86\x02\x81\x80x'])
def test_invalid_lace_lengths(tmp_path,raw):
    with pytest.raises(mediaformats.FormatError):inspect(tmp_path,webm(data=element(0xa3,raw)))


def test_simple_and_group_preserve_packet_order(tmp_path,monkeypatch):
    from thth import ebmlaudio
    calls=[];original=ebmlaudio._audio
    def observe(packet,streams):calls.append(packet.read(0,packet.size));return original(packet,streams)
    monkeypatch.setattr(ebmlaudio,'_audio',observe)
    raw=webm(data=master(0xa0,element(0xa1,block(b'\x80a',simple=False)))+element(0xa3,block(b'\x80b'))+master(0xa0,element(0xa1,block(b'\x80c',simple=False))))
    inspect(tmp_path,raw);assert calls==[b'\x80a',b'\x80b',b'\x80c']


def test_fresh_caps_and_audio_mime_only(env,wire):
    fm=configure(env,wire,webm());wire['caps']['configuration']['media_attachments']['supported_mime_types']=['video/webm']
    result,_,_=invoke(env,fm);assert 'unsupported_attachment' in result.error and not posts(wire,'/api/v2/media')
    wire['caps']['configuration']['media_attachments']['supported_mime_types']=['audio/webm'];wire['caps']['configuration']['media_attachments']['video_size_limit']=1
    result,_,_=invoke(env,fm);assert result.error and not posts(wire,'/api/v2/media')


def test_unknown_duration_not_zero_in_display(env,wire):
    fm=configure(env,wire,webm(duration=None));m=media.manifest_for(fm,env[0]);row=m['files'][0]
    assert row['duration'] is None and row['width'] is row['height'] is None
    assert '秒数: 未取得' in media.display(m) and '寸法: 適用外' in media.display(m)


def test_unknown_status_no_replay(env,wire):
    from thth import core
    _,adapter,_=env;fm=configure(env,wire,webm());factory=lambda *args:adapter
    first=core.send_once('alpha',text='',media_rows=fm['media'],production_flag=False,adapter_factory=factory,log=lambda _:None);wire['status']=(500,{})
    sent=core.send_once('alpha',text='',media_rows=fm['media'],production_flag=True,confirm=first.digest,adapter_factory=factory,log=lambda _:None)
    again=core.send_once('alpha',text='',media_rows=fm['media'],production_flag=True,confirm=first.digest,adapter_factory=factory,log=lambda _:None)
    assert sent.action==again.action=='inflight' and len(posts(wire,'/api/v2/media'))==len(posts(wire,'/api/v1/statuses'))==1


def test_source_changed_holds_uploaded_id(env,wire):
    fm=configure(env,wire,webm());wire['upload']=[(202,{'id':'7','type':'audio','url':None})];wire['poll']=[(200,{'id':'7','type':'audio','url':'https://instance.invalid/a'})]
    wire['on_poll']=lambda:(env[2]/'sound.webm').write_bytes(webm(extra=tags('TITLE','changed')))
    result,journal,_=invoke(env,fm);assert result.failure=='media_held' and journal['media']['remote_ids']==['7'] and not posts(wire,'/api/v1/statuses')


def test_webm_actual_git_approval_and_source_stale(tmp_path,isolated_account_factory,wire,monkeypatch):
    from pathlib import Path
    from thth import accounts,core
    from thth.adapters.mastodon import MastodonAdapter
    from tests.conftest import init_git_pair,make_queue_text,run_git,approve_via_cli
    text=make_queue_text({'account':'alpha','status':'draft','approved_sha':'','topic':''},body='## mastodon\n\n本文。\n',media='mastodon')
    text=text.replace('\n---\n','\nmedia:\n  - file: sound.webm\n    alt: 音声\n---\n',1)
    pair=init_git_pair(tmp_path,seed_content=text);repo=Path(pair['work']);path=Path(pair['queue_dir'])/'a.md'
    (repo/'sound.webm').write_bytes(webm());run_git(str(repo),['add','.']);run_git(str(repo),['commit','-m','synthetic WebM']);run_git(str(repo),['push'])
    isolated_account_factory(name='alpha',repo_dir=str(repo),media='mastodon',instance=wire['url'],production=True,hashtags=False,char_limit=500,quiet_hours=None,min_interval_hours=0)
    wire['caps']['configuration']['media_attachments']['supported_mime_types']=['audio/webm']
    approved=approve_via_cli(path);assert approved.returncode==0,approved.stderr
    monkeypatch.setattr(accounts,'load_token',lambda _: {'access_token':'synthetic-token'})
    adapter=MastodonAdapter(instance=wire['url'],access_token='synthetic-token')
    (repo/'sound.webm').write_bytes(webm(extra=tags('TITLE','changed')));run_git(str(repo),['add','.']);run_git(str(repo),['commit','-m','changed source']);run_git(str(repo),['push'])
    stale=core.throw_once('alpha',production_flag=True,adapter_factory=lambda *a:adapter,log=lambda _:None,bypass_pace=True)
    assert not posts(wire,'/api/v2/media') and not posts(wire,'/api/v1/statuses')
    approved=approve_via_cli(path);assert approved.returncode==0,approved.stderr
    wire['upload']=[(200,{'id':'7','type':'audio','url':'https://instance.invalid/a'})]
    core.throw_once('alpha',production_flag=True,adapter_factory=lambda *a:adapter,log=lambda _:None,bypass_pace=True)
    assert len(posts(wire,'/api/v2/media'))==len(posts(wire,'/api/v1/statuses'))==1


def test_v2_manifest_and_source_change_digest(env,wire,monkeypatch):
    from thth import cli,lint,bundle,threadrun
    import json
    cfg,_,repo=env;fm=configure(env,wire,webm())
    raw='---\nthth: 2\naccount: alpha\npublish_at: 2030-01-01T12:00:00+09:00\ncontinue_until: 2030-01-01T13:00:00+09:00\nstatus: draft\nposts:\n'
    raw+='  - index: 1\n    media:\n      - file: sound.webm\n        alt: 音声の説明\n  - index: 2\n---\n## mastodon\n音声\n<!-- thth: 2/2 -->\n続き\n'
    path=repo/'bundle.md';path.write_text(raw)
    monkeypatch.setattr(threadrun,'unreadable_runs',lambda:[]);monkeypatch.setattr(threadrun,'find_latest',lambda *a:None)
    assert not bundle.parse(str(path)).malformed
    assert not [x for x in lint.lint_file(str(path)) if not lint.is_warning(x)]
    prepared,reason=cli._prepare_one(str(path));assert reason is None and prepared['media_manifests'][0]['files'][0]['format']=='webm'
    (repo/'sound.webm').write_bytes(webm(extra=tags('TITLE','changed')));other,reason=cli._prepare_one(str(path))
    assert reason is None and other['approved_sha']!=prepared['approved_sha']
    assert not posts(wire,'/api/v2/media') and not posts(wire,'/api/v1/statuses')


@pytest.mark.parametrize('field', [uint(0x9c,2),uint(0xb9,2),uint(0x55aa,2),uint(0x23e383,0)])
def test_track_scalar_ranges(tmp_path,field):
    with pytest.raises(mediaformats.FormatError):inspect(tmp_path,webm(track_extra=field))


@pytest.mark.parametrize('bad', [b'\x01',b'\x7f',b'\xff',b'en\0hidden'])
def test_ascii_string_type_boundaries(tmp_path,bad):
    with pytest.raises(mediaformats.FormatError):inspect(tmp_path,webm(track_extra=element(0x22b59c,bad)))


def test_root_zero_void_and_printable_terminated_strings(tmp_path):
    raw=webm(track_extra=element(0x22b59c,b'~\0\0'))+element(0xec,bytes(4))
    assert inspect(tmp_path,raw).kind=='audio'


def test_declared_max_size_width_enforced(tmp_path):
    raw=webm(extra=element(0xec,bytes(256))).replace(uint(0x42f3,8),uint(0x42f3,1),1)
    with pytest.raises(mediaformats.FormatError):inspect(tmp_path,raw)


def test_large_text_and_tag_name_are_streamed(tmp_path,monkeypatch):
    from thth.audioformats import Reader
    calls=[];original=Reader.read
    def bounded(self,a,n):calls.append(n);assert n<=65536;return original(self,a,n)
    monkeypatch.setattr(Reader,'read',bounded)
    raw=webm(extra=tags('Q'*200000,'private'*200000))
    assert inspect(tmp_path,raw).kind=='audio' and max(calls)<=65536
    with pytest.raises(mediaformats.FormatError,match='location_metadata_present'):
        inspect(tmp_path,webm(extra=tags('Q'*200000+'GPSLatitude','1')))


def test_coded_payload_is_not_metadata_text_scanned(tmp_path):
    assert inspect(tmp_path,webm(data=element(0xa3,block(b'\x80GPSLatitude=12')))).kind=='audio'


def test_unknown_result_new_process_no_replay(env,wire):
    import subprocess,sys,json
    from thth import core
    _,adapter,_=env;fm=configure(env,wire,webm());factory=lambda *a:adapter
    dry=core.send_once('alpha',text='',media_rows=fm['media'],production_flag=False,adapter_factory=factory,log=lambda _:None)
    wire['status']=(500,{})
    sent=core.send_once('alpha',text='',media_rows=fm['media'],production_flag=True,confirm=dry.digest,adapter_factory=factory,log=lambda _:None)
    assert sent.action=='inflight';before=len(wire['calls'])
    script='''from thth import core,accounts
from thth.adapters.mastodon import MastodonAdapter
import sys,json
accounts.load_account=lambda _:json.loads(sys.argv[3])
accounts.load_token=lambda _:{'access_token':'synthetic-token'}
adapter=MastodonAdapter(instance=sys.argv[1],access_token='synthetic-token')
r=core.send_once('alpha',text='',media_rows=[{'file':'sound.webm','alt':'音声'}],production_flag=True,confirm=sys.argv[2],adapter_factory=lambda *a:adapter,log=lambda _:None)
assert r.action=='inflight'
'''
    for _ in range(2):
        child=subprocess.run([sys.executable,'-B','-c',script,wire['url'],dry.digest,json.dumps(env[0])],capture_output=True,timeout=15)
        assert child.returncode==0,child.stderr
    assert len(wire['calls'])==before
