"""11/23 masaru「変えようぜ。だって、自分のアカウントでしょ？」・設計 持ち主の依頼で消す（2026-11-23）。

THTH の記録（queue・sent/）が無い投稿でも、**その口座の鍵で id 1 件を読み、自分の投稿だと
確かめれば** `thth retract` で消せる（直近 25 件の一覧を探すのはやめた・§3）。記録は `sent/` に入れず
`retracted_outside/` に置き、安全装置は削除の 1 件に数える。digest には一段目で読んだ本文の指紋を
入れる（§0 の 2）。

偽サーバ（`tests/helpers/fake_threads_writes.py`）の 1 件読みは、777 を自分（owner 12345）、888 を
他人（owner 99999）の投稿として返す。DELETE の回数が証拠。
"""
from __future__ import annotations

import json
import os

import pytest

from tests.helpers.fake_threads_writes import fake_threads_writes_server
from tests.test_v21b_approved_writes import ACCOUNT, _account_with_token, _digest, _ns, point_at  # noqa: F401
from thth import accounts as accounts_mod
from thth import approval
from thth import guard as guard_mod
from thth import retract_cli
from thth import sent as sent_mod


def _stage1(capsys, post_id="777", reason="誤字"):
    """一段目（--json）を打って、返った JSON を返す（DELETE は 0 回のはず）。"""
    rc = retract_cli.cmd_retract(_ns(account=ACCOUNT, post_id=post_id, reason=reason, by="masaru", json=True))
    out = capsys.readouterr().out
    assert rc == 1
    return json.loads(out)


def _stage2(post_id="777", reason="誤字", confirm=None):
    return retract_cli.cmd_retract(_ns(account=ACCOUNT, post_id=post_id, reason=reason, by="masaru",
                                       confirm=confirm))


def test_記録の無い自分の投稿は二段確認で消せてsentの外に残る(isolated_account_factory, tmp_path, point_at, capsys):
    _account_with_token(isolated_account_factory, tmp_path)
    with fake_threads_writes_server() as base_url:
        point_at(base_url)
        rc1 = retract_cli.cmd_retract(_ns(account=ACCOUNT, post_id="777", reason="誤字", by="masaru"))
        assert rc1 == 1 and list(base_url.handler_cls.deletes) == []
        out = capsys.readouterr().out
        assert "THTH を通していない自分の投稿です" in out and "出した本文" in out and "種類: 投稿" in out
        first = _stage1(capsys)
        assert first["kind"] == "post" and first["posted_at"] == "2026-11-20T10:00:00+0000"
        assert first["digest"] != _digest("777")       # 本文の指紋入りの別の番号
        rc2 = _stage2(confirm=first["digest"])
        deletes = list(base_url.handler_cls.deletes)
        reads = list(base_url.handler_cls.reads)
    assert rc2 == 0 and len(deletes) == 1 and deletes[0].endswith("/777")
    assert reads and set(reads) == {"777"}              # 1 件読み（一覧は探さない）
    state_dir = accounts_mod.state_dir_for(ACCOUNT)
    assert sent_mod.read(state_dir, "777") is None          # THTH が出したものに数えない
    assert "777" not in sent_mod.post_ids(state_dir)
    row = sent_mod.read_outside_retraction(state_dir, "777")
    assert row["outside_thth"] is True and row["retracted_by"] == "masaru" and row["text"] == "出した本文"
    assert row["kind"] == "post" and row["posted_at"] == "2026-11-20T10:00:00+0000"
    # 安全装置は削除の 1 件に数える
    assert [r for r in guard_mod._action_rows(ACCOUNT) if r[0] == "retract"]
    # もう一度は「既に取り下げ済み」
    with fake_threads_writes_server() as base_url:
        point_at(base_url)
        rc3 = _stage2(confirm=first["digest"])
        assert rc3 == 1 and list(base_url.handler_cls.deletes) == []
    assert "既に取り下げ済み" in capsys.readouterr().err


def test_他人の投稿は消えない(isolated_account_factory, tmp_path, point_at, capsys):
    """柵: 偽サーバが別の持ち主（owner 99999）の投稿を返す → DELETE 0 回・post_not_own。"""
    _account_with_token(isolated_account_factory, tmp_path)
    with fake_threads_writes_server() as base_url:
        point_at(base_url)
        rc1 = retract_cli.cmd_retract(_ns(account=ACCOUNT, post_id="888", reason="誤字", by="masaru"))
        # 番号を何にしても（口座・id・理由だけの番号でも・本物の番号の形でも）消えない。
        rc2 = _stage2(post_id="888", confirm=_digest("888"))
        forged = approval.compute_outside_retract_digest(post_id="888", account=ACCOUNT, reason="誤字",
                                                         text="他人の本文", posted_at="2026-11-20T10:00:00+0000",
                                                         kind="post")
        rc3 = _stage2(post_id="888", confirm=forged)
        deletes = list(base_url.handler_cls.deletes)
    assert (rc1, rc2, rc3) == (1, 1, 1) and deletes == []
    err = capsys.readouterr().err
    assert "post_not_own" in err and "なぜ:" in err and "直し方:" in err and "より良く:" in err
    assert not os.path.exists(sent_mod.outside_dir_for(accounts_mod.state_dir_for(ACCOUNT)))


