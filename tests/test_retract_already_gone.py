"""媒体の画面で既に消した投稿に、記録だけ足す（`thth retract --already-gone`・関東 #1560・10/8）。"""
from __future__ import annotations

import argparse

from thth import accounts, retract_cli, sent as sent_mod
from thth.adapters import x as x_mod

POST_ID = "2107936529020805252"


def _args(account, *, confirm=None, gone=True):
    return argparse.Namespace(account=account, post_id=POST_ID, reason="自己紹介の書き間違い",
                              by="masaru", confirm=confirm, json=True, wait=0, already_gone=gone)


def _setup(tmp_path, factory, monkeypatch):
    a = factory(name="kx", repo_dir=str(tmp_path / "repos" / "_none"), media="x", handle="kx",
                production=False)
    sent_mod.write(accounts.state_dir_for(a["name"]), post_id=POST_ID, text="本文",
                   body_hash="h", sent_at="2026-10-08T05:00:00+09:00")
    monkeypatch.setattr(retract_cli, "_lookup_url", lambda *a, **k: None)
    monkeypatch.setattr(x_mod.XAdapter, "delete_post",
                        lambda self, pid: (_ for _ in ()).throw(AssertionError("DELETE を呼んだ")))
    return a


def test_two_stages_record_only_without_delete(tmp_path, isolated_account_factory, monkeypatch, capsys):
    import json
    a = _setup(tmp_path, isolated_account_factory, monkeypatch)
    assert retract_cli.cmd_retract(_args(a["name"])) == 1          # 一段目: 何もしない
    first = json.loads(capsys.readouterr().out)
    assert first["stage"] == 1 and first["digest"]
    # production: false でも、DELETE を呼ばないので記録は足せる。
    assert retract_cli.cmd_retract(_args(a["name"], confirm=first["digest"])) == 0
    done = json.loads(capsys.readouterr().out)
    assert done["retracted"] is True and done["deleted_id"] is None
    record = sent_mod.read(accounts.state_dir_for(a["name"]), POST_ID)
    assert record["retracted_at"] and "既に消えていた" in record["retract_reason"]


def test_digest_differs_from_a_real_retract(tmp_path, isolated_account_factory, monkeypatch, capsys):
    import json
    a = _setup(tmp_path, isolated_account_factory, monkeypatch)
    retract_cli.cmd_retract(_args(a["name"]))
    gone = json.loads(capsys.readouterr().out)["digest"]
    retract_cli.cmd_retract(_args(a["name"], gone=False))
    real = json.loads(capsys.readouterr().out)["digest"]
    assert gone != real
    # 消す取り下げの digest では、記録だけの道は通らない（逆も同じ）。
    assert retract_cli.cmd_retract(_args(a["name"], confirm=real)) == 1
