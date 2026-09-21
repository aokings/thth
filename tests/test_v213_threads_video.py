"""C11 video facts vs warnings, unchanged bytes, and C10 finite publication."""
import copy
import os
import struct
import pytest
from thth import media,mediaformats,media_delivery,media_relay
from thth.adapters import threads_media as tm
from tests.test_v213_threads_media import env,wire,invoke,posts
from tests.test_v213_media_formats import box,mp4
from tests.test_v213_mastodon_media import video_timing


def edit_movie(*,version=0,count=1,extra=b'',front=True):
    raw=mp4(box(b'edts',box(b'elst',bytes([version])+b'\0'*3+struct.pack('>I',count)+b'\0'*12))+extra)
    if not front:
        n=int.from_bytes(raw[:4],'big');m=int.from_bytes(raw[n:n+4],'big');raw=raw[:n]+raw[n+m:]+raw[n:n+m]
    return raw


def prepared(tmp_path,raw,medium='threads'):
    (tmp_path/'v.mp4').write_bytes(raw)
    return media.prepare(tmp_path,{'media':[{'file':'v.mp4','alt':'動画'}]},medium)


def test_valid_edit_list_and_late_moov_warn_only_threads(tmp_path):
    raw=edit_movie(front=False)
    with prepared(tmp_path,raw) as(m,items):
        notes=tm.notes(m,items)
        assert b''.join(items[0].chunks())==raw
        assert any('edit lists' in n for n in notes) and any('moov follows' in n for n in notes)
        assert any('frame rate unobserved' in n for n in notes)
    with pytest.raises(media.MediaError,match='duration_unverifiable'):
        with prepared(tmp_path,raw,'mastodon'):pass


@pytest.mark.parametrize('raw,reason',[(edit_movie(version=2),'invalid_attachment_structure'),(edit_movie(count=2),'invalid_attachment_structure'),(edit_movie(extra=box(b'uuid',b'private')),'location_metadata_unverifiable'),(edit_movie(extra=box(b'free',b'private')),'location_metadata_unverifiable'),(edit_movie(extra=box(b'ZZZZ',b'')),'location_metadata_unverifiable'),(edit_movie(extra=box(b'\xa9xyz',b'private')),'location_metadata_present')])
def test_warning_policy_does_not_bypass_structure_or_privacy(tmp_path,raw,reason):
    with pytest.raises(media.MediaError,match=reason):
        with prepared(tmp_path,raw):pass


@pytest.mark.parametrize('field,value,ok',[('duration',0,False),('duration',.001,True),('duration',300,True),('duration',300.001,False),('duration',None,False),('public_size',1_000_000_000,True),('public_size',1_000_000_001,False),('width',1920,True),('width',1921,False)])
def test_c11_video_limits(tmp_path,field,value,ok):
    with prepared(tmp_path,mp4()) as(m,_):
        m=copy.deepcopy(m);m['files'][0][field]=value
        assert (tm.intent_error(m) is None)==ok


@pytest.mark.parametrize('w,h,ok',[(1,100,True),(1,101,False),(100,10,True),(101,10,False),(1920,1920,True)])
def test_video_ratio_is_asymmetric(tmp_path,w,h,ok):
    with prepared(tmp_path,mp4()) as(m,_):
        m=copy.deepcopy(m);m['files'][0].update(width=w,height=h)
        assert (tm.intent_error(m) is None)==ok


@pytest.mark.parametrize('scale,ok',[(22999,False),(23000,True),(23976,True),(60000,True),(60001,False)])
def test_observed_average_fps_has_no_mastodon_floor(tmp_path,scale,ok):
    with prepared(tmp_path,video_timing([(30,1000)],scale=scale)) as(m,items):
        notes=tm.notes(m,items)
        assert any('frame_rate_outside_23_60' in n for n in notes) is (not ok)
        assert any('not certified' in n for n in notes)


@pytest.mark.parametrize('codec,ok',[(b'avc1',True),(b'hev1',True),(b'vp09',False)])
def test_known_codec_vs_unobserved_bitstream(tmp_path,codec,ok):
    raw=video_timing([(30,1000)],scale=30000).replace(b'avc1',codec)
    with prepared(tmp_path,raw) as(m,items):
        notes=tm.notes(m,items)
        assert any('GOP/chroma' in n for n in notes)
        assert any('video_codec_declared_not_recommended' in n for n in notes) is (not ok)


