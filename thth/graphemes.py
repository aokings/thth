"""Unicode 17.0.0 extended grapheme boundaries (UAX #29 revision 47).

Only the default extended-cluster algorithm, not normalization or collation.
Fixed property tables make behavior independent of Python's unicodedata version.
Each input code point is visited once, with fixed-size state and table searches.
Existing Bluesky count() remains its separately documented approximation.
"""
from bisect import bisect_right
from . import _grapheme_data as data

UNICODE_VERSION = data.UNICODE_VERSION
_TABLES = tuple((rows,tuple(row[0] for row in rows)) for rows in (data.GCB,data.INCB,data.EP))


def _property(code,table,default):
    rows,starts=_TABLES[table];index=bisect_right(starts,code)-1
    return rows[index][2] if index>=0 and code<=rows[index][1] else default


def boundaries(text):
    """Yield Python string offsets, including 0 and len(text), without a copy."""
    if not isinstance(text,str):raise TypeError('graphemes requires str')
    yield 0
    previous=None;regional=0;ep_extend=False;zwj_after_ep=False;indic=0
    controls={'CR','LF','Control'}
    for index,char in enumerate(text):
        code=ord(char);kind=_property(code,0,'Other');incb=_property(code,1,'None');ep=_property(code,2,None) is not None
        if previous is not None:
            if previous=='CR' and kind=='LF':split=False  # GB3
            elif previous in controls or kind in controls:split=True  # GB4/5
            elif previous=='L' and kind in ('L','V','LV','LVT'):split=False  # GB6
            elif previous in ('LV','V') and kind in ('V','T'):split=False  # GB7
            elif previous in ('LVT','T') and kind=='T':split=False  # GB8
            elif kind in ('Extend','ZWJ','SpacingMark') or previous=='Prepend':split=False  # GB9/9a/9b
            elif incb=='Consonant' and indic==2:split=False  # GB9c
            elif ep and previous=='ZWJ' and zwj_after_ep:split=False  # GB11
            elif previous=='Regional_Indicator' and kind=='Regional_Indicator' and regional%2:split=False  # GB12/13
            else:split=True  # GB999
            if split:yield index
        next_zwj=kind=='ZWJ' and ep_extend
        ep_extend=ep or (kind=='Extend' and ep_extend)
        zwj_after_ep=next_zwj
        if incb=='Consonant':indic=1
        elif incb=='Linker' and indic:indic=2
        elif incb!='Extend':indic=0
        regional=regional+1 if kind=='Regional_Indicator' else 0
        previous=kind
    if text:yield len(text)


def count(text,*,stop_after=None):
    """Count clusters; optional cap returns cap+1 as soon as that is certain."""
    total=-1
    for _ in boundaries(text):
        total+=1
        if stop_after is not None and total>stop_after:return total
    return total
