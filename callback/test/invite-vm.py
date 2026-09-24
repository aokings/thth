"""Local-only: the real VM side of invite links (3.10.0) against a local Worker.

Steps (one process each, sharing THTH_ROOT): `create` makes an invite and prints
the one-time code (test harness only; the product writes it to the tty), `run`
performs one approval-worker pass for invites. Threads itself is faked; every
other byte goes through the product signer, relay client and Worker.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sys
import urllib.parse

root = Path(os.environ['THTH_ROOT'])
apps = Path(os.environ['THTH_APPS_DIR'])
if sys.argv[1] == 'create':
    root.mkdir(mode=0o700)
    (root / 'accounts').mkdir()
    (root / 'secrets').mkdir(mode=0o700)
    apps.mkdir(parents=True, mode=0o700)
    shutil.copyfile(sys.argv[2], apps / 'relay-signer.key')
    (apps / 'relay-signer.key').chmod(0o600)
    app = root / 'secrets/app.env'
    app.write_text('THREADS_APP_ID=' + secrets.token_hex(8) + '\nTHREADS_APP_SECRET=' + secrets.token_urlsafe(32) + '\n')
    app.chmod(0o600)
os.environ['THTH_APP_ENV_PATH'] = str(root / 'secrets/app.env')
origin = os.environ['THTH_APPROVAL_BASE_URL']
port = urllib.parse.urlsplit(origin).port
connect = socket.socket.connect
socket.socket.connect = lambda self, address: connect(self, address) if address[:2] == ('127.0.0.1', port) else (_ for _ in ()).throw(RuntimeError('external_connect_denied'))

from thth import admin_log, invites, oauth  # noqa: E402

# Threads (token exchange and /me) is the only fake.
oauth.exchange_short_lived_token = lambda app_id, app_secret, redirect_uri, code: {'access_token': 'short-' + code}
oauth.exchange_long_lived_token = lambda app_secret, token: {'access_token': 'long-' + token, 'expires_in': 5184000}
oauth.fetch_me = lambda token: {'id': '17841400000000009', 'username': 'reviewer.local'}
oauth.fetch_token_scopes = lambda token: invites.invite_scopes()
invites.OPEN_POLL_SECONDS = 0

if sys.argv[1] == 'create':
    tty = io.StringIO()
    invites.terminal = lambda: contextlib.nullcontext(tty)
    row = invites.create(media='threads', project='meta-review', label='local', expires='7d', production=True, by='operator')
    code = tty.getvalue().rsplit('/invite/', 1)[1].strip()
    print(json.dumps({'code': code, 'invite_id': row['invite_id'], 'account': row['account']}))
else:
    invites.run_once()
    (row,), broken = invites.list_invites()
    events = [event['event'] for event in admin_log.read()[0]]
    print(json.dumps({'status': row['status'], 'approver_set': row['approver_set'], 'reason': row['reason'],
                      'events': events, 'broken': broken}))
