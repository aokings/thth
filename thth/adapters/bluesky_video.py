"""Bluesky video service processing; input snapshots and output blobs differ.

Fixed Lexicons 7870a59c: MP4 300 MB, captions20 x 20 KB. Official app limit
600s. Video service may optimize bytes; output CID is not the input SHA.
Service tokens stay in RAM, and uncertain POSTs are never repeated here.
"""
from __future__ import annotations
import base64
import errno
import json
import math
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from . import base,bluesky_media as images
from .. import accounts,httpsafe,jst,leave_gate,media,redact

SERVICE='https://video.bsky.app'
SERVICE_DID='did:web:video.bsky.app'
TOKEN_SECONDS=1800
POLL_SECONDS=1800.0
POLL_INTERVAL=2.0
MAX_BYTES=300_000_000


def require(value,reason):
    if not value:raise media.MediaError(reason)


def intent_error(manifest):
    if manifest['attachments']:return 'unsupported_attachment: bluesky/typed_attachment_pending'
    if set(manifest['post_options'])-{'presentation'}:return 'unsupported_attachment: bluesky/video_post_options'
    rows=[r for r in manifest['files'] if r['role']=='media']
    if len(rows)!=1:return 'media_limit_exceeded: video_count'
    row=rows[0]
    if row['kind']!='video' or row['format']!='mp4':return 'unsupported_attachment: bluesky/'+str(row['format'])
    if type(row['public_size']) is not int or not 0<row['public_size']<=MAX_BYTES:return 'media_limit_exceeded: video_bytes'
    duration=row.get('duration')
    if type(duration) not in (int,float) or not math.isfinite(duration):return 'media_duration_unavailable'
    if not 0<duration<=600:return 'media_limit_exceeded: video_duration'
    for key in ('width','height'):
        v=row.get(key)
        if type(v) not in (int,float) or not math.isfinite(v) or v<=0 or v!=int(v):return 'media_dimensions_unavailable'
    captions=[r for r in manifest['files'] if r['role']=='caption']
    if len(captions)!=len(manifest['captions']) or len(captions)+1!=len(manifest['files']):return 'media_prepared_mismatch'
    if len(captions)>20:return 'media_limit_exceeded: caption_count'
    for declaration,caption in zip(manifest['captions'],captions):
        if declaration['media_index']!=1 or caption['format']!='vtt':return 'unsupported_attachment: bluesky/caption'
        if type(caption['public_size']) is not int or not 0<caption['public_size']<=20000:return 'media_limit_exceeded: caption_bytes'
    return None


def service_url():
    override=os.environ.get('THTH_TEST_BLUESKY_VIDEO_URL')
    if override is None:return SERVICE
    require(os.environ.get('THTH_TEST_ALLOW_HTTP')=='1','video_service_override_forbidden')
    value=httpsafe.validated_url(override,base=True);url=urllib.parse.urlsplit(override)
    require(url.hostname in ('localhost','127.0.0.1','::1') and url.path in ('','/'),'video_service_override_forbidden')
    return value


def pds_audience(session):
    did=session.get('did');doc=session.get('didDoc')
    require(type(did) is str and re.fullmatch(r'did:[a-z]+:[A-Za-z0-9._:%-]+',did) is not None,'video_identity_unavailable')
    require(type(doc) is dict and doc.get('id')==did and type(doc.get('service')) is list,'video_pds_unavailable')
    # ATProto DID spec: use the first matching service, including relative IDs.
    selected=next((s for s in doc['service'] if type(s) is dict and s.get('id') in ('#atproto_pds',did+'#atproto_pds') and s.get('type')=='AtprotoPersonalDataServer'),None)
    require(selected is not None,'video_pds_unavailable')
    try:
        endpoint=httpsafe.validated_url(selected.get('serviceEndpoint'),base=True)
        url=urllib.parse.urlsplit(selected['serviceEndpoint'])
    except (ValueError,TypeError):raise media.MediaError('video_pds_unavailable') from None
    require(url.scheme=='https' and url.path in ('','/') and type(url.hostname) is str and re.fullmatch(r'[A-Za-z0-9.-]+',url.hostname) is not None,'video_pds_unavailable')
    # Official video guide derives service DID from hostname, not entryway/port.
    return 'did:web:'+url.hostname.lower()


def _pairs(rows):
    result={}
    for key,value in rows:
        require(key not in result,'video_response_invalid');result[key]=value
    return result


