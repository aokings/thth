"""Bluesky image blobs, prepared snapshots only; uncertain POSTs never retry.

ATProto blob spec: CIDv1/raw/SHA-256/base32. Fixed Lexicons 7870a59c:
images max4, gallery max20 (UI soft10), each image max2,000,000 bytes.
"""
from __future__ import annotations
import base64
import errno
import json
import urllib.error
import urllib.request
from . import base
from .. import accounts, httpsafe, jst, media

MIME={'jpeg':'image/jpeg','png':'image/png','webp':'image/webp','gif':'image/gif'}
MAX_BYTES=2_000_000


def require(ok,reason):
    if not ok:raise media.MediaError(reason)


def intent_error(manifest):
    if manifest['attachments']:return 'unsupported_attachment: bluesky/typed_attachment_pending'
    if manifest['captions']:return 'unsupported_attachment: bluesky/image_captions'
    if set(manifest['post_options'])-{'gallery'}:return 'unsupported_attachment: bluesky/image_post_options'
    rows=manifest['files']
    if not rows:return 'unsupported_attachment: bluesky/no_images'
    for row in rows:
        if row['role']!='media' or row['kind']!='image' or row['format'] not in MIME:
            return 'unsupported_attachment: bluesky/'+str(row['format'])
        if any(type(row.get(key)) is not int or row[key]<=0 for key in ('width','height')):
            return 'media_dimensions_unavailable'
    if len(rows)>(20 if manifest['post_options'].get('gallery') else 4):return 'media_limit_exceeded: count'
    if any(row['public_size']>MAX_BYTES for row in rows):return 'media_limit_exceeded: bytes'
    return None


def blob_cid(public_sha256):
    return 'b'+base64.b32encode(b'\x01\x55\x12\x20'+bytes.fromhex(public_sha256)).decode('ascii').lower().rstrip('=')


def checked_blob(value,row):
    blob=value.get('blob')
    require(type(blob) is dict and set(blob)=={'$type','ref','mimeType','size'},'media_response_invalid: blob')
    require(blob['$type']=='blob' and type(blob['ref']) is dict and set(blob['ref'])=={'$link'},'media_response_invalid: blob ref')
    require(blob['ref']['$link']==blob_cid(row['public_sha256']),'media_response_invalid: CID')
    require(type(blob['size']) is int and blob['size']==row['public_size'],'media_response_invalid: size')
    require(blob['mimeType']==MIME[row['format']],'media_response_invalid: MIME')
    return blob


