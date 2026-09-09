"""外部レビュー再レビュー（2026-09-09）の独立検証コードをそのまま取り込んだもの。

出所: 外部レビュアーが `43b7f29` を隔離して実行した再現コード
`/tmp/thth-review-qVnPaj/tests/test_atlas_review.py`（同ディレクトリの `REVIEW.md`・
`independent-results.txt` が実行結果）。「取り込むなら出所を docstring に書く」
（統括の指示）ので、ここに明記する。**中身は英語コメント・原文のまま**（レビュアーの
再現をそのまま実プロセスの git repo でもう一度回すことが目的なので、直訳や書き換えは
しない）。

`43b7f29` の時点では `test_remote_edit_during_publication_must_stop` と
`test_remote_revocation_before_run_must_not_publish` の 2 件が failed だった
（`independent-results.txt` 参照）。外部レビュー再レビュー A（`thth/writeback.py::sync_repo()`
を `thth/core.py::_throw_locked()` から呼ぶ）・B（`thth/writeback.py::commit_and_push()`
の `validate` 引数）を入れたあと、この 6 件（`test_local_mutation_after_cli_approval` の
5 parametrize 込み）+ 2 件が全部 pass することを確認した。

Independent acceptance attacks at 43b7f29; local Git only, fake publication.
"""
import dataclasses
import datetime
import json
import multiprocessing
from pathlib import Path

import pytest

from tests.conftest import init_git_pair, make_queue_text, run_git, run_thth
from thth import accounts, core, inflight, queuefile, sent
from thth.adapters.base import PublishResult

NOW = datetime.datetime.fromisoformat('2026-09-09T10:00:00+09:00')
REL = 'docs/sns/queue/a.md'
# Separate body edits from front-matter edits so Git can merge them normally.
BODY = '# Context\n\n' + '\n'.join('context line ' + str(i) for i in range(12)) + '\n\n## threads\n\nBODY_A\n'


def setup_pair(tmp_path, factory):
    pair = init_git_pair(tmp_path, seed_content=make_queue_text({'status': 'draft'}, body=BODY))
    account = factory(repo_dir=pair['work'], production=True, quiet_hours=None, min_interval_hours=0)
    path = Path(pair['work']) / REL
    approved = run_thth(['approve', str(path)])
    assert approved.returncode == 0, approved.stderr
    run_git(pair['work'], ['add', REL])
    run_git(pair['work'], ['commit', '-m', 'Human approval'])
    run_git(pair['work'], ['push'])
    run_git(pair['seed'], ['pull', '--ff-only'])
    return pair, account, path


@pytest.mark.parametrize('field', ['body', 'topic', 'reply_to', 'publish_at', 'account'])
def test_local_mutation_after_cli_approval(tmp_path, isolated_account_factory, field):
    pair, account, path = setup_pair(tmp_path, isolated_account_factory)
    text = path.read_text()
    changes = {
        'body': ('BODY_A', 'BODY_B'),
        'topic': ('topic: ', 'topic: Bitter'),
        'reply_to': ('reply_to: ', 'reply_to: 123456'),
        'publish_at': ('08:00:00+09:00', '09:00:00+09:00'),
        'account': ('account: nigamilab-threads', 'account: other-threads'),
    }
    before, after = changes[field]
    assert before in text
    path.write_text(text.replace(before, after))
    name = account['name']
    if field == 'account':
        isolated_account_factory(name='other-threads', repo_dir=pair['work'], production=True, quiet_hours=None)
        name = 'other-threads'
    calls = []
    class Spy:
        def publish(self, post, **kw):
            calls.append(post)
            return PublishResult('UNEXPECTED', None, NOW.isoformat())
    result = core.throw_once(name, production_flag=True, adapter_factory=lambda *_: Spy(), now=NOW)
    assert not calls, dataclasses.asdict(result)
    records = list(Path(accounts.state_dir_for(name)).glob('runs-*.ndjson'))
    assert any('approval_stale' in p.read_text() for p in records)


