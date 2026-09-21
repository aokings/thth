import json
import pytest
from thth.adapters import base
from thth import accounts,inflight,media,media_delivery
from tests.test_v213_bluesky_video import env,wire,calls
from tests.test_v213_bluesky_metadata import feature,facet


def test_video_metadata_and_quote_nested_fields_survive(env,wire):
    from tests.test_v213_bluesky_record import quote
    fm={'media':[{'file':'v.mp4','alt':'video'}],'attachments':[quote()],
        'post_options':{'languages':['ja'],'labels':['custom-unknown'],'tags':['tag'],'facets':[facet()],'presentation':'gif'}}
    cfg,adapter,_=env;manifest=media.manifest_for(fm,cfg);state=accounts.state_dir_for('alpha');inflight.write(state,file='fixture',started='2030-01-01T00:00:00+09:00')
    result=media_delivery.publish(adapter,base.Post('abc',hashtags_allowed=True),cfg=cfg,fm=fm,manifest=manifest,state_dir=state)
    assert result.post_id
    record=wire['records'][0]['record'];assert record['langs']==['ja'] and record['tags']==['tag']
    assert record['labels']['values']==[{'val':'custom-unknown'}] and record['facets']==[facet()]
    assert record['embed']['media']['presentation']=='gif' and record['embed']['media']['video']==wire['blob']


def test_bad_video_utf8_facet_before_session_or_upload(env,wire):
    fm={'media':[{'file':'v.mp4','alt':'video'}],'post_options':{'facets':[facet(1,3)]}}
    cfg,adapter,_=env;manifest=media.manifest_for(fm,cfg);state=accounts.state_dir_for('alpha');inflight.write(state,file='fixture',started='2030-01-01T00:00:00+09:00')
    result=media_delivery.publish(adapter,base.Post('茶'),cfg=cfg,fm=fm,manifest=manifest,state_dir=state)
    assert result.error=='metadata_invalid: facet_utf8_range' and wire['calls']==[]
