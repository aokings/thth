import base64
import hashlib
import hmac
import json
import secrets
import time
from types import SimpleNamespace
import pytest
from thth import accounts,admin_log,deletion,leave,relay
from tests.test_v212_server_writes import env


def blob(env,monkeypatch,*,algorithm='HMAC-SHA256',known=True):
    secret=secrets.token_urlsafe(32);path=env['root']/'secrets/app.env'
    path.write_text('THREADS_APP_ID='+secrets.token_hex(16)+'\nTHREADS_APP_SECRET='+secret+'\n');path.chmod(0o600)
    monkeypatch.setenv('THTH_APP_ENV_PATH',str(path))
    user=secrets.token_hex(16);token=env['root']/'secrets/alpha.json';data=json.loads(token.read_text());data['user_id']=user;token.write_text(json.dumps(data))
    encode=lambda raw:base64.urlsafe_b64encode(raw).decode().rstrip('=')
    payload=encode(json.dumps({'algorithm':algorithm,'user_id':user if known else secrets.token_hex(16)}).encode())
    return encode(hmac.digest(secret.encode(),payload.encode(),'sha256'))+'.'+payload


class Relay:
    def __init__(self,blob):self.code=secrets.token_urlsafe(32);self.blob=blob;self.state='unverified';self.calls=[];self.account=None
    def __call__(self,kind,subject,operation,body):
        self.calls.append((kind,subject,operation))
        if kind=='account':return {'status':'revoked'}
        assert kind=='deletion'
        if operation=='list':return {'receipts':[{'receipt':self.code,'status':self.state}], 'next':None}
        assert subject==self.code
        if operation=='read':return dict(status=self.state,signed_request=self.blob,account=self.account,expires_at=int(time.time()*1000)+86400000)
        if operation=='discard':
            assert body=={'blob_sha256':hashlib.sha256(self.blob.encode()).hexdigest()}
            self.state='discarded';return {'status':self.state}
        if operation=='verify':
            assert body['blob_sha256']==hashlib.sha256(self.blob.encode()).hexdigest()
            assert admin_log.read(event='deletion_requested')[0]
            self.account=body['account'];self.state='verified';return {'status':self.state}
        if operation=='complete':
            assert self.state in ('verified','completed')
            event=admin_log.read(account=body['account'],event='account_removed')[0][-1]
            assert event['run_id']==body['operation_id'] and type(body['completed_at']) is int
            assert not (self.root/'accounts/alpha.json').exists()
            self.state='completed';return {'status':self.state}
        pytest.fail('unexpected relay operation')


def test_verified_deletion_event_precedes_receipt_ack_then_leave_completion(env,monkeypatch):
    raw=blob(env,monkeypatch);remote=Relay(raw);remote.root=env['root'];monkeypatch.setattr(relay,'signed_request',remote)
    assert deletion.sync(by='operator')=={'verified':1,'unmatched':0,'discarded_invalid_signature':0,'observed':1}
    assert remote.state=='verified' and (env['root']/'accounts/alpha.json').exists()
    event=admin_log.read(event='deletion_requested')[0][0]
    assert event['account']=='alpha' and raw not in json.dumps(event) and remote.code not in json.dumps(event)
    leave.run('alpha',by='operator');assert remote.state=='completed'
    assert len(admin_log.read(event='account_removed')[0])==1


@pytest.mark.parametrize('variant',['signature','algorithm','identity','ambiguous'])
def test_unverified_request_never_links_account_or_stops_it(env,monkeypatch,variant):
    raw=blob(env,monkeypatch,algorithm='none' if variant=='algorithm' else 'HMAC-SHA256',known=variant!='identity')
    if variant=='signature':raw=('A' if raw[0]!='A' else 'B')+raw[1:]
    if variant=='ambiguous':
        source=json.loads((env['root']/'secrets/alpha.json').read_text());dest=env['root']/'secrets/beta.json';other=json.loads(dest.read_text());other['user_id']=source['user_id'];dest.write_text(json.dumps(other))
    remote=Relay(raw);monkeypatch.setattr(relay,'signed_request',remote)
    result=deletion.sync(by='operator')
    assert result['unmatched']==(0 if variant=='signature' else 1)
    assert result['discarded_invalid_signature']==(1 if variant=='signature' else 0)
    assert remote.state==('discarded' if variant=='signature' else 'unverified')
    assert admin_log.read(event='deletion_requested')[0]==[] and leave.read('alpha') is None
    assert accounts.load_account('alpha') and not deletion._records()


def test_log_failure_cannot_ack_verification(env,monkeypatch):
    remote=Relay(blob(env,monkeypatch));monkeypatch.setattr(relay,'signed_request',remote)
    monkeypatch.setattr(admin_log,'_emit',lambda *a:(_ for _ in ()).throw(admin_log.AdminLogError('synthetic')))
    with pytest.raises(admin_log.AdminLogError):deletion.sync(by='operator')
    assert remote.state=='unverified' and not any(x[2]=='verify' for x in remote.calls)
    assert admin_log.read(event='deletion_requested')[0]==[]


def test_existing_receipt_mapping_cannot_change_account(env):
    code=secrets.token_urlsafe(32);row={'receipt':code,'account':'alpha','expires_at':int(time.time()*1000)+1000}
    deletion._save(row)
    with pytest.raises(ValueError):deletion._save(dict(row,account='beta'))
    assert deletion._records()==[row]


@pytest.mark.parametrize('variant',['missing_key','unsafe_key','empty_key'])
def test_unknown_key_never_discards_even_a_bad_signature(env,monkeypatch,variant):
    raw=blob(env,monkeypatch);raw=('A' if raw[0]!='A' else 'B')+raw[1:]
    path=env['root']/'secrets/app.env'
    if variant=='missing_key':path.unlink()
    elif variant=='unsafe_key':path.chmod(0o644)
    else:path.write_text('THREADS_APP_ID=synthetic\n')
    remote=Relay(raw);monkeypatch.setattr(relay,'signed_request',remote)
    result=deletion.sync(by='operator')
    assert result['unmatched']==1 and result['discarded_invalid_signature']==0
    assert remote.state=='unverified' and not any(c[2]=='discard' for c in remote.calls)


def test_bad_signature_is_discardable_before_payload_interpretation(env,monkeypatch):
    blob(env,monkeypatch)
    raw=secrets.token_urlsafe(32)+'.'+base64.urlsafe_b64encode(b'not-json').decode().rstrip('=')
    remote=Relay(raw);monkeypatch.setattr(relay,'signed_request',remote)
    assert deletion.sync(by='operator')['discarded_invalid_signature']==1
    assert remote.state=='discarded' and not deletion._records()
