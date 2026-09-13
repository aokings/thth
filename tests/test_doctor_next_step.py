"""`thth doctor` の「次の一手」（設計 v1.0.0・§2 の C1）。

app.env → accounts/<account>.json の存在と必須項目 → token の有無、の順に見て、
足りないものごとに導入文書の節番号（§3 app.env・§4 アカウント台帳・§5 トークン）
を 1 行で言うことを確かめる。既存の検査（トークンを実際に叩く診断・T2a の
「トークンが無い」判定）は壊さない——`tests/test_doctor.py` の既存テストを見よ。

**app.env だけは扱いが違う**（masaru 裁定 2026-09-13）。app.env を使うのは
`thth auth` だけで、管理画面で発行したトークンを `thth token set` で入れる運用
——いまの本番 4 本がそう——では置かなくても正しく動く。だから **「無い」は §3 を
指さない**（「任意」と言うだけ）。§3 を指すのは **置いたのに使えないとき**
（項目が空・読めない）だけ。トークンが無いときだけ、`thth auth` へ進む人向けに
`thth app set` を 1 行添える。

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


def test_doctorはapp_env無しでも節3を言わず任意だと言う(
        tmp_path, monkeypatch, isolated_account_factory):
    """**app.env が無いだけでは §3 を指さない**（masaru 裁定 2026-09-13）。

    トークンは入っている（`thth token set` の運用）ので app.env は要らない。
    正しい状態なので「次の一手」ではなく「任意」と言う。**正しい状態を毎回
    「足りない」と言う道具は、本当に足りないときに読まれなくなる。**
    """
    account = isolated_account_factory(token=str(tmp_path / "a.token"))
    with open(str(tmp_path / "a.token"), "w", encoding="utf-8") as f:
        f.write('{"access_token": "x"}')

    monkeypatch.setenv("THTH_APP_ENV_PATH", str(tmp_path / "does-not-exist.env"))
    result = run_thth(["doctor", account["name"]])

    out = result.stdout + result.stderr
    assert "\u00a73" not in out, "app.env は任意なのに \u00a73 を指した: " + out
    assert "app.env: 無し" in out, out
    assert "任意" in out, out
    assert "thth app set" in out, out


def test_doctorはapp_env無しかつトークン無しで節5とapp_setを言う(
        tmp_path, monkeypatch, isolated_account_factory):
    """トークンが無いときは \u00a75。**そのとき app.env が無ければ `thth app set` も添える。**

    トークンを入れる道は 2 つ（`thth token set` は app.env 不要・`thth auth` は
    要る）。`auth` を選ぶ人が \u00a73 へ戻れる 1 行だけ足す。
    """
    account = isolated_account_factory(token=str(tmp_path / "missing.token"))
    monkeypatch.setenv("THTH_APP_ENV_PATH", str(tmp_path / "does-not-exist.env"))
    result = run_thth(["doctor", account["name"]])

    out = result.stdout + result.stderr
    assert "\u00a75" in out, out
    assert "app.env: 無し" in out, out
    # **「無し」の行にも `thth app set` は出ている。** それに当たって通ってしまうと
    # この 1 行を落とす変異が生き残るので（M5・2026-09-13 に実際に生き残った）、
    # **添える 1 行そのもの**を見る。
    assert "`thth auth` を使うなら先に `thth app set`" in out, out


def test_doctorはapp_envがあるならapp_setの1行を添えない(
        tmp_path, monkeypatch, isolated_account_factory):
    """app.env が揃っていれば、トークンが無くても `thth app set` は言わない
    （§5 だけ）。**要らない案内を足さない**——添える条件が効いていることの裏取り。"""
    account = isolated_account_factory(token=str(tmp_path / "missing.token"))
    app_env_path = tmp_path / "app.env"
    _write_app_env(str(app_env_path))
    monkeypatch.setenv("THTH_APP_ENV_PATH", str(app_env_path))

    result = run_thth(["doctor", account["name"]])

    out = result.stdout + result.stderr
    assert "\u00a75" in out, out
    assert "thth app set" not in out, out


def test_doctorは壊れたapp_envでは節3を言う(
        tmp_path, monkeypatch, isolated_account_factory):
    """**置いたのに使えない**（項目が空）ときは、いままでどおり \u00a73 を指す。

    「無い」（任意）と「置いたのに使えない」（要修理）を分けるのがこの直しの
    要点なので、両方を別のテストで押さえる。
    """
    account = isolated_account_factory(token=str(tmp_path / "a.token"))
    with open(str(tmp_path / "a.token"), "w", encoding="utf-8") as f:
        f.write('{"access_token": "x"}')
    app_env_path = tmp_path / "broken.env"
    _write_app_env(str(app_env_path), app_secret="")

    monkeypatch.setenv("THTH_APP_ENV_PATH", str(app_env_path))
    result = run_thth(["doctor", account["name"]])

    out = result.stdout + result.stderr
    assert "\u00a73" in out, out
    assert "項目が足りません" in out, out
    assert "任意" not in out, "壊れているのに「任意」と言った: " + out


def test_doctorのjsonにapp_envの状態が出る(
        tmp_path, monkeypatch, isolated_account_factory):
    """`--json` に `app_env`: `absent` / `ok` / `broken`（追加のみ）。"""
    import json as _json

    account = isolated_account_factory(token=str(tmp_path / "missing.token"))

    monkeypatch.setenv("THTH_APP_ENV_PATH", str(tmp_path / "does-not-exist.env"))
    absent = _json.loads(run_thth(["doctor", account["name"], "--json"]).stdout)
    assert absent["app_env"] == "absent", absent

    good = tmp_path / "app.env"
    _write_app_env(str(good))
    monkeypatch.setenv("THTH_APP_ENV_PATH", str(good))
    ok = _json.loads(run_thth(["doctor", account["name"], "--json"]).stdout)
    assert ok["app_env"] == "ok", ok

    broken = tmp_path / "broken.env"
    _write_app_env(str(broken), app_id="")
    monkeypatch.setenv("THTH_APP_ENV_PATH", str(broken))
    bad = _json.loads(run_thth(["doctor", account["name"], "--json"]).stdout)
    assert bad["app_env"] == "broken", bad


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
