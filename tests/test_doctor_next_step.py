"""`thth doctor` の「次の一手」（設計 v1.0.0・§2 の C1）。

app.env → accounts/<account>.json の存在と必須項目 → token の有無、の順に見て、
足りないものごとに導入文書の節番号（§3 app.env・§4 アカウント台帳・§5 トークン）
を 1 行で言うことを確かめる。既存の検査（トークンを実際に叩く診断・T2a の
「トークンが無い」判定）は壊さない——`tests/test_doctor.py` の既存テストを見よ。

**秘密は扱わない。** ここで使う app.env・token はすべて tmp_path 上の偽物で、
`THTH_APP_ENV_PATH` / `THTH_APP_DIR` で隔離する。`~/.config/thth/` には
一切触れない。
"""
from __future__ import annotations

import os

from tests.conftest import run_thth


def _write_app_env(path, *, app_id="APP-ID", app_secret="APP-SECRET"):
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"THREADS_APP_ID={app_id}\nTHREADS_APP_SECRET={app_secret}\n")
    os.chmod(path, 0o600)


def test_doctorはapp_env無しで節3を言う(tmp_path, monkeypatch, isolated_account_factory):
    """app.env が無いとき、`§3 app.env` を指す（台帳・token は揃えておく）。"""
    account = isolated_account_factory(token=str(tmp_path / "a.token"))
    with open(str(tmp_path / "a.token"), "w", encoding="utf-8") as f:
        f.write('{"access_token": "x"}')

    monkeypatch.setenv("THTH_APP_ENV_PATH", str(tmp_path / "does-not-exist.env"))
    result = run_thth(["doctor", account["name"]])

    out = result.stdout + result.stderr
    assert "§3" in out, out
    assert "app.env" in out, out


def test_doctorは台帳無しで節4を言う(tmp_path, monkeypatch):
    """`accounts/<account>.json` が無いとき、`§4 アカウント台帳` を指す。

    app.env は揃っている状態で確かめる——app.env の欠落（§3）と台帳の欠落
    （§4）が混ざらないことも、ここで一緒に確認する。
    """
    app_dir = tmp_path / "app"
    os.makedirs(str(app_dir / "accounts"))
    app_env_path = tmp_path / "app.env"
    _write_app_env(str(app_env_path))

    monkeypatch.setenv("THTH_APP_ENV_PATH", str(app_env_path))
    result = run_thth(["doctor", "no-such-account"], env={"THTH_APP_DIR": str(app_dir)})

    out = result.stdout + result.stderr
    assert result.returncode == 2
    assert "§4" in out, out
    assert "§3" not in out, "app.env は揃えたのに §3 が出た: " + out


def test_doctorはtoken無しで節5を言う(tmp_path, monkeypatch, isolated_account_factory):
    """app.env・台帳は揃っていて token だけ無いとき、`§5 トークン` を指す。

    既存の検査（`トークンが無い`・T2a）は壊さない——同じ出力に両方出る。
    """
    account = isolated_account_factory(token=str(tmp_path / "missing.token"))
    app_env_path = tmp_path / "app.env"
    _write_app_env(str(app_env_path))
    monkeypatch.setenv("THTH_APP_ENV_PATH", str(app_env_path))

    result = run_thth(["doctor", account["name"]])

    out = result.stdout + result.stderr
    assert "§5" in out, out
    assert "トークンが無い" in out, out
    assert "§3" not in out and "§4" not in out, out
