"""Distinguish only the empty, owner-private stable account coordination inode."""
import os
from pathlib import Path
import stat
from thth import accounts


def account_coordination(path,account):
    expected=Path(accounts.thth_root())/'state'/'_leave'/(account+'.lock')
    if path.absolute()!=expected.absolute():return False
    info=path.lstat()
    assert stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_uid==os.getuid()
    assert stat.S_IMODE(info.st_mode)==0o600 and path.read_bytes()==b''
    return True
