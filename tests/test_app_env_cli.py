"""`thth app set` / `thth app show`（masaru 裁定 2026-09-13）。

`~/.config/thth/app.env` を**手で書く**のをやめて道具にする。守らせたいのは
次の 4 つで、ここは全部それを見ている:

  1. **App Secret を画面に出さない。** 入力は `getpass`（非対話は `--secret-stdin`）、
     出力は path と 600 だけ。stdout にも stderr にも値の文字列が出ない。
  2. **原子的に・600 で書く**（`thth/secrets_fs.py` の作法・`token set` と同じ）。
     上書きしても 600 のまま。
  3. **空は断る**（rc=2）。断ったときはファイルを作らない・既存を壊さない。
  4. **`show` は値を出さない。** 有無・鍵の名前・パーミッションだけ。

**秘密は扱わない。** ここで使う値はすべて `DUMMY-...` の明らかな偽物で、
`THTH_APP_ENV_PATH` を `tmp_path` に向けて隔離する。**実物の `~/.config/thth/`
には一切触れない**（`app_env_path` fixture が毎回 assert している）。
"""
from __future__ import annotations

import json
import os
import stat

import pytest

from tests.conftest import run_thth

DUMMY_APP_ID = "DUMMY-APP-ID-0000"
DUMMY_SECRET = "DUMMY-SECRET-do-not-use-6f0a"
DUMMY_SECRET_2 = "DUMMY-SECRET-second-value-9c31"


@pytest.fixture
def app_env_path(tmp_path, monkeypatch):
    """`THTH_APP_ENV_PATH` を tmp_path 配下へ向ける（実物を触らないことも確かめる）。"""
    path = tmp_path / "config" / "app.env"
    real = os.path.expanduser("~/.config/thth/app.env")
    assert str(path) != real
    monkeypatch.setenv("THTH_APP_ENV_PATH", str(path))
    return path


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(str(path)).st_mode)


def _set(app_id=DUMMY_APP_ID, secret=DUMMY_SECRET, extra=None):
    """`thth app set --app-id … --secret-stdin`（標準入力から 1 行）。"""
    args = ["app", "set", "--app-id", app_id, "--secret-stdin", *(extra or [])]
    return run_thth(args, stdin=(secret + "\n"))


# --------------------------------------------------------------------------
# (a) 書けて 600・鍵 2 つ・値が一致
# --------------------------------------------------------------------------

def test_app_setはsecret_stdinで600のapp_envを書く(app_env_path):
    """**ファイルを読んで**中身を確かめる（出力から確かめてはいけない——値は
    出力に出ないのが仕様なので、出力で確かめられたらそれ自体が違反）。"""
    result = _set()
    assert result.returncode == 0, result.stdout + result.stderr
    assert os.path.exists(str(app_env_path)), result.stdout + result.stderr

    assert _mode(app_env_path) == 0o600, oct(_mode(app_env_path))
    # ディレクトリも作られて 700（`~/.config/thth/` を umask 任せにしない）。
    assert _mode(app_env_path.parent) == 0o700, oct(_mode(app_env_path.parent))

    body = open(str(app_env_path), encoding="utf-8").read()
    assert body == f"THREADS_APP_ID={DUMMY_APP_ID}\nTHREADS_APP_SECRET={DUMMY_SECRET}\n", body

    # 読み手（`thth auth` が使う口）が同じ 2 つを取り出せる。
    from thth import appenv as appenv_mod
    assert appenv_mod.load_app_env(str(app_env_path)) == (DUMMY_APP_ID, DUMMY_SECRET)

    # 成功時の出力は path と 600 だけ。
    assert "app.env を書きました" in result.stdout, result.stdout
    assert str(app_env_path) in result.stdout, result.stdout


# --------------------------------------------------------------------------
# (b) Secret が stdout にも stderr にも出ない
# --------------------------------------------------------------------------

def test_app_setはsecretを出力に一切出さない(app_env_path):
    """**これがこのコマンドの存在理由。** 変異（`log(secret)` を足す）で必ず落ちる。"""
    result = _set()
    both = result.stdout + result.stderr
    assert DUMMY_SECRET not in both, both
    # 断片でも出ていない（前後を切って貼られる形も塞ぐ）。
    assert "do-not-use" not in both, both
    # app ID は秘密ではないが、出す必要も無いので出していないことを見ておく。
    assert result.returncode == 0


def test_app_showはsecretを出力に一切出さない(app_env_path):
    assert _set().returncode == 0
    result = run_thth(["app", "show"])
    both = result.stdout + result.stderr
    assert DUMMY_SECRET not in both, both
    assert DUMMY_APP_ID not in both, both


# --------------------------------------------------------------------------
# (c) 空は rc=2 でファイルを作らない
# --------------------------------------------------------------------------

