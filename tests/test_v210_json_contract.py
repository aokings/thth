"""Every --json CLI entry point: real error/read handlers, isolated empty ledger."""
import argparse
import json
import socket
from pathlib import Path
import pytest
from thth import cli, topic_cli


def json_parsers():
    found = {}
    def visit(parser, path):
        if any('--json' in a.option_strings for a in parser._actions):
            found[' '.join(path)] = parser
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, child in action.choices.items():visit(child, [*path, name])
    visit(cli.build_parser(), [])
    visit(topic_cli.build_parser(), ['topics'])
    found["account add"] = found["account"]
    found["account migrate"] = found["account"]
    return found

# This list is independent of the parser traversal: a newly added JSON command
# must acquire a table row, rather than quietly escaping the contract.
COMMANDS = '''lint|preview|approve|account|revoke|posts|replies|measured|threads|study-report|analytics-report|after|topics|forms|queue|schedule|throw|handoff-report|board|pull|maintain|doctor|app show|admin inventory|admin account|admin log|admin tokens|admin timers|admin release|admin diff|ask before-you-post|mentions|profile|thread|where|who|retract|location|topics suggest|topics observe|topics record-decision|topics decision|topics observation|topics retract|topics unretract|topics profile|topics adopt-reason|topics adoptions|topics record-vocabulary|topics vocabulary|topics record-review|topics review|topics record-form-spec|topics form-spec|topics form-check|topics improvements|topics impact|topics record-hypothesis|topics hypotheses'''.split('|') + ['account add', 'account migrate', 'unanswered', 'study add']


def test_json_table_covers_every_parser():
    assert set(COMMANDS) == set(json_parsers())


@pytest.mark.parametrize('command', COMMANDS)
def test_json_cli_stdout_is_one_value_or_empty_error(command, tmp_path, monkeypatch, capsys):
    root=tmp_path/'root';(root/'accounts').mkdir(parents=True)
    monkeypatch.setenv('THTH_ROOT',str(root));monkeypatch.setenv('THTH_ACCOUNTS_DIR',str(root/'accounts'))
    monkeypatch.setenv('HOME',str(root));monkeypatch.setenv('XDG_CONFIG_HOME',str(root/'config'))
    monkeypatch.setattr(socket,'socket',lambda *a,**k:pytest.fail('unexpected network'))
    parser=json_parsers()[command];argv=command.split()
    for action in parser._actions:
        if isinstance(action,argparse._SubParsersAction):continue
        if not action.option_strings and action.required:
            argv.append(str(tmp_path/'missing.md') if action.dest in ('file','path') else
                        str(next(iter(action.choices))) if action.choices else 'missing-json-account')
        elif action.option_strings and action.required:
            flag=next((s for s in action.option_strings if s.startswith('--')),action.option_strings[0])
            argv.append(flag)
            if action.nargs!=0:argv.append(str(next(iter(action.choices))) if action.choices else 'fixture')
    if command=='admin diff':argv.append('--since-last-read')
    argv.append('--json')
    try:rc=cli.main(argv)
    except SystemExit as exc:rc=exc.code
    output=capsys.readouterr()
    if output.out.strip():
        json.loads(output.out)
    else:
        assert rc!=0 and output.err, (command,rc,output)


@pytest.mark.parametrize('command', ['throw', 'maintain', 'account', 'handoff-report'])
def test_json_real_account_read_and_rehearsal(command, isolated_account_factory, capsys, monkeypatch):
    isolated_account_factory('json-one')
    monkeypatch.setattr(socket, 'socket', lambda *a, **k: pytest.fail('unexpected network'))
    args=[command,'json-one','--json']
    if command=='maintain':args=[command,'--account','json-one','--check','--json']
    cli.main(args)
    output=capsys.readouterr()
    value=json.loads(output.out)
    assert isinstance(value, dict)
    assert value

# Successful report transports: nonempty evidence, not just error/empty replies.
# The existing draft-file MCP tools intentionally expose user-supplied paths and
# are separate from this server-path-free report contract.
from tests.test_admin_report import fixture as report_seed

