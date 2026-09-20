from tests.coordination_snapshot import account_coordination
import json
from pathlib import Path
import pytest
from thth import accounts, cli, collect, engagements, jst, operations_handoff, runs, sent, unanswered
from tests.conftest import write_queue_file

NOW=jst.parse('2026-09-09T10:00:00+09:00')
AT='2026-09-09T09:00:00+09:00'


def save(cfg, root='ROOT', extra=()):
    directory=Path(accounts.data_dirs(accounts.load_account(cfg['name']),cfg['name'])['replies'])
    directory.mkdir(parents=True,exist_ok=True)
    rows=[{'id':rid,'username':'outside','text':'PRIVATE REPLY','timestamp':AT,'post_id':root}
          for rid in ('R1','R2')]+list(extra)+[{'kind':'fetch','post_id':root,'collected_at':AT}]
    (directory/(root+'.ndjson')).write_text(''.join(json.dumps(r)+'\n' for r in rows))


def own_sent(name,pid,parent=None):
    sent.write(accounts.state_dir_for(name),post_id=pid,text='PRIVATE SENT',body_hash='hash',sent_at=AT,reply_to=parent)


@pytest.mark.parametrize('evidence',['sent','engagement','queue','replies'])
def test_reply_specific_edges_never_hide_other_reply(evidence,isolated_account_factory):
    cfg=isolated_account_factory('one',handle='owner')
    own_sent('one','ROOT')
    extra=[]
    if evidence=='sent':own_sent('one','ANSWER','R1')
    elif evidence=='engagement':
        engagements.append(accounts.load_account('one'),'one',dict(post_id='ANSWER',reply_to='R1',root_post='ROOT',author_key='a'*16,account='one',medium='threads',topic=None,kind=None,hour_band='朝',posted_at=AT,found_by='manual'))
    elif evidence=='queue':
        write_queue_file(cfg['queue_dir'],'answer.md',fm_overrides={'account':'one','status':'posted','post_id':'ANSWER','posted_at':AT,'reply_to':'R1'})
    else:extra=[{'id':'ANSWER','username':'owner','timestamp':AT,'post_id':'ROOT','replied_to':{'id':'R1'}}]
    # A different own reply to the root cannot answer R2.
    extra.append({'id':'ROOT-ANSWER','username':'owner','timestamp':AT,'post_id':'ROOT','replied_to':{'id':'ROOT'}})
    save(cfg,extra=extra)
    result=unanswered.answer('one',now=NOW)
    assert result['n_total']==1
    assert [r['reply_id'] for r in result['replies']]==['R2']
    assert result['summary']=={'n':1,'oldest_age_hours':1.0}
    assert result['replies'][0]['verified']=='ledger_only'


def test_legacy_sent_and_shared_foreign_roots_stay_unknown(isolated_account_factory):
    cfg=isolated_account_factory('one',handle='owner')
    own_sent('one','OLD')
    path=Path(sent.path_for(accounts.state_dir_for('one'),'OLD'));value=json.loads(path.read_text());value.pop('reply_to');path.write_text(json.dumps(value))
    old=path.read_bytes()
    save(cfg,'OLD');save(cfg,'FOREIGN')
    result=unanswered.answer('one',now=NOW)
    assert result['n_total']==0 and result['replies']==[]
    assert {'own_post_parent_unknown','unproven_roots_excluded'}<=set(result['cannot_say'])
    assert path.read_bytes()==old


def test_bundle_roots_and_missing_writeback(isolated_account_factory):
    cfg=isolated_account_factory('one',handle='owner')
    text=f'''---
thth: 2
account: one
status: posted
posts:
  - index: 1
    post_id: ROOT
    posted_at: {AT}
    reply_to:
  - index: 2
    post_id: ANSWER
    posted_at: {AT}
    reply_to: R1
---
## threads
body
'''
    path=Path(cfg['queue_dir'])/'bundle.md';path.write_text(text)
    save(cfg)
    assert [r['reply_id'] for r in unanswered.answer('one',now=NOW)['replies']]==['R2']
    path.write_text(text.replace('    post_id: ROOT\n','').replace('    post_id: ANSWER\n',''))
    result=unanswered.answer('one',now=NOW)
    assert result['n_total']==0 and 'bundle_publication_not_recorded' in result['cannot_say']


