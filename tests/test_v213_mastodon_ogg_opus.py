"""Synthetic RFC3533/7845/6716 containers and fake-provider wire, not PCM."""
import base64,email.parser,email.policy,hashlib,os,struct
import pytest
from thth import media,mediaformats
from tests.test_v213_mastodon_media import env,wire,invoke,posts
from tests.test_v213_media_foundation import png,jpeg
from tests.test_v213_media_formats import exif,segment
from tests.test_v213_mastodon_flac import comments,picture


def crc(raw):
    value=0
    for byte in raw:
        for shift in range(7,-1,-1):
            bit=(value>>31)^((byte>>shift)&1);value=(value<<1)&0xffffffff
            if bit:value^=0x04c11db7
    return value


def page(payload=b'',*,serial=1,seq=0,flags=0,granule=0,laces=None):
    laces=bytes([255]*(len(payload)//255)+[len(payload)%255]) if laces is None else bytes(laces)
    assert sum(laces)==len(payload) and len(laces)<256
    head=b'OggS'+bytes([0,flags])+struct.pack('<qII',granule,serial,seq)+bytes(4)+bytes([len(laces)])
    raw=head+laces+payload;return raw[:22]+struct.pack('<I',crc(raw))+raw[26:]


def head(channels=1,preskip=0,mapping=0,streams=1,coupled=0,indices=None):
    raw=b'OpusHead'+struct.pack('<BBHIhB',1,channels,preskip,48000,0,mapping)
    return raw if mapping==0 else raw+bytes([streams,coupled])+bytes(range(channels) if indices is None else indices)


def tags(*values,tail=b''):return b'OpusTags'+comments(*values)+tail

def opus(*,packet=b'\x80\0',samples=120,preskip=0,extra=(),serial=1,header=None,granule=None):
    return page(head(preskip=preskip) if header is None else header,flags=2,serial=serial)+page(tags(*extra),seq=1,serial=serial)+page(packet,seq=2,flags=4,granule=samples if granule is None else granule,serial=serial)


def inspect(tmp_path,raw):
    path=tmp_path/'sound.opus';path.write_bytes(raw);fd=os.open(path,os.O_RDONLY)
    try:return mediaformats.inspect(fd,len(raw))
    finally:os.close(fd)


def configure(env,wire,raw,mime='audio/ogg'):
    (env[2]/'sound.opus').write_bytes(raw)
    wire['caps']['configuration']['media_attachments'].update(supported_mime_types=[mime],image_size_limit=1,image_matrix_limit=None,video_size_limit=len(raw)+1,video_matrix_limit=None,video_frame_rate_limit=None)
    wire['upload']=[(200,{'id':'7','type':'audio','url':'https://instance.invalid/audio'})]
    return {'media':[{'file':'sound.opus','alt':'音声の説明'}]}


@pytest.mark.parametrize('mime',['audio/ogg','audio/opus'])
def test_actual_wire_original_bytes_cover_and_na(env,wire,mime):
    raw=opus(extra=[b'TITLE=PRIVATE SONG',b'COMMENT=GPSLatitude is ordinary prose',b'METADATA_BLOCK_PICTURE='+base64.b64encode(picture(png()))])
    fm=configure(env,wire,raw,mime);result,journal,m=invoke(env,fm)
    assert result.post_id and journal['media']['phase']=='published';row=m['files'][0]
    assert row['width'] is row['height'] is None and row['duration']==.0025 and row['format']=='ogg' and row['kind']=='audio'
    assert row['source_sha256']==row['public_sha256']==hashlib.sha256(raw).hexdigest()
    assert 'PRIVATE SONG' not in media.display(m) and 'embedded_cover_retained' in media.display(m)
    call=posts(wire,'/api/v2/media')[0];message=email.parser.BytesParser(policy=email.policy.default).parsebytes(('Content-Type: '+call[3]['Content-Type']+'\r\n\r\n').encode()+call[2]);parts={x.get_param('name',header='content-disposition'):x for x in message.iter_parts()}
    assert parts['file'].get_payload(decode=True)==raw and parts['file'].get_content_type()==mime
    assert parts['description'].get_payload(decode=True).decode()=='音声の説明'


@pytest.mark.parametrize('packet,samples',[
    (b'\x80x',120),(b'\x81ab',240),(b'\x82\x01ab',240),
    (b'\x83\x03abc',360),(b'\x83\x83\x01\x02abbccc',360),
    (b'\x83\x43\x02abc\0\0',360),(b'\x80',120),
    (b'\x00x',480),(b'\x18x',2880),(b'\x60x',480),(b'\x78x',960),
    (b'\x98x',960),
])
def test_normative_packet_codes_and_durations(tmp_path,packet,samples):
    assert inspect(tmp_path,opus(packet=packet,samples=samples)).duration==samples/48000


@pytest.mark.parametrize('packet',[
    b'',b'\x81x',b'\x82\x03ab',b'\x83\0',b'\x83\x3f'+bytes(63),
    b'\x83\x43\x10abc',b'\x83\x83\x05\x02abc',b'\x80'+bytes(1276),
])
def test_invalid_packet_framing_before_http(env,wire,packet):
    fm=configure(env,wire,opus(packet=packet))
    with pytest.raises(media.MediaError):media.manifest_for(fm,env[0])
    assert not wire['calls']


@pytest.mark.parametrize('value,reason',[
    (b'LOCATION=1,2','location_metadata_present'),(b'gpslatitude=12','location_metadata_present'),
    (b'XMP=<GPS/>','location_metadata_unverifiable'),(b'COVERART=encoded','location_metadata_unverifiable'),
    (b'FIELD=\xff','invalid_attachment_structure'),(b'BROKEN','invalid_attachment_structure'),
    (b'METADATA_BLOCK_PICTURE=!!!!','invalid_attachment_structure'),
    (b'METADATA_BLOCK_PICTURE='+base64.b64encode(picture(b'https://no-fetch.invalid/p',b'-->')),'location_metadata_unverifiable'),
])
def test_comment_privacy_before_http(env,wire,value,reason):
    raw=opus(extra=[value]);fm=configure(env,wire,raw)
    with pytest.raises(media.MediaError,match=reason):media.manifest_for(fm,env[0])
    assert not wire['calls'] and (env[2]/'sound.opus').read_bytes()==raw


@pytest.mark.parametrize('cover,mime',[
    (png()[:-12]+mediaformats._chunk(b'eXIf',exif())+png()[-12:],b'image/png'),
    (jpeg()[:2]+segment(0xe1,b'Exif\0\0'+exif())+jpeg()[2:],b'image/jpeg'),
])
def test_cover_location_before_http(env,wire,cover,mime):
    raw=opus(extra=[b'METADATA_BLOCK_PICTURE='+base64.b64encode(picture(cover,mime))]);fm=configure(env,wire,raw)
    with pytest.raises(media.MediaError,match='location_metadata_present'):media.manifest_for(fm,env[0])
    assert not wire['calls']


@pytest.mark.parametrize('where',['tags','packet'])
def test_unknown_binary_padding_is_unverified(tmp_path,where):
    raw=(page(head(),flags=2)+page(tags(tail=b'\x02private'),seq=1)+page(b'\x80x',seq=2,flags=4,granule=120)) if where=='tags' else opus(packet=b'\x83\x41\x01xP')
    with pytest.raises(mediaformats.FormatError,match='location_metadata_unverifiable'):inspect(tmp_path,raw)


def test_continued_tag_packet_and_zero_padding(tmp_path):
    comment=tags(b'TITLE='+b'x'*65500,tail=bytes(400));first=comment[:65025];rest=comment[65025:]
    raw=page(head(),flags=2)+page(first,seq=1,granule=-1,laces=[255]*255)+page(rest,seq=2,flags=1)+page(b'\x80x',seq=3,flags=4,granule=120)
    assert inspect(tmp_path,raw).duration==.0025


def test_initial_offset_preskip_end_trim_and_chain(tmp_path):
    raw=page(head(preskip=20),flags=2)+page(tags(),seq=1)+page(b'\x80x',seq=2,granule=1000)+page(b'\x80x',seq=3,flags=4,granule=1100)
    assert inspect(tmp_path,raw).duration==200/48000
    assert inspect(tmp_path,opus(preskip=20,granule=100)).duration==80/48000
    assert inspect(tmp_path,opus()+opus(serial=2)).duration==.005


@pytest.mark.parametrize('header,packet',[
    (head(channels=2),b'\x84x'),
    (head(channels=3,mapping=1,streams=2,coupled=1),b'\x84\x01x\x80y'),
    (head(channels=2,mapping=255,streams=1,coupled=0,indices=[0,255]),b'\x80x'),
])
def test_channel_mapping_self_delimited_positive(tmp_path,header,packet):
    assert inspect(tmp_path,opus(header=header,packet=packet)).duration==.0025


@pytest.mark.parametrize('header',[
    head(channels=0),head(channels=3),head(channels=2)+b'opaque',
    head(channels=3,mapping=1,streams=1,coupled=0,indices=[0,1,2]),
    head(channels=3,mapping=2,streams=2,coupled=1),
    head(channels=3,mapping=1,streams=1,coupled=2),
])
def test_uninspected_or_invalid_header_mapping(tmp_path,header):
    with pytest.raises(mediaformats.FormatError):inspect(tmp_path,opus(header=header))


@pytest.mark.parametrize('mutation',['crc','seq','continue','bos','eos','granule','bounds'])
def test_page_structure_refuses(tmp_path,mutation):
    h=page(head(),flags=2);t=page(tags(),seq=1);last=page(b'\x80x',seq=2,flags=4,granule=120)
    if mutation=='crc':last=last[:-1]+bytes([last[-1]^1])
    elif mutation=='seq':last=page(b'\x80x',seq=3,flags=4,granule=120)
    elif mutation=='continue':last=page(b'\x80x',seq=2,flags=5,granule=120)
    elif mutation=='bos':h=page(head())
    elif mutation=='eos':last=page(b'\x80x',seq=2,granule=120)
    elif mutation=='granule':last=page(b'\x80x',seq=2,flags=4,granule=-1)
    else:last=last[:-1]
    with pytest.raises(mediaformats.FormatError):inspect(tmp_path,h+t+last)


def test_audio_payload_is_not_searched_for_metadata(tmp_path):
    assert inspect(tmp_path,opus(packet=b'\x80GPSLatitude XMP location')).duration==.0025


def test_multiplexed_duration_is_not_invented(tmp_path):
    raw=page(head(),flags=2)+page(head(),flags=2,serial=2)+page(tags(),seq=1)+page(tags(),seq=1,serial=2)+page(b'\x80x',seq=2,flags=4,granule=120)+page(b'\x80x',seq=2,flags=4,granule=120,serial=2)
    assert inspect(tmp_path,raw).duration is None


@pytest.mark.parametrize('change,reason',[({'supported_mime_types':['audio/mp4']},'unsupported_attachment'),({'video_size_limit':1},'media_limit_exceeded'),({'video_size_limit':None},'media_capability_unavailable')])
def test_latest_instance_caps(env,wire,change,reason):
    fm=configure(env,wire,opus());wire['caps']['configuration']['media_attachments'].update(change)
    result,_,_=invoke(env,fm);assert reason in result.error and not posts(wire,'/api/v2/media')


def test_cover_change_after_upload_holds_id(env,wire):
    fm=configure(env,wire,opus());wire['upload']=[(202,{'id':'7','type':'audio','url':None})];wire['poll']=[(200,{'id':'7','type':'audio','url':'https://instance.invalid/a'})]
    wire['on_poll']=lambda:(env[2]/'sound.opus').write_bytes(opus(extra=[b'TITLE=changed']))
    result,journal,_=invoke(env,fm);assert result.failure=='media_held' and journal['media']['remote_ids']==['7'] and not posts(wire,'/api/v1/statuses')


def test_unknown_publication_no_replay(env,wire):
    from thth import core
    _,adapter,_=env;fm=configure(env,wire,opus());factory=lambda *args:adapter
    first=core.send_once('alpha',text='',media_rows=fm['media'],production_flag=False,adapter_factory=factory,log=lambda _:None);wire['status']=(500,{})
    result=core.send_once('alpha',text='',media_rows=fm['media'],production_flag=True,confirm=first.digest,adapter_factory=factory,log=lambda _:None)
    assert result.action=='inflight' and len(posts(wire,'/api/v2/media'))==1
    retry=core.send_once('alpha',text='',media_rows=fm['media'],production_flag=True,confirm=first.digest,adapter_factory=factory,log=lambda _:None)
    assert retry.action=='inflight' and len(posts(wire,'/api/v2/media'))==1


def test_opus_actual_git_approval_and_source_stale(tmp_path,isolated_account_factory,wire,monkeypatch):
    from pathlib import Path
    from thth import accounts,core
    from thth.adapters.mastodon import MastodonAdapter
    from tests.conftest import init_git_pair,make_queue_text,run_git,approve_via_cli
    text=make_queue_text({'account':'alpha','status':'draft','approved_sha':'','topic':''},body='## mastodon\n\n本文。\n',media='mastodon')
    text=text.replace('\n---\n','\nmedia:\n  - file: sound.opus\n    alt: 音声\n---\n',1)
    pair=init_git_pair(tmp_path,seed_content=text);repo=Path(pair['work']);path=Path(pair['queue_dir'])/'a.md'
    (repo/'sound.opus').write_bytes(opus());run_git(str(repo),['add','.']);run_git(str(repo),['commit','-m','synthetic Opus']);run_git(str(repo),['push'])
    isolated_account_factory(name='alpha',repo_dir=str(repo),media='mastodon',instance=wire['url'],production=True,hashtags=False,char_limit=500,quiet_hours=None,min_interval_hours=0)
    wire['caps']['configuration']['media_attachments']['supported_mime_types']=['audio/ogg']
    approved=approve_via_cli(path);assert approved.returncode==0,approved.stderr
    monkeypatch.setattr(accounts,'load_token',lambda _: {'access_token':'synthetic-token'})
    adapter=MastodonAdapter(instance=wire['url'],access_token='synthetic-token')
    (repo/'sound.opus').write_bytes(opus(extra=[b'TITLE=changed']));run_git(str(repo),['add','.']);run_git(str(repo),['commit','-m','changed source']);run_git(str(repo),['push'])
    stale=core.throw_once('alpha',production_flag=True,adapter_factory=lambda *a:adapter,log=lambda _:None,bypass_pace=True)
    assert not posts(wire,'/api/v2/media') and not posts(wire,'/api/v1/statuses')
    approved=approve_via_cli(path);assert approved.returncode==0,approved.stderr
    wire['upload']=[(200,{'id':'7','type':'audio','url':'https://instance.invalid/a'})]
    core.throw_once('alpha',production_flag=True,adapter_factory=lambda *a:adapter,log=lambda _:None,bypass_pace=True)
    assert len(posts(wire,'/api/v2/media'))==len(posts(wire,'/api/v1/statuses'))==1


def test_v2_manifest_and_source_change_digest(env,wire,monkeypatch):
    from thth import cli,lint,bundle,threadrun
    import json
    cfg,_,repo=env;fm=configure(env,wire,opus())
    raw='---\nthth: 2\naccount: alpha\npublish_at: 2030-01-01T12:00:00+09:00\ncontinue_until: 2030-01-01T13:00:00+09:00\nstatus: draft\nposts:\n'
    raw+='  - index: 1\n    media:\n      - file: sound.opus\n        alt: 音声の説明\n  - index: 2\n---\n## mastodon\n音声\n<!-- thth: 2/2 -->\n続き\n'
    path=repo/'bundle.md';path.write_text(raw)
    monkeypatch.setattr(threadrun,'unreadable_runs',lambda:[]);monkeypatch.setattr(threadrun,'find_latest',lambda *a:None)
    assert not bundle.parse(str(path)).malformed
    assert not [x for x in lint.lint_file(str(path)) if not lint.is_warning(x)]
    prepared,reason=cli._prepare_one(str(path));assert reason is None and prepared['media_manifests'][0]['files'][0]['format']=='ogg'
    (repo/'sound.opus').write_bytes(opus(extra=[b'TITLE=changed']));other,reason=cli._prepare_one(str(path))
    assert reason is None and other['approved_sha']!=prepared['approved_sha']
    assert not posts(wire,'/api/v2/media') and not posts(wire,'/api/v1/statuses')


def test_late_bos_after_comments_is_not_multiplex_header(tmp_path):
    raw=page(head(),flags=2)+page(tags(),seq=1)+page(head(),flags=2,serial=2)+page(tags(),seq=1,serial=2)+page(b'\x80x',seq=2,flags=4,granule=120)+page(b'\x80x',seq=2,flags=4,granule=120,serial=2)
    with pytest.raises(mediaformats.FormatError):inspect(tmp_path,raw)


def test_self_delimited_cbr_and_vbr_multistream(tmp_path):
    header=head(channels=3,mapping=1,streams=2,coupled=1)
    for first in (b'\x85\x01ab',b'\x86\x01\x01ab',b'\x87\x02\x01ab'):
        assert inspect(tmp_path,opus(header=header,packet=first+b'\x81ab',samples=240)).duration==.005


def test_multiframe_large_stream_reads_are_page_bounded(tmp_path,monkeypatch):
    from thth.audioformats import Reader
    raw=page(head(),flags=2)+page(tags(),seq=1)
    for n in range(1,51):raw+=page(b'\x80'+bytes(1275),seq=n+1,flags=4 if n==50 else 0,granule=n*120)
    original=Reader.read;seen=[]
    def read(self,at,n):seen.append(n);return original(self,at,n)
    monkeypatch.setattr(Reader,'read',read)
    assert inspect(tmp_path,raw).duration==.125 and max(seen)<=65536


@pytest.mark.parametrize('fields',[
    [b'R128_TRACK_GAIN=32768'],[b'R128_ALBUM_GAIN=-32769'],[b'R128_TRACK_GAIN= 1'],
    [b'R128_TRACK_GAIN=+'],[b'R128_TRACK_GAIN=0000001'],[b'R128_TRACK_GAIN=1',b'r128_track_gain=1'],
])
def test_gain_tag_normative_constraints(tmp_path,fields):
    with pytest.raises(mediaformats.FormatError):inspect(tmp_path,opus(extra=fields))


def test_gain_tag_boundaries_and_duplicate_serial(tmp_path):
    assert inspect(tmp_path,opus(extra=[b'R128_TRACK_GAIN=-32768',b'R128_ALBUM_GAIN=+32767'])).duration==.0025
    with pytest.raises(mediaformats.FormatError):inspect(tmp_path,opus()+opus())


def test_valid_future_header_extension_is_unverified_not_corrupt(tmp_path):
    h=bytearray(head());h[8]=2
    with pytest.raises(mediaformats.FormatError,match='location_metadata_unverifiable: opus header extension'):inspect(tmp_path,opus(header=bytes(h)+b'new'))


@pytest.mark.parametrize('last,flags',[(121,0),(119,4),(241,4)])
def test_granule_discontinuity_refuses(tmp_path,last,flags):
    raw=page(head(),flags=2)+page(tags(),seq=1)+page(b'\x80x',seq=2,granule=120)+page(b'\x80x',seq=3,flags=flags,granule=last)
    if not flags:raw+=page(b'\x80x',seq=4,flags=4,granule=last+120)
    with pytest.raises(mediaformats.FormatError):inspect(tmp_path,raw)
