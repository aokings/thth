"""Local-only actual Python signer, deletion synchronizer and leave integration."""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sys
import urllib.parse
import urllib.request

root=Path(os.environ['THTH_ROOT']);root.mkdir(mode=0o700)
(root/'accounts').mkdir();(root/'secrets').mkdir(mode=0o700)
apps=Path(os.environ['THTH_APPS_DIR']);apps.mkdir(parents=True,mode=0o700)
shutil.copyfile(sys.argv[1],apps/'relay-signer.key');(apps/'relay-signer.key').chmod(0o600)
origin=os.environ['THTH_APPROVAL_BASE_URL'];port=urllib.parse.urlsplit(origin).port
connect=socket.socket.connect
socket.socket.connect=lambda self,address: connect(self,address) if address==('127.0.0.1',port) else (_ for _ in ()).throw(RuntimeError('external_connect_denied'))
from thth import accounts,deletion,leave,admin_log,approval_relay
secret=secrets.token_urlsafe(32);uid=secrets.token_hex(16)
app=root/'secrets/app.env';app.write_text('THREADS_APP_ID='+secrets.token_hex(12)+'\nTHREADS_APP_SECRET='+secret+'\n');app.chmod(0o600)
os.environ['THTH_APP_ENV_PATH']=str(app)
token=root/'secrets/delta.token';token.write_text(json.dumps({'access_token':secrets.token_urlsafe(32),'user_id':uid}));token.chmod(0o600)
cfg={key:None for key in accounts.REQUIRED_FIELDS};cfg.update(account='delta',project='delta',media='threads',handle='synthetic',token=str(token))
(root/'accounts/delta.json').write_text(json.dumps(cfg))
def encode(value):return base64.urlsafe_b64encode(value).decode().rstrip('=')
def submit(identity,valid=True):
    payload=encode(json.dumps({'algorithm':'HMAC-SHA256','user_id':identity}).encode())
    digest=hmac.digest(secret.encode(),payload.encode(),'sha256') if valid else secrets.token_bytes(32)
    blob=encode(digest)+'.'+payload
    req=urllib.request.Request(origin+'/data-deletion',data=urllib.parse.urlencode({'signed_request':blob}).encode(),headers={'content-type':'application/x-www-form-urlencoded'})
    with urllib.request.urlopen(req) as response:receipt=json.load(response)['confirmation_code']
    return receipt,blob
bad,_=submit(uid,False);unknown,_=submit(secrets.token_hex(16));code,blob=submit(uid)
result=deletion.sync(by='operator')
assert result['verified']==1 and result['unmatched']==1 and result['discarded_invalid_signature']==1
assert (root/'accounts/delta.json').exists()
assert approval_relay.signed_request('deletion',code,'read',{})['status']=='verified'
local=leave.run('delta',by='operator');assert local['phase']=='completed' and local['remote']=='unconfirmed_manual'
with urllib.request.urlopen(origin+'/data-deletion-status?code='+code) as response:remote=json.load(response)
assert remote['status']=='completed' and not token.exists() and not (root/'accounts/delta.json').exists()
assert len(admin_log.read(account='delta',event='deletion_requested')[0])==1
assert len(admin_log.read(account='delta',event='account_removed')[0])==1
assert secret not in json.dumps(admin_log.read()[0]) and blob not in json.dumps(admin_log.read()[0])
print(json.dumps({'verified':result['verified'],'unmatched':result['unmatched'],'discarded_invalid_signature':result['discarded_invalid_signature'],'local_completed':True,'remote_completed':True,'events':2}))
