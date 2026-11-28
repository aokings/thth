"""5.34.0: `thth live create --unpublished`（Meta 申請の録画・masaru 11/28）とフォロワー 100 人の断り。

見るのは:
  - `--unpublished` は `status=UNPUBLISHED` を送り、旗なしは今までどおり `LIVE_NOW`。
  - 二段確認の digest に公開か非公開かが入る（一段目で見せた形と違う形を二段目で打てない）。
  - 一段目の文が非公開の意味（ほかの人に見えない・数時間で消えることがある・100 人の決まりは未確認）を言う。
  - `--json` に `unpublished` が出る。宛先（配信の鍵）はどこにも残さない。
  - Meta の subcode 1363144（フォロワー 100 人未満）は前向きな断り `live_followers_too_few`（目録の 3 つ）。
偽の Graph API（loopback）だけを使う。本物の Meta は叩かない。
"""
from __future__ import annotations

import json

from thth import refusal_catalog, runs, accounts, jst
from tests.test_m4_meta_writes import (FB_ACCOUNT, PAGE, STREAM_KEY, V, m4, main, nothing_kept,  # noqa: F401
                                       two_steps, writes)

CREATE = ("live", "create", FB_ACCOUNT, "--title", "審査の録画", "--by", "m")


def test_非公開はUNPUBLISHEDを送りjsonに出し宛先を残さない(m4, capsys):
    (rc, out, err), first = two_steps(capsys, *CREATE, "--unpublished")
    assert rc == 0, err
    assert first["unpublished"] is True and first["status"] == "UNPUBLISHED"
    value = json.loads(out)
    assert value["unpublished"] is True and value["status"] == "UNPUBLISHED"
    assert STREAM_KEY in value["stream_url"] and value["storage"] == "display_only"
    posts = writes(m4)
    assert len(posts) == 1 and posts[0]["path"] == V + f"/{PAGE}/live_videos"
    assert posts[0]["form"]["status"] == ["UNPUBLISHED"]
    nothing_kept(m4, STREAM_KEY)


def test_旗なしは今までどおりLIVE_NOW(m4, capsys):
    (rc, out, err), first = two_steps(capsys, *CREATE)
    assert rc == 0, err
    assert first["unpublished"] is False and first["status"] == "LIVE_NOW"
    assert json.loads(out)["unpublished"] is False
    assert writes(m4)[0]["form"]["status"] == ["LIVE_NOW"]


def test_一段目で見せた形と違う形は二段目で打てない(m4, capsys):
    rc, out, _err = main(capsys, *CREATE, "--json")
    public = json.loads(out)["digest"]
    rc, out, _err = main(capsys, *CREATE, "--unpublished", "--json")
    private = json.loads(out)["digest"]
    assert public != private
    # 公開で見せた digest で非公開を打つ・その逆、どちらも打たない。
    rc, out, _err = main(capsys, *CREATE, "--unpublished", "--confirm", public, "--json")
    assert rc == 1 and json.loads(out)["error"] == "meta_write_digest_mismatch"
    rc, out, _err = main(capsys, *CREATE, "--confirm", private, "--json")
    assert rc == 1 and json.loads(out)["error"] == "meta_write_digest_mismatch"
    assert writes(m4) == []


def test_非公開の一段目は意味を言い換える(m4, capsys):
    rc, out, _err = main(capsys, *CREATE, "--unpublished")
    assert rc == 1
    assert "ほかの人には見えません" in out and "数時間で自動で消す" in out
    assert "100 人" in out and "未確認" in out
    assert "--unpublished" in out.split("打つなら:")[1]          # 打ち直しの命令にも旗が残る
    assert "すぐ「配信中」" not in out
    rc, out, _err = main(capsys, *CREATE)
    assert "すぐ「配信中」" in out and "ほかの人には見えません" not in out
    assert writes(m4) == []


def _followers_refusal(m4, code=100, message="(#100) Page must have at least 100 followers to go live"):
    m4["routes"][("POST", V + f"/{PAGE}/live_videos")] = (400, {"error": {
        "code": code, "error_subcode": 1363144, "type": "OAuthException", "message": message}})


def test_フォロワー100人未満は前向きな断りになる(m4, capsys):
    _followers_refusal(m4)
    (rc, out, err), _first = two_steps(capsys, *CREATE, "--unpublished")
    assert rc == 1
    value = json.loads(out)
    assert value["error"] == "live_followers_too_few"
    assert value.get("why") and value.get("fix") and value.get("better")
    month = jst.month_str(jst.now_jst())
    text = open(runs.path_for(accounts.state_dir_for(FB_ACCOUNT), month), encoding="utf-8").read()
    assert '"error": "live_followers_too_few"' in text


def test_フォロワーの断りは権限の不足に化けない(m4, capsys):
    # 文面に permission が入っても、subcode 1363144 なら認可し直しを勧めない。
    _followers_refusal(m4, code=200, message="Permission denied: not enough followers")
    (rc, out, err), _first = two_steps(capsys, *CREATE)
    assert rc == 1 and json.loads(out)["error"] == "live_followers_too_few"
    assert "meta_write_permission_missing" not in out + err and "thth auth" not in out + err


def test_目録に3つがそろう():
    entry = refusal_catalog.CATALOG["live_followers_too_few"]
    assert entry["why"] and entry["fix"]["text"] and entry["better"]
    assert "1363144" in entry["why"]
