"""外部レビュー再々レビュー（2026-09-10）の独立検証コードをそのまま取り込んだもの。

出所: 外部レビュアーが `e0b5855..79b5ec2` を隔離して実行した再現コード
`/tmp/thth-rereview-7r7Adb/tests/test_atlas_second_review.py`（同ディレクトリの
`REVIEW.md`・`independent-results.txt` が実行結果）。`tests/test_atlas_review.py`
と同じ扱い（統括の指示: 取り込むなら出所を docstring に書く）。**中身は英語コメント・
原文のまま**（レビュアーの再現をそのまま実プロセスの git repo でもう一度回すことが
目的なので、直訳や書き換えはしない）。

`79b5ec2` の時点では次の 4 件が failed だった（`independent-results.txt` 参照）:
`test_remote_metadata_during_publish_must_preserve_stop[account-other-threads]`・
`test_remote_metadata_during_publish_must_preserve_stop[topic-ChangedTopic]`・
`test_unavailable_sync_must_never_publish[missing_origin]`・
`test_board_stale_approval_visible_at_scheduled_0620`。

これらは 3 件の指摘に対応する:
  - P1・1（`thth/core.py`）: rebase 後の検証が本文の hash しか見ておらず、
    account・topic だけの remote 書き換えを見逃す。
  - P1・2（`thth/writeback.py::sync_repo()`）: git repo なのに origin が無いのを
    同期成功として素通りさせ、公開まで進んでしまう。
  - P2（`thth/report.py`・`thth/select.py`）: board が `select_one()` に委譲して
    おり、静かな時間帯には内容の妥当性検査（`approval_stale` 等）自体が走らない
    （board の実行時刻 06:20 JST が静かな時間帯 22:00〜07:00 の中にあるため、
    朝の画面が朝には必ず何も出さない）。

この取り込んだファイルが 5 件とも pass することを、上の 3 件の修正（各 commit）の
最終確認に使う。**修正前の commit（`79b5ec2` 相当）にこのファイルだけを当てると
4 件が落ちることを、統括が確認する前提**（実装を経由しない独立した検証であることを
保つため、このファイルの assert は直さない）。

Independent re-review attacks at e0b5855..79b5ec2; real Git, fake publication only.
"""
import dataclasses
import datetime
import json
from pathlib import Path

import pytest

from tests.test_atlas_review import setup_pair, NOW, REL
from tests.conftest import run_git
from thth import core, accounts, inflight, writeback, report
from thth.adapters.base import PublishResult


@pytest.mark.parametrize('field,value', [
    ('account', 'other-threads'), ('reply_to', '12345678'),
    ('topic', 'ChangedTopic'), ('publish_at', '2026-09-09T09:00:00+09:00'),
])
def test_remote_metadata_during_publish_must_preserve_stop(tmp_path, isolated_account_factory, field, value):
    pair, account, path = setup_pair(tmp_path, isolated_account_factory)
    calls = []
    class RemoteWriter:
        def publish(self, post, **kw):
            calls.append(dataclasses.asdict(post))
            editor = Path(pair['seed']) / REL
            lines = editor.read_text().splitlines()
            editor.write_text('\n'.join(f'{field}: {value}' if l.startswith(field + ':') else l for l in lines) + '\n')
            run_git(pair['seed'], ['add', REL])
            run_git(pair['seed'], ['commit', '-m', 'Remote metadata edit'])
            run_git(pair['seed'], ['push'])
            return PublishResult('META_POST', None, NOW.isoformat())
    first = core.throw_once(account['name'], production_flag=True, adapter_factory=lambda *_: RemoteWriter(), now=NOW)
    state = accounts.state_dir_for(account['name'])
    second = core.throw_once(account['name'], production_flag=True, adapter_factory=lambda *_: RemoteWriter(), now=NOW)
    remote = run_git(pair['bare'], ['show', 'main:' + REL]).stdout
    evidence = {'field': field, 'first': dataclasses.asdict(first), 'second': dataclasses.asdict(second),
                'calls': calls, 'inflight': inflight.read(state), 'remote': remote}
    print('METADATA=' + json.dumps(evidence, ensure_ascii=False))
    assert first.exit_code != 0 and inflight.read(state) is not None, evidence
    assert second.action == 'inflight'
    assert 'post_id: META_POST' not in remote


