"""4.4.1 の柵: 承認の記録は VM の台帳（`state/<account>/approvals.json`）・⑦ gate の番号。

設計: docs/設計_4.4.1_承認の記録をVMに_2026-10-21.md。それぞれの柵は 1 回わざと壊して赤を見た
（何を壊したかは設計の「現在地」に書いた）。

- (a) `approve --confirm` のあと利用者の repo に commit が無い（bare の main の先頭が同じ）
- (b) 台帳の承認で timer が出す
- (c) 承認のあと原稿を変えたら台帳の承認は効かない（approval_stale）
- (d) require_gate の口座: 番号無し・形の違う番号を断る。正しい番号は台帳に残る
- (e) 台帳の取り消しが repo の古い `status: approved`（4.4.0 までの承認）を止める
- (f) 台帳が壊れていたら承認済みを出さない・承認も断る
- (①) 承認のあと人が repo の status を書き換えたら、台帳に承認があっても出さない
- (②) VM で直しただけ（commit と違う）の原稿の承認を断り「手元で commit・push してから」と言う
- (④) account を書き換えたら「account が承認のときと違う」と言う
- (束) 段がすべて出た束は、repo に古い approved_sha が残っていても台帳の指紋で照合する
"""
import dataclasses
import datetime
import json
import os
import re
from pathlib import Path

from tests.conftest import (approve_via_cli, init_git_pair, make_queue_text, run_git, run_thth)
from thth import accounts, approval_ledger, core, queuefile
from thth.adapters.base import PublishResult

NOW = datetime.datetime.fromisoformat('2026-09-09T10:00:00+09:00')
REL = 'docs/sns/queue/a.md'
BODY = '## threads\n\nBODY_A\n'


def _head(repo, ref='HEAD'):
    return run_git(repo, ['rev-parse', ref]).stdout.strip()


def _setup(tmp_path, factory, *, fm=None, approve=True, **account):
    pair = init_git_pair(tmp_path, seed_content=make_queue_text({'status': 'draft', **(fm or {})}, body=BODY))
    acct = factory(repo_dir=pair['work'], production=True, quiet_hours=None, min_interval_hours=0, **account)
    path = Path(pair['work']) / REL
    if approve:
        approved = approve_via_cli(str(path))
        assert approved.returncode == 0, approved.stderr
    return pair, acct, path


class _Spy:
    def __init__(self):
        self.calls = []

    def publish(self, post, **kw):
        self.calls.append(post)
        return PublishResult('LEDGER_POST', None, NOW.isoformat())


def _throw(name):
    spy = _Spy()
    result = core.throw_once(name, production_flag=True, adapter_factory=lambda *_: spy, now=NOW)
    return result, spy.calls


def _push_from_seed(pair, edit, message):
    seed = Path(pair['seed']) / REL
    run_git(pair['seed'], ['pull', '--ff-only'])
    seed.write_text(edit(seed.read_text()))
    run_git(pair['seed'], ['add', REL])
    run_git(pair['seed'], ['commit', '-m', message])
    run_git(pair['seed'], ['push'])


# (a)
def test_confirm_writes_ledger_not_repo(tmp_path, isolated_account_factory):
    pair, acct, path = _setup(tmp_path, isolated_account_factory, approve=False)
    bare_before, work_before = _head(pair['bare'], 'main'), _head(pair['work'])
    approved = approve_via_cli(str(path))
    assert approved.returncode == 0, approved.stderr
    assert _head(pair['bare'], 'main') == bare_before
    assert _head(pair['work']) == work_before
    assert 'status: draft' in path.read_text()
    assert run_git(pair['work'], ['status', '--porcelain']).stdout == ''
    entry = approval_ledger.load(acct['name'])[REL]
    assert entry['state'] == 'approved' and entry['by'] == 'テスト' and entry['repo_status'] == 'draft'


# (b)
def test_ledger_approval_publishes(tmp_path, isolated_account_factory):
    pair, acct, path = _setup(tmp_path, isolated_account_factory)
    assert queuefile.parse(str(path)).front_matter['status'] == 'approved'
    result, calls = _throw(acct['name'])
    assert result.action == 'post' and len(calls) == 1, dataclasses.asdict(result)


