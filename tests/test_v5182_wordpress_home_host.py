"""5.18.2 の候補: REST の入口のホストと、記事の URL のホスト（WordPress の「サイトアドレス」＝home）が違うサイト
（5.16.0 監査 の 7）。

前は、鍵を入れる時点（`thth principal auth`・`thth doctor`）では何も言わず、1 本めが**出たあと**に
`_record_outcome` が `published_url` のホストを入口のホストと比べて conflict で止まった（記事は出たのに台帳は止まる）。

直し: `users/me` の応答の `link`（その利用者の著者の頁。WordPress は home の上に組む）のホストを入口のホストと
比べ、違えば鍵を入れる時点で `wordpress_home_host_mismatch` で知らせる。新しい REST の道は足さない（`ROUTES` は
4 つのまま）。比べるのは「知らせる」ためだけで、出す側の柵（出たあとの conflict）は緩めない。
`link` が無い・読めない応答では何も言わない（分からないことを「合っている」とも「違う」とも言わない）。

送る先は手元の偽の WordPress（127.0.0.1・`THTH_TEST_ALLOW_HTTP=1` の柵の内）。本物のサイトには何も送らない。
"""
from __future__ import annotations

import json

import pytest

from tests.test_v5160_wordpress import (ACCOUNT, PASSWORD, PRINCIPAL, WP_USER, approve, call, lane,  # noqa: F401
                                        posts_sent, throw, wp)
from thth import accounts, deliveries, doctor, principals, refusal
from thth.adapters import wordpress

CODE = "wordpress_home_host_mismatch"


def home_elsewhere(lane):
    """記事の URL を別のホスト（localhost）に組むサイトにする。入口は 127.0.0.1 のまま。"""
    lane.wp.home = f"http://localhost:{lane.wp.port}"
    return lane.wp.home


def auth(*extra):
    return call(["principal", "auth", PRINCIPAL, "--by", "tester", "--stdin", *extra], stdin=PASSWORD + "\n")


def adapter(lane):
    return wordpress.factory({"blog_id": "wp.test", "token": str(lane.token), "media": "wordpress",
                              "principal": PRINCIPAL}, principals.load_principal(PRINCIPAL))


def test_記事のURLのホストが入口と違えば鍵を入れる時点で知らせる(lane):
    home_elsewhere(lane)
    rc, out, err = auth()
    said = out + err
    assert rc == 1, said
    assert "localhost" in said and "127.0.0.1" in said            # どのホストとどのホストが違うか
    assert "通すには:" in said and "もっと良く:" in said              # 前向きな断り（なぜ・通すには・もっと良く）
    assert PASSWORD not in said
    assert lane.token.exists()                                    # 鍵は置いた（入口を直せば打ち直さずに済む）
    assert posts_sent(lane.wp) == []                              # 記事は 1 本も出ていない
    who = adapter(lane).whoami()
    assert (who["ok"], who["error"], who["home_host"]) == (False, CODE, "localhost")


def test_doctorも同じことを知らせる(lane):
    home_elsewhere(lane)
    auth()
    rc, out, err = call(["doctor", ACCOUNT])
    said = out + err
    assert rc != 0 and CODE in said and "localhost" in said, said


def test_入口を記事のURLのホストにそろえれば鍵が通り出せる(lane):
    home = home_elsewhere(lane)
    auth()
    data = json.loads(lane.principal_path().read_text(encoding="utf-8"))
    data["blogs"][0]["site_url"] = home                           # 通し方: 入口を home のホストに
    lane.principal_path().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    rc, out, err = auth("--force")
    assert rc == 0, out + err
    path = lane.queue()
    approve(path)
    result = throw()
    assert (result.action, result.exit_code) == ("post", 0), result


def real_shaped(lane, monkeypatch, blog_id, link):
    """本物の形のホスト（loopback の抜け道を通らない）。主体のブログの行は site_url 無し・口座の blog_id も同じ。
    網には出さない: `_get` を差し替え、users/me 以外の道が呼ばれたら落とす。"""
    body = {"id": 2, "username": WP_USER, "roles": ["editor"], "link": link}

    def fake_get(self, url, *, op="get"):
        assert "/wp/v2/users/me" in url, url                          # 読むのは users/me だけ
        return body
    monkeypatch.setattr(wordpress.WordPressAdapter, "_get", fake_get)
    set_blog_id(lane, blog_id)


