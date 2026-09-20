"""VM boundary uses prepared bytes, static errors, and verified private snapshots."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import urllib.request
import pytest
from thth import media_relay as relay


@pytest.fixture
def env(tmp_path,monkeypatch):
    root=tmp_path.resolve()/'root';root.mkdir(mode=0o700)
    monkeypatch.setenv('THTH_ROOT',str(root));monkeypatch.setenv('THTH_MEDIA_BASE_URL','http://127.0.0.1:12345')
    return root


@pytest.mark.parametrize('bad',['http://remote.example','https://remote.example','https://thth.me@remote.example','https://thth.me/x','https://thth.me?x=1','https://thth.me\n'])
def test_origin_fail_closed(monkeypatch,bad):
    monkeypatch.setenv('THTH_MEDIA_BASE_URL',bad)
    with pytest.raises(relay.MediaRelayError,match='media_relay_origin_invalid'):relay.origin()


def test_canonical_is_domain_and_all_authority_bound():
    values=['POST','/media/example/create','example','create',123,'nonce',b'{}']
    original=relay.canonical(*values)
    assert original.startswith(b'thth-media-v1\nPOST\n') and b'\nmedia\n' in original
    for i in range(len(values)):
        different=list(values);different[i]=b'other' if i==6 else 124 if i==4 else str(values[i])+'x'
        assert relay.canonical(*different)!=original


def test_wrong_actor_stops_before_signing(env,monkeypatch):
    monkeypatch.setattr(relay,'request_for',lambda *a:pytest.fail('signed outside owner'))
    with pytest.raises(relay.MediaRelayError,match='media_owner_mismatch'):
        relay.MediaRelay('alpha','person').control(secrets.token_urlsafe(32),'status',{'actor':'other','account':'alpha'})


class Prepared:
    def __init__(self,data):
        self.data=data;self.reads=0;self.checked=0
        self.manifest={'format':'png','public_size':len(data),'public_sha256':hashlib.sha256(data).hexdigest(),'source_sha256':hashlib.sha256(b'source metadata').hexdigest()}
    def verify(self):self.checked+=1
    def chunks(self):
        self.reads+=1
        for at in range(0,len(self.data),700000):yield self.data[at:at+700000]


@pytest.mark.parametrize('size',[30,100000001])
def test_prepared_stream_and_multipart_are_exact_order_without_auto_retry(env,monkeypatch,size):
    item=Prepared(b'J'*size);client=relay.MediaRelay('alpha','person');controls=[];uploads=[]
    def control(subject,operation,body):
        controls.append((operation,body))
        return {'status':'pending','size':size} if operation=='create' else {'status':'ready','sha256':item.manifest['public_sha256']}
    monkeypatch.setattr(client,'control',control)
    received=[]
    monkeypatch.setattr(client,'_receive',lambda subject,binding,n:received.append((binding,n)))
    def upload(account,request):
        assert account=='alpha'
        data=request.data if isinstance(request.data,bytes) else b''.join(request.data)
        assert int(request.get_header('Content-length'))==len(data)
        uploads.append(data)
        return {'status':'uploaded' if size<=100000000 else 'part_uploaded'}
    monkeypatch.setattr(relay,'_json',upload)
    client.upload_prepared(item)
    same_bytes=b''.join(uploads)==item.data
    assert same_bytes, 'prepared bytes differ'
    assert item.reads==1 and item.checked==1
    assert [x[0] for x in controls]==['create','complete']
    assert controls[0][1]['sha256']==item.manifest['public_sha256']
    assert received==[(client.binding(item.manifest['public_sha256']),size)]
    if size>100000000:assert all(len(x)==5*1024*1024 for x in uploads[:-1])


@pytest.mark.parametrize('fault',['short','extra','sha','io'])
def test_raw_source_bad_transfer_never_acknowledged(env,monkeypatch,fault):
    expected=b'generated synthetic '+secrets.token_bytes(12);sha=hashlib.sha256(expected).hexdigest();calls=[]
    client=relay.MediaRelay('alpha','person')
    def control(subject,op,body):
        calls.append(op);return {'status':'ready','size':len(expected),'sha256':sha}
    monkeypatch.setattr(client,'control',control)
    monkeypatch.setattr(relay,'request_for',lambda *a:urllib.request.Request('http://127.0.0.1/read'))
    class Response(io.BytesIO):
        status=200
        def read(self,*a):
            if fault=='io':raise OSError('fake private error '+secrets.token_urlsafe(10))
            return super().read(*a)
    body=expected[:-1] if fault=='short' else expected+b'x' if fault=='extra' else b'K'*len(expected)
    monkeypatch.setattr(relay.httpsafe,'build_opener',lambda *a:type('Opener',(),{'open':lambda *a,**kw:Response(body)})())
    with pytest.raises(relay.MediaRelayError,match='media_source_(mismatch|unavailable)'):
        with client.source_snapshot(secrets.token_urlsafe(32),sha):pytest.fail('bad transfer yielded')
    assert calls==['status']


def test_raw_source_verified_then_ack_then_closed(env,monkeypatch):
    data=secrets.token_bytes(40);sha=hashlib.sha256(data).hexdigest();calls=[];client=relay.MediaRelay('alpha','person')
    def control(subject,op,body):
        calls.append(op);return {'status':'ready','size':len(data),'sha256':sha} if op=='status' else {'status':'retired'}
    monkeypatch.setattr(client,'control',control)
    monkeypatch.setattr(relay,'request_for',lambda *a:urllib.request.Request('http://127.0.0.1/read'))
    class Response(io.BytesIO):status=200
    monkeypatch.setattr(relay.httpsafe,'build_opener',lambda *a:type('Opener',(),{'open':lambda *a,**kw:Response(data)})())
    with client.source_snapshot(secrets.token_urlsafe(32),sha) as snapshot:
        assert calls==['status','ack'] and snapshot.read()==data
        assert os.fstat(snapshot.fileno()).st_mode&0o777==0o600
    assert snapshot.closed


def test_sanitized_upload_checks_stored_bytes_before_returning_grant_source(env,monkeypatch):
    item=Prepared(secrets.token_bytes(40));client=relay.MediaRelay('alpha','person');calls=[]
    def control(subject,op,body):
        calls.append(op)
        return {'status':'pending','size':40} if op=='create' else {'status':'ready','sha256':item.manifest['public_sha256']}
    monkeypatch.setattr(client,'control',control)
    monkeypatch.setattr(relay,'_json',lambda *a:{'status':'uploaded'})
    monkeypatch.setattr(relay,'request_for',lambda *a:urllib.request.Request('http://127.0.0.1/read'))
    class Response(io.BytesIO):status=200
    monkeypatch.setattr(relay.httpsafe,'build_opener',lambda *a:type('Opener',(),{'open':lambda *a,**kw:Response(b'X'*40)})())
    with pytest.raises(relay.MediaRelayError,match='media_source_mismatch'):client.upload_prepared(item)
    assert calls==['create','complete']
