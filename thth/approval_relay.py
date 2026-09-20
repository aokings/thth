"""Signed approval relay management. Private material stays outside project/state logs."""
import base64
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from . import __version__, accounts, admin_log, authclients, redact, httpsafe

PERSON = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z')
OPAQUE = re.compile(r'[A-Za-z0-9_-]{43}\Z')
# Cloudflare Workers の WebCrypto は PBKDF2 の反復を 100,000 までしか受け付けない
# （本番で実測: NotSupportedError "iteration counts above 100000 are not supported"）。
# 承認 secret は 32 byte の乱数なので、伸長はこの回数で十分。Worker 側と一致させること。
ITERATIONS = 100_000

class RelayError(Exception):
    """Only static, non-secret reason codes cross the CLI boundary."""
    def __init__(self, message, *, status=None):
        super().__init__(message)
        self.status = status


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


def key_path():
    try:
        return authclients.path_for('relay-signer', '', {}).with_suffix('.key')
    except authclients.FlowError as exc:
        raise RelayError('relay_signer_store_invalid') from exc


def _directory(path, *, create=False):
    directory = authclients._directory(path, create=create)
    info = os.fstat(directory)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        os.close(directory)
        raise RelayError('relay_signer_parent_unsafe')
    return directory


@contextlib.contextmanager
def private_key():
    directory = _directory(key_path())
    fd = None
    try:
        fd = os.open(key_path().name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 16384):
            raise RelayError('relay_signer_key_unsafe')
        yield fd
    finally:
        if fd is not None: os.close(fd)
        os.close(directory)


def openssl():
    # Fixed trusted locations, not PATH or a shell. No inherited OpenSSL config/modules.
    for binary in ('/opt/homebrew/bin/openssl', '/usr/bin/openssl', '/usr/local/bin/openssl'):
        if not os.path.isfile(binary): continue
        try:
            result = subprocess.run([binary, 'version'], capture_output=True, timeout=5,
                                    env={'PATH': '/usr/bin:/bin', 'OPENSSL_CONF': '/dev/null'})
            if result.returncode == 0 and re.match(rb'OpenSSL 3\.', result.stdout): return binary
        except (OSError, subprocess.SubprocessError): pass
    raise RelayError('openssl_3_required')


def _openssl(arguments, data=b'', *, fd=None):
    try:
        result = subprocess.run([openssl(), *arguments], input=data, capture_output=True, timeout=60,
                                pass_fds=(() if fd is None else (fd,)),
                                env={'PATH':'/usr/bin:/bin', 'OPENSSL_CONF':'/dev/null'})
        if result.returncode: raise RelayError('relay_signer_unavailable')
        return result.stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise RelayError('relay_signer_unavailable') from exc


def public_key():
    with private_key() as fd:
        return b64(_openssl(['pkey', '-in', f'/dev/fd/{fd}', '-pubout', '-outform', 'DER'], fd=fd))


def show_key(by):
    """Recover public output without generating, rotating, or modifying a key."""
    admin_log.actor(by)
    return public_key()


def canonical(method, path, role, subject, operation, timestamp, nonce, body):
    return '\n'.join(('thth-approval-v1', method, path, role, subject, operation,
                      str(timestamp), nonce, hashlib.sha256(body).hexdigest())).encode()


