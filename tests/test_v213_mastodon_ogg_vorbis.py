"""Independent LSB setup builder and Ogg Vorbis fake-provider contracts."""
import base64,email.parser,email.policy,hashlib,os,struct
import pytest
from thth import media,mediaformats
from tests.test_v213_mastodon_media import env,wire,invoke,posts
from tests.test_v213_mastodon_ogg_opus import page,inspect
from tests.test_v213_mastodon_flac import comments,picture
from tests.test_v213_media_foundation import png
from tests.test_v213_media_formats import exif


class Writer:
    def __init__(self):self.data=[]
    def put(self,n,v):assert 0<=v<1<<n if n else v==0;self.data.extend((v>>i)&1 for i in range(n));return self
    def raw(self):return bytes(sum(v<<j for j,v in enumerate(self.data[i:i+8])) for i in range(0,len(self.data),8))


def header(channels=1,rate=48000,small=6,large=8):
    return b'\x01vorbis'+struct.pack('<IBIiiiBB',0,channels,rate,0,0,0,small|(large<<4),1)


def setup(*,floor=1,ordered=False,sparse=False,lookup=0,mutate=None,modes=(0,1)):
    w=Writer();w.put(8,0).put(24,0x564342).put(16,1).put(24,2).put(1,int(ordered))
    if ordered:w.put(5,0).put(2,2)
    else:
        w.put(1,int(sparse))
        for _ in range(2):
            if sparse:w.put(1,1)
            w.put(5,0)
    w.put(4,lookup)
    if lookup:w.put(32,0).put(32,0).put(4,0).put(1,0).put(2,0)
    w.put(6,0).put(16,1 if mutate=='time' else 0).put(6,0).put(16,2 if mutate=='floor' else floor)
    if floor==0:w.put(8,1).put(16,48000).put(16,32).put(6,1).put(8,0).put(4,0).put(8,1 if mutate=='book' else 0)
    else:w.put(5,0).put(2,0).put(4,6)
    w.put(6,0).put(16,3 if mutate=='residue' else 0).put(24,0).put(24,32).put(24,15).put(6,0).put(8,1 if mutate=='classbook' else 0).put(3,0).put(1,0)
    w.put(6,0).put(16,1 if mutate=='mapping' else 0).put(1,0).put(1,0).put(2,1 if mutate=='reserved' else 0).put(8,0).put(8,1 if mutate=='floor_ref' else 0).put(8,1 if mutate=='residue_ref' else 0)
    w.put(6,len(modes)-1)
    for mode in modes:w.put(1,mode).put(16,1 if mutate=='window' else 0).put(16,1 if mutate=='transform' else 0).put(8,1 if mutate=='mapping_ref' else 0)
    w.put(1,0 if mutate=='framing' else 1)
    return b'\x05vorbis'+w.raw()


def audio(mode=0):return bytes([mode<<1]) # Remaining floor/residue data may legally be truncated.


