"""Unanswered replies grounded in owned local roots; answer() is pure."""
import json
import sys
from pathlib import Path
from . import accounts, bundle, collect, engagements, jst, queuefile, replies, runs, sent
from . import read_window, lock
from .adapters import base
from .threads_read_cli import _one_line


def _id(value):
    return value if isinstance(value, str) and value.strip() else None


def _relations(name, cfg, now):
    relations, reasons = {}, set()

    def accept(row, *, timestamp):
        pid = _id(row.get('post_id'))
        if not pid:
            return
        at = jst.parse(row.get(timestamp))
        if at is None or at > now:
            reasons.add('own_post_time_unknown')
            return
        if 'reply_to' not in row:
            reasons.add('own_post_parent_unknown')
            return
        parent = row['reply_to']
        if parent not in (None, '') and _id(parent) is None:
            reasons.add('own_post_parent_unknown')
            return
        relations.setdefault(pid, set()).add(parent or None)

    errors = []
    for row in sent.records(accounts.state_dir_for(name), errors=errors):
        accept(row, timestamp='sent_at')
    if errors:
        reasons.add('sent_unreadable')
    repo = cfg.get('repo_dir')
    if repo and Path(repo).name != accounts.REPO_NONE_BASENAME:
        directory = Path(repo) / cfg['queue_dir']
        try:
            entries = sorted(directory.glob('*.md')) if directory.is_dir() else []
            if not directory.is_dir():reasons.add('queue_unavailable')
            for path in entries:
                try:
                    text = path.read_text(encoding='utf-8')
                    if bundle.is_bundle_text(text):
                        value = bundle.parse_text(text, str(path))
                        if value.malformed:
                            reasons.add('queue_unreadable');continue
                        if value.front_matter.get('account') != name:continue
                        if value.front_matter.get('status') not in ('approved', 'posted'):
                            reasons.add('queue_publication_not_recorded');continue
                        for row in value.posts:
                            # Missing bundle reply_to is unknown, never a root.
                            normalized = {k: bundle.unquote(v) for k, v in row.items()}
                            accept(normalized, timestamp='posted_at')
                        if not any(_id(p.get('post_id')) for p in value.posts):
                            reasons.add('bundle_publication_not_recorded')
                    else:
                        value = queuefile.parse_text(text, str(path))
                        if value.malformed:
                            reasons.add('queue_unreadable');continue
                        if value.front_matter.get('account') == name:
                            if value.front_matter.get('status') != 'posted':
                                if value.front_matter.get('post_id'):reasons.add('queue_publication_not_recorded')
                                continue
                            accept(value.front_matter, timestamp='posted_at')
                except (OSError, ValueError, TypeError, AttributeError):
                    reasons.add('queue_unreadable')
        except OSError:
            reasons.add('queue_unreadable')
    ledger = engagements.load(cfg, name)
    if ledger['broken']:reasons.add('engagements_unreadable')
    for row in ledger['rows']:
        if row.get('account') == name and row.get('medium') == cfg['media']:
            accept(row, timestamp='posted_at')
    for pid, parents in relations.items():
        if len(parents)>1:reasons.add('own_post_parent_conflict')
    roots = {pid for pid, parents in relations.items() if parents == {None}}
    answered = {next(iter(parents)) for parents in relations.values()
                if len(parents)==1 and None not in parents}
    return roots, answered, reasons


