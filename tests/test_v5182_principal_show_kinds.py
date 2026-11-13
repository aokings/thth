"""5.18.2: `thth principal show <WordPress の主体>` が `KeyError: 'hatena_id'` で落ちた
（監査 次の版・2026-11-13 の「直しの確かめ（645f37f）」の直したほうがよい 4）。

WordPress の主体の台帳の名乗りの欄は `wp_user` で、`hatena_id` は無い。断りの目録の 8 つが次の一手に
`thth principal show <主体>` を出すので、案内のとおりに打つと例外になっていた。名乗りの欄は主体の種類の表
（`principals.ARTICLE_KINDS` の `id_field`）から引く。はてなの主体の出力（`hatena_id` の欄）は前のまま。

送る先は無い（台帳を読むだけ）。本物の WordPress・はてなには何も送らない。
"""
from __future__ import annotations

import json

from tests.test_v5160_wordpress import PASSWORD, PRINCIPAL, WP_USER, call, lane, wp  # noqa: F401
from thth import principals


def test_WordPressの主体でprincipal_showが落ちずに利用者名を出す(lane):
    rc, out, err = call(["principal", "show", PRINCIPAL])
    assert rc == 0, out + err
    assert "Traceback" not in out + err and "KeyError" not in out + err
    first = out.splitlines()[0]
    assert PRINCIPAL in first and "wordpress" in first and WP_USER in first, first
    assert "はてな ID" not in out                                   # WordPress の主体にはてな ID は無い
    assert "consumer" not in out and "ブックマークを打てるレーン" not in out   # はてなだけの欄は出さない
    assert "auth app_password" in first                            # 既定の認証は種類の表から（oauth ではない）
    assert "入口 http://127.0.0.1:" in out                          # 断りが「見て」と言う blog_id と site_url
    assert "ブログ wp.test" in out
    assert PASSWORD not in out + err


def test_WordPressの主体でprincipal_show_jsonが落ちずにwp_userを返す(lane):
    rc, out, err = call(["principal", "show", PRINCIPAL, "--json"])
    assert rc == 0, out + err
    row = json.loads(out)
    assert row["kind"] == "wordpress" and row["wp_user"] == WP_USER and row["id_field"] == "wp_user"
    assert "hatena_id" not in row
    assert row["auth"] == "app_password"
    assert row["blogs"][0]["blog_id"] == "wp.test"
    assert PASSWORD not in out


def test_はてなの主体のprincipal_showは前のまま(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "accounts").mkdir(parents=True)
    (root / "principals").mkdir(mode=0o700)
    monkeypatch.setenv("THTH_ROOT", str(root))
    monkeypatch.setenv("THTH_ACCOUNTS_DIR", str(root / "accounts"))
    monkeypatch.delenv("THTH_PRINCIPALS_DIR", raising=False)
    data = {"principal": "hb-main", "kind": "hatena", "hatena_id": "kopicha-test",
            "token": str(tmp_path / "hb.token"), "blogs": [{"blog_id": "kopicha.hatenablog.com", "lanes": ["lane-h"]}]}
    (root / "principals" / "hb-main.json").write_text(json.dumps(data), encoding="utf-8")
    rc, out, err = call(["principal", "show", "hb-main"])
    assert rc == 0, out + err
    assert "はてな ID kopicha-test" in out.splitlines()[0] and "auth oauth" in out.splitlines()[0]
    assert "consumer:" in out and "ブックマークを打てるレーン" in out
    rc, out, err = call(["principal", "show", "hb-main", "--json"])
    row = json.loads(out)
    assert rc == 0 and row["hatena_id"] == "kopicha-test" and row["auth"] == "oauth", row


def test_主体のどの種類でも名乗りの欄に画面の名がある():
    """種類を足したとき、`principal show` の 1 行めの名が欄の名のまま出ないように。"""
    from thth import principal_cli

    for kind, spec in principals.ARTICLE_KINDS.items():
        assert spec["id_field"] in principal_cli._ID_LABELS, kind
