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

from tests.test_v5160_wordpress import (ACCOUNT, PASSWORD, PRINCIPAL, approve, call, lane, posts_sent,  # noqa: F401
                                        throw, wp)
from thth import deliveries, principals, refusal
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
