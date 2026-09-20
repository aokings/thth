"""Local generated fakes only; never contact the deployed approval service."""
import contextlib
import io
import json
import os
from pathlib import Path
import secrets
import socket
import stat
import subprocess
from types import SimpleNamespace
import pytest
from thth import admin_log, approval_relay as relay, cli


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    root=tmp_path/'root';root.mkdir();apps=tmp_path/'apps';apps.mkdir(mode=0o700)
    home=tmp_path/'home';home.mkdir()
    for key,value in {'HOME':home,'THTH_ROOT':root,'THTH_APPS_DIR':apps,'THTH_ACCOUNTS_DIR':root/'accounts'}.items():monkeypatch.setenv(key,str(value))
    monkeypatch.setattr(socket.socket,'connect',lambda *a:pytest.fail('external network'))
    return root,apps


@pytest.fixture
def key(isolated):
    public=relay.init_key('operator');return public


def test_init_current_cli_public_only_private600_event(isolated,capsys):
    assert cli.main(['admin','relay-key','init','--by','operator'])==0
    output=capsys.readouterr();path=relay.key_path()
    assert output.out.startswith('APPROVAL_PUBLIC_KEY=') and output.err==''
    assert stat.S_IMODE(path.stat().st_mode)==0o600
    assert path.read_text().startswith('-----BEGIN PRIVATE KEY-----')
    rows,broken=admin_log.read();assert not broken and rows[0]['event']=='relay_key_initialized'
    assert rows[0]['diff']=={'private_key':['absent','present']}
    assert path.read_text() not in output.out+json.dumps(rows)
    before=path.read_bytes();assert cli.main(['admin','relay-key','init','--by','operator'])==2;assert path.read_bytes()==before


def test_missing_actor_has_no_files_or_openssl(isolated,monkeypatch):
    monkeypatch.setattr(relay,'_openssl',lambda *a,**k:pytest.fail('keygen before by'))
    with pytest.raises(ValueError):relay.init_key(None)
    assert not list(isolated[0].rglob('*')) and not list(isolated[1].rglob('*'))


@pytest.mark.parametrize('kind',['symlink','hardlink','fifo','directory','mode','parent_mode'])
def test_private_leaf_and_parent_fail_closed(isolated,kind):
    path=relay.key_path()
    if kind=='symlink':
        target=isolated[1]/'target';target.write_text(secrets.token_urlsafe(32));path.symlink_to(target)
    elif kind=='hardlink':
        target=isolated[1]/'target';target.write_text(secrets.token_urlsafe(32));target.chmod(0o600);os.link(target,path)
    elif kind=='fifo':os.mkfifo(path,0o600)
    elif kind=='directory':path.mkdir()
    else:path.write_text('invalid');path.chmod(0o644 if kind=='mode' else 0o600)
    if kind=='parent_mode':isolated[1].chmod(0o755)
    with pytest.raises((relay.RelayError,OSError)):
        with relay.private_key():pytest.fail('unsafe key opened')


def test_symlink_parent_is_not_followed(isolated,monkeypatch):
    alias=isolated[1].parent/'alias';alias.symlink_to(isolated[1],target_is_directory=True);monkeypatch.setenv('THTH_APPS_DIR',str(alias))
    with pytest.raises(OSError):relay.init_key('operator')
    assert list(isolated[1].iterdir())==[]