# (c)
def test_edit_after_ledger_approval_is_stale(tmp_path, isolated_account_factory):
    pair, acct, path = _setup(tmp_path, isolated_account_factory)
    _push_from_seed(pair, lambda t: t.replace('BODY_A', 'BODY_B'), 'edit after approval')
    result, calls = _throw(acct['name'])
    assert not calls, dataclasses.asdict(result)
    records = list(Path(accounts.state_dir_for(acct['name'])).glob('runs-*.ndjson'))
    assert any('approval_stale' in p.read_text() for p in records)


# (d)
def test_require_gate_refuses_missing_and_malformed(tmp_path, isolated_account_factory):
    pair, acct, path = _setup(tmp_path, isolated_account_factory, approve=False, require_gate=True)
    missing = approve_via_cli(str(path))
    assert missing.returncode == 1 and 'require_gate' in missing.stderr, missing.stderr
    malformed = approve_via_cli(str(path), extra=['--approval', 'A2610-25'])
    assert malformed.returncode == 1 and '--approval の形が違います' in malformed.stderr, malformed.stderr
    assert approval_ledger.load(acct['name']) == {}
    assert _throw(acct['name'])[1] == []
    good = approve_via_cli(str(path), extra=['--approval', 'A261021-25'])
    assert good.returncode == 0, good.stderr
    entry = approval_ledger.load(acct['name'])[REL]
    assert entry['gate'] == 'A261021-25' and entry['fields']['approved_gate'] == 'A261021-25'


def _legacy_approved(pair, path, acct):
    """4.4.0 までの承認（repo の `status: approved` と指紋）を作る。台帳は空にする。"""
    sha = approval_ledger.load(acct['name'])[REL]['approved_sha']
    os.remove(approval_ledger.path_for(acct['name']))
    _push_from_seed(pair, lambda t: re.sub(r'(?m)^approved_sha:.*$', f'approved_sha: {sha}',
                                           t.replace('status: draft', 'status: approved', 1), count=1),
                    'legacy approval (4.4.0)')
    run_git(pair['work'], ['pull', '--ff-only'])
    assert queuefile.parse(str(path)).front_matter['status'] == 'approved'


def _reasons(name):
    from thth import select
    cfg = accounts.load_account(name)
    files = core.list_queue_files(cfg, tree_sha=_head(cfg['repo_dir']))
    result = select.select_one(files, account_name=name, account_cfg=cfg, now=NOW,
                               last_post_at=None, recent_texts=set())
    return [r.reason for r in result.rejections]


# (e)
def test_ledger_revoke_stops_legacy_repo_approval(tmp_path, isolated_account_factory):
    pair, acct, path = _setup(tmp_path, isolated_account_factory)
    _legacy_approved(pair, path, acct)
    bare_before = _head(pair['bare'], 'main')
    revoked = run_thth(['revoke', str(path), '--by', 'テスト', '--reason', '止める'])
    assert revoked.returncode == 0, revoked.stderr
    assert _head(pair['bare'], 'main') == bare_before
    assert 'status: approved' in path.read_text()  # repo には書かない
    assert queuefile.parse(str(path)).front_matter['status'] == 'draft'
    result, calls = _throw(acct['name'])
    assert not calls, dataclasses.asdict(result)


# (f)
def test_broken_ledger_publishes_nothing_and_refuses_approval(tmp_path, isolated_account_factory):
    pair, acct, path = _setup(tmp_path, isolated_account_factory)
    # repo に 4.4.0 までの承認が残る原稿で試す（台帳が読めないと、台帳の取り消しを読み落として
    # repo の古い承認で出てしまう——そこを止める柵）。
    _legacy_approved(pair, path, acct)
    Path(approval_ledger.path_for(acct['name'])).write_text('{"version": 1, "items": [')
    assert queuefile.parse(str(path)).front_matter['status'] == 'draft'
    result, calls = _throw(acct['name'])
    assert not calls, dataclasses.asdict(result)
    again = approve_via_cli(str(path))
    assert again.returncode == 1 and '承認の台帳を読めません' in again.stderr, again.stderr


