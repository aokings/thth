"""行の種類ごとの決まり（段 1 は土台だけ。実店舗の種類は段 2 で足す）。

どの種類にも当てはまる決まりは `form.py`。ここは種類ごとの差だけを持つ:

- `two_stage`: 認める操作を二段確認にする（間違えたときの害が大きい種類・計画 段 2）。
- `private_by_default`: 既定で外に出さない頭の欄（持ち主が `public_fields` に書いたときだけ出す）。
- `check`: 種類ごとの形の点検（`(head, body) -> [問題の語]`）。

表に無い種類（自前のメディアの `school`・`voice` など）は、どの種類にも共通の決まりだけで読む。
"""
from __future__ import annotations

RULES: dict[str, dict] = {}


def rule(kind: str) -> dict:
    return RULES.get(kind) or {}


def two_stage(kind: str) -> bool:
    return bool(rule(kind).get("two_stage"))


def private_by_default(kind: str) -> frozenset:
    return frozenset(rule(kind).get("private_by_default") or ())


def problems(kind: str, head: dict, body: str) -> list:
    check = rule(kind).get("check")
    return list(check(head, body)) if check else []
