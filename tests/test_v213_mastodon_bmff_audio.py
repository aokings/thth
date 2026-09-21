"""C14 BMFF audio, unchanged public bytes and inspected retained covers."""
import email.parser,email.policy,hashlib,json,struct,urllib.parse
import pytest
from thth import media,media_delivery,mediaformats as mf
from thth.adapters import mastodon_media as mm
from tests.test_v213_mastodon_media import env,wire,invoke,posts
from tests.test_v213_media_formats import box,segment,exif
from tests.test_v213_media_foundation import png,jpeg


def audio(extra=b'',*,unknown=False,brand=b'M4A ',sample=b'GPSLatitude location covr synthetic audio'):
    mvhd=bytes(12)+struct.pack('>II',1000,0xffffffff if unknown else 2500)+bytes(80)
    sound=bytes(6)+b'\0\1'+bytes(8)+struct.pack('>HHHHI',2,16,0,0,44100<<16)
    track=box(b'tkhd',bytes(84))+box(b'mdia',box(b'hdlr',bytes(8)+b'soun'+bytes(12))+box(b'minf',box(b'stbl',box(b'stsd',bytes(4)+struct.pack('>I',1)+box(b'mp4a',sound)))))
    return box(b'ftyp',brand+bytes(4)+b'isom')+box(b'moov',box(b'mvhd',mvhd)+box(b'trak',track)+extra)+box(b'mdat',sample)


def metadata(cover=None,kind=14):
    title=box(b'\xa9nam',box(b'data',struct.pack('>II',1,0)+b'PRIVATE TITLE'))
    if cover is not None:title+=box(b'covr',box(b'data',struct.pack('>II',kind,0)+cover))
    return box(b'udta',box(b'meta',bytes(4)+box(b'ilst',title)))


def configure(env,wire,raw,*,mime='audio/mp4'):
    path=env[2]/'sound.m4a';path.write_bytes(raw)
    cap=wire['caps']['configuration']['media_attachments']
    cap.update(supported_mime_types=[mime],image_size_limit=1,image_matrix_limit=None,video_size_limit=len(raw)+1,video_matrix_limit=None,video_frame_rate_limit=None)
    wire['upload']=[(200,{'id':'7','type':'audio','url':'https://instance.invalid/audio'})]
    return {'media':[{'file':'sound.m4a','alt':'音声の説明'}]}


@pytest.mark.parametrize('mime',['audio/mp4','audio/m4a','audio/x-m4a'])
@pytest.mark.parametrize('cover,kind',[(None,14),(png(),14),(jpeg(),13)])
def test_audio_exact_multipart_and_non_location_metadata(env,wire,mime,cover,kind):
    raw=audio(metadata(cover,kind));fm=configure(env,wire,raw,mime=mime)
    result,journal,manifest=invoke(env,fm)
    assert result.post_id and journal['media']['phase']=='published'
    row=manifest['files'][0]
    assert row['kind']=='audio' and row['width'] is row['height'] is None and row['duration']==2.5
    assert row['source_sha256']==row['public_sha256']==hashlib.sha256(raw).hexdigest()
    call=posts(wire,'/api/v2/media')[0]
    msg=email.parser.BytesParser(policy=email.policy.default).parsebytes(('Content-Type: '+call[3]['Content-Type']+'\r\n\r\n').encode()+call[2])
    parts={part.get_param('name',header='content-disposition'):part for part in msg.iter_parts()}
    assert parts['file'].get_payload(decode=True)==raw and parts['file'].get_content_type()==mime
    assert parts['description'].get_payload(decode=True).decode()=='音声の説明'
    assert urllib.parse.parse_qs(posts(wire,'/api/v1/statuses')[0][2].decode())['media_ids[]']==['7']
    assert 'non_location_metadata_retained' in row['metadata_notes']
    assert ('embedded_cover_retained' in row['metadata_notes'])==(cover is not None)
    assert 'PRIVATE TITLE' not in media.display(manifest) and 'non_location_metadata_retained' in media.display(manifest)
    assert media.prepared_component(manifest)
    notes=media_delivery.lint_notes(env[0],fm,text='body')
    assert any('non_location_metadata_retained' in note for note in notes)


@pytest.mark.parametrize('terminal',[(200,{'id':'7','type':'audio','url':'https://instance.invalid/a'}),(200,{'id':'7','type':'video','url':'https://instance.invalid/a'}),(404,{}),(422,{})])
def test_audio_202_206_terminal_preserves_type(env,wire,terminal):
    fm=configure(env,wire,audio());wire['upload']=[(202,{'id':'7','type':'audio','url':None})];wire['poll']=[(206,{}),terminal]
    result,journal,_=invoke(env,fm)
    ok=terminal[0]==200 and terminal[1]['type']=='audio'
    assert bool(result.post_id)==ok and bool(posts(wire,'/api/v1/statuses'))==ok
    if not ok:assert journal['media']['phase']=='held' and journal['media']['remote_ids']==['7']


