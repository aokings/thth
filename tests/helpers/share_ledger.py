"""`thth share` のテスト用の偽台帳（返信・取得記録・実測）。

`tests/test_threadshape.py` と**同じ形**の 3 階層を置く（そちらが手計算と一致する
ことを既に確かめているので、share 側は「その形が正しくハッシュされて積まれるか」
だけを見ればよい）。

**本文・`username`・`repo_dir` に目印を仕込める**（`secret=` を渡すと、
`SECRET-…` を落ちてはいけない場所に埋める）。`tests/test_share_safety.py` は
それが outbox に 1 バイトも出ないことを grep で確かめる。
"""
from __future__ import annotations

import json
import os

ROOT = "17912345678901234"          # Threads の生の post_id（数字だけ）
POSTED_AT = "2026-09-10T08:00:00+09:00"   # = 2026-09-09T23:00:00Z（JST 朝）


def _write_ndjson(path: str, rows: list) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _reply(rid: str, *, parent: str, username: str, minutes: int,
           secret: str = "") -> dict:
    base_h, base_m = 23, 0
    total = base_m + minutes
    day = 9 + (base_h * 60 + total) // 1440
    rem = (base_h * 60 + total) % 1440
    return {
        "kind": "reply", "post_id": ROOT, "id": rid, "is_reply": True,
        "username": f"{username}{secret}",
        "text": f"{rid} の本文{secret}",
        "timestamp": f"2026-09-{day:02d}T{rem // 60:02d}:{rem % 60:02d}:00+0000",
        "replied_to": {"id": parent}, "root_post": {"id": ROOT},
        "collected_at": "2026-09-11T09:00:00+09:00",
    }


def _fetch(*, marks: list, age_hours: float) -> dict:
    return {"kind": "fetch", "post_id": ROOT,
            "collected_at": f"2026-09-10T{int(8 + age_hours) % 24:02d}:00:00+09:00",
            "age_hours": age_hours, "marks": marks, "trigger": "marks",
            "replies": 0, "id_missing": 0}


def build_reply_ledger(account: dict, *, secret: str = "",
                        topic: str = "中学受験2027") -> str:
    """3 階層の返信と、1/6/24h の取得記録と、実測を 1 本置く。

      ROOT
        ├ R1  よそ子      +10 分   ← 最初の他人の返信
        │   └ R2  nigamilab（作者） +90 分
        │       └ R3  よそ太  +200 分   ← 深さ 3
        ├ R4  よそ子      +400 分
        └ R5  nigamilab（作者） +1000 分

    枝 3・最深 3・返信 5・作者 2・参加者 2。168h の刻みは**採っていない**
    （`growth["168"]` が `null` になることの材料）。
    """
    repo = account["repo_dir"]
    replies = os.path.join(repo, "data", "sns", "replies", f"{ROOT}.ndjson")
    _write_ndjson(replies, [
        _reply("R1", parent=ROOT, username="よそ子", minutes=10, secret=secret),
        _reply("R2", parent="R1", username="nigamilab", minutes=90, secret=secret),
        _reply("R3", parent="R2", username="よそ太", minutes=200, secret=secret),
        _reply("R4", parent=ROOT, username="よそ子", minutes=400, secret=secret),
        _reply("R5", parent=ROOT, username="nigamilab", minutes=1000, secret=secret),
        _fetch(marks=[1], age_hours=1.0),
        _fetch(marks=[6], age_hours=6.1),
        _fetch(marks=[24], age_hours=24.0),
    ])
    insights = os.path.join(repo, "data", "sns", "insights", "posts",
                             f"{ROOT}.ndjson")
    _write_ndjson(insights, [
        {"post_id": ROOT, "file": f"2026-09-10-x{secret}.md",
         "account": account["name"], "topic": topic,
         "collected_at": "2026-09-10T09:00:00+09:00", "posted_at": POSTED_AT,
         "age_hours": 1.0, "marks": [1],
         "metrics": {"views": 100, "likes": 0, "replies": 0, "reposts": 0,
                      "quotes": 0, "shares": 0}},
        {"post_id": ROOT, "file": f"2026-09-10-x{secret}.md",
         "account": account["name"], "topic": topic,
         "collected_at": "2026-09-11T08:00:00+09:00", "posted_at": POSTED_AT,
         "age_hours": 24.0, "marks": [24],
         "metrics": {"views": 300, "likes": 0, "replies": 0, "reposts": 0,
                      "quotes": 0, "shares": 0}},
    ])
    return replies