@pytest.mark.parametrize('order',[['v.mp4'],['a.png','v.mp4'],['v.mp4','a.png']])
def test_video_and_mixed_wire_kind_order_and_bytes(env,wire,order):
    raw=video_timing([(30,1000)],scale=30000);(env[2]/'v.mp4').write_bytes(raw)
    fm={'media':[{'file':f,'alt':'説明 '+str(i)} for i,f in enumerate(order)]}
    result,journal,m=invoke(env,fm)
    assert result.post_id=='100' and journal['media']['phase']=='published'
    create=posts(wire,'/threads');items=create[:len(order)]
    assert [x[2]['media_type'][0] for x in items]==['VIDEO' if f.endswith('mp4') else 'IMAGE' for f in order]
    assert [('video_url' in x[2], 'image_url' in x[2]) for x in items]==[(f.endswith('mp4'),not f.endswith('mp4')) for f in order]
    assert [x[2]['alt_text'][0] for x in items]==[row['alt'] for row in fm['media']]
    assert wire['fetched']==env[3]['upload'] and raw in wire['fetched']
    assert [row['kind'] for row in result.media]==['video' if f.endswith('mp4') else 'image' for f in order]
    assert len(posts(wire,'/threads_publish'))==1 and env[3]['results']==[True]*len(order)
    if len(order)>1:assert create[-1][2]['children']==['11,12']


def test_video_can_wait_beyond_image_deadline_but_not_video_deadline(env,wire,monkeypatch):
    (env[2]/'v.mp4').write_bytes(mp4());clock=[0.0]
    monkeypatch.setattr(tm.time,'monotonic',lambda:clock[0]);wire['on_poll']=lambda:clock.__setitem__(0,121)
    result,_,_=invoke(env,{'media':[{'file':'v.mp4','alt':'動画'}]});assert result.post_id=='100'


def test_video_response_at_deadline_held_no_publication(env,wire,monkeypatch):
    (env[2]/'v.mp4').write_bytes(mp4());clock=[0.0]
    monkeypatch.setattr(tm.time,'monotonic',lambda:clock[0]);wire['on_poll']=lambda:clock.__setitem__(0,1800)
    result,_,_=invoke(env,{'media':[{'file':'v.mp4','alt':'動画'}]})
    assert result.failure=='media_held' and not posts(wire,'/threads_publish')


def test_mixed_uses_earliest_image_grant_expiry(env,wire,monkeypatch):
    (env[2]/'v.mp4').write_bytes(mp4());clock=[tm.time.time()]
    monkeypatch.setattr(tm.time,'time',lambda:clock[0])
    def after_get():
        if len(posts(wire,'/threads'))==2:clock[0]+=601
    wire['on_poll']=after_get
    result,_,_=invoke(env,{'media':[{'file':'a.png','alt':'画像'},{'file':'v.mp4','alt':'動画'}]})
    assert result.failure=='media_held' and result.error=='media_provider_url_expired'
    assert not posts(wire,'/threads_publish') and env[3]['results']==[False,False]


@pytest.mark.parametrize('channels,rate,codec,ok',[(1,48000,b'mp4a',True),(2,48000,b'mp4a',True),(3,48000,b'mp4a',False),(2,60000,b'mp4a',False),(2,48000,b'ac-3',False),(0,0,b'mp4a',True)])
def test_audio_header_observations_unknown_is_not_zero_failure(tmp_path,channels,rate,codec,ok):
    sample=b'\0'*16+struct.pack('>HHHHI',channels,16,0,0,rate<<16)
    track=box(b'trak',box(b'mdia',box(b'hdlr',b'\0'*8+b'soun'+b'\0'*12)+box(b'minf',box(b'stbl',box(b'stsd',b'\0'*4+struct.pack('>I',1)+box(codec,sample))))))
    with prepared(tmp_path,mp4(track)) as(m,items):
        notes=tm.notes(m,items)
        assert any('audio AAC/bitrate' in n for n in notes)
        assert any('audio_codec_declared_not_recommended' in n or 'audio_channels_declared_above_2' in n or 'audio_sample_rate_declared_above_48k' in n for n in notes) is (not ok)


@pytest.mark.parametrize('ext,ok',[(box(b'fiel',b'\x01\0'),True),(box(b'fiel',b'\x02\0'),False),(box(b'btrt',struct.pack('>III',0,100000000,1)),True),(box(b'btrt',struct.pack('>III',0,100000001,1)),False)])
def test_known_video_header_constraints(tmp_path,ext,ok):
    sample=b'\0'*24+struct.pack('>HH',640,480)+b'\0'*50
    raw=mp4(box(b'stsd',b'\0'*4+struct.pack('>I',1)+box(b'avc1',sample+ext)))
    with prepared(tmp_path,raw) as(m,items):
        notes=tm.notes(m,items)
        assert any('interlaced_video_declared' in n or 'video_bitrate_declared_above_100mbps' in n for n in notes) is (not ok)


