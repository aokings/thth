"""Mastodon media transport: status-aware, no automatic POST retry.

Official media/Instance/statuses methods, read 2026-09-21. Processing deadlines
are local waiting policy, not provider file/duration limits.
"""
from __future__ import annotations
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from . import base
from .. import accounts, httpsafe, jst, media, mediaformats

MIME = {'jpeg':'image/jpeg','png':'image/png','webp':'image/webp','gif':'image/gif','mp4':'video/mp4','mov':'video/quicktime'}
POLL_SECONDS = 120.0
POLL_INTERVAL = 2.0


def require(ok, code):
    if not ok: raise media.MediaError(code)


def intent_error(manifest):
    attachments=manifest['attachments']
    kinds={row['type'] for row in attachments}
    if kinds-{'poll','quote','link'}:
        return 'unsupported_attachment: mastodon/typed_attachment_pending'
    if 'poll' in kinds and manifest['files']:return 'unsupported_attachment: mastodon/poll_media_exclusive'
    quote=next((row for row in attachments if row['type']=='quote'),None)
    if quote is not None and (set(quote)!={'type','uri'} or not quote['uri'].isascii() or not quote['uri'].isdecimal()):
        return 'invalid_quote_target: mastodon/local_status_id_required'
    link=next((row for row in attachments if row['type']=='link'),None)
    if link is not None and set(link)!={'type','url'}:
        return 'unsupported_attachment: mastodon/custom_card_fields'
    if manifest['captions']: return 'unsupported_attachment: mastodon/captions'
    options=manifest['post_options']
    if 'poll' in kinds and 'focus' in options:return 'unsupported_attachment: mastodon/poll_focus'
    if not manifest['files'] and 'focus' in options:return 'unsupported_attachment: mastodon/fileless_focus'
    if set(options)-{'visibility','language','sensitive','spoiler_text','focus','quote_approval_policy'}:
        return 'unsupported_attachment: mastodon/post_options'
    if options.get('visibility','public') not in ('public','unlisted'):
        return 'unsupported_attachment: mastodon/non_public_visibility'
    if 'quote_approval_policy' in options and options['quote_approval_policy'] not in ('public','followers','nobody'):
        return 'invalid_quote_approval_policy: mastodon'
    if not manifest['files'] and not attachments and not options:return 'unsupported_attachment: mastodon/no_media'
    if any(x['role']!='media' or x['kind'] not in ('image','video') or x['format'] not in MIME for x in manifest['files']):
        return 'unsupported_attachment: mastodon/format'
    return None


def check_text_intent(manifest,text):
    kinds={row['type'] for row in manifest['attachments']}
    if not manifest['files']:
        reason='poll_text_required' if 'poll' in kinds else 'quote_text_required' if 'quote' in kinds or 'quote_approval_policy' in manifest['post_options'] else 'status_text_required'
        require(type(text) is str and bool(text.strip()),reason+': mastodon')
    link=next((row for row in manifest['attachments'] if row['type']=='link'),None)
    if link is not None:
        # Card generation belongs to Mastodon, from the approved plaintext URL.
        # Exact whitespace-delimited token avoids accepting a host/path prefix.
        require(type(text) is str and link['url'] in text.split(),'link_text_mismatch: mastodon')


def quote_capabilities(body):
    versions=body.get('api_versions') if type(body) is dict else None
    version=versions.get('mastodon') if type(versions) is dict else None
    require(type(version) is int and version>=0,'quote_capability_unavailable: mastodon/api_versions')
    require(version>=7,'unsupported_attachment: mastodon/quote_requires_api_7')
    return version


def quote_target(adapter,quote):
    # Only the local numeric status ID is accepted. No URL resolution/fetch.
    try:code,value=_json(adapter,'GET','/api/v1/statuses/'+quote['uri'])
    except urllib.error.HTTPError as exc:
        raise media.MediaError('quote_target_http_'+str(exc.code)+': mastodon') from exc
    require(code==200 and value.get('id')==quote['uri'],'quote_target_unavailable: mastodon')
    # A private quote can make Mastodon silently narrow our posted visibility.
    # Preserve the existing public-only boundary before any upload/status POST.
    require(value.get('visibility') in ('public','unlisted'),'quote_target_non_public: mastodon')


