import contextlib
import hashlib
import json
import pytest
from thth import budget_x as b,report_http,report_service,admin_log
from tests.test_v212_server_writes import env
from tests.test_mcp import _load_server_module


def manager(env,monkeypatch):
    item=env['value']['credentials'][0];item.update(scope='admin',writes=False,accounts={})
    env['path'].write_text(json.dumps(env['value']))
    monkeypatch.setenv('THTH_REPORT_CREDENTIALS',str(env['path']));monkeypatch.setenv('THTH_REPORT_TOKEN',env['bearer'])
    return report_http.load_credentials(env['path'])[1][0][3]


def test_only_admin_mcp_setter_and_existing_reads_are_listed(env,monkeypatch):
    context=manager(env,monkeypatch);server=_load_server_module();names={x['name'] for x in server.server_tools(context)}
    assert {'analytics_report','operations_handoff','study_report','thth_admin_budget_set'}<=names
    for name in ('draft_put','approval_request','send_request','retract_request'):
        assert 'thth_'+name not in names and server.call_tool('thth_'+name,{'account':'alpha'}).get('isError')
    result=server.call_tool('thth_admin_budget_set',{'monthly':'0.02','by':'operator'})
    assert not result.get('isError') and json.loads(result['content'][0]['text'])['cap_usd']=='0.02'
    assert admin_log.read(event='budget_set')[0][0]['via']=='mcp'
    with pytest.raises(report_service.ReportServiceError):report_service.execute_report(context,{'operation':'admin_budget_set','monthly':'1','by':'operator'})


@pytest.mark.parametrize('kind',['user','revoked','expired','bad_token','missing_by','override'])
def test_mcp_rejects_unauthorized_or_invalid_before_budget_write(env,monkeypatch,kind):
    manager(env,monkeypatch);server=_load_server_module();args={'monthly':'1','by':'operator'}
    item=env['value']['credentials'][0]
    if kind=='user':item.update(scope='user',accounts={'alpha':'alpha'})
    elif kind=='revoked':item['revoked']=True
    elif kind=='expired':item['expires_at']='2000-01-01T00:00:00Z'
    elif kind=='bad_token':monkeypatch.setenv('THTH_REPORT_TOKEN','not-the-fixture-token')
    elif kind=='missing_by':args.pop('by')
    else:args['operation']='admin_inventory'
    env['path'].write_text(json.dumps(env['value']))
    assert server.call_tool('thth_admin_budget_set',args).get('isError')
    assert not (b.folder()/'budget_x.json').exists()


@pytest.mark.parametrize('change',['revoked','root','scope'])
def test_credential_rechecked_after_budget_lock_before_save(env,monkeypatch,change):
    context=manager(env,monkeypatch);original=b.locked
    @contextlib.contextmanager
    def locked():
        with original() as fd:
            if change=='root':env['value']['root']=str(env['root'].parent)
            else:env['value']['credentials'][0][change]=True if change=='revoked' else 'user'
            env['path'].write_text(json.dumps(env['value']))
            yield fd
    monkeypatch.setattr(b,'locked',locked)
    with pytest.raises((report_service.ReportServiceError,ValueError)):
        report_service.execute_admin_write(context,{'operation':'admin_budget_set','monthly':'1','by':'operator'})
    assert not (b.folder()/'budget_x.json').exists() and admin_log.read(event='budget_set')[0]==[]