def _request(url,*,method='GET',token=None,data=None,mime=None,length=None,timeout=30,already_exists=False):
    headers={}
    if token:headers['Authorization']='Bearer '+token
    if mime:headers['Content-Type']=mime
    if length is not None:headers['Content-Length']=str(length)
    req=urllib.request.Request(url,method=method,data=data,headers=headers)
    try:
        with leave_gate.urlopen(images._transport.open,req,timeout=timeout) as response:
            status=response.status;raw=response.read(1024*1024+1)
    except urllib.error.HTTPError as exc:
        if not already_exists or not 400<=exc.code<500:raise
        try:raw=exc.read(1024*1024+1);status=exc.code
        finally:exc.close()
        try:value=json.loads(raw,object_pairs_hook=_pairs)
        except (ValueError,UnicodeError):raise exc
        if type(value) is not dict or value.get('error')!='already_exists':raise exc
    require(len(raw)<=1024*1024,'video_response_invalid')
    try:value=json.loads(raw,object_pairs_hook=_pairs)
    except (ValueError,UnicodeError):raise media.MediaError('video_response_invalid') from None
    require(type(value) is dict and (status==200 or already_exists and value.get('error')=='already_exists'),'video_response_invalid')
    return value


def _service_token(adapter,aud,lxm):
    expires=int(time.time())+TOKEN_SECONDS
    query=urllib.parse.urlencode({'aud':aud,'lxm':lxm,'exp':expires})
    response=_request(adapter.service+'/xrpc/com.atproto.server.getServiceAuth?'+query,token=adapter.session()['accessJwt'],timeout=adapter.timeout)
    token=response.get('token')
    if type(token) is str:redact.register_secret(token)
    require(type(token) is str and 0<len(token)<=65536 and all(32<ord(c)<127 for c in token) and not response.get('error'),'video_service_auth_invalid')
    return token,expires


def _limits(adapter,url,size):
    token,_=_service_token(adapter,SERVICE_DID,'app.bsky.video.getUploadLimits')
    response=_request(url+'/xrpc/app.bsky.video.getUploadLimits',token=token,timeout=adapter.timeout)
    require(type(response.get('canUpload')) is bool and not response.get('error'),'video_limits_unavailable')
    require(response['canUpload'],'video_upload_not_allowed')
    for key,needed in (('remainingDailyVideos',1),('remainingDailyBytes',size)):
        if key not in response:continue
        amount=response[key]
        require(type(amount) is int and amount>=0,'video_limits_unavailable')
        require(amount>=needed,'video_upload_limit_exceeded')


def _job(response,did,job_id=None):
    if 'jobStatus' in response:
        require(set(response)=={'jobStatus'},'video_job_invalid')
        response=response['jobStatus']
    require(type(response) is dict,'video_job_invalid')
    identifier=response.get('jobId');state=response.get('state')
    require(type(identifier) is str and re.fullmatch(r'[A-Za-z0-9._-]{1,256}',identifier) is not None and redact.redact(identifier)==identifier,'video_job_invalid')
    require(response.get('did')==did and (job_id is None or identifier==job_id),'video_job_identity_mismatch')
    require(type(state) is str and 0<len(state)<=256,'video_job_invalid')
    if 'progress' in response:require(type(response['progress']) is int and 0<=response['progress']<=100,'video_job_invalid')
    return response


def checked_video_blob(blob):
    require(type(blob) is dict and set(blob)=={'$type','ref','mimeType','size'},'video_blob_invalid')
    require(blob['$type']=='blob' and type(blob['ref']) is dict and set(blob['ref'])=={'$link'},'video_blob_invalid')
    cid=blob['ref']['$link']
    require(type(cid) is str and re.fullmatch(r'b[a-z2-7]{58}',cid) is not None,'video_blob_invalid')
    try:raw=base64.b32decode(cid[1:].upper()+'='*((-len(cid[1:]))%8))
    except ValueError:raise media.MediaError('video_blob_invalid') from None
    require(len(raw)==36 and raw[:4]==b'\x01\x55\x12\x20' and images.blob_cid(raw[4:].hex())==cid,'video_blob_invalid')
    require(type(blob['size']) is int and 0<blob['size']<=MAX_BYTES and blob['mimeType']=='video/mp4','video_blob_invalid')
    return blob


def _caption_blob(response,row):
    blob=response.get('blob')
    require(type(blob) is dict and set(blob)=={'$type','ref','mimeType','size'} and blob['$type']=='blob','video_caption_blob_invalid')
    require(blob['ref']=={'$link':images.blob_cid(row['public_sha256'])} and blob['mimeType']=='text/vtt' and type(blob['size']) is int and blob['size']==row['public_size'],'video_caption_blob_invalid')
    return blob


