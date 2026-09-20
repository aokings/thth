"""Reacceptance followups: input meaning, stop directory facts, relay transport."""
import json
import pytest
from thth import accounts,cli,leave_gate


@pytest.mark.parametrize('name',['../x','../../etc/passwd','a/b','..','.','','x\x00y','日本語'])
def test_run_invalid_name_before_any_storage_or_effect(monkeypatch,capsys,name):
    def forbidden(*a,**kw):pytest.fail('invalid name reached storage/effect')
    for module,attr in [(leave_gate,'require_active'),(accounts,'state_dir_for'),(accounts,'load_account')]:
        monkeypatch.setattr(module,attr,forbidden)
    monkeypatch.setattr('thth.core.throw_once',forbidden)
    monkeypatch.setattr('thth.healthcheck.notify',forbidden)
    monkeypatch.setattr('thth.incident.notify',forbidden)
    assert cli.main(['run',name])==2
    captured=capsys.readouterr();value=json.loads(captured.out)
    assert captured.err.strip()=='invalid_account_name'
    assert value['error']==value['record_unavailable']=='invalid_account_name'
    assert value['account'] is None and value['runs_recorded'] is False
    assert '使えない字' in value['message'] and 'account_stopped' not in captured.out
    assert name not in captured.out if name and name not in ('..','.') else True


@pytest.mark.parametrize('directory',['state/_leave','state/_leave/coordination'])
def test_doctor_actual_stop_directories_warn_without_write(tmp_path,monkeypatch,capsys,directory):
    from thth import stop_observation
    root=tmp_path.resolve()/'root';(root/'state/_leave/coordination').mkdir(parents=True,mode=0o700)
    (root/'state/_leave').chmod(0o700);(root/directory).chmod(0o775)
    monkeypatch.setenv('THTH_ROOT',str(root));monkeypatch.setenv('THTH_ACCOUNTS_DIR',str(root/'accounts'))
    before={str(p.relative_to(root)):p.lstat().st_mode for p in root.rglob('*')}
    monkeypatch.setattr(accounts,'load_token',lambda *a:pytest.fail('token read'))
    assert cli.main(['doctor','alpha','--json'])==2
    captured=capsys.readouterr();value=json.loads(captured.out)
    row=next(r for r in value['directory_checks'] if r['directory']==directory)
    assert row['warning']=='unsafe_directory_mode' and row['present'] is True
    assert value['probes']==[] and str(root) not in captured.out
    assert before=={str(p.relative_to(root)):p.lstat().st_mode for p in root.rglob('*')}


def test_doctor_does_not_follow_stop_directory_symlink(tmp_path,monkeypatch):
    from thth import stop_observation
    root=tmp_path.resolve()/'root';(root/'state').mkdir(parents=True)
    outside=tmp_path.resolve()/'outside';(outside/'coordination').mkdir(parents=True)
    (root/'state/_leave').symlink_to(outside,target_is_directory=True)
    monkeypatch.setenv('THTH_ROOT',str(root));monkeypatch.setenv('THTH_ACCOUNTS_DIR',str(root/'accounts'))
    rows={r['directory']:r for r in stop_observation.directories()}
    assert rows['state/_leave']['warning']=='unsafe_directory_type'
    assert rows['state/_leave/coordination']['warning']=='directory_unreadable'
    assert rows['state/_leave/coordination']['mode'] is None


@pytest.mark.parametrize('url',['http://127.0.0.1:8123','http://localhost:8123','http://[::1]:8123',
                                'http://127.0.0.2','http://127.1','http://outside.invalid',
                                'https://user:secret@thth.me','https://thth.me:bad','file:///tmp/key','data:text/plain,a'])
def test_relay_rejected_endpoint_before_signing_or_connect(monkeypatch,url):
    from thth import approval_relay as relay,httpsafe
    monkeypatch.delenv('THTH_TEST_ALLOW_HTTP',raising=False)
    monkeypatch.setenv('THTH_APPROVAL_BASE_URL',url)
    monkeypatch.setattr(relay,'private_key',lambda:pytest.fail('key access'))
    monkeypatch.setattr(httpsafe,'build_opener',lambda *a:pytest.fail('transport creation'))
    with pytest.raises(relay.RelayError,match='^approval_origin_invalid$'):
        relay.signed_request('person','tester','status',{})


def fake_signer(monkeypatch):
    import contextlib
    from thth import approval_relay as relay
    @contextlib.contextmanager
    def key():yield -1
    monkeypatch.setattr(relay,'private_key',key)
    monkeypatch.setattr(relay,'_openssl',lambda *a,**kw:b'synthetic-signature')


def test_relay_explicit_loopback_uses_common_gate_and_actual_http(monkeypatch):
    import http.server,threading
    from thth import approval_relay as relay
    calls=[]
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_POST(self):
            raw=self.rfile.read(int(self.headers['Content-Length']));calls.append((self.path,json.loads(raw)))
            out=b'{"status":"active"}';self.send_response(200);self.send_header('Content-Length',str(len(out)));self.end_headers();self.wfile.write(out)
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        monkeypatch.setenv('THTH_TEST_ALLOW_HTTP','1');monkeypatch.setenv('THTH_APPROVAL_BASE_URL',f'http://127.0.0.1:{server.server_port}')
        fake_signer(monkeypatch)
        assert relay.signed_request('person','tester','status',{})=={'status':'active'}
        assert calls==[('/approval/person/tester/status',{})]
    finally:server.shutdown();server.server_close();thread.join(3)


@pytest.mark.parametrize('phase',['preconnect','after_send'])
def test_relay_preconnect_rejected_but_post_send_failure_unknown(monkeypatch,phase):
    from thth import approval_relay as relay,httpsafe
    fake_signer(monkeypatch);monkeypatch.setenv('THTH_APPROVAL_BASE_URL','https://thth.me')
    calls=[]
    class Transport:
        def open(self,*a,**kw):
            calls.append(phase)
            if phase=='preconnect':raise httpsafe.EndpointRejected('static')
            raise TimeoutError('synthetic response loss')
    monkeypatch.setattr(httpsafe,'build_opener',lambda *a:Transport())
    reason='approval_relay_endpoint_rejected' if phase=='preconnect' else 'approval_relay_outcome_unknown'
    with pytest.raises(relay.RelayError,match='^'+reason+'$'):relay.signed_request('person','tester','status',{})
    assert calls==[phase]
