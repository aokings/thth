"""Acceptance audit against 52f6b4c. Local fixture accounts and real CLI only."""
import copy
import datetime
import json
from pathlib import Path
import pytest
from tests.conftest import run_thth, write_queue_file
from tests.test_topic_advice import make_article, make_observation, candidate


def cli(tmp, args, data=None):
    args=list(args)
    if data is not None:
        path=tmp/'input.json'
        path.write_text(json.dumps(data,ensure_ascii=False))
        args += ['--input',str(path),'--by','independent-review']
    p=run_thth(['topics',*args])
    assert p.stdout, p.stderr
    return p.returncode,json.loads(p.stdout)


def setup(tmp, account, *, profile_status='confirmed', observation_status='ok', wrong_url=False):
    q=write_queue_file(account['queue_dir'],'audit.md',body='## threads\n\nコーヒーの精製の話。https://example.test/coffee\n',fm_overrides={'status':'draft'})
    profile={'account':account['name'],'language':'ja','primary_goal':'article_visits',
        'editorial_scope':'PROFILE_MARKER: 家庭で楽しむコーヒー','intended_interests':['精製'],
        'avoid_misrepresentation':['医療効能を主張しない'],'status':profile_status,
        'confirmed_by':'review','basis':['existing policy']}
    assert cli(tmp,['profile',account['name']],profile)[0]==0
    obs=make_observation('コーヒー',excerpt='SAMPLE_MARKER: 今日飲んだ精製方法の比較')
    obs['retrieved_at']=(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(minutes=1)).isoformat()
    obs['status']=observation_status
    if observation_status!='ok': obs['samples']=[]
    _,saved=cli(tmp,['observe'],obs)
    oid=saved['observation_id']
    article=make_article()
    if wrong_url:
        article['requested_url']='https://different.test/unrelated'
        article['final_url']='https://different.test/unrelated'
    ap=tmp/'article.json';ap.write_text(json.dumps(article,ensure_ascii=False))
    rc,context=cli(tmp,['suggest',q,'--article',str(ap)])
    assert rc==0,context
    proposal={'context_id':context['context_id'],'prompt_version':'audit',
        'intended_reader':'コーヒーを淹れる人','article_value':'精製の違い','post_angle':'味の違い',
        'candidates':[candidate('コーヒー',[oid],counterevidence='重大な反証を確認できず')],
        'selected_topic':'コーヒー','selection_reason':'本文と会話の適合'}
    return q,ap,article,oid,context,proposal


def evaluate(tmp,q,ap,proposal,extra=()):
    pp=tmp/'proposal.json';pp.write_text(json.dumps(proposal,ensure_ascii=False))
    return cli(tmp,['suggest',q,'--article',str(ap),'--proposal',str(pp),*extra])


def test_wrong_article_url_cannot_be_recommended(tmp_path,isolated_account):
    q,ap,a,oid,c,p=setup(tmp_path,isolated_account,wrong_url=True)
    rc,result=evaluate(tmp_path,q,ap,p)
    print('wrong article:',rc,result['status'],c['context']['main_article_url'],a['requested_url'])
    assert result['status']!='recommended', 'Different article accepted without checking post URL'


def test_context_delivers_evidence_to_next_session(tmp_path,isolated_account):
    q,ap,a,oid,c,p=setup(tmp_path,isolated_account)
    print('context keys:',sorted(c),'context fields:',sorted(c['context']))
    serialized=json.dumps(c,ensure_ascii=False)
    assert 'SAMPLE_MARKER' in serialized and 'PROFILE_MARKER' in serialized, 'Only IDs delivered, saved evidence and editorial policy cannot be read via context'


def test_no_article_quotes_cannot_be_recommended(tmp_path,isolated_account):
    q,ap,a,oid,c,p=setup(tmp_path,isolated_account)
    p['candidates'][0]['article_quotes']=[]
    rc,result=evaluate(tmp_path,q,ap,p)
    print('empty quotes:',rc,result['status'])
    assert rc!=0 or result['status']!='recommended', 'Empty evidence passes vacuous validation'


