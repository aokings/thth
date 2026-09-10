"""外部レビュー第 3 巡（2026-09-10）の独立検証コードをそのまま取り込んだもの。

出所: 外部レビュアーが `a67dfec..3ca8ed6` を隔離して実行した再現コード
`/tmp/thth-third-bltB1t/tests/test_atlas_third_review.py`（同ディレクトリの
`REVIEW.md`・`independent-results.txt` が実行結果）。`tests/test_atlas_review.py`・
`tests/test_atlas_second_review.py` と同じ扱い（統括の指示: 取り込むなら出所を
docstring に書く）。**中身はレビュアーの原文のまま**（実プロセスの git repo で
もう一度回すことが目的なので、直訳や書き換えはしない）。

`3ca8ed6` の時点では次の 2 件が failed だった（`independent-results.txt` 参照）:
`test_sync_failure_before_any_publication[missing_git]`・
`test_board_before_scheduled_time_preserves_diagnostics`。

これらは今回の 2 件（P1・P2）に対応する:
  - P1（`thth/writeback.py::sync_repo()`）: `repo_dir` に `.git` が無いことを
    「git repo ではないので queue も無いはず」として無条件に成功扱いにしていた。
    実際には queue ファイルが残ったまま `.git` だけ失われる・退避される事故が
    起こりうり、同期を経ずに select → 公開まで進んでしまっていた。
  - P2（`thth/select.py::select_one()`）: `publish_at` が未来である承認済み
    ファイルは、1 段目のループの `continue` で妥当性検査（`approval_stale` 等）
    に届く前に落ちていた。承認後に本文を書き換えても、publish_at が未来の間は
    `approval_stale` が board に出ない（実行時刻に依存する事故）。

この取り込んだファイルが全件 pass することを、上の 2 件の修正（各 commit）の
最終確認に使う。**修正前の commit（`3ca8ed6` 相当）にこのファイルだけを当てると
2 件が落ちることを、統括が確認する前提**（実装を経由しない独立した検証であることを
保つため、このファイルの assert は直さない）。
"""
import dataclasses
import datetime
import json
from pathlib import Path

import pytest

from tests.test_atlas_review import setup_pair, NOW, REL
from tests.conftest import commit_and_push_if_changed, run_git, run_thth
from thth import core, accounts, inflight, report
from thth.adapters.base import PublishResult


@pytest.mark.parametrize('field,value', [('reply_to', '12345678'), ('publish_at', '2026-09-09T09:00:00+09:00')])
@pytest.mark.parametrize('reapprove', [False, True])
def test_metadata_without_merge_conflict(tmp_path, isolated_account_factory, field, value, reapprove):
    pair, account, path = setup_pair(tmp_path, isolated_account_factory)
    # Legal front-matter layout separates THTH output fields from editorial fields.
    # Therefore safety must come from validation, not an incidental Git conflict.
    raw = path.read_text().split('\n')
    end = raw.index('---', 1)
    output_keys = {'status', 'post_id', 'posted_at'}
    editorial = [l for l in raw[1:end] if l.partition(':')[0] not in output_keys]
    output = [l for l in raw[1:end] if l.partition(':')[0] in output_keys]
    path.write_text('\n'.join(['---', *editorial, *[''] * 8, *output, '---', *raw[end + 1:]]))
    run_git(pair['work'], ['add', REL])
    run_git(pair['work'], ['commit', '-m', 'Separate editorial and output fields'])
    run_git(pair['work'], ['push'])
    run_git(pair['seed'], ['pull', '--ff-only'])
    calls = []
    class Editor:
        def publish(self, post, **kw):
            calls.append(dataclasses.asdict(post))
            editor = Path(pair['seed']) / REL
            editor.write_text('\n'.join(f'{field}: {value}' if l.startswith(field + ':') else l
                                       for l in editor.read_text().splitlines()) + '\n')
            if reapprove:
                assert run_thth(['approve', str(editor)]).returncode == 0
            # `thth approve` が commit・push まで行う（外部レビュー第 4 巡 P1）ので、
            # reapprove のときは既に載っている。載っていなければここで載せる。
            commit_and_push_if_changed(pair['seed'], REL, 'Change metadata during publication')
            return PublishResult('THIRD_META', None, NOW.isoformat())
    result = core.throw_once(account['name'], production_flag=True, adapter_factory=lambda *_: Editor(), now=NOW)
    assert result.error == 'text_mismatch_after_rebase', dataclasses.asdict(result)
    assert inflight.read(accounts.state_dir_for(account['name'])) is not None
    second = core.throw_once(account['name'], production_flag=True, adapter_factory=lambda *_: Editor(), now=NOW)
    assert second.action == 'inflight' and len(calls) == 1
    assert 'post_id: THIRD_META' not in run_git(pair['bare'], ['show', 'main:' + REL]).stdout


