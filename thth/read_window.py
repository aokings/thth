"""Shared read-window parsing; no storage or remote calls."""
import datetime
import re
from . import jst


def cutoff(value, *, now=None):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('since は期間（7d・24h・2w）か ISO 時刻です')
    match = re.fullmatch(r'([1-9][0-9]*)([hdw])', value)
    if match:
        hours = int(match[1]) * {'h': 1, 'd': 24, 'w': 168}[match[2]]
        try:
            return (now or jst.now_jst()) - datetime.timedelta(hours=hours)
        except OverflowError:
            raise ValueError('since の期間が大きすぎます') from None
    try:
        parsed = jst.parse(value)
    except (ValueError, TypeError, OverflowError):
        parsed = None
    if parsed is None:
        raise ValueError('since は期間（7d・24h・2w）か ISO 時刻です')
    return parsed