def test_remote_edit_during_publication_must_stop(tmp_path, isolated_account_factory):
    pair, account, path = setup_pair(tmp_path, isolated_account_factory)
    calls = []
    class RemoteWriter:
        def publish(self, post, **kw):
            calls.append(post.text)
            remote_path = Path(pair['seed']) / REL
            remote_path.write_text(remote_path.read_text().replace('BODY_A', 'BODY_B'))
            run_git(pair['seed'], ['add', REL])
            run_git(pair['seed'], ['commit', '-m', 'Concurrent remote edit'])
            run_git(pair['seed'], ['push'])
            return PublishResult('REMOTE_P1', None, NOW.isoformat())
    result = core.throw_once(account['name'], production_flag=True, adapter_factory=lambda *_: RemoteWriter(), now=NOW)
    state = accounts.state_dir_for(account['name'])
    remote = run_git(pair['bare'], ['show', 'main:' + REL]).stdout
    second = core.throw_once(account['name'], production_flag=True, adapter_factory=lambda *_: RemoteWriter(), now=NOW)
    evidence = {'first': dataclasses.asdict(result), 'second': dataclasses.asdict(second),
                'published': calls, 'remote': remote, 'inflight': inflight.read(state),
                'sent': sent.read(state, 'REMOTE_P1')}
    print('REMOTE_EVIDENCE=' + json.dumps(evidence, ensure_ascii=False))
    assert result.exit_code != 0 and inflight.read(state) is not None, evidence
    assert 'post_id: REMOTE_P1' not in remote
    assert second.action == 'inflight'


def test_remote_revocation_before_run_must_not_publish(tmp_path, isolated_account_factory):
    pair, account, path = setup_pair(tmp_path, isolated_account_factory)
    remote_path = Path(pair['seed']) / REL
    remote_path.write_text(remote_path.read_text().replace('status: approved', 'status: withdrawn'))
    run_git(pair['seed'], ['add', REL])
    run_git(pair['seed'], ['commit', '-m', 'Human revokes before run'])
    run_git(pair['seed'], ['push'])
    calls = []
    class Spy:
        def publish(self, post, **kw):
            calls.append(post.text)
            return PublishResult('REVOKED_P1', None, NOW.isoformat())
    result = core.throw_once(account['name'], production_flag=True, adapter_factory=lambda *_: Spy(), now=NOW)
    print('REVOCATION_EVIDENCE=' + json.dumps({'published': calls, 'result': dataclasses.asdict(result)}, ensure_ascii=False))
    assert not calls, 'Remote withdrawal was already pushed before throw_once began'


def test_two_real_processes_share_clone(tmp_path, isolated_account_factory):
    pair, a, path = setup_pair(tmp_path, isolated_account_factory)
    b = isolated_account_factory(name='other-threads', repo_dir=pair['work'], production=True, quiet_hours=None, min_interval_hours=0)
    bpath = Path(pair['queue_dir']) / 'b.md'
    bpath.write_text(make_queue_text({'status':'draft', 'account':b['name']}, body='## threads\n\nOTHER_BODY\n'))
    assert run_thth(['approve', str(bpath)]).returncode == 0
    run_git(pair['work'], ['add', 'docs/sns/queue/b.md'])
    run_git(pair['work'], ['commit', '-m', 'Approve B'])
    run_git(pair['work'], ['push'])
    ctx = multiprocessing.get_context('fork')
    entered, release = ctx.Event(), ctx.Event()
    output = ctx.Queue()
    def child():
        class Blocking:
            def publish(self, post, **kw):
                entered.set()
                assert release.wait(15)
                return PublishResult('LOCK_A', None, NOW.isoformat())
        r = core.throw_once(a['name'], production_flag=True, adapter_factory=lambda *_: Blocking(), now=NOW)
        output.put(dataclasses.asdict(r))
    p = ctx.Process(target=child)
    p.start()
    calls = []
    class BSpy:
        def publish(self, post, **kw):
            calls.append(post.text)
            return PublishResult('LOCK_B', None, NOW.isoformat())
    try:
        assert entered.wait(10), 'A did not reach publication'
        blocked = core.throw_once(b['name'], production_flag=True, adapter_factory=lambda *_: BSpy(), now=NOW)
        assert blocked.action == 'locked' and not calls
    finally:
        release.set()
        p.join(15)
        if p.is_alive():
            p.terminate()
            p.join()
    assert p.exitcode == 0
    assert output.get(timeout=2)['exit_code'] == 0
    result = core.throw_once(b['name'], production_flag=True, adapter_factory=lambda *_: BSpy(), now=NOW)
    assert result.exit_code == 0 and result.post_id == 'LOCK_B'
    assert calls == ['OTHER_BODY']
    assert run_git(pair['work'], ['status', '--porcelain']).stdout == ''
