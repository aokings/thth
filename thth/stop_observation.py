"""Static stop diagnostics; never repair permissions or bypass a stop gate."""
import os
from pathlib import Path
import stat
from . import accounts,leave_gate


def reason(exc):
    value=str(exc)
    return value if value in ('account_stopped','account_stop_state_unreadable','account_leaving') else 'account_stopped'


def directories():
    root=Path(accounts.thth_root())
    paths={'state':root/'state','repos':root/'repos','accounts':Path(accounts.accounts_dir())}
    rows=[]
    for name,path in paths.items():
        row={'directory':name,'present':None,'mode':None,'warning':None}
        try:
            info=path.lstat();row.update(present=True,mode=format(stat.S_IMODE(info.st_mode),'04o'))
            if not stat.S_ISDIR(info.st_mode):row['warning']='unsafe_directory_type'
            elif info.st_uid!=os.getuid():row['warning']='unsafe_directory_owner'
            elif info.st_mode&0o022:row['warning']='unsafe_directory_mode'
        except FileNotFoundError:row['present']=False
        except OSError:row['warning']='directory_unreadable'
        rows.append(row)
    return rows


def diagnostic(account):
    try:accounts.validate_name(account)
    except accounts.AccountError:
        return {'account':None,'error':'invalid_account_name','cannot_say':['invalid_account_name'],
                'message':'アカウント名に使えない字が入っています。英数字と _・.・- で指定してください。',
                'probes':[],'directory_checks':[]}
    rows=directories();code=None
    try:leave_gate.require_active(account)
    except accounts.AccountStopped as exc:code=reason(exc)
    if code is None:code=next((r['warning'] for r in rows if r['warning']),None)
    return {'account':account,'error':code,'cannot_say':[code] if code else [],'probes':[],
            'directory_checks':rows}