def observe_instance(cfg):
    from .mastodon import MastodonAdapter
    from .. import leave_gate
    adapter=MastodonAdapter(instance=cfg.get('instance',''))
    with leave_gate.scope(cfg):
        code,value=_json(adapter,'GET','/api/v2/instance')
        require(code==200,'media_capability_unavailable')
        return value,adapter.instance


def poll_capabilities(body,instance):
    require(type(body) is dict and type(body.get('configuration')) is dict,'poll_capability_unavailable')
    version=body.get('version');limits=body['configuration'].get('polls')
    require(type(version) is str and 0<len(version)<=128 and all(32<=ord(c)<127 for c in version),'poll_capability_unavailable: version')
    require(type(limits) is dict,'poll_capability_unavailable')
    out={'instance':instance,'version':version,'observed_at':jst.iso()}
    for key in ('max_options','max_characters_per_option','min_expiration','max_expiration'):
        value=limits.get(key)
        require(type(value) is int and value>0,'poll_capability_unavailable: '+key)
        out[key]=value
    require(out['max_options']>=2 and out['min_expiration']<=out['max_expiration'],'poll_capability_unavailable')
    return out


def check_poll(cap,poll,text):
    from .. import graphemes
    require(type(text) is str and bool(text.strip()),'poll_text_required: mastodon')
    # Upstream Poll.prepare_options uses Ruby String#strip (ASCII whitespace),
    # followed by its grapheme-cluster validator, not Python code-point length.
    values=[value.strip('\x00\t\n\v\f\r ') for value in poll['options']]
    require(all(values) and len(set(values))==len(values),'invalid_poll_options: mastodon')
    require(2<=len(values)<=cap['max_options'],'poll_limit_exceeded: options')
    require(all(graphemes.count(value,stop_after=cap['max_characters_per_option'])<=cap['max_characters_per_option'] for value in values),'poll_limit_exceeded: option_characters')
    require(cap['min_expiration']<=poll['expires_in']<=cap['max_expiration'],'poll_limit_exceeded: expires_in')


def observe_poll(cfg):
    """Poll-only checks require no media limits, token, or persistent cache."""
    from .mastodon import MastodonAdapter
    from .. import leave_gate
    adapter=MastodonAdapter(instance=cfg.get('instance',''))
    with leave_gate.scope(cfg):
        code,value=_json(adapter,'GET','/api/v2/instance')
        require(code==200,'poll_capability_unavailable')
        return poll_capabilities(value,adapter.instance)


def _positive(obj,key):
    value=obj.get(key)
    require(type(value) is int and value>0,'media_capability_unavailable: '+key)
    return value


def capabilities(body,instance):
    require(isinstance(body,dict) and isinstance(body.get('configuration'),dict),'media_capability_unavailable')
    c=body['configuration'];statuses=c.get('statuses');limits=c.get('media_attachments')
    require(isinstance(statuses,dict) and isinstance(limits,dict),'media_capability_unavailable')
    version=body.get('version');require(type(version) is str and 0<len(version)<=128 and all(32<=ord(c)<127 for c in version),'media_capability_unavailable: version')
    mime=limits.get('supported_mime_types');require(type(mime) is list and mime and all(type(v) is str and v for v in mime),'media_capability_unavailable: MIME')
    out={'instance':instance,'version':version,'observed_at':jst.iso(),'max_media_attachments':_positive(statuses,'max_media_attachments'),'supported_mime_types':mime}
    for key in ('image_size_limit','image_matrix_limit','video_size_limit','video_matrix_limit','video_frame_rate_limit','description_limit'):
        value=limits.get(key)
        require(value is None or (type(value) is int and value>0),'media_capability_unavailable: '+key)
        out[key]=value
    return out


