"""Synthetic WebM video container timing and typed metadata, no codec execution."""
import os,struct,hashlib,email.parser,email.policy
from fractions import Fraction
import pytest
from thth import media,mediaformats
from tests.test_v213_mastodon_webm_audio import element,uint,text,master,vint,tags,cover,webm as audio_webm
from tests.test_v213_mastodon_ogg_opus import head as opus_head
from tests.test_v213_mastodon_media import env,wire,invoke,posts
from tests.test_v213_media_foundation import png
from tests.test_v213_media_formats import exif


def video(*,codec='V_VP8',private=None,video_extra=b'',extra=b'',audio=False,default=40000000,times=(0,40,80),last_duration=None,declared=120,unknown=False,cluster_data=None):
    header=master(0x1a45dfa3,uint(0x4286,1),uint(0x42f7,1),uint(0x42f2,4),uint(0x42f3,8),text(0x4282,'webm'),uint(0x4287,4),uint(0x4285,2))
    info=master(0x1549a966,uint(0x2ad7b1,1000000),b'' if declared is None else element(0x4489,struct.pack('>d',declared)),text(0x4d80,'synthetic'),text(0x5741,'fixture'))
    track=master(0xae,uint(0xd7,1),uint(0x73c5,1),uint(0x83,1),text(0x86,codec),b'' if private is None else element(0x63a2,private),b'' if default is None else uint(0x23e383,default),master(0xe0,uint(0xb0,640),uint(0xba,480),video_extra))
    if audio:track+=master(0xae,uint(0xd7,2),uint(0x73c5,2),uint(0x83,2),text(0x86,'A_OPUS'),element(0x63a2,opus_head()),master(0xe1,element(0xb5,struct.pack('>d',48000)),uint(0x9f,1)))
    blocks=[]
    for i,tick in enumerate(times):
        data=vint(1)+struct.pack('>h',tick)+(b'\0' if last_duration is not None and i==len(times)-1 else b'\x80')+b'\x10\x00\x00\x9d\x01\x2a\x80\x02\xe0\x01'
        blocks.append(master(0xa0,element(0xa1,data),uint(0x9b,last_duration)) if last_duration is not None and i==len(times)-1 else element(0xa3,data))
    if audio:blocks.append(element(0xa3,vint(2)+b'\0\0\x80\x80x'))
    segment=info+master(0x1654ae6b,track)+(master(0x1f43b675,uint(0xe7,0),*blocks) if cluster_data is None else cluster_data)+extra
    return header+(b'\x18\x53\x80\x67\xff'+segment if unknown else element(0x18538067,segment))


def observe(tmp_path,raw,metrics=False):
    p=tmp_path/'a.webm';p.write_bytes(raw);fd=os.open(p,os.O_RDONLY)
    try:return mediaformats.video_metrics(fd,len(raw)) if metrics else mediaformats.inspect(fd,len(raw))
    finally:os.close(fd)


def configure(env,wire,raw):
    (env[2]/'video.webm').write_bytes(raw);wire['caps']['configuration']['media_attachments'].update(supported_mime_types=['video/webm','image/png'],video_size_limit=len(raw)+1,video_matrix_limit=640*480,video_frame_rate_limit=30)
    wire['upload']=[(200,{'id':'7','type':'video','url':'https://instance.invalid/video'})]
    return {'media':[{'file':'video.webm','alt':'動画'}]}


@pytest.mark.parametrize('codec,private',[('V_VP8',None),('V_VP8',b''),('V_VP9',None),('V_VP9',b'\x01\x01\x00\x02\x01\x1e\x03\x01\x08\x04\x01\x01')])
@pytest.mark.parametrize('audio',[False,True])
def test_same_video_bytes_actual_wire(env,wire,codec,private,audio):
    raw=video(codec=codec,private=private,audio=audio);fm=configure(env,wire,raw);result,journal,m=invoke(env,fm)
    assert result.post_id and journal['media']['phase']=='published';row=m['files'][0]
    assert row['kind']=='video' and row['width']==640 and row['height']==480 and row['duration']==.12
    assert row['source_sha256']==row['public_sha256']==hashlib.sha256(raw).hexdigest()
    call=posts(wire,'/api/v2/media')[0];msg=email.parser.BytesParser(policy=email.policy.default).parsebytes(('Content-Type: '+call[3]['Content-Type']+'\r\n\r\n').encode()+call[2]);part=next(p for p in msg.iter_parts() if p.get_param('name',header='content-disposition')=='file')
    assert part.get_content_type()=='video/webm' and part.get_payload(decode=True)==raw


