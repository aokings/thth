"""RIFF/WAVE grammar and unchanged prepared audio, with fake instance delivery."""
import email.parser,email.policy,hashlib,io,struct,wave
import pytest
from thth import media
from tests.test_v213_mastodon_media import env,wire,invoke,posts


def chunk(kind,value):return kind+struct.pack('<I',len(value))+value+(b'\0' if len(value)%2 else b'')

def wav(*,tag=1,bits=16,channels=2,rate=8000,extra=b'',samples=None,extended=False):
    align=channels*(bits//8);samples=samples if samples is not None else bytes(align*8)
    fmt=struct.pack('<HHIIHH',0xfffe if extended else tag,channels,rate,rate*align,align,bits)
    if extended:fmt+=struct.pack('<HHI',22,bits,0)+struct.pack('<I',tag)+bytes.fromhex('00001000800000aa00389b71')
    payload=b'WAVE'+chunk(b'fmt ',fmt)+extra+chunk(b'data',samples)
    return b'RIFF'+struct.pack('<I',len(payload))+payload


def setup(env,wire,raw,*,mime='audio/wav'):
    (env[2]/'sound.wav').write_bytes(raw)
    wire['caps']['configuration']['media_attachments'].update(supported_mime_types=[mime],video_size_limit=len(raw)+1,image_size_limit=1,video_matrix_limit=None,video_frame_rate_limit=None)
    wire['upload']=[(200,{'id':'7','type':'audio','url':'https://instance.invalid/a'})]
    return {'media':[{'file':'sound.wav','alt':'波形の説明'}]}


@pytest.mark.parametrize('mime',['audio/wav','audio/wave','audio/x-wav','audio/vnd.wave'])
@pytest.mark.parametrize('tag,bits,extended',[(1,16,False),(3,32,False),(1,24,True),(3,64,True)])
def test_wav_public_multipart_exact_bytes_and_na(env,wire,mime,tag,bits,extended):
    extra=chunk(b'LIST',b'INFO'+chunk(b'INAM',b'PRIVATE TITLE\0')+chunk(b'ICMT',b'GPSLatitude in freeform comment\0'))+chunk(b'JUNK',bytes(3))
    raw=wav(tag=tag,bits=bits,extended=extended,extra=extra);fm=setup(env,wire,raw,mime=mime)
    result,_,manifest=invoke(env,fm);assert result.post_id
    row=manifest['files'][0];assert row['kind']=='audio' and row['format']=='wav' and row['width'] is row['height'] is None
    assert row['duration']==.001 and row['public_sha256']==row['source_sha256']==hashlib.sha256(raw).hexdigest()
    assert 'non_location_metadata_retained' in media.display(manifest) and 'PRIVATE TITLE' not in media.display(manifest)
    call=posts(wire,'/api/v2/media')[0]
    message=email.parser.BytesParser(policy=email.policy.default).parsebytes(('Content-Type: '+call[3]['Content-Type']+'\r\n\r\n').encode()+call[2])
    parts={part.get_param('name',header='content-disposition'):part for part in message.iter_parts()}
    assert parts['file'].get_payload(decode=True)==raw and parts['file'].get_content_type()==mime
    assert parts['description'].get_payload(decode=True).decode()=='波形の説明'
    if tag==1 and not extended:
        with wave.open(io.BytesIO(raw)) as parsed:assert parsed.getnframes()==8 and parsed.getframerate()==8000


@pytest.mark.parametrize('extra,reason',[
    (chunk(b'LIST',b'INFO'+chunk(b'IARL',b'archive address\0')),'location_metadata_present'),
    (chunk(b'LIST',b'INFO'+chunk(b'GPS ',b'12,34\0')),'location_metadata_unverifiable'),
    (chunk(b'LIST',b'INFO'+chunk(b'INAM',b'not terminated')),'invalid_attachment_structure'),
    (chunk(b'LIST',b'INFO'+chunk(b'INAM',b'x\0hidden\0')),'invalid_attachment_structure'),
    (chunk(b'LIST',b'wavl'),'location_metadata_unverifiable'),
    (chunk(b'iXML',b'<GPS>12</GPS>'),'location_metadata_unverifiable'),
    (chunk(b'id3 ',b'APIC private cover'),'location_metadata_unverifiable'),
    (chunk(b'bext',bytes(602)),'location_metadata_unverifiable'),
    (chunk(b'JUNK',b'private'),'location_metadata_unverifiable'),
    (chunk(b'fact',struct.pack('<I',9)),'invalid_attachment_structure'),
    (chunk(b'fact',struct.pack('<II',8,0)),'location_metadata_unverifiable'),
    (chunk(b'CSET',bytes(7)),'invalid_attachment_structure'),
])
def test_wav_unknown_metadata_and_broken_fields_stop_before_http(env,wire,extra,reason):
    raw=wav(extra=extra);fm=setup(env,wire,raw)
    with pytest.raises(media.MediaError,match=reason):media.manifest_for(fm,env[0])
    assert not wire['calls'] and (env[2]/'sound.wav').read_bytes()==raw


@pytest.mark.parametrize('raw',[
    wav()[:-1],wav()+b'\0',wav(samples=b'x'),
    wav().replace(b'fmt ',b'data',1),
    wav(extra=chunk(b'fmt ',bytes(16))),
    wav(rate=0),wav(channels=0),
    wav(extended=True).replace(bytes.fromhex('00001000800000aa00389b71'),bytes(12)),
    wav(extra=chunk(b'JUNK',bytes(1))).replace(b'JUNK\1\0\0\0\0\0',b'JUNK\1\0\0\0\0x'),
])
def test_wav_structural_corruption_refuses(env,wire,raw):
    fm=setup(env,wire,raw)
    with pytest.raises(media.MediaError):media.manifest_for(fm,env[0])
    assert not wire['calls']


def test_sample_bytes_never_scanned_and_fact_verified(env,wire):
    sample=b'GPSLatitude XMP'+b'\0';assert len(sample)%4==0
    raw=wav(extra=chunk(b'fact',struct.pack('<I',len(sample)//4))+chunk(b'CSET',bytes(8)),samples=sample)
    fm=setup(env,wire,raw)
    with media.prepare(env[0]['repo_dir'],fm,'mastodon') as (manifest,items):
        assert b''.join(items[0].chunks())==raw and manifest['files'][0]['duration']==len(sample)/4/8000


@pytest.mark.parametrize('change,reason',[
    ({'supported_mime_types':['audio/mp4']},'unsupported_attachment'),
    ({'video_size_limit':1},'media_limit_exceeded'),
    ({'video_size_limit':None},'media_capability_unavailable')])
def test_wav_fresh_instance_gate_precedes_upload(env,wire,change,reason):
    fm=setup(env,wire,wav());wire['caps']['configuration']['media_attachments'].update(change)
    result,_,_=invoke(env,fm);assert reason in result.error and not posts(wire,'/api/v2/media')


def test_wav_approval_fingerprint_and_source_veto(env,wire):
    raw=wav();fm=setup(env,wire,raw)
    first=media.prepared_component(media.manifest_for(fm,env[0]))
    wire['upload']=[(202,{'id':'7','type':'audio','url':None})];wire['poll']=[(200,{'id':'7','type':'audio','url':'https://instance.invalid/a'})]
    wire['on_poll']=lambda:(env[2]/'sound.wav').write_bytes(wav(samples=bytes(36)))
    result,journal,_=invoke(env,fm)
    assert result.failure=='media_held' and journal['media']['remote_ids']==['7'] and not posts(wire,'/api/v1/statuses')
    assert first!=media.prepared_component(media.manifest_for(fm,env[0]))


def test_wav_parser_does_not_read_large_sample_payload(tmp_path,monkeypatch):
    import os
    from thth import audioformats
    payload_size=16*1024*1024
    header=wav(samples=b'')[:-8]
    header=header[:4]+struct.pack('<I',len(header)+payload_size)+header[8:]+b'data'+struct.pack('<I',payload_size)
    path=tmp_path/'large.wav'
    with path.open('wb') as stream:stream.write(header);stream.truncate(len(header)+payload_size)
    original=audioformats.os.pread;reads=[]
    def guarded(fd,n,at):
        reads.append((n,at));assert at+n<=len(header)
        return original(fd,n,at)
    fd=os.open(path,os.O_RDONLY)
    try:
        monkeypatch.setattr(audioformats.os,'pread',guarded)
        info=audioformats.wav(fd,path.stat().st_size)
    finally:os.close(fd)
    assert info.duration==payload_size/4/8000 and max(n for n,_ in reads)<=16


def test_wav_actual_git_approval_and_publish(tmp_path,isolated_account_factory,wire,monkeypatch):
    from pathlib import Path
    from thth import accounts,core
    from thth.adapters.mastodon import MastodonAdapter
    from tests.conftest import init_git_pair,make_queue_text,run_git,approve_via_cli
    text=make_queue_text({'account':'alpha','status':'draft','approved_sha':'','topic':''},body='## mastodon\n\n本文。\n',media='mastodon')
    text=text.replace('\n---\n','\nmedia:\n  - file: sound.wav\n    alt: 音声\n---\n',1)
    pair=init_git_pair(tmp_path,seed_content=text);repo=Path(pair['work']);path=Path(pair['queue_dir'])/'a.md'
    (repo/'sound.wav').write_bytes(wav());run_git(str(repo),['add','.']);run_git(str(repo),['commit','-m','synthetic WAV']);run_git(str(repo),['push'])
    isolated_account_factory(name='alpha',repo_dir=str(repo),media='mastodon',instance=wire['url'],production=True,hashtags=False,char_limit=500,quiet_hours=None,min_interval_hours=0)
    wire['caps']['configuration']['media_attachments']['supported_mime_types']=['audio/wav']
    approved=approve_via_cli(path);assert approved.returncode==0,approved.stderr
    monkeypatch.setattr(accounts,'load_token',lambda _: {'access_token':'synthetic-token'})
    adapter=MastodonAdapter(instance=wire['url'],access_token='synthetic-token')
    wire['upload']=[(200,{'id':'7','type':'audio','url':'https://instance.invalid/a'})]
    core.throw_once('alpha',production_flag=True,adapter_factory=lambda *a:adapter,log=lambda _:None,bypass_pace=True)
    assert len(posts(wire,'/api/v2/media'))==len(posts(wire,'/api/v1/statuses'))==1
