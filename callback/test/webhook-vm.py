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
threads_secret=os.environ.pop('WEBHOOK_TEST_THREADS_SECRET')
PAGE='1361492233719922'
THREADS_USER='25000000000000001'
from thth import accounts,webhook,adapters
token=root/'secrets/kopicha-fb.token';token.write_text(json.dumps({'access_token':secrets.token_urlsafe(32),'user_id':PAGE}));token.chmod(0o600)
cfg={key:None for key in accounts.REQUIRED_FIELDS};cfg.update(account='kopicha-fb',project='kopicha',media='facebook',handle=PAGE,page_id=PAGE,token=str(token))
(root/'accounts/kopicha-fb.json').write_text(json.dumps(cfg))
threads_token=root/'secrets/kopicha-th.token';threads_token.write_text(json.dumps({'access_token':secrets.token_urlsafe(32),'user_id':THREADS_USER}));threads_token.chmod(0o600)
cfg={key:None for key in accounts.REQUIRED_FIELDS};cfg.update(account='kopicha-th',project='kopicha',media='threads',handle='kopi_chaba',token=str(threads_token))
(root/'accounts/kopicha-th.json').write_text(json.dumps(cfg))
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
# Threads: 署名の鍵を 1 字変えると 403。本文は thth.me に積まれず、inbox には id と時刻だけが落ちる。
reply={'id':'17901','username':'visitor_x','text':'other-persons-text','permalink':'https://www.threads.net/@visitor_x/post/C1',
       'timestamp':'2026-11-05T01:00:00+0000','media_type':'TEXT_POST','replied_to':{'id':'1789'},'root_post':{'id':'1789','username':'kopi_chaba'}}
threads_payload={'object':'threads','entry':[{'id':THREADS_USER,'time':1793000200,'changes':[{'field':'replies','value':reply}]}]}
threads_wrong=threads_secret[:-1]+('A' if threads_secret[-1]!='A' else 'B')
threads_statuses=[deliver(threads_payload,threads_wrong),deliver(threads_payload,threads_secret)]
third=webhook.pull_once()
threads_rows=webhook.rows_for('kopicha-th')
assert 'other-persons-text' not in json.dumps(threads_rows) and 'visitor_x' not in json.dumps(threads_rows)
threads_adapter=adapters.make_adapter(accounts.load_account('kopicha-th'),json.loads(threads_token.read_text()))
print(json.dumps({'statuses':statuses,'first':{'pulled':first['pulled'],'routed':first['routed'],'unrouted':first['unrouted']},
                  'second':second['pulled'],'stored':len(stored),'mentions':[m['message_id'] for m in adapter.mentions()],
                  'replies':[m['message_id'] for m in adapter.webhook_replies()],'duplicates':first['worker']['duplicates'],
                  'threads':{'statuses':threads_statuses,'routed':third['routed'],'rows':[[r['field'],r['value']] for r in threads_rows],
                             'replies':[m['message_id'] for m in threads_adapter.webhook_replies()]}}))
