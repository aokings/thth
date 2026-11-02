"""5.6.3: 広告の読む口の追加（masaru 11/03「全て足して下さい」）。偽の Graph だけ。**本物の Meta は叩かない。**

- 効率（frequency・cpm・ctr・cpc ほか・Meta の計算済み）と動画（動画でない広告では出さない）
- 内訳（--by）・出す前の見積もり（estimate）・ピクセル（doctor・show・insights の次の一手）
- **skill を先に示す**: 広告の次の一手（next_steps）とエラーの文の先頭は skill thth-ads
"""
from __future__ import annotations

import json
import os

from thth import cli
from thth.ads import graphread, guide, ledger
from tests.test_meta_stage4 import NAME, env, ready  # noqa: F401  (env は fixture)

ROW = {"spend": "470", "impressions": "1000", "clicks": "60", "reach": "800",
       "date_start": "2026-11-01", "date_stop": "2026-11-03"}


def _route(**extra):
    return lambda query: (200, {"data": [dict(ROW, **extra)]})


def _json(capsys, argv, rc=0):
    capsys.readouterr()
    assert cli.main([*argv, "--json"]) == rc
    return json.loads(capsys.readouterr().out)


# ------------------------------------------------------------------ 効率・動画

def test_効率はMetaの計算済みの値をそのまま出す(env, capsys):
    ready(env)
    env["insights_route"] = _route(frequency="1.25", cpm="470.00", ctr="6.0", cpc="7.833333",
                                   cost_per_unique_click="9.4", inline_link_click_ctr="0.5",
                                   outbound_clicks_ctr=[{"action_type": "outbound_click", "value": "0.3"}],
                                   cost_per_outbound_click=[{"action_type": "outbound_click", "value": "156.67"}])
    row = _json(capsys, ["ads", "insights", NAME])["rows"][0]
    eff = row["efficiency"]
    assert eff["frequency"] == "1.25" and eff["cpm"] == "470.00" and eff["ctr"] == "6.0" and eff["cpc"] == "7.833333"
    assert eff["outbound_clicks_ctr"] == "0.3" and eff["cost_per_outbound_click"] == "156.67"
    assert eff["cost_per_inline_link_click"] is None   # 返らなかった欄は None（推して埋めない）
    capsys.readouterr()
    cli.main(["ads", "insights", NAME])
    assert "効率（Meta の計算）: 1 人あたりの表示 1.25・1000 表示あたり 470.00" in capsys.readouterr().out


def test_動画の欄が返らない広告では動画を出さない_返れば深さを出す(env, capsys):
    ready(env)
    env["insights_route"] = _route()
    row = _json(capsys, ["ads", "insights", NAME])["rows"][0]
    assert row["video"] is None
    capsys.readouterr()
    cli.main(["ads", "insights", NAME])
    assert "動画:" not in capsys.readouterr().out
    vv = lambda n: [{"action_type": "video_view", "value": str(n)}]   # noqa: E731
    env["insights_route"] = _route(video_play_actions=vv(100), video_p25_watched_actions=vv(60),
                                   video_p50_watched_actions=vv(40), video_p75_watched_actions=vv(20),
                                   video_p95_watched_actions=vv(10), video_p100_watched_actions=vv(8),
                                   video_avg_time_watched_actions=vv(4), cost_per_thruplay=vv(12))
    video = _json(capsys, ["ads", "insights", NAME])["rows"][0]["video"]
    assert video == {"plays": "100", "p25": "60", "p50": "40", "p75": "20", "p95": "10", "p100": "8",
                     "avg_time_watched": "4", "cost_per_thruplay": "12"}


def test_頼む欄に確かめられなかった名前を入れない():
    fields = graphread.INSIGHT_FIELDS.split(",")
    for name in ("quality_ranking", "engagement_rate_ranking", "conversion_rate_ranking", "unique_clicks",
                 "unique_ctr", "video_thruplay_watched_actions"):
        assert name not in fields
    assert {"frequency", "cpm", "ctr", "cpc", "video_p25_watched_actions", "cost_per_thruplay"} <= set(fields)


# ------------------------------------------------------------------ 内訳

