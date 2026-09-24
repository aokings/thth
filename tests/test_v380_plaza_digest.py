"""3.8.0 E 生きたコツ集（設計 3.8.0 §E・`thth plaza digest <owner|project>`）。

2 媒体以上で reproduced の追試がそろった finding・measure を 1 枚に。**再現しなかった媒体も
並べる**（試していない媒体も）・分母つき。読める範囲は自分の project と同じ持ち主の組だけ。
"""
from __future__ import annotations

import json

import pytest

from thth import cli, plaza
from tests.test_v340_plaza_store import owners, post, reply  # noqa: F401  (fixture)
from tests.test_v380_plaza_owner import grouped, viewer  # noqa: F401  (fixture)


def _trial(plaza_id, account, result):
    return reply(plaza_id, account=account, kind="trial", result=result, text=None, by="x",
                 viewer=viewer(account))


def test_2媒体以上で再現したものだけ_再現しなかった媒体も並べる(grouped):
    plaza.set_owner("masaru", ["kopicha", "other"], by="operator")
    lived = post(account="kopicha-threads", title="冒頭を問いにする", visibility="owner",
                 kind_detail="pattern", goal="reply")
    _trial(lived["plaza_id"], "kopicha-bsky", "reproduced")
    _trial(lived["plaza_id"], "kopicha-mstdn", "reproduced")
    _trial(lived["plaza_id"], "other-threads", "not_reproduced")
    one_medium = post(account="kopicha-threads", title="1 媒体だけ")
    _trial(one_medium["plaza_id"], "kopicha-bsky", "reproduced")
    _trial(one_medium["plaza_id"], "kopicha-bsky", "reproduced")
    post(account="kopicha-threads", title="追試なし")
    payload = plaza.digest(viewer("kopicha-threads"), target="kopicha")
    assert payload["n"] == 1 and payload["denominator"] == 3 and payload["n_with_trials"] == 2
    item = payload["items"][0]
    assert item["plaza_id"] == lived["plaza_id"]
    assert item["media"] == {"reproduced": ["bluesky", "mastodon"], "not_reproduced": ["threads"],
                             "not_tried": []}
    assert item["trials"] == {"reproduced": 2, "not_reproduced": 1, "not_tried": 0, "denominator": 3}
    assert "body" not in item


def test_組の名前で読むと組の全projectの書き込み_組の外は入らない(grouped, capsys):
    plaza.set_owner("masaru", ["kopicha", "other"], by="operator")
    theirs = post(account="other-threads", title="other の型", by="o", visibility="owner")
    _trial(theirs["plaza_id"], "kopicha-threads", "reproduced")
    _trial(theirs["plaza_id"], "kopicha-bsky", "reproduced")
    _trial(theirs["plaza_id"], "kopicha-mstdn", "not_tried")
    inside = post(account="third-threads", title="THIRD-ONLY", by="t")
    rc = cli.main(["plaza", "digest", "masaru", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0 and payload["basis"] == "owner"
    assert [item["plaza_id"] for item in payload["items"]] == [theirs["plaza_id"]]
    assert payload["items"][0]["media"]["not_tried"] == ["mastodon"]
    # 組に入っていない project の digest には組の書き込みが入らない。
    third = plaza.digest(viewer("third-threads"), target="third")
    assert third["n"] == 0 and theirs["plaza_id"] not in json.dumps(third)
    assert inside["plaza_id"]
    rc = cli.main(["plaza", "digest", "masaru"])
    out = capsys.readouterr().out
    assert rc == 0 and out.startswith("生きたコツ集（masaru・2 媒体以上で再現した書き込み 1 件／")
    assert ("再現した媒体: bluesky・threads ／ 再現しなかった媒体: — ／ 試していない: mastodon"
            "（追試 3 件: 再現 2・再現せず 0・試していない 1）") in out


def test_openの写しと非表示は入れない(grouped):
    for project in ("kopicha", "other"):
        plaza.set_membership(project, joined=True, by="operator")
    theirs = post(account="other-threads", title="other の open", by="o", visibility="open")
    _trial(theirs["plaza_id"], "other-threads", "reproduced")
    assert plaza.digest(viewer("kopicha-threads"), target="kopicha")["n"] == 0
    mine = post(account="kopicha-threads", title="隠す")
    _trial(mine["plaza_id"], "kopicha-bsky", "reproduced")
    _trial(mine["plaza_id"], "kopicha-mstdn", "reproduced")
    assert plaza.digest(viewer("kopicha-threads"), target="kopicha")["n"] == 1
    plaza.hide(mine["plaza_id"], by="operator", reason="試験")
    assert plaza.digest(viewer("kopicha-threads"), target="kopicha")["n"] == 0


def test_知らない名前は断る(grouped, capsys):
    rc = cli.main(["plaza", "digest", "no-such"])
    assert rc == 2 and "invalid_account" in capsys.readouterr().err
    with pytest.raises(plaza.PlazaError, match="^invalid_account$"):
        plaza.viewer_for_digest("../x")