@pytest.mark.parametrize('failure',['zero','partial','fsync'])
def test_init_audit_failure_contract(isolated,monkeypatch,failure):
    def emit(fd,data):
        if failure=='zero':raise admin_log.AdminLogError('fault')
        os.write(fd,data if failure=='fsync' else data[:len(data)//2])
        raise admin_log.AdminLogError('fault',appended=True,complete=failure=='fsync')
    monkeypatch.setattr(admin_log,'_emit',emit)
    with pytest.raises(admin_log.AdminLogError):relay.init_key('operator')
    assert relay.key_path().exists() is (failure!='zero')
    rows,broken=admin_log.read();assert len(rows)==(failure=='fsync');assert broken==(failure=='partial')


def test_no_tty_refuses_before_secret_generation_or_provision(isolated,monkeypatch):
    @contextlib.contextmanager
    def missing():raise OSError('no controlling tty');yield
    monkeypatch.setattr(relay,'terminal',missing)
    monkeypatch.setattr(relay.secrets,'token_urlsafe',lambda *a:pytest.fail('generated before tty'))
    monkeypatch.setattr(relay,'signed_request',lambda *a:pytest.fail('provision before tty'))
    assert cli.main(['admin','approver','set','person','--by','operator'])==2
    assert not list(isolated[0].rglob('*'))


def tty_fixture(monkeypatch):
    stream=io.StringIO()
    @contextlib.contextmanager
    def tty():yield stream
    monkeypatch.setattr(relay,'terminal',tty)
    return stream


def test_set_secret_only_tty_and_presence_event(isolated,monkeypatch,capsys):
    tty=tty_fixture(monkeypatch);received=[]
    def request(*args):received.append(args);return {'status':'configured'}
    monkeypatch.setattr(relay,'signed_request',request)
    monkeypatch.setattr('thth.accounts.load_account',lambda *a:pytest.fail('person is not account'))
    assert cli.main(['admin','approver','set','person','--by','operator'])==0
    secret=tty.getvalue().strip().split(': ')[1];assert len(secret)>=16 and tty.getvalue().count(secret)==1
    data=received[0][3]
    assert data['iterations']==600000
    import hashlib,base64
    assert relay.b64(hashlib.pbkdf2_hmac('sha256',secret.encode(),base64.urlsafe_b64decode(data['salt']+'='),600000,32))==data['verifier']
    rows,broken=admin_log.read();assert not broken and rows[0]['event']=='approver_set'
    assert rows[0]['diff']=={'credential_present':[None,True]}
    output=capsys.readouterr();observed=output.out+output.err+json.dumps(rows)
    assert all(v not in observed for v in (secret,data['verifier']))


@pytest.mark.parametrize('mode',['badlog','zero','partial','fsync','remote','delivery'])
def test_provision_partial_failure_is_explicit(isolated,monkeypatch,capsys,mode):
    tty=tty_fixture(monkeypatch);calls=[]
    def request(*args):
        calls.append(1)
        if mode=='remote':raise relay.RelayError('approval_relay_outcome_unknown')
        return {'status':'configured'}
    monkeypatch.setattr(relay,'signed_request',request)
    if mode=='badlog':
        with admin_log.transaction():pass
        (isolated[0]/'state/_admin/accounts.ndjson').chmod(0o644)
    if mode in ('zero','partial','fsync'):
        def emit(fd,data):
            if mode!='zero':os.write(fd,data if mode=='fsync' else data[:len(data)//2])
            raise admin_log.AdminLogError('fault',appended=mode!='zero',complete=mode=='fsync')
        monkeypatch.setattr(admin_log,'_emit',emit)
    if mode=='delivery':monkeypatch.setattr(tty,'write',lambda *a:(_ for _ in ()).throw(OSError('tty disappeared')))
    assert cli.main(['admin','approver','set','person','--by','operator'])==2
    assert len(calls)==(mode!='badlog')
    output=capsys.readouterr();assert 'secret' not in output.out
    if mode in ('zero','partial','fsync'):assert 'remote_changed_audit_unconfirmed' in output.err and tty.getvalue()
    if mode=='delivery':assert 'remote_changed_delivery_failed' in output.err


def test_signer_actual_rsa_signature_and_canonical_wire(key,monkeypatch,tmp_path):
    captured=[]
    class Response:
        status=200
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,n):return b'{"status":"unlocked"}'
    class Opener:
        def open(self,request,timeout):captured.append(request);return Response()
    monkeypatch.setattr(relay.urllib.request,'build_opener',lambda *a:Opener())
    assert relay.signed_request('person','person','unlock',{})=={'status':'unlocked'}
    request=captured[0];headers={k.lower():v for k,v in request.header_items()}
    import base64
    sig=tmp_path/'sig';sig.write_bytes(base64.urlsafe_b64decode(headers['x-thth-signature']))
    pub=tmp_path/'public.der';pub.write_bytes(base64.urlsafe_b64decode(key+'='*((-len(key))%4)))
    message=relay.canonical('POST','/approval/person/person/unlock','operator','person','unlock',headers['x-thth-time'],headers['x-thth-nonce'],b'{}')
    result=subprocess.run([relay.openssl(),'dgst','-sha256','-verify',str(pub),'-keyform','DER','-signature',str(sig),'-sigopt','rsa_padding_mode:pss','-sigopt','rsa_pss_saltlen:32'],input=message,capture_output=True)
    assert result.returncode==0
    assert headers['user-agent'].startswith('thth/')


@pytest.mark.parametrize('url',['http://outside.test','https://outside.test','https://thth.me@evil.test','https://thth.me/a','https://thth.me?x=1','https://thth.me\n'])
def test_only_official_origin_or_explicit_loopback(key,monkeypatch,url):
    monkeypatch.setenv('THTH_APPROVAL_BASE_URL',url)
    with pytest.raises(relay.RelayError,match='approval_origin_invalid'):relay.signed_request('person','person','unlock',{})


def test_openssl_missing_bounded(isolated,monkeypatch):
    monkeypatch.setattr(relay.os.path,'isfile',lambda _:False)
    with pytest.raises(relay.RelayError,match='openssl_3_required'):relay.init_key('operator')
    assert not relay.key_path().exists()


def test_provision_network_does_not_hold_global_admin_flock(isolated,monkeypatch):
    tty_fixture(monkeypatch)
    def request(*args):
        import sys
        path=isolated[0]/'state/_admin/accounts.ndjson'
        check=subprocess.run([sys.executable,'-c','import os,fcntl,sys;f=os.open(sys.argv[1],os.O_RDONLY);fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)',str(path)],capture_output=True,timeout=3)
        assert check.returncode==0
        return {'status':'configured'}
    monkeypatch.setattr(relay,'signed_request',request)
    relay.manage_person('set','person','operator')


def test_privacy_logical_ttl_and_no_physical_erase_claim():
    from tests.test_site import build_site
    page=build_site.build_privacy()
    for phrase in ('600 seconds','600秒','600,000','PITR','30 days','30日','not a promise of immediate physical erasure','即時物理消去とは約束しません'):
        assert phrase in page


def test_project_key_store_refused_without_traceback(isolated,monkeypatch,capsys):
    monkeypatch.setenv('THTH_APPS_DIR',str(isolated[0]/'apps'))
    assert cli.main(['admin','relay-key','init','--by','operator'])==2
    assert 'relay_signer_store_invalid' in capsys.readouterr().err
    assert not (isolated[0]/'apps').exists()


def test_show_recovers_committed_key_after_export_failure(isolated,monkeypatch,capsys):
    export=relay.public_key
    monkeypatch.setattr(relay,'public_key',lambda:(_ for _ in ()).throw(relay.RelayError('relay_signer_unavailable')))
    assert cli.main(['admin','relay-key','init','--by','operator'])==2
    assert relay.key_path().exists()
    capsys.readouterr()
    before={str(p):(p.read_bytes(),stat.S_IMODE(p.stat().st_mode)) for parent in isolated for p in parent.rglob('*') if p.is_file()}
    monkeypatch.setattr(relay,'public_key',export)
    expected=export()
    monkeypatch.setattr(relay,'init_key',lambda *a:pytest.fail('show must never initialize'))
    for _ in range(2):
        assert cli.main(['admin','relay-key','show','--by','operator'])==0
        output=capsys.readouterr();assert output.out=='APPROVAL_PUBLIC_KEY='+expected+'\n' and output.err==''
        assert 'PRIVATE KEY' not in output.out
    after={str(p):(p.read_bytes(),stat.S_IMODE(p.stat().st_mode)) for parent in isolated for p in parent.rglob('*') if p.is_file()}
    assert before==after
    rows,broken=admin_log.read();assert not broken and len(rows)==1 and rows[0]['event']=='relay_key_initialized'


def test_show_missing_key_never_creates_and_requires_actor(isolated,monkeypatch,capsys):
    monkeypatch.setattr(relay,'_openssl',lambda *a,**k:pytest.fail('missing key must not run openssl'))
    assert cli.main(['admin','relay-key','show','--by','operator'])==2
    assert not list(isolated[0].rglob('*')) and not list(isolated[1].rglob('*'))
    with pytest.raises(ValueError):relay.show_key(None)
    with pytest.raises(SystemExit):cli.main(['admin','relay-key','show'])
    output=capsys.readouterr();assert 'PRIVATE KEY' not in output.out+output.err


@pytest.mark.parametrize('kind',['symlink','hardlink','fifo','mode','parent_mode'])
def test_show_unsafe_key_refused_at_cli(isolated,kind,capsys):
    path=relay.key_path();value=secrets.token_urlsafe(32)
    target=isolated[1]/'target';target.write_text(value);target.chmod(0o600)
    if kind=='symlink':path.symlink_to(target)
    elif kind=='hardlink':os.link(target,path)
    elif kind=='fifo':os.mkfifo(path,0o600)
    else:path.write_text(value);path.chmod(0o644 if kind=='mode' else 0o600)
    if kind=='parent_mode':isolated[1].chmod(0o755)
    assert cli.main(['admin','relay-key','show','--by','operator'])==2
    output=capsys.readouterr();assert output.out=='' and value not in output.err
    assert not list(isolated[0].rglob('*'))
