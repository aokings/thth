"""Threads image/video containers from immutable bytes and private R2 grants.

C11: JPEG/PNG, provisional SI 8 MB, ratio <=10 either way. Numeric byte
boundary is an adjudicated temporary contract, not a verified API integer cap.
"""
from __future__ import annotations
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from . import base
from .. import accounts,approval_relay,httpsafe,jst,leave_gate,media,media_relay,mediaformats

MAX_BYTES=8_000_000
POLL_SECONDS=120.0
VIDEO_POLL_SECONDS=1800.0
MAX_VIDEO_BYTES=1_000_000_000
POLL_INTERVAL=2.0


def require(ok,reason):
    if not ok:raise media.MediaError(reason)


def intent_error(manifest):
    if manifest['captions']:return 'unsupported_attachment: threads/typed_attachment'
    if manifest['attachments']:
        for a in manifest['attachments']:
            if a['type']=='link':
                if manifest['files']:return 'unsupported_attachment: threads/link_requires_text'
                if set(a)!={'type','url'}:return 'unsupported_attachment: threads/link_option'
            elif a['type']=='quote':
                if set(a)!={'type','uri'}:return 'unsupported_attachment: threads/quote_option'
                if type(a['uri']) is not str or not a['uri'].isascii() or not a['uri'].isdecimal():return 'invalid_attachment: threads/quote_id'
            elif a['type']=='poll':
                if set(a)!={'type','options'}:return 'unsupported_attachment: threads/poll_option'
                if any(row['type'] not in ('poll','quote') for row in manifest['attachments']):return 'unsupported_attachment: threads/poll_combination'
                if not 2<=len(a['options'])<=4:return 'media_limit_exceeded: poll_options'
                # C20-B: provisional code-point count for this new field only.
                if any(not 1<=len(option)<=25 for option in a['options']):return 'media_limit_exceeded: poll_option_characters'
            elif a['type']=='gif':
                if manifest['files']:return 'unsupported_attachment: threads/gif_requires_text'
                if set(a)!={'type','provider','id'}:return 'unsupported_attachment: threads/gif_option'
                if a['provider']!='GIPHY':return 'unsupported_attachment: threads/gif_provider'
            else:return 'unsupported_attachment: threads/typed_attachment'
    if manifest['post_options']:return 'unsupported_attachment: threads/image_post_options'
    rows=manifest['files']
    if not rows and manifest['attachments']:return None
    if not 1<=len(rows)<=20:return 'media_limit_exceeded: count'
    for row in rows:
        video=row['kind']=='video' and row['format'] in ('mp4','mov')
        image=row['kind']=='image' and row['format'] in ('jpeg','png')
        if row['role']!='media' or not (video or image):return 'unsupported_attachment: threads/'+str(row['format'])
        if type(row['public_size']) is not int or row['public_size']<1:return 'media_size_unavailable'
        if row['public_size']>(MAX_VIDEO_BYTES if video else MAX_BYTES):return 'media_limit_exceeded: bytes (provisional SI '+str(MAX_VIDEO_BYTES if video else MAX_BYTES)+')'
        w,h=row.get('width'),row.get('height')
        if type(w) not in (int,float) or type(h) not in (int,float) or not math.isfinite(w) or not math.isfinite(h) or w<=0 or h<=0:return 'media_dimensions_unavailable'
        if video:
            d=row.get('duration')
            if type(d) not in (int,float) or not math.isfinite(d):return 'media_duration_unavailable'
            if not 0<d<=300:return 'media_limit_exceeded: duration'
            if w>1920:return 'media_limit_exceeded: width'
            if w*100<h or w>10*h:return 'media_limit_exceeded: aspect_ratio'
        elif type(w) is not int or type(h) is not int:return 'media_dimensions_unavailable'
        elif max(w,h)>10*min(w,h):return 'media_limit_exceeded: aspect_ratio'
    return None



