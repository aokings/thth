"""柵の関数 1 つ: この行は中身（投稿・頁・貼る文）の材料に使えるか（構想 §9 の 4・計画 段 1）。

使えるのは、次のどれにも当たらない行だけ。当たれば前向きに断る（3 つの文は目録の第 10 部）。

1. 形が崩れている（`register_row_invalid`）・秘密らしい値がある（`register_row_secret`）
2. 退役している（`register_row_retired`）
3. 本人が違うと言った（`register_row_denied`）
4. 推測のまま（`register_row_guess`）
5. 頭は「本人が認めた」なのに、THTH の認めた記録が無いか、認めたあとに中身が変わった（`register_row_unconfirmed`）
6. 見直し日を過ぎた（`register_row_review_passed`）
7. 外に出してよい印が無い（`register_row_not_public`）

5 は頭の印でなく、THTH の側の記録（中身の指紋）で見る。頭の印は手でも LLM でも書き換えられるので。
"""
from __future__ import annotations

import datetime

from . import form
from .errors import RegisterRefused


def state(row: form.Row, records: dict, today: datetime.date) -> str | None:
    """使えなければ断りの符丁、使えれば None（断らずに数えるとき・`show` が使う）。"""
    if [p for p in row.problems if p == "secret"]:
        return "register_row_secret"
    if row.problems:
        return "register_row_invalid"
    head = row.head
    if head["status"] == form.RETIRED:
        return "register_row_retired"
    if head["confirmed"] == form.NO:
        return "register_row_denied"
    if head["confirmed"] == form.GUESS:
        return "register_row_guess"
    record = records.get(row.id)
    if not isinstance(record, dict) or record.get("digest") != form.fingerprint(row.text):
        return "register_row_unconfirmed"
    if form.parse_day(head["review_by"]) < today:
        return "register_row_review_passed"
    if head["public"] != "true":
        return "register_row_not_public"
    return None


def usable(row: form.Row, records: dict, today: datetime.date) -> form.Row:
    """使える行ならそのまま返す。使えなければ `RegisterRefused`（詳細は行の番号）。"""
    code = state(row, records, today)
    if code is not None:
        raise RegisterRefused(code, row.id, row=row.id)
    return row
