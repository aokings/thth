"""3.7.0 §A2 follow の「比べる日が無い」。

- follow の投稿が出た日／出ていない日の片方が 0 日（または min_n 未満）なら
  `cannot_say: no_comparison_days` と事実の 1 行。**推奨はしない**。
"""
from __future__ import annotations

import datetime
import os

from tests.test_after_cli import _insight_path, _write_ndjson
from thth import accounts as accounts_mod
from thth import analytics_report, jst
from thth import sent as sent_mod

NOW = jst.parse("2026-09-24T12:00:00+09:00")   # 木曜


def _post(account, post_id, posted, goal):
    row = {"account": account["name"], "post_id": post_id, "posted_at": jst.iso(posted),
           "collected_at": jst.iso(posted + datetime.timedelta(hours=25)), "age_hours": 25,
           "marks": [24], "reply_to": None, "topic": None,
           "metrics": {"views": 10, "likes": 0, "replies": 0, "reposts": 0}}
    _write_ndjson(_insight_path(account, post_id), [row])
    if goal is not None:
        sent_mod.write(accounts_mod.state_dir_for(account["name"]), post_id=post_id, text="本文",
                       body_hash="h", sent_at=jst.iso(posted), goal=goal)


def _followers(account, counts):
    cfg = accounts_mod.load_account(account["name"])
    folder = accounts_mod.data_dirs(cfg, account["name"])["insights_account"]
    _write_ndjson(os.path.join(folder, f"{account['name']}-2026-09.ndjson"),
                  [{"account": account["name"], "date": date, "metrics": {"followers_count": n}}
                   for date, n in counts.items()])


def _follow_yard(account, min_n=1):
    payload = analytics_report.answer(account["name"], now=NOW, compare_previous=True,
                                      min_n=min_n, by="goal")
    strata = payload["by_account"][account["name"]]["posts"]["stratified"]["strata"]
    return payload, strata["follow"]["yardstick"]["current"]


# ------------------------------------------------------------ A2

def test_followの投稿が毎日出ていれば比べる日が無いと事実だけ言う(isolated_account_factory):
    account = isolated_account_factory()
    start = NOW - datetime.timedelta(days=7)
    for i in range(8):
        _post(account, f"F{i}", (start + datetime.timedelta(days=i)).replace(hour=9), "follow")
    _followers(account, {(start + datetime.timedelta(days=i - 1)).date().isoformat(): 100 + i
                         for i in range(9)})
    payload, yard = _follow_yard(account)
    daily = yard["daily"]
    assert daily["without_goal_posts"]["n_days"] == 0 and daily["with_goal_posts"]["n_days"] > 0
    assert "no_comparison_days" in yard["cannot_say"] and daily["reason"] == "no_comparison_days"
    assert daily["fact"].startswith("follow の投稿が毎日出ていて、出ていない日がありません（直近 7 日")
    assert daily["difference_of_medians"] is None
    text = analytics_report.render_markdown(payload)
    head = text.split("## 根拠と構造化データ")[0]
    assert "比べる日がありません（no_comparison_days）" in head
    for word in ("とよい", "しましょう", "作ってください", "おすすめ"):
        assert word not in head, "推奨はしない"


def test_出ていない日がmin_n未満でも比べる日が無い(isolated_account_factory):
    account = isolated_account_factory()
    start = NOW - datetime.timedelta(days=7)
    for i in range(7):
        if i == 3:
            continue
        _post(account, f"F{i}", (start + datetime.timedelta(days=i)).replace(hour=9), "follow")
    _followers(account, {(start + datetime.timedelta(days=i - 1)).date().isoformat(): 100 + i
                         for i in range(9)})
    _payload, yard = _follow_yard(account, min_n=2)
    daily = yard["daily"]
    assert daily["without_goal_posts"]["n_days"] == 1
    assert daily["reason"] == "no_comparison_days"
    assert "比べるには少なすぎます（min_n=2" in daily["fact"]


def test_日次が無ければ比べる日の話はしない(isolated_account_factory):
    account = isolated_account_factory()
    _post(account, "F1", NOW - datetime.timedelta(days=2), "follow")
    _payload, yard = _follow_yard(account)
    assert yard["daily"]["reason"] == "account_daily_unavailable"
    assert yard["daily"]["fact"] is None and "no_comparison_days" not in yard["cannot_say"]
