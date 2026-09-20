"""Trusted parent aliases work; the credential leaf and transactions stay safe."""
import json
import os
from pathlib import Path
import pytest
from thth import accounts, admin_log, authflow, doctor, oauth
from tests.test_v211_authflow import env, opaque, url
from tests.test_v211_bluesky_stdin import env as bs_env
from tests.test_v211_x_auth import env as x_env
from tests import test_v211_auth_integration as integration
from tests import test_v211_bluesky_stdin as bs_tests
from tests import test_v211_x_auth as x_tests


def alias_token(env):
    real=env['root']/'credentials';real.mkdir()
    alias=env['root']/'host-alias';alias.symlink_to(real,target_is_directory=True)
    env['cfg']['token']=str(alias/'alpha.token')
    env['ledger'].write_text(json.dumps(env['cfg']))
    return alias,real/'alpha.token'


def test_parent_alias_snapshot_and_recorded_probe(env):
    alias,real=alias_token(env)
    value=integration.put_token(env)
    snap=authflow._token_snapshot(Path(env['cfg']['token']))
    assert json.loads(snap[0])==value and snap[1]==0o600
    doctor.record_observation('alpha',{'probes':[],'error':None})
    assert doctor.read_observation('alpha')['probe_current_credentials'] is True
    assert alias.is_symlink() and real.exists()


@pytest.mark.parametrize('kind',['symlink','hardlink','fifo','directory'])
def test_unsafe_leaf_refused_with_parent_alias(env,kind):
    _,real=alias_token(env)
    other=env['root']/'other';other.write_text(opaque());before=other.read_bytes()
    if kind=='symlink':real.symlink_to(other)
    elif kind=='hardlink':os.link(other,real)
    elif kind=='fifo':os.mkfifo(real)
    else:real.mkdir()
    with pytest.raises((authflow.FlowError,OSError)):
        authflow._token_snapshot(Path(env['cfg']['token']))
    assert other.read_bytes()==before


@pytest.mark.parametrize('entry',['auth','threads_stdin','mastodon_stdin'])
@pytest.mark.parametrize('fault',['zero','partial','fsync','observation'])
def test_alias_save_event_and_observation_faults(env,monkeypatch,entry,fault):
    alias_token(env)
    integration.test_event_failure_never_updates_auth_observation_and_observation_failure_is_success(env,monkeypatch,entry,fault)


@pytest.mark.parametrize('fault',['zero','partial','fsync','rollback'])
def test_bluesky_alias_save_faults(bs_env,monkeypatch,fault):
    alias_token(bs_env)
    bs_tests.test_event_failure_rollback_or_uncertain_retention(bs_env,monkeypatch,fault)


@pytest.mark.parametrize('fault',['zero','partial','fsync','rollback'])
def test_x_refresh_alias_save_faults(x_env,monkeypatch,fault):
    alias_token(x_env)
    x_tests.test_refresh_log_failure_retention_and_rollback_boundary(x_env,monkeypatch,fault)


@pytest.mark.parametrize('media',['threads','mastodon'])
def test_alias_token_generation_change_refused(env,monkeypatch,media):
    alias_token(env)
    integration.test_manual_token_wait_cannot_overwrite_concurrent_change(env,monkeypatch,media,'token')


@pytest.mark.parametrize('entry',['auth','manual'])
def test_rollback_uses_same_canonical_target_after_host_alias_changes(env,monkeypatch,entry):
    alias,real=alias_token(env)
    old=integration.put_token(env);before=real.read_bytes()
    otherdir=env['root']/'other-credentials';otherdir.mkdir()
    other=otherdir/'alpha.token';other.write_text(opaque());untouched=other.read_bytes()
    def fail_after_switch(fd,data):
        alias.unlink();alias.symlink_to(otherdir,target_is_directory=True)
        raise admin_log.AdminLogError('fault')
    monkeypatch.setattr(admin_log,'_emit',fail_after_switch)
    if entry=='auth':
        rc=oauth.run_auth('alpha',by='operator',input_func=lambda:url(authflow._read_session('alpha'),env['code']),human_output=lambda _:None,log=lambda _:None)
    else:
        class Adapter:
            def whoami(self):return {'user_id':'123','username':'demo'}
        monkeypatch.setattr('thth.adapters.make_adapter',lambda *a,**k:Adapter())
        rc=oauth.run_token_set('alpha',stdin=True,force=True,by='operator',input_func=lambda:env['access'],log=lambda _:None)
    assert rc==2 and real.read_bytes()==before and other.read_bytes()==untouched
