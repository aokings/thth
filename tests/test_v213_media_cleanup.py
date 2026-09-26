import json
from types import SimpleNamespace
import pytest
from thth import admin_log, relay, cli, doctor, media_cleanup


def test_status_strict_counts_and_no_private_values(monkeypatch):
    calls=[]
    def request(*args):
        calls.append(args)
        return {'active':True,'cleanup':{'pending_count':3,'failed_count':2,'reason':'cleanup_failed'}}
    monkeypatch.setattr(relay,'signed_request',request)
    assert media_cleanup.observe('alpha')=={'pending_count':3,'failed_count':2,'reason':'cleanup_failed'}
    assert calls==[('account','alpha','status',{})]


@pytest.mark.parametrize('value',[
    None, {}, {'pending_count':0,'failed_count':0,'reason':'cleanup_failed'},
    {'pending_count':False,'failed_count':0,'reason':None},
    {'pending_count':1,'failed_count':2,'reason':'cleanup_failed'},
    {'pending_count':0,'failed_count':0,'reason':None,'subjects':['private']},
])
def test_invalid_observation_is_unknown_not_healthy(monkeypatch,value):
    monkeypatch.setattr(relay,'signed_request',lambda *a:{'cleanup':value})
    assert media_cleanup.observe('alpha')==dict(pending_count=None,failed_count=None,reason='cleanup_observation_unavailable')


def test_transport_failure_is_unknown_and_diagnostic_keeps_counts(monkeypatch):
    from thth import stop_observation
    monkeypatch.setattr(relay,'signed_request',lambda *a:(_ for _ in ()).throw(relay.RelayError('opaque-private-value')))
    monkeypatch.setattr(stop_observation,'diagnostic',lambda a:{'error':None,'directory_checks':[]})
    monkeypatch.setattr(doctor,'_diagnose',lambda a:{'account':a,'probes':[]})
    monkeypatch.setattr(media_cleanup,'configured',lambda:True)
    report=doctor.diagnose('alpha')
    assert report['media_cleanup']['reason']=='cleanup_observation_unavailable'
    assert report['media_cleanup']['pending_count'] is None
    assert 'opaque-private-value' not in json.dumps(report)


@pytest.mark.parametrize('setting',['none','url','signer','unsafe_signer'])
def test_doctor_observes_only_configured_relay(tmp_path,monkeypatch,setting):
    from thth import stop_observation
    monkeypatch.delenv('THTH_APPROVAL_BASE_URL',raising=False);monkeypatch.delenv('THTH_MEDIA_BASE_URL',raising=False)
    path=tmp_path/'apps'/'relay-signer.key';monkeypatch.setattr(relay,'key_path',lambda:path)
    if setting=='url':monkeypatch.setenv('THTH_MEDIA_BASE_URL','https://thth.me')
    if setting in ('signer','unsafe_signer'):
        path.parent.mkdir();path.write_text('synthetic presence only');path.chmod(0o600 if setting=='signer' else 0o644)
    before=set(tmp_path.rglob('*'));calls=[]
    monkeypatch.setattr(stop_observation,'diagnostic',lambda a:{'error':None,'directory_checks':[]})
    monkeypatch.setattr(doctor,'_diagnose',lambda a:{'account':a,'probes':[]})
    def observe(account):
        calls.append(account);return {'pending_count':None,'failed_count':None,'reason':'cleanup_observation_unavailable'}
    monkeypatch.setattr(media_cleanup,'observe',observe)
    result=doctor.diagnose('alpha')
    assert calls==([] if setting=='none' else ['alpha'])
    # 未設定でも key は出る（静的な「観測できていない」の形で）。送信はしない。
    assert 'media_cleanup' in result and set(tmp_path.rglob('*'))==before
    if setting=='none':assert result['media_cleanup']==media_cleanup.UNAVAILABLE


def test_cli_requires_actor_and_recovery_has_no_post_operation(monkeypatch,capsys):
    parser=cli.build_parser()
    with pytest.raises(SystemExit):parser.parse_args(['admin','media','cleanup-retry','alpha'])
    calls=[]
    monkeypatch.setattr(relay,'signed_request',lambda *a:calls.append(a) or dict(scheduled_count=1,unavailable_count=0,remaining_count=0,reason=None))
    args=parser.parse_args(['admin','media','cleanup-retry','alpha','--by','operator','--json'])
    assert args.func(args)==0
    assert calls==[('account','alpha','cleanup-retry',{})]
    assert json.loads(capsys.readouterr().out)['scheduled_count']==1
    calls.clear()
    with pytest.raises(ValueError):media_cleanup.retry('alpha',by='')
    assert calls==[]


def test_incomplete_recovery_has_nonzero_exit_and_static_reason(monkeypatch,capsys):
    monkeypatch.setattr(relay,'signed_request',lambda *a:dict(scheduled_count=0,unavailable_count=1,remaining_count=0,reason='cleanup_retry_unavailable'))
    assert media_cleanup.command(SimpleNamespace(account='alpha',by='operator',json=True))==2
    assert json.loads(capsys.readouterr().out)['reason']=='cleanup_retry_unavailable'


def test_cleanup_help_discloses_unknown_write_recovery_limit(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(['admin','media','cleanup-retry','--help'])
    assert exc.value.code==0
    text=''.join(capsys.readouterr().out.split())
    assert '結果不明の書込みは強制解除せず' in text
    assert '本口だけでは回復できない場合があります' in text
    assert '再投稿はしません' in text


@pytest.mark.parametrize('account',['../alpha','a/b',''])
def test_invalid_account_precedes_actor_and_request(monkeypatch,account):
    monkeypatch.setattr(admin_log,'actor',lambda *a:pytest.fail('actor before name'))
    monkeypatch.setattr(relay,'signed_request',lambda *a:pytest.fail('request before name'))
    with pytest.raises(ValueError,match='invalid_account'):media_cleanup.retry(account,by='operator')


def test_unconfigured_relay_still_prints_the_static_cleanup_line(tmp_path,monkeypatch,isolated_account):
    """第 5・6 段 P3: relay 未設定でも行は出す。socket は一切開かない。"""
    import socket
    monkeypatch.delenv('THTH_APPROVAL_BASE_URL',raising=False);monkeypatch.delenv('THTH_MEDIA_BASE_URL',raising=False)
    monkeypatch.setattr(relay,'key_path',lambda:tmp_path/'apps'/'relay-signer.key')
    monkeypatch.setattr(socket.socket,'connect',lambda *a,**k:(_ for _ in ()).throw(AssertionError('network reached')))
    monkeypatch.setattr(media_cleanup,'observe',lambda a:(_ for _ in ()).throw(AssertionError('observed without a relay')))
    assert media_cleanup.configured() is False
    lines=[]
    doctor.run_doctor(isolated_account['name'],log=lines.append)
    assert 'media cleanup: cleanup_observation_unavailable (pending=None, failed=None)' in lines
    payload=[]
    doctor.run_doctor(isolated_account['name'],as_json=True,log=payload.append)
    report=json.loads(payload[-1])
    assert report['media_cleanup']==dict(pending_count=None,failed_count=None,reason='cleanup_observation_unavailable')
