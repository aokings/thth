"""2.14.0 §5: release の SSH 署名。鍵はこの試験の中で作って、外に出さない。"""
import argparse
import os
import shutil
import subprocess

import pytest

from thth import cli as cli_mod, report as report_mod, selfupdate
from tests.test_selfupdate import _advance_main, _app_pair, _release


def _run(*args, **kwargs):
    return subprocess.run(list(args), capture_output=True, text=True, **kwargs)


@pytest.fixture
def keys(tmp_path):
    """使い捨ての ed25519 鍵を 2 本（信じる鍵・信じない鍵）。"""
    if shutil.which('ssh-keygen') is None:
        pytest.skip('ssh-keygen がありません（SSH 署名の検証は測れません）')
    made = {}
    for name in ('trusted', 'other'):
        path = tmp_path / name
        result = _run('ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', name, '-f', str(path))
        if result.returncode != 0:
            pytest.skip('ssh-keygen が鍵を作れません')
        fingerprint = _run('ssh-keygen', '-lf', str(path) + '.pub').stdout.split()[1]
        made[name] = {'private': str(path), 'public': str(path) + '.pub',
                      'line': (tmp_path / (name + '.pub')).read_text().strip(),
                      'fingerprint': fingerprint}
    allowed = tmp_path / 'allowed_signers'
    allowed.write_text('thth-release ' + made['trusted']['line'] + '\n')
    allowed.chmod(0o600)
    made['allowed'] = str(allowed)
    return made


def sign_with(repo, key):
    _run('git', '-C', repo, 'config', 'gpg.format', 'ssh')
    _run('git', '-C', repo, 'config', 'user.signingkey', key['public'])


def commit(repo, text, *, signed):
    path = os.path.join(repo, 'docs', 'sns', 'queue', 'version.txt')
    with open(path, 'w', encoding='utf-8') as stream:
        stream.write(text)
    args = ['git', '-C', repo, 'commit', '-aq'] + (['-S'] if signed else []) + ['-m', 'advance']
    return _run(*args)


@pytest.fixture
def signed_pair(tmp_path, keys, monkeypatch):
    pair = _app_pair(tmp_path)
    monkeypatch.setenv(selfupdate.ALLOWED_SIGNERS_ENV, keys['allowed'])
    sign_with(pair['seed'], keys['trusted'])
    if commit(pair['seed'], 'v2\n', signed=True).returncode != 0:
        pytest.skip('この git は SSH 署名を作れません')
    _release(pair)
    return pair


def test_a_commit_signed_by_the_trusted_key_verifies_and_names_the_key(signed_pair, keys):
    subprocess.run(['git', '-C', signed_pair['work'], 'fetch', '-q', 'origin',
                    '+refs/heads/release:refs/remotes/origin/release'], check=True)
    oid = _run('git', '-C', signed_pair['work'], 'rev-parse', 'origin/release').stdout.strip()
    found = selfupdate.release_signature(signed_pair['work'], oid)
    assert found['state'] == 'verified'
    assert found['key_id'] == keys['trusted']['fingerprint']
    assert selfupdate.verify_release_signature(signed_pair['work'], oid) is True


def test_an_unsigned_commit_is_missing_not_invalid(tmp_path, keys, monkeypatch):
    pair = _app_pair(tmp_path)
    monkeypatch.setenv(selfupdate.ALLOWED_SIGNERS_ENV, keys['allowed'])
    _advance_main(pair)
    _release(pair)
    subprocess.run(['git', '-C', pair['work'], 'fetch', '-q', 'origin',
                    '+refs/heads/release:refs/remotes/origin/release'], check=True)
    oid = _run('git', '-C', pair['work'], 'rev-parse', 'origin/release').stdout.strip()
    found = selfupdate.release_signature(pair['work'], oid)
    assert found == {'state': selfupdate.SIGNATURE_MISSING, 'key_id': None}
    assert selfupdate.verify_release_signature(pair['work'], oid) is False


