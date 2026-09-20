"""Invalid user input is not a stopped account, nor an opaque endpoint failure."""
import json
import pytest
from thth import accounts,cli,doctor,httpsafe,stop_observation


@pytest.mark.parametrize('name',['../x','../not-present','x/y','', '.', '..'])
@pytest.mark.parametrize('as_json',[False,True])
def test_invalid_doctor_name_before_all_filesystem_and_provider_work(monkeypatch,name,as_json):
    def forbidden(*a,**kw):pytest.fail('invalid name reached filesystem/provider boundary')
    monkeypatch.setattr(stop_observation,'directories',forbidden)
    monkeypatch.setattr(stop_observation.leave_gate,'require_active',forbidden)
    monkeypatch.setattr(accounts,'load_account',forbidden)
    monkeypatch.setattr(doctor,'_diagnose',forbidden)
    logs=[];assert doctor.run_doctor(name,as_json=as_json,log=logs.append)==2
    assert '使えない字' in logs[0]
    assert '../' not in logs[0] and 'x/y' not in logs[0]
    if as_json:
        result=json.loads(logs[0]);assert result['error']=='invalid_account_name'
        assert result['probes']==[] and result['directory_checks']==[] and result['account'] is None
    direct=doctor.diagnose(name);assert direct['error']=='invalid_account_name'


@pytest.mark.parametrize('as_json',[False,True])
def test_cli_invalid_doctor_keeps_specific_reason_without_creating_state(tmp_path,monkeypatch,capsys,as_json):
    root=tmp_path/'absent';monkeypatch.setenv('THTH_ROOT',str(root))
    assert cli.main(['doctor','../x']+(['--json'] if as_json else []))==2
    out=capsys.readouterr();assert '使えない字' in out.out and 'account_stopped' not in out.out
    assert not root.exists()


@pytest.mark.parametrize('value,prefix,word',[
    ('http://not-a-provider.invalid/PRIVATE','endpoint_http_forbidden','平文'),
    ('http://127.0.0.1/PRIVATE','endpoint_http_forbidden','平文'),
    ('not-a-provider.invalid/PRIVATE','endpoint_scheme_invalid','scheme'),
    ('file:///PRIVATE','endpoint_scheme_invalid','scheme'),
])
def test_endpoint_diagnostics_are_actionable_static_and_do_not_echo_input(monkeypatch,value,prefix,word):
    monkeypatch.delenv('THTH_TEST_ALLOW_HTTP',raising=False)
    with pytest.raises(httpsafe.EndpointRejected) as caught:httpsafe.validated_url(value,base=True)
    text=str(caught.value);assert text.startswith(prefix+':') and word in text and 'https' in text
    assert 'PRIVATE' not in text and value not in text
