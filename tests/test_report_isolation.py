import json
import os
from pathlib import Path

import pytest

from thth import accounts
from thth.report_isolation import IsolationError, validate_environment


@pytest.fixture
def dedicated(tmp_path, monkeypatch):
    root = tmp_path / 'tenant'
    root.mkdir(mode=0o700)
    (root / 'accounts').mkdir()
    cfg = {key: None for key in accounts.REQUIRED_FIELDS}
    cfg.update(account='demo', project='project', repo_dir=str(root / 'repos' / '_none'),
               queue_dir='docs/sns/queue', replies_dir='data/sns/replies',
               env=str(root / 'secrets' / 'env'), token=str(root / 'secrets' / 'token'))
    (root / 'accounts' / 'demo.json').write_text(json.dumps(cfg))
    monkeypatch.setenv('THTH_ROOT', str(root))
    monkeypatch.delenv('THTH_ACCOUNTS_DIR', raising=False)
    return root


def test_dedicated_read_only_root(dedicated):
    before = (dedicated / 'accounts/demo.json').read_bytes()
    validate_environment(str(dedicated), {'demo': 'project'})
    assert (dedicated / 'accounts/demo.json').read_bytes() == before
    assert not (dedicated / 'secrets').exists()


@pytest.mark.parametrize('field', ['repo_dir', 'env', 'token', 'queue_dir', 'replies_dir'])
def test_outside_resource_rejected(dedicated, field, tmp_path):
    path = dedicated / 'accounts/demo.json'
    cfg = json.loads(path.read_text())
    cfg[field] = str(tmp_path / 'other-tenant')
    path.write_text(json.dumps(cfg))
    with pytest.raises(IsolationError, match='resource_outside_root'):
        validate_environment(str(dedicated), {'demo': 'project'})


@pytest.mark.parametrize('kind', ['directory', 'file', 'dangling', 'fifo', 'hardlink'])
def test_tree_escape_and_special_files_rejected(dedicated, tmp_path, kind):
    target = tmp_path / 'outside'
    if kind == 'directory':
        target.mkdir()
    elif kind in ('file', 'hardlink'):
        target.write_text('SECRET_CANARY')
    link = dedicated / 'unsafe'
    if kind == 'fifo':
        os.mkfifo(link)
    elif kind == 'hardlink':
        os.link(target, link)
    else:
        link.symlink_to(target)
    with pytest.raises(IsolationError, match='unsafe_report_tree') as exc:
        validate_environment(str(dedicated), {'demo': 'project'})
    assert 'SECRET_CANARY' not in str(exc.value)


def test_no_registry_override(dedicated, monkeypatch, tmp_path):
    monkeypatch.setenv('THTH_ACCOUNTS_DIR', str(tmp_path))
    with pytest.raises(IsolationError, match='account_registry_mismatch'):
        validate_environment(str(dedicated), {'demo': 'project'})


def test_private_root_required(dedicated):
    dedicated.chmod(0o755)
    with pytest.raises(IsolationError, match='private_root_required'):
        validate_environment(str(dedicated), {'demo': 'project'})


@pytest.mark.parametrize('scope', [{'demo': 'wrong'}, {'../other': 'project'}, {}])
def test_invalid_scope_rejected(dedicated, scope):
    with pytest.raises(IsolationError):
        validate_environment(str(dedicated), scope)


def test_explicit_root_required(dedicated, monkeypatch):
    monkeypatch.delenv('THTH_ROOT')
    with pytest.raises(IsolationError, match='root_mismatch'):
        validate_environment(str(dedicated), {'demo': 'project'})


def test_unrelated_git_objects_not_walked_but_read_paths_are(dedicated, tmp_path, monkeypatch):
    repo = dedicated / "repos/_none"
    objects = repo / ".git/objects"
    objects.mkdir(parents=True)
    outside = tmp_path / "object"
    outside.write_text("not report data")
    os.link(outside, objects / "hardlink")
    import thth.report_isolation as isolation
    walk = isolation.os.walk
    visited = []
    def tracked(path, **kwargs):
        visited.append(Path(path))
        return walk(path, **kwargs)
    monkeypatch.setattr(isolation.os, "walk", tracked)
    validate_environment(str(dedicated), {"demo": "project"})
    assert all(not objects.is_relative_to(path) for path in visited)
    data = dedicated / "state/demo"
    data.mkdir(parents=True)
    os.link(outside, data / "hardlink")
    with pytest.raises(IsolationError, match="unsafe_report_tree"):
        validate_environment(str(dedicated), {"demo": "project"})


def test_read_path_ancestor_symlink_rejected(dedicated):
    target = dedicated / "actual"
    target.mkdir()
    (dedicated / "state").symlink_to(target)
    with pytest.raises(IsolationError, match="unsafe_report_tree"):
        validate_environment(str(dedicated), {"demo": "project"})


def test_root_parent_alias_preserves_in_root_symlink_detection(dedicated, tmp_path, monkeypatch):
    alias = tmp_path / "parent-alias"
    alias.symlink_to(dedicated.parent, target_is_directory=True)
    root = alias / dedicated.name
    monkeypatch.setenv("THTH_ROOT", str(root))
    cfg_path = dedicated / "accounts/demo.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["repo_dir"] = str(root / "repos/_none")
    cfg_path.write_text(json.dumps(cfg))
    validate_environment(str(root), {"demo": "project"})
    target = dedicated / "actual"
    target.mkdir()
    (dedicated / "state").symlink_to(target)
    with pytest.raises(IsolationError, match="unsafe_report_tree"):
        validate_environment(str(root), {"demo": "project"})