def check_limits(cap,items):
    require(len(items)<=cap['max_media_attachments'],'media_limit_exceeded: count')
    for item in items:
        row=item.manifest;kind=row['kind'];fmt=row['format']
        require(MIME.get(fmt) in cap['supported_mime_types'],'unsupported_attachment: mastodon/'+fmt)
        limit=_positive(cap,kind+'_size_limit');matrix=_positive(cap,kind+'_matrix_limit')
        require(row['public_size']<=limit,'media_limit_exceeded: bytes')
        require(len(row['alt'])<=_positive(cap,'description_limit'),'media_limit_exceeded: alt')
        width,height=row['width'],row['height']
        if kind=='video':
            width,height,rate=mediaformats.video_metrics(item._public_fd,row['public_size'])
            # Mastodon uses average frame rate (r_frame_rate fallback), floor.
            require(math.floor(rate)<=_positive(cap,'video_frame_rate_limit'),'media_limit_exceeded: frame_rate')
        require(width is not None and height is not None and width*height<=matrix,'media_limit_exceeded: matrix')


def observe(cfg):
    """Public instance limits for attachment lint; no credential is loaded."""
    from .mastodon import MastodonAdapter
    from .. import leave_gate
    adapter=MastodonAdapter(instance=cfg.get('instance',''))
    with leave_gate.scope(cfg):
        code,value=_json(adapter,'GET','/api/v2/instance')
        require(code==200,'media_capability_unavailable')
        cap=capabilities(value,adapter.instance)
        cache(cfg,cap)
        return cap


def cache(cfg,cap):
    from .. import handoff_cursor
    name=cfg.get('account')
    if name: handoff_cursor.write_snapshot(name,'media_capabilities.json',{'schema_version':1,'media':'mastodon','capabilities':cap})


def cached(cfg):
    from .. import handoff_cursor
    from .mastodon import _instance_url
    try:
        value=handoff_cursor.read_snapshot(cfg['account'],'media_capabilities.json')
        if not isinstance(value,dict):return None
        cap=value['capabilities']; expected=_instance_url(cfg.get('instance',''))
        require(value.get('schema_version')==1 and value.get('media')=='mastodon' and cap.get('instance')==expected,'media_capability_unavailable')
        normalized=capabilities({'version':cap['version'],'configuration':{'statuses':cap,'media_attachments':cap}},expected)
        require(set(cap)==set(normalized),'media_capability_unavailable')
        at=jst.parse(cap['observed_at']);require(at is not None and at<=jst.now_jst(),'media_capability_unavailable')
        return cap
    except (OSError,ValueError,TypeError,KeyError):return None