def signed_request(kind, subject, operation, body):
    if (kind == 'person' and PERSON.fullmatch(subject) and operation in ('set','revoke','unlock','status')):
        role = 'operator'
    elif kind == 'deletion' and (subject == 'inbox' and operation == 'list' or OPAQUE.fullmatch(subject) and operation in ('read','verify','complete','discard')):
        role = 'operator'
    elif kind == 'account' and PERSON.fullmatch(subject) and operation in ('revoke','status'):
        role = 'operator'
    elif kind == 'session' and OPAQUE.fullmatch(subject) and operation in ('create','consume','status','cancel'):
        role = 'job'
    else: raise RelayError('invalid_approval_operation')
    path = f'/approval/{kind}/{subject}/{operation}'
    raw = json.dumps(body, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    timestamp, nonce = int(time.time()*1000), secrets.token_urlsafe(32)
    base = os.environ.get('THTH_APPROVAL_BASE_URL', 'https://thth.me')
    try:base=httpsafe.validated_url(base,base=True)
    except httpsafe.EndpointRejected:
        raise RelayError('approval_origin_invalid') from None
    url = urllib.parse.urlsplit(base)
    # Test HTTP allowance (flag and exact hosts) belongs only to httpsafe.
    # Real signed control requests remain pinned to the thth.me origin.
    if url.path not in ('','/') or not (base == 'https://thth.me' or url.scheme == 'http'):
        raise RelayError('approval_origin_invalid')
    with private_key() as fd:
        signature = _openssl(['dgst','-sha256','-sign',f'/dev/fd/{fd}',
            '-sigopt','rsa_padding_mode:pss','-sigopt','rsa_pss_saltlen:32'],
            canonical('POST',path,role,subject,operation,timestamp,nonce,raw),fd=fd)
    request = urllib.request.Request(base.rstrip('/')+path, data=raw, method='POST', headers={
        'Content-Type':'application/json', 'User-Agent':f'thth/{__version__} (+https://thth.me)',
        'X-Thth-Time':str(timestamp),'X-Thth-Nonce':nonce,'X-Thth-Signature':b64(signature)})
    # Even same-origin redirects change the signed path. Never follow one.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs): return None
    try:
        with httpsafe.build_opener(NoRedirect()).open(request, timeout=10) as response:
            limit=16384 if kind=='deletion' else 4096
            data = response.read(limit+1)
            if len(data)>limit or response.status not in (200,201): raise RelayError('approval_relay_unavailable')
            value=json.loads(data)
            if not isinstance(value,dict):raise RelayError('approval_relay_invalid')
            return value
    except httpsafe.EndpointRejected:
        raise RelayError('approval_relay_endpoint_rejected') from None
    except urllib.error.HTTPError as exc:
        raise RelayError('approval_relay_outcome_unknown', status=exc.code) from None
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise RelayError('approval_relay_outcome_unknown') from exc


def _event(event, subject, by, diff):
    admin_log.append(event, subject, {}, by=by, diff=diff)


def init_key(by):
    admin_log.actor(by)
    directory = _directory(key_path(), create=True)
    created = False
    def rollback():
        if created: os.unlink(key_path().name, dir_fd=directory)
    try:
        with admin_log.transaction(rollback=rollback):
            # Refuse any existing leaf, including a link. Never rotate silently.
            try: os.stat(key_path().name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError: pass
            else: raise RelayError('relay_signer_already_exists')
            pem = _openssl(['genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:3072'])
            redact.register_secret(pem.decode())
            fd=os.open(key_path().name, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
            created=True
            with os.fdopen(fd,'wb') as stream:
                stream.write(pem);stream.flush();os.fsync(stream.fileno())
            _event('relay_key_initialized','relay-signer',by,{'private_key':['absent','present']})
        return public_key()
    finally: os.close(directory)


@contextlib.contextmanager
def terminal():
    # Open and verify before random secret generation or remote provisioning.
    fd=os.open('/dev/tty',os.O_WRONLY|os.O_NOCTTY)
    try:
        if not os.isatty(fd): raise RelayError('approver_tty_required')
        with os.fdopen(os.dup(fd),'w') as stream: yield stream
    finally: os.close(fd)


def manage_person(operation, person, by):
    admin_log.actor(by)
    if not isinstance(person,str) or not PERSON.fullmatch(person):raise RelayError('invalid_approver')
    with terminal() if operation=='set' else contextlib.nullcontext() as tty:
        # Known bad audit destination must refuse before any remote change.
        with admin_log.transaction(): pass
        data={};secret=None
        if operation=='set':
            secret=secrets.token_urlsafe(32);salt=secrets.token_bytes(32)
            derived=hashlib.pbkdf2_hmac('sha256',secret.encode(),salt,ITERATIONS,32)
            data={'salt':b64(salt),'verifier':b64(derived),'iterations':ITERATIONS}
            redact.register_secret(secret);redact.register_secret(data['verifier'])
        result=signed_request('person',person,operation,data)
        expected={'set':'configured','revoke':'revoked','unlock':'unlocked'}[operation]
        if result!={'status':expected}:raise RelayError('approval_relay_outcome_unknown')
        # The remote generation is already committed. Always deliver the new
        # secret once on the verified tty, even if the subsequent audit append fails.
        if tty is not None:
            try:
                tty.write('承認 secret（この表示は一度だけです）: '+secret+'\n');tty.flush()
            except OSError as exc:
                raise RelayError('approver_remote_changed_delivery_failed') from exc
        try:
            with admin_log.transaction():
                _event({'set':'approver_set','revoke':'approver_revoked','unlock':'approver_unlocked'}[operation],
                       person,by,{'credential_present':[None,operation!='revoke']})
        except (OSError,ValueError,admin_log.AdminLogError) as exc:
            raise RelayError('approver_remote_changed_audit_unconfirmed') from exc


def command(args):
    import sys
    try:
        if args.relay_command in ('init','show'):
            value=init_key(args.by) if args.relay_command=='init' else show_key(args.by)
            print('APPROVAL_PUBLIC_KEY='+value)
        else:
            manage_person(args.relay_command,args.person,args.by)
            print('approver_'+args.relay_command+'_completed')
        return 0
    except RelayError as exc:
        print(str(exc)+': operation may be incomplete; retry provisioning if remote state is uncertain',file=sys.stderr)
        return 2
    except (OSError,ValueError,admin_log.AdminLogError,subprocess.SubprocessError):
        # Static messages avoid leaking key paths, signatures or remote exception bodies.
        print('approval_admin_failed: operation may be incomplete; inspect admin log and retry provisioning if needed',file=sys.stderr)
        return 2


def register(commands):
    key=commands.add_parser('relay-key',help='承認 relay の署名鍵（管理者 CLI のみ）')
    key_operations=key.add_subparsers(required=True)
    for name in ('init','show'):
        p=key_operations.add_parser(name)
        p.add_argument('--by',required=True);p.set_defaults(func=command,relay_command=name)
    person=commands.add_parser('approver',help='承認 secret の登録・失効・解除（生成値は tty に一度だけ）')
    operations=person.add_subparsers(required=True)
    for name in ('set','revoke','unlock'):
        p=operations.add_parser(name);p.add_argument('person');p.add_argument('--by',required=True)
        p.set_defaults(func=command,relay_command=name)
