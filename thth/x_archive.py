"""X の全期間の検索の小さな部品（4.5.0・設計 `docs/設計_4.5.0_Xの全期間の検索_2026-10-21.md`）。

ここに置くのは 3 つだけ:

1. **人の居ない道の印**（`unattended()`）。timer・collect・morning・maintain・worker・map の
   命令は入口（`cli._main`）でこの印を立てる。全期間の読み取り（`XAdapter.archive_search`・
   `archive_counts`）は印が立っている間は**要求も予約もせずに**断る（`x_archive_unattended`）。
   印は process 全体の数（thread をまたいでも効く）。
2. **語の組み立て**（`build_query(handle, title)`）: `from:<handle> "<曲名>" -is:retweet`。
3. **時刻**（`utc_bound()`）: `--since`／`--until` を X の `start_time`／`end_time` の形に。
"""
from __future__ import annotations

import contextlib
import datetime
import math
import re
import threading

from . import jst

# timer・自動の経路の命令（`cli._main` の先頭の語）。`map` は `map collect`（timer）を含むので全体。
UNATTENDED_COMMANDS = frozenset({"run", "collect", "maintain", "morning", "observe", "worker", "map", "hatena"})

_lock = threading.Lock()
_depth = 0


@contextlib.contextmanager
def unattended():
    """この中では全期間の読み取りを断る（入れ子可・thread をまたいで効く）。"""
    global _depth
    with _lock:
        _depth += 1
    try:
        yield
    finally:
        with _lock:
            _depth -= 1


def is_unattended() -> bool:
    return _depth > 0


def guard_for(command: str):
    """`cli._main` の 1 行: 自動の経路の命令なら印を立てる文脈、ほかは何もしない文脈。"""
    return unattended() if command in UNATTENDED_COMMANDS else contextlib.nullcontext()


# ----- 語 ---------------------------------------------------------------------

QUERY_MAX = 1024          # 従量の契約の語の長さ（一次資料 search introduction「1,024 characters」）
HANDLE = re.compile(r"[A-Za-z0-9_]{1,15}")
# 引用符の中で語を区切ってしまう文字。X の検索には引用符の中の逃がし方が無いので、空白にする。
_QUOTES = re.compile(r'["“”„‟″‶＂]')


class QueryError(ValueError):
    pass


def clean_title(title) -> str:
    """曲名を 1 つの句にする。引用符は空白に・制御文字は断る・空白は 1 つに。"""
    if not isinstance(title, str):
        raise QueryError("曲名は文字列です")
    if any(ord(c) < 32 or ord(c) == 127 for c in title):
        raise QueryError("曲名に制御文字は使えません")
    text = " ".join(_QUOTES.sub(" ", title).split())
    if not text:
        raise QueryError("曲名が空です（引用符だけの曲名は句にできません）")
    return text


def build_query(handle, title, *, retweets: bool = False) -> str:
    """`from:<handle> "<曲名>" -is:retweet`。handle は `@` を外して英数と _ の 15 字まで。"""
    name = str(handle or "").strip().lstrip("@")
    if not HANDLE.fullmatch(name):
        raise QueryError("handle は @ の後の英数と _ の 15 字までです")
    query = f'from:{name} "{clean_title(title)}"' + ("" if retweets else " -is:retweet")
    if len(query) > QUERY_MAX:
        raise QueryError(f"語は {QUERY_MAX} 字までです（{len(query)} 字）")
    return query


def check_query(q) -> str:
    """自由に書いた語の検査（空・制御文字・長さ）。"""
    text = q.strip() if isinstance(q, str) else ""
    if not text:
        raise QueryError("検索の語が空です")
    if any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise QueryError("語に制御文字は使えません")
    if len(text) > QUERY_MAX:
        raise QueryError(f"語は {QUERY_MAX} 字までです（{len(text)} 字）")
    return text


# ----- 時刻 -------------------------------------------------------------------

# X の全期間の検索の始まり（2006-03-21 の最初の投稿。`start_time` の下限）。
EARLIEST = datetime.datetime(2006, 3, 21, 0, 0, 1, tzinfo=datetime.timezone.utc)
# `end_time` は要求の時刻より 10 秒以上前でなければならない（一次資料・余裕を見て 30 秒）。
END_MARGIN = datetime.timedelta(seconds=30)
# 件数の口は 31 日ごとに 1 ページ（一次資料 counts introduction「31-day chunks」）。
COUNTS_PAGE_DAYS = 31


def _parse(value):
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            day = datetime.date.fromisoformat(value)
        except ValueError:
            return None
        return datetime.datetime(day.year, day.month, day.day, tzinfo=datetime.timezone.utc)
    at = jst.parse(value)
    return at.astimezone(datetime.timezone.utc) if at is not None else None


def utc_bound(value, *, now=None, what="since"):
    """`YYYY-MM-DD`（UTC の 0 時）か ISO の時刻を UTC の datetime に。None は None。"""
    if value is None:
        return None
    at = _parse(value) if isinstance(value, str) else None
    if at is None:
        raise QueryError(f"--{what} は YYYY-MM-DD か ISO の時刻です（{value!r}）")
    now = now or jst.now_jst().astimezone(datetime.timezone.utc)
    if at < EARLIEST:
        at = EARLIEST
    if at > now - END_MARGIN:
        raise QueryError(f"--{what} が今より後（か 30 秒以内）です")
    return at


def window(since, until, *, now=None):
    """`(start, end_or_None)`。since が無ければ全期間の始まり。since < until でなければ断る。"""
    now = now or jst.now_jst().astimezone(datetime.timezone.utc)
    start = utc_bound(since, now=now, what="since") or EARLIEST
    end = utc_bound(until, now=now, what="until")
    if end is not None and end <= start:
        raise QueryError("--until は --since より後にしてください")
    return start, end


def stamp(at) -> str:
    return at.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def counts_pages_needed(start, end, *, now=None) -> int:
    """件数の口で窓を数えきるのに要るページの数（31 日ごと）。"""
    now = now or jst.now_jst().astimezone(datetime.timezone.utc)
    stop = end or (now - END_MARGIN)
    seconds = max(0.0, (stop - start).total_seconds())
    return max(1, math.ceil(seconds / (COUNTS_PAGE_DAYS * 86400)))
