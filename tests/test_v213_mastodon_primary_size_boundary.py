"""Pinned Mastodon less_than limits for primary image/video/audio uploads."""
import pytest
from thth import media,core
from thth.adapters import mastodon_media as mm
from tests.test_v213_mastodon_media import env,wire,invoke,posts,video_timing
from tests.test_v213_media_foundation import png
from tests.test_v213_mastodon_flac import flac


def configure(env,wire,kind,difference):
    raw={'image':png,'video':lambda:video_timing([(30,1000)],scale=30000),'audio':flac}[kind]()
    filename={'image':'p.png','video':'p.mp4','audio':'p.flac'}[kind];(env[2]/filename).write_bytes(raw)
    field=('image' if kind=='image' else 'video')+'_size_limit'
    cap=wire['caps']['configuration']['media_attachments'];cap['supported_mime_types']=['image/png','video/mp4','audio/flac'];cap[field]=len(raw)-difference
    wire['upload']=[(200,{'id':'7','type':kind,'url':'https://instance.invalid/a'})]
    return {'media':[{'file':filename,'alt':'primary'}]},field,len(raw)


@pytest.mark.parametrize('kind',['image','video','audio'])
@pytest.mark.parametrize('difference',[-1,0,1])
def test_primary_strict_byte_boundary(env,wire,kind,difference):
    fm,field,size=configure(env,wire,kind,difference);result,_,manifest=invoke(env,fm)
    assert manifest['files'][0]['public_size']==size
    assert any(c[:2]==('GET','/api/v2/instance') for c in wire['calls'])
    if difference<0:assert result.post_id and len(posts(wire,'/api/v2/media'))==len(posts(wire,'/api/v1/statuses'))==1
    else:assert result.error=='media_limit_exceeded: bytes' and not posts(wire,'/api/v2/media') and not posts(wire,'/api/v1/statuses')


@pytest.mark.parametrize('kind',['image','video','audio'])
def test_latest_cap_equal_size_overrides_old_permissive_cache(env,wire,kind):
    fm,field,size=configure(env,wire,kind,-1);cfg,adapter,_=env
    old=mm.observe(cfg);assert old[field]==size+1 and mm.cached(cfg)[field]==size+1
    dry=core.send_once('alpha',text='',media_rows=fm['media'],production_flag=False,adapter_factory=lambda *a:adapter,log=lambda _:None)
    wire['caps']['configuration']['media_attachments'][field]=size
    sent=core.send_once('alpha',text='',media_rows=fm['media'],production_flag=True,confirm=dry.digest,adapter_factory=lambda *a:adapter,log=lambda _:None)
    assert not posts(wire,'/api/v2/media') and not posts(wire,'/api/v1/statuses')
    assert sent.action!='sent'
