"""CLI read boundaries: no account/repo flock, only nonwaiting cleanup leases."""
import contextlib
import json
from pathlib import Path
import sys
from . import accounts, leave_gate

READS = frozenset(('account','posts','replies','measured','threads','topics','forms',
    'queue','schedule','handoff-report','board','doctor','where','thread','mentions',
    'profile','who','location','after','analytics-report','study-report','unanswered',
    'ask','lint','preview','systemd','topic-read'))


def names(args, command):
    account=getattr(args,'account',None)
    if command=='topic-read' and getattr(args,'sub',None) in ('decision','review'):
        account=getattr(args,'decision_id',None) or getattr(args,'target',None)
    if accounts.name_is_safe(account):return [account]
    project=getattr(args,'project',None)
    if command in ('where','who') and not project:
        targets=getattr(args,'targets',None)
        return [targets[0]] if targets and accounts.name_is_safe(targets[0]) else []
    if command=='study-report':
        from . import study_report,jst
        try:return [study_report.load_declaration(args.file,jst.now_jst())['account']]
        except study_report.StudyError:return [] # Keep the existing bounded input diagnostic.
    if command in ('lint','preview') or command=='topic-read' and getattr(args,'sub',None)=='suggest':
        from . import cli,queuefile
        result=[]
        values=args.file if isinstance(args.file,list) else [args.file]
        for value in values:
            try:
                path=Path(cli._resolve_repo_path(value))
                paths=path.rglob('*.md') if path.is_dir() else [path]
                for leaf in paths:
                    name=queuefile.parse(str(leaf)).front_matter.get('account')
                    if accounts.name_is_safe(name):result.append(name)
            except (OSError,ValueError):pass
        return result
    if project or command in ('account','queue','board','systemd'):
        result=[];excluded=[]
        for name in accounts.list_account_names():
            if project:
                try:
                    if accounts.load_account(name).get('project')!=project:continue
                except accounts.AccountStopped:
                    from .report_isolation import read_registry_ledger,registry_project
                    try:raw=read_registry_ledger(Path(accounts.accounts_dir())/(name+'.json'))
                    except (OSError,ValueError):continue
                    if registry_project(raw)==project:excluded.append(name)
                    continue
                except accounts.AccountError:continue
            elif leave_gate.stopped(name):
                excluded.append(name);continue # Historical diagnostics remain in admin reads.
            result.append(name)
        if not result:
            for name in excluded:leave_gate.check_busy(name)
        return result
    return []


def refusal(args, reason):
    print(reason,file=sys.stderr)
    if getattr(args,'json',False) or getattr(args,'as_json',False):print(json.dumps({'error':reason,'cannot_say':[reason]}))
    return 2


def invoke(args, command, call=None):
    if command=='send' and call is None:return args.func(args)
    call=call or (lambda:args.func(args))
    # account subcommands change state; send reads its input before calling here.
    if command not in READS and command!='send':return call()
    if command=='topic-read' and getattr(args,'sub',None) not in ('suggest','decision','review','improvements','impact','form-check'):return call()
    if command=='account' and getattr(args,'account',None) in ('add','migrate','leave'):return call()
    if command in ('replies','unanswered') and getattr(args,'refresh',False):return call()
    if command=='send' and getattr(args,'production',False):return call()
    try:selected=names(args,command)
    except accounts.AccountLeaving:return refusal(args,'account_leaving')
    except (accounts.AccountError,OSError,ValueError):return call()
    stack=contextlib.ExitStack()
    try:stack.enter_context(leave_gate.read_leases(selected))
    except accounts.AccountLeaving:return refusal(args,'account_leaving')
    except accounts.AccountStopped:return refusal(args,'account_stopped')
    except (OSError,ValueError):return refusal(args,'account_stop_state_unreadable')
    with stack:
        try:return call()
        except accounts.AccountLeaving:return refusal(args,'account_leaving')