def test_cli_records_only_minimal_run_handoff_computation_is_pure(isolated_account_factory,capsys,monkeypatch):
    cfg=isolated_account_factory('one',handle='owner');own_sent('one','ROOT');save(cfg)
    roots=[Path(accounts.thth_root()),Path(cfg['repo_dir'])]
    def snapshot():return {str(p):p.read_bytes() for root in roots for p in root.rglob('*') if not account_coordination(p,'one') and p.is_file() and not p.name.startswith('runs-')}
    before=snapshot()
    monkeypatch.setattr(collect,'refresh_replies',lambda *a,**k:pytest.fail('implicit network'))
    pure=unanswered.answer('one',now=NOW)
    handoff=operations_handoff.answer('one',now=NOW)
    assert handoff['by_account']['one']['unanswered']==pure['summary']
    assert runs.read_runs(accounts.state_dir_for('one'))==[]
    assert cli.main(['unanswered','one','--since','7d','--json'])==0
    assert json.loads(capsys.readouterr().out)==pure
    assert snapshot()==before
    rows=runs.read_runs(accounts.state_dir_for('one'))
    assert len(rows)==1 and rows[0]['action']=='unanswered' and rows[0]['n']==2
    assert all(v not in json.dumps(rows) for v in ('PRIVATE','outside','ROOT','R1','R2'))


def test_stale_and_bad_times_are_not_silently_fresh(isolated_account_factory):
    cfg=isolated_account_factory('one',handle='owner');own_sent('one','ROOT');save(cfg)
    directory=Path(accounts.data_dirs(accounts.load_account('one'),'one')['replies']);path=directory/'ROOT.ndjson'
    data=[json.loads(line) for line in path.read_text().splitlines()]
    data[0]['timestamp']='bad';data[-1]['collected_at']='2026-09-01T10:00:00+09:00'
    path.write_text(''.join(json.dumps(r)+'\n' for r in data))
    result=unanswered.answer('one',now=NOW,since='2h')
    assert result['n_total']==1 and result['collection_stale_hours']==192
    assert {'reply_time_unknown','collection_stale_hours'}<=set(result['cannot_say'])


def test_refresh_is_explicit_and_wait_forwarded(isolated_account_factory,monkeypatch,capsys):
    isolated_account_factory('one');calls=[]
    def refresh(*a,**k):calls.append((a,k));return {'skipped':None,'failed':[],'errors':[]}
    monkeypatch.setattr(collect,'refresh_replies',refresh)
    assert cli.main(['unanswered','one','--refresh','--wait','2','--json'])==0
    assert len(calls)==1 and calls[0][1]['wait']==2
    assert json.loads(capsys.readouterr().out)['refresh']['skipped'] is None


def test_invalid_since_refuses_before_explicit_refresh(isolated_account_factory,monkeypatch,capsys):
    isolated_account_factory('one')
    monkeypatch.setattr(collect,'refresh_replies',lambda *a,**k:pytest.fail('invalid option caused refresh'))
    assert cli.main(['unanswered','one','--since','bad','--refresh','--json'])==2
    assert capsys.readouterr().out==''


def test_new_sent_records_store_parent_but_default_is_explicit_null(isolated_account_factory):
    isolated_account_factory('one')
    own_sent('one','ROOT');own_sent('one','CHILD','ROOT')
    assert sent.read(accounts.state_dir_for('one'),'ROOT')['reply_to'] is None
    assert sent.read(accounts.state_dir_for('one'),'CHILD')['reply_to']=='ROOT'