@pytest.mark.parametrize('change,reason',[
    ({'supported_mime_types':['image/png']},'unsupported_attachment'),
    ({'video_size_limit':1},'media_limit_exceeded: bytes'),
    ({'video_size_limit':None},'media_capability_unavailable'),
    ({'description_limit':1},'media_limit_exceeded: alt')])
def test_audio_latest_caps_before_any_upload(env,wire,change,reason):
    fm=configure(env,wire,audio());wire['caps']['configuration']['media_attachments'].update(change)
    result,_,_=invoke(env,fm)
    assert reason in result.error and not posts(wire,'/api/v2/media') and not posts(wire,'/api/v1/statuses')


def test_unknown_audio_duration_and_dimensions_are_not_zero(env,wire):
    fm=configure(env,wire,audio(unknown=True));manifest=media.manifest_for(fm,env[0]);row=manifest['files'][0]
    assert row['duration'] is row['width'] is row['height'] is None
    assert '秒数: 未取得' in media.display(manifest) and '寸法: 適用外' in media.display(manifest)
    result,_,_=invoke(env,fm);assert result.post_id


@pytest.mark.parametrize('cover,kind,reason',[
    (jpeg()[:2]+segment(0xe1,b'Exif\0\0'+exif())+jpeg()[2:],13,'location_metadata_present'),
    (png()[:-12]+mf._chunk(b'eXIf',exif())+png()[-12:],14,'location_metadata_present'),
    (jpeg()[:2]+segment(0xe1,b'http://ns.adobe.com/xap/1.0/\0<x xmlns:e="urn:exif" e:GPSLatitude="12"/>')+jpeg()[2:],13,'location_metadata_present'),
    (jpeg()[:2]+segment(0xe1,b'http://ns.adobe.com/xap/1.0/\0<x/>')+jpeg()[2:],13,'location_metadata_unverifiable'),
    (b'unknown cover',14,'invalid_attachment_structure'),
    (png(),0,'location_metadata_unverifiable'),
    (png()[:-1],14,'location_metadata_unverifiable'),
    (jpeg()[:2]+segment(0xe0,b'JFXX\0\x10'+jpeg())+jpeg()[2:],13,'location_metadata_unverifiable')])
def test_embedded_cover_cannot_publish_location_or_unknown_structure(env,wire,cover,kind,reason):
    raw=audio(metadata(cover,kind));fm=configure(env,wire,raw)
    with pytest.raises(media.MediaError,match=reason):media.manifest_for(fm,env[0])
    assert not wire['calls'] and (env[2]/'sound.m4a').read_bytes()==raw


def test_cover_comment_retained_without_metadata_string_scanning(env,wire):
    cover=jpeg()[:2]+segment(0xfe,b'non-location COMMENT')+jpeg()[2:]
    raw=audio(metadata(cover,13));fm=configure(env,wire,raw)
    with media.prepare(env[0]['repo_dir'],fm,'mastodon') as (manifest,items):
        assert b''.join(items[0].chunks())==raw
        assert manifest['files'][0]['public_sha256']==hashlib.sha256(raw).hexdigest()


def test_unknown_bmff_privacy_and_cover_replacement_stale(env,wire):
    raw=audio(metadata(png()));fm=configure(env,wire,raw)
    before=media.prepared_component(media.manifest_for(fm,env[0]))
    changed=audio(metadata(jpeg(),13));(env[2]/'sound.m4a').write_bytes(changed)
    assert before!=media.prepared_component(media.manifest_for(fm,env[0]))
    (env[2]/'sound.m4a').write_bytes(audio(box(b'uuid',b'unknown')))
    with pytest.raises(media.MediaError,match='location_metadata_unverifiable'):media.manifest_for(fm,env[0])


def test_audio_processing_source_change_preserves_held_and_prevents_status(env,wire):
    fm=configure(env,wire,audio());wire['upload']=[(202,{'id':'7','type':'audio','url':None})];wire['poll']=[(200,{'id':'7','type':'audio','url':'https://instance.invalid/a'})]
    wire['on_poll']=lambda:(env[2]/'sound.m4a').write_bytes(audio(sample=b'changed'))
    result,journal,_=invoke(env,fm)
    assert result.failure=='media_held' and journal['media']['remote_ids']==['7'] and not posts(wire,'/api/v1/statuses')


