import copy, hashlib
from pathlib import Path
from thth import topic_store as store
from tests.test_external_acceptance import record
from tests.test_review_cli import _thth, _json, _register_vocabulary, _finding, VOCABULARY
from tests.test_form_spec_cli import _register_spec, SPEC

def test_same_version_different_meanings(isolated_account):
    v1=_register_vocabulary(); sid=_register_spec()
    other=copy.deepcopy(VOCABULARY)
    other['name']='独立した別語彙'
    other['entries'][0]['definition']='条件が多すぎるので削るべき（元と逆の意味）'
    v2=_register_vocabulary(other)
    record(v1,provenance='production');record(v2,'b'*64,provenance='production')
    out=_json(_thth(['topics','improvements','--form-spec',sid]));print('MEANINGS',out)
    assert not out['candidates']

def test_missing_form_spec_excluded(isolated_account):
    v=_register_vocabulary(); sid=_register_spec()
    other=_register_spec(dict(SPEC,meaning_version=2))
    record(v,form_spec_id=other,provenance='production');record(v,'b'*64,form_spec_id=other,provenance='production')
    (Path(store.root())/'form_specs'/(other[7:]+'.json')).unlink()
    out=_json(_thth(['topics','improvements','--form-spec',sid]));print('MISSING_SPEC',out)
    assert not out['candidates'] and out['unresolved_records']

def test_unrelated_carry_does_not_hide_findings(isolated_account,tmp_path):
    v=_register_vocabulary();f=tmp_path/'draft.md';f.write_text('A')
    old=record(v,hashlib.sha256(f.read_bytes()).hexdigest(),findings=[_finding('missing_condition'),_finding('unsupported_claim')])
    f.write_text('B')
    record(v,hashlib.sha256(f.read_bytes()).hexdigest(),carried_from=old,findings=[_finding('unsupported_claim',result='no_problem',note='裏付けのみ再検査した。条件不足は未確認')])
    out=_json(_thth(['topics','review','nigamilab-threads','--draft',str(f)]));print('CARRY',out)
    assert any(r['review_id']==old for r in out['unreviewed_history'])

def test_trial_not_used_to_reach_candidate_threshold(isolated_account):
    v=_register_vocabulary();sid=_register_spec()
    record(v,provenance='production');record(v,'b'*64,provenance='trial')
    out=_json(_thth(['topics','improvements','--form-spec',sid]));print('TRIAL',out)
    assert not out['candidates']

def test_unlinked_versions_not_confirmed_independent(isolated_account):
    v=_register_vocabulary();sid=_register_spec()
    record(v,provenance='production');record(v,'b'*64,provenance='production')
    out=_json(_thth(['topics','improvements','--form-spec',sid]));print('INDEPENDENCE',out)
    assert not any(c.get('independent_cases',0)>=2 for c in out['candidates'])
