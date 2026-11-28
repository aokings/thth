"""5.33.0: 監査 5.32.0 の気づきの 3 つを柵にする。

1. 鍵の入口（`thth/principal_cli.py`・`thth/authpassword.py`・`thth/appconfig.py`）は関門の危ない領域の表に入る
   （`thth secret accept` が値を渡す先。ここだけの直しが監査なしで通らない）。
2. WordPress の鍵は、置いてあれば `--force` なしで置き換えない（受け取った鍵が正しい利用者のものでも）。
3. はてなの預け（主体の状態の置き場の `auth_pending.json`・request token の secret を持つ）は、その主体を使う
   最後の口座を畳む（leave）と消える。`--forget` でも消える。持ち主ごと畳む（owner の leave）と状態の置き場ごと消える。

鍵は試験の値（本物ではない）。網には出ない。
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys
from types import SimpleNamespace

from tests.test_v212_leave import worker  # noqa: F401  (fixture)
from tests.test_v212_server_writes import env  # noqa: F401  (fixture)
from tests.test_v5160_wordpress import PASSWORD, PRINCIPAL, call, lane, wp  # noqa: F401
from tests.test_v5181_leave_article import _article_account, _principal
from thth import leave, principal_cli, principals
from thth.adapters import wordpress

ROOT = pathlib.Path(__file__).resolve().parent.parent
FAKE_PENDING_SECRET = "fake-request-token-secret-5330"   # 試験の値


def _gate():
    spec = importlib.util.spec_from_file_location("release_gate_v5330", ROOT / "tools" / "release_gate.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["release_gate_v5330"] = mod  # dataclass が自分のモジュールを引く
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ 1. 関門の表


def test_鍵の入口の3つは危ない領域の表に入る():
    gate = _gate()
    for path in ("thth/principal_cli.py", "thth/authpassword.py", "thth/appconfig.py"):
        assert "auth_keys" in gate.areas_of(path), path
        assert (ROOT / path).exists(), path


# ------------------------------------------------------------------ 2. WordPress の --force


OTHER = "zyxw VUTS rqpo NMLK jihg FEDC"   # 試験の値（形だけ合わせた・本物ではない）


def test_WordPressは鍵があればforce無しでは正しい利用者の鍵でも置き換えない(lane, monkeypatch):
    rc, out, err = call(["principal", "auth", PRINCIPAL, "--by", "tester", "--stdin"], stdin=PASSWORD + "\n")
    assert rc == 0, out + err
    before = lane.token.read_bytes()

    # どの鍵でも正しい利用者として通るサイト（`--force` の柵だけを見る）
    def fake_get(self, url, *, op="get"):
        return {"id": 1, "username": json.loads(before)["wp_user"], "roles": ["editor"]}
    monkeypatch.setattr(wordpress.WordPressAdapter, "_get", fake_get)

    rc, out, err = call(["principal", "auth", PRINCIPAL, "--by", "tester", "--stdin"], stdin=OTHER + "\n")
    said = out + err
    assert rc == 2, said
    assert "token_present" in said and "--force" in said, said
    assert lane.token.read_bytes() == before, "--force なしで鍵を置き換えた"
    assert OTHER not in said and PASSWORD not in said

    # --force なら置き換わる（柵が強すぎて入れ替えられない、になっていない）
    rc, out, err = call(["principal", "auth", PRINCIPAL, "--by", "tester", "--stdin", "--force"], stdin=OTHER + "\n")
    assert rc == 0, out + err
    assert json.loads(lane.token.read_bytes())["application_password"] == OTHER


# ------------------------------------------------------------------ 3. はてなの預けと leave


def _put_pending(name):
    path = principal_cli._pending_path(name)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"oauth_token": "fake-request-token", "oauth_token_secret": FAKE_PENDING_SECRET}, f)
    os.chmod(path, 0o600)
    return path


def _leave(name, capsys):
    rc = leave.command(SimpleNamespace(name=name, by="operator", json=False))
    got = capsys.readouterr()
    return rc, got.out, got.err


def test_はてなの主体を使う最後の口座を畳むと預けが消える(env, worker, capsys):
    token, blog = _principal(env, "hatenablog", "alpha-hatena", ["alpha"])
    _article_account(env, "alpha", "hatenablog", "alpha-hatena", blog)
    pending = _put_pending("alpha-hatena")
    before = token.read_bytes()
    rc, out, err = _leave("alpha", capsys)
    assert rc == 0, err
    assert leave.read("alpha")["phase"] == "completed"
    assert not os.path.exists(pending), "最後の口座を畳んだのに request token の預けが残っている"
    # 主体の鍵と台帳はこれまでどおり残す（口座の leave は主体の鍵を消さない・5.18.1）
    assert token.read_bytes() == before
    assert os.path.exists(principals.path_for("alpha-hatena"))
    assert FAKE_PENDING_SECRET not in out + err


def test_ほかの口座がまだ使う主体の預けは口座のleaveで消さない(env, worker, capsys):
    token, blog = _principal(env, "hatenablog", "alpha-hatena", ["alpha", "beta"])
    _article_account(env, "alpha", "hatenablog", "alpha-hatena", blog)
    _article_account(env, "beta", "hatenablog", "alpha-hatena", blog)
    pending = _put_pending("alpha-hatena")
    rc, out, err = _leave("alpha", capsys)
    assert rc == 0, err
    assert os.path.exists(pending), "まだ使う口座がある主体の認可の途中を口座の leave で捨てた"


def test_forgetではてなの預けも消える(env):
    _principal(env, "hatenablog", "alpha-hatena", ["alpha"])
    pending = _put_pending("alpha-hatena")
    out, errs = [], []
    rc = principal_cli.run_auth("alpha-hatena", by="tester", forget=True, out=out.append, err=errs.append)
    assert rc == 0, errs
    assert not os.path.exists(pending), "--forget のあとに request token の預けが残っている"


def test_持ち主ごと畳むと主体の状態の置き場ごと預けが消える(env):
    _principal(env, "hatenablog", "alpha-hatena", ["alpha"])
    pending = _put_pending("alpha-hatena")
    kept = []
    done = leave.remove_principal("alpha-hatena", [], kept)
    assert done["principal_state"] >= 1, (done, kept)
    assert not os.path.exists(pending)