def text_params(manifest,text):
    """C15 explicit link preview: publication data only; never fetch the URL."""
    if manifest['files']:return None
    require(type(text) is str,'media_text_unavailable')
    links=[a['url'] for a in manifest['attachments'] if a['type']=='link']
    # Count lexical HTTP(S) links without network normalization or fetching.
    visible={v.rstrip('.,!?;:)]}') for v in re.findall(r'https?://[^\s<>"\\]+',text)}
    require(len(visible|set(links))<=5,'media_limit_exceeded: links')
    params={'link_attachment':links[0]} if links else {}
    quotes=[a['uri'] for a in manifest['attachments'] if a['type']=='quote']
    if quotes:params['quote_post_id']=quotes[0]
    polls=[a for a in manifest['attachments'] if a['type']=='poll']
    if polls:params['poll_attachment']=json.dumps(dict(zip(('option_a','option_b','option_c','option_d'),polls[0]['options'])),ensure_ascii=False,separators=(',',':'))
    gifs=[a for a in manifest['attachments'] if a['type']=='gif']
    if gifs:params['gif_attachment']=json.dumps({'gif_id':gifs[0]['id'],'provider':gifs[0]['provider']},ensure_ascii=False,separators=(',',':'))
    return params


def video_notes(items):
    notes=[]
    for item in items:
        row=item.manifest
        if row['kind']!='video':continue
        facts=mediaformats.threads_video_info(item._public_fd,row['public_size'])
        if facts['edit_lists']:notes.append('warning: threads video has edit lists; provider may refuse')
        if not facts['moov_at_front']:notes.append('warning: threads video moov follows mdat; provider may refuse')
        if any(c not in ('avc1','avc3','hvc1','hev1') for c in facts['video_codecs']):notes.append('warning: threads video_codec_declared_not_recommended; provider may refuse')
        if False in facts['progressive']:notes.append('warning: threads interlaced_video_declared; provider may refuse')
        for declared in facts['bitrates']:
            notes.append(f"warning: video_bitrate_declared: max={declared['max']}, avg={declared['avg']}; not certified")
            if any(v>100_000_000 for v in declared.values()):notes.append('warning: video_bitrate_declared_above_100mbps; provider may refuse')
        for audio in facts['audio']:
            if audio['codec']!='mp4a':notes.append('warning: threads audio_codec_declared_not_recommended; provider may refuse')
            if audio['channels'] is not None and audio['channels'] not in (1,2):notes.append('warning: threads audio_channels_declared_above_2; provider may refuse')
            if audio['sample_rate'] is not None and audio['sample_rate']>48000:notes.append('warning: threads audio_sample_rate_declared_above_48k; provider may refuse')
            declared=audio['bitrate_declared']
            if declared is not None:
                notes.append(f"warning: audio_bitrate_declared: max={declared['max']}, avg={declared['avg']}; not certified")
                if any(v>128_000 for v in declared.values()):notes.append('warning: audio_bitrate_declared_above_128k; provider may refuse')
        try:width,height,rate=mediaformats.video_metrics(item._public_fd,row['public_size'])
        except mediaformats.FormatError:notes.append('warning: threads video frame rate unobserved; provider may refuse')
        else:
            notes.append(f'warning: frame_rate_observed: {rate}; not bitstream-certified')
            if not 23<=rate<=60:notes.append('warning: frame_rate_outside_23_60; provider may refuse')
            require(width<=1920,'media_limit_exceeded: width')
            require(width*100>=height and width<=10*height,'media_limit_exceeded: aspect_ratio')
        notes.append('warning: threads video bitstream codec/GOP/chroma/VBR and audio AAC/bitrate are not certified; provider may refuse')
    return notes


def notes(manifest,items=()):
    return (['warning: threads poll option characters use provisional Unicode code points; live-provider counting unverified'] if any(a['type']=='poll' for a in manifest['attachments']) else [])+['warning: threads provider scales image width below 320 or above 1440; ICC retained, provider converts color space' for row in manifest['files'] if row['kind']=='image' and (row['height'] if row['orientation'] in (5,6,7,8) else row['width']) not in range(320,1441)]+video_notes(items)