def test_a_commit_signed_by_an_untrusted_key_is_invalid(tmp_path, keys, monkeypatch):
    pair = _app_pair(tmp_path)
    monkeypatch.setenv(selfupdate.ALLOWED_SIGNERS_ENV, keys['allowed'])
    sign_with(pair['seed'], keys['other'])
    if commit(pair['seed'], 'v2\n', signed=True).returncode != 0:
        pytest.skip('この git は SSH 署名を作れません')
    _release(pair)
    subprocess.run(['git', '-C', pair['work'], 'fetch', '-q', 'origin',
                    '+refs/heads/release:refs/remotes/origin/release'], check=True)
    oid = _run('git', '-C', pair['work'], 'rev-parse', 'origin/release').stdout.strip()
    found = selfupdate.release_signature(pair['work'], oid)
    assert found == {'state': selfupdate.SIGNATURE_INVALID, 'key_id': None}


def test_the_signed_release_is_taken_in_and_the_board_names_the_key(signed_pair, keys,
                                                                    monkeypatch, capsys):
    before = selfupdate.head(signed_pair['work'])
    monkeypatch.setenv(selfupdate.REQUIRE_SIGNED_ENV, '1')
    monkeypatch.setattr(os, 'execve', lambda *a: None)
    selfupdate.pull_and_reexec(['thth'], app_dir=signed_pair['work'], log=lambda _l: None)
    assert selfupdate.head(signed_pair['work']) != before
    record = selfupdate.release_check(signed_pair['work'])
    assert record['ok'] is True and record['signature_key'] == keys['trusted']['fingerprint']
    monkeypatch.setattr(report_mod.selfupdate_mod, 'release_check', lambda *a, **k: dict(record))
    cli_mod.cmd_board(argparse.Namespace(json=False, account=None))
    assert '署名: 確認済み（' + keys['trusted']['fingerprint'] + '）' in capsys.readouterr().out


@pytest.mark.parametrize('flavour,expected,line', [
    ('unsigned', selfupdate.SIGNATURE_MISSING, '署名: 未確認（取り込んでいません）'),
    ('untrusted', selfupdate.SIGNATURE_INVALID, '署名: 不正（取り込んでいません）'),
])
def test_an_unverifiable_release_is_not_taken_in_and_the_board_says_which(
        tmp_path, keys, monkeypatch, capsys, flavour, expected, line):
    pair = _app_pair(tmp_path)
    monkeypatch.setenv(selfupdate.ALLOWED_SIGNERS_ENV, keys['allowed'])
    if flavour == 'untrusted':
        sign_with(pair['seed'], keys['other'])
        if commit(pair['seed'], 'v2\n', signed=True).returncode != 0:
            pytest.skip('この git は SSH 署名を作れません')
    else:
        _advance_main(pair)
    _release(pair)
    before = selfupdate.head(pair['work'])
    monkeypatch.setenv(selfupdate.REQUIRE_SIGNED_ENV, '1')
    execs = []
    monkeypatch.setattr(os, 'execve', lambda *a: execs.append(a))
    message = selfupdate.pull_and_reexec(['thth'], app_dir=pair['work'], log=lambda _l: None)
    assert selfupdate.head(pair['work']) == before and execs == []
    assert message and selfupdate.SIGNATURE_ERROR in message and expected in message
    record = selfupdate.release_check(pair['work'])
    assert record['ok'] is False and record['error'].endswith(expected)
    monkeypatch.setattr(report_mod.selfupdate_mod, 'release_check', lambda *a, **k: dict(record))
    cli_mod.cmd_board(argparse.Namespace(json=False, account=None))
    assert line in capsys.readouterr().out


def test_the_default_still_takes_an_unsigned_release(tmp_path, keys, monkeypatch):
    pair = _app_pair(tmp_path)
    monkeypatch.delenv(selfupdate.REQUIRE_SIGNED_ENV, raising=False)
    monkeypatch.setenv(selfupdate.ALLOWED_SIGNERS_ENV, keys['allowed'])
    before = selfupdate.head(pair['work'])
    _advance_main(pair)
    _release(pair)
    monkeypatch.setattr(os, 'execve', lambda *a: None)
    selfupdate.pull_and_reexec(['thth'], app_dir=pair['work'], log=lambda _l: None)
    assert selfupdate.head(pair['work']) != before


def test_the_allowed_signers_path_defaults_outside_the_project(monkeypatch):
    monkeypatch.delenv(selfupdate.ALLOWED_SIGNERS_ENV, raising=False)
    assert selfupdate.allowed_signers_path().endswith('/.config/thth/allowed_signers')
