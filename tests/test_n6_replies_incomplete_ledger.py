"""account 台帳の一部が読めないと、不明な返信を「他者」に数えない（外部レビュー再々判定 N6・2026-09-12）。

外部レビューの記述（そのまま）: 対象 account は正常、**別 account の設定読み込みだけが
失敗する** fixture で、その別 account かもしれない username を、取得できた handle
集合にないという理由で `own=False`・`other=1`・`unknown=0` と返していた。

閉じる条件: handle 集合が不完全なら、**一致した身内は身内と判定してよいが、
非一致を他者と確定しない**（未知として数え、読めなかった設定を示す）。
"""
from __future__ import annotations

import json
import os

from thth import replies as replies_mod


def _write_ndjson(path: str, rows: list) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _replies_path(account, post_id: str = "POST1") -> str:
    return os.path.join(account["repo_dir"], "data", "sns", "replies", f"{post_id}.ndjson")


def _break_another_account(accounts_dir: str, name: str = "broken-threads") -> str:
    """**対象 account は正常。別 account の設定だけ壊す**（外部レビューの fixture 通り）。"""
    path = os.path.join(accounts_dir, f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{ これは json ではない")
    return name


def test_他account台帳が壊れていると非一致usernameがunknownになる(isolated_account_factory):
    """**これが本題。** 対象 account（nigamilab）は正常。別 account の台帳が
    壊れているとき、どちらの handle とも一致しない username を `other`（False）
    と確定してはいけない——読めなかった台帳の中の身内かもしれない。
    """
    account = isolated_account_factory()  # handle: "nigamilab"
    broken_name = _break_another_account(account["accounts_dir"])

    _write_ndjson(_replies_path(account), [
        {"kind": "reply", "id": "R1", "text": "身内かもしれない・不明な相手",
         "username": "だれかの handle", "post_id": "POST1",
         "collected_at": "2026-09-10T19:00:00+09:00"},
    ])

    result = replies_mod.load(account["name"])

    assert result["unreadable_accounts"] == [broken_name], (
        "読めなかった account の名前が出ていない"
    )
    row = result["replies"][0]
    assert row["own"] is None, (
        f"handle 集合が不完全なのに、非一致を「他者」と確定している: {row['own']}"
    )
    assert result["counts"]["unknown"] == 1
    assert result["counts"]["other"] == 0, "止めすぎるべきところを行き過ぎて他者に数えていないか、の逆——非一致を他者に数えていないか"


def test_一致した身内は他account台帳が壊れていても身内のまま(isolated_account_factory):
    """**止めすぎない。** 一致は確定できる事実なので、他の台帳が読めなくても崩さない。"""
    account = isolated_account_factory()  # handle: "nigamilab"
    _break_another_account(account["accounts_dir"])

    _write_ndjson(_replies_path(account), [
        {"kind": "reply", "id": "R1", "text": "身内の返信", "username": "@NigamiLab",
         "post_id": "POST1", "collected_at": "2026-09-10T19:00:00+09:00"},
    ])

    result = replies_mod.load(account["name"])

    assert result["replies"][0]["own"] is True, (
        "一致した身内まで、別 account の台帳が壊れているせいで崩れている"
    )
    assert result["counts"]["own"] == 1
    assert result["unreadable_accounts"] != [], "読めなかった account があった事実は残るはず"


def test_全account台帳が読めれば従来どおり非一致はother(isolated_account_factory):
    """**退行確認。** 台帳がすべて読めるときは、これまでどおり非一致は `other`。"""
    account = isolated_account_factory()  # handle: "nigamilab"

    _write_ndjson(_replies_path(account), [
        {"kind": "reply", "id": "R1", "text": "よそからの返信", "username": "だれか",
         "post_id": "POST1", "collected_at": "2026-09-10T19:00:00+09:00"},
    ])

    result = replies_mod.load(account["name"])

    assert result["unreadable_accounts"] == []
    assert result["replies"][0]["own"] is False
    assert result["counts"]["other"] == 1