class _NoRedirect(httpsafe.SameOriginRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise httpsafe.RedirectBlocked(req.full_url,code,'media_redirect_refused',headers,fp)


_transport=httpsafe.build_opener(_NoRedirect())


def _json(adapter,nsid,*,data,content_type,length):
    from .. import leave_gate
    req=urllib.request.Request(adapter.service+'/xrpc/'+nsid,data=data,method='POST',headers={
        'Authorization':'Bearer '+adapter.session()['accessJwt'],'Content-Type':content_type,'Content-Length':str(length)})
    with leave_gate.urlopen(_transport.open,req,timeout=adapter.timeout) as response:
        code=response.status;raw=response.read(1024*1024+1)
    require(code==200 and len(raw)<=1024*1024,'media_response_invalid: HTTP')
    value=json.loads(raw)
    require(type(value) is dict and not value.get('error'),'media_response_invalid: object')
    return value


def publish(adapter,post,*,before_publish=None):
    from .bluesky import POST_COLLECTION,post_url
    ts=jst.iso();phase='preflight';ids=[];progress=post.media_progress
    def record(value,**details):
        nonlocal phase
        phase=value;progress(value,remote_ids=list(ids),**details)
    def veto():
        for item in post.media_files:item.verify()
        if before_publish:
            reason=before_publish();require(not reason,str(reason))
    try:
        require(callable(progress),'media_journal_required')
        why=intent_error(post.media_manifest);require(why is None,why or '')
        require(len(post.media_files)==len(post.media_manifest['files']) and all(item.manifest==row for item,row in zip(post.media_files,post.media_manifest['files'])),'media_prepared_mismatch')
        record_body=adapter._post_record(post)
        session=adapter.session()
        if post.reply_to:record_body['reply']=adapter._reply_ref(post.reply_to)
        entries=[];gallery=post.media_manifest['post_options'].get('gallery',False)
        for index,item in enumerate(post.media_files):
            veto();row=item.manifest
            width,height=row['width'],row['height']
            if row['orientation'] in (5,6,7,8):width,height=height,width
            require(type(width) is int and type(height) is int and width>0 and height>0,'media_dimensions_unavailable')
            record('uploading',index=index)
            value=_json(adapter,'com.atproto.repo.uploadBlob',data=item.chunks(),content_type=MIME[row['format']],length=row['public_size'])
            blob=checked_blob(value,row);ids.append(blob['ref']['$link']);record('ready',index=index)
            entry={'image':blob,'alt':row['alt'],'aspectRatio':{'width':width,'height':height}}
            if gallery:entry['$type']='app.bsky.embed.gallery#image'
            entries.append(entry)
        record_body['embed']={'$type':'app.bsky.embed.gallery','items':entries} if gallery else {'$type':'app.bsky.embed.images','images':entries}
        veto()
        payload=json.dumps({'repo':session['did'],'collection':POST_COLLECTION,'record':record_body},ensure_ascii=False).encode('utf-8')
        record('publishing')
        value=_json(adapter,'com.atproto.repo.createRecord',data=payload,content_type='application/json',length=len(payload))
        uri=value.get('uri');prefix='at://'+session['did']+'/'+POST_COLLECTION+'/'
        require(type(uri) is str and uri.startswith(prefix) and bool(uri[len(prefix):]) and '/' not in uri[len(prefix):] and all(32<ord(c)<127 for c in uri),'media_response_invalid: post URI')
        record('published',post_id=uri)
        return base.PublishResult(uri,post_url(session.get('handle'),uri),ts,media=[{'sha256':item.manifest['public_sha256'],'kind':'image','alt_present':True,'remote_id':identifier} for item,identifier in zip(post.media_files,ids)])
    except accounts.AccountStopped:
        return base.PublishResult(None,None,ts,error='account_stopped',failure='media_held' if ids and phase=='ready' else 'media_ambiguous' if phase!='preflight' else 'publish_vetoed')
    except (OSError,ValueError,RuntimeError,urllib.error.URLError) as exc:
        http=isinstance(exc,urllib.error.HTTPError)
        redirect=isinstance(exc,httpsafe.RedirectBlocked)
        endpoint=isinstance(exc,httpsafe.EndpointRejected)
        preconnect=endpoint or isinstance(exc,urllib.error.URLError) and isinstance(exc.reason,OSError) and exc.reason.errno==errno.ECONNREFUSED
        reason='media_redirect_refused' if redirect else 'media_endpoint_rejected' if endpoint else 'media_connection_refused' if preconnect else ('media_'+phase+'_http_'+str(exc.code)) if http else str(exc) if isinstance(exc,media.MediaError) else 'media_'+phase+'_failed'
        definite=(redirect or preconnect or http and 400<=exc.code<500) and phase=='uploading' and not ids
        held=bool(ids) and (phase=='ready' or (http and 400<=exc.code<500 and phase in ('uploading','publishing')))
        uncertain=phase!='preflight' and not definite and not held
        try:
            if callable(progress):record('held' if held else 'unknown' if uncertain else 'failed',reason=reason)
        except (OSError,ValueError,accounts.AccountStopped):pass
        return base.PublishResult(None,None,ts,error=reason,failure='media_held' if held else 'media_ambiguous' if uncertain else 'publish_definite')
