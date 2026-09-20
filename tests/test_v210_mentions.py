import json
import urllib.error
import urllib.parse
from pathlib import Path
import pytest
from thth import accounts, cli, engagements, runs, thread_read
from tests.coordination_snapshot import account_coordination
from thth.adapters import base, bluesky, mastodon


def test_bluesky_pages_filter_reasons_uri_not_subject_and_bad_record(monkeypatch):
    adapter=bluesky.BlueskyAdapter(identifier='reader',app_password='FAKE_PASSWORD')
    calls=[]
    def request(method,nsid,params):
        assert method=='GET' and nsid=='app.bsky.notification.listNotifications'
        calls.append(params)
        if params.get('cursor'):
            return {'notifications':[{'uri':'at://did:plc:a/app.bsky.feed.post/quote','reason':'quote','record':None,'indexedAt':'2026-09-09T00:00:00Z','author':{'did':'did:plc:a','handle':'a'}}]}
        return {'cursor':'next','notifications':[
            {'uri':f'at://did:plc:a/app.bsky.feed.post/{reason}','reason':reason,
             'reasonSubject':'at://did:plc:wrong/app.bsky.feed.post/subject',
             'record':{'text':'PRIVATE NOTIFICATION'},'author':{'did':'did:plc:a','handle':'a'},
             'indexedAt':'2026-09-09T00:00:00Z'} for reason in ('reply','mention','like')]}
    monkeypatch.setattr(adapter,'_request',request)
    rows=adapter.mentions(since='7d')
    assert [row['kind'] for row in rows]==['reply','mention','quote']
    assert [row['message_id'].rsplit('/',1)[-1] for row in rows]==['reply','mention','quote']
    assert rows[-1]['text']==''
    assert calls==[{'limit':100},{'limit':100,'cursor':'next'}]
    assert len({r['author_key'] for r in rows})==1


def test_mastodon_notification_ids_paginate_status_ids_display_and_public_only(monkeypatch):
    adapter=mastodon.MastodonAdapter(instance='https://fixture.invalid')
    calls=[]
    def request(method,path,**kwargs):
        assert method=='GET'
        query=urllib.parse.parse_qs(urllib.parse.urlsplit(path).query);calls.append(query)
        assert query['types[]']==['mention']
        if 'max_id' in query:return []
        return [{'id':str(100-i),'type':'mention','created_at':'2026-09-09T00:00:00Z',
                 'status':None if visibility=='missing' else {'id':str(200-i),'visibility':visibility,
                   'content':'<p>PRIVATE BODY &amp; text</p>','created_at':'2026-09-08T00:00:00Z',
                   'account':{'acct':'other'},'url':'https://fixture.invalid/post'}}
                for i,visibility in enumerate(('public','direct','missing'))]
    monkeypatch.setattr(adapter,'_request',request)
    rows=adapter.mentions()
    assert len(rows)==1 and rows[0]['message_id']=='200'
    assert rows[0]['timestamp']=='2026-09-09T00:00:00Z'
    assert rows[0]['text']=='PRIVATE BODY & text'
    assert calls[1]['max_id']==['98']


@pytest.mark.parametrize('media',['threads','bluesky','mastodon'])
@pytest.mark.parametrize('reply_state',['none','ledger','unreadable'])
def test_common_cli_keys_reply_tristate_and_minimal_runs_only(media,reply_state,isolated_account_factory,monkeypatch,capsys):
    from thth import adapters
    cfg=isolated_account_factory('mention-one',media=media,instance='https://fixture.invalid',handle='reader')
    actual=accounts.load_account('mention-one')
    Path(actual['token']).write_text(json.dumps({'access_token':'FAKE_TOKEN','identifier':'reader','app_password':'FAKE_PASSWORD'}))
    if reply_state=='ledger':
        engagements.append(actual,'mention-one',{'post_id':'own','reply_to':'POST1','root_post':'POST1','author_key':'a'*16,'account':'mention-one','medium':media,'topic':None,'kind':None,'hour_band':'朝','posted_at':'2026-09-09T09:00:00+09:00','found_by':'mention'})
    if reply_state=='unreadable':
        monkeypatch.setattr(thread_read,'_already_replied_index',lambda *a:({},None,['unreadable']))
    class Reader:
        def mentions(self,**kwargs):return [{'message_id':'POST1','text':'PRIVATE BODY','username':'PRIVATE_USER','author_key':'a'*16,'timestamp':'2026-09-09T00:00:00Z','permalink':'https://fixture.invalid/p'}]
    monkeypatch.setattr(adapters,'make_adapter',lambda *a,**k:Reader())
    roots=[Path(accounts.thth_root()),Path(cfg['repo_dir'])]
    before={str(p):p.read_bytes() for root in roots for p in root.rglob('*') if p.is_file()}
    assert cli.main(['mentions','mention-one','--json'])==0
    output=json.loads(capsys.readouterr().out);row=output['mentions'][0]
    assert {'kind','post_id','author_key','username','preview','timestamp','replied'}<=set(row)
    assert row['post_id']==row['message_id']=='POST1' and row['text']=='PRIVATE BODY'
    assert row['replied']==({'post_id':'own','at':'2026-09-09T09:00:00+09:00','source':'ledger'} if reply_state=='ledger' else None if reply_state=='unreadable' else False)
    after={str(p):p.read_bytes() for root in roots for p in root.rglob('*') if not account_coordination(p,'mention-one') and p.is_file() and not p.name.startswith('runs-')}
    assert after==before
    recorded=runs.read_runs(accounts.state_dir_for('mention-one'))
    assert len(recorded)==1 and recorded[0]['action']=='mentions' and recorded[0]['n']==1
    assert not any(v in json.dumps(recorded) for v in ('PRIVATE BODY','PRIVATE_USER','POST1','FAKE_TOKEN','FAKE_PASSWORD'))


def test_bad_or_repeated_cursor_is_loud(monkeypatch):
    adapter=bluesky.BlueskyAdapter(identifier='reader',app_password='FAKE_PASSWORD')
    monkeypatch.setattr(adapter,'_request',lambda *a,**k:{'notifications':[],'cursor':'same'})
    with pytest.raises(base.AdapterError,match='cursor'):adapter.mentions()
    monkeypatch.setattr(adapter,'_request',lambda *a,**k:(_ for _ in ()).throw(urllib.error.URLError('offline')))
    with pytest.raises(base.AdapterError,match='offline'):adapter.mentions()
