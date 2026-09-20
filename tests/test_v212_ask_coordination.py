"""The ask snapshot exception rejects every nonempty or unsafe inode."""
import os
from pathlib import Path
import pytest
from tests.test_ask import _exclude_new_coordination


@pytest.fixture
def coordination(tmp_path,monkeypatch):
    root=tmp_path/'root';root.mkdir();monkeypatch.setenv('THTH_ROOT',str(root))
    state=root/'state';state.mkdir(mode=0o700)
    directory=state/'_leave';directory.mkdir(mode=0o700)
    leaf=directory/'alpha.lock';leaf.write_bytes(b'');leaf.chmod(0o600)
    snapshot={'state/':'dir','state/_leave/':'dir','state/_leave/alpha.lock':'fingerprint'}
    return root,leaf,snapshot


def test_only_exact_empty_private_coordination_is_excluded(coordination):
    root,leaf,snapshot=coordination
    assert _exclude_new_coordination(root,'alpha',{},snapshot)=={}
    assert snapshot['state/_leave/alpha.lock']=='fingerprint' # Input is not changed.


@pytest.mark.parametrize('change',['body','mode','hardlink','symlink','fifo','owner','other-account',
    'parent-mode','state-mode','parent-symlink','state-symlink','extra-leaf','extra-directory','extra-state'])
def test_ask_snapshot_exception_refuses_unsafe_or_extra_changes(coordination,tmp_path,monkeypatch,change):
    root,leaf,snapshot=coordination
    if change=='body':leaf.write_bytes(b'synthetic-content')
    elif change=='mode':leaf.chmod(0o644)
    elif change=='hardlink':os.link(leaf,tmp_path/'alias')
    elif change=='symlink':
        outside=tmp_path/'outside';outside.write_bytes(b'');outside.chmod(0o600)
        leaf.unlink();leaf.symlink_to(outside)
    elif change=='fifo':leaf.unlink();os.mkfifo(leaf,0o600)
    elif change=='owner':monkeypatch.setattr(os,'getuid',lambda:leaf.stat().st_uid+1)
    elif change=='other-account':leaf.rename(leaf.with_name('beta.lock'))
    elif change=='parent-mode':leaf.parent.chmod(0o755)
    elif change=='state-mode':leaf.parent.parent.chmod(0o755)
    elif change in ('parent-symlink','state-symlink'):
        target=leaf.parent if change=='parent-symlink' else leaf.parent.parent
        moved=tmp_path/'moved';target.rename(moved);target.symlink_to(moved,target_is_directory=True)
    elif change=='extra-leaf':(leaf.parent/'other.lock').write_bytes(b'')
    elif change=='extra-directory':(leaf.parent/'other').mkdir()
    elif change=='extra-state':(leaf.parent.parent/'sns').mkdir()
    with pytest.raises((AssertionError,FileNotFoundError)):
        _exclude_new_coordination(root,'alpha',{},snapshot)
