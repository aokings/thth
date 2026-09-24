"""3.11.0 承認待ちの一覧（VM 側）。docs/設計_3.11.0_承認待ちの一覧ページ_2026-09-25.md §1・§2・§4。

見るのは:
  - 承認 job に「一覧に出す」印（listed）が既定で付き、Worker への create にも乗る。
    その job は最大 24 時間待つ。変更ログに一覧に出したことが残る。
  - listed=False（`thth admin approval request --no-list`）は従来どおり 10 分・印なし。
  - Worker が 24 時間を超える期限を返したら、登録を信じない（unknown）。
  - 一覧に出した job は、24 時間の間のいつ承認されても受け取れる（受領の窓）。
  - 残りの長い一覧の job は、Worker への問い合わせを間引く（流量の上限を食い潰さない）。
"""
from __future__ import annotations

import json
import time

import pytest

from thth import admin_log, approval_jobs as jobs, approval_relay as relay, server_writes as writes
from thth.report_service import ReportServiceError
from tests.test_v212_server_writes import env, draft, FakeRelay  # noqa: F401


class ListingRelay(FakeRelay):
    """Worker と同じ答え方: listed の create は 24 時間（extra だけずらせる）。"""

    def __init__(self, extra=0):
        super().__init__()
        self.extra = extra
        self.polls = 0

    def __call__(self, kind, subject, operation, body):
        if operation == 'create':
            ttl = jobs.LIST_TTL if body.get('listed') is True else jobs.APPROVAL_TTL
            self.rows[subject] = dict(body, status='pending', expires_at=int(time.time() * 1000) + ttl + self.extra)
            return {k: self.rows[subject][k] for k in ('status', 'expires_at')}
        if operation == 'status':
            self.polls += 1
        return super().__call__(kind, subject, operation, body)


@pytest.fixture
def listing(monkeypatch):
    remote = ListingRelay()
    monkeypatch.setattr(relay, 'signed_request', remote)
    jobs._last_poll.clear()
    return remote


def _job(result):
    path = jobs.directory('alpha') / (result['job_id'] + '.json')
    return json.loads(path.read_text())


def _request(env, **options):
    one = draft(env)
    return writes.execute(env['context'], dict(operation='approval_request', account='alpha', draft_id=one['draft_id']), **options)


def test_既定で一覧に出す_印と24時間と変更ログ(env, listing):
    before = int(time.time() * 1000)
    result = _request(env)
    after = int(time.time() * 1000)
    (body,) = listing.rows.values()
    assert body['listed'] is True
    job = _job(result)
    assert job['listed'] is True and job['status'] == 'pending'
    assert before + jobs.LIST_TTL <= job['expires_at'] <= after + jobs.LIST_TTL
    assert result['pending_url'] == 'https://thth.me/pending'
    assert result['approval_url'].startswith('https://thth.me/approve/')
    (event,) = admin_log.read(event='approval_requested')[0]
    assert event['diff'] == {'request_present': [False, True], 'listed': [False, True]}


def test_listed_Falseは従来どおり10分で印が無い(env, listing):
    result = _request(env, listed=False)
    (body,) = listing.rows.values()
    assert 'listed' not in body
    job = _job(result)
    assert 'listed' not in job and job['expires_at'] <= int(time.time() * 1000) + jobs.APPROVAL_TTL
    assert 'pending_url' not in result
    (event,) = admin_log.read(event='approval_requested')[0]
    assert event['diff'] == {'request_present': [False, True]}


def test_Workerが24時間を超える期限を返したら登録を信じない(env, monkeypatch):
    remote = ListingRelay(extra=60_000)
    monkeypatch.setattr(relay, 'signed_request', remote)
    one = draft(env)
    with pytest.raises(ReportServiceError, match='approval_registration_unknown'):
        writes.execute(env['context'], dict(operation='approval_request', account='alpha', draft_id=one['draft_id']))
    (job_file,) = jobs.directory('alpha').glob('*.json')
    assert json.loads(job_file.read_text())['status'] == 'unknown'


