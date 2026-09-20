"""Production endpoint/handler boundary and explicit local-only test transport."""
import contextlib
import http.server
import threading
import urllib.request
import pytest
from thth import httpsafe
from thth.adapters import bluesky, mastodon, threads

BAD=['','file:///tmp/not-a-provider','ftp://example.invalid/x','data:text/plain,secret','http://example.invalid','http://127.0.0.1','http://localhost','http://[::1]','https://','https://user:secret@example.invalid','https://example.invalid:bad','https://example.invalid:99999','https://example.invalid\n','https://example.invalid#fragment']
@pytest.mark.parametrize('value',BAD)
def test_production_endpoint_refusal_before_any_adapter_request(monkeypatch,value):
    monkeypatch.delenv('THTH_TEST_ALLOW_HTTP',raising=False)
    for create in (lambda:bluesky.BlueskyAdapter(service=value),lambda:mastodon.MastodonAdapter(instance=value),lambda:threads.ThreadsAdapter(base_url=value)):
        expected=('endpoint_http_forbidden: 平文 HTTP は使えません。https を指定してください' if value.startswith('http://') else 'endpoint_scheme_invalid: scheme は https を指定してください' if value.startswith(('file:','ftp:','data:')) else 'endpoint_invalid')
        with pytest.raises(httpsafe.EndpointRejected) as caught:create()
        assert str(caught.value)==expected


@pytest.mark.parametrize('host',['localhost','127.0.0.1','[::1]'])
def test_local_http_requires_explicit_test_flag(monkeypatch,host):
    url='http://'+host+':8123'
    monkeypatch.delenv('THTH_TEST_ALLOW_HTTP',raising=False)
    with pytest.raises(httpsafe.EndpointRejected):httpsafe.validated_url(url)
    monkeypatch.setenv('THTH_TEST_ALLOW_HTTP','1');assert httpsafe.validated_url(url)==url
    for bad in ['localhost.example','127.0.0.1.example','127.1','2130706433','0x7f000001']:
        with pytest.raises(httpsafe.EndpointRejected):httpsafe.validated_url('http://'+bad)


def test_final_opener_has_no_implicit_protocol_or_proxy_handlers(tmp_path):
    opener=httpsafe.build_opener()
    forbidden=(urllib.request.FileHandler,urllib.request.FTPHandler,urllib.request.DataHandler,urllib.request.ProxyHandler)
    assert not any(isinstance(handler,forbidden) for handler in opener.handlers)
    path=tmp_path/'private';path.write_text('private fixture')
    for url in (path.as_uri(),'data:text/plain,private','ftp://127.0.0.1/private'):
        with pytest.raises(httpsafe.EndpointRejected):opener.open(url,timeout=.1)
    req=urllib.request.Request('https://example.invalid/x');req.set_proxy('127.0.0.1:8123','http')
    with pytest.raises(httpsafe.EndpointRejected):opener.open(req,timeout=.1)


@contextlib.contextmanager
def server():
    calls=[]
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            calls.append(self.path);self.send_response(200);self.end_headers();self.wfile.write(b'local')
    value=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    worker=threading.Thread(target=value.serve_forever,daemon=True);worker.start()
    try:yield f'http://127.0.0.1:{value.server_port}',calls
    finally:value.shutdown();value.server_close();worker.join(3)


def test_real_http_ignores_environment_proxy(monkeypatch):
    with server() as (origin,direct),server() as (proxy,proxied):
        for key in ('http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','all_proxy'):monkeypatch.setenv(key,proxy)
        for key in ('no_proxy','NO_PROXY'):monkeypatch.setenv(key,'')
        with httpsafe.build_opener().open(origin+'/actual',timeout=2) as response:assert response.read()==b'local'
        assert direct==['/actual'] and proxied==[]
