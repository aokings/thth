import json
from pathlib import Path
import pytest
from thth import accounts, handoff_cursor, jst, operations_handoff, tool_version
from thth.report_service import ReportContext, execute_report

NOW = jst.parse('2026-09-20T07:00:00+09:00')

@pytest.fixture
def notes(tmp_path, monkeypatch):
    root = tmp_path/'app'; (root/'docs').mkdir(parents=True)
    for version in ('2.8.0', '2.9.0', '2.9.1', '2.10.0', '2.11.0'):
        (root/'docs'/f'リリースノート_{version}_2026-09-20.md').write_text('notes')
    monkeypatch.setattr(tool_version, 'NOTES_ROOT', root)
    monkeypatch.setattr(tool_version, '__version__', '2.10.0')
    monkeypatch.setattr(jst, 'now_jst', lambda: NOW)
    return root

@pytest.mark.parametrize('cursor_version', ['absent', 'legacy', '2.9.0', '2.10.0'])
def test_tool_version_cursor_states_and_read_only(notes, isolated_account_factory, cursor_version):
    cfg=isolated_account_factory('one'); name=cfg['name']
    node=operations_handoff.answer(name, now=NOW)['by_account'][name]
    path=Path(accounts.state_dir_for(name))/'handoff_cursor.json'
    if cursor_version != 'absent':
        handoff_cursor.write(name,node,'tester',NOW)
        value=json.loads(path.read_text())
        if cursor_version=='legacy': value['snapshot'].pop('tool_version')
        else:value['snapshot']['tool_version']=cursor_version
        path.write_text(json.dumps(value))
    before=path.read_bytes() if path.exists() else None
    result=operations_handoff.answer(name,now=NOW,since_last_read=True)
    tool=result['tool']; assert tool['version']=='2.10.0'
    assert result['by_account'][name]['tool']==tool
    assert tool['notes_root']==str(notes)
    assert tool['previous_version']==(cursor_version if cursor_version not in ('absent','legacy') else None)
    assert tool['changed_since_last_read'] is (True if cursor_version=='2.9.0' else False if cursor_version=='2.10.0' else None)
    assert tool['release_notes']==([] if cursor_version=='2.10.0' else
          [f'docs/リリースノート_{v}_2026-09-20.md' for v in (('2.9.1','2.10.0') if cursor_version=='2.9.0' else ('2.10.0',))])
    assert 'cursor_unreadable' not in result['by_account'][name]['cannot_say']
    if cursor_version=='2.9.0':
        assert any(c['field']=='tool_version' and c['previous']=='2.9.0' and c['current']=='2.10.0' for c in result['by_account'][name]['changes_since']['changes'])
    assert (path.read_bytes() if path.exists() else None)==before
    assert operations_handoff.answer(name,now=NOW)['tool']['previous_version'] is None
    # Authenticated HTTP and MCP operations_handoff use this same facade/source.
    payload=execute_report(ReportContext({name: cfg.get('project')}),{'operation':'operations_handoff','account':name,'since_last_read':True})
    assert payload['reports'][name]['tool']==tool

@pytest.mark.parametrize('change',[{'unknown':None},{'tool_version':[]},{'tool_version':'bad'}])
def test_legacy_tolerance_does_not_allow_other_schema_errors(notes, isolated_account_factory, change):
    cfg=isolated_account_factory('one');node=operations_handoff.answer('one',now=NOW)['by_account']['one']
    handoff_cursor.write('one',node,'tester',NOW)
    path=Path(accounts.state_dir_for('one'))/'handoff_cursor.json'; value=json.loads(path.read_text());value['snapshot'].update(change);path.write_text(json.dumps(value))
    assert handoff_cursor.read('one',NOW)==(None,'cursor_unreadable')
