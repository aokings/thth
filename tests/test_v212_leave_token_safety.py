"""Leave/delete matching must validate nonregular leaves before any blocking read."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from thth import accounts,leave
from tests.test_v212_server_writes import env


@pytest.mark.parametrize('account',['alpha','beta'])
@pytest.mark.parametrize('kind',['fifo','symlink','hardlink','directory','mode'])
def test_unsafe_token_in_own_or_shared_registry_refuses_without_network(env,tmp_path,account,kind):
    p=Path(accounts.load_account(account)['token']);p.unlink()
    outside=tmp_path/'outside';outside.write_text(json.dumps({'user_id':'synthetic'}));outside.chmod(0o600)
    if kind=='fifo':os.mkfifo(p,0o600)
    elif kind=='symlink':p.symlink_to(outside)
    elif kind=='hardlink':os.link(outside,p)
    elif kind=='directory':p.mkdir(mode=0o700)
    else:p.write_bytes(outside.read_bytes());p.chmod(0o644)
    code="""from thth import leave,approval_relay
import socket
socket.socket.connect=lambda *a: (_ for _ in ()).throw(AssertionError('network reached'))
approval_relay.signed_request=lambda *a: (_ for _ in ()).throw(AssertionError('worker reached'))
try: leave.run('alpha',by='operator')
except (OSError,ValueError): print('refused')
else: raise AssertionError('unsafe token accepted')
"""
    result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=3)
    assert result.returncode==0 and result.stdout.strip()=='refused' and result.stderr==''
    assert outside.read_text()==json.dumps({'user_id':'synthetic'}) and p.exists()
    assert leave.read('alpha')['phase']=='stopped'


def test_token_identity_and_json_are_from_same_pinned_read(env,monkeypatch):
    cfg=accounts.load_account('alpha');raw=Path(cfg['token']).read_bytes()
    monkeypatch.setattr(accounts,'load_token',lambda *a:pytest.fail('unsafe token reader reached'))
    value,identity=leave._token(cfg)
    import hashlib
    assert value==json.loads(raw) and identity['sha256']==hashlib.sha256(raw).hexdigest()
    assert identity['mode']==0o600