@pytest.mark.parametrize('mode', ['bad_url', 'no_upstream', 'diverged', 'dirty_conflict', 'missing_git'])
def test_sync_failure_before_any_publication(tmp_path, isolated_account_factory, mode):
    pair, account, path = setup_pair(tmp_path, isolated_account_factory)
    if mode == 'bad_url':
        run_git(pair['work'], ['remote', 'set-url', 'origin', str(tmp_path / 'unreachable.git')])
    elif mode == 'no_upstream':
        run_git(pair['work'], ['branch', '--unset-upstream'])
    elif mode in {'diverged', 'dirty_conflict'}:
        local = Path(pair['work']) / 'note.txt'
        local.write_text('local')
        if mode == 'diverged':
            run_git(pair['work'], ['add', 'note.txt'])
            run_git(pair['work'], ['commit', '-m', 'Unpushed unrelated local commit'])
        editor = Path(pair['seed']) / 'note.txt'
        editor.write_text('remote')
        run_git(pair['seed'], ['add', 'note.txt'])
        run_git(pair['seed'], ['commit', '-m', 'Remote advance'])
        run_git(pair['seed'], ['push'])
    else:
        # Preserve all data; simulate losing/misplacing Git metadata without deleting it.
        (Path(pair['work']) / '.git').rename(tmp_path / 'saved-git-metadata')
        assert path.exists()  # Losing .git does not remove queue files.
    calls = []
    class Spy:
        def publish(self, post, **kw):
            calls.append(post.text)
            return PublishResult('THIRD_SYNC', None, NOW.isoformat())
    result = core.throw_once(account['name'], production_flag=True, adapter_factory=lambda *_: Spy(), now=NOW)
    print('SYNC=' + json.dumps({'mode': mode, 'calls': calls, 'result': dataclasses.asdict(result)}, ensure_ascii=False))
    assert not calls, 'Published without establishing synchronized queue state'
    assert result.exit_code == 2 and result.error == 'repo_sync_failed'


def test_board_before_scheduled_time_preserves_diagnostics(tmp_path, isolated_account_factory, frozen_now_jst):
    pair, account, path = setup_pair(tmp_path, isolated_account_factory)
    # Approve a future posting and then change only the body; compare before/after due.
    path.write_text(path.read_text().replace('2026-09-09T08:00:00+09:00', '2026-09-10T08:00:00+09:00'))
    assert run_thth(['approve', str(path)]).returncode == 0
    path.write_text(path.read_text().replace('BODY_A', 'BODY_B'))
    evidence = {}
    for hour in (6, 10):
        frozen_now_jst(datetime.datetime.fromisoformat(f'2026-09-10T{hour:02d}:20:00+09:00'))
        evidence[str(hour)] = report.board_summary()['accounts'][0]
    print('BOARD=' + json.dumps(evidence, ensure_ascii=False))
    assert evidence['10']['approval_stale_count'] == 1
    assert evidence['6']['approval_stale_count'] == 1, 'Future publish_at hides stale approval'
