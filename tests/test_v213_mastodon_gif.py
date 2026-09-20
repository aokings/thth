"""GIF source privacy exception and actual local multipart/async wire."""
import email.parser
import email.policy
import hashlib
import urllib.parse
import pytest
from thth import media
from thth.adapters import mastodon_media as mm
from tests.test_v213_mastodon_media import env, wire, invoke, posts
from tests.test_v213_media_formats import gif


def source(frames):
    value=gif()
    frame=value[value.index(b'\x2c'): -1]
    return value[:-1]+frame*(frames-1)+b'\x3b'


def setup(env,wire,frames=2):
    raw=source(frames);(env[2]/'a.gif').write_bytes(raw)
    wire['caps']['configuration']['media_attachments'].update(
        supported_mime_types=['image/gif','image/png'],image_size_limit=len(raw),
        image_matrix_limit=1,video_size_limit=1,video_matrix_limit=1,video_frame_rate_limit=1)
    return raw,{'media':[{'file':'a.gif','alt':'二つの点 🌿'}]}


@pytest.mark.parametrize('frames',[1,2])
@pytest.mark.parametrize('kind',['image','gifv'])
@pytest.mark.parametrize('pending',[False,True])
def test_source_gif_keeps_bytes_and_uses_source_image_caps(env,wire,frames,kind,pending):
    raw,fm=setup(env,wire,frames)
    ready={'id':'7','type':kind,'url':'https://example.invalid/gif'}
    wire['upload']=[(202,{**ready,'url':None})] if pending else [(200,ready)]
    if pending:wire['poll']=[(206,{}),(200,ready)]
    result,journal,manifest=invoke(env,fm)
    assert result.post_id=='100' and journal['media']['phase']=='published'
    item=manifest['files'][0];assert item['format']=='gif' and item['kind']=='image'
    assert item['source_sha256']==item['public_sha256']==hashlib.sha256(raw).hexdigest()
    assert b'PRIVATE' in raw  # C2 permits GIF comments unchanged; not a sanitizer.
    call=posts(wire,'/api/v2/media')[0]
    msg=email.parser.BytesParser(policy=email.policy.default).parsebytes(('Content-Type: '+call[3]['Content-Type']+'\r\n\r\n').encode()+call[2])
    fields={p.get_param('name',header='Content-Disposition'):p for p in msg.iter_parts()}
    assert fields['file'].get_payload(decode=True)==raw
    assert fields['file'].get_content_type()=='image/gif'
    assert fields['description'].get_payload(decode=True).decode()==fm['media'][0]['alt']
    assert urllib.parse.parse_qs(posts(wire,'/api/v1/statuses')[0][2].decode())['media_ids[]']==['7']


@pytest.mark.parametrize('fault',['size','mime','broken'])
def test_gif_rejects_before_upload(env,wire,fault):
    raw,fm=setup(env,wire)
    if fault=='size':wire['caps']['configuration']['media_attachments']['image_size_limit']=len(raw)-1
    elif fault=='mime':wire['caps']['configuration']['media_attachments']['supported_mime_types']=['image/png']
    else:(env[2]/'a.gif').write_bytes(raw[:-1])
    if fault=='broken':
        with pytest.raises(media.MediaError):invoke(env,fm)
    else:
        result,_,_=invoke(env,fm);assert result.error
    assert not posts(wire,'/api/v2/media') and not posts(wire,'/api/v1/statuses')


def test_gif_stale_after_known_processing_result_is_held(env,wire):
    raw,fm=setup(env,wire)
    wire['upload']=[(202,{'id':'7','type':'gifv','url':None})]
    wire['poll']=[(200,{'id':'7','type':'gifv','url':'https://example.invalid/gif'})]
    wire['on_poll']=lambda:(env[2]/'a.gif').write_bytes(raw+b'changed')
    result,journal,_=invoke(env,fm)
    assert result.failure=='media_held' and journal['media']['phase']=='held'
    assert not posts(wire,'/api/v1/statuses')


def test_gif_and_image_keep_provider_order(env,wire):
    raw,fm=setup(env,wire);fm['media'].append({'file':'a.png','alt':'静止画'})
    wire['caps']['configuration']['media_attachments']['image_size_limit']=100000
    wire['upload']=[(200,{'id':'7','type':'gifv','url':'https://example.invalid/gif'}),(200,{'id':'2','type':'image','url':'https://example.invalid/image'})]
    result,_,_=invoke(env,fm);assert result.post_id=='100'
    assert urllib.parse.parse_qs(posts(wire,'/api/v1/statuses')[0][2].decode())['media_ids[]']==['7','2']