# (①)
def test_human_status_change_in_repo_stops_ledger_approval(tmp_path, isolated_account_factory):
    pair, acct, path = _setup(tmp_path, isolated_account_factory)
    _legacy_approved(pair, path, acct)
    # 4.4.0 の承認の上から 4.4.1 で承認し直す（台帳の repo_status は approved）。
    assert approve_via_cli(str(path)).returncode == 0
    assert approval_ledger.load(acct['name'])[REL]['repo_status'] == 'approved'
    # 人が repo で draft に戻す（4.4.0 までの止め方）。
    _push_from_seed(pair, lambda t: t.replace('status: approved', 'status: draft', 1), 'human stops')
    result, calls = _throw(acct['name'])
    assert not calls, dataclasses.asdict(result)
    fm = queuefile.parse(str(path)).front_matter
    assert fm['status'] == 'draft' and fm.get(approval_ledger.REPO_STATUS_MARK)
    assert any('status が書き換えられています' in r for r in _reasons(acct['name'])), _reasons(acct['name'])


def test_human_withdrawn_in_repo_stops_ledger_approval(tmp_path, isolated_account_factory):
    pair, acct, path = _setup(tmp_path, isolated_account_factory)
    _push_from_seed(pair, lambda t: t.replace('status: draft', 'status: withdrawn', 1), 'human withdraws')
    result, calls = _throw(acct['name'])
    assert not calls, dataclasses.asdict(result)


# (②)
def test_vm_only_edit_is_refused_with_local_commit_hint(tmp_path, isolated_account_factory):
    pair, acct, path = _setup(tmp_path, isolated_account_factory, approve=False)
    path.write_text(path.read_text().replace('BODY_A', 'BODY_VM'))
    first = run_thth(['approve', str(path)])
    assert first.returncode == 1 and '手元で原稿を commit・push してから' in first.stderr, first.stderr
    assert approval_ledger.load(acct['name']) == {}


# (④)
def test_account_change_says_account_differs(tmp_path, isolated_account_factory):
    pair, acct, path = _setup(tmp_path, isolated_account_factory)
    isolated_account_factory(name='other-threads', repo_dir=pair['work'], production=True, quiet_hours=None)
    _push_from_seed(pair, lambda t: t.replace(f"account: {acct['name']}", 'account: other-threads'), 'move account')
    result, calls = _throw('other-threads')
    assert not calls
    assert any('account が承認のときと違います' in r for r in _reasons('other-threads')), _reasons('other-threads')


# (束)
def test_finished_bundle_uses_ledger_fingerprint_over_stale_repo_value(tmp_path, isolated_account_factory):
    raw = make_queue_text({'status': 'draft'}, body=BODY)
    pair = init_git_pair(tmp_path, seed_content=raw)
    acct = isolated_account_factory(repo_dir=pair['work'])
    path = Path(pair['work']) / REL
    approval_ledger.record([{'account': acct['name'], 'file': REL, 'state': approval_ledger.APPROVED,
                             'repo': pair['work'],
                             'fields': {'approved_sha': 'L' * 64, 'approved_at': NOW.isoformat(),
                                        'approved_by': 'テスト'}}])
    stale = {'thth': '2', 'account': acct['name'], 'status': 'draft', 'approved_sha': 'R' * 64}
    shown = approval_ledger.overlay(str(path), stale, posted=True)
    assert shown['approved_sha'] == 'L' * 64 and shown['status'] == 'draft'


# (d) thth.me の道（`schedule_request`）も同じ: require_gate の口座は番号無し・形の違う番号を断る。
from tests.test_v212_server_writes import env, remote  # noqa: E402,F401
from tests.test_v3250_burst_reply_queue import publisher  # noqa: E402,F401


