"""`THTH_ROOT` の解決（2026-09-09 の最初の本番投稿で踏んだ事故）。"""
from __future__ import annotations

import os

from thth import accounts as accounts_mod


def test_20260909_state_split_between_hand_run_and_timer(tmp_path, monkeypatch):
    """呼び方が違っても同じ state を指すこと。

    VM で手打ちすると `THTH_ROOT` が無いので `/srv/thth/app/state/` に、timer から
    走ると unit が渡すので `/srv/thth/state/` に state が出来ていた。置き場が割れると
    **ロックも inflight も別物になり**、§3.7 で塞いだはずの穴（timer 実行中の手打ちが
    ロックを踏まない）がパスの側から開く。
    """
    root = tmp_path / "srv-thth"
    app = root / "app"
    app.mkdir(parents=True)
    monkeypatch.delenv("THTH_ROOT", raising=False)
    monkeypatch.setattr(accounts_mod, "APP_DIR", str(app))
    assert accounts_mod.thth_root() == str(root), "app の親を指していない"

    # 環境変数があればそちらが勝つ（systemd unit の指定を尊重する）。
    monkeypatch.setenv("THTH_ROOT", str(tmp_path / "elsewhere"))
    assert accounts_mod.thth_root() == str(tmp_path / "elsewhere")


def test_app_という名前でなければ自分自身を使う(tmp_path, monkeypatch):
    """Mac 手元（repo 名が thth 等）では従来どおり repo 自身を使う。"""
    repo = tmp_path / "thth"
    repo.mkdir()
    monkeypatch.delenv("THTH_ROOT", raising=False)
    monkeypatch.setattr(accounts_mod, "APP_DIR", str(repo))
    assert accounts_mod.thth_root() == str(repo)
