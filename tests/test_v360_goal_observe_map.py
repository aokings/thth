"""3.6.0 observe と観測の地図の目的の表示（設計 3.6.0 §A2）。

- `thth observe` の「前回の観測から」の段: 投稿ごとの `goal` と、目的ごとの主な物差しを
  先頭に。click・follow は投稿単位の数字が無いので「言えない」（日次を割らない）。
- 観測の地図の自分の層: 点×目的の表（その話題で目的ごとの本数と主な物差しの中央値）。
"""
from __future__ import annotations

import datetime

from tests.test_analytics_comparison import seed
from thth import accounts as accounts_mod
from thth import jst, map_view, morning
from thth import sent as sent_mod

NOW = jst.parse("2026-09-18T12:00:00+09:00")
SINCE = NOW - datetime.timedelta(days=7)


def _post(account, post_id, posted, goal, *, topic="コーヒー", views=10, likes=1, replies=2,
          medium="threads"):
    seed(account, post_id, posted, value=views,
         extra={"topic": topic, "medium": medium,
                "metrics": {"views": views, "likes": likes, "replies": replies, "reposts": 0}})
    if goal:
        sent_mod.write(accounts_mod.state_dir_for(account["name"]), post_id=post_id, text="本文",
                       body_hash="h", sent_at=jst.iso(posted), goal=goal)


def test_observeの投稿に目的と主な物差し_clickは言えない(isolated_account_factory):
    account = isolated_account_factory()
    y = NOW - datetime.timedelta(days=1)          # 前日 JST（栞なし）
    _post(account, "R1", y.replace(hour=9), "reach", views=120, likes=3, replies=1)
    _post(account, "P1", y.replace(hour=10), "reply", replies=5)
    _post(account, "C1", y.replace(hour=11), "click")
    _post(account, "N1", y.replace(hour=12), None)
    node = morning._yesterday_posts(account["name"], NOW)
    by_id = {post["post_id"]: post for post in node["posts"]}
    assert by_id["R1"]["goal"] == "reach" and by_id["R1"]["lead_metrics"] == ["views"]
    assert by_id["P1"]["lead_metrics"] == ["replies"]
    assert by_id["C1"]["goal_cannot_say"] == "per_post_clicks_unavailable"
    assert by_id["C1"]["lead_metrics"] == []
    assert by_id["N1"]["goal"] == "none" and by_id["N1"]["goal_cannot_say"] is None

    lines = []
    morning._render_yesterday(account["name"], {"medium": "threads", **node}, lines.append)
    text = "\n".join(lines)
    assert "R1  [reach] views=120・likes=3" in text
    assert "P1  [reply] replies=5・likes=1" in text
    assert "言えない: per_post_clicks_unavailable（日次の数を投稿に割りません）" in text
    assert "N1  likes=1・replies=2" in text, "目的が無ければ従前の並び"


def test_地図の自分の層に点と目的の表(isolated_account_factory):
    account = isolated_account_factory("kopicha-threads", project="kopicha")
    for i, views in enumerate([10, 20, 30]):
        _post(account, f"R{i}", NOW - datetime.timedelta(days=2, hours=i), "reach", views=views)
    _post(account, "F1", NOW - datetime.timedelta(days=2, hours=5), "follow")
    _post(account, "P1", NOW - datetime.timedelta(days=2, hours=6), "reply", replies=4)
    _post(account, "T1", NOW - datetime.timedelta(days=2, hours=7), "reach", topic="紅茶")
    layer = map_view.self_layer({account["name"]: {"media": "threads"}}, ["コーヒー"],
                                since=SINCE, now=NOW, min_n=1)
    table = layer["コーヒー"]["by_account"][account["name"]]["by_goal"]
    assert table["reach"]["posts"] == 3
    assert table["reach"]["primary"] == {"metric": "views_24h", "median": 20, "n": 3,
                                         "denominator": 3, "reason": None}
    assert table["follow"]["posts"] == 1 and table["follow"]["primary"] is None
    assert table["follow"]["cannot_say"] == "per_post_follows_unavailable"
    assert table["reply"]["primary"]["metric"] == "replies_24h"
    assert table["reply"]["primary"]["median"] == 4
    assert table["click"]["posts"] == 0 and table["none"]["posts"] == 0
    line = map_view.goal_line(table)
    assert line.startswith("目的: reach 3 本（views_24h 中央値 20・n=3/3）")
    assert "follow 1 本（言えない: per_post_follows_unavailable）" in line
    assert "click" not in line


def test_地図の表は小標本なら中央値を出さない(isolated_account_factory):
    account = isolated_account_factory("kopicha-threads", project="kopicha")
    _post(account, "R0", NOW - datetime.timedelta(days=2), "reach", views=10)
    layer = map_view.self_layer({account["name"]: {"media": "threads"}}, ["コーヒー"],
                                since=SINCE, now=NOW)
    primary = layer["コーヒー"]["by_account"][account["name"]]["by_goal"]["reach"]["primary"]
    assert primary["median"] is None and primary["reason"] == "below_min_n"
