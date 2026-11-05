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


# 揺れ（4.5.0 の publish run 39662720366）: 木を歩く間に台帳の一時ファイルが os.replace で消えると、
# lstat の FileNotFoundError が invalid_report_environment になっていた。消えた子は読めないので見逃す。
def test_entry_vanishing_during_walk_is_tolerated(dedicated, monkeypatch):
    data = dedicated / "state/demo"
    data.mkdir(parents=True)
    (data / "approvals.json").write_text("{}")
    real_walk = os.walk

    def racing_walk(path, *args, **kwargs):
        for directory, dirs, files in real_walk(path, *args, **kwargs):
            if Path(directory).resolve() == data.resolve():
                files = [*files, "approvals.json.0f0f.tmp"]   # 並べた直後に入れ替えで消えた
            yield directory, dirs, files

    monkeypatch.setattr(os, "walk", racing_walk)
    validate_environment(str(dedicated), {"demo": "project"})


# --------------------------------------------------------------------------
# 設計 広告の資格と遠くの道（2026-11-06）段 0・段 1: 行の口座が `adaccounts/` の台帳でも同じ門で見る
# --------------------------------------------------------------------------

def _adledger(root, name="demo-ads", project="project", **extra):
    from thth.ads import ledger as ads_ledger
    data = ads_ledger.build(name, act_id="1234567890", project=project)
    data.update({"token": str(root / "secrets" / (name + ".token")), **extra})
    (root / "adaccounts").mkdir(exist_ok=True)
    (root / "adaccounts" / (name + ".json")).write_text(json.dumps(data))
    return data


def test_広告の台帳の名前の行が常駐と報告の口を止めない(dedicated):
    """段 0 で赤（`accounts/<名前>.json` が無いので invalid_report_environment）→ 段 1 で緑。"""
    _adledger(dedicated)
    validate_environment(str(dedicated), {"demo": "project", "demo-ads": "project"})
    validate_environment(str(dedicated), {"demo-ads": "project"})


def test_広告の台帳の置き場のsymlinkは断る(dedicated, tmp_path):
    _adledger(dedicated)
    outside = tmp_path / "outside"
    outside.write_text("SECRET_CANARY")
    (dedicated / "adaccounts" / "other.json").symlink_to(outside)
    with pytest.raises(IsolationError, match="unsafe_report_tree") as exc:
        validate_environment(str(dedicated), {"demo-ads": "project"})
    assert "SECRET_CANARY" not in str(exc.value)


def test_広告の状態の置き場のsymlinkは断る(dedicated, tmp_path):
    _adledger(dedicated)
    state = dedicated / "adaccount_state" / "demo-ads"
    state.mkdir(parents=True)
    target = tmp_path / "elsewhere"
    target.mkdir()
    (state / "guard").symlink_to(target)
    with pytest.raises(IsolationError, match="unsafe_report_tree"):
        validate_environment(str(dedicated), {"demo-ads": "project"})


def test_広告の台帳のprojectが行と違えばaccount_scope_mismatch(dedicated):
    _adledger(dedicated, project="other")
    with pytest.raises(IsolationError, match="account_scope_mismatch"):
        validate_environment(str(dedicated), {"demo-ads": "project"})


def test_両方の置き場にある名前は断る(dedicated):
    _adledger(dedicated, name="demo")
    with pytest.raises(IsolationError, match="account_registry_ambiguous"):
        validate_environment(str(dedicated), {"demo": "project"})
    with pytest.raises(accounts.AccountError, match="account_registry_ambiguous"):
        accounts.ledger_kind("demo")


def test_tokenがrootの外ならresource_outside_root(dedicated, tmp_path):
    _adledger(dedicated, token=str(tmp_path / "other-tenant.token"))
    with pytest.raises(IsolationError, match="resource_outside_root"):
        validate_environment(str(dedicated), {"demo-ads": "project"})


def test_どちらの置き場にも無い名前は今までどおり断る(dedicated):
    with pytest.raises(IsolationError, match="invalid_report_environment"):
        validate_environment(str(dedicated), {"nobody": "project"})
    assert accounts.ledger_kind("nobody") is None
    assert accounts.ledger_kind("demo") == "account"