def test_declared_frame_rate_warning_does_not_preempt_provider(env,wire):
    raw=video_timing([(30,1000)],scale=61000);(env[2]/'v.mp4').write_bytes(raw)
    fm={'media':[{'file':'v.mp4','alt':'動画'}]}
    assert any('frame_rate' in note for note in media_delivery.lint_notes(env[0],fm))
    result,_,_=invoke(env,fm)
    assert result.post_id and result.failure=='none' and env[3]['upload'] and posts(wire,'/threads_publish')


@pytest.mark.parametrize('version,entry',[(0,struct.pack('>IiHH',2500,-1,1,0)),(1,struct.pack('>QqHH',2500,-1,1,0))])
def test_valid_elst_versions_empty_edit_preserve_bytes(tmp_path,version,entry):
    raw=mp4(box(b'edts',box(b'elst',bytes([version])+b'\0'*3+struct.pack('>I',1)+entry)))
    with prepared(tmp_path,raw) as(m,items):
        assert b''.join(items[0].chunks())==raw
        assert any('edit lists' in x for x in tm.notes(m,items))


def test_mov_single_and_twenty_mixed_children_preserve_order(env,wire):
    raw=mp4().replace(b'isom',b'qt  ');(env[2]/'v.mov').write_bytes(raw)
    result,_,manifest=invoke(env,{'media':[{'file':'v.mov','alt':'MOV'}]})
    assert manifest['files'][0]['format']=='mov' and result.post_id=='100'
    wire['calls'].clear();wire['fetched'].clear();env[3]['upload'].clear();env[3]['results'].clear()
    rows=[]
    for i in range(20):
        name=f'item{i}.mov' if i%2 else f'item{i}.png'
        (env[2]/name).write_bytes(raw if i%2 else (env[2]/'a.png').read_bytes())
        rows.append({'file':name,'alt':f'item {i}'})
    result,journal,_=invoke(env,{'media':rows})
    assert result.post_id=='100' and journal['media']['phase']=='published'
    create=posts(wire,'/threads');assert len(create)==21
    assert create[-1][2]['children']==[','.join(str(11+i) for i in range(20))]
    assert [x[2]['alt_text'][0] for x in create[:20]]==[r['alt'] for r in rows]
    assert [r['kind'] for r in result.media]==['video' if i%2 else 'image' for i in range(20)]
    assert env[3]['results']==[True]*20 and len(posts(wire,'/threads_publish'))==1


def test_audio_only_never_becomes_video_success(env,wire):
    (env[2]/'a.mp4').write_bytes(mp4(handler=b'soun'))
    result,_,_=invoke(env,{'media':[{'file':'a.mp4','alt':'audio'}]})
    assert result.post_id is None and result.error.startswith('unsupported_attachment: threads/')
    assert not env[3]['upload'] and not wire['calls']


def declared_movie(kind,maximum=None,average=0):
    ext=b'' if maximum is None else box(b'btrt',struct.pack('>III',0,maximum,average))
    if kind=='audio':
        sample=b'\0'*16+struct.pack('>HHHHI',2,16,0,0,48000<<16)
        table=box(b'stsd',b'\0'*4+struct.pack('>I',1)+box(b'mp4a',sample+ext))
        extra=box(b'trak',box(b'mdia',box(b'hdlr',b'\0'*8+b'soun'+b'\0'*12)+box(b'minf',box(b'stbl',table))))
    else:
        sample=b'\0'*24+struct.pack('>HH',640,480)+b'\0'*50
        extra=box(b'stsd',b'\0'*4+struct.pack('>I',1)+box(b'avc1',sample+ext))
    return mp4(extra)


@pytest.mark.parametrize('kind,limit',[('audio',128000),('video',100000000)])
@pytest.mark.parametrize('which',['missing','zero','below','equal','max','avg','both'])
def test_c12_declared_bitrates_are_separate_observations_not_rejections(tmp_path,kind,limit,which):
    maximum,average={'missing':(None,0),'zero':(0,0),'below':(limit-1,limit-1),
                     'equal':(limit,limit),'max':(limit+1,0),'avg':(0,limit+1),
                     'both':(limit+1,limit+2)}[which]
    raw=declared_movie(kind,maximum,average)
    with prepared(tmp_path,raw) as(m,items):
        notes=tm.notes(m,items)
        facts=mediaformats.threads_video_info(items[0]._public_fd,len(raw))
        observed=facts['audio'][0]['bitrate_declared'] if kind=='audio' else (facts['bitrates'][0] if facts['bitrates'] else None)
        assert observed==(None if maximum is None else {'max':maximum,'avg':average})
        assert any(kind+'_bitrate_declared_above_' in n for n in notes) is (which in ('max','avg','both'))
        if maximum is not None:assert any(f'{kind}_bitrate_declared: max={maximum}, avg={average}' in n for n in notes)
        assert any('not certified' in n for n in notes)
        assert tm.intent_error(m) is None and b''.join(items[0].chunks())==raw


