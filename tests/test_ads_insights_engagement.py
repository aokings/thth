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


def test_読み全体でactionsが1行も返らないときは0でなく不明(env, capsys):
    # 5.7.0 で決まりを変えた（masaru 11/03「直して」）: 行に actions の欄が無いだけなら 0 と見なす。ただし
    # **読み全体で 1 行も actions が返らない**ときは、頼めたのか・Meta が出さなかったのかを区別できないので
    # 「不明」のまま。内訳なしの 1 行の読みもこちら（ほかに比べる行が無い）。
    ready(env)
    env["insights_route"] = _row()
    row = _insights(env, capsys)["rows"][0]
    assert row["engagement"] is None and row["engagement_unknown"] == "actions_missing"
    assert row["actions_absent_as_zero"] is False
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


def _rows(*rows):
    return lambda query: (200, {"data": [dict({"spend": "100", "impressions": "300", "clicks": "4", "reach": "200",
                                                "date_start": "2026-11-01", "date_stop": "2026-11-03"}, **r)
                                          for r in rows]})


def test_内訳で反応の無い区分はactionsの欄が無くても0(env, capsys):
    # 11/03 本番の `--by age-gender`: Meta は反応が 1 件も無い区分では actions の欄ごと返さない。同じ読みの
    # ほかの区分に actions が返っているので、頼めていたことは分かる → 欄の無い区分は 0（「不明」と出さない）。
    ready(env)
    env["insights_route"] = _rows(
        {"age": "35-44", "gender": "female", "actions": [{"action_type": "post_reaction", "value": "3"},
                                                         {"action_type": "link_click", "value": "1"}],
         "inline_link_clicks": "1"},
        {"age": "65+", "gender": "male", "inline_link_clicks": "0"})
    value = _insights(env, capsys, "--by", "age-gender")
    hit, empty = value["rows"]
    assert hit["engagement"]["reactions"] == 3 and hit["actions_absent_as_zero"] is False
    assert empty["engagement"] == {"reactions": 0, "comments": 0, "shares": 0, "photo_views": 0, "page_likes": 0}
    assert empty["engagement_unknown"] is None and empty["actions_absent_as_zero"] is True
    assert empty["actions"] is None   # 生の行は Meta が返したまま（欄は無かった）
    assert empty["site"]["link_clicks"] == 0 and empty["site"]["landing_page_views"] == 0
    assert empty["site"]["why"]["link_clicks"] == "actions_absent_as_zero"
    assert empty["cost_per"]["reactions"] is None   # 0 件は 1 件あたりを出さない
    capsys.readouterr()
    assert cli.main(["ads", "insights", NAME, "--by", "age-gender"]) == 0
    out = capsys.readouterr().out
    assert "65+・male: spend 100・表示 300・リーチ 200・clicks 4・いいね・リアクション 0・リンククリック 0" in out
    assert "不明" not in out


def test_内訳で全区分にactionsが無ければ不明のまま(env, capsys):
    ready(env)
    env["insights_route"] = _rows({"age": "18-24", "gender": "female"}, {"age": "65+", "gender": "male"})
    rows = _insights(env, capsys, "--by", "age-gender")["rows"]
    assert all(r["engagement"] is None and r["engagement_unknown"] == "actions_missing" for r in rows)
    assert all(r["site"]["link_clicks"] is None for r in rows)
    capsys.readouterr()
    cli.main(["ads", "insights", NAME, "--by", "age-gender"])
    assert "いいね・リアクション 不明・リンククリック 不明" in capsys.readouterr().out


def test_内訳なしでも複数行の読みなら欄の無い行は0(env, capsys):
    # 内訳なしの成績（--level campaign など）にも同じ考えを当てる: ほかの行に actions があれば頼めている。
    ready(env)
    env["insights_route"] = _rows({"campaign_id": "1", "campaign_name": "A",
                                   "actions": [{"action_type": "post_reaction", "value": "2"}]},
                                  {"campaign_id": "2", "campaign_name": "B"})
    rows = _insights(env, capsys, "--level", "campaign")["rows"]
    assert rows[1]["engagement"]["reactions"] == 0 and rows[1]["actions_absent_as_zero"] is True


def test_欄が壊れている行は0にしない(env, capsys):
    # 欄が「無い」のと「読めない」は違う。読めない行は、ほかの行に actions があっても不明のまま。
    ready(env)
    env["insights_route"] = _rows({"actions": [{"action_type": "post_reaction", "value": "2"}]},
                                  {"actions": "壊れた"})
    rows = _insights(env, capsys, "--level", "campaign")["rows"]
    assert rows[1]["engagement"] is None and rows[1]["engagement_unknown"] == "actions_unreadable"
    assert rows[1]["actions_absent_as_zero"] is False


def test_actionsの欄が無くても広告の中のリンクが押された行は0と言わない(env, capsys):
    # 監査 N-1: 欄の無い行を 0 と見なすと、inline_link_clicks 3 の行で「リンククリック 0・頁が開いた 0」と
    # 食い違う。押されたのだから 0 ではない → この行は不明のまま（頁が開いた数も 0 と言わない）。
    ready(env)
    env["insights_route"] = _rows({"age": "35-44", "gender": "female",
                                   "actions": [{"action_type": "post_reaction", "value": "2"}],
                                   "inline_link_clicks": "0"},
                                  {"age": "25-34", "gender": "male", "inline_link_clicks": "3"})
    row = _insights(env, capsys, "--by", "age-gender")["rows"][1]
    assert row["actions_absent_as_zero"] is False
    assert row["engagement"] is None and row["engagement_unknown"] == "actions_missing"
    assert row["site"]["inline_link_clicks"] == 3
    assert row["site"]["link_clicks"] is None and row["site"]["landing_page_views"] is None
    # site() を直に呼んでも同じ（呼ぶ側が印を立てても 0 にしない）。
    s = graphread.site(None, "3", None, outbound_asked=False, actions_absent_as_zero=True)
    assert s["link_clicks"] is None and s["landing_page_views"] is None
