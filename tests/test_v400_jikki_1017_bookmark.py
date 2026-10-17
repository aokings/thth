"""本物のはてなで 4.0 S4 を確かめて見つけたことの直し（docs/記録_はてなの実機の確かめ_2026-10-17.md の 7〜9）。

  - 7: 主体の `bookmark.lanes` に無いレーンの口座で `thth bookmark add` の 1 段目が 2 段目の断りを予告する。
       直す先は主体の台帳（`thth principal set <主体> bookmark.lanes <a,b>`・admin_log に残る）。
  - 8: 主体の枠の断りの文は「数は … で変えられます」を主体の方だけ言う（`account set` を重ねない）。
  - 9: 消した印（retracted_at）のある行に `thth bookmark delete` を打つと「もう消しています（日時）」。

HTTP は偽物（`httpsafe.urlopen` の差し替え）。**はてなには一切つながない。**
"""
from __future__ import annotations

import json

import pytest

from tests.hatena_s1_flow_helpers import call  # noqa: F401
from tests.hatena_s4_helpers import ACCOUNT, EXTERNAL, bm, bookmark, cfg, fake_http, world  # noqa: F401
from thth import admin_log, core, guard, hatena_bookmark, principals


def _stage1_add(url=EXTERNAL):
    return call(["bookmark", "add", ACCOUNT, "--url", url, "--comment", "相場の頁"])


# ---------------------------------------------------------------- 7: レーンの予告と principal set bookmark.lanes

def test_7_許すレーンに無ければ1段目で予告し2段目は主体の台帳を指して断る(bm):
    """柵: 1 段目の予告（`lines` の lane_allowed）を消すと赤。"""
    bm.edit_principal("hatena-main", bookmark={"lanes": ["lane-b"]})
    rc, out, err = _stage1_add()
    assert rc == 1
    assert "2 段目で断ります（bookmark_not_allowed_for_lane）" in out
    assert "thth principal set hatena-main bookmark.lanes lane-b,lane-a --by <名前>" in out
    digest = next(line.split(": ", 1)[1] for line in out.splitlines() if line.startswith("digest: "))
    rc, out, err = call(["bookmark", "add", ACCOUNT, "--url", EXTERNAL, "--comment", "相場の頁",
                         "--confirm", digest, "--by", "tester", "--production"])
    assert rc == 1 and "bookmark_not_allowed_for_lane" in err
    assert "thth principal set hatena-main bookmark.lanes" in err and "account set" not in err


def test_7_許すレーンにあれば予告しない(bm):
    rc, out, err = _stage1_add()
    assert rc == 1 and "2 段目で断ります（bookmark_not_allowed_for_lane）" not in out
    p = hatena_bookmark.prepare(ACCOUNT, cfg(ACCOUNT), url=EXTERNAL, comment="c")
    assert p["lane_allowed"] is True and hatena_bookmark.preview(p)["lane_allowed"] is True


def test_7_principal_setでbookmark_lanesを変えadmin_logに残す(bm):
    """柵: `change` の bookmark.lanes の枝を消すと赤（invalid_setting）。"""
    bm.edit_principal("hatena-main", bookmark={"lanes": ["lane-b"]})
    rc, out, err = call(["principal", "set", "hatena-main", "bookmark.lanes", "lane-b,lane-a,lane-a", "--by", "tester"])
    assert rc == 0, err
    assert "bookmark.lanes lane-b → lane-b,lane-a" in out
    assert principals.load_principal("hatena-main")["bookmark"]["lanes"] == ["lane-b", "lane-a"]
    rows, _broken = admin_log.read(event="settings_set")
    assert rows[-1]["account"] == "hatena-main" and rows[-1]["diff"] == {"bookmark.lanes": [["lane-b"], ["lane-b", "lane-a"]]}
    # これで 1 段目は予告しない
    rc, out, err = _stage1_add()
    assert "2 段目で断ります（bookmark_not_allowed_for_lane）" not in out
    # 同じ値は変えない・`-` で空に・使えない字は断る
    rc, out, err = call(["principal", "set", "hatena-main", "bookmark.lanes", "lane-b,lane-a", "--by", "tester"])
    assert rc == 0 and "変えていません" in out
    rc, out, err = call(["principal", "set", "hatena-main", "bookmark.lanes", "-", "--by", "tester", "--json"])
    assert rc == 0 and json.loads(out)["after"] == []
    assert principals.load_principal("hatena-main")["bookmark"]["lanes"] == []
    rc, out, err = call(["principal", "set", "hatena-main", "bookmark.lanes", "lane a/../x", "--by", "tester"])
    assert rc == 2 and "invalid_setting" in err
    assert principals.load_principal("hatena-main")["bookmark"]["lanes"] == []


# ---------------------------------------------------------------- 8: 主体の枠の文は主体の set だけ

@pytest.mark.parametrize("code", ["principal_daily_limit", "bookmark_limit", "self_bookmark_limit",
                                  "bookmark_host_limit"])
def test_8_主体の枠の断りはaccount_setを重ねない(bm, code):
    """柵: `PRINCIPAL_REFUSALS` の枝を外すと赤（account set が 2 回目に出る）。"""
    msg = core.guard_refusal_message(ACCOUNT, guard.GuardRefused(code, next_at="2026-09-10T00:00:00+09:00"))
    assert msg.count("で変えられます") == 1 and "thth principal set" in msg and "account set" not in msg


def test_8_口座の枠の断りは今までどおりaccount_set(bm):
    msg = core.guard_refusal_message(ACCOUNT, guard.GuardRefused("daily_limit", next_at="2026-09-10T00:00:00+09:00"))
    assert msg.count("で変えられます") == 1 and f"thth account set {ACCOUNT}" in msg


# ---------------------------------------------------------------- 9: 消したあとの削除

def test_9_消した行をもう一度消すとbookmark_already_deletedで日時を言う(bm):
    """柵: `_check_deletable` の retracted_at の枝を外すと赤（bookmark_not_found に戻る）。"""
    bookmark(ACCOUNT, url=EXTERNAL, comment="c")
    c = cfg(ACCOUNT)
    p = hatena_bookmark.prepare(ACCOUNT, c, url=EXTERNAL, op="delete")
    hatena_bookmark.delete_locked(ACCOUNT, c, p, actor="tester", via="cli")
    with pytest.raises(hatena_bookmark.BookmarkError) as caught:
        hatena_bookmark.prepare(ACCOUNT, c, url=EXTERNAL, op="delete")
    assert caught.value.code == "bookmark_already_deleted" and caught.value.at
    rc, out, err = call(["bookmark", "delete", ACCOUNT, "--url", EXTERNAL])
    assert rc == 2
    assert f"bookmark_already_deleted: このブックマークはもう消しています（{caught.value.at} に削除）" in err
    assert "記録がありません" not in err
    # ロックの中（2 段目）でも同じ符丁
    with pytest.raises(hatena_bookmark.BookmarkError) as again:
        hatena_bookmark.delete_locked(ACCOUNT, c, p, actor="tester", via="cli")
    assert again.value.code == "bookmark_already_deleted"
    # 遠くの道の口から出してよい静的な語
    from thth import server_writes
    assert "bookmark_already_deleted" in server_writes.SAFE_ERRORS


def test_9_記録の無いURLは今までどおりbookmark_not_found(bm):
    with pytest.raises(hatena_bookmark.BookmarkError) as caught:
        hatena_bookmark.prepare(ACCOUNT, cfg(ACCOUNT), url=EXTERNAL, op="delete")
    assert caught.value.code == "bookmark_not_found"