@pytest.mark.parametrize('private',[b'\x05\x01\0',b'\x81\x01\0',b'\x01\x02\0\0',b'\x01\x01',b'\x01\x01\x04',b'\x03\x01\x09'])
def test_bad_or_unverified_private_refuses_before_http(env,wire,private):
    fm=configure(env,wire,video(codec='V_VP9',private=private))
    with pytest.raises(media.MediaError):media.manifest_for(fm,env[0])
    assert not wire['calls']


def test_vp8_unknown_private_and_unchecked_codec(tmp_path):
    for raw in [video(private=b'XMP GPS'),video(codec='V_AV1')]:
        with pytest.raises(mediaformats.FormatError):observe(tmp_path,raw)


def test_known_private_duplicate_and_audio_legacy(tmp_path):
    assert observe(tmp_path,video(codec='V_VP9',private=b'\x01\x01\0'*2)).kind=='video'
    assert observe(tmp_path,audio_webm()).kind=='audio'


@pytest.mark.parametrize('times,default,last,expected', [((0,40,80),40000000,None,Fraction(25)),((0,10,50),None,50,Fraction(30)),((0,10,50),None,47,Fraction(3000,97)),((80,0,40),40000000,None,Fraction(25))])
def test_average_timing_not_instantaneous(tmp_path,times,default,last,expected):
    assert observe(tmp_path,video(times=times,default=default,last_duration=last),True)==(640,480,expected)


def test_unknown_timing_not_invented_from_info_duration(env,wire):
    fm=configure(env,wire,video(default=None,declared=120));result,_,m=invoke(env,fm)
    assert m['files'][0]['duration']==.12 and result.error=='video_rate_unverifiable' and not posts(wire,'/api/v2/media')


def test_missing_duration_observation_remains_unknown(tmp_path):
    assert observe(tmp_path,video(declared=None)).duration is None


@pytest.mark.parametrize('cap,success',[(30,True),(29,False)])
def test_fresh_fraction_floor(env,wire,cap,success):
    fm=configure(env,wire,video(times=(0,10,50),default=None,last_duration=47));wire['caps']['configuration']['media_attachments']['video_frame_rate_limit']=cap
    result,_,_=invoke(env,fm);assert bool(result.post_id)==success
    if not success:assert not posts(wire,'/api/v2/media')


@pytest.mark.parametrize('extra',[tags('GPSLatitude','1'),element(0xec,b'x'),element(0x4abc,b'opaque'),cover(png()[:-12]+mediaformats._chunk(b'eXIf',exif())+png()[-12:])])
def test_existing_privacy_gates_apply_to_video(env,wire,extra):
    fm=configure(env,wire,video(extra=extra))
    with pytest.raises(media.MediaError,match='location_metadata'):media.manifest_for(fm,env[0])
    assert not wire['calls']


def test_colour_structures_retained_and_unrecognized_rejected(tmp_path):
    colour=master(0x55b0,uint(0x55b1,1),uint(0x55b2,8),master(0x55d0,element(0x55d1,struct.pack('>d',.5))))
    assert observe(tmp_path,video(video_extra=colour,unknown=True)).kind=='video'
    with pytest.raises(mediaformats.FormatError,match='location_metadata_unverifiable'):observe(tmp_path,video(video_extra=master(0x55b0,element(0x55be,b'unknown'))))



def test_declared_default_rate_precedes_timeline_average(tmp_path):
    # A VFR timeline must not override the demuxer-declared average.
    assert observe(tmp_path,video(times=(0,10,50),last_duration=10),True)[2]==25


@pytest.mark.parametrize('lace',[0,1,2,3])
def test_video_lacing_and_multicluster_signed_time(tmp_path,lace):
    from tests.test_v213_mastodon_webm_audio import block
    packets=[b'coded-a'] if not lace else [b'coded-a',b'coded-b',b'coded-c']
    payload=block(*packets,lace=lace,simple=False)
    first=master(0x1f43b675,uint(0xe7,10),master(0xa0,element(0xa1,payload[:1]+struct.pack('>h',-10)+payload[3:]),uint(0x9b,len(packets)*40)))
    second=master(0x1f43b675,uint(0xe7,len(packets)*40),master(0xa0,element(0xa1,block(b'coded-z',simple=False)),uint(0x9b,40)))
    assert observe(tmp_path,video(default=None,cluster_data=first+second),True)==(640,480,Fraction(25))


