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
    """Mac 手元（repo 名が thth 等）では従来どおり repo 自身を使う。

    **「repo から走っている」の判定が要る**（監査 1・P1-2）。ここは clone を
    模すので `.git` を置く。worktree では `.git` が**ファイル**なので、
    下の試験でその形も見る。
    """
    repo = tmp_path / "thth"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.delenv("THTH_ROOT", raising=False)
    monkeypatch.setattr(accounts_mod, "APP_DIR", str(repo))
    assert accounts_mod.thth_root() == str(repo)


def test_worktreeの_gitはファイルでも_repoとみなす(tmp_path, monkeypatch):
    repo = tmp_path / "thth"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: /somewhere/.git/worktrees/x\n", encoding="utf-8")
    monkeypatch.delenv("THTH_ROOT", raising=False)
    monkeypatch.setattr(accounts_mod, "APP_DIR", str(repo))
    assert accounts_mod.running_from_repo() is True
    assert accounts_mod.thth_root() == str(repo)


def test_gitが無くてもbin_ththがあればrepoとみなす(tmp_path, monkeypatch):
    """`.git` を持たない export（tarball を展開しただけ）でも従来どおり。"""
    repo = tmp_path / "thth"
    (repo / "bin").mkdir(parents=True)
    (repo / "bin" / "thth").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.delenv("THTH_ROOT", raising=False)
    monkeypatch.setattr(accounts_mod, "APP_DIR", str(repo))
    assert accounts_mod.thth_root() == str(repo)


def test_pipで入れたときはsite_packagesではなくホームの下(tmp_path, monkeypatch):
    """**`pip install --upgrade` で利用者のデータが黙って消えない**（監査 1・P1-2）。

    `pip install thth` で入ると `APP_DIR` は `site-packages/` になる。そこを
    `$THTH_ROOT` にしていたので、台帳・state・share の outbox・塩・仮名が全部
    site-packages に落ち、**wheel の入れ替えで消えていた**（しかも誰も言わない）。
    """
    site = tmp_path / "venv" / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True)          # **`.git` も `bin/thth` も無い**
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.delenv("THTH_ROOT", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(accounts_mod, "APP_DIR", str(site))

    assert accounts_mod.running_from_repo() is False
    root = accounts_mod.thth_root()
    assert "site-packages" not in root, root
    assert root == str(home / ".thth")


def test_XDG_DATA_HOMEがあればその下(tmp_path, monkeypatch):
    site = tmp_path / "site-packages"
    site.mkdir()
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    monkeypatch.delenv("THTH_ROOT", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    monkeypatch.setattr(accounts_mod, "APP_DIR", str(site))
    assert accounts_mod.thth_root() == str(xdg / "thth")


def test_THTH_ROOTがあれば何よりも強い(tmp_path, monkeypatch):
    """pip で入っていても、unit や打った人の指定が勝つ（順番 1）。"""
    site = tmp_path / "site-packages"
    site.mkdir()
    monkeypatch.setenv("THTH_ROOT", str(tmp_path / "明示"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(accounts_mod, "APP_DIR", str(site))
    assert accounts_mod.thth_root() == str(tmp_path / "明示")
