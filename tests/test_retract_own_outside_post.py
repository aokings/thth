"""11/23 masaru「変えようぜ。だって、自分のアカウントでしょ？」

THTH の記録（queue・sent/）が無い投稿でも、**その口座の鍵で読んだ自分の直近の投稿**に
見えれば `thth retract` で消せる。記録は `sent/` に入れず `retracted_outside/` に置き、
安全装置は削除の 1 件に数える。見えない id（他人の投稿・古い投稿）は今までどおり断る。
偽サーバの `GET /threads` は自分の投稿として id 777 を返す。
"""
from __future__ import annotations

import os

from tests.helpers.fake_threads_writes import fake_threads_writes_server
from tests.test_v21b_approved_writes import ACCOUNT, _account_with_token, _digest, _ns, point_at  # noqa: F401
from thth import accounts as accounts_mod
from thth import guard as guard_mod
from thth import retract_cli
from thth import sent as sent_mod


def test_記録の無い自分の投稿は二段確認で消せてsentの外に残る(isolated_account_factory, tmp_path, point_at, capsys):
    _account_with_token(isolated_account_factory, tmp_path)
    with fake_threads_writes_server() as base_url:
        point_at(base_url)
        rc1 = retract_cli.cmd_retract(_ns(account=ACCOUNT, post_id="777", reason="誤字", by="masaru"))
        assert rc1 == 1 and list(base_url.handler_cls.deletes) == []
        out = capsys.readouterr().out
        assert "THTH を通していない自分の投稿です" in out and "出した本文" in out
        rc2 = retract_cli.cmd_retract(_ns(account=ACCOUNT, post_id="777", reason="誤字",
                                          by="masaru", confirm=_digest("777")))
        deletes = list(base_url.handler_cls.deletes)
    assert rc2 == 0 and len(deletes) == 1 and deletes[0].endswith("/777")
    state_dir = accounts_mod.state_dir_for(ACCOUNT)
    assert sent_mod.read(state_dir, "777") is None          # THTH が出したものに数えない
    assert "777" not in sent_mod.post_ids(state_dir)
    row = sent_mod.read_outside_retraction(state_dir, "777")
    assert row["outside_thth"] is True and row["retracted_by"] == "masaru" and row["text"] == "出した本文"
    # 安全装置は削除の 1 件に数える
    assert [r for r in guard_mod._action_rows(ACCOUNT) if r[0] == "retract"]
    # もう一度は「既に取り下げ済み」
    with fake_threads_writes_server() as base_url:
        point_at(base_url)
        rc3 = retract_cli.cmd_retract(_ns(account=ACCOUNT, post_id="777", reason="誤字",
                                          by="masaru", confirm=_digest("777")))
        assert rc3 == 1 and list(base_url.handler_cls.deletes) == []
    assert "既に取り下げ済み" in capsys.readouterr().err


def test_自分の投稿に見えないidは断りになぜと直し方とより良くを添える(isolated_account_factory, tmp_path, point_at, capsys):
    _account_with_token(isolated_account_factory, tmp_path)
    with fake_threads_writes_server() as base_url:
        point_at(base_url)
        rc = retract_cli.cmd_retract(_ns(account=ACCOUNT, post_id="999", reason="誤字",
                                         by="masaru", confirm=_digest("999")))
        deletes = list(base_url.handler_cls.deletes)
    assert rc == 1 and deletes == []
    err = capsys.readouterr().err
    assert "なぜ:" in err and "直し方:" in err and "より良く:" in err
    assert not os.path.exists(sent_mod.outside_dir_for(accounts_mod.state_dir_for(ACCOUNT)))


def test_読めないときは消さない(isolated_account_factory, tmp_path, point_at):
    _account_with_token(isolated_account_factory, tmp_path)
    with fake_threads_writes_server({"recent": "absent"}) as base_url:
        point_at(base_url)
        rc = retract_cli.cmd_retract(_ns(account=ACCOUNT, post_id="777", reason="誤字",
                                         by="masaru", confirm=_digest("777")))
        assert rc == 1 and list(base_url.handler_cls.deletes) == []