def answer(account_name, *, since='7d', now=None):
    now = now or jst.now_jst()
    floor = read_window.cutoff(since, now=now)
    cfg = accounts.load_account(account_name)
    roots, answered, reasons = _relations(account_name, cfg, now)
    try:
        data = replies.load(account_name)
    except (OSError, ValueError, TypeError, AttributeError):
        data = {'replies': [], 'fetches': [], 'broken': ['unreadable'], 'unreadable_accounts': []}
    if data['broken']:reasons.add('replies_unreadable')
    if data['unreadable_accounts']:reasons.add('reply_ownership_unknown')
    # Shared repositories may contain identical platform-local ids. Metadata
    # can disambiguate; an old row without it cannot choose between owners.
    owners = {pid: {(account_name, cfg['media'])} for pid in roots}
    directory = Path(accounts.data_dirs(cfg, account_name)['replies']).resolve()
    for other in accounts.list_account_names():
        if other == account_name:continue
        try:
            other_cfg = accounts.load_account(other)
            if Path(accounts.data_dirs(other_cfg, other)['replies']).resolve() != directory:continue
            other_roots, _, _ = _relations(other, other_cfg, now)
            for pid in roots & other_roots:owners[pid].add((other, other_cfg['media']))
        except (accounts.AccountError, OSError, ValueError, TypeError, KeyError):
            reasons.add('reply_ownership_unknown')

    def belongs(row):
        pid = _id(row.get('post_id'))
        if pid not in roots:
            reasons.add('unproven_roots_excluded');return False
        if ('account' in row and row['account'] != account_name
                or 'medium' in row and row['medium'] != cfg['media']):
            reasons.add('foreign_reply_evidence_excluded');return False
        candidates = {(name, medium) for name, medium in owners[pid]
                      if ('account' not in row or row['account'] == name)
                      and ('medium' not in row or row['medium'] == medium)}
        if candidates != {(account_name, cfg['media'])}:
            reasons.add('reply_scope_ambiguous');return False
        return True

    selected = [row for row in data['replies'] if belongs(row)]
    # Exact parent edges only: replying elsewhere under this root proves nothing
    # about whether a different participant's reply received an answer.
    for row in selected:
        if (row.get('own') is True and replies._normalize_handle(row.get('username'))
                == replies._normalize_handle(cfg.get('handle'))):
            message = base.normalize_message(row, medium=cfg['media'])
            parent = _id(message.get('replied_to'))
            if parent:answered.add(parent)
            else:reasons.add('own_reply_parent_unknown')
    rows, seen = [], set()
    for row in selected:
        if row.get('own') is not False:
            if row.get('own') is None:reasons.add('reply_ownership_unknown')
            continue
        if not isinstance(row.get('username'), str):
            reasons.add('reply_ownership_unknown');continue
        message = base.normalize_message(row, medium=cfg['media'])
        rid = _id(message.get('message_id'))
        if not rid:
            reasons.add('reply_id_unknown');continue
        key = (row['post_id'], rid)
        if rid in answered or key in seen:continue
        seen.add(key)
        at = jst.parse(message.get('timestamp'))
        if at is None:
            reasons.add('reply_time_unknown');continue
        if at > now or floor and at < floor:continue
        rows.append(dict(post_id=row['post_id'], reply_id=rid,
                         author_key=_id(message.get('author_key')), username=message.get('username'),
                         preview=_one_line(message.get('text')),
                         age_hours=round((now-at).total_seconds()/3600, 3), verified='ledger_only'))
    observed = {}
    for row in data['fetches']:
        pid=_id(row.get('post_id'));at=jst.parse(row.get('collected_at'))
        if belongs(row) and at is not None and at <= now:
            observed[pid]=max(observed.get(pid, at), at)
    stale = max(((now-at).total_seconds()/3600 for at in observed.values()), default=None)
    if roots-set(observed):reasons.add('collection_not_recorded')
    if stale is not None and stale>24:reasons.add('collection_stale_hours')
    rows.sort(key=lambda row: (-row['age_hours'], row['reply_id']))
    return dict(account=account_name, n_total=len(rows), replies=rows,
                window=dict(since=jst.iso(floor) if floor else None, until=jst.iso(now), basis='reply_timestamp'),
                collection_stale_hours=round(stale, 3) if stale is not None else None,
                cannot_say=sorted(reasons),
                summary=dict(n=len(rows), oldest_age_hours=max((r['age_hours'] for r in rows), default=None)))


def cmd(args):
    try:
        read_window.cutoff(args.since)
    except ValueError as exc:
        print(str(exc), file=sys.stderr);return 2
    refresh = None
    if args.refresh:
        refresh = collect.refresh_replies(args.account, wait=args.wait,
                    log=lambda line:print(line,file=sys.stderr))
    try:
        result=answer(args.account,since=args.since)
    except (accounts.AccountError, ValueError) as exc:
        print(str(exc),file=sys.stderr);return 2
    if refresh is not None:result['refresh']=refresh
    rc=1 if refresh and (refresh.get('skipped') or refresh.get('failed') or refresh.get('errors')) else 0
    runs.record_minimal(args.account, dict(account=args.account, action='unanswered', n=result['n_total'],
                        status='ok' if rc==0 else 'error', error=None if rc==0 else 'refresh_failed'))
    if args.json:print(json.dumps(result,ensure_ascii=False,indent=2))
    else:
        print(f"{args.account}: 台帳で未回答 {result['n_total']} 件")
        for row in result['replies']:
            print(f"  {row['age_hours']:.1f} 時間前 @{row['username'] or '—'} {row['reply_id']} {row['preview']}")
            print(f"    根: {row['post_id']}（ledger_only）")
        if result['cannot_say']:print('確認できないこと: '+', '.join(result['cannot_say']))
    return rc


def register(sub):
    parser=sub.add_parser('unanswered',help='自分の根投稿の未回答返信を台帳から読む')
    parser.add_argument('account')
    parser.add_argument('--since',default='7d')
    parser.add_argument('--json',action='store_true')
    parser.add_argument('--refresh',action='store_true')
    parser.add_argument('--wait',type=lock.wait_seconds,default=0)
    parser.set_defaults(func=cmd)
