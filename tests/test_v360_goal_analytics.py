"""3.6.0 `analytics-report --compare-previous --by goal`（設計 3.6.0 §A2・§C）。

目的ごとに、その目的に合う物差しで比べる。**click と follow は投稿単位に割らない**
（一次資料に無い数を作らない）——「言えない」と言い、日次との並べ方は観察の差の印つき。
層は公開の時点の記録（sent・連投の実行記録）から作り、記録の無い投稿は `none`。
"""
from __future__ import annotations

import datetime
import os

import pytest

from tests.test_after_cli import _insight_path, _write_ndjson
from thth import accounts as accounts_mod
from thth import analytics_report, goals, jst
from thth import sent as sent_mod

NOW = jst.parse("2026-09-18T12:00:00+09:00")


def _at(text):
    return jst.parse(f"2026-09-{text}+09:00")


def _seed_post(account, post_id, posted, *, goal=None, views=10, replies=0, likes=0,
               reposts=0, extra_marks=()):
    rows = [{"account": account["name"], "post_id": post_id, "posted_at": jst.iso(posted),
             "collected_at": jst.iso(posted + datetime.timedelta(hours=25)), "age_hours": 25,
             "marks": [24], "reply_to": None, "topic": None,
             "metrics": {"views": views, "likes": likes, "replies": replies, "reposts": reposts}}]
    for mark, value in extra_marks:
        rows.append({**rows[0], "collected_at": jst.iso(posted + datetime.timedelta(hours=mark + 1)),
                     "marks": [mark], "metrics": {"views": value, "likes": likes,
                                                  "replies": replies, "reposts": reposts}})
    _write_ndjson(_insight_path(account, post_id), rows)
    if goal is not None:
        sent_mod.write(accounts_mod.state_dir_for(account["name"]), post_id=post_id, text="本文",
                       body_hash="h", sent_at=jst.iso(posted), goal=goal)


def _seed_daily(account, rows):
    cfg = accounts_mod.load_account(account["name"])
    folder = accounts_mod.data_dirs(cfg, account["name"])["insights_account"]
    _write_ndjson(os.path.join(folder, f"{account['name']}-2026-09.ndjson"),
                  [{"account": account["name"], "date": date, "metrics": metrics}
                   for date, metrics in rows])


def _seed_replies(account, post_id, posted, rows):
    cfg = accounts_mod.load_account(account["name"])
    folder = accounts_mod.data_dirs(cfg, account["name"])["replies"]
    lines = [{"kind": "reply", "id": f"r{i}", "post_id": post_id, "username": user,
              "timestamp": jst.iso(posted + datetime.timedelta(hours=hours))}
             for i, (user, hours) in enumerate(rows)]
    lines.append({"kind": "fetch", "post_id": post_id, "marks": [24],
                  "collected_at": jst.iso(posted + datetime.timedelta(hours=25))})
    _write_ndjson(os.path.join(folder, f"{post_id}.ndjson"), lines)


@pytest.fixture
def report(isolated_account_factory):
    account = isolated_account_factory()
    _seed_post(account, "R1", _at("15T09:00:00"), goal="reach", views=100,
               extra_marks=[(72, 300)])
    _seed_post(account, "R2", _at("16T09:00:00"), goal="reach", views=50)
    _seed_post(account, "C1", _at("14T09:00:00"), goal="click")
    _seed_post(account, "C2", _at("13T09:00:00"), goal="click")
    _seed_post(account, "C3", _at("13T11:00:00"), goal="click")
    _seed_post(account, "F1", _at("15T10:00:00"), goal="follow")
    _seed_post(account, "P1", _at("16T10:00:00"), goal="reply", replies=3)
    _seed_post(account, "N1", _at("12T09:00:00"))                    # 記録なし（3.6.0 より前）
    _seed_post(account, "OLD", _at("08T09:00:00"), goal="reach", views=7)   # 前期間
    _seed_replies(account, "P1", _at("16T10:00:00"),
                  [("alice", 1), ("bob", 2), ("alice", 3), ("nigamilab", 4), ("carol", 30)])
    followers = {"11": 100, "12": 101, "13": 101, "14": 103, "15": 110, "16": 111, "17": 111}
    clicks = {"13": 40, "14": 7}
    _seed_daily(account, [(f"2026-09-{day}", {"followers_count": count,
                                             **({"clicks": clicks[day],
                                                 "clicks_by_url": {"https://example.com/a": clicks[day]}}
                                                if day in clicks else {})})
                          for day, count in followers.items()])
    payload = analytics_report.answer(account["name"], now=NOW, compare_previous=True,
                                      min_n=1, by="goal")
    return payload, payload["by_account"][account["name"]]["posts"]["stratified"]


def test_層は4語と目的なし_記録の無い投稿はnone_分母が合う(report):
    _payload, stratified = report
    assert stratified["by"] == "goal" and stratified["goal_source"] == "recorded_at_publish"
    strata = stratified["strata"]
    assert set(strata) == {"reach", "click", "follow", "reply", "none"}
    counts = {label: group["current"]["n_total"] for label, group in strata.items()}
    assert counts == {"reach": 2, "click": 3, "follow": 1, "reply": 1, "none": 1}
    assert strata["reach"]["previous"]["n_total"] == 1
    assert stratified["reconciliation"]["current"] == {"sum_n_total": 8, "n_total": 8}
    assert strata["none"]["yardstick"]["current"] is None


