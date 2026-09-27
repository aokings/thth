"""3.14.3 穴 1: 招待の口座の記録置き場（managed repo）。

- 招待の完了（口座を作った直後）で記録置き場を作る。作れなくても口座は作り、変更ログに
  `managed_repo_init_failed` と理由の符丁を残す。
- 依頼の入口: repo_dir が `_server` の下で `.git` が無く、中身が空か `data/` だけなら、
  `data/` を退避して初期化し、戻して commit と push をしてから進む。それ以外の途中の状態には触らない。
- 招待 → `data/` だけを書く送信（`send --text` の足跡）→ `collect` が通る（repo は使える）。
"""
from __future__ import annotations

import json
import os
import stat
import subprocess

import pytest

from thth import accounts, admin_log, invites, managed_repo, report_http, server_reads
from thth.report_service import execute_report
from tests.test_v3100_invite_create import env as _env  # noqa: F401
from tests.test_v3100_invite_worker import world, make, authorize_params, complete_flow  # noqa: F401
from tests.test_v3100_invite_credential import credentials, run_flow  # noqa: F401
from tests.test_v212_server_writes import env  # noqa: F401
from tests.test_v3140_server_reads import media, collector  # noqa: F401


def _git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True, check=True).stdout


def _data_only(root, name):
    clone = root / 'repos' / '_server' / name
    (clone / 'data' / 'sns' / 'insights' / 'posts').mkdir(parents=True)
    (clone / 'data' / 'sns' / 'insights' / 'posts' / 'p1.json').write_text('{"views": 3}\n')
    return clone


# ---------------------------------------------------------------- 招待の完了

def test_招待の完了で記録置き場を作る(world):
    root = world[0]
    row, _ = complete_flow(world)
    name = invites.STORE.get(row['invite_id'])['account']
    clone, origin = managed_repo.validate(str(root / 'repos' / '_server' / name))
    assert (clone / 'docs' / 'sns' / 'queue').is_dir()
    assert _git(clone, 'rev-parse', 'HEAD') == subprocess.run(
        ['git', '--git-dir', str(origin), 'rev-parse', 'main'], capture_output=True, text=True).stdout
    assert accounts.repo_state(accounts.load_account(name)) == accounts.REPO_OK
    assert not admin_log.read(event='managed_repo_init_failed')[0]


def test_記録置き場を作れなくても口座は作り_変更ログに理由を残す(world, monkeypatch):
    def broken(account, cfg):
        raise ValueError('managed_initialization_failed')
    monkeypatch.setattr(managed_repo, 'initialize', broken)
    row, _ = complete_flow(world)
    record = invites.STORE.get(row['invite_id'])
    assert record['status'] == 'used' and record['remote_ready'] is True
    rows = admin_log.read(event='managed_repo_init_failed')[0]
    assert len(rows) == 1 and rows[0]['account'] == record['account']
    assert rows[0]['diff']['managed_repo'] == [None, 'managed_initialization_failed']


# ---------------------------------------------------------------- 依頼の入口で直す

def test_dataだけの置き場は入口で初期化し_dataをcommitして進む(env, media, collector):
    clone = _data_only(env['root'], 'alpha')
    assert accounts.repo_state(accounts.load_account('alpha')) != accounts.REPO_OK
    execute_report(env['context'], dict(operation='collect', account='alpha'))
    assert collector == [('alpha', 'manual')]
    managed_repo.validate(str(clone))
    assert accounts.repo_state(accounts.load_account('alpha')) == accounts.REPO_OK
    assert json.loads((clone / 'data/sns/insights/posts/p1.json').read_text()) == {'views': 3}
    assert 'data/sns/insights/posts/p1.json' in _git(clone, 'ls-files').split()
    assert _git(clone, 'status', '--porcelain') == ''
    # push まで済んでいる（未 push の commit を残さない）。
    assert _git(clone, 'rev-parse', 'HEAD') == _git(clone, 'rev-parse', '@{u}')
    # 退避の一時ディレクトリは残らない。
    assert sorted(os.listdir(clone.parent)) == ['alpha']
    assert stat.S_IMODE(os.stat(clone).st_mode) == 0o700


def test_空の置き場も初期化する_書く口の入口でも(env):
    clone = env['root'] / 'repos' / '_server' / 'alpha'
    clone.mkdir(parents=True)
    from thth import server_writes
    server_writes.recover_repo('alpha', accounts.load_account('alpha'))
    managed_repo.validate(str(clone))


def test_それ以外の途中の状態には触らない(env, media, collector):
    clone = _data_only(env['root'], 'alpha')
    (clone / 'notes.txt').write_text('keep me\n')
    execute_report(env['context'], dict(operation='collect', account='alpha'))
    assert not (clone / '.git').exists() and (clone / 'notes.txt').read_text() == 'keep me\n'
    assert (clone / 'data/sns/insights/posts/p1.json').exists()
    # 今までどおり断る（途中の状態）。
    with pytest.raises((ValueError, OSError)):
        managed_repo.initialize('alpha', accounts.load_account('alpha'))


def test_bareのoriginだけがあれば触らない(env, media, collector):
    clone = _data_only(env['root'], 'alpha')
    origin = env['root'] / 'state' / 'alpha' / 'git-origin.git'
    origin.mkdir(parents=True, mode=0o700)
    execute_report(env['context'], dict(operation='collect', account='alpha'))
    assert not (clone / '.git').exists() and (clone / 'data').is_dir()


# ---------------------------------------------------------------- 通し

def test_招待から送信だけのあとcollectが通る(world, credentials, monkeypatch):
    root = world[0]
    path, _ = credentials
    record, _ = run_flow(world, path)
    name = record['account']
    clone = root / 'repos' / '_server' / name
    # `send --text` の足跡（data/ に書く）。repo は招待の完了で既にある。
    (clone / 'data' / 'sns' / 'insights' / 'posts').mkdir(parents=True, exist_ok=True)
    (clone / 'data' / 'sns' / 'insights' / 'posts' / 'p1.json').write_text('{}\n')
    context = next(item[3] for item in report_http.load_credentials(path)[1]
                   if item[0] == record['credential_sha256'])
    from thth import collect
    calls = []
    monkeypatch.setattr(collect, 'run_collect', lambda account, **k: calls.append(account) or 0)
    monkeypatch.setattr(server_reads, '_measured', lambda account, request, cfg: {'account': account, 'posts': []})
    got = execute_report(context, dict(operation='collect', account=name))
    assert got == {'account': name, 'posts': []} and calls == [name]
    assert accounts.repo_state(accounts.load_account(name)) == accounts.REPO_OK
