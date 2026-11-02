"""5.6.3: boost のリンクの印（url_tags）と、サイトへの誘導（traffic）のあとの次の一手。偽の Meta だけ。

- `--url-tags`（無ければ台帳の `url_tags`）をクリエイティブの `url_tags` として送る（AdCreative の文書）。
- traffic で作ったら、ピクセルを**読むだけ**で確かめ、動いていなければ次の一手を返す。**先頭は skill。**
- 書く口は増やさない（ピクセルを作る・変える POST は無い）。
"""
from __future__ import annotations

import json

import pytest

from thth.ads import guide, ledger, ops
from tests.test_meta_stage5 import ACT, NAME, STORY, V, end_in, env, posts, setup_ledger  # noqa: F401


def test_url_tagsを付けるとクリエイティブにだけ送り_記録に残す(env):
    setup_ledger(env)
    out = ops.boost(NAME, by="masaru", object_story_id=STORY, lifetime_minor=500, start="now", end=end_in(1),
                    url_tags="utm_source=facebook&utm_medium=paid&utm_campaign=kopicha")
    creative = posts(env, "/adcreatives")[0]["form"]
    assert creative["url_tags"] == ["utm_source=facebook&utm_medium=paid&utm_campaign=kopicha"]
    assert creative["object_story_id"] == [STORY]
    assert all("url_tags" not in c["form"] for c in posts(env) if not c["path"].endswith("adcreatives"))
    assert out["plan"]["url_tags"].startswith("utm_source=facebook")
    rows = [r for r in ops.read_ops(NAME) if r["op"] == "boost"]
    assert rows[-1]["plan"]["url_tags"] == out["plan"]["url_tags"]
    # 印が無ければ送らない（既定の振る舞いは変えない）
    ops.boost(NAME, by="masaru", object_story_id=STORY, lifetime_minor=500, start="now", end=end_in(1))
    assert "url_tags" not in posts(env, "/adcreatives")[-1]["form"]
    assert [s["kind"] for s in ops.boost(NAME, by="masaru", object_story_id=STORY, lifetime_minor=500, start="now",
                                         end=end_in(1))["next_steps"]] == ["skill"]


def test_utm_campaignだけなら有機と同じ鍵でmediumだけpaidの印を組む(env):
    """10/27 に窓口が決めた形（有機 utm_medium=social）に揃え、広告は paid・campaign は原稿のファイル名。"""
    setup_ledger(env)
    out = ops.boost(NAME, by="masaru", object_story_id=STORY, lifetime_minor=500, start="now", end=end_in(1),
                    utm_campaign="2026-11-04-fb-sakura")
    tags = "utm_source=facebook&utm_medium=paid&utm_campaign=2026-11-04-fb-sakura"
    assert posts(env, "/adcreatives")[0]["form"]["url_tags"] == [tags] and out["plan"]["url_tags"] == tags
    # --url-tags を渡せばそちらが先
    ops.boost(NAME, by="masaru", object_story_id=STORY, lifetime_minor=500, start="now", end=end_in(1),
              utm_campaign="x", url_tags="utm_source=a")
    assert posts(env, "/adcreatives")[-1]["form"]["url_tags"] == ["utm_source=a"]
    with pytest.raises(ops.OpsRefused) as exc:
        ops.boost(NAME, by="masaru", object_story_id=STORY, lifetime_minor=500, start="now", end=end_in(1),
                  utm_campaign="a&utm_source=b")
    assert exc.value.code == "ads_utm_campaign_invalid"


def test_台帳のurl_tagsとlanding_urlを使い_形の違う物は作る前に断る(env):
    setup_ledger(env)
    path = ledger.path_for(NAME)
    raw = json.loads(open(path, encoding="utf-8").read())
    raw.update(url_tags="utm_source=facebook&utm_campaign={{campaign.id}}", landing_url="https://kopicha.example/lp")
    open(path, "w", encoding="utf-8").write(json.dumps(raw))
    out = ops.boost(NAME, by="masaru", object_story_id=STORY, lifetime_minor=500, start="now", end=end_in(1))
    assert posts(env, "/adcreatives")[0]["form"]["url_tags"] == ["utm_source=facebook&utm_campaign={{campaign.id}}"]
    assert out["plan"]["landing_url"] == "https://kopicha.example/lp"
    before = len(posts(env))
    for kwargs, code in (({"url_tags": "utm_source=a b"}, "ads_url_tags_invalid"),
                         ({"url_tags": "javascript:alert(1)"}, "ads_url_tags_invalid"),
                         ({"landing_url": "ftp://x"}, "ads_landing_url_invalid")):
        with pytest.raises(ops.OpsRefused) as exc:
            ops.boost(NAME, by="masaru", object_story_id=STORY, lifetime_minor=500, start="now", end=end_in(1),
                      **kwargs)
        assert exc.value.code == code
    assert len(posts(env)) == before
    raw["url_tags"] = "壊れた 印"
    open(path, "w", encoding="utf-8").write(json.dumps(raw))
    with pytest.raises(ledger.AdAccountError):
        ledger.load(NAME)


def test_trafficで作ったらピクセルを読み_動いていなければ入れる一手を返す_先頭はskill(env):
    setup_ledger(env)
    env["meta"].fail[("GET", V + f"/act_{ACT}/adspixels")] = (200, {"data": [{"id": "555", "name": "p"}]})
    out = ops.boost(NAME, by="masaru", object_story_id=STORY, lifetime_minor=500, start="now", end=end_in(1),
                    objective="traffic", landing_url="https://kopicha.example/lp")
    kinds = [s["kind"] for s in out["next_steps"]]
    assert kinds == ["skill", "pixel_install", "url_tags_unset"]
    assert out["skill"]["name"] == "thth-ads"
    assert "誘導先 https://kopicha.example/lp のサイトにピクセル 555 の基本のコードを入れる" in out["next_steps"][1]["text"]
    assert guide.PIXEL_DOC_URL in out["next_steps"][1]["text"]
    # ピクセルが動いていて印も付けたなら skill だけ
    env["meta"].fail[("GET", V + f"/act_{ACT}/adspixels")] = (
        200, {"data": [{"id": "555", "last_fired_time": "2026-11-03T05:00:00+0000"}]})
    out = ops.boost(NAME, by="masaru", object_story_id=STORY, lifetime_minor=500, start="now", end=end_in(1),
                    objective="traffic", url_tags="utm_source=facebook")
    assert [s["kind"] for s in out["next_steps"]] == ["skill"]
    # ピクセルを読む口は GET だけ（作る・変える POST は無い）
    assert not any("adspixels" in c["path"] for c in posts(env))