class NoRedirect(httpsafe.SameOriginRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise httpsafe.RedirectBlocked(req.full_url,code,'media_redirect_refused',headers,fp)


_transport=httpsafe.build_opener(NoRedirect())


def _json(adapter,method,path,params,*,timeout=None):
    values={**params,'access_token':adapter.access_token}
    data=urllib.parse.urlencode(values).encode()
    url=adapter.base_url+path
    if method=='GET':url+='?'+data.decode();data=None
    request=urllib.request.Request(url,data=data,method=method)
    with leave_gate.urlopen(_transport.open,request,timeout=adapter.timeout if timeout is None else min(adapter.timeout,timeout)) as response:
        code=response.status;raw=response.read(1024*1024+1)
    require(code==200 and len(raw)<=1024*1024,'media_response_invalid')
    result=json.loads(raw);require(type(result) is dict and not result.get('error'),'media_response_invalid')
    return result


def identifier(value):
    value=value.get('id')
    require(type(value) is str and value.isascii() and value.isdecimal(),'media_response_invalid: id')
    return value


def publish(adapter,post,*,before_publish=None,on_container_created=None):
    phase='preflight';ids=[];grants=[];relay=post.media_relay;progress=post.media_progress;ts=jst.iso()
    def record(value,**fields):
        nonlocal phase
        phase=value;progress(value,remote_ids=list(ids),**fields)
    def veto():
        for item in post.media_files:item.verify()
        if before_publish:
            reason=before_publish();require(not reason,str(reason))
        require(all(time.time()*1000<g['expires_at'] for g in grants),'media_provider_url_expired')
    def wait(container,*,video=False):
        deadline=time.monotonic()+(VIDEO_POLL_SECONDS if video else POLL_SECONDS)
        while True:
            veto();remaining=min(deadline-time.monotonic(),min((g['expires_at']/1000-time.time() for g in grants),default=float('inf')))
            require(remaining>0,'media_processing_timeout')
            value=_json(adapter,'GET','/'+container,{'fields':'status,error_message'},timeout=remaining)
            require(time.monotonic()<deadline,'media_processing_timeout');veto()
            status=value.get('status')
            if status=='FINISHED':return
            require(status=='IN_PROGRESS','media_container_'+(status.lower() if status in ('ERROR','EXPIRED','PUBLISHED') else 'invalid'))
            time.sleep(min(POLL_INTERVAL,max(0,deadline-time.monotonic())))
    try:
        require(callable(progress) and (relay is not None or not post.media_files),'media_journal_required')
        reason=intent_error(post.media_manifest);require(reason is None,reason or '')
        require(len(post.media_files)==len(post.media_manifest['files']) and all(item.manifest==row for item,row in zip(post.media_files,post.media_manifest['files'])),'media_prepared_mismatch')
        video_notes(post.media_files)  # Same measured facts as lint, before any upload.
        typed=text_params(post.media_manifest,post.text) if not post.media_files else None
        require(type(adapter.user_id) is str and adapter.user_id.isascii() and adapter.user_id.isdecimal(),'media_account_id_invalid')
        common={'text':post.text}
        quotes=[a['uri'] for a in post.media_manifest['attachments'] if a['type']=='quote']
        if quotes:common['quote_post_id']=quotes[0]
        if post.reply_to:common['reply_to_id']=post.reply_to
        if post.topic:common['topic_tag']=post.topic
        if post.location_id:common['location_id']=post.location_id
        if post.share_to_instagram:common['crossreshare_to_ig']='true'
        for index,item in enumerate(post.media_files):
            veto();record('uploading',index=index)
            source=relay.upload_prepared(item)
            veto();record('granting',index=index)
            grant=relay.grant(item,source);grants.append(grant)
            veto();record('creating',index=index)
            is_video=item.manifest['kind']=='video'
            params={'media_type':'VIDEO' if is_video else 'IMAGE','video_url' if is_video else 'image_url':grant['url'],'alt_text':item.manifest['alt']}
            if len(post.media_files)>1:params['is_carousel_item']='true'
            else:params.update(common)
            container=identifier(_json(adapter,'POST','/'+adapter.user_id+'/threads',params))
            ids.append(container);record('processing',index=index);wait(container,video=is_video);record('ready',index=index)
        if typed is not None:
            veto();record('creating')
            container=identifier(_json(adapter,'POST','/'+adapter.user_id+'/threads',{'media_type':'TEXT',**common,**typed}))
            ids.append(container);record('processing');wait(container);record('ready')
        else:container=ids[0]
        if len(ids)>1:
            veto();record('creating_carousel')
            container=identifier(_json(adapter,'POST','/'+adapter.user_id+'/threads',{'media_type':'CAROUSEL','children':','.join(ids),**common}))
            record('processing_carousel',container_id=container);wait(container,video=any(i.manifest['kind']=='video' for i in post.media_files));record('ready',container_id=container)
        if on_container_created:on_container_created(container)
        veto();record('publishing',container_id=container)
        result=identifier(_json(adapter,'POST','/'+adapter.user_id+'/threads_publish',{'creation_id':container}))
        record('published',post_id=result,publication_ack='pending')
        # Known publication remains known when only its capability acknowledgment
        # fails. No automatic retry/renewal; the original provisional TTL remains.
        acknowledged=True
        for grant in grants:
            try:require(relay.result(grant,published=True).get('status')=='acknowledged','media_ack_unconfirmed')
            except (OSError,ValueError,approval_relay.RelayError,accounts.AccountStopped):acknowledged=False
        # Provider ID and pending acknowledgement are already durable. A failure
        # to enrich that record must not erase publication or retry either API.
        try:record('published',post_id=result,publication_ack='acknowledged' if acknowledged else 'unconfirmed')
        except (OSError,ValueError,RuntimeError,accounts.AccountStopped):pass
        return base.PublishResult(result,None,ts,media=[{'sha256':item.manifest['public_sha256'],'kind':item.manifest['kind'],'alt_present':True,'remote_id':remote} for item,remote in zip(post.media_files,ids)])
    except accounts.AccountStopped:
        return base.PublishResult(None,None,ts,error='account_stopped',failure='media_held' if ids and phase not in ('publishing','published') else 'media_ambiguous' if phase!='preflight' else 'publish_vetoed')
    except (OSError,ValueError,RuntimeError,urllib.error.URLError,approval_relay.RelayError) as exc:
        http=isinstance(exc,urllib.error.HTTPError)
        endpoint=isinstance(exc,httpsafe.EndpointRejected)
        definite=endpoint or http and 400<=exc.code<500
        held=bool(ids or grants) and (phase in ('ready','processing','processing_carousel') or definite)
        uncertain=phase!='preflight' and not held and not definite
        reason='media_relay_endpoint_rejected' if endpoint else str(exc) if isinstance(exc,media.MediaError) else 'media_relay_failed' if isinstance(exc,(media_relay.MediaRelayError,approval_relay.RelayError)) else 'media_'+phase+('_http_'+str(exc.code) if http else '_failed')
        # Explicit refusal/veto: retire grants once, never retry an unknown POST.
        if held:
            for grant in grants:
                try:relay.result(grant,published=False)
                except (OSError,ValueError,approval_relay.RelayError,accounts.AccountStopped):pass
        try:
            if callable(progress):record('held' if held else 'unknown' if uncertain else 'failed',reason=reason)
        except (OSError,ValueError,accounts.AccountStopped):pass
        return base.PublishResult(None,None,ts,error=reason,failure='media_held' if held else 'media_ambiguous' if uncertain else 'publish_definite')
