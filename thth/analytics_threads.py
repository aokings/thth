"""Owned engagement outcomes. Raw reply bodies and handles never leave this module."""
import collections
import re
from . import analytics_comparison as c, replies, threadshape, jst, after_cli
from .adapters.base import author_key


def reaction(name, medium, engagement, posted, now):
    try:
        ledger = replies.load(name, post_id=engagement['post_id'])
    except (OSError, ValueError, TypeError, AttributeError):
        return None, None
    if ledger.get('broken'):
        return None, None
    fetched = any(posted <= at <= now and f.get('post_id') == engagement['post_id'] and f.get('id_missing', 0) == 0
                  for f in ledger['fetches'] if (at := c._timestamp(f.get('collected_at'))) is not None)
    direct = []
    uncertain = bool(ledger.get('unreadable_accounts')) or any(
        f.get('post_id') == engagement['post_id'] and
        (type(f.get('id_missing', 0)) is not int or f.get('id_missing', 0) != 0)
        for f in ledger['fetches'])
    for row in ledger['replies']:
        if row.get('post_id') != engagement['post_id']:
            uncertain = True
            continue
        collected = c._timestamp(row.get('collected_at'))
        at = c._timestamp(row.get('timestamp'))
        if collected is None or collected > now or at is None or not posted <= at <= collected:
            uncertain = True
            continue
        if threadshape._ref_id(row.get('replied_to')) is None:
            uncertain = True
        if threadshape._ref_id(row.get('replied_to')) != str(engagement['post_id']):
            continue
        if row.get('own') is None:
            uncertain = True
        elif row.get('own') is False:
            key = row.get('author_key') or author_key(medium, row.get('username'))
            key = key if isinstance(key, str) and re.fullmatch(r'[0-9a-f]{16}', key) else None
            direct.append((at, key))
            uncertain |= key is None
    key = engagement.get('author_key')
    key = key if isinstance(key, str) and re.fullmatch(r'[0-9a-f]{16}', key) else None
    matched = bool(key) and any(k == key for _, k in direct)
    returned = True if matched else False if fetched and key and not uncertain else None
    return returned, min(((at-posted).total_seconds()/3600 for at, _ in direct), default=None)


def summarize(name, cfg, eng, by_id, start, end, now, min_n):
    grouped = collections.defaultdict(list)
    for row in eng['rows']:
        if isinstance(row, dict) and row.get('account') == name and row.get('post_id'):
            grouped[str(row['post_id'])].append(row)
    branches = collections.defaultdict(list)
    for pid, rows in grouped.items():
        # Duplicate conflicting declarations are not merged into invented evidence.
        row = rows[0]
        if any(any(r.get(k) != row.get(k) for k in ('posted_at','root_post','reply_to','author_key','topic','form','hour_band')) for r in rows):
            continue
        posted = c._timestamp(row.get('posted_at'))
        post = by_id.get(pid)
        if posted is None or not start <= posted < end:
            continue
        if post and c._timestamp(post.get('posted_at')) != posted:
            continue
        curve = c._marks_population([(pid, posted, post)], start, end, now, min_n)['marks_by_post'][0]
        back, first = reaction(name, cfg.get('media'), row, posted, now)
        observation = curve['marks'].get('24')
        topic, valid = after_cli._normalized_topic(row.get('topic'))
        root = row.get('root_post')
        root = root if isinstance(root, str) and root else None
        branches[(root, pid if root is None else '')].append({**curve, 'reply_to': row.get('reply_to'),
            'author_key': row.get('author_key') if isinstance(row.get('author_key'), str) and re.fullmatch(r'[0-9a-f]{16}', row['author_key']) else None, 'topic': topic if valid else None,
            'form': row.get('form'), 'hour_band': row.get('hour_band'),
            'metrics_24h': observation['metrics'] if observation else dict.fromkeys(c.METRICS),
            'author_replied_back': back, 'first_reaction_hours': first,
            'reaction_basis': 'observed_other_direct_reply_timestamp',
            'author_basis': 'reply_to_author_key'})
    result = [{'root_post': root[0], 'missing_reason': 'root_post_unknown' if root[0] is None else None, 'replies': sorted(rows, key=lambda x: (x['posted_at'], x['post_id']))}
              for root, rows in sorted(branches.items(), key=lambda item: str(item[0]))]
    strata = {}
    flat = [r for b in result for r in b['replies']]
    for field in ('topic','form','hour_band'):
        groups = collections.defaultdict(list)
        for row in flat:
            value = row.get(field)
            groups[value if isinstance(value, str) and value else 'unknown'].append(row)
        strata[field] = {}
        for value, rows in sorted(groups.items()):
            metrics = {}
            for metric in c.METRICS:
                values = [r['metrics_24h'][metric] for r in rows if r['metrics_24h'][metric] is not None]
                metrics[metric] = {'n_eligible': len(values), 'median': c._median(values) if len(values)>=min_n else None, **c._spread(values,min_n)}
            strata[field][value] = {'n':len(rows), 'metrics': metrics}
    return {'by_thread': result, 'by_thread_strata': strata}
