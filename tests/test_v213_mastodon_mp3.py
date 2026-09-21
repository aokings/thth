"""Synthetic LayerIII envelopes and ID3 metadata: no playback/decoder oracle."""
import email.parser,email.policy,hashlib,os,struct
import pytest
from thth import media,mediaformats
from tests.test_v213_mastodon_media import env,wire,invoke,posts
from tests.test_v213_media_foundation import png,jpeg
from tests.test_v213_media_formats import segment,exif


def sync(n):return bytes([(n>>21)&127,(n>>14)&127,(n>>7)&127,n&127])
def tag_frame(name,value,version=4,flags=b'\0\0'):
    return name+(sync(len(value)) if version==4 else len(value).to_bytes(4,'big'))+flags+value

def tag(*frames,version=4,flags=0,padding=0):
    data=b''.join(frames)+bytes(padding);head=b'ID3'+bytes([version,0,flags])+sync(len(data))
    return head+data+(b'3DI'+head[3:] if flags&16 else b'')

def text(value,encoding=3):
    return bytes([encoding])+value.encode({0:'latin1',1:'utf-16',2:'utf-16-be',3:'utf-8'}[encoding])

def apic(data=None,mime=b'image/png',description=b'',kind=3,version=4):
    return tag_frame(b'APIC',b'\0'+mime+b'\0'+bytes([kind])+description+b'\0'+(png() if data is None else data),version)

def crc(raw):
    value=65535
    for bit in ''.join(format(x,'08b') for x in raw):
        feedback=(value>>15)^int(bit);value=(value<<1)&65535
        if feedback:value^=0x8005
    return value.to_bytes(2,'big')