@pytest.mark.parametrize("mode", ["5xx", "timeout"])
def test_読めなければ消さない(isolated_account_factory, tmp_path, point_at, capsys, monkeypatch, mode):
    """柵: 1 件読みが 5xx・時間切れ → DELETE 0 回・post_unreadable（なぜ・直し方・より良く）。"""
    monkeypatch.setenv("THTH_THREADS_TIMEOUT_SECONDS", "0.5")
    _account_with_token(isolated_account_factory, tmp_path)
    with fake_threads_writes_server({"own": mode}) as base_url:
        point_at(base_url)
        rc = _stage2(confirm=_digest("777"))
        deletes = list(base_url.handler_cls.deletes)
    assert rc == 1 and deletes == []
    err = capsys.readouterr().err
    assert "post_unreadable" in err and "なぜ:" in err and "直し方:" in err and "より良く:" in err


def test_番号は一段目を通らないと作れない(isolated_account_factory, tmp_path, point_at, capsys):
    """柵: 口座・id・理由だけから計算した番号（記録のある投稿の番号）では、記録の無い投稿は消えない。"""
    _account_with_token(isolated_account_factory, tmp_path)
    with fake_threads_writes_server() as base_url:
        point_at(base_url)
        rc = _stage2(confirm=_digest("777"))
        deletes = list(base_url.handler_cls.deletes)
    assert rc == 1 and deletes == []
    assert "digest が一致しない" in capsys.readouterr().err


def test_見せた後に本文が変わったら消さない(isolated_account_factory, tmp_path, point_at, capsys):
    """柵: 一段目と二段目の間に媒体の本文が変わった → 番号が合わず DELETE 0 回。"""
    _account_with_token(isolated_account_factory, tmp_path)
    with fake_threads_writes_server() as base_url:
        point_at(base_url)
        first = _stage1(capsys)
        base_url.handler_cls.behavior["own_text"] = "あとで直した本文"
        rc = _stage2(confirm=first["digest"])
        deletes = list(base_url.handler_cls.deletes)
    assert rc == 1 and deletes == []


def test_ownerが無ければ台帳のhandleと比べる(isolated_account_factory, tmp_path, point_at, capsys):
    """`owner` の欄が本物で返るかは未確認（設計 §3）。無ければ username を台帳の handle と比べる。"""
    _account_with_token(isolated_account_factory, tmp_path, handle="fake")
    with fake_threads_writes_server({"own": "no_owner"}) as base_url:
        point_at(base_url)
        first = _stage1(capsys)
        assert _stage2(confirm=first["digest"]) == 0
        assert len(base_url.handler_cls.deletes) == 1


def test_ownerが無くhandleも違えば消さない(isolated_account_factory, tmp_path, point_at, capsys):
    _account_with_token(isolated_account_factory, tmp_path, handle="someone-else")
    with fake_threads_writes_server({"own": "no_owner"}) as base_url:
        point_at(base_url)
        rc = _stage2(confirm=_digest("777"))
        assert rc == 1 and list(base_url.handler_cls.deletes) == []
    assert "post_not_own" in capsys.readouterr().err


def test_再投稿の_id_は一段目に種類を出す(isolated_account_factory, tmp_path, point_at, capsys):
    """Threads の再投稿の id に DELETE すると再投稿の取り消しになる（設計 §8）。一段目で言う。"""
    _account_with_token(isolated_account_factory, tmp_path)
    with fake_threads_writes_server({"own": "repost"}) as base_url:
        point_at(base_url)
        rc = retract_cli.cmd_retract(_ns(account=ACCOUNT, post_id="777", reason="誤字", by="masaru"))
        assert rc == 1 and list(base_url.handler_cls.deletes) == []
    out = capsys.readouterr().out
    assert "種類: 再投稿" in out and "再投稿の取り消しになります" in out


def test_25件の一覧と古い投稿の文は無い():
    """設計 §3: 直近 25 件の一覧を探すのをやめた。古いかどうかは理由にならない。"""
    import inspect
    src = inspect.getsource(retract_cli)
    assert not hasattr(retract_cli, "OWN_POST_LOOKUP") and not hasattr(retract_cli, "_find_own_post")
    assert "古い投稿には触りません" not in src
    assert "承認の段を通す・3.13.0" not in src


def test_already_goneは記録の無い投稿に足す先が無いと言う(isolated_account_factory, tmp_path, point_at, capsys):
    _account_with_token(isolated_account_factory, tmp_path)
    with fake_threads_writes_server() as base_url:
        point_at(base_url)
        rc = retract_cli.cmd_retract(_ns(account=ACCOUNT, post_id="777", reason="誤字", by="masaru",
                                         already_gone=True))
        assert rc == 1 and list(base_url.handler_cls.deletes) == [] and base_url.handler_cls.reads == []
    err = capsys.readouterr().err
    assert "なぜ:" in err and "直し方:" in err and "より良く:" in err