class _NoRedirect(httpsafe.SameOriginRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise httpsafe.RedirectBlocked(req.full_url,code,'media_redirect_refused',headers,fp)


_transport=httpsafe.build_opener(_NoRedirect())


def _json(adapter,method,path,*,data=None,headers=None,timeout=None):
    req=urllib.request.Request(adapter.instance+path,data=data,method=method,headers=adapter._headers(headers))
    from .. import leave_gate
    with leave_gate.urlopen(_transport.open,req,timeout=adapter.timeout if timeout is None else min(adapter.timeout,timeout)) as resp:
        code=resp.status;adapter._remember_rate_limit(resp)
        raw=resp.read(1024*1024+1)
    require(len(raw)<=1024*1024,'media_response_invalid')
    value=json.loads(raw) if raw else {}
    require(type(value) is dict,'media_response_invalid')
    return code,value


def _multipart(item,focus=None):
    boundary='thth-'+uuid.uuid4().hex
    fields=[('description',item.manifest['alt'])]
    if focus is not None:fields.append(('focus',','.join(str(v) for v in focus)))
    pre=b''.join((f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n').encode('utf-8') for key,value in fields)
    pre+=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="attachment.{item.manifest["format"]}"\r\nContent-Type: {MIME[item.manifest["format"]]}\r\n\r\n').encode('ascii')
    end=f'\r\n--{boundary}--\r\n'.encode('ascii')
    def chunks():
        yield pre
        yield from item.chunks()
        yield end
    return chunks(),{'Content-Type':'multipart/form-data; boundary='+boundary,'Content-Length':str(len(pre)+item.manifest['public_size']+len(end))}


def _entity(value,expected=None,ready=False,kind=None):
    identifier=value.get('id');require(type(identifier) is str and identifier.isascii() and identifier.isdecimal(),'media_response_invalid: id')
    require(expected is None or identifier==expected,'media_response_invalid: id changed')
    require(value.get('type') in ('image','video','gifv','audio'),'media_response_invalid: type')
    require(kind is None or value['type'] in ({'image','gifv'} if kind=='image' else {'video','gifv'}),'media_response_invalid: type mismatch')
    if ready:
        url=value.get('url')
        try:valid=type(url) is str and url.startswith('https://') and bool(httpsafe.validated_url(url))
        except ValueError:valid=False
        require(valid,'media_response_invalid: ready URL')
    return identifier


def publish(adapter,post,*,before_publish=None):
    ts=jst.iso();phase='preflight';ids=[];progress=post.media_progress
    def record(value,**details):
        nonlocal phase
        # Change in-memory phase before callback: fsync failures cannot pretend
        # the previous provider action never occurred.
        phase=value;progress(value,remote_ids=list(ids),**details)
    try:
        require(callable(progress),'media_journal_required')
        granted=getattr(adapter,'granted_scopes',None)
        require(not post.media_manifest['files'] or granted is None or bool({'write','write:media'} & set(granted)),
                'mastodon_scope_missing: write:media; thth auth '+getattr(adapter,'auth_account','<account>')+' --by <名前>')
        why=intent_error(post.media_manifest);require(why is None,why or '')
        require(len(post.media_files)==len(post.media_manifest['files']) and all(x.manifest==row for x,row in zip(post.media_files,post.media_manifest['files'])),'media_prepared_mismatch')
        options=post.media_manifest['post_options']
        quote=next((row for row in post.media_manifest['attachments'] if row['type']=='quote'),None)
        quote_intent=quote is not None or 'quote_approval_policy' in options
        poll=next((row for row in post.media_manifest['attachments'] if row['type']=='poll'),None)
        check_text_intent(post.media_manifest,post.text)
        if post.media_files or poll is not None or quote_intent:
            code,value=_json(adapter,'GET','/api/v2/instance');require(code==200,'media_capability_unavailable')
        if poll is not None:
            cap=poll_capabilities(value,adapter.instance);check_poll(cap,poll,post.text)
        if quote_intent:quote_capabilities(value)
        if quote is not None:quote_target(adapter,quote)
        if post.media_files:
            cap=capabilities(value,adapter.instance);check_limits(cap,post.media_files)
            if post.media_cache:post.media_cache(cap)
        options=post.media_manifest['post_options']
        for i,item in enumerate(post.media_files):
            item.verify()
            if before_publish:
                veto=before_publish();require(not veto,str(veto))
            body,headers=_multipart(item,(options.get('focus') or [None]*len(post.media_files))[i])
            record('uploading',index=i)
            code,value=_json(adapter,'POST','/api/v2/media',data=body,headers=headers)
            require(code in (200,202),'media_response_invalid: upload status')
            identifier=_entity(value,ready=code==200,kind=item.manifest['kind']);ids.append(identifier)
            record('ready' if code==200 else 'processing',index=i)
            deadline=time.monotonic()+POLL_SECONDS
            while code!=200:
                require(time.monotonic()<deadline,'media_processing_timeout')
                time.sleep(min(POLL_INTERVAL,max(0,deadline-time.monotonic())))
                require(time.monotonic()<deadline,'media_processing_timeout')
                if before_publish:
                    veto=before_publish();require(not veto,str(veto))
                remaining=deadline-time.monotonic()
                require(remaining>0,'media_processing_timeout')
                code,value=_json(adapter,'GET','/api/v1/media/'+identifier,timeout=remaining)
                # A late ready response does not extend the processing budget.
                require(time.monotonic()<deadline,'media_processing_timeout')
                require(code in (200,206),'media_response_invalid: processing status')
                if code==200:_entity(value,identifier,ready=True,kind=item.manifest['kind']);record('ready',index=i)
            item.verify()
        if before_publish:
            veto=before_publish();require(not veto,str(veto))
        params=[('status',post.text),('visibility',options.get('visibility',adapter.visibility))]
        if post.reply_to:params.append(('in_reply_to_id',post.reply_to))
        params.extend(('media_ids[]',identifier) for identifier in ids)
        if poll is not None:
            params.extend(('poll[options][]',value) for value in poll['options'])
            params.append(('poll[expires_in]',str(poll['expires_in'])))
            for key in ('multiple','hide_totals'):
                if key in poll:params.append(('poll['+key+']',str(poll[key]).lower()))
        if quote is not None:params.append(('quoted_status_id',quote['uri']))
        for key in ('language','sensitive','spoiler_text','quote_approval_policy'):
            if key in options:params.append((key,str(options[key]).lower() if type(options[key]) is bool else options[key]))
        record('publishing')
        headers={'Content-Type':'application/x-www-form-urlencoded','Idempotency-Key':adapter._idempotency_key(post,options.get('visibility',adapter.visibility))}
        code,value=_json(adapter,'POST','/api/v1/statuses',data=urllib.parse.urlencode(params).encode(),headers=headers)
        require(code in (200,201) and type(value.get('id')) is str and value['id'].isascii() and value['id'].isdecimal(),'media_response_invalid: status id')
        if quote is not None:require(value.get('visibility') in ('public','unlisted'),'quote_result_non_public: mastodon')
        record('published',post_id=value['id'])
        return base.PublishResult(value['id'],value.get('url'),ts,media=[{'sha256':x.manifest['public_sha256'],'kind':x.manifest['kind'],'alt_present':True,'remote_id':identifier} for x,identifier in zip(post.media_files,ids)])
    except accounts.AccountStopped:
        # The stop authority forbids new state writes; keep the last durable
        # intent, including uploaded IDs, and never downgrade it to clearable.
        return base.PublishResult(None,None,ts,error='account_stopped',failure='media_held' if ids else 'media_ambiguous' if phase!='preflight' else 'publish_vetoed')
    except (OSError,ValueError,urllib.error.URLError) as exc:
        if isinstance(exc,urllib.error.HTTPError):
            reason=f'media_{phase}_http_{exc.code}'
            if exc.code==403:
                scope='write:media' if phase=='uploading' else 'write:statuses' if phase=='publishing' else 'write:media'
                reason='provider_forbidden: 権限（'+scope+'）の確認か再認可: thth auth '+getattr(adapter,'auth_account','<account>')+' --by <名前> (HTTP 403)'
            definite=400<=exc.code<500 and phase in ('uploading','publishing') and not ids
        else:
            reason=str(exc) if isinstance(exc,(media.MediaError,mediaformats.FormatError)) else f'media_{phase}_failed'
            definite=False
        # Even a definite post rejection can leave earlier remote media behind.
        # Preserve the journal rather than automatically re-uploading those IDs.
        held=(bool(ids) and (phase in ('processing','ready') or (phase=='publishing' and isinstance(exc,urllib.error.HTTPError) and 400<=exc.code<500)))
        uncertain=phase!='preflight' and not definite and not held
        try:
            if callable(progress):record('held' if held else 'unknown' if uncertain else 'failed',reason=reason)
        except (OSError,ValueError,accounts.AccountStopped):pass  # existing durable pre-action intent remains
        return base.PublishResult(None,None,ts,error=reason,failure='media_held' if held else 'media_ambiguous' if uncertain else 'publish_definite')
