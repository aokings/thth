"""Generated native FLAC frames, independent checksums and local provider wire."""
import email.parser,email.policy,hashlib,os,struct
import pytest
from thth import media,mediaformats
from tests.test_v213_mastodon_media import env,wire,invoke,posts
from tests.test_v213_media_foundation import png,jpeg
from tests.test_v213_media_formats import segment,exif


def crc(raw,bits,poly):
    value=0
    for byte in raw:
        value^=byte<<(bits-8)
        for _ in range(8):value=(value<<1)^((poly if value&(1<<(bits-1)) else 0));value&=(1<<bits)-1
    return value.to_bytes(bits//8,'big')


def bitpack(value):return int(value+'0'*((-len(value))%8),2).to_bytes((len(value)+7)//8,'big')
def bits(value,n):return format(value,f'0{n}b')


def frame(*,kind='constant',samples=16,number=0,variable=False,channels=1,depth=16,rate_code=0,channel_code=None,wasted=0,escape=False):
    cc=channels-1 if channel_code is None else channel_code
    number_bytes=bytes([number]) if number<128 else bytes([0xc0|(number>>6),0x80|(number&63)])
    header=b'\xff'+bytes([0xf9 if variable else 0xf8,0x60|rate_code,(cc<<4)])+number_bytes+bytes([samples-1])
    header+=crc(header,8,7);body=''
    for i in range(channels):
        width=depth+(1 if cc in (8,10) and i==1 or cc==9 and i==0 else 0)-wasted
        t={'constant':0,'verbatim':1,'fixed':8,'lpc':32}[kind]
        body+='0'+bits(t,6)+('1' if wasted else '0')
        if wasted:body+='0'*(wasted-1)+'1'
        if kind=='constant':body+=bits(0,width)
        elif kind=='verbatim':body+=''.join(bits(i% (1<<width),width) for i in range(samples))
        else:
            order=0 if kind=='fixed' else 1
            if order:body+=bits(0,width)+bits(0,4)+bits(0,5)+bits(0,1)
            body+='00'+'0000'
            if escape:body+='1111'+'00000'
            else:body+='0000'+'1'*(samples-order)
    raw=header+bitpack(body);return raw+crc(raw,16,0x8005)


def block(kind,value,last=False):return bytes([kind|(128 if last else 0)])+len(value).to_bytes(3,'big')+value

def flac(*,extra=(),frames=None,total=16,rate=8000,depth=16,channels=1,kind='constant'):
    stream=struct.pack('>HH',16,256)+bytes(6)+((rate<<44)|((channels-1)<<41)|((depth-1)<<36)|total).to_bytes(8,'big')+bytes(16)
    blocks=[(0,stream),*extra]
    return b'fLaC'+b''.join(block(k,v,i==len(blocks)-1) for i,(k,v) in enumerate(blocks))+(frame(kind=kind,depth=depth,channels=channels) if frames is None else frames)


def comments(*fields):
    vendor=b'synthetic encoder';return struct.pack('<I',len(vendor))+vendor+struct.pack('<I',len(fields))+b''.join(struct.pack('<I',len(x))+x for x in fields)


def picture(value,mime=b'image/png',kind=3):
    return struct.pack('>II',kind,len(mime))+mime+struct.pack('>I',0)+bytes(16)+struct.pack('>I',len(value))+value


def configure(env,wire,raw,mime='audio/flac'):
    (env[2]/'sound.flac').write_bytes(raw)
    wire['caps']['configuration']['media_attachments'].update(supported_mime_types=[mime],image_size_limit=1,image_matrix_limit=None,video_size_limit=len(raw),video_matrix_limit=None,video_frame_rate_limit=None)
    wire['upload']=[(200,{'id':'7','type':'audio','url':'https://instance.invalid/audio'})]
    return {'media':[{'file':'sound.flac','alt':'音声の説明'}]}


def inspect(tmp_path,raw):
    p=tmp_path/'sound.flac';p.write_bytes(raw);fd=os.open(p,os.O_RDONLY)
    try:return mediaformats.inspect(fd,len(raw))
    finally:os.close(fd)


@pytest.mark.parametrize('kind',['constant','verbatim','fixed','lpc'])
@pytest.mark.parametrize('mime',['audio/flac','audio/x-flac'])
def test_flac_actual_multipart_keeps_bytes_and_na(env,wire,kind,mime):
    raw=flac(kind=kind,extra=[(4,comments(b'TITLE=PRIVATE TITLE',b'COMMENT=GPSLatitude is ordinary prose')),(6,picture(png()))])
    fm=configure(env,wire,raw,mime);result,journal,m=invoke(env,fm)
    assert result.post_id and journal['media']['phase']=='published'
    row=m['files'][0];assert row['kind']=='audio' and row['format']=='flac' and row['duration']==.002 and row['width'] is row['height'] is None
    assert row['source_sha256']==row['public_sha256']==hashlib.sha256(raw).hexdigest()
    assert 'non_location_metadata_retained' in media.display(m) and 'PRIVATE TITLE' not in media.display(m)
    call=posts(wire,'/api/v2/media')[0];msg=email.parser.BytesParser(policy=email.policy.default).parsebytes(('Content-Type: '+call[3]['Content-Type']+'\r\n\r\n').encode()+call[2]);parts={p.get_param('name',header='content-disposition'):p for p in msg.iter_parts()}
    assert parts['file'].get_payload(decode=True)==raw and parts['file'].get_content_type()==mime
    assert parts['description'].get_payload(decode=True).decode()=='音声の説明'


@pytest.mark.parametrize('kind',['constant','verbatim','fixed','lpc'])
@pytest.mark.parametrize('depth',[4,16,32])
def test_subframes_depth_wasted_escape(tmp_path,kind,depth):
    raw=flac(depth=depth,frames=frame(kind=kind,depth=depth,wasted=1,escape=True))
    assert inspect(tmp_path,raw).duration==.002


@pytest.mark.parametrize('channel_code',[1,8,9,10])
def test_stereo_side_channel_width(tmp_path,channel_code):
    assert inspect(tmp_path,flac(channels=2,frames=frame(channels=2,channel_code=channel_code))).duration==.002


@pytest.mark.parametrize('variable',[False,True])
def test_two_frames_and_unknown_total(tmp_path,variable):
    frames=frame(variable=variable)+frame(number=16 if variable else 1,variable=variable,samples=8)
    assert inspect(tmp_path,flac(frames=frames,total=24)).duration==.003
    assert inspect(tmp_path,flac(frames=frames,total=0)).duration is None


def test_changing_rate_duration_and_unknown_display(env,wire):
    raw=flac(frames=frame()+frame(number=1,rate_code=9),total=32);fm=configure(env,wire,raw)
    row=media.manifest_for(fm,env[0])['files'][0];assert row['duration']==16/8000+16/44100
    fm=configure(env,wire,flac(total=0));m=media.manifest_for(fm,env[0]);assert m['files'][0]['duration'] is None
    assert '秒数: 未取得' in media.display(m) and '寸法: 適用外' in media.display(m)


@pytest.mark.parametrize('extra,reason',[
    ([(2,b'opaque APP')],'location_metadata_unverifiable'), ([(5,bytes(396))],'location_metadata_unverifiable'), ([(7,b'future')],'location_metadata_unverifiable'), ([(127,b'forbidden')],'location_metadata_unverifiable'),
    ([(1,b'private')],'location_metadata_unverifiable'),
    ([(4,comments(b'LOCATION=12,34'))],'location_metadata_present'), ([(4,comments(b'gPsLaTiTuDe=12'))],'location_metadata_present'),
    ([(4,comments(b'LATITUDE=12'))],'location_metadata_present'),
    ([(4,comments(b'METADATA_BLOCK_PICTURE=opaque'))],'location_metadata_unverifiable'),
    ([(4,comments(b'COVERART=opaque'))],'location_metadata_unverifiable'),
    ([(4,comments(b'XMP=<gps/>'))],'location_metadata_unverifiable'),
    ([(4,comments(b'invalid'))],'invalid_attachment_structure'), ([(4,comments(b'\x01bad=value'))],'invalid_attachment_structure'),
    ([(4,comments(b'TITLE=\xff'))],'invalid_attachment_structure'),
    ([(4,comments()),(4,comments())],'invalid_attachment_structure'),
    ([(6,picture(b'https://must-not-fetch.invalid/cover',b'-->'))],'location_metadata_unverifiable'),
    ([(6,picture(b'BM bitmap',b'image/bmp'))],'location_metadata_unverifiable'),
    ([(6,picture(png(),kind=21))],'location_metadata_unverifiable'),
    ([(6,picture(png(),kind=2)),(6,picture(png(),kind=2))],'invalid_attachment_structure'),
    ([(6,picture(png(),kind=1))],'invalid_attachment_structure'),
    ([(6,picture(png()[:-1]))],'location_metadata_unverifiable'),
])
def test_metadata_privacy_structure_before_http(env,wire,extra,reason):
    raw=flac(extra=extra);fm=configure(env,wire,raw)
    with pytest.raises(media.MediaError,match=reason):media.manifest_for(fm,env[0])
    assert not wire['calls'] and (env[2]/'sound.flac').read_bytes()==raw


@pytest.mark.parametrize('cover,mime',[(jpeg()[:2]+segment(0xe1,b'Exif\0\0'+exif())+jpeg()[2:],b'image/jpeg'),(png()[:-12]+mediaformats._chunk(b'eXIf',exif())+png()[-12:],b'image/png'),(jpeg()[:2]+segment(0xe1,b'http://ns.adobe.com/xap/1.0/\0<x GPSLatitude="12"/>')+jpeg()[2:],b'image/jpeg')])
def test_cover_location_never_uploads(env,wire,cover,mime):
    fm=configure(env,wire,flac(extra=[(6,picture(cover,mime))]))
    with pytest.raises(media.MediaError,match='location_metadata_present'):media.manifest_for(fm,env[0])
    assert not wire['calls']


@pytest.mark.parametrize('extra',[
    [(1,bytes(17))],[(3,b'')],[(3,struct.pack('>QQH',0,0,16)+b'\xff'*18)],
    [(4,comments(b'CUSTOM FIELD=arbitrary valid Unicode '+ '茶'.encode(),b'TITLE=one',b'TITLE=two'))],
    [(6,picture(jpeg(),b'image/jpeg')),(6,picture(png()))],
])
def test_known_metadata_preserved(tmp_path,extra):assert inspect(tmp_path,flac(extra=extra)).public_bytes is None


@pytest.mark.parametrize('raw',[
    flac()[:-1],flac()+b'XMP GPS',flac(frames=b''),flac(total=17),flac(rate=0),
    flac(frames=frame(number=1)),flac(frames=frame()+frame(number=2),total=32),
    flac(frames=frame()+frame(number=16,variable=True),total=32),
    flac(extra=[(3,b'bad')]),flac(extra=[(3,struct.pack('>QQH',1,0,16)+struct.pack('>QQH',0,0,16))]),
    flac(extra=[(0,bytes(34))]),flac().replace(b'fLaC',b'ID3 ',1),
])
def test_broken_structure_refuses(tmp_path,raw):
    with pytest.raises(mediaformats.FormatError):inspect(tmp_path,raw)


@pytest.mark.parametrize('offset',[-1,-3,47])
def test_crc_corruption_refuses(tmp_path,offset):
    raw=bytearray(flac());raw[offset]^=1
    with pytest.raises(mediaformats.FormatError):inspect(tmp_path,bytes(raw))


@pytest.mark.parametrize('change,reason',[({'supported_mime_types':['audio/mp4']},'unsupported_attachment'),({'video_size_limit':1},'media_limit_exceeded'),({'video_size_limit':None},'media_capability_unavailable')])
def test_latest_instance_gate(env,wire,change,reason):
    fm=configure(env,wire,flac());wire['caps']['configuration']['media_attachments'].update(change)
    result,_,_=invoke(env,fm);assert reason in result.error and not posts(wire,'/api/v2/media')


def test_source_cover_change_stale_and_known_remote_held(env,wire):
    fm=configure(env,wire,flac(extra=[(6,picture(png()))]));before=media.prepared_component(media.manifest_for(fm,env[0]))
    wire['upload']=[(202,{'id':'7','type':'audio','url':None})];wire['poll']=[(200,{'id':'7','type':'audio','url':'https://instance.invalid/a'})]
    wire['on_poll']=lambda:(env[2]/'sound.flac').write_bytes(flac(extra=[(6,picture(jpeg(),b'image/jpeg'))]))
    result,journal,_=invoke(env,fm);assert result.failure=='media_held' and journal['media']['remote_ids']==['7'] and not posts(wire,'/api/v1/statuses')
    assert before!=media.prepared_component(media.manifest_for(fm,env[0]))


# RFC9639 Appendix D.1/D.2/D.3 miniature synthetic examples; text source
# https://www.rfc-editor.org/rfc/rfc9639.html#appendix-D (December 2024).
@pytest.mark.parametrize("hexadecimal",['664c6143800000221000100000000f00000f0ac442f0000000013e84b41807dc690307586a3dad1a2e0ffff869180000bf0358fd03128baa9a', '664c614300000022001000100000170000440ac442f000000013d5b0564975e98b8d8b930422757b8103030000120000000000000000000000000000000000100400003a200000007265666572656e6365206c6962464c414320312e332e33203230313930383034010000000e0000005449544c453dd7a9d79cd795d79d81000006000000000000fff86998000f9912086701623d1442998f5df70d6fe00c17caeb21000ee7a77a24a1590c1217b603097b784faa9a33d285e070ad5b1b4851b4010d99d2cd1a68f1e6b810fff869180102a402c382c40bc14a03ee48dd03b67c1330', '664c6143800000221000100000001f00001f07d0007000000018f8f9e396f5cbcfc6dc807f9977906b32fff868020017e944004f6f313d1047d227cb6d090831452bdc2822228057a3'])
def test_rfc9639_appendix_frames(tmp_path,hexadecimal):
    assert inspect(tmp_path,bytes.fromhex(hexadecimal)).kind=="audio"


def test_unknown_publication_durable_no_retry(env,wire):
    from thth import core
    cfg,adapter,_=env;fm=configure(env,wire,flac());factory=lambda *args:adapter
    first=core.send_once('alpha',text='',media_rows=fm['media'],production_flag=False,adapter_factory=factory,log=lambda value:None)
    wire['status']=(500,{})
    result=core.send_once('alpha',text='',media_rows=fm['media'],production_flag=True,confirm=first.digest,adapter_factory=factory,log=lambda value:None)
    assert result.action=='inflight' and len(posts(wire,'/api/v2/media'))==1
    retry=core.send_once('alpha',text='',media_rows=fm['media'],production_flag=True,confirm=first.digest,adapter_factory=factory,log=lambda value:None)
    assert retry.action=='inflight' and len(posts(wire,'/api/v2/media'))==1


def test_frame_reader_is_bounded_and_cache_shared(tmp_path,monkeypatch):
    from thth import flacformats
    raw=flac(frames=b''.join(frame(number=i) for i in range(256)),total=256*16)
    p=tmp_path/'large.flac';p.write_bytes(raw);fd=os.open(p,os.O_RDONLY)
    original=flacformats.Reader.read;reads=[]
    def measured(self,at,n):reads.append(n);assert n<=65536;return original(self,at,n)
    monkeypatch.setattr(flacformats.Reader,'read',measured)
    try:assert flacformats.flac(fd,len(raw)).duration==256*16/8000
    finally:os.close(fd)
    assert sum(reads)<len(raw)*4


def test_flac_actual_git_approval_and_publish(tmp_path,isolated_account_factory,wire,monkeypatch):
    from pathlib import Path
    from thth import accounts,core
    from thth.adapters.mastodon import MastodonAdapter
    from tests.conftest import init_git_pair,make_queue_text,run_git,approve_via_cli
    text=make_queue_text({'account':'alpha','status':'draft','approved_sha':'','topic':''},body='## mastodon\n\n本文。\n',media='mastodon')
    text=text.replace('\n---\n','\nmedia:\n  - file: sound.flac\n    alt: 音声\n---\n',1)
    pair=init_git_pair(tmp_path,seed_content=text);repo=Path(pair['work']);path=Path(pair['queue_dir'])/'a.md'
    (repo/'sound.flac').write_bytes(flac());run_git(str(repo),['add','.']);run_git(str(repo),['commit','-m','synthetic FLAC']);run_git(str(repo),['push'])
    isolated_account_factory(name='alpha',repo_dir=str(repo),media='mastodon',instance=wire['url'],production=True,hashtags=False,char_limit=500,quiet_hours=None,min_interval_hours=0)
    wire['caps']['configuration']['media_attachments']['supported_mime_types']=['audio/flac']
    approved=approve_via_cli(path);assert approved.returncode==0,approved.stderr
    monkeypatch.setattr(accounts,'load_token',lambda _: {'access_token':'synthetic-token'})
    adapter=MastodonAdapter(instance=wire['url'],access_token='synthetic-token')
    wire['upload']=[(200,{'id':'7','type':'audio','url':'https://instance.invalid/a'})]
    core.throw_once('alpha',production_flag=True,adapter_factory=lambda *a:adapter,log=lambda _:None,bypass_pace=True)
    assert len(posts(wire,'/api/v2/media'))==len(posts(wire,'/api/v1/statuses'))==1


def test_audio_sample_bytes_are_not_searched_for_metadata(tmp_path):
    sample=b'GPSLatitude XMP location'.ljust(32,b'\0')
    body=frame()[:7]+b'\x02'+sample
    raw=flac(frames=body+crc(body,16,0x8005))
    assert inspect(tmp_path,raw).duration==.002