def test_reachは24hと72hのviews(report):
    yard = report[1]["strata"]["reach"]["yardstick"]["current"]
    assert yard["metric"] == "views" and yard["metric_unavailable"] == {}
    assert yard["by_mark"]["24"] == {"median": 75.0, "n_eligible": 2, "n_total": 2, "reason": None}
    assert yard["by_mark"]["72"]["median"] == 300 and yard["by_mark"]["72"]["n_eligible"] == 1


def test_clickは投稿単位に割らず言えないと言う_1本だけの日だけ日次を並べる(report):
    yard = report[1]["strata"]["click"]["yardstick"]["current"]
    assert yard["per_post"] is None
    assert yard["cannot_say"] == ["per_post_clicks_unavailable"]
    daily = yard["daily"]
    assert daily["basis"] == "single_click_post_day"
    assert daily["observational_difference"] is True and daily["causal"] is False
    assert daily["days"] == [{"date": "2026-09-14", "post_id": "C1", "clicks": 7,
                              "clicks_by_url": {"https://example.com/a": 7}}]
    assert daily["excluded_days_multiple_click_posts"] == 1, "09-13 は 2 本なので言わない"
    assert all("clicks" not in (row["observation"] or {}).get("metrics", {})
               for row in report[1]["strata"]["click"]["current"]["evidence"])


def test_followは投稿単位に割らず_出た日と出ていない日を観察の差として並べる(report):
    yard = report[1]["strata"]["follow"]["yardstick"]["current"]
    assert yard["per_post"] is None
    assert yard["cannot_say"] == ["per_post_follows_unavailable"]
    daily = yard["daily"]
    assert daily["basis"] == "followers_count_day_over_day"
    assert daily["observational_difference"] is True and daily["causal"] is False
    assert daily["note"] == "観察の差（因果ではない）"
    assert daily["with_goal_posts"]["days"] == [{"date": "2026-09-15", "delta": 7}]
    assert [row["date"] for row in daily["without_goal_posts"]["days"]] == [
        "2026-09-12", "2026-09-13", "2026-09-14", "2026-09-16", "2026-09-17"]
    assert daily["with_goal_posts"]["median_delta"] == 7
    assert daily["without_goal_posts"]["median_delta"] == 1
    assert daily["difference_of_medians"] == 6


def test_replyは24hのrepliesと返信した人の異なり数_自分を除く(report):
    yard = report[1]["strata"]["reply"]["yardstick"]["current"]
    assert yard["replies_24h"]["median"] == 3
    people = yard["distinct_repliers_24h"]
    assert people["median"] == 2, "alice・bob（nigamilab は自分・carol は 24h の外）"
    assert people["excludes"] == "own_accounts" and people["n_eligible"] == 1


def test_markdownは言えないと観察の差を書き効果とは書かない(report):
    payload, _stratified = report
    text = analytics_report.render_markdown(payload)
    assert "per_post_clicks_unavailable" in text and "per_post_follows_unavailable" in text
    assert "観察の差（因果ではない）" in text
    head = text.split("## 根拠と構造化データ")[0]
    assert "効果" not in head.replace("施策の効果・推奨行動は判断しない", "")


def test_goalはCLIとMCPの層に並ぶ_compare_previousが要る(isolated_account_factory):
    from thth import after_cli, cli
    assert "goal" in analytics_report.BY_CHOICES
    parser = cli.build_parser()
    action = next(a for a in parser._subparsers._group_actions[0].choices["analytics-report"]._actions
                  if a.dest == "by")
    assert "goal" in action.choices
    from tests.test_after_cli import _load_server_module
    server = _load_server_module()
    tool = next(t for t in server.TOOLS if t["name"] == "analytics_report")
    assert "goal" in tool["inputSchema"]["properties"]["by"]["enum"]
    server.validate_arguments("analytics_report", {"by": "goal", "compare_previous": True,
                                                   "account": "x"})
    with pytest.raises(after_cli.AfterError):
        analytics_report.answer("x", by="goal")


def test_blueskyのreachはlikesとreposts_viewsはmetric_unavailable(isolated_account_factory):
    account = isolated_account_factory(media="bluesky")
    _seed_post(account, "B1", _at("15T09:00:00"), goal="reach", views=None, likes=3, reposts=2)
    payload = analytics_report.answer(account["name"], now=NOW, compare_previous=True,
                                      min_n=1, by="goal")
    yard = payload["by_account"][account["name"]]["posts"]["stratified"]["strata"]["reach"][
        "yardstick"]["current"]
    assert yard["metric"] == "likes_plus_reposts" and yard["by_mark"]["24"]["median"] == 5
    assert yard["metric_unavailable"] == {"views": "metric_unavailable"}


def test_公開のあとに原稿の目的を書き換えても層は動かない(isolated_account_factory):
    """層の材料は公開の時点の記録だけ。いまの原稿は読まない。"""
    account = isolated_account_factory()
    _seed_post(account, "Q1", _at("15T09:00:00"), goal="reach")
    queue = account["queue_dir"]
    os.makedirs(queue, exist_ok=True)
    with open(os.path.join(queue, "q1.md"), "w", encoding="utf-8") as f:
        f.write("---\nthth: 1\naccount: nigamilab-threads\nstatus: posted\npost_id: Q1\n"
                "goal: click\n---\n## threads\n\n本文\n")
    assert goals.recorded_goals(account["name"]) == {"Q1": "reach"}
    payload = analytics_report.answer(account["name"], now=NOW, compare_previous=True,
                                      min_n=1, by="goal")
    strata = payload["by_account"][account["name"]]["posts"]["stratified"]["strata"]
    assert strata["reach"]["current"]["n_total"] == 1 and strata["click"]["current"]["n_total"] == 0
