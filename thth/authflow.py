"""Shared authorization orchestration. Waiting and HTTP never hold the admin lock."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from . import accounts, admin_log, appenv, handoff_cursor, httpsafe, jst, redact, secrets_fs

TTL = 600
POLL_SECONDS = 2
RELAY = 'https://thth.me'


class FlowError(ValueError):
    pass


def secret(value):
    redact.register_secret(value)
    return value


@dataclass(repr=False)
class AuthProfile:
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: list
    media = ''
    pkce = True

    def validate(self):
        raise NotImplementedError

    def binding(self, cfg, *, current=False):
        client = self.current_client() if current else (self.client_id, self.client_secret)
        for value in client:
            secret(value)
        raw = json.dumps([cfg, client, self.redirect_uri, self.scopes], sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()


def _read_session(account):
    directory = None
    try:
        directory = handoff_cursor._directory(account)
        fd = os.open('auth_state.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, 'rb') as f:
            info = os.fstat(f.fileno())
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
                raise FlowError('auth_session_unreadable')
            raw = f.read(65537)
        if len(raw) > 65536:
            raise FlowError('auth_session_unreadable')
        value = json.loads(raw, object_pairs_hook=handoff_cursor._pairs)
        if not isinstance(value, dict):
            raise FlowError('auth_session_unreadable')
        for key in ('state', 'read_key', 'code_verifier'):
            if isinstance(value.get(key), str):
                secret(value[key])
        return value
    except FileNotFoundError:
        return None
    except handoff_cursor.CursorDirectoryUnavailable as exc:
        if isinstance(exc.__cause__, FileNotFoundError):
            return None
        raise
    finally:
        if directory is not None:
            os.close(directory)


def _write_session(account, value):
    directory = handoff_cursor._directory(account, create=True)
    temporary = '.auth-' + uuid.uuid4().hex
    try:
        # An existing special-file destination is rejected, not replaced.
        try:
            info = os.stat('auth_state.json', dir_fd=directory, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise FlowError('auth_session_unreadable')
        except FileNotFoundError:
            pass
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f, ensure_ascii=False, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, 'auth_state.json', src_dir_fd=directory, dst_dir_fd=directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)


def _clear_session(account):
    directory = handoff_cursor._directory(account)
    try:
        os.unlink('auth_state.json', dir_fd=directory)
    finally:
        os.close(directory)


def _remaining(session):
    at = jst.parse(session.get('created_at'))
    age = (jst.now_jst() - at).total_seconds() if at else -1
    if age < 0 or age >= TTL:
        raise FlowError('auth_session_expired')
    return TTL - age


def _validate_session(session, cfg, profile):
    if not session or not isinstance(session.get('state'), str) or not session['state']:
        raise FlowError('auth_session_missing: 前回の state がありません')
    _remaining(session)
    if set(session) == {'state', 'created_at'} and profile.media == 'threads':
        # Legacy Threads stored no client binding. Bind it now without replacing
        # its state or creation time; new flows always have a complete binding.
        return {**session, 'schema_version': 1, 'binding': profile.binding(cfg),
                'media': profile.media, 'read_key': None, 'code_verifier': None,
                'credential_generation': _generation(_token_snapshot(Path(cfg['token'])))}
    if (set(session) != {'state', 'created_at', 'schema_version', 'binding', 'media', 'read_key', 'code_verifier', 'credential_generation'}
            or type(session['schema_version']) is not int or session['schema_version'] != 1
            or session['media'] != profile.media or session['binding'] != profile.binding(cfg)):
        raise FlowError('auth_session_changed')
    for key in ('read_key', 'code_verifier'):
        if session[key] is not None and not isinstance(session[key], str):
            raise FlowError('auth_session_unreadable')
    return session


def begin(account, cfg, profile, *, resume=False):
    from . import oauth
    if admin_log._active_fd.get() is not None:
        raise FlowError("auth_nested_transaction_refused")
    Path(accounts.thth_root()).mkdir(parents=True, exist_ok=True)
    with admin_log.transaction():
        current = accounts.load_account(account)
        if current != cfg or profile.binding(current, current=True) != profile.binding(cfg):
            raise FlowError('auth_account_changed')
        if resume:
            session = _validate_session(_read_session(account), cfg, profile)
        else:
            session = dict(schema_version=1, state=secret(oauth._new_state()), created_at=jst.iso(),
                           binding=profile.binding(cfg), media=profile.media,
                           read_key=secret(secrets.token_urlsafe(32)),
                           code_verifier=secret(secrets.token_urlsafe(32)) if profile.pkce else None,
                           credential_generation=_generation(_token_snapshot(Path(cfg['token']))))
        _write_session(account, session)
    return session


def _relay_base():
    override = os.environ.get('THTH_AUTH_RELAY_BASE_URL')
    if not override:
        return RELAY
    parsed = urllib.parse.urlsplit(override)
    # Local test servers only; never redirect credentials to an arbitrary host.
    if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        raise FlowError('invalid_relay_test_origin')
    return override.rstrip('/')


def relay_request(session, *, register=False, timeout=10):
    url = _relay_base() + '/relay/' + urllib.parse.quote(session['state'], safe='')
    if register:
        data = json.dumps({'read_key_hash': hashlib.sha256(session['read_key'].encode()).hexdigest()}).encode()
        request = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'}, method='POST')
    else:
        request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + session['read_key']}, method='GET')
    try:
        with httpsafe.urlopen(request, timeout=timeout) as response:
            status = response.status
            raw = response.read(16385)
        if len(raw) > 16384:
            raise FlowError('relay_invalid_response')
        payload = json.loads(raw) if raw else {}
        if not isinstance(payload, dict):
            raise FlowError('relay_invalid_response')
        return status, payload
    except urllib.error.HTTPError as exc:
        # Do not read/echo error bodies or URLs; they can reflect credentials.
        exc.close()
        return exc.code, {}
    except (OSError, ValueError) as exc:
        raise FlowError('relay_unavailable') from None


def poll(session, *, rehearsing=False):
    deadline = time.monotonic() + (TTL if rehearsing else _remaining(session))
    while time.monotonic() < deadline:
        status, value = relay_request(session, timeout=min(10, max(.001, deadline-time.monotonic())))
        if status == 200:
            if rehearsing:
                raise FlowError('rehearse_unexpected_code: 交換しません')
            code, received = value.get('code'), jst.parse(value.get('received_at'))
            if isinstance(code, str):
                secret(code)
            age = (jst.now_jst() - received).total_seconds() if received else -1
            if (not isinstance(code, str) or not code or len(code.encode()) > 4096
                    or any(ord(c) < 32 or ord(c) == 127 for c in code) or age < 0 or age >= 300):
                raise FlowError('relay_invalid_code')
            _remaining(session)
            return code
        if status not in (404, 429):
            raise FlowError('relay_unavailable')
        time.sleep(min(POLL_SECONDS, max(0, deadline-time.monotonic())))
    raise FlowError('auth_timeout: 600 秒以内に届きませんでした')


def rehearse(cfg, *, redirect_uri=None, log, human_output):
    from . import appenv, oauth
    if cfg.get('media') != 'threads':
        log('rehearse_unsupported_medium: この媒体のOAuthはまだ対応していません')
        return 2
    uri = redirect_uri or cfg.get('redirect_uri')
    if not uri or accounts.redirect_uri_is_dummy(uri):
        log('rehearse_redirect_uri_required')
        return 2
    # Read only the public client ID, without load_app_env's chmod repair.
    try:
        fd = os.open(appenv.default_path(), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('invalid app')
            data = stream.read(65537)
        if len(data) > 65536:
            raise ValueError('invalid app')
        client_id = next((line.partition('=')[2].strip().strip('"\'') for line in data.splitlines()
                          if line.partition('=')[0].strip() == 'THREADS_APP_ID'), '')
        if not client_id:
            raise ValueError('missing client')
    except (OSError, ValueError):
        log('rehearse_client_id_unavailable')
        return 2
    session = {'state': secret(secrets.token_urlsafe(32)), 'read_key': secret(secrets.token_urlsafe(32))}
    human_output(oauth.build_authorize_url(client_id, uri, cfg.get('scopes') or oauth.scopes_mod.DEFAULT_SCOPES, state=session['state']))
    try:
        poll(session, rehearsing=True)
    except FlowError as exc:
        log(redact.redact(str(exc)))
        return 2
    return 2


def paste(raw, session):
    # Strict keys/duplicates: parse_qs must not silently choose one of two states.
    raw = str(raw or '').strip()
    query = urllib.parse.urlsplit(raw).query if '?' in raw else raw.split('#', 1)[0]
    values = urllib.parse.parse_qs(query, keep_blank_values=True)
    states, codes = values.get('state', []), values.get('code', [])
    for value in states + codes:
        secret(value)
    if not states or not states[0]:
        raise FlowError('戻り URL に state がありません。戻り URL 全体を貼ってください')
    if len(states) != 1 or not secrets.compare_digest(states[0].encode(), session['state'].encode()):
        raise FlowError('state が一致しません。受け付けません')
    if len(codes) != 1 or not codes[0] or len(codes[0].encode()) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in codes[0]):
        raise FlowError('code が読み取れませんでした')
    _remaining(session)
    return codes[0]


def _token_snapshot(path):
    for part in (path, *path.parents):
        if part.is_symlink():
            raise FlowError('unsafe_mutation_path')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise FlowError('unsafe_mutation_path')
        return stream.read(), stat.S_IMODE(info.st_mode)


def _generation(snapshot):
    return hashlib.sha256(snapshot[0]).hexdigest() if snapshot is not None else None


def commit(account, cfg, profile, session, token, *, by, via):
    path = Path(cfg['token'])
    snapshot = None
    changed = False
    def rollback():
        if changed:
            try:
                if snapshot is None:
                    path.unlink(missing_ok=True)
                else:
                    secrets_fs.atomic_write_text(str(path), snapshot[0].decode(), mode=snapshot[1])
                _write_session(account, session)
            except (OSError, ValueError):
                raise FlowError('auth_rollback_failed_outcome_uncertain: 保存状態を確認してください') from None
    with admin_log.transaction(rollback=rollback):
        latest = accounts.load_account(account)
        current = _read_session(account)
        if latest != cfg or profile.binding(latest, current=True) != session['binding'] or current != session:
            raise FlowError('auth_session_changed: 新しい認可または台帳変更のため保存しません')
        _remaining(session)
        snapshot = _token_snapshot(path)
        if _generation(snapshot) != session['credential_generation']:
            raise FlowError('auth_credential_changed: 別の操作で認証情報が変わったため保存しません')
        changed = True
        secrets_fs.atomic_write_json(str(path), token, mode=0o600)
        admin_log.append('token_set', account, latest, by=by,
                         diff={'token': ['present' if snapshot else 'absent', 'present'], 'auth_via': [None, via]})
        _clear_session(account)


def run(account, cfg, profile, *, code=None, input_func=None, log=print, by, human_output=print):
    from . import oauth
    try:
        profile.validate()
        session = begin(account, cfg, profile, resume=code is not None)
        via = 'paste'
        if code is None:
            use_relay = input_func is None and profile.redirect_uri.rstrip('/') == RELAY + '/callback'
            if use_relay:
                try:
                    status, _ = relay_request(session, register=True)
                    use_relay = status == 201
                except FlowError:
                    use_relay = False
            # The authorization URL is a deliberate human output, never a logger input.
            human_output(profile.authorize(session))
            if use_relay:
                try:
                    code_value = poll(session)
                    via = 'relay'
                except FlowError as exc:
                    if not str(exc).startswith('relay_unavailable'):
                        raise
                    log('預かり所を使えません。戻り URL 全体を貼ってください')
                    code_value = paste((input_func or input)(), session)
            else:
                log('承認後の戻り URL 全体を貼ってください（code と state の両方が要ります）')
                code_value = paste((input_func or input)(), session)
        else:
            code_value = paste(code, session)
        token = profile.exchange(code_value, session, cfg, log=log)
        token['auth_via'] = via
        commit(account, cfg, profile, session, token, by=by, via=via)
        oauth._out(f"user_id={token['user_id']} username={token['username']}", log=log)
        oauth._out(f"保存しました: {cfg['token']}（600）", log=log)
        return 0
    except admin_log.AdminLogError as exc:
        if exc.appended:
            log('admin_change_recorded_durability_unconfirmed' if exc.complete else 'admin_change_partially_recorded_outcome_uncertain')
        else:
            log('admin_change_refused: 保存と変更ログを完了できませんでした')
        return 2
    except oauth.OAuthError as exc:
        oauth._out(str(exc), log=log)
        return 1
    except EOFError:
        log('戻り URL を読めませんでした。thth auth --code で戻り URL 全体を渡してください')
        return 2
    except (OSError, ValueError, accounts.AccountError, appenv.AppEnvError) as exc:
        # Never print raw OS/request messages containing session paths/URLs.
        log(redact.redact(str(exc)) if isinstance(exc, FlowError) else 'auth_failed: 保存しませんでした')
        return 2
