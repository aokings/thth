import hashlib
import json
from datetime import datetime,timedelta,timezone
import pytest
from thth import accounts, report_http, admin_report
from thth.report_service import ReportContext,ReportServiceError,execute_report
from tests.test_mcp import _load_server_module

@pytest.fixture
def credentials(tmp_path,monkeypatch):
    root=tmp_path/'tenant';root.mkdir(mode=0o700);(root/'accounts').mkdir()
    monkeypatch.setenv('THTH_ROOT',str(root));monkeypatch.setenv('THTH_ACCOUNTS_DIR',str(root/'accounts'))
    for name in ('first','second'):
        cfg={k:None for k in accounts.REQUIRED_FIELDS}
        cfg.update(account=name,project=name,media='threads',handle=name,repo_dir=str(root/'repos/_none'),
                   token=str(root/'secret'/name),env=str(root/'secret'/('env-'+name)),queue_dir='docs/sns/queue',replies_dir='data/sns/replies')
        (root/'accounts'/(name+'.json')).write_text(json.dumps(cfg))
    token='a'*43
    value=dict(schema_version=1,root=str(root),credentials=[dict(sha256=hashlib.sha256(token.encode()).hexdigest(),
               expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),revoked=False,accounts={},scope='admin')])
    path=tmp_path/'credential.json';path.write_text(json.dumps(value));path.chmod(0o600)
    return path,token,value

def test_user_admin_negative_contract():
    user=ReportContext({'first':'first'})
    with pytest.raises(ReportServiceError,match='unsupported_operation'):execute_report(user,dict(operation='admin_inventory'))
    admin=ReportContext({'first':'first'},scope='admin')
    for operation in ('admin_inventory','admin_diff','admin_account'):
        with pytest.raises(ReportServiceError,match='invalid_request'):execute_report(admin,dict(operation=operation,mark_read=True))

def test_admin_all_registry_and_mcp_auth_rechecked(credentials,monkeypatch):
    path,token,value=credentials
    root,creds=report_http.load_credentials(path)
    assert set(creds[0][3].allowed_accounts)=={'first','second'}
    server=_load_server_module()
    assert server.admin_context() is None
    assert server.call_tool('thth_admin_inventory',{})['content'][0]['text']=='unauthorized'
    monkeypatch.setenv('THTH_REPORT_CREDENTIALS',str(path));monkeypatch.setenv('THTH_REPORT_TOKEN',token)
    assert server.admin_context().scope=='admin'
    assert {tool['name'] for tool in server.ADMIN_TOOLS}=={
        'thth_admin_inventory','thth_admin_account','thth_admin_diff','thth_admin_log',
        'thth_admin_tokens','thth_admin_release','thth_admin_budget_set'}
    result=server.call_tool('thth_admin_inventory',{})
    assert set(json.loads(result['content'][0]['text'])['by_account'])=={'first','second'}
    assert server.call_tool('thth_admin_diff',{'mark_read':True})['isError']
    value['credentials'][0]['revoked']=True;path.write_text(json.dumps(value))
    assert server.admin_context() is None
    assert server.call_tool('thth_admin_inventory',{})['content'][0]['text']=='unauthorized'

def test_admin_preflight_checks_unlisted_account(credentials):
    path,token,value=credentials
    root=path.parent/'tenant';cfgpath=root/'accounts/second.json';cfg=json.loads(cfgpath.read_text());cfg['token']='/outside/secret';cfgpath.write_text(json.dumps(cfg))
    with pytest.raises(ValueError):report_http.PrivateReportServer(path,0)

def test_admin_shared_state_symlink_is_rejected(credentials):
    path,token,value=credentials
    root=path.parent/'tenant';outside=path.parent/'external';outside.mkdir()
    (root/'state').mkdir();(root/'state/_admin').symlink_to(outside)
    (outside/'timers.json').write_text(json.dumps(dict(by_account={'first':dict(observed_at=datetime.now(timezone.utc).isoformat(),units=[dict(unit='FAKE_EXTERNAL_SECRET')])})))
    with pytest.raises(ValueError):report_http.PrivateReportServer(path,0)
    report=admin_report.answer('timers',via='http')
    assert 'FAKE_EXTERNAL_SECRET' not in json.dumps(report)