def frame(*,version=3,index=9,rate_index=0,channels=1,padding=0,protected=False,back=0,partbits=0,payload=b'',side_override=None):
    header=(0x7ff<<21)|(version<<19)|(1<<17)|((not protected)<<16)|(index<<12)|(rate_index<<10)|(padding<<9)|((3 if channels==1 else 0)<<6)
    raw=header.to_bytes(4,'big');lower=version!=3;rate=(44100,48000,32000)[rate_index]>>(0 if version==3 else 1 if version==2 else 2)
    bitrate=([0,8,16,24,32,40,48,56,64,80,96,112,128,144,160] if lower else [0,32,40,48,56,64,80,96,112,128,160,192,224,256,320])[index]
    length=bitrate*(72000 if lower else 144000)//rate+padding
    prefix=format(back,'08b' if lower else '09b')+'0'*(channels if lower else (5 if channels==1 else 3)+4*channels)
    parts=(1 if lower else 2)*channels
    side_bits=prefix+''.join(format(partbits if i==0 else 0,'012b')+'0'*(51 if lower else 47) for i in range(parts))
    assert len(side_bits)%8==0;side=int(side_bits,2).to_bytes(len(side_bits)//8,'big') if side_override is None else side_override
    data=raw+(crc(raw[2:]+side) if protected else b'')+side
    assert len(payload)<=length-len(data)
    return data+payload+bytes(length-len(data)-len(payload))


def inspect(tmp_path,raw):
    path=tmp_path/'sound.mp3';path.write_bytes(raw);fd=os.open(path,os.O_RDONLY)
    try:return mediaformats.inspect(fd,len(raw))
    finally:os.close(fd)


def configure(env,wire,raw,mime='audio/mpeg'):
    (env[2]/'sound.mp3').write_bytes(raw)
    wire['caps']['configuration']['media_attachments'].update(supported_mime_types=[mime],image_size_limit=1,image_matrix_limit=None,video_size_limit=len(raw)+1,video_matrix_limit=None,video_frame_rate_limit=None)
    wire['upload']=[(200,{'id':'7','type':'audio','url':'https://instance.invalid/audio'})]
    return {'media':[{'file':'sound.mp3','alt':'音声の説明'}]}


@pytest.mark.parametrize('version',[0,2,3])
@pytest.mark.parametrize('channels',[1,2])
@pytest.mark.parametrize('protected',[False,True])
def test_frames_versions_channels_crc_and_duration(tmp_path,version,channels,protected):
    raw=frame(version=version,channels=channels,protected=protected)
    row=inspect(tmp_path,raw);rate=44100/(1 if version==3 else 2 if version==2 else 4)
    assert row.format=='mp3' and row.kind=='audio' and row.width is row.height is None
    assert row.duration==(1152 if version==3 else 576)/rate and row.public_bytes is None


@pytest.mark.parametrize('version',[3,4])
@pytest.mark.parametrize('mime',['audio/mpeg','audio/mp3'])
def test_actual_multipart_metadata_cover_same_bytes(env,wire,version,mime):
    raw=tag(tag_frame(b'TIT2',text('PRIVATE TITLE',0),version),tag_frame(b'COMM',b'\0engordinary\0GPSLatitude is prose',version),apic(version=version),version=version)+frame()
    fm=configure(env,wire,raw,mime);result,journal,m=invoke(env,fm)
    assert result.post_id and journal['media']['phase']=='published'
    row=m['files'][0];assert row['source_sha256']==row['public_sha256']==hashlib.sha256(raw).hexdigest()
    assert 'non_location_metadata_retained' in media.display(m) and 'embedded_cover_retained' in media.display(m) and 'PRIVATE TITLE' not in media.display(m)
    call=posts(wire,'/api/v2/media')[0];message=email.parser.BytesParser(policy=email.policy.default).parsebytes(('Content-Type: '+call[3]['Content-Type']+'\r\n\r\n').encode()+call[2]);parts={x.get_param('name',header='content-disposition'):x for x in message.iter_parts()}
    assert parts['file'].get_payload(decode=True)==raw and parts['file'].get_content_type()==mime
    assert parts['description'].get_payload(decode=True).decode()=='音声の説明'


@pytest.mark.parametrize('encoding',[0,1,2,3])
def test_encodings_scalar_and_user_text(tmp_path,encoding):
    term=bytes(2 if encoding in (1,2) else 1)
    desc=text('CUSTOM',encoding);value=text('artist',encoding)[1:]
    raw=tag(tag_frame(b'TIT2',text('title',encoding)),tag_frame(b'TXXX',desc+term+value))+frame()
    assert inspect(tmp_path,raw).metadata_notes==('non_location_metadata_retained',)


@pytest.mark.parametrize('tail',[b'TAG'+bytes(125),tag(tag_frame(b'TIT2',text('tail')),flags=16)])
def test_known_trailing_tags(tmp_path,tail):
    raw=frame()+tail;assert inspect(tmp_path,raw).duration==1152/44100


def test_footer_padding_urls_and_counters(tmp_path):
    assert inspect(tmp_path,tag(tag_frame(b'PCNT',bytes(4)),flags=16)+frame()).kind=='audio'
    assert inspect(tmp_path,tag(tag_frame(b'WOAR',b'https://never-fetch.invalid/one'),tag_frame(b'WOAR',b'https://never-fetch.invalid/two'),padding=31)+frame()).kind=='audio'


@pytest.mark.parametrize('key',[b'GPSLatitude',b'gps_longitude',b'LOCATION',b'com.apple.quicktime.location.ISO6709',b'latitude',b'GPSPosition',b'exif:GPSCoordinates'])
def test_known_location_refuses_before_http(env,wire,key):
    fm=configure(env,wire,tag(tag_frame(b'TXXX',b'\0'+key+b'\0' +b'12,34'))+frame())
    with pytest.raises(media.MediaError,match='location_metadata_present'):media.manifest_for(fm,env[0])
    assert not wire['calls']


@pytest.mark.parametrize('frame_id',[b'PRIV',b'GEOB',b'CHAP',b'LINK',b'XABC',b'TXYZ'])
def test_uninspected_metadata_is_not_silently_skipped(env,wire,frame_id):
    raw=tag(tag_frame(frame_id,b'com.example.timestamp\0'+bytes(8)))+frame();fm=configure(env,wire,raw)
    with pytest.raises(media.MediaError,match='location_metadata_unverifiable'):media.manifest_for(fm,env[0])
    assert not wire['calls'] and (env[2]/'sound.mp3').read_bytes()==raw


@pytest.mark.parametrize('payload',[b'\0XMP\0opaque',b'\0EXIF\0opaque',b'\0COVERART\0encoded'])
def test_opaque_user_extension_refuses(tmp_path,payload):
    with pytest.raises(mediaformats.FormatError,match='location_metadata_unverifiable'):inspect(tmp_path,tag(tag_frame(b'TXXX',payload))+frame())


@pytest.mark.parametrize('raw',[
    tag(tag_frame(b'TIT2',text('test')),flags=128)+frame(),
    tag(tag_frame(b'TIT2',text('test')),flags=64)+frame(),
    tag(tag_frame(b'TIT2',text('test')),flags=32)+frame(),
    tag(tag_frame(b'TIT2',text('test'),flags=b'\0\x02'))+frame(),
    tag(tag_frame(b'TIT2',text('test'),flags=b'\0\x08'))+frame(),
    b'ID3\x02\0\0'+sync(4)+bytes(4)+frame(),
    frame()+b'APETAGEX'+bytes(24),frame()+b'TAG+'+bytes(223),
])
def test_uninspected_transform_and_trailers(tmp_path,raw):
    with pytest.raises(mediaformats.FormatError,match='location_metadata_unverifiable'):inspect(tmp_path,raw)


@pytest.mark.parametrize('data,mime,reason',[
    (jpeg()[:2]+segment(0xe1,b'Exif\0\0'+exif())+jpeg()[2:],b'image/jpeg','location_metadata_present'),
    (png()[:-12]+mediaformats._chunk(b'eXIf',exif())+png()[-12:],b'image/png','location_metadata_present'),
    (jpeg()[:2]+segment(0xe1,b'http://ns.adobe.com/xap/1.0/\0<x GPSLatitude="12"/>')+jpeg()[2:],b'image/jpeg','location_metadata_present'),
    (b'https://never-fetch.invalid/a',b'-->','location_metadata_unverifiable'),
    (b'BM opaque',b'image/bmp','location_metadata_unverifiable'),
    (png()[:-1],b'image/png','location_metadata_unverifiable'),
])
def test_cover_privacy_before_upload(env,wire,data,mime,reason):
    fm=configure(env,wire,tag(apic(data,mime))+frame())
    with pytest.raises(media.MediaError,match=reason):media.manifest_for(fm,env[0])
    assert not wire['calls']


@pytest.mark.parametrize('raw',[
    frame()[:-1],frame()+b'junk',frame(back=1),frame(partbits=4095),
    tag(tag_frame(b'TIT2',b'\x03\xff'))+frame(),tag(tag_frame(b'TIT2',b'\x01no BOM'))+frame(),
    tag(tag_frame(b'TXXX',b'\0unterminated'))+frame(),
    tag(tag_frame(b'TIT2',text('one')),tag_frame(b'TIT2',text('two')))+frame(),
    tag(apic(),apic())+frame(),tag(apic(kind=2),apic(description=b'other',kind=2))+frame(),
    tag(apic(kind=1))+frame(),tag(tag_frame(b'TIT2',text('x')),flags=16,padding=1)+frame(),
    tag(tag_frame(b'TIT2',text('x')))[:-1],
])
def test_malformed_metadata_or_frame_bounds_refuse(tmp_path,raw):
    with pytest.raises(mediaformats.FormatError):inspect(tmp_path,raw)


@pytest.mark.parametrize('offset',[4,6,2])
def test_protected_crc_corruption(tmp_path,offset):
    raw=bytearray(frame(protected=True));raw[offset]^=1
    with pytest.raises(mediaformats.FormatError):inspect(tmp_path,raw)


def test_reservoir_vbr_and_sample_prose(tmp_path):
    raw=frame(index=9,payload=b'GPSLatitude XMP location')+frame(index=10,back=100,partbits=400)
    assert inspect(tmp_path,raw).duration==2304/44100
    # Backref would reuse already consumed bits rather than merely prior frame data.
    bad=frame(partbits=3000)+frame(back=100)
    with pytest.raises(mediaformats.FormatError):inspect(tmp_path,bad)


def test_sample_area_is_not_read_as_metadata(tmp_path,monkeypatch):
    from thth import mpeg_audio
    raw=frame()*200;reads=[];orig=mpeg_audio.Reader.read
    def observe(self,a,n):reads.append(n);return orig(self,a,n)
    monkeypatch.setattr(mpeg_audio.Reader,'read',observe)
    assert inspect(tmp_path,raw).duration==200*1152/44100
    assert max(reads)<=32 and sum(reads)<len(raw)//4


@pytest.mark.parametrize('change,reason',[({'supported_mime_types':['audio/mp4']},'unsupported_attachment'),({'video_size_limit':1},'media_limit_exceeded'),({'video_size_limit':None},'media_capability_unavailable')])
def test_latest_instance_caps(env,wire,change,reason):
    fm=configure(env,wire,frame());wire['caps']['configuration']['media_attachments'].update(change)
    result,_,_=invoke(env,fm);assert reason in result.error and not posts(wire,'/api/v2/media')


def test_cover_change_stale_after_upload_holds_id(env,wire):
    fm=configure(env,wire,tag(apic())+frame());before=media.prepared_component(media.manifest_for(fm,env[0]))
    wire['upload']=[(202,{'id':'7','type':'audio','url':None})];wire['poll']=[(200,{'id':'7','type':'audio','url':'https://instance.invalid/a'})]
    wire['on_poll']=lambda:(env[2]/'sound.mp3').write_bytes(tag(apic(jpeg(),b'image/jpeg'))+frame())
    result,journal,_=invoke(env,fm);assert result.failure=='media_held' and journal['media']['remote_ids']==['7'] and not posts(wire,'/api/v1/statuses')
    assert before!=media.prepared_component(media.manifest_for(fm,env[0]))


def test_unknown_publication_durable_no_retry(env,wire):
    from thth import core
    _,adapter,_=env;fm=configure(env,wire,frame());factory=lambda *args:adapter
    first=core.send_once('alpha',text='',media_rows=fm['media'],production_flag=False,adapter_factory=factory,log=lambda value:None);wire['status']=(500,{})
    result=core.send_once('alpha',text='',media_rows=fm['media'],production_flag=True,confirm=first.digest,adapter_factory=factory,log=lambda value:None)
    assert result.action=='inflight' and len(posts(wire,'/api/v2/media'))==1
    retry=core.send_once('alpha',text='',media_rows=fm['media'],production_flag=True,confirm=first.digest,adapter_factory=factory,log=lambda value:None)
    assert retry.action=='inflight' and len(posts(wire,'/api/v2/media'))==1


@pytest.mark.parametrize('mask,value,reason',[
    (3<<19,1<<19,'invalid_attachment_structure'),
    # Layer 00 with the 0xFFF syncword is ADTS AAC, named before the MP3 parse.
    (3<<17,0,'unsupported_attachment: aac_adts'),
    (3<<17,2<<17,'unsupported_attachment_structure: mpeg layer'),
    (15<<12,0,'unsupported_attachment_structure: mp3 free bitrate'),
    (15<<12,15<<12,'invalid_attachment_structure'),(3<<10,3<<10,'invalid_attachment_structure'),
    (3,2,'invalid_attachment_structure'),
])
def test_header_reserved_and_unsupported_are_distinct(tmp_path,mask,value,reason):
    raw=frame();header=(int.from_bytes(raw[:4],'big')&~mask)|value
    with pytest.raises(mediaformats.FormatError,match=reason):inspect(tmp_path,header.to_bytes(4,'big')+raw[4:])


def test_id3_declared_tlen_does_not_override_observed_duration(tmp_path):
    raw=tag(tag_frame(b'TLEN',text('0')))+frame()+frame(index=10)
    assert inspect(tmp_path,raw).duration==2304/44100


def test_frame_size_and_header_footer_mismatch(tmp_path):
    raw=bytearray(tag(tag_frame(b'TIT2',text('text')),flags=16)+frame());raw[15]=127
    with pytest.raises(mediaformats.FormatError):inspect(tmp_path,raw)
    raw=bytearray(tag(tag_frame(b'TIT2',text('text')),flags=16)+frame());end=10+sum(x<<(7*(3-i)) for i,x in enumerate(raw[6:10]));raw[end+5]^=1
    with pytest.raises(mediaformats.FormatError):inspect(tmp_path,raw)


def test_mp3_actual_git_approval_and_source_stale(tmp_path,isolated_account_factory,wire,monkeypatch):
    from pathlib import Path
    from thth import accounts,core
    from thth.adapters.mastodon import MastodonAdapter
    from tests.conftest import init_git_pair,make_queue_text,run_git,approve_via_cli
    text=make_queue_text({'account':'alpha','status':'draft','approved_sha':'','topic':''},body='## mastodon\n\n本文。\n',media='mastodon')
    text=text.replace('\n---\n','\nmedia:\n  - file: sound.mp3\n    alt: 音声\n---\n',1)
    pair=init_git_pair(tmp_path,seed_content=text);repo=Path(pair['work']);path=Path(pair['queue_dir'])/'a.md'
    (repo/'sound.mp3').write_bytes(frame());run_git(str(repo),['add','.']);run_git(str(repo),['commit','-m','synthetic MP3']);run_git(str(repo),['push'])
    isolated_account_factory(name='alpha',repo_dir=str(repo),media='mastodon',instance=wire['url'],production=True,hashtags=False,char_limit=500,quiet_hours=None,min_interval_hours=0)
    wire['caps']['configuration']['media_attachments']['supported_mime_types']=['audio/mpeg']
    approved=approve_via_cli(path);assert approved.returncode==0,approved.stderr
    monkeypatch.setattr(accounts,'load_token',lambda _: {'access_token':'synthetic-token'})
    adapter=MastodonAdapter(instance=wire['url'],access_token='synthetic-token')
    (repo/'sound.mp3').write_bytes(frame(index=10));run_git(str(repo),['add','.']);run_git(str(repo),['commit','-m','changed source']);run_git(str(repo),['push'])
    stale=core.throw_once('alpha',production_flag=True,adapter_factory=lambda *a:adapter,log=lambda _:None,bypass_pace=True)
    assert not posts(wire,'/api/v2/media') and not posts(wire,'/api/v1/statuses')
    approved=approve_via_cli(path);assert approved.returncode==0,approved.stderr
    wire['upload']=[(200,{'id':'7','type':'audio','url':'https://instance.invalid/a'})]
    core.throw_once('alpha',production_flag=True,adapter_factory=lambda *a:adapter,log=lambda _:None,bypass_pace=True)
    assert len(posts(wire,'/api/v2/media'))==len(posts(wire,'/api/v1/statuses'))==1


def test_v2_manifest_and_source_change_digest(env,wire,monkeypatch):
    from thth import cli,lint,bundle,threadrun
    import json
    cfg,_,repo=env;fm=configure(env,wire,frame())
    raw='---\nthth: 2\naccount: alpha\npublish_at: 2030-01-01T12:00:00+09:00\ncontinue_until: 2030-01-01T13:00:00+09:00\nstatus: draft\nposts:\n'
    raw+='  - index: 1\n    media:\n      - file: sound.mp3\n        alt: 音声の説明\n  - index: 2\n---\n## mastodon\n音声\n<!-- thth: 2/2 -->\n続き\n'
    path=repo/'bundle.md';path.write_text(raw)
    monkeypatch.setattr(threadrun,'unreadable_runs',lambda:[]);monkeypatch.setattr(threadrun,'find_latest',lambda *a:None)
    assert not bundle.parse(str(path)).malformed
    assert not [x for x in lint.lint_file(str(path)) if not lint.is_warning(x)]
    prepared,reason=cli._prepare_one(str(path));assert reason is None and prepared['media_manifests'][0]['files'][0]['format']=='mp3'
    (repo/'sound.mp3').write_bytes(frame(index=10));other,reason=cli._prepare_one(str(path))
    assert reason is None and other['approved_sha']!=prepared['approved_sha']
    assert not posts(wire,'/api/v2/media') and not posts(wire,'/api/v1/statuses')