def vorbis(*,extra=(),config=None,head=None,serial=1,one_page=False):
    h=header() if head is None else head;c=b'\x03vorbis'+comments(*extra)+b'\x01';s=setup() if config is None else config
    raw=page(h,flags=2,serial=serial)+page(c+s,seq=1,serial=serial,laces=[255]*(len(c)//255)+[len(c)%255]+[255]*(len(s)//255)+[len(s)%255])
    if one_page:return raw+page(audio()+audio(),seq=2,flags=4,granule=32,laces=[1,1],serial=serial)
    return raw+page(audio()+audio(),seq=2,granule=32,laces=[1,1],serial=serial)+page(audio(),seq=3,flags=4,granule=64,serial=serial)


def configure(env,wire,raw):
    (env[2]/'sound.ogg').write_bytes(raw)
    wire['caps']['configuration']['media_attachments'].update(supported_mime_types=['audio/ogg'],image_size_limit=1,image_matrix_limit=None,video_size_limit=len(raw)+1,video_matrix_limit=None,video_frame_rate_limit=None)
    wire['upload']=[(200,{'id':'7','type':'audio','url':'https://instance.invalid/audio'})]
    return {'media':[{'file':'sound.ogg','alt':'音声の説明'}]}


def test_wire_byte_identical_and_real_cover(env,wire):
    raw=vorbis(extra=[b'TITLE=private title',b'METADATA_BLOCK_PICTURE='+base64.b64encode(picture(png()))]);fm=configure(env,wire,raw)
    result,journal,m=invoke(env,fm);assert result.post_id and journal['media']['phase']=='published';row=m['files'][0]
    assert row['format']=='ogg_vorbis' and row['kind']=='audio' and row['duration']==64/48000 and row['width'] is row['height'] is None
    assert row['source_sha256']==row['public_sha256']==hashlib.sha256(raw).hexdigest() and 'private title' not in media.display(m)
    call=posts(wire,'/api/v2/media')[0];message=email.parser.BytesParser(policy=email.policy.default).parsebytes(('Content-Type: '+call[3]['Content-Type']+'\r\n\r\n').encode()+call[2]);parts={x.get_param('name',header='content-disposition'):x for x in message.iter_parts()}
    assert parts['file'].get_payload(decode=True)==raw and parts['file'].get_content_type()=='audio/ogg'
    assert parts['description'].get_payload(decode=True).decode()=='音声の説明'


@pytest.mark.parametrize('floor,ordered,sparse,lookup',[(0,False,False,0),(1,True,False,0),(1,False,True,1),(0,True,False,2)])
def test_setup_known_structures(tmp_path,floor,ordered,sparse,lookup):
    assert inspect(tmp_path,vorbis(config=setup(floor=floor,ordered=ordered,sparse=sparse,lookup=lookup))).duration==64/48000


@pytest.mark.parametrize('mutation',['time','floor','residue','classbook','mapping','reserved','floor_ref','residue_ref','window','transform','mapping_ref','framing','book'])
def test_invalid_setup_refuses_before_provider(env,wire,mutation):
    fm=configure(env,wire,vorbis(config=setup(floor=0 if mutation=='book' else 1,mutate=mutation)))
    with pytest.raises(media.MediaError):media.manifest_for(fm,env[0])
    assert not wire['calls']


@pytest.mark.parametrize('field,reason',[
    (b'LOCATION=12,34','location_metadata_present'),(b'GPS=12','location_metadata_present'),
    (b'XMP=secret','location_metadata_unverifiable'),(b'COVERART=secret','location_metadata_unverifiable'),
    (b'TITLE=\xff','invalid_attachment_structure'),
    (b'METADATA_BLOCK_PICTURE='+base64.b64encode(picture(png()[:-12]+mediaformats._chunk(b'eXIf',exif())+png()[-12:])),'location_metadata_present'),
])
def test_privacy_before_provider(env,wire,field,reason):
    raw=vorbis(extra=[field]);fm=configure(env,wire,raw)
    with pytest.raises(media.MediaError,match=reason):media.manifest_for(fm,env[0])
    assert not wire['calls'] and (env[2]/'sound.ogg').read_bytes()==raw


def test_first_eos_ambiguous_origin_is_unknown(tmp_path):
    assert inspect(tmp_path,vorbis(one_page=True)).duration is None
    assert inspect(tmp_path,vorbis()+vorbis(serial=2)).duration==128/48000


def test_vorbis_is_not_audio_opus(env,wire):
    fm=configure(env,wire,vorbis());wire['caps']['configuration']['media_attachments']['supported_mime_types']=['audio/opus']
    result,_,_=invoke(env,fm);assert 'unsupported_attachment' in result.error and not posts(wire,'/api/v2/media')


@pytest.mark.parametrize('extra',[b'\x01',b'opaque'])
def test_unknown_setup_tail_privacy(tmp_path,extra):
    with pytest.raises(mediaformats.FormatError,match='location_metadata_unverifiable'):inspect(tmp_path,vorbis(config=setup()+extra))


@pytest.mark.parametrize('case',['crc','setup_missing','comment_framing','bad_audio','header_order','empty_rate','version','small_blocks','duplicate_serial'])
def test_ogg_vorbis_envelope_negatives(tmp_path,case):
    h=header();c=b'\x03vorbis'+comments()+b'\x01';s=setup()
    if case=='comment_framing':c=c[:-1]+b'\0'
    if case=='empty_rate':h=header(rate=0)
    if case=='version':h=h[:7]+struct.pack('<I',1)+h[11:]
    if case=='small_blocks':h=header(small=5)
    raw=page(h,flags=2)+page(c,seq=1)+page(s,seq=2)+page(audio()+audio(),seq=3,granule=32,laces=[1,1])+page(audio(),seq=4,flags=4,granule=64)
    if case=='crc':raw=raw[:-1]+bytes([raw[-1]^1])
    if case=='setup_missing':raw=page(h,flags=2)+page(c,seq=1)+page(audio(),seq=2,flags=4,granule=0)
    if case=='bad_audio':raw=raw[:-29]+page(b'\x01',seq=4,flags=4,granule=64)
    if case=='header_order':raw=page(h,flags=2)+page(s,seq=1)+page(c,seq=2)+page(audio(),seq=3,flags=4,granule=0)
    if case=='duplicate_serial':raw=vorbis()+vorbis()
    with pytest.raises(mediaformats.FormatError):inspect(tmp_path,raw)


def test_variable_blocks_initial_offset_and_trim(tmp_path):
    h=header();c=b'\x03vorbis'+comments()+b'\x01';s=setup();prefix=page(h,flags=2)+page(c,seq=1)+page(s,seq=2)
    # Short64 -> long256 has 80 output samples; starting at100 gives granule180.
    raw=prefix+page(audio(0)+audio(1),seq=3,granule=180,laces=[1,1])+page(audio(0),seq=4,flags=4,granule=250)
    assert inspect(tmp_path,raw).duration==150/48000
    raw=prefix+page(audio()+audio(),seq=3,granule=12,laces=[1,1])+page(audio(),seq=4,flags=4,granule=40)
    assert inspect(tmp_path,raw).duration==40/48000


@pytest.mark.parametrize('kind',['under','over','single_bad','lookup','sync','truncate'])
def test_codebook_invalid_boundaries(tmp_path,kind):
    from thth.vorbisformats import _book,Bits
    from thth.oggformats import Packet
    from thth.audioformats import Reader
    w=Writer().put(24,0 if kind=='sync' else 0x564342).put(16,1).put(24,3 if kind=='over' else 1 if kind=='single_bad' else 2).put(1,0).put(1,0)
    for _ in range(3 if kind=='over' else 1 if kind=='single_bad' else 2):w.put(5,1 if kind in ('under','single_bad') else 0)
    w.put(4,3 if kind=='lookup' else 0);raw=w.raw()
    if kind=='truncate':raw=raw[:-1]
    path=tmp_path/'book';path.write_bytes(raw);fd=os.open(path,os.O_RDONLY)
    try:
        p=Packet(Reader(fd,len(raw)));p.append(0,len(raw))
        with pytest.raises(mediaformats.FormatError):_book(Bits(p))
    finally:os.close(fd)


def test_codebook_single_entry_erratum_and_sparse_unused(tmp_path):
    from thth.vorbisformats import _book,Bits
    from thth.oggformats import Packet
    from thth.audioformats import Reader
    for sparse in (False,True):
        w=Writer().put(24,0x564342).put(16,1).put(24,2 if sparse else 1).put(1,0).put(1,int(sparse))
        if sparse:w.put(1,0).put(1,1)
        w.put(5,0).put(4,0);raw=w.raw();path=tmp_path/'book';path.write_bytes(raw);fd=os.open(path,os.O_RDONLY)
        try:
            p=Packet(Reader(fd,len(raw)));p.append(0,len(raw));assert _book(Bits(p))[2]==0
        finally:os.close(fd)


def test_unknown_status_no_replay(env,wire):
    from thth import core
    _,adapter,_=env;fm=configure(env,wire,vorbis());factory=lambda *args:adapter
    first=core.send_once('alpha',text='',media_rows=fm['media'],production_flag=False,adapter_factory=factory,log=lambda _:None);wire['status']=(500,{})
    sent=core.send_once('alpha',text='',media_rows=fm['media'],production_flag=True,confirm=first.digest,adapter_factory=factory,log=lambda _:None)
    again=core.send_once('alpha',text='',media_rows=fm['media'],production_flag=True,confirm=first.digest,adapter_factory=factory,log=lambda _:None)
    assert sent.action==again.action=='inflight' and len(posts(wire,'/api/v2/media'))==len(posts(wire,'/api/v1/statuses'))==1


def test_source_changed_holds_uploaded_id(env,wire):
    fm=configure(env,wire,vorbis());wire['upload']=[(202,{'id':'7','type':'audio','url':None})];wire['poll']=[(200,{'id':'7','type':'audio','url':'https://instance.invalid/a'})]
    wire['on_poll']=lambda:(env[2]/'sound.ogg').write_bytes(vorbis(extra=[b'TITLE=changed']))
    result,journal,_=invoke(env,fm);assert result.failure=='media_held' and journal['media']['remote_ids']==['7'] and not posts(wire,'/api/v1/statuses')


def test_vorbis_actual_git_approval_and_source_stale(tmp_path,isolated_account_factory,wire,monkeypatch):
    from pathlib import Path
    from thth import accounts,core
    from thth.adapters.mastodon import MastodonAdapter
    from tests.conftest import init_git_pair,make_queue_text,run_git,approve_via_cli
    text=make_queue_text({'account':'alpha','status':'draft','approved_sha':'','topic':''},body='## mastodon\n\n本文。\n',media='mastodon')
    text=text.replace('\n---\n','\nmedia:\n  - file: sound.ogg\n    alt: 音声\n---\n',1)
    pair=init_git_pair(tmp_path,seed_content=text);repo=Path(pair['work']);path=Path(pair['queue_dir'])/'a.md'
    (repo/'sound.ogg').write_bytes(vorbis());run_git(str(repo),['add','.']);run_git(str(repo),['commit','-m','synthetic Vorbis']);run_git(str(repo),['push'])
    isolated_account_factory(name='alpha',repo_dir=str(repo),media='mastodon',instance=wire['url'],production=True,hashtags=False,char_limit=500,quiet_hours=None,min_interval_hours=0)
    wire['caps']['configuration']['media_attachments']['supported_mime_types']=['audio/ogg']
    approved=approve_via_cli(path);assert approved.returncode==0,approved.stderr
    monkeypatch.setattr(accounts,'load_token',lambda _: {'access_token':'synthetic-token'})
    adapter=MastodonAdapter(instance=wire['url'],access_token='synthetic-token')
    (repo/'sound.ogg').write_bytes(vorbis(extra=[b'TITLE=changed']));run_git(str(repo),['add','.']);run_git(str(repo),['commit','-m','changed source']);run_git(str(repo),['push'])
    stale=core.throw_once('alpha',production_flag=True,adapter_factory=lambda *a:adapter,log=lambda _:None,bypass_pace=True)
    assert not posts(wire,'/api/v2/media') and not posts(wire,'/api/v1/statuses')
    approved=approve_via_cli(path);assert approved.returncode==0,approved.stderr
    wire['upload']=[(200,{'id':'7','type':'audio','url':'https://instance.invalid/a'})]
    core.throw_once('alpha',production_flag=True,adapter_factory=lambda *a:adapter,log=lambda _:None,bypass_pace=True)
    assert len(posts(wire,'/api/v2/media'))==len(posts(wire,'/api/v1/statuses'))==1


def test_v2_manifest_and_source_change_digest(env,wire,monkeypatch):
    from thth import cli,lint,bundle,threadrun
    import json
    cfg,_,repo=env;fm=configure(env,wire,vorbis())
    raw='---\nthth: 2\naccount: alpha\npublish_at: 2030-01-01T12:00:00+09:00\ncontinue_until: 2030-01-01T13:00:00+09:00\nstatus: draft\nposts:\n'
    raw+='  - index: 1\n    media:\n      - file: sound.ogg\n        alt: 音声の説明\n  - index: 2\n---\n## mastodon\n音声\n<!-- thth: 2/2 -->\n続き\n'
    path=repo/'bundle.md';path.write_text(raw)
    monkeypatch.setattr(threadrun,'unreadable_runs',lambda:[]);monkeypatch.setattr(threadrun,'find_latest',lambda *a:None)
    assert not bundle.parse(str(path)).malformed
    assert not [x for x in lint.lint_file(str(path)) if not lint.is_warning(x)]
    prepared,reason=cli._prepare_one(str(path));assert reason is None and prepared['media_manifests'][0]['files'][0]['format']=='ogg_vorbis'
    (repo/'sound.ogg').write_bytes(vorbis(extra=[b'TITLE=changed']));other,reason=cli._prepare_one(str(path))
    assert reason is None and other['approved_sha']!=prepared['approved_sha']
    assert not posts(wire,'/api/v2/media') and not posts(wire,'/api/v1/statuses')




def test_observed_sample_rate_not_fixed_opus_clock(tmp_path):
    assert inspect(tmp_path,vorbis(head=header(rate=96000))).duration==64/96000
