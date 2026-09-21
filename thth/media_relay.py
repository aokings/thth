"""Private VM-to-media control. No R2 credential or capability enters a journal."""
from __future__ import annotations
import contextlib
import hashlib
import json
import os
import secrets
import time
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from . import __version__,accounts,approval_relay,leave_gate,redact,httpsafe

MIME={'jpeg':'image/jpeg','png':'image/png','webp':'image/webp','gif':'image/gif','mp4':'video/mp4','mov':'video/quicktime'}
OPS=frozenset(('create','complete','read','ack','preview','provider','published','invalidate','status'))


class MediaRelayError(ValueError):
    """Only static diagnostics escape transport and response validation."""


def origin():
    value=os.environ.get('THTH_MEDIA_BASE_URL','https://thth.me')
    try:httpsafe.validated_url(value,base=True)
    except ValueError:raise MediaRelayError('media_relay_origin_invalid') from None
    parsed=urllib.parse.urlsplit(value)
    if (any(ord(c)<33 or ord(c)==127 for c in value) or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/') or
        not (value.rstrip('/')=='https://thth.me' or parsed.scheme=='http' and parsed.hostname in ('127.0.0.1','localhost','::1'))):
        raise MediaRelayError('media_relay_origin_invalid')
    return value.rstrip('/')


def canonical(method,path,subject,operation,timestamp,nonce,raw):
    return '\n'.join(('thth-media-v1',method,path,'media',subject,operation,str(timestamp),nonce,hashlib.sha256(raw).hexdigest())).encode()


