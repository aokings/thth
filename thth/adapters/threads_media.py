"""Threads image containers from immutable prepared bytes and private R2 grants.

C11: JPEG/PNG, provisional SI 8 MB, ratio <=10 either way. Numeric byte
boundary is an adjudicated temporary contract, not a verified API integer cap.
"""
from __future__ import annotations
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from . import base
from .. import accounts,approval_relay,httpsafe,jst,leave_gate,media,media_relay

MAX_BYTES=8_000_000
POLL_SECONDS=120.0
POLL_INTERVAL=2.0


def require(ok,reason):
    if not ok:raise media.MediaError(reason)


def intent_error(manifest):
    if manifest['attachments'] or manifest['captions']:return 'unsupported_attachment: threads/typed_attachment'
    if manifest['post_options']:return 'unsupported_attachment: threads/image_post_options'
    rows=manifest['files']
    if not 1<=len(rows)<=20:return 'media_limit_exceeded: count'
    for row in rows:
        if row['role']!='media' or row['kind']!='image' or row['format'] not in ('jpeg','png'):
            return 'unsupported_attachment: threads/'+str(row['format'])
        if type(row['public_size']) is not int or row['public_size']<1:return 'media_size_unavailable'
        if row['public_size']>MAX_BYTES:return 'media_limit_exceeded: bytes (provisional SI 8000000)'
        w,h=row.get('width'),row.get('height')
        if type(w) is not int or type(h) is not int or w<=0 or h<=0:return 'media_dimensions_unavailable'
        if max(w,h)>10*min(w,h):return 'media_limit_exceeded: aspect_ratio'
    return None


def notes(manifest):
    return ['warning: threads provider scales image width below 320 or above 1440; ICC retained, provider converts color space' for row in manifest['files'] if (row['height'] if row['orientation'] in (5,6,7,8) else row['width']) not in range(320,1441)]


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
    def wait(container):
        deadline=time.monotonic()+POLL_SECONDS
        while True:
            veto();remaining=min(deadline-time.monotonic(),min(g['expires_at']/1000-time.time() for g in grants))
            require(remaining>0,'media_processing_timeout')
            value=_json(adapter,'GET','/'+container,{'fields':'status,error_message'},timeout=remaining)
            require(time.monotonic()<deadline,'media_processing_timeout');veto()
            status=value.get('status')
            if status=='FINISHED':return
            require(status=='IN_PROGRESS','media_container_'+(status.lower() if status in ('ERROR','EXPIRED','PUBLISHED') else 'invalid'))
            time.sleep(min(POLL_INTERVAL,max(0,deadline-time.monotonic())))
    try:
        require(callable(progress) and relay is not None,'media_journal_required')
        reason=intent_error(post.media_manifest);require(reason is None,reason or '')
        require(len(post.media_files)==len(post.media_manifest['files']) and all(item.manifest==row for item,row in zip(post.media_files,post.media_manifest['files'])),'media_prepared_mismatch')
        require(type(adapter.user_id) is str and adapter.user_id.isascii() and adapter.user_id.isdecimal(),'media_account_id_invalid')
        common={'text':post.text}
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
            params={'media_type':'IMAGE','image_url':grant['url'],'alt_text':item.manifest['alt']}
            if len(post.media_files)>1:params['is_carousel_item']='true'
            else:params.update(common)
            container=identifier(_json(adapter,'POST','/'+adapter.user_id+'/threads',params))
            ids.append(container);record('processing',index=index);wait(container);record('ready',index=index)
        container=ids[0]
        if len(ids)>1:
            veto();record('creating_carousel')
            container=identifier(_json(adapter,'POST','/'+adapter.user_id+'/threads',{'media_type':'CAROUSEL','children':','.join(ids),**common}))
            record('processing_carousel',container_id=container);wait(container);record('ready',container_id=container)
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
        return base.PublishResult(result,None,ts,media=[{'sha256':item.manifest['public_sha256'],'kind':'image','alt_present':True,'remote_id':remote} for item,remote in zip(post.media_files,ids)])
    except accounts.AccountStopped:
        return base.PublishResult(None,None,ts,error='account_stopped',failure='media_held' if ids and phase not in ('publishing','published') else 'media_ambiguous' if phase!='preflight' else 'publish_vetoed')
    except (OSError,ValueError,RuntimeError,urllib.error.URLError,approval_relay.RelayError) as exc:
        http=isinstance(exc,urllib.error.HTTPError)
        definite=http and 400<=exc.code<500
        held=bool(grants) and (phase in ('ready','processing','processing_carousel') or definite)
        uncertain=phase!='preflight' and not held
        reason=str(exc) if isinstance(exc,media.MediaError) else 'media_relay_failed' if isinstance(exc,(media_relay.MediaRelayError,approval_relay.RelayError)) else 'media_'+phase+('_http_'+str(exc.code) if http else '_failed')
        # Explicit refusal/veto: retire grants once, never retry an unknown POST.
        if held:
            for grant in grants:
                try:relay.result(grant,published=False)
                except (OSError,ValueError,approval_relay.RelayError,accounts.AccountStopped):pass
        try:
            if callable(progress):record('held' if held else 'unknown' if uncertain else 'failed',reason=reason)
        except (OSError,ValueError,accounts.AccountStopped):pass
        return base.PublishResult(None,None,ts,error=reason,failure='media_held' if held else 'media_ambiguous' if uncertain else 'publish_definite')