def test_内訳はbreakdownsで頼み_本筋の欄だけにして1区分1行で出す(env, capsys):
    ready(env)

    def route(query):
        assert query["breakdowns"] == ["age,gender"]
        assert query["fields"] == [graphread.INSIGHT_FIELDS_BASE]
        return 200, {"data": [dict(ROW, age="25-34", gender="female", actions=[{"action_type": "post_reaction",
                                                                                "value": "9"}]),
                              dict(ROW, age="35-44", gender="male")]}
    env["insights_route"] = route
    value = _json(capsys, ["ads", "insights", NAME, "--by", "age-gender"])
    assert value["breakdown"] == "age-gender" and value["breakdown_fields"] == "age,gender"
    assert value["rows"][0]["breakdown"] == {"age": "25-34", "gender": "female"}
    assert value["rows"][0]["efficiency"] is None and value["rows"][0]["extras_asked"] is False
    capsys.readouterr()
    cli.main(["ads", "insights", NAME, "--by", "age-gender"])
    out = capsys.readouterr().out
    assert "25-34・female: spend 470・表示 1000・リーチ 800・clicks 60・いいね・リアクション 9" in out
    assert "35-44・male:" in out and "いいね・リアクション 不明" in out
    for by, wanted in (("placement", "publisher_platform,platform_position"), ("device", "impression_device"),
                       ("hour", "hourly_stats_aggregated_by_advertiser_time_zone"), ("region", "region")):
        env["insights_route"] = lambda q, w=wanted: (200, {"data": []}) if q["breakdowns"] == [w] else (500, {})
        assert _json(capsys, ["ads", "insights", NAME, "--by", by])["breakdown_fields"] == wanted


# ------------------------------------------------------------------ 見積もり

def test_見積もりは読むだけで_狙う相手の大きさと予算あたりの見込みを出す(env, capsys):
    ready(env)
    value = _json(capsys, ["ads", "estimate", NAME, "--targeting", '{"geo_locations":{"countries":["JP"]}}'])
    assert value["reach"]["users_lower_bound"] == 1200000 and value["reach"]["users_upper_bound"] == 1400000
    assert value["delivery"]["estimate_dau"] == 50000 and value["delivery"]["confident"] is False
    calls = [c for c in env["calls"] if c["path"].endswith(("reachestimate", "delivery_estimate"))]
    assert {c["method"] for c in calls} == {"GET"}
    assert json.loads(calls[0]["query"]["targeting_spec"][0]) == {"geo_locations": {"countries": ["JP"]}}
    assert calls[1]["query"]["optimization_goal"] == ["LINK_CLICKS"]
    env["delivery_route"] = (200, {"data": [{"daily_outcomes_curve": [{"spend": 1000, "reach": 3000,
                                                                      "impressions": 4000, "actions": 30}]}]})
    capsys.readouterr()
    assert cli.main(["ads", "estimate", NAME, "--targeting", '{"geo_locations":{"countries":["JP"]}}']) == 0
    out = capsys.readouterr().out
    assert "狙う相手の大きさ: 1200000〜1400000 人" in out and "1 日 spend 1000・reach 3000" in out
    assert cli.main(["ads", "estimate", NAME, "--targeting", "[1]"]) == 2


# ------------------------------------------------------------------ ピクセルと次の一手（先頭は skill）

