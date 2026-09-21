"""Known control refusals keep static status; transmitted failures stay unknown."""
import contextlib
import http.server
import json
import secrets
import threading
import urllib.error
import urllib.request
import pytest
from thth import accounts,httpsafe,inflight,media,media_delivery,media_relay as relay
from thth.adapters import base
from tests.test_v213_threads_media import wire,env,invoke,posts

@pytest.mark.parametrize('status',[400,401,403,404,409,410,413,429,500,503])
def test_actual_http_status_survives_without_capability_or_body(tmp_path,monkeypatch,status):
    monkeypatch.setenv('THTH_ROOT',str(tmp_path.resolve()/'root'))
    secret=secrets.token_urlsafe(32);calls=[]
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_POST(self):
            calls.append(self.path);self.rfile.read(int(self.headers.get('Content-Length','0')))
            body=json.dumps({'private':secret}).encode();self.send_response(status);self.send_header('Content-Length',str(len(body)));self.send_header('X-Private',secret);self.end_headers();self.wfile.write(body)
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        request=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/media/'+secret,data=b'{}',method='POST')
        with pytest.raises(urllib.error.HTTPError) as caught:relay._json('alpha',request)
        error=caught.value
        assert error.code==status and error.url=='' and error.headers is None
        assert secret not in str(error) and secret not in repr(error) and error.read()==b''
        assert calls==['/media/'+secret]
    finally:server.shutdown();server.server_close();thread.join(3)


def test_endpoint_refusal_precedes_connection_and_is_static(tmp_path,monkeypatch):
    monkeypatch.setenv('THTH_ROOT',str(tmp_path.resolve()/'root'));monkeypatch.delenv('THTH_TEST_ALLOW_HTTP',raising=False)
    secret=secrets.token_urlsafe(32)
    monkeypatch.setattr(http.server,'HTTPServer',lambda *a,**kw:pytest.fail('server created'))
    monkeypatch.setattr('socket.socket.connect',lambda *a:pytest.fail('connection attempted'))
    with pytest.raises(httpsafe.EndpointRejected,match='^media_relay_endpoint_rejected$'):
        relay._json('alpha',urllib.request.Request('http://127.0.0.1/media/'+secret,data=b'{}'))


@pytest.mark.parametrize('fault',[TimeoutError('private'),ConnectionResetError('private'),urllib.error.URLError('private')])
def test_transmitted_transport_failures_are_not_definite(tmp_path,monkeypatch,fault):
    monkeypatch.setenv('THTH_ROOT',str(tmp_path.resolve()/'root'));calls=[]
    class Opener:
        def open(self,*a,**kw):calls.append(1);raise fault
    monkeypatch.setattr(httpsafe,'build_opener',lambda *a:Opener())
    with pytest.raises(relay.MediaRelayError,match='^media_relay_outcome_unknown$'):
        relay._json('alpha',urllib.request.Request('https://thth.me/media/'+secrets.token_urlsafe(32),data=b'{}'))
    assert calls==[1]


@pytest.mark.parametrize('phase',['upload','grant'])
@pytest.mark.parametrize('fault,expected',[(400,'publish_definite'),(403,'publish_definite'),(429,'publish_definite'),(500,'media_ambiguous'),('endpoint','publish_definite'),('timeout','media_ambiguous')])
def test_real_journal_refusal_before_graph(env,wire,monkeypatch,phase,fault,expected):
    old=relay.MediaRelay;calls=[]
    class Broken(old):
        def fail(self):
            calls.append(phase)
            if fault=='endpoint':raise httpsafe.EndpointRejected('media_relay_endpoint_rejected')
            if fault=='timeout':raise relay.MediaRelayError('media_relay_outcome_unknown')
            raise urllib.error.HTTPError('',fault,'media_relay_http_error',None,None)
        def upload_prepared(self,item):
            if phase=='upload':self.fail()
            return super().upload_prepared(item)
        def grant(self,*a,**kw):self.fail()
    monkeypatch.setattr(relay,'MediaRelay',Broken)
    result,journal,manifest=invoke(env)
    assert result.post_id is None and result.failure==expected
    assert journal['media']['phase']==('failed' if expected=='publish_definite' else 'unknown')
    assert calls==[phase] and wire['calls']==[] and env[3]['results']==[]
    if fault=='endpoint':assert result.error=='media_relay_endpoint_rejected'
    if expected=='media_ambiguous':
        from thth import core
        again=core.send_once('alpha',text='',media_rows=[{'file':'a.png','alt':'dot'}],production_flag=True,confirm='unused',adapter_factory=lambda *a:pytest.fail('restart adapter constructed'),log=lambda _:None)
        assert again.action=='inflight' and calls==[phase] and wire['calls']==[]

@pytest.mark.parametrize('fault',[403,'endpoint'])
def test_later_refusal_holds_existing_container_and_invalidates_once(env,wire,monkeypatch,fault):
    old=relay.MediaRelay;attempts=[]
    class Broken(old):
        def upload_prepared(self,item):
            attempts.append(1)
            if len(attempts)==2:
                if fault=='endpoint':raise httpsafe.EndpointRejected('media_relay_endpoint_rejected')
                raise urllib.error.HTTPError('',fault,'media_relay_http_error',None,None)
            return super().upload_prepared(item)
    monkeypatch.setattr(relay,'MediaRelay',Broken)
    (env[2]/'b.png').write_bytes((env[2]/'a.png').read_bytes())
    result,journal,_=invoke(env,{'media':[{'file':'a.png','alt':'a'},{'file':'b.png','alt':'b'}]})
    assert result.failure=='media_held' and journal['media']['phase']=='held'
    assert journal['media']['remote_ids']==['11'] and attempts==[1,1]
    assert env[3]['results']==[False] and len(posts(wire,'/threads'))==1
    assert not posts(wire,'/threads_publish')