def test_app_setは空のsecretをrc2で断りファイルを作らない(app_env_path):
    result = _set(secret="")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "App Secret が空です" in result.stdout, result.stdout
    assert not os.path.exists(str(app_env_path)), "断ったのに書いた"


def test_app_setは空のapp_idをrc2で断りファイルを作らない(app_env_path):
    result = _set(app_id="   ")
    assert result.returncode == 2, result.stdout + result.stderr
    assert not os.path.exists(str(app_env_path)), "断ったのに書いた"


def test_app_setは断っても既存のapp_envを壊さない(app_env_path):
    """**断り方で壊さない。** 一時ファイル ＋ `os.replace` の作法が効いている
    ことを、断る経路でも見ておく（`open(path, "w")` に戻すと落ちる）。"""
    assert _set().returncode == 0
    before = open(str(app_env_path), encoding="utf-8").read()

    assert _set(secret="").returncode == 2
    assert open(str(app_env_path), encoding="utf-8").read() == before


# --------------------------------------------------------------------------
# (d) 上書きしても 600
# --------------------------------------------------------------------------

def test_app_setは上書きしても600のまま(app_env_path):
    assert _set().returncode == 0
    # わざと緩めてから上書きする（原子的な書きが 600 を作り直すことを見る）。
    os.chmod(str(app_env_path), 0o644)

    result = _set(secret=DUMMY_SECRET_2)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _mode(app_env_path) == 0o600, oct(_mode(app_env_path))

    from thth import appenv as appenv_mod
    assert appenv_mod.load_app_env(str(app_env_path)) == (DUMMY_APP_ID, DUMMY_SECRET_2)
    assert DUMMY_SECRET_2 not in (result.stdout + result.stderr)


# --------------------------------------------------------------------------
# (e)(f) `app show`
# --------------------------------------------------------------------------

def test_app_showは値を出さず鍵名だけ出す(app_env_path):
    assert _set().returncode == 0
    result = run_thth(["app", "show"])
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert "THREADS_APP_ID" in out, out
    assert "THREADS_APP_SECRET" in out, out
    assert str(app_env_path) in out, out
    assert "600" in out, out
    assert DUMMY_SECRET not in out, out


def test_app_showはapp_envが無いとき任意だと言う(app_env_path):
    result = run_thth(["app", "show"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "無し" in result.stdout, result.stdout
    assert "任意" in result.stdout, result.stdout


def test_app_showのjsonの形(app_env_path):
    """`{"path", "exists", "keys_present", "mode_ok"}` の 4 つだけ（値は無い）。"""
    absent = json.loads(run_thth(["app", "show", "--json"]).stdout)
    assert set(absent) == {"path", "exists", "keys_present", "mode_ok"}, absent
    assert absent["path"] == str(app_env_path)
    assert absent["exists"] is False
    assert absent["keys_present"] == []
    assert absent["mode_ok"] is False

    assert _set().returncode == 0
    present = json.loads(run_thth(["app", "show", "--json"]).stdout)
    assert set(present) == {"path", "exists", "keys_present", "mode_ok"}, present
    assert present["exists"] is True
    assert present["keys_present"] == ["THREADS_APP_ID", "THREADS_APP_SECRET"]
    assert present["mode_ok"] is True
    # **値は JSON にも入らない。**
    assert DUMMY_SECRET not in json.dumps(present, ensure_ascii=False)

    os.chmod(str(app_env_path), 0o644)
    loose = json.loads(run_thth(["app", "show", "--json"]).stdout)
    assert loose["mode_ok"] is False, loose
    # `show` は読むだけ——パーミッションを勝手に直さない。
    assert _mode(app_env_path) == 0o644, oct(_mode(app_env_path))


def test_app_showは項目が足りないと足りない鍵を言う(app_env_path):
    os.makedirs(str(app_env_path.parent), exist_ok=True)
    with open(str(app_env_path), "w", encoding="utf-8") as f:
        f.write(f"THREADS_APP_ID={DUMMY_APP_ID}\n")
    os.chmod(str(app_env_path), 0o600)

    data = json.loads(run_thth(["app", "show", "--json"]).stdout)
    assert data["keys_present"] == ["THREADS_APP_ID"], data

    human = run_thth(["app", "show"]).stdout
    assert "THREADS_APP_SECRET" in human, human
    assert "足りない" in human, human


# --------------------------------------------------------------------------
# 秘密を扱うコマンドは MCP に出さない（設計 §3.7・`auth`/`refresh`/`token` と同じ）
# --------------------------------------------------------------------------

def test_app_はMCPに出ていない():
    """秘密を書くコマンドは MCP に出さない（`tests/test_mcp.py` の見張りと対に置く）。"""
    import importlib.util

    server_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp", "server.py")
    spec = importlib.util.spec_from_file_location("thth_mcp_server_appcheck", server_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    names = {t["name"] for t in mod.TOOLS}
    assert not [n for n in names if "app" in n], names
