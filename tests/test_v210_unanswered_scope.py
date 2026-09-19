import json
from pathlib import Path
import pytest
from thth import accounts, engagements, sent, unanswered
from tests.conftest import write_queue_file
from tests.test_v210_unanswered import NOW, AT, own_sent, save

@pytest.mark.parametrize('metadata',[{'account':'beta','medium':'mastodon'},{'account':'beta','medium':'threads'},{'medium':'mastodon'},{}])
def test_shared_root_collision_does_not_select_foreign_or_ambiguous_rows(metadata,isolated_account_factory):
    alpha=isolated_account_factory('alpha',handle='a',media='threads')
    isolated_account_factory('beta',handle='b',media=metadata.get('medium','threads'))
    own_sent('alpha','100');own_sent('beta','100');save(alpha,'100')
    path=Path(accounts.data_dirs(accounts.load_account('alpha'),'alpha')['replies'])/'100.ndjson'
    rows=[dict(json.loads(line),**metadata) for line in path.read_text().splitlines()]
    path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    result=unanswered.answer('alpha',now=NOW)
    assert result['n_total']==0 and result['collection_stale_hours'] is None
    assert {'foreign_reply_evidence_excluded','reply_scope_ambiguous'} & set(result['cannot_say'])


def test_explicit_target_metadata_resolves_collision(isolated_account_factory):
    alpha=isolated_account_factory('alpha',handle='a');isolated_account_factory('beta',handle='b')
    own_sent('alpha','100');own_sent('beta','100');save(alpha,'100')
    path=Path(accounts.data_dirs(accounts.load_account('alpha'),'alpha')['replies'])/'100.ndjson'
    path.write_text(''.join(json.dumps(dict(json.loads(line),account='alpha',medium='threads'))+'\n' for line in path.read_text().splitlines()))
    assert unanswered.answer('alpha',now=NOW)['n_total']==2


@pytest.mark.parametrize('evidence',['collected','engagement','draft'])
def test_foreign_self_and_unpublished_queue_are_not_answers(evidence,isolated_account_factory):
    alpha=isolated_account_factory('alpha',handle='a');isolated_account_factory('beta',media='mastodon',handle='b')
    own_sent('alpha','ROOT');extra=[]
    if evidence=='collected':extra=[dict(id='ANSWER',post_id='ROOT',username='b',replied_to={'id':'R1'},timestamp=AT)]
    elif evidence=='engagement':engagements.append(accounts.load_account('alpha'),'alpha',dict(account='alpha',medium='mastodon',post_id='ANSWER',reply_to='R1',posted_at=AT))
    else:write_queue_file(alpha['queue_dir'],'draft.md',fm_overrides=dict(account='alpha',status='draft',post_id='ANSWER',posted_at=AT,reply_to='R1'))
    save(alpha,extra=extra)
    assert unanswered.answer('alpha',now=NOW)['n_total']==2


def test_foreign_fetch_does_not_make_collection_fresh(isolated_account_factory):
    alpha=isolated_account_factory('alpha',handle='a');own_sent('alpha','ROOT')
    save(alpha,extra=[dict(kind='fetch',post_id='ROOT',account='beta',medium='mastodon',collected_at='2026-09-09T10:00:00+09:00')])
    assert unanswered.answer('alpha',now=NOW)['collection_stale_hours']==1


def test_foreign_legacy_sent_still_proves_a_possible_owner(isolated_account_factory):
    alpha=isolated_account_factory('alpha',handle='a');isolated_account_factory('beta',handle='b')
    own_sent('alpha','100');own_sent('beta','100')
    path=Path(sent.path_for(accounts.state_dir_for('beta'),'100'))
    row=json.loads(path.read_text());row.pop('reply_to');path.write_text(json.dumps(row))
    save(alpha,'100')
    result=unanswered.answer('alpha',now=NOW)
    assert result['n_total']==0 and 'reply_scope_ambiguous' in result['cannot_say']


def test_handoff_incomplete_ledger_preserves_unknown_summary(isolated_account_factory):
    from thth import operations_handoff
    cfg=isolated_account_factory('alpha',handle='a');config=accounts.load_account('alpha')
    path=Path(accounts.accounts_dir())/'alpha.json'
    value=json.loads(path.read_text());value.pop('quiet_hours');path.write_text(json.dumps(value))
    result=operations_handoff._account('alpha',config,NOW)
    assert result['unanswered']=={'n':None,'oldest_age_hours':None}
    assert 'unanswered_ledger_unavailable' in result['cannot_say']
