"""3.1.1 件 1: 退出の「共有」判定は token の中身（値・ファイル）に限る。

同じ instance の同じ利用者（user_id 同一）の別台帳は、Mastodon／X では共有と見ない。
失効は渡した token の値だけに効くので、相手の token file・台帳・停止状態に触らずに
退出が completed まで通ることを、loopback の fake 媒体で固定する。
"""
import json
import pytest
from thth import accounts,leave,leave_gate,approval_relay
from tests.test_v212_server_writes import env
from tests.test_v212_leave_providers import provider,configure


def same_user_beta(env,provider,tokenpath,media,*,same_bytes=False):
    """beta を alpha と同じ媒体・同じ instance・同じ user_id にする。token の値は既定で別。"""
    user='synthetic-user-'+media
    a=json.loads(tokenpath.read_text());a['user_id']=user;tokenpath.write_text(json.dumps(a))
    path=env['root']/'accounts/beta.json';b=json.loads(path.read_text());b['media']=media
    if media=='mastodon':b['instance']=provider['base']
    path.write_text(json.dumps(b))
    beta_token=env['root']/'secrets/beta.json'
    if same_bytes:beta_token.write_bytes(tokenpath.read_bytes())
    else:
        t=json.loads(beta_token.read_text());t['user_id']=user;beta_token.write_text(json.dumps(t))
        assert t['access_token']!=a['access_token']
    return beta_token,path


@pytest.mark.parametrize('media',['mastodon','x'])
def test_same_user_different_token_leave_completes_and_leaves_other_account_untouched(env,provider,monkeypatch,media):
    _,tokenpath=configure(env,provider,monkeypatch,media)
    beta_token,beta_ledger=same_user_beta(env,provider,tokenpath,media)
    other_token=beta_token.read_bytes();other_ledger=beta_ledger.read_bytes()
    other_access=json.loads(other_token)['access_token']
    result=leave.run('alpha',by='operator')
    assert result['phase']=='completed' and result['remote']=='confirmed'
    assert 'token_shared' not in result['preserved'] and not tokenpath.exists()
    # 媒体の失効に来たのは自分の token の値だけ。相手の値は 1 度も送られていない。
    sent=[form['token'][0] for _,form,_ in provider['calls']]
    assert provider['access'] in sent and other_access not in sent
    assert set(sent)<={provider['access'],provider['refresh']}
    # 相手の token file と台帳は byte 一致で残り、停止もしていない。
    assert beta_token.read_bytes()==other_token and beta_ledger.read_bytes()==other_ledger
    assert not leave_gate.stopped('beta') and leave.read('beta') is None
    assert accounts.load_token(accounts.load_account('beta'))['access_token']==other_access


def test_same_token_bytes_do_not_revoke_remotely(env,provider,monkeypatch):
    _,tokenpath=configure(env,provider,monkeypatch,'mastodon')
    beta_token,_=same_user_beta(env,provider,tokenpath,'mastodon',same_bytes=True)
    old=tokenpath.read_bytes()
    # 3.1.2 件 6: 失効は投げない（他方も死ぬ）。止まらずに completed まで進む
    # （完了までの形は tests/test_v312_leave_shared_token.py）。
    result=leave.run('alpha',by='operator')
    assert result['phase']=='completed' and result['remote']=='unconfirmed_shared'
    # 裁定 09-23: 値だけの共有（別ファイル）なら自分の token file は消す。相手の file は残る。
    assert provider['calls']==[] and not tokenpath.exists() and beta_token.read_bytes()==old


def test_threads_same_user_is_still_token_shared_and_preserved(env,monkeypatch):
    monkeypatch.setattr(approval_relay,'signed_request',lambda *a:{'status':'revoked'})
    paths=[env['root']/'secrets'/(n+'.json') for n in ('alpha','beta')]
    for p in paths:
        t=json.loads(p.read_text());t['user_id']='synthetic-threads-user';p.write_text(json.dumps(t))
    old=[p.read_bytes() for p in paths]
    result=leave.run('alpha',by='operator')
    assert result['phase']=='completed' and result['preserved']==['token_shared'] and result['remote']=='unconfirmed_shared'
    assert [p.read_bytes() for p in paths]==old and not leave_gate.stopped('beta')
