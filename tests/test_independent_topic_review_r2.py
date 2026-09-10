import json
from pathlib import Path
from tests.test_independent_topic_review import setup, evaluate, cli

def test_profile_content_is_reverified(tmp_path, isolated_account, thth_root):
    q,ap,a,oid,c,p=setup(tmp_path,isolated_account,profile_status='provisional')
    _,first=evaluate(tmp_path,q,ap,p)
    assert first['status']=='provisional'
    path=Path(thth_root)/'state/topic_advice/profiles'/f"{isolated_account['name']}.json"
    row=json.loads(path.read_text());row['status']='confirmed';row['editorial_scope']='全く異なる編集方針';row['basis']=[]
    path.write_text(json.dumps(row,ensure_ascii=False))
    rc,saved=cli(tmp_path,['record-decision'],first)
    print('PROFILE',rc,saved.get('status'),saved.get('stored'),saved.get('context_id')==c['context_id'])
    assert rc!=0 or not saved.get('stored'), 'Unverified profile promoted same context to recommended'

def test_override_profile_body_delivered(tmp_path, isolated_account):
    q,ap,a,oid,c,p=setup(tmp_path,isolated_account)
    profile=dict(c['evidence']['profile']);profile['editorial_scope']='OVERRIDE_READER_POLICY'
    pp=tmp_path/'profile.json';pp.write_text(json.dumps(profile,ensure_ascii=False))
    rc,out=cli(tmp_path,['suggest',q,'--article',str(ap),'--profile',str(pp)])
    print('OVERRIDE',rc,out['context_id']==c['context_id'],out['evidence']['profile'])
    assert 'OVERRIDE_READER_POLICY' in json.dumps(out,ensure_ascii=False)

def test_own_legacy_judgment_not_hidden(tmp_path,isolated_account):
    q,ap,a,oid,c,p=setup(tmp_path,isolated_account)
    name=isolated_account['name']
    rc,_=cli(tmp_path,[name,'--note','お茶','--verdict','mismatch','--reason','OWN_ACCOUNT_REASON','--by','review','--json'])
    assert rc==0
    rc,_=cli(tmp_path,['--note','お茶','--verdict','alive','--reason','GLOBAL_LATEST','--by','review','--json'])
    assert rc==0
    _,out=cli(tmp_path,['suggest',q,'--article',str(ap)])
    row=next(r for r in out['evidence']['legacy_notes'] if r['topic']=='お茶')
    print('LEGACY',row)
    assert 'OWN_ACCOUNT_REASON' in json.dumps(row,ensure_ascii=False), 'Own judgment missing after newer accountless observation'

def test_large_valid_article_roundtrip(tmp_path,isolated_account):
    q,ap,a,oid,c,p=setup(tmp_path,isolated_account)
    a['content_text'] += 'a'*700000
    ap.write_text(json.dumps(a,ensure_ascii=False))
    assert ap.stat().st_size<1024*1024
    _,c=cli(tmp_path,['suggest',q,'--article',str(ap)])
    p['context_id']=c['context_id']
    rc,result=evaluate(tmp_path,q,ap,p)
    assert result['status']=='recommended'
    rc,saved=cli(tmp_path,['record-decision'],result)
    print('LARGE',rc,len(json.dumps(result,ensure_ascii=False).encode()),saved.get('error'))
    assert rc==0

def test_control_legitimate_profile_update_invalidates(tmp_path,isolated_account):
    q,ap,a,oid,c,p=setup(tmp_path,isolated_account)
    _,first=evaluate(tmp_path,q,ap,p)
    profile=dict(c['evidence']['profile']);profile['editorial_scope']='正規の方針更新'
    rc,_=cli(tmp_path,['profile',isolated_account['name']],profile)
    assert rc==0
    rc,out=cli(tmp_path,['record-decision'],first)
    assert rc==1 and out['error']['code']=='stale_context'

def test_control_own_legacy_row_without_newer_row(tmp_path,isolated_account):
    q,ap,a,oid,c,p=setup(tmp_path,isolated_account)
    rc,_=cli(tmp_path,[isolated_account['name'],'--note','お茶','--verdict','mismatch','--reason','OWN_ACCOUNT_REASON','--by','review','--json'])
    assert rc==0
    _,out=cli(tmp_path,['suggest',q,'--article',str(ap)])
    row=next(r for r in out['evidence']['legacy_notes'] if r['topic']=='お茶')
    # 形の変更にあわせた（上の注記）。見ているものは同じ。
    assert row['own_judgment']['fit']=='unsuitable'
    assert row['own_judgment']['reason']=='OWN_ACCOUNT_REASON'
    assert row['own_judgment']['account']==isolated_account['name']