def test_coded_body_is_not_accumulated_or_text_scanned(tmp_path,monkeypatch):
    from thth.audioformats import Reader
    from tests.test_v213_mastodon_webm_audio import block
    original=Reader.read;sizes=[]
    def read(self,a,n):sizes.append(n);assert n<=65536;return original(self,a,n)
    monkeypatch.setattr(Reader,'read',read)
    cluster=master(0x1f43b675,uint(0xe7,0),element(0xa3,block(b'GPSLatitude'+bytes(1900000))))
    assert observe(tmp_path,video(cluster_data=cluster)).kind=='video'
    assert max(sizes)<=65536 and sum(sizes)<4096


@pytest.mark.parametrize('field,value',[('video_matrix_limit',640*480-1),('video_frame_rate_limit',24),('video_size_limit',1),('supported_mime_types',['audio/webm'])])
def test_fresh_video_caps_upload_zero(env,wire,field,value):
    fm=configure(env,wire,video());wire['caps']['configuration']['media_attachments'][field]=value
    result,_,_=invoke(env,fm);assert result.error and not posts(wire,'/api/v2/media') and not posts(wire,'/api/v1/statuses')


def test_video_parent_thumbnail_keeps_roles_and_bytes(env,wire):
    from tests.test_v213_mastodon_thumbnail import parts
    raw=video();fm=configure(env,wire,raw);(env[2]/'thumb.png').write_bytes(png())
    fm['media'][0].update(thumbnail_file='thumb.png',thumbnail_alt='approval only')
    wire['caps']['configuration']['media_attachments'].update(image_size_limit=len(png())+1,image_matrix_limit=1)
    result,_,manifest=invoke(env,fm);assert result.post_id and len(result.media)==1
    fields=parts(posts(wire,'/api/v2/media')[0]);assert fields[-2].get_payload(decode=True)==raw and fields[-1].get_payload(decode=True)==png()
    assert manifest['files'][1]['parent']==1 and b'approval only' not in posts(wire,'/api/v2/media')[0][2]


def test_video_source_changed_holds_known_id(env,wire):
    fm=configure(env,wire,video());wire['upload']=[(202,{'id':'7','type':'video','url':None})];wire['poll']=[(200,{'id':'7','type':'video','url':'https://instance.invalid/v'})]
    wire['on_poll']=lambda:(env[2]/'video.webm').write_bytes(video(extra=tags('TITLE','changed')))
    result,journal,_=invoke(env,fm);assert result.failure=='media_held' and journal['media']['remote_ids']==['7'] and not posts(wire,'/api/v1/statuses')


def test_video_unknown_new_process_no_replay(env,wire):
    import subprocess,sys,json
    from thth import core
    cfg,adapter,_=env;fm=configure(env,wire,video());factory=lambda *a:adapter
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


def test_video_actual_git_approval_and_source_stale(tmp_path,isolated_account_factory,wire,monkeypatch):
    from pathlib import Path
    from thth import accounts,core
    from thth.adapters.mastodon import MastodonAdapter
    from tests.conftest import init_git_pair,make_queue_text,run_git,approve_via_cli
    text=make_queue_text({'account':'alpha','status':'draft','approved_sha':'','topic':''},body='## mastodon\n\n本文。\n',media='mastodon')
    text=text.replace('\n---\n','\nmedia:\n  - file: video.webm\n    alt: 音声\n---\n',1)
    pair=init_git_pair(tmp_path,seed_content=text);repo=Path(pair['work']);path=Path(pair['queue_dir'])/'a.md'
    (repo/'video.webm').write_bytes(video());run_git(str(repo),['add','.']);run_git(str(repo),['commit','-m','synthetic WebM']);run_git(str(repo),['push'])
    isolated_account_factory(name='alpha',repo_dir=str(repo),media='mastodon',instance=wire['url'],production=True,hashtags=False,char_limit=500,quiet_hours=None,min_interval_hours=0)
    wire['caps']['configuration']['media_attachments']['supported_mime_types']=['video/webm']
    approved=approve_via_cli(path);assert approved.returncode==0,approved.stderr
    monkeypatch.setattr(accounts,'load_token',lambda _: {'access_token':'synthetic-token'})
    adapter=MastodonAdapter(instance=wire['url'],access_token='synthetic-token')
    (repo/'video.webm').write_bytes(video(extra=tags('TITLE','changed')));run_git(str(repo),['add','.']);run_git(str(repo),['commit','-m','changed source']);run_git(str(repo),['push'])
    stale=core.throw_once('alpha',production_flag=True,adapter_factory=lambda *a:adapter,log=lambda _:None,bypass_pace=True)
    assert not posts(wire,'/api/v2/media') and not posts(wire,'/api/v1/statuses')
    approved=approve_via_cli(path);assert approved.returncode==0,approved.stderr
    wire['upload']=[(200,{'id':'7','type':'video','url':'https://instance.invalid/a'})]
    core.throw_once('alpha',production_flag=True,adapter_factory=lambda *a:adapter,log=lambda _:None,bypass_pace=True)
    assert len(posts(wire,'/api/v2/media'))==len(posts(wire,'/api/v1/statuses'))==1