@pytest.mark.parametrize('version',[1,2])
@pytest.mark.parametrize('change',[False,True])
def test_audio_actual_git_approval_and_cover_change_veto(tmp_path,isolated_account_factory,wire,monkeypatch,version,change):
    import datetime,secrets
    from pathlib import Path
    from thth import accounts,core,jst,threadthrow
    from thth.adapters.mastodon import MastodonAdapter
    from tests.conftest import init_git_pair,make_queue_text,run_git,approve_via_cli
    now=jst.now_jst();until=(now+datetime.timedelta(hours=1)).isoformat()
    if version==1:
        text=make_queue_text({'account':'alpha','status':'draft','approved_sha':'','topic':''},body='## mastodon\n\n本文。\n',media='mastodon')
        text=text.replace('\n---\n','\nmedia:\n  - file: sound.m4a\n    alt: 音声\n---\n',1)
    else:
        from tests.test_thread_publish import bundle_text
        text=bundle_text(account='alpha',segments=['一段目','二段目'],status='draft',topic='',publish_at=now.isoformat(),continue_until=until).replace('## threads','## mastodon')
        for n in (1,2):text=text.replace('  - index: '+str(n),'  - index: '+str(n)+'\n    media:\n      - file: sound.m4a\n        alt: 音声')
    pair=init_git_pair(tmp_path,seed_content=text);repo=Path(pair['work']);path=Path(pair['queue_dir'])/'a.md'
    raw=audio(metadata(png()));(repo/'sound.m4a').write_bytes(raw)
    run_git(str(repo),['add','.']);run_git(str(repo),['commit','-m','synthetic sound']);run_git(str(repo),['push'])
    isolated_account_factory(name='alpha',repo_dir=str(repo),media='mastodon',instance=wire['url'],production=True,hashtags=False,char_limit=500,quiet_hours=None,min_interval_hours=0)
    wire['caps']['configuration']['media_attachments']['supported_mime_types']=['audio/mp4']
    approved=approve_via_cli(path);assert approved.returncode==0,approved.stderr
    token=secrets.token_urlsafe(30);monkeypatch.setattr(accounts,'load_token',lambda _: {'access_token':token})
    adapter=MastodonAdapter(instance=wire['url'],access_token=token)
    wire['upload']=[(202,{'id':'7','type':'audio','url':None})];wire['poll']=[(200,{'id':'7','type':'audio','url':'https://instance.invalid/a'})]
    if change:wire['on_poll']=lambda:(repo/'sound.m4a').write_bytes(audio(metadata(jpeg(),13)))
    if version==1:core.throw_once('alpha',production_flag=True,adapter_factory=lambda *a:adapter,log=lambda _:None,bypass_pace=True)
    else:threadthrow.publish_bundle('alpha',str(path.relative_to(repo)),adapter_factory=lambda *a:adapter,now=now,log=lambda _:None,max_posts=1)
    assert bool(posts(wire,'/api/v1/statuses')) is (not change)


def test_audio_unknown_publication_does_not_reupload(env,wire):
    from thth import core
    fm=configure(env,wire,audio());wire['status']=(500,{})
    result,journal,_=invoke(env,fm)
    assert result.failure=='media_ambiguous' and journal['media']['phase']=='unknown'
    count=len(wire['calls'])
    again=core.send_once('alpha',text='body',production_flag=True,confirm='unused',adapter_factory=lambda *a:env[1],log=lambda _:None)
    assert again.action=='inflight' and len(wire['calls'])==count


def test_mov_sound_uses_observed_quicktime_mime(env,wire):
    fm=configure(env,wire,audio(brand=b'qt  '),mime='video/quicktime')
    result,_,manifest=invoke(env,fm)
    assert result.post_id and manifest['files'][0]['format']=='mov' and manifest['files'][0]['kind']=='audio'


def test_cover_missing_value_and_nested_thumbnail_are_unverified(env):
    missing=box(b'udta',box(b'meta',bytes(4)+box(b'ilst',box(b'covr',b''))))
    path=env[2]/'sound.m4a';path.write_bytes(audio(missing));fm={'media':[{'file':'sound.m4a','alt':'sound'}]}
    with pytest.raises(media.MediaError,match='embedded_cover'):media.manifest_for(fm,env[0])
    data=b'II*\0\x08\0\0\0'+struct.pack('<H',1)+struct.pack('<HHII',0x201,4,1,0)+bytes(4)
    cover=jpeg()[:2]+segment(0xe1,b'Exif\0\0'+data)+jpeg()[2:]
    path.write_bytes(audio(metadata(cover,13)))
    with pytest.raises(media.MediaError,match='location_metadata_unverifiable'):media.manifest_for(fm,env[0])