# Clarification 5: user reports hide server paths; admin reports preserve
# their established location fields. Every scope still protects secret values.
REPORT_CASES = [('http','analytics_report'),('http','operations_handoff'),
                ('mcp','analytics_report'),('mcp','operations_handoff'),('mcp','study_report')]



def _all_strings(value):
    if isinstance(value,str):yield value
    elif isinstance(value,dict):
        for key,child in value.items():
            yield str(key)
            yield from _all_strings(child)
    elif isinstance(value,list):
        for child in value:yield from _all_strings(child)


def _assert_no_server_paths(value):
    import re
    assert not [s for s in _all_strings(value)
                if re.match(r'^/(?:Users|private|tmp|srv|Volumes)',s)]


@pytest.mark.parametrize('prefix',['/Users','/private','/tmp','/srv','/Volumes'])
def test_report_path_scanner_covers_each_deployment_root(prefix):
    with pytest.raises(AssertionError):_assert_no_server_paths({'nested':[{'path':prefix+'/FAKE_INTERNAL'}]})


USER_REPORT_TARGETS = [(via,operation,'account') for via,operation in REPORT_CASES] + [
    (via,operation,'project') for via in ('http','mcp')
    for operation in ('analytics_report','operations_handoff')]
ADMIN_REPORT_CASES = [('http', name) for name in (
    'admin_inventory','admin_account','admin_log','admin_tokens','admin_timers',
    'admin_release','admin_diff')] + [('mcp', name) for name in (
    'admin_inventory','admin_account','admin_log','admin_tokens','admin_release','admin_diff')]
REPORT_TARGET_CASES = [(via,operation,target,scope)
    for via,operation,target in USER_REPORT_TARGETS for scope in ('user','admin')] + [
    (via,operation,'account','admin') for via,operation in ADMIN_REPORT_CASES]


