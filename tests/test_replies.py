"""返信の台帳を読む口（`thth/replies.py`・T4）。

`thth/collect.py` は返信を `data/sns/replies/<post_id>.ndjson` に採っているが、
それを読むコードがどこにも無かった。ここではその読む口を確かめる。**読むだけ。
何も書かない。**
"""
from __future__ import annotations

import argparse
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


def _own_post(account, post_id: str = "POST1") -> None:
    """その account が出した記録（`sent/`）を置く。`thth replies` は置き場を共有する
    他 account の返信を返さないので（3.1.1）、CLI の試験は所有の記録を先に置く。"""
    from thth import accounts, approval, sent
    sent.write(accounts.state_dir_for(account["name"]), post_id=post_id, text="本文",
               body_hash=approval.compute_body_hash("本文"), sent_at="2026-09-10T09:00:00+09:00")


def test_返信を読めること(isolated_account):
    """`kind: reply` の行だけが `replies` に入り、`kind: fetch` の行は `fetches` に入る。"""
    _write_ndjson(_replies_path(isolated_account), [
        {"kind": "reply", "id": "R1", "text": "いいね", "username": "よそのひと",
         "timestamp": "2026-09-10T10:00:00+0000", "permalink": "https://example/r1",
         "post_id": "POST1", "collected_at": "2026-09-10T19:00:00+09:00"},
        {"kind": "fetch", "post_id": "POST1", "collected_at": "2026-09-10T19:00:00+09:00",
         "marks": [1], "replies": 1},
    ])

    result = replies_mod.load(isolated_account["name"])

    assert [r["id"] for r in result["replies"]] == ["R1"]
    assert result["replies"][0]["text"] == "いいね"
    assert len(result["fetches"]) == 1
    assert result["fetches"][0]["replies"] == 1
    assert result["broken"] == []
    assert result["counts"]["replies"] == 1
    assert result["counts"]["fetches"] == 1


def test_うちのhandleからの返信にownがつく(isolated_account_factory):
    """うちの account（handle: nigamilab）からの返信は `own: true`。

    handle は `@` 付き・大小文字が揺れうるので、揺れた形でも一致すること。
    """
    account = isolated_account_factory()  # handle: "nigamilab"
    _write_ndjson(_replies_path(account), [
        {"kind": "reply", "id": "R1", "text": "身内の返信", "username": "@NigamiLab",
         "post_id": "POST1", "collected_at": "2026-09-10T19:00:00+09:00"},
        {"kind": "reply", "id": "R2", "text": "よそからの返信", "username": "だれか",
         "post_id": "POST1", "collected_at": "2026-09-10T19:00:00+09:00"},
    ])

    result = replies_mod.load(account["name"])
    by_id = {r["id"]: r for r in result["replies"]}

    assert by_id["R1"]["own"] is True, "@ と大小文字が揺れても身内と判定できるはず"
    assert by_id["R2"]["own"] is False
    assert result["counts"]["own"] == 1
    assert result["counts"]["other"] == 1


def test_usernameが無い行はownがNoneになる(isolated_account):
    """`username` が無い行は `own: None`——「違う」ではなく「判らない」。"""
    _write_ndjson(_replies_path(isolated_account), [
        {"kind": "reply", "id": "R1", "text": "username無し",
         "post_id": "POST1", "collected_at": "2026-09-10T19:00:00+09:00"},
    ])

    result = replies_mod.load(isolated_account["name"])

    assert result["replies"][0]["own"] is None
    assert result["replies"][0]["own"] is not False, "判らないものを「違う」にしていないか"
    assert result["counts"]["unknown"] == 1
    assert result["counts"]["own"] == 0
    assert result["counts"]["other"] == 0


def test_壊れたndjsonを返信0件と言わない(isolated_account):
    """壊れた行のあるファイルは、`broken` に名前が出て、中身を混ぜない。

    「壊れていて読めなかった（broken）」と「まだ 1 件も採っていない（0 件）」を
    混ぜると、返信が本当に無いのか、読み損ねているだけなのかが判らなくなる。
    """
    path = _replies_path(isolated_account, post_id="POST_BROKEN")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"kind": "reply", "id": "R1", "text": "読める行"}\n')
        f.write("これは JSON ではない\n")

    result = replies_mod.load(isolated_account["name"])

    assert result["broken"] == ["POST_BROKEN.ndjson"]
    assert result["replies"] == [], "壊れたファイルの中身を混ぜてはいけない"
    assert result["counts"]["replies"] == 0

    # 存在しないファイル（採っていないだけ）は broken に出ない
    empty = replies_mod.load(isolated_account["name"], post_id="POST_NOTHING_YET")
    assert empty["broken"] == []
    assert empty["counts"]["replies"] == 0


def test_post_idを渡すとその投稿だけを読む(isolated_account):
    _write_ndjson(_replies_path(isolated_account, post_id="POST1"), [
        {"kind": "reply", "id": "R1", "text": "POST1 宛て", "post_id": "POST1"},
    ])
    _write_ndjson(_replies_path(isolated_account, post_id="POST2"), [
        {"kind": "reply", "id": "R2", "text": "POST2 宛て", "post_id": "POST2"},
    ])

    result = replies_mod.load(isolated_account["name"], post_id="POST1")

    assert [r["id"] for r in result["replies"]] == ["R1"]


def test_CLIがjsonでloadと同じものを返す(isolated_account, capsys):
    """`thth replies <account> --json` は `replies.load()` の戻り値をそのまま返す。"""
    from thth import cli as cli_mod

    _write_ndjson(_replies_path(isolated_account), [
        {"kind": "reply", "id": "R1", "text": "本文", "username": "だれか",
         "post_id": "POST1", "collected_at": "2026-09-10T19:00:00+09:00"},
    ])

    _own_post(isolated_account)

    args = argparse.Namespace(account=isolated_account["name"], post=None, json=True)
    rc = cli_mod.cmd_replies(args)
    captured = capsys.readouterr()

    assert rc == 0
    printed = json.loads(captured.out)
    expected = replies_mod.load(isolated_account["name"], owned_only=True)
    assert printed == expected and [r["id"] for r in printed["replies"]] == ["R1"]


def test_CLIの人向け出力は身内に印をつける(isolated_account_factory, capsys):
    from thth import cli as cli_mod

    account = isolated_account_factory()  # handle: "nigamilab"
    _write_ndjson(_replies_path(account), [
        {"kind": "reply", "id": "R1", "text": "身内の返信", "username": "nigamilab",
         "post_id": "POST1", "collected_at": "2026-09-10T19:00:00+09:00"},
        {"kind": "reply", "id": "R2", "text": "よその返信", "username": "だれか",
         "post_id": "POST1", "collected_at": "2026-09-10T19:00:00+09:00"},
    ])
    _own_post(account)

    args = argparse.Namespace(account=account["name"], post=None, json=False)
    rc = cli_mod.cmd_replies(args)
    captured = capsys.readouterr()

    assert rc == 0
    assert "[身内]" in captured.out
    assert captured.out.count("[身内]") == 1, "身内でない返信に印を付けていないか"