def test_provisional_profile_cannot_be_recommended(tmp_path,isolated_account):
    q,ap,a,oid,c,p=setup(tmp_path,isolated_account,profile_status='provisional')
    rc,result=evaluate(tmp_path,q,ap,p)
    print('provisional profile:',rc,result['status'],result['warnings'])
    assert result['status']=='provisional'


def test_record_rejects_changed_observation_under_same_id(tmp_path,isolated_account,thth_root):
    q,ap,a,oid,c,p=setup(tmp_path,isolated_account,observation_status='partial')
    rc,first=evaluate(tmp_path,q,ap,p)
    assert first['status']=='provisional'
    path=Path(thth_root)/'state/topic_advice/observations'/f'{oid[7:]}.json'
    row=json.loads(path.read_text())
    row['status']='ok';row['samples']=make_observation('コーヒー')['samples']
    path.write_text(json.dumps(row,ensure_ascii=False))
    payload={'article':a,'draft_path':q,'context_id':c['context_id'],'proposal':p}
    rc,result=cli(tmp_path,['record-decision'],payload)
    print('changed observation:',rc,result['status'],result.get('stored'),result.get('context_id')==c['context_id'])
    assert rc!=0 and not result.get('stored'), 'Same content ID now holds different evidence and gets stored as recommended'


def test_changing_main_url_invalidates_context(tmp_path,isolated_account):
    q,ap,a,oid,c,p=setup(tmp_path,isolated_account)
    rc,result=evaluate(tmp_path,q,ap,p,extra=['--article-url','https://unrelated.test/else'])
    print('changed main URL:',rc,result['status'],result['context_id']==c['context_id'])
    assert rc!=0 or result['context_id']!=c['context_id'], 'Main target URL is neither checked nor bound to context'


def test_suggest_output_can_be_recorded_directly(tmp_path,isolated_account):
    q,ap,a,oid,c,p=setup(tmp_path,isolated_account)
    rc,result=evaluate(tmp_path,q,ap,p)
    assert rc==0 and result['status']=='recommended'
    rc,saved=cli(tmp_path,['record-decision'],result)
    print('direct recording:',rc,saved.get('error'))
    assert rc==0 and saved.get('stored'), 'suggest omits article/proposal/draft_path needed by record-decision'

def test_control_manual_roundtrip_and_approval(tmp_path,isolated_account):
    from tests.conftest import approve_via_cli
    from thth import writeback
    q,ap,a,oid,c,p=setup(tmp_path,isolated_account)
    writeback.set_front_matter_fields(q,{'topic':'コーヒー'})
    _,c=cli(tmp_path,['suggest',q,'--article',str(ap)])
    p['context_id']=c['context_id']
    rc,result=evaluate(tmp_path,q,ap,p)
    assert result['status']=='recommended'
    payload={'article':a,'draft_path':q,'context_id':c['context_id'],'proposal':p}
    rc,saved=cli(tmp_path,['record-decision'],payload)
    assert rc==0 and saved['stored']
    rc,loaded=cli(tmp_path,['decision',saved['decision_id']])
    assert rc==0 and loaded['freshness']['draft_unchanged'] is True
    approved=approve_via_cli(q)
    assert approved.returncode==0, approved.stdout+approved.stderr


def test_control_bad_quote_and_unknown_reference_rejected(tmp_path,isolated_account):
    q,ap,a,oid,c,p=setup(tmp_path,isolated_account)
    p['candidates'][0]['article_quotes']=['本文に存在しない根拠']
    rc,result=evaluate(tmp_path,q,ap,p)
    assert rc==1 and result['error']['code']=='invalid_proposal'
    p['candidates'][0]['article_quotes']=['精製']
    p['candidates'][0]['observation_refs']=['sha256:'+'f'*64]
    rc,result=evaluate(tmp_path,q,ap,p)
    assert rc==1 and result['error']['code']=='invalid_proposal'


def test_control_legacy_note_named_suggest_not_dispatched(tmp_path,isolated_account):
    rc,result=cli(tmp_path,[isolated_account['name'],'--note','suggest','--verdict','unknown','--by','review','--json'])
    assert rc==0
    assert result['topic']=='suggest'
