"""広告の呼び出しの数の控え（計画 Meta 全部盛り §1.5・§2-14・M5 の (b)）。

Marketing API の段（Full）の条件を数える: 「at least 500 Marketing API calls in the last 15 days」
「error rate of less than 15% in the last 500 calls」（authorization の頁・11/05）。保つ条件も同じ。

置き場: `adaccount_state/<名前>/meta_calls.json`（量の写し `meta_usage.json` の隣・0600）。中身は数だけ:

```
{"version": 1, "since": "2026-11-05", "days": {"2026-11-05": [呼び出し, 誤り]}, "last": [[UNIX 秒, 0 か 1], …]}
```

**本文も宛先も残さない。** 数えるのは Meta が答えた呼び出しだけ（成功と HTTP の誤り）。網に出る前に断った待ちと、
網の失敗（Meta に届いたか分からない）は数えない。**数えるのは THTH を通った呼び出しだけ**で、Meta が数えた値
そのものではない。同時に走ると 1 回落ちることがあるが、多く数える向きには外れない（量の写しと同じく柵ではない）。
"""
from __future__ import annotations

import datetime
import json
import os

from .. import jst, secrets_fs

FILE = "meta_calls.json"
VERSION = 1
WINDOW_DAYS = 15
MIN_CALLS = 500
RATE_WINDOW = 500
MAX_ERROR_RATE = 0.15
# 15 日の窓より少し長く残す（古い日は書くときに落とす）。
KEEP_DAYS = WINDOW_DAYS + 1


def path_beside(usage_path: str) -> str:
    return os.path.join(os.path.dirname(usage_path), FILE)


def read(path: str) -> dict:
    """中身（無い・読めない・形が違えば空の形）。"""
    try:
        with open(path, encoding="utf-8") as stream:
            value = json.loads(stream.read(262145))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def note(usage_path: str, ok: bool, *, now=None) -> None:
    """1 回を足す。書けなくても呼び出しは止めない。"""
    now = now or jst.now_jst()
    path = path_beside(usage_path)
    row = read(path)
    today = now.date().isoformat()
    floor = (now.date() - datetime.timedelta(days=KEEP_DAYS)).isoformat()
    days = {day: pair for day, pair in (row.get("days") or {}).items() if day >= floor}
    calls, errors = days.get(today) or (0, 0)
    days[today] = [calls + 1, errors + (0 if ok else 1)]
    last = list(row.get("last") or [])[-(RATE_WINDOW - 1):] + [[int(now.timestamp()), 0 if ok else 1]]
    try:
        secrets_fs.atomic_write_json(path, {"version": VERSION, "since": row.get("since") or today,
                                            "days": days, "last": last})
    except (OSError, ValueError):
        pass


def summary(paths, *, now) -> dict:
    """全部の広告の口座を足した数（段の条件はアプリ全体の数）。`meets` は 2 つの条件を満たすか。"""
    floor = (now.date() - datetime.timedelta(days=WINDOW_DAYS - 1)).isoformat()
    calls = errors = 0
    last, since = [], None
    for path in paths:
        row = read(path)
        for day, pair in (row.get("days") or {}).items():
            if day >= floor and isinstance(pair, list) and len(pair) == 2:
                calls, errors = calls + pair[0], errors + pair[1]
        last.extend(pair for pair in row.get("last") or [] if isinstance(pair, list) and len(pair) == 2)
        if isinstance(row.get("since"), str):
            since = min(since or row["since"], row["since"])
    recent = sorted(last)[-RATE_WINDOW:]
    failed = sum(pair[1] for pair in recent)
    rate = failed / len(recent) if recent else None
    return {"calls_15d": calls, "errors_15d": errors, "last_n": len(recent), "last_errors": failed,
            "error_rate": round(rate, 4) if rate is not None else None, "counting_since": since,
            "meets": calls >= MIN_CALLS and len(recent) >= RATE_WINDOW and rate < MAX_ERROR_RATE}
