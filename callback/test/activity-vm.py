"""Local-only: the real VM side of /activity (3.12.0 段 3) against a local Worker.

Steps (one process each, sharing THTH_ROOT): `setup <signer key>` makes one invite-style
account with a few sent records and a credential file; `run` performs one activity pass
(push the summary, carry out requested operations, push the outcome) and prints the
account's guard state. Every byte to the Worker goes through the product signer and relay.
"""
import hashlib
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
NAME = 'inv-local-00a1b2'
origin = os.environ['THTH_RELAY_BASE_URL']
port = urllib.parse.urlsplit(origin).port
connect = socket.socket.connect
socket.socket.connect = lambda self, address: connect(self, address) if address[:2] == ('127.0.0.1', port) else (_ for _ in ()).throw(RuntimeError('external_connect_denied'))

from thth import accounts, activity, guard, jst, sent  # noqa: E402

credentials = root / 'private/credentials.json'
if sys.argv[1] == 'setup':
    root.mkdir(mode=0o700)
    (root / 'accounts').mkdir()
    (root / 'secrets').mkdir(mode=0o700)
    (root / 'private').mkdir(mode=0o700)
    apps.mkdir(parents=True, mode=0o700)
    shutil.copyfile(sys.argv[2], apps / 'relay-signer.key')
    (apps / 'relay-signer.key').chmod(0o600)
    token = root / 'secrets' / (NAME + '.token')
    token.write_text(json.dumps({'access_token': secrets.token_urlsafe(32)}))
    token.chmod(0o600)
    cfg = {key: None for key in accounts.REQUIRED_FIELDS}
    cfg.update(account=NAME, project='local', media='threads', handle='reviewer', repo_dir=str(root / 'repos/_server' / NAME),
               queue_dir='queue', replies_dir='replies', token=str(token), production=True, hashtags=True, char_limit=500,
               scheduled=False, approval='none', min_interval_hours=6,
               provenance={'created_at': jst.iso(), 'created_by': 'operator', 'created_via': 'invite', 'created_host': 'vm'})
    (root / 'accounts' / (NAME + '.json')).write_text(json.dumps(cfg))
    state = str(root / 'state' / NAME)
    # 先頭 60 字を越える本文・制御文字・絵文字（Worker の形の検査を通ること）。
    sent.write(state, post_id='1790001', text='一本目の本文\x07です🙂' + 'あ' * 80 + 'ここは出ない', body_hash='x', sent_at=jst.iso())
    sent.write(state, post_id='1790002', text='二本目', body_hash='y', sent_at=jst.iso())
    bearer = secrets.token_urlsafe(32)
    credentials.write_text(json.dumps({'schema_version': 1, 'root': str(root), 'credentials': [{
        'sha256': hashlib.sha256(bearer.encode()).hexdigest(), 'expires_at': '2099-01-01T00:00:00+00:00', 'revoked': False,
        'accounts': {NAME: 'local'}, 'scope': 'user', 'writes': True, 'actor': NAME}]}))
    credentials.chmod(0o600)
    print(json.dumps({'account': NAME}))
else:
    completed = activity.sync_person(NAME, [NAME], str(credentials))
    halted = guard.stopped(NAME)
    config = json.loads(credentials.read_text())
    print(json.dumps({'account': NAME, 'completed': completed, 'stopped': halted and halted.get('reason'),
                      'credential_sha256': [row['sha256'] for row in config['credentials']],
                      'daily_max_posts': accounts.guard_limits(accounts.load_account(NAME))['daily_max_posts']}))