def test_ff_only_divergence_with_valid_approval_never_publishes(tmp_path, isolated_account_factory):
    pair, account, path = setup_pair(tmp_path, isolated_account_factory)
    # Leave queue unchanged: refusal must be from sync, not an invalid approval hash.
    (Path(pair['work']) / 'local.txt').write_text('leftover local commit')
    run_git(pair['work'], ['add', 'local.txt'])
    run_git(pair['work'], ['commit', '-m', 'Leftover local commit'])
    (Path(pair['seed']) / 'remote.txt').write_text('independent remote advance')
    run_git(pair['seed'], ['add', 'remote.txt'])
    run_git(pair['seed'], ['commit', '-m', 'Remote advance'])
    run_git(pair['seed'], ['push'])
    calls = []
    class Spy:
        def publish(self, post, **kw):
            calls.append(post.text)
            return PublishResult('UNEXPECTED', None, NOW.isoformat())
    result = core.throw_once(account['name'], production_flag=True, adapter_factory=lambda *_: Spy(), now=NOW)
    assert not calls
    assert result.exit_code == 2 and result.error == 'repo_sync_failed'
    assert run_git(pair['work'], ['log', '-1', '--format=%s']).stdout.strip() == 'Leftover local commit'


@pytest.mark.parametrize('mode', ['missing_origin', 'unreachable_origin'])
def test_unavailable_sync_must_never_publish(tmp_path, isolated_account_factory, mode):
    pair, account, path = setup_pair(tmp_path, isolated_account_factory)
    if mode == 'missing_origin':
        run_git(pair['work'], ['remote', 'remove', 'origin'])
    else:
        run_git(pair['work'], ['remote', 'set-url', 'origin', str(tmp_path / 'missing.git')])
    calls = []
    class Spy:
        def publish(self, post, **kw):
            calls.append(post.text)
            return PublishResult('UNSYNCED', None, NOW.isoformat())
    result = core.throw_once(account['name'], production_flag=True, adapter_factory=lambda *_: Spy(), now=NOW)
    print('SYNC=' + json.dumps({'mode': mode, 'calls': calls, 'result': dataclasses.asdict(result)}, ensure_ascii=False))
    assert not calls, 'Published without a usable remote to verify current approval'
    assert result.exit_code == 2 and result.error == 'repo_sync_failed'


def test_actual_push_rejection_then_rebase_revalidates(tmp_path, isolated_account_factory, monkeypatch):
    pair, account, path = setup_pair(tmp_path, isolated_account_factory)
    original_git = writeback._run_git
    push_calls = []
    def timed_git(repo_dir, args):
        if args == ['push']:
            push_calls.append(True)
            if len(push_calls) == 1:
                # Change origin AFTER validation and BEFORE actual push. No fake Git result.
                editor = Path(pair['seed']) / REL
                editor.write_text(editor.read_text().replace('BODY_A', 'BODY_B'))
                run_git(pair['seed'], ['add', REL])
                run_git(pair['seed'], ['commit', '-m', 'Advance origin before push'])
                run_git(pair['seed'], ['push'])
        return original_git(repo_dir, args)
    monkeypatch.setattr(writeback, '_run_git', timed_git)
    calls = []
    class Spy:
        def publish(self, post, **kw):
            calls.append(post.text)
            return PublishResult('RETRY_P1', None, NOW.isoformat())
    result = core.throw_once(account['name'], production_flag=True, adapter_factory=lambda *_: Spy(), now=NOW)
    assert len(push_calls) == 1  # first actual push rejected; retry must stop before second push
    assert result.error == 'text_mismatch_after_rebase'
    assert inflight.read(accounts.state_dir_for(account['name'])) is not None
    assert calls == ['BODY_A']
    remote = run_git(pair['bare'], ['show', 'main:' + REL]).stdout
    assert 'BODY_B' in remote and 'post_id: RETRY_P1' not in remote


def test_board_stale_approval_visible_at_scheduled_0620(tmp_path, isolated_account_factory, frozen_now_jst):
    pair, account, path = setup_pair(tmp_path, isolated_account_factory)
    isolated_account_factory(repo_dir=pair['work'], production=True,
                             quiet_hours=['22:00', '07:00'], min_interval_hours=0)
    path.write_text(path.read_text().replace('BODY_A', 'BODY_B'))
    frozen_now_jst(datetime.datetime.fromisoformat('2026-09-10T10:00:00+09:00'))
    daytime = report.board_summary()['accounts'][0]
    assert daytime['approval_stale_count'] == 1
    frozen_now_jst(datetime.datetime.fromisoformat('2026-09-10T06:20:00+09:00'))
    morning = report.board_summary()['accounts'][0]
    print('BOARD=' + json.dumps({'daytime': daytime, '0620': morning}, ensure_ascii=False))
    assert morning['approval_stale_count'] == 1, 'Quiet hours must not suppress approval diagnostics'