def test_require_gate_refuses_server_schedule_without_number(env, remote):
    from tests.test_v3120_guard import call, draft, ledger, refused
    ledger(env, require_gate=True)
    one = draft(env)
    assert str(refused(env, operation='schedule_request', draft_id=one['draft_id'])) == 'approval_gate_required'
    assert str(refused(env, operation='schedule_request', draft_id=one['draft_id'],
                       approval='A26-1')) == 'invalid_request'
    assert approval_ledger.load('alpha') == {}
    result = call(env, operation='schedule_request', draft_id=one['draft_id'], approval='A261021-25')
    assert result['status'] == 'approved'
    (entry,) = approval_ledger.load('alpha').values()
    assert entry['gate'] == 'A261021-25'


# ---- 監査（Fable 10/21）のあとに足した柵 ----------------------------------

# 台帳が壊れたときの取り消し（CLI）: status を見る前に断る（「承認されていません」と言わない）。
def test_broken_ledger_refuses_revoke_before_status(tmp_path, isolated_account_factory):
    pair, acct, path = _setup(tmp_path, isolated_account_factory)
    ledger_path = Path(approval_ledger.path_for(acct['name']))
    good = ledger_path.read_text()
    ledger_path.write_text('{"version": 1, "items": [')
    revoked = run_thth(['revoke', str(path), '--by', 'テスト'])
    assert revoked.returncode == 1 and '台帳が読めないので取り消しを受け付けられません' in revoked.stderr, revoked.stderr
    ledger_path.write_text(good)
    assert queuefile.parse(str(path)).front_matter['status'] == 'approved'  # 断っただけ・何も書いていない


# 台帳が壊れたときの取り消し（thth.me の cancel）: already_draft と答えない。
def test_broken_ledger_refuses_cancel_before_status(env, remote):
    from tests.test_v3120_guard import call, draft
    from thth import server_writes
    from thth.report_service import ReportServiceError
    import pytest
    one = draft(env)
    assert call(env, operation='schedule_request', draft_id=one['draft_id'])['status'] == 'approved'
    ledger_path = Path(approval_ledger.path_for('alpha'))
    ledger_path.write_text('{"version": 1, "items": [')
    with pytest.raises(ReportServiceError) as caught:
        server_writes.cancel_schedule('alpha', one['draft_id'], by='owner')
    assert str(caught.value) == 'ledger_unavailable'


# 別の repo の同じ相対パスには台帳の承認を重ねない。
def test_same_relative_path_in_other_repo_is_not_approved(tmp_path, isolated_account_factory):
    pair, acct, path = _setup(tmp_path / 'one', isolated_account_factory)
    other = init_git_pair(tmp_path / 'two', seed_content=make_queue_text({'status': 'draft'}, body=BODY))
    other_path = Path(other['work']) / REL
    assert queuefile.parse(str(path)).front_matter['status'] == 'approved'
    assert queuefile.parse(str(other_path)).front_matter['status'] == 'draft'


# require_gate の口座では、台帳に記録の無い repo の古い承認を使わない。
def test_require_gate_ignores_legacy_repo_approval(tmp_path, isolated_account_factory):
    pair, acct, path = _setup(tmp_path, isolated_account_factory)
    _legacy_approved(pair, path, acct)
    isolated_account_factory(repo_dir=pair['work'], production=True, quiet_hours=None, min_interval_hours=0,
                             require_gate=True)
    fm = queuefile.parse(str(path)).front_matter
    assert fm['status'] == 'draft' and fm.get(approval_ledger.GATE_LEGACY_MARK)
    result, calls = _throw(acct['name'])
    assert not calls, dataclasses.asdict(result)
    assert any('require_gate の口座では' in r for r in _reasons(acct['name'])), _reasons(acct['name'])


# require_gate の口座の `thth send --production`: 番号が無い・形が違えば断り、repo の HEAD も動かない。
def test_require_gate_refuses_cli_send(tmp_path, isolated_account_factory):
    pair, acct, path = _setup(tmp_path, isolated_account_factory, approve=False, require_gate=True)
    before = (_head(pair['work']), _head(pair['bare'], 'main'))
    for extra in ([], ['--approval', 'A26-1']):
        sent_ = run_thth(['send', acct['name'], '--production', '--confirm', '0' * 12, '--text', '出す'] + extra)
        assert sent_.returncode == 1 and 'approval_gate_required' in sent_.stdout + sent_.stderr, sent_
    assert (_head(pair['work']), _head(pair['bare'], 'main')) == before


