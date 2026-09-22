"""2.14.0: `media.write` を足した client の世代（旧無印は read-only）。"""
import hashlib
import json
from pathlib import Path
import urllib.parse

import pytest

from thth import authclients, authflow, oauth, scopes
from thth.adapters import auth_x as x
from tests.test_v211_x_auth import env, entered, run, saved  # noqa: F401
from tests.test_v211_authflow import snapshot


def legacy(env):
    """2.11 の無印ファイル（`x.env`）を置く。**世代の付いた方は消す。**"""
    new = env['client_path']
    old = x.legacy_client_path(env['cfg'])
    authclients.write(old, env['client'])
    new.unlink()
    return old


def test_scopes_include_media_write_and_the_file_name_carries_the_generation(env):
    assert 'media.write' in x.SCOPES and 'media.write' not in x.LEGACY_SCOPES
    canonical = json.dumps(sorted(set(x.SCOPES)), ensure_ascii=True, separators=(',', ':'))
    origin = x.api_origin()
    expected = ('x.' + hashlib.sha256(origin.encode()).hexdigest() + '.'
                + hashlib.sha256(canonical.encode()).hexdigest()[:8] + '.env')
    assert env['client_path'].name == expected
    assert env['client_path'] != x.legacy_client_path(env['cfg'])
    # 集合が同じなら並び順で世代は変わらない。
    assert authclients.path_for('x', origin, env['cfg'],
                                required_scopes=list(reversed(x.SCOPES))) == env['client_path']


def test_the_new_generation_asks_for_media_write_and_records_it(env):
    assert run(env) == 0
    assert env['queries'][0]['scope'] == [' '.join(x.SCOPES)]
    token = saved(env)
    assert 'media.write' in token['scopes']
    assert token['client_scope_generation'] == authclients.scope_generation(x.SCOPES)


def test_the_old_unsuffixed_client_still_posts_text_and_is_never_written(env):
    old = legacy(env)
    before = old.read_bytes()
    profile = x.XAuthProfile.prepare(env['cfg'])
    assert profile.scopes == x.LEGACY_SCOPES and profile.client_path == old
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(
        profile.authorize({'state': 'st', 'code_verifier': 'cv'})).query)
    assert query['scope'] == [' '.join(x.LEGACY_SCOPES)]
    assert 'media.write' not in query['scope'][0]
    env['behavior']['token'] = {'scope': ' '.join(x.LEGACY_SCOPES)}
    assert run(env) == 0
    assert old.read_bytes() == before and not env['client_path'].exists()
    assert saved(env)['client_scope_generation'] == authclients.scope_generation(x.LEGACY_SCOPES)


def test_a_pending_authorization_from_the_old_generation_must_restart(env):
    old = legacy(env)
    profile = x.XAuthProfile.prepare(env['cfg'])
    authflow.begin('alpha', env['cfg'], profile)
    # ここで新しい世代の client が登録される（`thth app set x` のやり直し）。
    authclients.write(env['client_path'], env['client'])
    before = snapshot(env['root'].parent)
    lines = []
    assert oauth.run_auth('alpha', by='operator', code='irrelevant', log=lines.append,
                          human_output=lambda _: None) == 2
    assert 'auth_restart_required' in ' '.join(lines)
    assert env['calls'] == [] and snapshot(env['root'].parent) == before


def test_without_any_client_the_refusal_names_the_next_step(env):
    env['client_path'].unlink()
    with pytest.raises(authflow.FlowError, match='x_client_not_registered'):
        x.XAuthProfile.prepare(env['cfg'])


@pytest.mark.parametrize('generation', ['new', 'legacy', 'absent', 'unknown'])
def test_revoke_uses_the_client_that_issued_the_token(env, generation):
    token = {'access_token': 'a', 'refresh_token': 'r'}
    if generation == 'legacy':
        legacy(env)
        token['client_scope_generation'] = authclients.scope_generation(x.LEGACY_SCOPES)
    elif generation == 'new':
        token['client_scope_generation'] = authclients.scope_generation(x.SCOPES)
    elif generation == 'unknown':
        token['client_scope_generation'] = 'not-a-known-generation'
    if generation == 'absent':
        legacy(env)
    if generation == 'unknown':
        with pytest.raises(authflow.FlowError, match='x_client_generation_unknown'):
            x.client_for_token(env['cfg'], token)
        return
    pair, required = x.client_for_token(env['cfg'], token)
    assert pair == (env['client_id'], env['client_secret'])
    assert required == (x.SCOPES if generation == 'new' else x.LEGACY_SCOPES)


def test_the_x_default_scopes_reported_to_the_administrator_include_media_write():
    from thth import admin_report  # noqa: F401
    assert scopes.MASTODON_SCOPES and 'media.write' in x.SCOPES