@pytest.mark.parametrize('kind,maximum,average',[('audio',128001,0),('audio',0,128001),('video',0,100000001)])
def test_c12_high_declarations_warn_in_lint_and_reach_provider(env,wire,kind,maximum,average):
    raw=declared_movie(kind,maximum,average);(env[2]/'v.mp4').write_bytes(raw)
    fm={'media':[{'file':'v.mp4','alt':'動画'}]}
    notes=media_delivery.lint_notes(env[0],fm)
    assert all(n.startswith('warning: ') for n in notes)
    assert any(kind+'_bitrate_declared_above_' in n for n in notes)
    assert not env[3]['upload'] and not wire['calls']
    result,journal,_=invoke(env,fm)
    assert result.post_id and result.failure=='none'
    assert journal['media']['phase']=='published' and posts(wire,'/threads_publish')


@pytest.mark.parametrize('payload',[b'\0'*11,b'\0'*13])
def test_c12_warning_does_not_accept_malformed_bitrate_box(tmp_path,payload):
    sample=b'\0'*16+struct.pack('>HHHHI',2,16,0,0,48000<<16)
    table=box(b'stsd',b'\0'*4+struct.pack('>I',1)+box(b'mp4a',sample+box(b'btrt',payload)))
    with prepared(tmp_path,mp4(table)) as(m,items):
        with pytest.raises(mediaformats.FormatError,match='invalid_attachment_structure'):
            tm.notes(m,items)

@pytest.mark.parametrize('order',[['a.png','v.mp4'],['v.mp4','a.png']])
def test_mixed_poll_timeout_uses_minimum_live_grant(env,wire,monkeypatch,order):
    (env[2]/'v.mp4').write_bytes(mp4());clock=[tm.time.time()];timeouts=[]
    monkeypatch.setattr(tm.time,'time',lambda:clock[0]);monkeypatch.setattr(tm.time,'monotonic',lambda:0.)
    def created():
        if len(posts(wire,'/threads'))==2:clock[0]+=590
    wire['on_create']=created
    original=tm._json
    def request(adapter,method,path,params,**kwargs):
        if method=='GET' and len(posts(wire,'/threads'))==2:
            timeouts.append(kwargs['timeout']);assert 9<=kwargs['timeout']<=10
            clock[0]+=11
        return original(adapter,method,path,params,**kwargs)
    monkeypatch.setattr(tm,'_json',request)
    result,_,_=invoke(env,{'media':[{'file':f,'alt':f} for f in order]})
    assert result.error=='media_provider_url_expired' and result.failure=='media_held'
    assert len(timeouts)==1 and not posts(wire,'/threads_publish')
    assert env[3]['results']==[False,False]


@pytest.mark.parametrize('codec',[b'avc1',b'avc3',b'hvc1',b'hev1',b'vp09',b'av01',b'mp4v',b'jpeg',b'mjpa',b'mjpb'])
def test_visual_sample_entry_privacy_and_observation_agree(tmp_path,codec):
    sample=bytes(24)+struct.pack('>HH',640,480)+bytes(50)
    raw=mp4(box(b'stsd',bytes(4)+struct.pack('>I',1)+box(codec,sample)))
    with prepared(tmp_path,raw) as(manifest,items):
        facts=mediaformats.threads_video_info(items[0]._public_fd,len(raw))
        assert codec.decode('ascii') in facts['video_codecs'] and facts['audio']==[]

@pytest.mark.parametrize('doctype,reason',[(b'webm','webm'),(b'matroska','unknown format')])
def test_webm_rejection_is_named_without_upload(env,wire,doctype,reason,monkeypatch):
    raw=b'\x42\x82'+bytes([0x80+len(doctype)])+doctype
    (env[2]/'v.webm').write_bytes(b'\x1aE\xdf\xa3'+bytes([0x80+len(raw)])+raw)
    monkeypatch.setattr(media_relay,'MediaRelay',lambda *a:pytest.fail('unsupported WebM relay'))
    with pytest.raises(media.MediaError,match='threads/'+reason):
        media.manifest_for({'media':[{'file':'v.webm','alt':'video'}]},env[0])
    assert wire['calls']==[] and env[3]['upload']==[]
