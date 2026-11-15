"""5.23.0 の報告への直し（返信の印・返せない返信・根への reply_to の予告・札のファイルの道・タグの順）。

- r20261021-c6f28e99: 枝の中で答えた・返さない返信に印を付けて未回答から外す（`thth reply-mark`）。
- r20261104-366caff8: Threads が返信先を断った（code 24・subcode 4279009）返信は THTH が「もう返せない」に。
  reply_to に自分の根を入れ、その根に未回答があれば lint で予告する。
- r20261103-41553a23: 札のファイルを開けないときは batch_file_unreadable で `-`（標準入力）を案内する。
- r20261104-1e45aa54（タグの順）は tests/test_v400_s4_bookmark.py の T_S4_10d。

PyYAML も外の API も使わない（偽の台帳と 127.0.0.1 の偽物だけ）。
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from thth import accounts, bookmark_batch, jst, lint, reply_marks, sent, unanswered
from thth import core
from tests.conftest import write_queue_file

NOW = jst.parse('2026-09-09T10:00:00+09:00')
AT = '2026-09-09T09:00:00+09:00'


def _save_replies(name, root, rows, *, at=AT):
    cfg = accounts.load_account(name)
    directory = Path(accounts.data_dirs(cfg, name)['replies'])
    directory.mkdir(parents=True, exist_ok=True)
    lines = [{'id': rid, 'username': user, 'text': 'PRIVATE', 'timestamp': at, 'post_id': root, **extra}
             for rid, user, extra in rows]
    lines.append({'kind': 'fetch', 'post_id': root, 'collected_at': at})
    (directory / (root + '.ndjson')).write_text(''.join(json.dumps(r) + '\n' for r in lines))


def _own(name, pid, parent=None, at=AT):
    sent.write(accounts.state_dir_for(name), post_id=pid, text='PRIVATE SENT', body_hash='hash',
               sent_at=at, reply_to=parent)


@pytest.fixture
def threads_one(isolated_account_factory):
    cfg = isolated_account_factory('one', handle='owner', media='threads')
    _own('one', 'ROOT')
    # R1 には reply_to で答えた。R2・R3 は同じ枝の中で答えた／読者どうしの会話。
    _own('one', 'ANSWER', 'R1')
    _save_replies('one', 'ROOT', [('R1', 'outside', {}), ('R2', 'reader_a', {}), ('R3', 'reader_b', {})])
    return cfg


def test_印を付けた返信は未回答に数えず_markedに別に数える(threads_one):
    before = unanswered.answer('one', now=NOW)
    assert [r['reply_id'] for r in before['replies']] == ['R2', 'R3'] and before['n_marked'] == 0
    reply_marks.mark('one', 'R2', 'answered_in_branch', by='テスト', answered_by='ANSWER')
    reply_marks.mark('one', 'R3', 'no_reply_needed', by='テスト', reason='読者どうしの会話')
    after = unanswered.answer('one', now=NOW)
    assert after['n_total'] == 0 and after['replies'] == []
    assert after['n_marked'] == 2
    assert [(m['reply_id'], m['mark'], m['answered_by']) for m in after['marked']] == [
        ('R2', 'answered_in_branch', 'ANSWER'), ('R3', 'no_reply_needed', None)]
    assert 'reply_marks_unreadable' not in after['cannot_say']
    # 印の置き場は本文も相手の名前も持たない。
    raw = (Path(accounts.state_dir_for('one')) / 'reply_marks' / 'marks.json').read_text()
    assert 'PRIVATE' not in raw and 'reader_a' not in raw
    assert reply_marks.unmark('one', 'R3')['mark'] == 'no_reply_needed'
    assert [r['reply_id'] for r in unanswered.answer('one', now=NOW)['replies']] == ['R3']


def test_印の置き場が壊れていたら数えるまま言えないことに足す(threads_one):
    reply_marks.mark('one', 'R2', 'no_reply_needed', by='テスト')
    path = Path(accounts.state_dir_for('one')) / 'reply_marks' / 'marks.json'
    path.write_text('{broken')
    result = unanswered.answer('one', now=NOW)
    assert [r['reply_id'] for r in result['replies']] == ['R2', 'R3']
    assert 'reply_marks_unreadable' in result['cannot_say']


def test_reply_markの命令_付ける_外す_台帳に無いidは断る(threads_one, capsys, monkeypatch):
    from thth import cli
    monkeypatch.delenv('THTH_ACTOR', raising=False)
    rc = cli.main(['reply-mark', 'one', 'R2', '--answered-by', 'ANSWER', '--by', 'テスト', '--json'])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out['mark'] == 'answered_in_branch' and out['answered_by'] == 'ANSWER'
    rc = cli.main(['reply-mark', 'one', 'NOPE', '--no-reply-needed', '--by', 'テスト', '--json'])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and out['error'] == 'reply_not_in_ledger' and out['fix'] and out['better']
    assert cli.main(['reply-mark', 'one', 'R3', '--by', 'テスト']) == 2  # 印の種類が無い
    assert 'reply_mark_kind_required' in capsys.readouterr().err
    assert cli.main(['reply-mark', 'one', 'R3', '--no-reply-needed']) == 1  # --by が無い
    capsys.readouterr()
    assert cli.main(['reply-mark', 'one', 'R2', '--undo', '--by', 'テスト']) == 0
    assert '外しました' in capsys.readouterr().out
    assert [r['reply_id'] for r in unanswered.answer('one', now=NOW)['replies']] == ['R2', 'R3']


@pytest.mark.parametrize('detail,error,marked', [
    ({'http_status': 400, 'code': 24, 'error_subcode': 4279009, 'is_transient': False}, 'container作成失敗: HTTP 400', True),
    (None, 'container_http_400_code_24_subcode_4279009', True),
    ({'http_status': 400, 'code': 100, 'error_subcode': 33}, 'container作成失敗: HTTP 400', False),
    (None, 'container作成失敗: HTTP 400', False),
])
def test_Threadsが返信先を断ったら_もう返せない_の印を付ける(threads_one, detail, error, marked):
    lines = []
    core._note_unreplyable('one', accounts.load_account('one'), 'R2', detail, error, lines.append)
    marks, ok = reply_marks.load('one')
    assert ok and (('R2' in marks) is marked)
    if marked:
        assert marks['R2']['mark'] == 'unreplyable' and marks['R2']['by'] == 'thth'
        assert [r['reply_id'] for r in unanswered.answer('one', now=NOW)['replies']] == ['R3']
        assert any('未回答から外しました' in line for line in lines)


def test_X_の口座と返信でない投稿には印を付けない(isolated_account_factory):
    isolated_account_factory('xone', handle='owner', media='x')
    detail = {'code': 24, 'error_subcode': 4279009}
    assert reply_marks.note_publish_refusal('xone', accounts.load_account('xone'), 'R2', detail) is False
    core._note_unreplyable('xone', accounts.load_account('xone'), None, detail, '', None)
    assert reply_marks.load('xone') == ({}, True)


def test_reply_toに自分の根を入れ_根に未回答があれば予告する(isolated_account_factory):
    cfg = isolated_account_factory('one', handle='owner', media='threads')
    recent = jst.iso(jst.now_jst() - datetime.timedelta(hours=2))
    _own('one', 'ROOT', at=recent)
    _save_replies('one', 'ROOT', [('C1', 'outside', {})], at=recent)
    lint._ROOT_REPLIES_CACHE.clear()
    to_root = write_queue_file(cfg['queue_dir'], 'to-root.md', commit=False,
                               fm_overrides={'account': 'one', 'status': 'draft', 'reply_to': 'ROOT'})
    to_comment = write_queue_file(cfg['queue_dir'], 'to-comment.md', commit=False,
                                  fm_overrides={'account': 'one', 'status': 'draft', 'reply_to': 'C1'})
    notes = [n for n in lint.lint_file(to_root) if 'reply_to_is_root' in n]
    assert len(notes) == 1 and notes[0].startswith('warning: ') and 'C1' in notes[0]
    assert lint.reason_code(lint.lint_file(to_root)) == lint.reason_code([])  # 断らない
    assert not [n for n in lint.lint_file(to_comment) if 'reply_to_is_root' in n]


def test_札のファイルを開けないときは道の読み方と標準入力を案内する(tmp_path):
    with pytest.raises(bookmark_batch.BatchError) as caught:
        bookmark_batch.read_source(str(tmp_path / 'mac-only' / 'cards.jsonl'))
    assert caught.value.code == 'batch_file_unreadable'
    bad = tmp_path / 'latin1.jsonl'
    bad.write_bytes(b'\xff\xfe{')
    with pytest.raises(bookmark_batch.BatchError) as caught:
        bookmark_batch.read_source(str(bad))
    assert caught.value.code == 'batch_text_invalid'
    [line] = bookmark_batch.lint_problems(str(tmp_path / 'nope.jsonl'))
    assert line.startswith('batch_file_unreadable: ') and ' - ' in line
    from thth import bookmark_cli, refusal_catalog
    assert 'batch_file_unreadable' in bookmark_batch.REASONS
    text = bookmark_cli._BATCH_MESSAGES['batch_file_unreadable']
    assert ' - ' in text and 'VM' in text
    entry = refusal_catalog.CATALOG['batch_file_unreadable']
    assert entry['better'] and 'remote' in entry['doors']
