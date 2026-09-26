"""VM の常駐（`thth worker`）: 招待の完了と /activity を回す。

設計 3.13.0 で承認ページは無くなった。承認 job を作る道も、Worker に承認を問い合わせる道も
無い。この常駐に残っているのは:

  - 招待の完了を拾う（`invites.run_once`）
  - 持ち主ごとの要約を Worker へ押し上げ、/activity で頼まれた操作を行う（`activity.run_once`）。
    3.14.0: 同じ sync で鍵の表を押し上げ、thth.me/api/v1 の依頼を行う（`api_requests`・待ちがあれば 2 秒ごと）
  - ディスクの版が動いたら自分で終わる（`worker_version`・systemd が新しい版で起こし直す）

3.13.0 で `approval_jobs`・`thth approval-worker`・`thth-approval-worker.service` から改名した。
旧名の `thth approval-worker` は alias として残す（deprecated・3.12.0 以前の unit がそれで起こす）。

3.12.0 までに作られた承認 job（`state/<口座>/approval-jobs/*.json`）は、起動時に
`status: expired` に書き換えて残す（消さない・記録として）。
"""
import json
import os
import time
from pathlib import Path
from . import accounts, relay, server_files

TERMINAL = frozenset(('completed','failed','unknown','expired'))
# 3.13.0 で承認ページを外したので期限切れにした、の印。
REMOVED_REASON = 'approval_page_removed'


def directory(account):
    """3.12.0 までの承認 job の置き場（読むのは起動時の `expire_leftover_jobs` だけ）。"""
    if not accounts.name_is_safe(account): raise ValueError('invalid_account')
    return Path(accounts.state_dir_for(account))/'approval-jobs'


def expire_leftover_jobs():
    """残っている承認 job のうち終わっていないものを `expired` にする。戻り値は書き換えた数。

    消さない。読めないものは触らない（記録として残す）。何度呼んでも同じ結果。
    """
    changed=0
    for account in accounts.list_account_names():
        try:
            path=directory(account)
            if not path.is_dir(): continue
            with server_files.directory(path,private=True) as fd:
                names=sorted(os.listdir(fd))
                for name in names:
                    if not name.endswith('.json') or not relay.OPAQUE.fullmatch(name[:-5]): continue
                    try:
                        with server_files.lock_at(fd,name[:-5]+'.lock'):
                            raw=server_files.read_at(fd,name,private=True)
                            job=json.loads(raw)
                            if type(job) is not dict or job.get('status') in TERMINAL: continue
                            job.update(status='expired',reason=REMOVED_REASON)
                            server_files.replace_at(fd,name,server_files.encode(job),expected=raw,private=True)
                            changed+=1
                    except (OSError,ValueError,RecursionError):
                        continue
        except (OSError,ValueError):
            continue
    return changed


def run_once(credentials_path):
    from .report_http import load_credentials
    root, credentials=load_credentials(Path(credentials_path))
    from .report_isolation import validate_environment
    for item in credentials:
        validate_environment(root,item[3].allowed_accounts,allow_unreadable=item[3].scope=='admin',allow_empty=True)
    # 招待リンク（設計 3.10.0）: 押された招待を拾い、認可の完了で口座を用意する。
    # 同じ常駐に載せる（Worker を定期に見ている process は 1 つにする）。
    from . import invites
    invites.run_once(credentials_path)
    # 動きの一覧（設計 3.12.0 §3.4）: 持ち主ごとの要約を Worker へ押し上げ、/activity で頼まれた操作を行う。
    from . import activity
    activity.run_once(credentials_path)


def command(args):
    import sys
    # 3.12.0 §6-6: ssh で手で打っても（umask 0002）state に 775/664 を作らない。unit の UMask=0077 に頼らない。
    os.umask(0o077)
    # 3.12.0 §6-1: 読み込んだ版を残し、ディスクの版が動いたら終わる（systemd が新しい版で起こし直す）。
    from . import worker_version
    start=worker_version.loaded()
    if not args.once: worker_version.record_start(start)
    checked=time.monotonic()
    try:
        # 3.13.0: 承認ページは無い。残っている承認 job は期限切れにして残す。
        expire_leftover_jobs()
        while True:
            run_once(args.credentials)
            if args.once: return 0
            if time.monotonic()-checked>=worker_version.CHECK_SECONDS:
                checked=time.monotonic();disk=worker_version.on_disk()
                if worker_version.moved(start,disk):
                    print('worker_restart: '+worker_version.describe(start)+' -> '+worker_version.describe(disk),file=sys.stderr)
                    return worker_version.EXIT_MOVED
            time.sleep(2)
    except KeyboardInterrupt: return 0
    except (OSError,ValueError):
        print('worker_unavailable',file=sys.stderr);return 2


# 旧名（deprecated）。3.12.0 以前の unit（thth-approval-worker.service）はこれで起こす。
DEPRECATED_NAME = 'approval-worker'


def register(sub):
    for name,text in (('worker','招待の完了・/activity の要約と操作・安全装置を回す常駐（管理者の常駐 process）'),
                      (DEPRECATED_NAME,'旧名（deprecated）: thth worker と同じ')):
        parser=sub.add_parser(name,help=text)
        parser.add_argument('--credentials',required=True)
        parser.add_argument('--once',action='store_true',help='1 巡だけ検査する')
        parser.set_defaults(func=command)
