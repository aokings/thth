"""Copy already recorded recent daily context into a new observation only."""
import datetime
import json
import math
import os
import re
import stat
from pathlib import Path
from . import jst


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate context key')
        result[key] = value
    return result


def followers(account, account_dir, now):
    now=jst.to_jst(now)
    # Filenames use the daily row's date, not its collection timestamp. Scan
    # account-specific monthly files so a month boundary cannot hide recent data.
    rows = []
    try:
        paths = sorted(path for path in Path(account_dir).iterdir()
                       if re.fullmatch(re.escape(account) + r'-\d{4}-\d{2}\.ndjson', path.name))
        for path in paths:
            fd=os.open(path,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
            with os.fdopen(fd,'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):return None
                data=stream.read(1024*1024+1)
            if len(data)>1024*1024:return None
            rows.extend(json.loads(line, object_pairs_hook=_unique_object)
                        for line in data.splitlines() if line.strip())
    except (OSError,ValueError,UnicodeError,TypeError,RecursionError):
        return None
    candidates=[]
    for row in rows:
        if not isinstance(row,dict):return None
        if row.get('account')!=account:continue
        try:at=jst.parse(row.get('collected_at'))
        except (ValueError,TypeError,OverflowError):return None
        if at is None:return None
        if at>now or now-at>datetime.timedelta(hours=48):continue
        metrics=row.get('metrics')
        value=metrics.get('followers_count') if isinstance(metrics,dict) else None
        try:
            valid=type(value) in (int,float) and value>=0 and math.isfinite(value) and int(value)==value
        except (OverflowError,ValueError):valid=False
        candidates.append((at,value if valid else None))
    if not candidates:return None
    latest=max(at for at,_ in candidates)
    values={value for at,value in candidates if at==latest}
    if len(values)!=1 or None in values:return None
    return {'followers_count':values.pop(),'followers_count_at':jst.iso(latest),
            'staleness_hours':round((now-latest).total_seconds()/3600,2), 'source':'insights_account'}
