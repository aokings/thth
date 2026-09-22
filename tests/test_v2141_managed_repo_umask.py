"""Managed Git leaves nothing group-writable, whatever umask the operator runs under.

初回の 3.0 通し運転（Ubuntu・`umask 0002`・2026-09-23）で、`git init` が作った
`.git` が 0775、`git fetch` が作った `FETCH_HEAD` が 0664 になり、その直後に
thth 自身の隔離検査が同じ repo を断った（下書きは `write_unavailable` としか
言わない）。ここで見るのは「Git を呼ぶのは thth なのだから umask も thth が
決める」ということ。
"""
import os
import stat
import pytest
from thth import cli, managed_repo, systemd_gen
from tests.test_v212_server_writes import env, draft  # noqa: F401


@pytest.fixture
def loose_umask():
    """運用 VM と同じ `umask 0002` でテストを走らせ、後で必ず戻す。"""
    previous = os.umask(0o002)
    try:
        yield
    finally:
        os.umask(previous)


def _entries(root):
    """`root` 自身とその下の全ディレクトリ・全ファイル（link は辿らない）。"""
    yield str(root)
    for base, directories, files in os.walk(str(root)):
        for name in directories + files:
            yield os.path.join(base, name)


def test_managed_git_store_stays_owner_private_under_umask_002(env, loose_umask):
    assert os.umask(0o002) == 0o002  # 読むだけの往復。テスト自身の umask を確かめる。
    draft(env)
    clone = env['root'] / 'repos/_server/alpha'
    bare = env['root'] / 'state/alpha/git-origin.git'
    assert (clone / '.git' / 'FETCH_HEAD').exists()  # fetch まで通っている
    loose = [path for root in (clone / '.git', bare) for path in _entries(root)
             if os.lstat(path).st_mode & 0o022]
    assert not loose
    assert stat.S_IMODE(os.lstat(clone / '.git').st_mode) == 0o700
    assert managed_repo.validate(clone) == (clone, bare)


def test_managed_repo_refusal_names_the_mode_so_an_operator_can_chmod(env):
    draft(env)
    clone = env['root'] / 'repos/_server/alpha'
    loosened = clone / '.git' / 'FETCH_HEAD'
    loosened.chmod(0o664)
    with pytest.raises(ValueError) as refusal:
        managed_repo.validate(clone)
    assert str(refusal.value) == 'managed_git_store_unsafe_mode'
    loosened.chmod(0o600)
    (clone / '.git').chmod(0o775)
    with pytest.raises(ValueError) as refusal:
        managed_repo.validate(clone)
    assert str(refusal.value) == 'managed_git_store_unsafe_mode'
    (clone / '.git').chmod(0o700)
    assert managed_repo.validate(clone)


def test_draft_put_says_the_mode_instead_of_write_unavailable(env):
    from thth.report_service import ReportServiceError
    draft(env)
    ((env['root'] / 'repos/_server/alpha' / '.git')).chmod(0o775)
    with pytest.raises(ReportServiceError) as refusal:
        draft(env)
    assert str(refusal.value) == 'managed_git_store_unsafe_mode'
    from thth.server_writes import SAFE_ERRORS
    assert 'managed_git_store_unsafe_mode' in SAFE_ERRORS


def test_approval_worker_unit_sets_umask_0077(capsys):
    credentials = '/srv/thth/private/report-credentials.json'
    unit = systemd_gen.render_approval_worker_service(credentials)
    assert 'UMask=0077\n' in unit
    assert cli.main(['systemd', '--approval-worker', '--credentials', credentials]) == 0
    assert 'UMask=0077\n' in capsys.readouterr().out