def request_for(subject,operation,body):
    if not isinstance(subject,str) or not approval_relay.OPAQUE.fullmatch(subject) or operation not in OPS:
        raise MediaRelayError('invalid_media_operation')
    path=f'/media/{subject}/{operation}'
    raw=json.dumps(body,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
    timestamp=int(time.time()*1000);nonce=secrets.token_urlsafe(32)
    with approval_relay.private_key() as fd:
        signature=approval_relay._openssl(['dgst','-sha256','-sign',f'/dev/fd/{fd}','-sigopt','rsa_padding_mode:pss','-sigopt','rsa_pss_saltlen:32'],canonical('POST',path,subject,operation,timestamp,nonce,raw),fd=fd)
    url=origin()+path;redact.register_secret(subject);redact.register_secret(url)
    return urllib.request.Request(url,data=raw,method='POST',headers={'Content-Type':'application/json','User-Agent':f'thth/{__version__} (+https://thth.me)','X-Thth-Time':str(timestamp),'X-Thth-Nonce':nonce,'X-Thth-Signature':approval_relay.b64(signature)})


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def _json(account,request):
    try:
        with leave_gate.lease(account),httpsafe.build_opener(NoRedirect()).open(request,timeout=20) as response:
            raw=response.read(16385)
            if response.status not in (200,201) or len(raw)>16384:raise MediaRelayError('media_relay_response_invalid')
            value=json.loads(raw)
            if type(value) is not dict or 'error' in value:raise MediaRelayError('media_relay_response_invalid')
            return value
    except accounts.AccountStopped:raise
    except httpsafe.EndpointRejected:
        raise httpsafe.EndpointRejected('media_relay_endpoint_rejected') from None
    except urllib.error.HTTPError as exc:
        # Preserve only status, never a capability URL, response body or headers.
        code=exc.code
        exc.close()
        raise urllib.error.HTTPError('',code,'media_relay_http_error',None,None) from None
    except (OSError,ValueError,urllib.error.URLError) as exc:
        if isinstance(exc,MediaRelayError):raise
        raise MediaRelayError('media_relay_outcome_unknown') from None


class MediaRelay:
    def __init__(self,account,actor):
        if not accounts.name_is_safe(account) or not accounts.name_is_safe(actor):raise MediaRelayError('media_owner_invalid')
        self.account,self.actor=account,actor
    def control(self,subject,operation,body):
        if body.get('account')!=self.account or body.get('actor')!=self.actor:raise MediaRelayError('media_owner_mismatch')
        return _json(self.account,request_for(subject,operation,body))
    def binding(self,sha):return {'account':self.account,'actor':self.actor,'sha256':sha}
    def upload_prepared(self,item):
        row=item.manifest;item.verify();subject=secrets.token_urlsafe(32);redact.register_secret(subject)
        size=row['public_size'];mime=MIME.get(row['format'])
        if not mime or type(size) is not int or size<1:raise MediaRelayError('media_prepared_invalid')
        part_size=5*1024*1024 if size>100_000_000 else None
        body={**self.binding(row['public_sha256']),'size':size,'mime':mime,'kind':'sanitized','part_size':part_size}
        value=self.control(subject,'create',body)
        if value.get('status')!='pending' or value.get('size')!=size:raise MediaRelayError('media_relay_response_invalid')
        url=origin()+'/media-upload/'+subject;redact.register_secret(url)
        def send(target,data,length,expected):
            request=urllib.request.Request(target,data=data,method='PUT',headers={'Content-Type':'application/octet-stream','Content-Length':str(length),'User-Agent':f'thth/{__version__} (+https://thth.me)'})
            if _json(self.account,request).get('status')!=expected:raise MediaRelayError('media_relay_response_invalid')
        if part_size is None:send(url,item.chunks(),size,'uploaded')
        else:
            pending=bytearray();number=1;sent=0
            for chunk in item.chunks():
                pending.extend(chunk)
                while len(pending)>=part_size:
                    part=bytes(pending[:part_size]);del pending[:part_size]
                    send(url+'/'+str(number),part,len(part),'part_uploaded');sent+=len(part);number+=1
            if pending:send(url+'/'+str(number),bytes(pending),len(pending),'part_uploaded');sent+=len(pending)
            if sent!=size:raise MediaRelayError('media_prepared_invalid')
        ready=self.control(subject,'complete',self.binding(row['public_sha256']))
        if ready.get('status')!='ready' or ready.get('sha256')!=row['public_sha256']:raise MediaRelayError('media_relay_response_invalid')
        # A declared SHA is not proof of the stored bytes (parts can retry).
        # Complete makes the object immutable; verify its actual stream before
        # creating any public grant. This read does not retire sanitized bytes.
        self._receive(subject,self.binding(row['public_sha256']),size)
        return subject
    def grant(self,item,source,*,purpose='provider',expires_at=None):
        if purpose not in ('preview','provider'):raise MediaRelayError('invalid_media_purpose')
        row=item.manifest;item.verify();subject=secrets.token_urlsafe(32)
        body={**self.binding(row['public_sha256']),'media_id':row['source_sha256'],'source':source,'expires_at':expires_at if expires_at is not None else int(time.time()*1000)+600_000}
        value=self.control(subject,purpose,body)
        generation=value.get('generation')
        if value.get('status')!='ready' or not isinstance(generation,str) or not approval_relay.OPAQUE.fullmatch(generation) or type(value.get('expires_at')) is not int:
            raise MediaRelayError('media_relay_response_invalid')
        url=origin()+'/m/'+subject;redact.register_secret(url);redact.register_secret(subject);redact.register_secret(generation)
        return {'subject':subject,'url':url,'generation':generation,'purpose':purpose,'media_id':row['source_sha256'],'sha256':row['public_sha256'],'expires_at':value['expires_at']}
    def result(self,grant,*,published):
        if grant.get('purpose')!='provider':raise MediaRelayError('invalid_media_purpose')
        body={**self.binding(grant['sha256']),'media_id':grant['media_id'],'purpose':'provider','generation':grant['generation']}
        return self.control(grant['subject'],'published' if published else 'invalidate',body)


    def _receive(self,subject,binding,size,snapshot=None):
        request=request_for(subject,'read',binding);count=0;hash_value=hashlib.sha256()
        try:
            with leave_gate.lease(self.account),httpsafe.build_opener(NoRedirect()).open(request,timeout=20) as response:
                if response.status!=200:raise MediaRelayError('media_source_unavailable')
                while True:
                    chunk=response.read(min(1024*1024,size-count+1))
                    if not chunk:break
                    count+=len(chunk)
                    if count>size:raise MediaRelayError('media_source_mismatch')
                    hash_value.update(chunk)
                    if snapshot is not None:snapshot.write(chunk)
        except accounts.AccountStopped:raise
        except (OSError,ValueError,urllib.error.URLError) as exc:
            if isinstance(exc,MediaRelayError):raise
            raise MediaRelayError('media_source_unavailable') from None
        if count!=size or hash_value.hexdigest()!=binding['sha256']:raise MediaRelayError('media_source_mismatch')

    @contextlib.contextmanager
    def source_snapshot(self,subject,sha256,size):
        # `size` is the VM's own record, written before the bytes existed. The
        # Worker only repeats it, so a different answer is a mismatch and not a
        # new truth: refuse it here, before a single byte is read or buffered.
        if type(size) is not int or size<1:raise MediaRelayError('media_prepared_invalid')
        binding=self.binding(sha256);value=self.control(subject,'status',binding)
        if value.get('status')!='ready' or value.get('sha256')!=sha256 or type(value.get('size')) is not int or value['size']<1:
            raise MediaRelayError('media_source_unavailable')
        if value['size']!=size:raise MediaRelayError('media_source_mismatch')
        with tempfile.TemporaryFile() as snapshot:
            self._receive(subject,binding,size,snapshot)
            snapshot.flush();snapshot.seek(0)
            if self.control(subject,'ack',binding).get('status')!='retired':raise MediaRelayError('media_retirement_unconfirmed')
            yield snapshot
