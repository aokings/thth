"""Historical shapes bounded by when replies were actually collected."""
import datetime
from . import analytics_comparison as c, replies, threadshape, jst


def shape_at(name, post_id, posted, now):
    out = dict.fromkeys(('24','72','168'))
    try:
        ledger = replies.load(name, post_id=post_id)
    except (OSError, ValueError, TypeError, AttributeError):
        return out
    if ledger.get('broken'):
        return out
    for mark in (24,72,168):
        cutoff = posted + datetime.timedelta(hours=mark)
        if cutoff > now:
            continue
        fetches = [f for f in ledger['fetches'] if f.get('post_id') == post_id
                   and (at := c._timestamp(f.get('collected_at'))) is not None and posted <= at <= cutoff
                   and f.get('id_missing',0) == 0]
        if not fetches:
            continue
        observed = max(c._timestamp(f['collected_at']) for f in fetches)
        candidates = [r for r in ledger['replies'] if r.get('post_id') == post_id
                      and (at := c._timestamp(r.get('collected_at'))) is not None and posted <= at <= cutoff
                      and (ts := c._timestamp(r.get('timestamp'))) is not None and posted <= ts <= at]
        # Earliest known version wins; a later duplicate cannot rewrite history.
        candidates.sort(key=lambda r: (c._timestamp(r['collected_at']), str(r.get('id',''))))
        tree = threadshape._tree(post_id, candidates)
        rows = [node['row'] for node in tree['nodes'].values()]
        others = [r for r in rows if r.get('own') is False]
        participants = threadshape._participants(others)
        depths = [n['depth'] for n in tree['nodes'].values() if n['depth'] is not None]
        out[str(mark)] = {'cutoff': jst.iso(cutoff), 'last_observed_at': jst.iso(observed),
            'basis':'collected_at_at_or_before_cutoff',
            'branches': sum(n['depth']==1 for n in tree['nodes'].values()),
            'depth':max(depths, default=0), 'replies_total':len(rows),
            'author_replies':sum(r.get('own') is True for r in rows),
            'other_replies':len(others), 'own_unknown':sum(r.get('own') is None for r in rows),
            'participants': {k:v for k,v in participants.items() if k != 'top_share'},
            'first_reply_min': min(((c._timestamp(r['timestamp'])-posted).total_seconds()/60 for r in others), default=None),
            'author_reply_effect':threadshape._author_reply_effect(post_id,tree),
            'orphan_replies':len(tree['orphans']), 'duplicate_reply_ids':len(tree['duplicate_ids'])}
    return out


def attach(name, population, now):
    for post in population.get('marks_by_post', []):
        post['shape_at'] = shape_at(name, post['post_id'], c._timestamp(post['posted_at']), now)