# require_gate の口座の CLI の burst の順番待ち: 番号を台帳に残す。
def test_require_gate_cli_burst_queue_records_gate(isolated_account_factory):
    from tests.test_v3250_burst_reply_queue import BURST, _queued_files
    from tests.test_send import _digest_for
    from tests.test_send_guard_cli import NOW, _Spy
    account = isolated_account_factory(production=True, min_interval_hours=1, burst=BURST, require_gate=True)
    spy = _Spy()

    def send(i, approval):
        text, reply_to = f"お返事{i}", f"1799{i}"
        return core.send_once(account['name'], text=text, reply_to=reply_to, production_flag=True,
                              confirm=_digest_for(account['name'], text, reply_to=reply_to),
                              adapter_factory=lambda *_: spy, now=NOW + datetime.timedelta(minutes=i),
                              approval=approval)
    assert send(0, None).error == 'approval_gate_required' and spy.n == 0
    for i in range(3):
        assert send(i, 'A261021-25').exit_code == 0
    queued = send(3, 'A261021-25')
    assert queued.action == 'queued', queued
    (path,) = _queued_files(account)
    entry = approval_ledger.load(account['name'])[approval_ledger.rel_key(path)]
    assert entry['gate'] == 'A261021-25'


# require_gate の口座の thth.me の send_request（その場・猶予・burst）: 番号が無ければ commit の前に断る。
def test_require_gate_refuses_server_send_request(env, remote):
    import subprocess
    from tests.test_v3120_guard import call, ledger, refused
    repo = env['root'] / 'repos/_server/alpha'

    def head():
        # 置き場は最初の下書きで作られる。まだ無ければ「何も積んでいない」（None）。
        done = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True)
        return done.stdout.strip() if done.returncode == 0 else None
    ledger(env, require_gate=True)
    assert str(refused(env, operation='send_request', body='その場')) == 'approval_gate_required'
    # 最初の依頼で置き場が初期化される（「Initialize account-owned server drafts」の 1 本）。その先は動かない。
    before = head()
    assert str(refused(env, operation='send_request', body='その場', approval='A1')) == 'approval_gate_required'
    ledger(env, require_gate=True, hold_minutes=15)
    assert str(refused(env, operation='send_request', body='猶予')) == 'approval_gate_required'
    assert head() == before and approval_ledger.load('alpha') == {}
    assert not list((repo / 'queue').glob('*.md'))
    held = call(env, operation='send_request', body='猶予', approval='A261021-25')
    assert held['status'] == 'held'
    (entry,) = approval_ledger.load('alpha').values()
    assert entry['gate'] == 'A261021-25'


def test_require_gate_server_burst_queue_records_gate(env, remote, publisher):
    from tests.test_v3120_guard import ledger
    from tests.test_v3250_burst_reply_queue import _call
    ledger(env, burst={'count': 3, 'minutes': 10}, min_interval_hours=1, require_gate=True)
    results = [_call(env, operation='send_request', body=f'お返事{i}', reply_to=f'1799{i}', approval='A261021-25')
               for i in range(4)]
    assert [r['status'] for r in results] == ['published'] * 3 + ['queued']
    (entry,) = approval_ledger.load('alpha').values()
    assert entry['gate'] == 'A261021-25'


# --confirm-file と require_gate: 番号が無ければその口座の行を断る・あれば番号を残す。
def test_require_gate_with_confirm_file(tmp_path, isolated_account_factory):
    pair, acct, path = _setup(tmp_path, isolated_account_factory, approve=False, require_gate=True)
    first = run_thth(['approve', str(path)])
    digest = [l.split(': ', 1)[1].strip() for l in first.stdout.splitlines() if l.startswith('digest: ')][0]
    listing = tmp_path / 'confirm.txt'
    listing.write_text(f'{path} {digest}\n')
    refused_ = run_thth(['approve', '--confirm-file', str(listing), '--by', 'テスト'])
    assert refused_.returncode == 1 and 'require_gate' in refused_.stderr, refused_.stderr
    assert approval_ledger.load(acct['name']) == {}
    ok = run_thth(['approve', '--confirm-file', str(listing), '--by', 'テスト', '--approval', 'A261021-25'])
    assert ok.returncode == 0, ok.stderr
    assert approval_ledger.load(acct['name'])[REL]['gate'] == 'A261021-25'