def set_blog_id(lane, blog_id, *, principal=True, account=True, site_url=None):
    if principal:
        data = json.loads(lane.principal_path().read_text(encoding="utf-8"))
        data["blogs"][0] = {"blog_id": blog_id, "lanes": ["lane-w"], **({"site_url": site_url} if site_url else {})}
        lane.principal_path().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    if account:
        path = lane.root / "accounts" / f"{ACCOUNT}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["blog_id"] = blog_id
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_本物の形のホストでも文のとおりに直せば通る(lane, monkeypatch):
    """監査（次の版・2026-11-13）の直したほうがよい 1。blog_id が example.com・記事の URL が www.example.com の
    サイトで、前の文（site_url だけをそろえる）は site_url_invalid で主体の台帳ごと読めなくなった。文は
    主体のブログの行と口座の台帳の blog_id を両方そろえる道を言い、そのとおりに直すと鍵も doctor も通る。"""
    real_shaped(lane, monkeypatch, "example.com", "https://www.example.com/author/editor1/")
    rc, out, err = auth()
    said = out + err
    assert rc == 1 and CODE in said and "www.example.com" in said, said
    fix = refusal.parts(CODE)["fix"]
    for told in (said, fix):                                      # 断りの目録と principal auth の文
        assert "主体のブログの行" in told and "口座の台帳" in told and "blog_id" in told, told
    rc, out, err = call(["doctor", ACCOUNT])
    assert rc != 0 and CODE in out + err, out + err
    assert "口座の台帳" in out + err and "blog_id" in out + err, out + err   # doctor の文も同じ道
    # 前の文の道（site_url だけ）は主体の台帳ごと読めなくなる。文はこの道を言わない。
    set_blog_id(lane, "example.com", account=False, site_url="https://www.example.com")
    with pytest.raises(Exception):
        principals.load_principal(PRINCIPAL)
    # 片方だけ（主体の行だけ）では口座と主体が噛み合わない。
    set_blog_id(lane, "www.example.com", account=False)
    with pytest.raises(Exception):
        wordpress.factory(accounts.load_account(ACCOUNT), principals.load_principal(PRINCIPAL))
    # 文のとおり: 両方の blog_id を記事の URL のホストに（site_url は書かない）。
    set_blog_id(lane, "www.example.com")
    rc, out, err = auth("--force")
    assert rc == 0 and CODE not in out + err, out + err
    rc, out, err = call(["doctor", ACCOUNT])
    assert CODE not in out + err, out + err
    [probe] = [p for p in doctor.read_observation(ACCOUNT)["probes"] if p["key"] == "wp_users_me"]
    assert probe["ok"] is True, probe


def test_違うときのホストは画面と診断の記録に入り鍵を入れるだけでは残らない(lane):
    """監査（次の版・2026-11-13）の直したほうがよい 2。whoami の注記と privacy の文が言うことの事実の柵。
    principal auth は画面で言うだけ（state に何も残さない）。doctor は doctor.json の detail に両方のホストを残す。"""
    home_elsewhere(lane)
    auth()
    state = lane.root / "state"
    left = [p for p in state.rglob("*") if p.is_file() and "localhost" in p.read_text(encoding="utf-8", errors="replace")]
    assert left == [], left                                       # 鍵を入れるだけではホストを残さない
    call(["doctor", ACCOUNT])
    [probe] = [p for p in doctor.read_observation(ACCOUNT)["probes"] if p["key"] == "wp_users_me"]
    assert probe["ok"] is False and "localhost" in probe["detail"] and "127.0.0.1" in probe["detail"], probe
    assert PASSWORD not in (state / ACCOUNT / "doctor.json").read_text(encoding="utf-8")


def test_知らせを聞かずに出しても出たあとの柵は緩めず誤った成功を言わない(lane):
    home_elsewhere(lane)
    auth()
    path = lane.queue()
    approve(path)
    result = throw()
    assert result.exit_code == 1 and result.error == "published_elsewhere", result
    from tests.test_v5160_wordpress import fm_of
    assert deliveries.read(ACCOUNT, fm_of(path)["delivery"])["state"] == "conflict"


def test_同じホストか分からない応答では何も言わない(lane):
    lane.wp.home = lane.wp.base                                    # 同じホスト
    rc, out, err = auth()
    assert rc == 0 and CODE not in out + err, out + err
    lane.wp.home = None                                            # users/me に link が無い
    who = adapter(lane).whoami()
    assert who["ok"] is True and who["error"] is None and who["home_host"] is None


def test_REST_の道は増やさない():
    assert wordpress.ROUTES == ("posts", "categories", "tags", "users/me")


def test_目録になぜと通すにはともっと良くがある():
    said = refusal.parts(CODE)
    assert said and said["why"] and said["fix"] and said["better"]
    assert CODE in wordpress.WP_REFUSALS
