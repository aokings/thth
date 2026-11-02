"""5.6.3: 広告の成績に反応の内訳とサイトへの誘導（masaru 11/03「絶対に出す必要があります」）。

偽の Graph（`tests/test_meta_stage4.py` の env）だけを使う。**本物の Meta は叩かない。**
偽の応答の形は 11/03 本番の試しの広告（120252025539640383）で Meta v26.0 が返した形をそのまま写した。
"""
from __future__ import annotations

import json

from thth import cli
from thth.ads import graphread
from tests.test_meta_stage4 import NAME, env, ready  # noqa: F401  (env は fixture)

# 11/03 本番で返った actions（シェア・コメントは 0 件で行が無かった）。
PROD_ACTIONS = [{"action_type": "post_reaction", "value": "47"}, {"action_type": "post_engagement", "value": "52"},
                {"action_type": "post_interaction_gross", "value": "47"}, {"action_type": "page_engagement", "value": "52"},
                {"action_type": "post_interaction_net", "value": "47"}, {"action_type": "photo_view", "value": "5"},
                {"action_type": "onsite_conversion.post_net_like", "value": "47"}]


def _row(**extra):
    base = {"spend": "470", "impressions": "1000", "clicks": "60", "reach": "800",
            "date_start": "2026-11-01", "date_stop": "2026-11-03"}
    base.update(extra)
    return lambda query: (200, {"data": [base]})


def _insights(env, capsys, *args):
    capsys.readouterr()
    assert cli.main(["ads", "insights", NAME, *args, "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def test_本番の形のactionsから反応を数え_行の無い種類は0(env, capsys):
    ready(env)
    env["insights_route"] = _row(actions=PROD_ACTIONS, inline_link_clicks="0")
    value = _insights(env, capsys)
    row = value["rows"][0]
    assert row["engagement"] == {"reactions": 47, "comments": 0, "shares": 0, "photo_views": 5, "page_likes": 0}
    assert row["engagement_unknown"] is None
    assert row["clicks"] == 60 and row["inline_link_clicks"] == 0   # clicks はリンクのクリックではない
    assert row["actions"][0] == {"action_type": "post_reaction", "value": "47"} and len(row["actions"]) == 7
    assert row["cost_per"]["reactions"] == "10.00" and row["cost_per"]["shares"] is None
    assert "engagement_note" in value
    # 頼む欄（鍵が載らない params の形・fields は既定の並び）
    fields = env["insights_queries"][-1]["fields"][0]
    assert fields == graphread.INSIGHT_FIELDS and "actions" in fields and "outbound_clicks" in fields


def test_actionsの欄が無いときは0でなく不明(env, capsys):
    ready(env)
    env["insights_route"] = _row()
    row = _insights(env, capsys)["rows"][0]
    assert row["engagement"] is None and row["engagement_unknown"] == "actions_missing"
    assert row["cost_per"]["reactions"] is None
    assert row["site"]["link_clicks"] is None and row["site"]["why"]["link_clicks"] == "actions_missing"
    env["insights_route"] = _row(actions="壊れた")
    row = _insights(env, capsys)["rows"][0]
    assert row["engagement"] is None and row["engagement_unknown"] == "actions_unreadable"


def test_サイトへの誘導_リンクが押されていなければ本当に0(env, capsys):
    ready(env)
    env["insights_route"] = _row(actions=PROD_ACTIONS, inline_link_clicks="0")
    site = _insights(env, capsys)["rows"][0]["site"]
    assert site["link_clicks"] == 0 and site["inline_link_clicks"] == 0
    assert site["outbound_clicks"] == 0 and site["why"]["outbound_clicks"] == "no_link_clicks"
    assert site["landing_page_views"] == 0 and site["why"]["landing_page_views"] == "no_link_clicks"


def test_サイトへの誘導_押されたのに頁の記録が無ければ計測なしで0と言わない(env, capsys):
    ready(env)
    env["insights_route"] = _row(actions=PROD_ACTIONS + [{"action_type": "link_click", "value": "20"}],
                                 inline_link_clicks="18",
                                 outbound_clicks=[{"action_type": "outbound_click", "value": "15"}])
    row = _insights(env, capsys)["rows"][0]
    site = row["site"]
    assert site["link_clicks"] == 20 and site["outbound_clicks"] == 15
    assert site["landing_page_views"] is None and site["why"]["landing_page_views"] == "not_measured_or_left"
    assert row["cost_per"]["link_clicks"] == "23.50" and row["cost_per"]["landing_page_views"] is None
    assert row["outbound_clicks_raw"] == [{"action_type": "outbound_click", "value": "15"}]
    env["insights_route"] = _row(actions=[{"action_type": "link_click", "value": "20"},
                                          {"action_type": "landing_page_view", "value": "10"}],
                                 inline_link_clicks="18")
    site = _insights(env, capsys)["rows"][0]["site"]
    assert site["landing_page_views"] == 10 and site["why"]["landing_page_views"] == "reported"
    # outbound_clicks の欄が無く、リンクは押された → 不明（0 と言わない）
    assert site["outbound_clicks"] is None and site["why"]["outbound_clicks"] == "outbound_clicks_missing"


def test_outbound_clicksを断られたら外して1度だけ読み直す(env, capsys):
    ready(env)

    def route(query):
        if "outbound_clicks" in query["fields"][0]:
            return 400, {"error": {"message": "(#100) bad field", "code": 100}}
        return 200, {"data": [{"spend": "470", "impressions": "1000", "clicks": "60", "reach": "800",
                               "actions": [{"action_type": "link_click", "value": "3"}], "inline_link_clicks": "3"}]}
    env["insights_route"] = route
    row = _insights(env, capsys)["rows"][0]
    assert [q["fields"][0] for q in env["insights_queries"][-2:]] == [graphread.INSIGHT_FIELDS,
                                                                      graphread.INSIGHT_FIELDS_BASE]
    assert row["spend_minor"] == 470 and row["site"]["why"]["outbound_clicks"] == "outbound_clicks_not_asked"


def test_人の読む表示に反応とサイトへの誘導と1件あたりが出て_clicksの違いを書く(env, capsys):
    ready(env)
    env["insights_route"] = _row(actions=PROD_ACTIONS, inline_link_clicks="0")
    capsys.readouterr()
    assert cli.main(["ads", "insights", NAME]) == 0
    out = capsys.readouterr().out
    assert "clicks 60（広告のどこかを押した数・リンクとは限らない）" in out
    assert "反応: いいね・リアクション 47・コメント 0・シェア 0・写真を開いた 5・ページへのいいね 0" in out
    assert "サイトへ出たクリック 0（リンクが押されていないので 0）" in out
    assert "1 件あたり（JPY）: いいね・リアクション 10.00・写真を開いた 94.00" in out
    env["insights_route"] = _row()
    cli.main(["ads", "insights", NAME])
    assert "反応: 不明（Meta が反応の欄（actions）を返しませんでした）" in capsys.readouterr().out


def test_同じ種類が2行あれば推さずにその種類だけ不明():
    counts, why = graphread.engagement([{"action_type": "post", "value": "1"}, {"action_type": "post", "value": "2"},
                                        {"action_type": "comment", "value": "x"}])
    assert why is None and counts["shares"] is None and counts["comments"] is None and counts["reactions"] == 0
