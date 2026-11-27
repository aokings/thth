"""monban の秘密の入力口の前提（2026-11-27）: WordPress の鍵は、書く前に `users/me` を読み、鍵の利用者が主体の
`wp_user` と合ったときだけ書く。合わなければ今の鍵を置き換えない（前は書いてから読んでいた）。

送る先は手元の偽の WordPress（127.0.0.1・`THTH_TEST_ALLOW_HTTP=1` の柵の内）。鍵は試験の値。
"""
from __future__ import annotations

import json

from tests.test_v5160_wordpress import PASSWORD, PRINCIPAL, WP_USER, call, lane, wp  # noqa: F401
from thth.adapters import wordpress

OTHER = "zyxw VUTS rqpo NMLK jihg FEDC"   # 試験の値（形だけ合わせた・本物ではない）


def auth(value, *extra):
    return call(["principal", "auth", PRINCIPAL, "--by", "tester", "--stdin", *extra], stdin=value + "\n")


def other_user(monkeypatch):
    """users/me が別の利用者を返すサイト（鍵は通るが wp_user と違う）。"""
    def fake_get(self, url, *, op="get"):
        assert "/wp/v2/users/me" in url, url
        return {"id": 7, "username": "someone-else", "roles": ["editor"]}
    monkeypatch.setattr(wordpress.WordPressAdapter, "_get", fake_get)


def test_利用者が違えば初めての鍵も書かない(lane, monkeypatch):
    other_user(monkeypatch)
    rc, out, err = auth(PASSWORD)
    said = out + err
    assert rc == 1, said
    assert "wp_user_mismatch" in said and WP_USER in said, said
    assert not lane.token.exists(), "利用者が違うのに鍵を書いた"
    assert PASSWORD not in said


def test_利用者が違えば今の鍵を置き換えない(lane, monkeypatch):
    rc, out, err = auth(PASSWORD)
    assert rc == 0, out + err
    before = lane.token.read_bytes()
    other_user(monkeypatch)
    rc, out, err = auth(OTHER, "--force")
    said = out + err
    assert rc == 1 and "wp_user_mismatch" in said and "今の鍵はそのまま" in said, said
    assert lane.token.read_bytes() == before, "利用者が違うのに今の鍵を置き換えた"
    assert json.loads(before)["application_password"] == PASSWORD
    assert OTHER not in said


def test_鍵が通らなければ書かない(lane):
    rc, out, err = auth(OTHER)            # 偽のサイトは PASSWORD 以外を 401 で断る
    said = out + err
    assert rc == 1 and "wordpress_auth_rejected" in said, said
    assert not lane.token.exists()


def test_書く前に読むのは受け取った鍵で読む(lane):
    rc, out, err = auth(PASSWORD)
    assert rc == 0, out + err
    me = [c for c in lane.wp.calls if c[1] == "/wp-json/wp/v2/users/me"]
    assert me, "users/me を読んでいない"
    assert json.loads(lane.token.read_text(encoding="utf-8"))["wp_user"] == WP_USER
