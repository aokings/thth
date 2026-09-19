from pathlib import Path
import pytest
from thth import cli
from tests.conftest import write_queue_file


def test_cwd_precedes_repo(isolated_account_factory,tmp_path,monkeypatch,capsys):
    cfg=isolated_account_factory('path-one');repo=Path(cfg['repo_dir'])
    (repo/'same.md').write_text('repo')
    cwd=tmp_path/'cwd';cwd.mkdir();(cwd/'same.md').write_text('cwd');monkeypatch.chdir(cwd)
    assert cli._resolve_repo_path('same.md')=='same.md'
    assert capsys.readouterr().err==''


def test_unique_repo_and_shared_repo_alias_deduplicate(isolated_account_factory,tmp_path,monkeypatch,capsys):
    cfg=isolated_account_factory('path-one');repo=Path(cfg['repo_dir'])
    isolated_account_factory('path-two',repo_dir=str(repo))
    (repo/'same.md').write_text('repo');monkeypatch.chdir(tmp_path)
    assert cli._resolve_repo_path('same.md')==str(repo/'same.md')
    assert str(repo) in capsys.readouterr().err


def test_ambiguous_and_missing_are_loud(isolated_account_factory,tmp_path,monkeypatch):
    roots=[]
    for name in ('one','two'):
        repo=tmp_path/name;repo.mkdir();cfg=isolated_account_factory(name,repo_dir=str(repo));roots.append(repo)
        (repo/'same.md').write_text(name)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError,match='複数') as exc:cli._resolve_repo_path('same.md')
    assert all(str(root/'same.md') in str(exc.value) for root in roots)
    with pytest.raises(ValueError,match='VM の repo に無い'):cli._resolve_repo_path('absent.md')
    with pytest.raises(ValueError,match='手元のパス'):cli._resolve_repo_path('/Users/not-real/file.md')


@pytest.mark.parametrize('command', ['preview','lint','approve','revoke'])
def test_all_four_cli_paths_resolve_before_operation(command,isolated_account_factory,tmp_path,monkeypatch,capsys):
    cfg=isolated_account_factory('paths')
    path=write_queue_file(cfg['queue_dir'],'path.md',fm_overrides={'account':'paths','status':'draft'})
    relative=str(Path(path).relative_to(cfg['repo_dir']))
    monkeypatch.chdir(tmp_path)
    # No production/API action: preview/lint read, approve refuses missing remote,
    # revoke refuses missing by. The path-resolution diagnostic proves dispatch.
    cli.main([command,relative,'--json'])
    output=capsys.readouterr()
    assert str(Path(path).resolve()) in output.err
    assert 'repo 相対パスを解決' in output.err
    assert 'VM の repo に無い' not in output.err


@pytest.mark.parametrize('bad', ['3','null','[]','{}'])
@pytest.mark.parametrize('command', ['preview','lint','approve','revoke'])
def test_unrelated_malformed_ledger_does_not_hide_unique_path(bad,command,isolated_account_factory,tmp_path,monkeypatch,capsys):
    from thth import accounts
    cfg=isolated_account_factory('valid')
    path=write_queue_file(cfg['queue_dir'],'path.md',fm_overrides={'account':'valid','status':'draft'})
    (Path(accounts.accounts_dir())/'broken.json').write_text(bad)
    monkeypatch.chdir(tmp_path)
    relative=str(Path(path).relative_to(cfg['repo_dir']))
    rc=cli.main([command,relative,'--json'])
    output=capsys.readouterr()
    assert rc in (0,1)
    assert 'repo 相対パスを解決' in output.err and '台帳 broken を読めない' in output.err
    if output.out:
        import json
        json.loads(output.out)
