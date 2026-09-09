"""`thth doctor`（統括が 2026-09-09 に追加）。

管理画面の表示とトークンの実力が一致しないことが分かったので、実際に叩いて測る
道具を持った。ここで確かめるのは 2 つ:
  - **トークンの値が出力に出ない**こと（診断のたびに漏れては本末転倒）
  - **読み取りしか呼ばない**こと（doctor が副作用を持つと「様子を見るつもりが出た」が起きる）
"""
from __future__ import annotations

import json

from tests.helpers.fake_oauth_server import fake_oauth_server
from thth import doctor as doctor_mod


def _write_token(path, token="DOCTOR-SECRET-TOKEN"):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"access_token": token, "obtained_at": "2026-09-09T00:00:00+09:00",
                   "expires_in": 5184000, "user_id": "999999",
                   "username": "nigamilab", "scopes": None}, f)


def test_20260909_doctor_must_not_print_the_token(tmp_path, monkeypatch, isolated_account_factory):
    token_path = str(tmp_path / "a.token")
    account = isolated_account_factory(token=token_path, handle="nigamilab")
    _write_token(token_path)

    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        lines = []
        doctor_mod.run_doctor(account["name"], log=lines.append)

    out = "\n".join(lines)
    assert "DOCTOR-SECRET-TOKEN" not in out
    assert "nigamilab" in out


def test_doctor_は書き込みの口を持たない():
    """副作用を持つ語（publish・delete・POST）が doctor に無いことを機械で見る。"""
    import inspect
    import re
    src = inspect.getsource(doctor_mod)
    # `threads_publishing_limit`（読み取り）に引っかからないよう、語の切れ目まで見る。
    forbidden = [
        r"threads_publish(?!ing)",   # 公開
        r"method=\"POST\"",          # 書き込み
        r"manage_reply",             # 返信の作成・非表示
        r"threads_delete",           # 削除
    ]
    for pat in forbidden:
        assert re.search(pat, src) is None, f"doctor に書き込みらしき語がある: {pat}"
    # 唯一の HTTP 呼び出しが GET（データを持たない urlopen）であること。
    assert src.count("urllib.request.urlopen(") == 1
    assert "data=" not in src


def test_トークンが無ければその旨を返す(tmp_path, isolated_account_factory):
    account = isolated_account_factory(token=str(tmp_path / "missing.token"))
    lines = []
    rc = doctor_mod.run_doctor(account["name"], log=lines.append)
    assert rc == 2
    assert "トークンが無い" in "\n".join(lines)