def test_ピクセルが無ければ作る一手_あって動いていなければ入れる一手_先頭はskill(env, capsys):
    ready(env)
    value = _json(capsys, ["ads", "doctor", NAME])   # ピクセルが無くても doctor は落とさない（？）
    steps = value["next_steps"]
    assert steps[0]["kind"] == "skill" and steps[0]["skill"] == "thth-ads"
    assert steps[1]["kind"] == "pixel_create" and "イベントマネージャでピクセルを作る" in steps[1]["text"]
    assert value["skill"]["name"] == "thth-ads" and value["skill"]["path"].endswith("skills/thth-ads/SKILL.md")
    pixel_row = next(r for r in value["rows"] if r["key"] == "pixel")
    assert pixel_row["ok"] is None
    env["pixels_route"] = (200, {"data": [{"id": "555", "name": "kopicha", "is_unavailable": False}]})
    raw = json.loads(open(ledger.path_for(NAME), encoding="utf-8").read())
    raw["landing_url"] = "https://kopicha.example/lp"
    open(ledger.path_for(NAME), "w", encoding="utf-8").write(json.dumps(raw))
    value = _json(capsys, ["ads", "show", NAME])
    assert value["pixel_ready"] is False and value["next_steps"][0]["kind"] == "skill"
    step = value["next_steps"][1]
    assert step["kind"] == "pixel_install" and step["pixel_id"] == "555"
    assert step["text"] == ("誘導先 https://kopicha.example/lp のサイトにピクセル 555 の基本のコードを入れる"
                            f"（{guide.PIXEL_DOC_URL}）。入れたら thth ads doctor {NAME} で動いたかを見る")
    env["pixels_route"] = (200, {"data": [{"id": "555", "last_fired_time": "2026-11-03T05:00:00+0000"}]})
    value = _json(capsys, ["ads", "show", NAME])
    assert value["pixel_ready"] is True and [s["kind"] for s in value["next_steps"]] == ["skill"]
    capsys.readouterr()
    cli.main(["ads", "show", NAME])
    out = capsys.readouterr().out
    assert "ピクセル: 555（最後に動いた 2026-11-03T05:00:00+0000）" in out
    assert out.strip().splitlines()[-1].startswith("次の一手: 広告の手順は skill thth-ads")


def test_成績で頁が開いた数が測れないときだけピクセルを読み次の一手を返す(env, capsys):
    ready(env)
    env["insights_route"] = _route(actions=[{"action_type": "post_reaction", "value": "3"}], inline_link_clicks="0")
    value = _json(capsys, ["ads", "insights", NAME])
    assert [s["kind"] for s in value["next_steps"]] == ["skill"]
    assert not any(c["path"].endswith("adspixels") for c in env["calls"])
    env["insights_route"] = _route(actions=[{"action_type": "link_click", "value": "4"}], inline_link_clicks="4")
    value = _json(capsys, ["ads", "insights", NAME])
    kinds = [s["kind"] for s in value["next_steps"]]
    assert kinds[0] == "skill" and kinds[1] == "pixel_create" and "landing_url_unknown" not in kinds
    capsys.readouterr()
    cli.main(["ads", "insights", NAME])
    out = capsys.readouterr().out.splitlines()
    first = next(i for i, line in enumerate(out) if line.startswith("次の一手"))
    assert out[first].startswith("次の一手: 広告の手順は skill thth-ads")
    assert "イベントマネージャでピクセルを作る" in out[first + 1]
    # ピクセルは動いているのに頁が開いた記録が無い → 誘導先の頁にもコードがあるかを確かめる一手
    env["pixels_route"] = (200, {"data": [{"id": "555", "last_fired_time": "2026-11-03T05:00:00+0000"}]})
    steps = _json(capsys, ["ads", "insights", NAME])["next_steps"]
    assert [s["kind"] for s in steps] == ["skill", "pixel_check_page", "landing_url_unknown"]
    assert steps[1]["last_fired_time"] == "2026-11-03T05:00:00+0000"


# ------------------------------------------------------------------ skill を先に示す

def test_広告の命令のエラーの文は先頭の行がskill(env, capsys):
    capsys.readouterr()
    assert cli.main(["ads", "insights", "nai-ads"]) == 2
    err = capsys.readouterr().err.splitlines()
    assert err[0].startswith("広告の手順は skill thth-ads") and len(err) >= 2
    ready(env)
    capsys.readouterr()
    assert cli.main(["ads", "estimate", NAME, "--targeting", "{壊れた"]) == 2
    assert capsys.readouterr().err.splitlines()[0].startswith("広告の手順は skill thth-ads")


def test_skillの命令は場所と全文を出し_skillに出てくるthth_adsの命令は全部実在する(capsys):
    assert cli.main(["ads", "skill"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("広告の手順は skill thth-ads") and os.path.isfile(out[1])
    assert cli.main(["ads", "skill", "--json"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["name"] == "thth-ads" and value["body"].startswith("---\nname: thth-ads\n")