def publish(adapter,post,*,before_publish=None):
    from .bluesky import POST_COLLECTION,post_url
    ts=jst.iso();phase='preflight';job_id=None;did=None;ids=[];progress=post.media_progress
    def record(value,**details):
        nonlocal phase
        phase=value;progress(value,remote_ids=list(ids),**({'video_job_id':job_id,'video_did':did} if job_id else {}),**details)
    def veto():
        for item in post.media_files:item.verify()
        if before_publish:
            reason=before_publish();require(not reason,str(reason))
    try:
        require(callable(progress),'media_journal_required')
        why=intent_error(post.media_manifest);require(why is None,why or '')
        require(len(post.media_files)==len(post.media_manifest['files']) and all(item.manifest==row for item,row in zip(post.media_files,post.media_manifest['files'])),'media_prepared_mismatch')
        item=post.media_files[0];row=item.manifest;veto()
        session=adapter.session();did=session['did'];aud=pds_audience(session);url=service_url()
        body=adapter._post_record(post)
        if post.reply_to:body['reply']=adapter._reply_ref(post.reply_to)
        _limits(adapter,url,row['public_size']);veto()
        token,expires=_service_token(adapter,aud,'com.atproto.repo.uploadBlob')
        deadline=time.monotonic()+min(POLL_SECONDS,max(0,expires-time.time()))
        def remaining():
            veto();left=deadline-time.monotonic();require(left>0,'video_processing_timeout');return min(adapter.timeout,left)
        query=urllib.parse.urlencode({'did':did,'name':secrets.token_hex(16)+'.mp4'})
        record('uploading',operation='video_upload')
        response=_request(url+'/xrpc/app.bsky.video.uploadVideo?'+query,method='POST',token=token,data=item.chunks(),mime='video/mp4',length=row['public_size'],timeout=remaining(),already_exists=True)
        job=_job(response,did);job_id=job['jobId'];record('processing')
        while True:
            remaining()
            if job.get('error')=='already_exists':blob=checked_video_blob(job.get('blob'));break
            require(not job.get('error') and job['state']!='JOB_STATE_FAILED','video_processing_failed')
            if job['state']=='JOB_STATE_COMPLETED':blob=checked_video_blob(job.get('blob'));break
            time.sleep(min(POLL_INTERVAL,remaining()))
            timeout=remaining()
            response=_request(url+'/xrpc/app.bsky.video.getJobStatus?'+urllib.parse.urlencode({'jobId':job_id}),timeout=timeout,already_exists=True)
            remaining();job=_job(response,did,job_id)
        ids.append(blob['ref']['$link']);record('ready')
        embed={'$type':'app.bsky.embed.video','video':blob,'alt':row['alt'],'aspectRatio':{'width':int(row['width']),'height':int(row['height'])}}
        if 'presentation' in post.media_manifest['post_options']:embed['presentation']=post.media_manifest['post_options']['presentation']
        captions=[]
        for caption,declaration in zip(post.media_files[1:],post.media_manifest['captions']):
            veto();record('uploading',operation='caption_upload',index=caption.manifest['index'])
            response=images._json(adapter,'com.atproto.repo.uploadBlob',data=caption.chunks(),content_type='text/vtt',length=caption.manifest['public_size'])
            caption_blob=_caption_blob(response,caption.manifest);ids.append(caption_blob['ref']['$link']);record('ready')
            captions.append({'lang':declaration['lang'],'file':caption_blob})
        if captions:embed['captions']=captions
        body['embed']=embed;veto()
        payload=json.dumps({'repo':did,'collection':POST_COLLECTION,'record':body},ensure_ascii=False).encode()
        record('publishing')
        response=images._json(adapter,'com.atproto.repo.createRecord',data=payload,content_type='application/json',length=len(payload))
        uri=response.get('uri');prefix='at://'+did+'/'+POST_COLLECTION+'/'
        require(type(uri) is str and uri.startswith(prefix) and bool(uri[len(prefix):]) and '/' not in uri[len(prefix):] and all(32<ord(c)<127 for c in uri),'media_response_invalid: post URI')
        record('published',post_id=uri)
        return base.PublishResult(uri,post_url(session.get('handle'),uri),ts,media=[{'sha256':row['public_sha256'],'kind':'video','alt_present':True,'remote_id':blob['ref']['$link']}])
    except (OSError,ValueError,RuntimeError,urllib.error.URLError,accounts.AccountStopped) as exc:
        http=isinstance(exc,urllib.error.HTTPError);redirect=isinstance(exc,httpsafe.RedirectBlocked)
        endpoint=isinstance(exc,httpsafe.EndpointRejected)
        refused=isinstance(exc,urllib.error.URLError) and isinstance(exc.reason,OSError) and exc.reason.errno==errno.ECONNREFUSED
        stopped=isinstance(exc,accounts.AccountStopped)
        reason='account_stopped' if stopped else 'media_redirect_refused' if redirect else 'media_endpoint_rejected' if endpoint else 'media_connection_refused' if refused else ('media_'+phase+'_http_'+str(exc.code)) if http else str(exc) if isinstance(exc,media.MediaError) else 'video_'+phase+'_failed'
        journal_failure=reason=='media_journal_unavailable'
        definite=phase=='preflight' or (phase=='uploading' and not job_id and (endpoint or refused or redirect or http and 400<=exc.code<500))
        held=bool(job_id) and not journal_failure and (phase in ('processing','ready') or phase in ('uploading','publishing') and http and 400<=exc.code<500)
        uncertain=not definite and not held
        try:
            if callable(progress):record('held' if held else 'unknown' if uncertain else 'failed',reason=reason)
        except (OSError,ValueError,accounts.AccountStopped):pass
        return base.PublishResult(None,None,ts,error=reason,failure='media_held' if held else 'media_ambiguous' if uncertain else 'publish_definite')
