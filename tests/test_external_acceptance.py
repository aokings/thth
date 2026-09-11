"""Independent counterexamples; external API blocked by sitecustomize."""
import copy, json, hashlib
from pathlib import Path
from thth import topic_models as m, topic_store as store
from tests.test_review_cli import _thth, _json, _register_vocabulary, _review, _finding, VOCABULARY
from tests.test_form_spec_cli import _register_spec, SPEC, FORM
from tests.test_thread_publish import thread_account, FakeAdapter, publish


def record(v, sha='a'*64, **extra):
    r=_review(v,draft_sha256=sha,form=FORM,**extra)
    p=_thth(['topics','record-review','--json-stdin','--by','independent'],r)
    assert p.returncode == 0,p.stdout+p.stderr
    return _json(p)['review_id']


def test_publishing_does_not_depend_on_editorial_profile(thread_account):
    p=m.build_profile(dict(account=thread_account['account']['name'],language='ja',primary_goal='article_visits',editorial_scope='x',intended_interests=['x'],avoid_misrepresentation=[],status='confirmed',confirmed_by='review',basis=['test'],avoid_forms=['困り事→理由→行動']))
    store.set_profile(p)
    api=FakeAdapter()
    result=publish(thread_account,api)
    print('PROFILE_BLOCK',result,len(api.calls))
    assert len(api.calls)==3,'editorial profile changed publication eligibility'


def test_requested_quote_check_is_not_silently_omitted(isolated_account):
    sid=_register_spec(dict(SPEC,machine_checks=['quotes_in_article']))
    p=_thth(['topics','form-check','--form-spec',sid,'--json-stdin'],{'segments':[{'index':1,'roles':['対象と結論の範囲']}],'article_quotes':['NOT IN ANY ARTICLE']})
    out=_json(p);print('QUOTE_CHECK',out)
    checks=out.get('machine_checks',[])
    assert p.returncode!=0 or any(c['check']=='quotes_in_article' and c['result']!='no_problem' for c in checks)


def test_non_reference_does_not_pass_reference_existence(isolated_account):
    sid=_register_spec()
    p=_thth(['topics','form-check','--form-spec',sid,'--json-stdin'],{'segments':[{'index':1,'roles':[r['role_id'] for r in SPEC['roles']]}],'evidence':{'共通の比較軸':[123]}})
    out=_json(p);print('BAD_REFERENCE',out)
    assert p.returncode!=0 or any(c['check']=='evidence_refs_exist' and c['result']!='no_problem' for c in out.get('machine_checks',[]))


def test_fabricated_cases_cannot_promote_spec(isolated_account):
    cases=[dict(kind=k,draft_sha256=c*64,account='nigamilab-threads',review_ids=['sha256:'+'f'*64],note='no actual case') for k,c in [('positive','a'),('positive','b'),('counter','c')]]
    p=_thth(['topics','record-form-spec','--json-stdin','--by','author'],dict(SPEC,state='accepted',scope='nigamilab-threads and every other account',cases=cases))
    print('FABRICATED_PROMOTION',p.stdout)
    assert p.returncode!=0,'accepted with nonexistent review evidence and no independent decision'


def test_revision_chain_is_one_case(isolated_account):
    v=_register_vocabulary();sid=_register_spec()
    first=record(v, disposition='fixed',revised_draft_sha256='b'*64)
    record(v,'b'*64,recheck_of=first)
    out=_json(_thth(['topics','improvements','--form-spec',sid]));print('REVISION_CASES',out)
    assert not out['candidates'],'one manuscript revision chain counted as independent cases'


def test_meaning_versions_do_not_merge(isolated_account):
    v1=_register_vocabulary();sid=_register_spec()
    other=copy.deepcopy(VOCABULARY)
    other['entries'][0]['definition']='A different incompatible meaning'
    other['entries'][0]['meaning_version']=2
    v2=_register_vocabulary(other)
    record(v1);record(v2,'b'*64)
    out=_json(_thth(['topics','improvements','--form-spec',sid]));print('MIXED_MEANINGS',out)
    assert not out['candidates'],'same reason_id with different meanings merged'


def test_missing_vocabulary_is_not_candidate_evidence(isolated_account):
    v=_register_vocabulary();sid=_register_spec()
    record(v);record(v,'b'*64)
    (Path(store.root())/'vocabularies'/ (v[7:]+'.json')).unlink()
    out=_json(_thth(['topics','improvements','--form-spec',sid]));print('UNKNOWN_VOCAB',out)
    assert not out['candidates'],'unreadable meaning used as evidence'


def test_draft_change_reports_unreviewed_history(isolated_account,tmp_path):
    v=_register_vocabulary();f=tmp_path/'draft.md';f.write_text('BODY A')
    record(v,hashlib.sha256(f.read_bytes()).hexdigest(),disposition='deferred',disposition_reason='not checked yet')
    f.write_text('BODY B')
    out=_json(_thth(['topics','review','nigamilab-threads','--draft',str(f)]));print('LOST_HISTORY',out)
    assert out['count'] or out['warnings'] or out.get('stale_reviews'),'changed manuscript returned clean empty list'


def test_cross_account_supersedes_rejected(isolated_account):
    v=_register_vocabulary();first=record(v)
    p=_thth(['topics','record-review','--json-stdin','--by','independent'],_review(v,account='other-account',draft_sha256='b'*64,supersedes=first,disposition='dismissed',disposition_reason='unrelated'))
    print('CROSS_ACCOUNT',p.stdout)
    assert p.returncode!=0,'unrelated account may supersede a review'


def test_corrupt_profile_must_not_relax_effective_publication_rule(thread_account):
    account=thread_account['account']['name']
    p=m.build_profile(dict(account=account,language='ja',primary_goal='article_visits',editorial_scope='x',intended_interests=['x'],avoid_misrepresentation=[],status='confirmed',confirmed_by='review',basis=['test'],avoid_forms=['困り事→理由→行動']))
    store.set_profile(p)
    api=FakeAdapter()
    publish(thread_account,api)
    before_count=len(api.calls)
    Path(store.profile_path(account)).write_text('{broken')
    result=publish(thread_account,api)
    print('CORRUPT_PROFILE_PUBLICATION',result,len(api.calls))
    assert len(api.calls)==before_count,'unreadable editorial profile adds publication eligibility to unchanged manuscript'
