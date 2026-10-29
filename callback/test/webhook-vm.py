"""Local-only: a Meta-shaped signed delivery to the real local Worker, then the real VM pull (Meta 計画 段 6).

The Worker runs under `wrangler dev --local`; the VM side is the product `thth.webhook.pull_once` with the
real relay signer. No connection leaves 127.0.0.1. Secrets are throwaway values made by the test.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request

root=Path(os.environ['THTH_ROOT']);root.mkdir(mode=0o700)
(root/'accounts').mkdir();(root/'secrets').mkdir(mode=0o700)
apps=Path(os.environ['THTH_APPS_DIR']);apps.mkdir(parents=True,mode=0o700)
shutil.copyfile(sys.argv[1],apps/'relay-signer.key');(apps/'relay-signer.key').chmod(0o600)
origin=os.environ['THTH_RELAY_BASE_URL'];port=urllib.parse.urlsplit(origin).port
connect=socket.socket.connect
socket.socket.connect=lambda self,address: connect(self,address) if address==('127.0.0.1',port) else (_ for _ in ()).throw(RuntimeError('external_connect_denied'))
secret=os.environ.pop('WEBHOOK_TEST_APP_SECRET')
PAGE='1361492233719922'
from thth import accounts,webhook,adapters
token=root/'secrets/kopicha-fb.token';token.write_text(json.dumps({'access_token':secrets.token_urlsafe(32),'user_id':PAGE}));token.chmod(0o600)
cfg={key:None for key in accounts.REQUIRED_FIELDS};cfg.update(account='kopicha-fb',project='kopicha',media='facebook',handle=PAGE,page_id=PAGE,token=str(token))
(root/'accounts/kopicha-fb.json').write_text(json.dumps(cfg))
def deliver(payload,key):
    raw=json.dumps(payload,ensure_ascii=False).encode()
    signature='sha256='+hmac.new(key.encode(),raw,hashlib.sha256).hexdigest()
    request=urllib.request.Request(origin+'/webhook/meta',data=raw,method='POST',headers={'content-type':'application/json','x-hub-signature-256':signature})
    try:
        with urllib.request.urlopen(request) as response:return response.status
    except urllib.error.HTTPError as exc:return exc.code
comment={'item':'comment','verb':'add','post_id':PAGE+'_9001','comment_id':'9001_1','parent_id':PAGE+'_9001','from':{'id':'555','name':'visitor'},'message':'hello','created_time':1793000000}
mention={'item':'post','verb':'add','post_id':'77_1','sender_id':'556','sender_name':'mentioner','message':'@kopicha','created_time':1793000100}
payload={'object':'page','entry':[{'id':PAGE,'time':1793000000,'changes':[{'field':'feed','value':comment},{'field':'mention','value':mention}]},
                                  {'id':'999000999000','time':1793000001,'changes':[{'field':'feed','value':comment}]}]}
wrong=secret[:-1]+('A' if secret[-1]!='A' else 'B')
statuses=[deliver(payload,wrong),deliver(payload,secret),deliver(payload,secret)]
first=webhook.pull_once()
second=webhook.pull_once()
adapter=adapters.make_adapter(accounts.load_account('kopicha-fb'),json.loads(token.read_text()))
stored=webhook.rows_for('kopicha-fb')
assert secret not in json.dumps(stored) and secret not in json.dumps(webhook.status())
print(json.dumps({'statuses':statuses,'first':{'pulled':first['pulled'],'routed':first['routed'],'unrouted':first['unrouted']},
                  'second':second['pulled'],'stored':len(stored),'mentions':[m['message_id'] for m in adapter.mentions()],
                  'replies':[m['message_id'] for m in adapter.webhook_replies()],'duplicates':first['worker']['duplicates']}))