def test_一覧のjobは24時間の間のいつ承認されても受け取れる(env, listing, monkeypatch):
    result = _request(env)
    listing.approve()
    later = time.time() + 2 * 3600
    monkeypatch.setattr(jobs.time, 'time', lambda: later)
    jobs.run_once(env['path'])
    assert jobs.status(env['context'], 'alpha', result['job_id'])['status'] == 'completed'
    assert listing.consumes == 1


def test_残りの長い一覧のjobは問い合わせを間引く(env, listing, monkeypatch):
    result = _request(env)
    clock = [1000.0]
    monkeypatch.setattr(jobs.time, 'monotonic', lambda: clock[0])
    for _ in range(3):
        jobs.run_once(env['path'])
    assert listing.polls == 1
    clock[0] += jobs.SLOW_POLL_SECONDS
    jobs.run_once(env['path'])
    assert listing.polls == 2
    # 残りが 10 分を切ったら、従来どおり毎巡問い合わせる。
    end = _job(result)['expires_at'] / 1000 - 300
    monkeypatch.setattr(jobs.time, 'time', lambda: end)
    for _ in range(3):
        jobs.run_once(env['path'])
    assert listing.polls == 5
    assert jobs.status(env['context'], 'alpha', result['job_id'])['status'] == 'pending'


def test_pendingはassetsより先にWorkerが受ける():
    import fnmatch
    from pathlib import Path
    config = (Path(__file__).resolve().parent.parent / 'callback' / 'wrangler.jsonc').read_text(encoding='utf-8')
    config = '\n'.join(line for line in config.splitlines() if not line.lstrip().startswith('//'))
    first = json.loads(config)['assets']['run_worker_first']
    for path in ('/pending', '/pending/' + 'a' * 43):
        assert any(fnmatch.fnmatchcase(path, pattern) for pattern in first), path


def test_privacyは一覧と24時間と認可の始まり方を英日で書く():
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / 'tools'))
    import build_site
    page = build_site.build_privacy()
    assert page == (root / 'callback' / 'public' / 'privacy' / 'index.html').read_text(encoding='utf-8')
    for must in ('https://thth.me/pending', 'at most 24 hours', 'first 60 characters',
                 'expires 10 minutes after it is opened', 'limited to <code>/pending</code>',
                 'does not record who views the list', 'opens the invitation link', '<code>thth auth</code>',
                 '最大24時間', '先頭60字', '開いてから10分で失効', '誰が一覧を見たかを記録しません', '招待リンクを開いて'):
        assert must in page, must
    for obsolete in ('The operator starts authorization', '運営者が認可を開始し'):
        assert obsolete not in page, obsolete


def test_文書_リリースノートと運用と導入に一覧の段落():
    import re
    from pathlib import Path
    docs = Path(__file__).resolve().parent.parent / 'docs'
    note = (docs / 'リリースノート_3.11.0_2026-09-25.md').read_text(encoding='utf-8')
    # 題の「— 下書き」の有無は固定しない（版を上げる commit で外れる）。
    assert note.startswith('# リリースノート 3.11.0（承認待ちの一覧ページ）')
    # 版を上げる commit で「要る」は「要った」になる。見るのは Worker の deploy と順番が書かれていること。
    assert 'Worker（`callback/`）の deploy が要' in note and 'Worker を先に deploy' in note
    section = note.split('## 本人向け: 承認待ちの見方', 1)[1].split('\n## ', 1)[0]
    assert len(re.findall(r'^\d\. ', section, re.M)) == 3 and 'https://thth.me/pending' in section
    operator = (docs / '運用_招待する側.md').read_text(encoding='utf-8')
    assert '### 承認待ちの一覧（3.11.0）' in operator and '`--no-list`' in operator
    invited = (docs / '導入_招待されたら.md').read_text(encoding='utf-8')
    assert '**承認待ちの一覧（3.11.0）。**' in invited and 'https://thth.me/pending' in invited
    assert '承認ページの URL は masaru から届きます' not in invited