# ---- 再監査（Fable 10/21 2 回目）のあとに足した柵 -------------------------

# 中-1: MCP の send_request・schedule_request が approval を受け、遠くの CLI に --approval を組む。
def test_mcp_tools_accept_and_forward_approval():
    import sys as _sys
    _sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'mcp'))
    import server
    tools = {t['name']: t for t in server.SERVER_TOOLS}
    for name in ('thth_send_request', 'thth_schedule_request'):
        assert 'approval' in tools[name]['inputSchema']['properties'], name
    send_argv = server.REMOTE_CLI['thth_send_request'][0]({'account': 'alpha', 'body': 'x', 'approval': 'A261021-25'})
    sched_argv = server.REMOTE_CLI['thth_schedule_request'][0]({'account': 'alpha', 'draft_id': 'd' * 64,
                                                                'approval': 'A261021-25'})
    assert '--approval=A261021-25' in send_argv and '--approval=A261021-25' in sched_argv


# 中-2: require_gate を後から true にしたら、gate の番号の無い台帳の承認は出さない。
def test_require_gate_ignores_ledger_approval_without_gate(tmp_path, isolated_account_factory):
    pair, acct, path = _setup(tmp_path, isolated_account_factory)   # require_gate: false の間の承認
    isolated_account_factory(repo_dir=pair['work'], production=True, quiet_hours=None, min_interval_hours=0,
                             require_gate=True)
    fm = queuefile.parse(str(path)).front_matter
    assert fm['status'] == 'draft' and fm.get(approval_ledger.GATE_ABSENT_MARK)
    result, calls = _throw(acct['name'])
    assert not calls, dataclasses.asdict(result)
    assert any('gate の番号の無い承認は使いません' in r for r in _reasons(acct['name'])), _reasons(acct['name'])


# 低-1: repo の頂上が見つからなかったことを控えない（後から .git ができても重ねられる）。
def test_repo_root_lookup_does_not_cache_missing(tmp_path):
    folder = tmp_path / 'later' / 'docs'
    folder.mkdir(parents=True)
    page = folder / 'a.md'
    page.write_text('x')
    assert approval_ledger.repo_root_of(str(page)) is None
    (tmp_path / 'later' / '.git').mkdir()
    assert approval_ledger.repo_root_of(str(page)) == os.path.realpath(tmp_path / 'later')


# 低-2: 口座の repo_dir の外の原稿は承認も取り消しも断り、口座の repo_dir を示す。
def test_approve_and_revoke_refuse_file_outside_account_repo(tmp_path, isolated_account_factory):
    pair, acct, path = _setup(tmp_path / 'mine', isolated_account_factory, approve=False)
    other = init_git_pair(tmp_path / 'copy', seed_content=make_queue_text({'status': 'draft'}, body=BODY))
    other_path = Path(other['work']) / REL
    first = run_thth(['approve', str(other_path)])
    assert first.returncode == 1 and 'repo の中の原稿ではありません' in first.stderr \
        and pair['work'] in first.stderr, first.stderr
    revoked = run_thth(['revoke', str(other_path), '--by', 'テスト'])
    assert revoked.returncode == 1 and 'repo の中の原稿ではありません' in revoked.stderr, revoked.stderr
    assert approval_ledger.load(acct['name']) == {}


# 低-3: record は repo を必須にする。
def test_record_requires_repo(tmp_path, isolated_account_factory):
    import pytest
    acct = isolated_account_factory()
    with pytest.raises(ValueError, match='repo が要ります'):
        approval_ledger.record([{'account': acct['name'], 'file': REL, 'state': approval_ledger.APPROVED,
                                 'fields': {'approved_sha': 'L' * 64}}])
    assert approval_ledger.load(acct['name']) == {}