def test_video_v2_manifest_and_source_change_digest(env,wire,monkeypatch):
    from thth import cli,lint,bundle,threadrun
    import json
    cfg,_,repo=env;fm=configure(env,wire,video())
    raw='---\nthth: 2\naccount: alpha\npublish_at: 2030-01-01T12:00:00+09:00\ncontinue_until: 2030-01-01T13:00:00+09:00\nstatus: draft\nposts:\n'
    raw+='  - index: 1\n    media:\n      - file: video.webm\n        alt: 音声の説明\n  - index: 2\n---\n## mastodon\n音声\n<!-- thth: 2/2 -->\n続き\n'
    path=repo/'bundle.md';path.write_text(raw)
    monkeypatch.setattr(threadrun,'unreadable_runs',lambda:[]);monkeypatch.setattr(threadrun,'find_latest',lambda *a:None)
    assert not bundle.parse(str(path)).malformed
    assert not [x for x in lint.lint_file(str(path)) if not lint.is_warning(x)]
    prepared,reason=cli._prepare_one(str(path));assert reason is None and prepared['media_manifests'][0]['files'][0]['format']=='webm'
    (repo/'video.webm').write_bytes(video(extra=tags('TITLE','changed')));other,reason=cli._prepare_one(str(path))
    assert reason is None and other['approved_sha']!=prepared['approved_sha']
    assert not posts(wire,'/api/v2/media') and not posts(wire,'/api/v1/statuses')





def test_video_audio_fields_must_match_track_type(tmp_path):
    raw=video().replace(uint(0x83,1),uint(0x83,2),1)
    with pytest.raises(mediaformats.FormatError):observe(tmp_path,raw)


def test_multiple_video_tracks_choose_first_without_merging_timing(tmp_path):
    from thth.ebmlaudio import WebM
    from thth.audioformats import Reader
    raw=video();path=tmp_path/'parse';path.write_bytes(raw);fd=os.open(path,os.O_RDONLY)
    try:
        reader=WebM(Reader(fd,len(raw)));roots=list(reader.elements(0,len(raw)));parts=list(reader.elements(roots[1][1],roots[1][2]))
    finally:os.close(fd)
    before=raw[:roots[1][3]];children=[]
    for ident,a,b,_ in parts:
        data=raw[a:b]
        if ident==0x1654ae6b:
            data+=master(0xae,uint(0xd7,2),uint(0x73c5,2),uint(0x83,1),text(0x86,'V_VP9'),uint(0x23e383,20000000),master(0xe0,uint(0xb0,1280),uint(0xba,720)))
        if ident==0x1f43b675:data+=element(0xa3,vint(2)+b'\0\0\x80coded')
        children.append(element(ident,data))
    both=before+element(0x18538067,b''.join(children));assert observe(tmp_path,both,True)==(640,480,Fraction(25))



def test_declared_rate_rational_rounding_floor_boundary(env,wire):
    fm=configure(env,wire,video(default=33333334));wire['caps']['configuration']['media_attachments']['video_frame_rate_limit']=29
    result,_,_=invoke(env,fm);assert result.error=='media_limit_exceeded: frame_rate' and not posts(wire,'/api/v2/media')


@pytest.mark.parametrize('value,limit',[(Fraction(314159,100000),11),(Fraction(355,113),25),(Fraction(1,100),5),(Fraction(25,3),5),(Fraction(30),30000)])
def test_bounded_rational_nearest_independent_exhaustive_small_oracle(value,limit):
    from thth.ebmlvideo import _bounded_rate
    got=_bounded_rate(value,limit)
    if limit<100:
        candidates=[Fraction(n,d) for n in range(limit+1) for d in range(1,limit+1)]
        assert abs(got-value)==min(abs(x-value) for x in candidates)
    else:assert got==value
    assert got.numerator<=limit and got.denominator<=limit