@pytest.mark.parametrize('via,operation,target,scope', REPORT_TARGET_CASES)
def test_successful_http_mcp_reports_have_evidence_without_server_paths(via,operation,target,scope,report_seed,tmp_path,monkeypatch):
    import datetime, hashlib, http.client, subprocess, threading
    from thth import accounts, admin_report, handoff_cursor, jst, operations_handoff, report_http, sent
    from tests.test_analytics_comparison import seed
    from tests.test_mcp import _load_server_module
    root,_,cfg=report_seed
    # All runtime resources remain in a private temporary root for preflight.
    now=datetime.datetime.now(datetime.timezone.utc)
    monkeypatch.setattr(jst,'now_jst',lambda:now)
    repo=Path(cfg['repo_dir']);subprocess.run(['git','init','-q',str(repo)],check=True)
    name='test-threads';account={'name':name,'repo_dir':str(repo)}
    seed(account,'BEFORE',now-datetime.timedelta(days=10),value=2)
    seed(account,'AFTER',now-datetime.timedelta(days=3),value=9)
    state=Path(accounts.state_dir_for(name))
    sent.write(str(state),post_id='OWNED',text='FAKE_PRIVATE_BODY',body_hash='hash',sent_at=jst.iso(now-datetime.timedelta(days=1)),reply_to=None)
    node=operations_handoff.answer(name,now=now)['by_account'][name]
    handoff_cursor.write(name,node,'tester',now)
    admin_report.answer('diff',since_last_read=True,mark_read=True,by='tester',now=now)
    # An actual changed field makes admin diff a nonempty success response.
    ledger=root/'accounts'/f'{name}.json';raw=json.loads(ledger.read_text());raw['production']=True;ledger.write_text(json.dumps(raw))
    timers=root/'state/_admin/timers.json'
    timers.write_text(json.dumps({'by_account':{name:{'observed_at':jst.iso(now),'units':[{'unit':'thth@test-threads.timer','active':'active'}]}}}));timers.chmod(0o600)
    policy=repo/'study.json';policy.write_text(json.dumps(dict(schema_version=1,id='study',account=name,hypothesis='h',change='c',decision={'status':'adopted','by':'tester','at':jst.iso(now-datetime.timedelta(days=7))},baseline_post_ids=['BEFORE'],changed_post_ids=['AFTER'])))
    token='a'*43
    credential=tmp_path/'transport-credential.json'
    credential.write_text(json.dumps(dict(schema_version=1,root=str(root),credentials=[dict(sha256=hashlib.sha256(token.encode()).hexdigest(),expires_at=(now+datetime.timedelta(hours=1)).isoformat(),revoked=False,scope=scope,accounts={name:'test'} if scope=='user' else {})])))
    credential.chmod(0o600)
    request={'operation':operation}
    if operation in ('analytics_report','operations_handoff','admin_account'):request['account']=name
    if target=='project':request.pop('account');request['project']='test'
    if operation=='analytics_report':request.update(compare_previous=True,min_n=1)
    if operation in ('operations_handoff','admin_diff'):request['since_last_read']=True
    if via=='http':
        with report_http.PrivateReportServer(credential,0) as server:
            worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            try:
                connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10)
                connection.request('POST','/report',json.dumps(request),{'Authorization':'Bearer '+token,'Content-Type':'application/json'})
                response=connection.getresponse();payload=json.loads(response.read());connection.close()
                assert response.status==200, payload
            finally:server.shutdown();worker.join(5)
    else:
        server=_load_server_module();monkeypatch.setattr(server,'THTH_BIN',None)
        monkeypatch.setenv('THTH_REPORT_CREDENTIALS',str(credential));monkeypatch.setenv('THTH_REPORT_TOKEN',token)
        args={key:value for key,value in request.items() if key!='operation'}
        if operation=='study_report':args={'file':str(policy),'min_n':1}
        response=server.call_tool('thth_'+operation if operation.startswith('admin_') else operation,args)
        assert not response.get('isError'),response
        payload=json.loads(response['content'][0]['text'])
    if not operation.startswith('admin_'):_assert_no_server_paths(payload)
    # Tool metadata obeys the same boundary even when embedded in admin cursors.
    def check_tools(value):
        if isinstance(value,dict):
            if isinstance(value.get('tool'),dict):
                _assert_no_server_paths(value['tool'])
                assert 'notes_root' not in value['tool']
            for child in value.values():check_tools(child)
        elif isinstance(value,list):
            for child in value:check_tools(child)
    check_tools(payload)
    dumped=json.dumps(payload)
    assert all(secret not in dumped for secret in ('FAKE_PRIVATE_TOKEN_12345','FAKE_APP_SECRET_98765','fake-private@example.test','FAKE_PRIVATE_BODY'))
    core=payload['reports'][name] if via=='http' and not operation.startswith('admin_') else payload
    if operation=='analytics_report':assert core['by_account'][name]['posts']['current']['metrics']['views']['median']==9
    elif operation=='operations_handoff':
        assert core['by_account'][name]['sent_count']==1
        assert core['tool']['version']=='2.10.0' and core['tool']['notes_root_local_hint']=='~/Developer/thth'
        assert 'notes_root' not in core['tool'] and 'notes_root' not in core['by_account'][name]['tool']
    elif operation in ('admin_inventory','admin_account'):
        row=core['by_account'][name]
        assert row['production'] is True and row['repo']['dir']==str(repo)
        if operation=='admin_account':
            assert row['ledger_fields']['env']['path']==cfg['env']
            assert row['ledger_fields']['token']['path']==cfg['token']
    elif operation=='admin_log':
        assert core['events'] and core['events'][0]['event']=='account_added'
        assert str(repo) in set(_all_strings(core['events'][0]['diff']))
    elif operation=='admin_tokens':assert core['tokens'][0]['present'] is True
    elif operation=='admin_timers':assert core['by_account'][name]['units'][0]['active']=='active'
    elif operation=='admin_release':
        assert core['release']['version']=='2.10.0'
        assert core['release']['app_env']['path']==str(root/'.config/thth/app.env')
    elif operation=='admin_diff':
        assert any(c['field']=='production' for c in core['changes'])
        assert core['snapshot']['inventory']['by_account'][name]['repo']['dir']==str(repo)
    elif operation=='study_report':assert core['observations']['changed']['metrics']['views']['median']==9
