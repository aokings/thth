"""Quote+optimized video preserves caption/presentation and service boundaries."""
import json
import pytest
from thth import media
from tests.test_v213_bluesky_video import env,wire,invoke,calls
from tests.test_v213_bluesky_record import quote


def declaration():
    return {'media':[{'file':'v.mp4','alt':'動画 alt'}],'attachments':[quote()],
            'captions':[{'media_index':1,'file':'ja.vtt','lang':'ja'}],'post_options':{'presentation':'default'}}


def test_record_with_video_captions_keeps_optimized_blob(env,wire):
    (env[2]/'ja.vtt').write_text('WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n字幕\n')
    result,journal,manifest=invoke(env,declaration())
    assert result.post_id and journal['media']['phase']=='published'
    embed=wire['records'][0]['record']['embed']
    assert embed['$type']=='app.bsky.embed.recordWithMedia' and embed['record']['record']=={k:v for k,v in quote().items() if k!='type'}
    video=embed['media'];assert video['$type']=='app.bsky.embed.video' and video['video']==wire['blob']
    assert video['alt']=='動画 alt' and video['presentation']=='default'
    assert video['captions'][0]['lang']=='ja'
    assert len(calls(wire,'app.bsky.video.uploadVideo'))==1 and len(calls(wire,'com.atproto.repo.uploadBlob'))==1
    assert not any('getPosts' in c['nsid'] for c in wire['calls'])
    assert all(secret not in json.dumps(journal) for secret in wire['tokens'].values())


def test_video_quote_invalid_before_all_service_requests(env,wire):
    fm={'media':[{'file':'v.mp4','alt':'v'}],'attachments':[{**quote(),'cid':'bad'}]}
    result,_,_=invoke(env,fm);assert result.error=='invalid_attachment: bluesky/quote_reference' and wire['calls']==[]


def test_quote_video_final_stale_retains_job_without_record(env,wire):
    fm={'media':[{'file':'v.mp4','alt':'v'}],'attachments':[quote()]}
    result,journal,_=invoke(env,fm,veto=lambda:'approval_stale' if calls(wire,'app.bsky.video.uploadVideo') else None)
    assert result.failure=='media_held' and journal['media']['video_job_id']=='fixture-job' and wire['records']==[]
