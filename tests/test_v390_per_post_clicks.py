"""3.9.0 §A 投稿ごとのクリックを目的に依らず（`analytics-report <account> --per-post-clicks`）。

- goal を問わず、unique_url_72h を満たす投稿を並べる（unrecorded でも出る）。
  **goal の層（`--by goal`）とは混ぜない**。
- 各投稿と全体に `clicks_before_post`（投稿日より前 3 暦日のそのリンク先のクリック）。
  **窓の和（clicks_72h）には足さない**。1 以上なら静的な 1 行（原因は言わない）。
- `--by goal` の click にも同じ欄。
"""
from __future__ import annotations

import json

from tests.test_v370_click_attribution import _clicks, _daily, _post, _yard, _by_id, _at
from thth import analytics_clicks, cli, jst

NOW = jst.parse("2026-09-24T12:00:00+09:00")
URL_U = "https://example.com/u"


def _fixture(account):
    # U: goal を書く口が無かった頃の投稿（sent に goal の欄が無い＝unrecorded）。
    _post(account, "U", _at("15T09:00:00"), text=f"記事 {URL_U}", goal=None, views=100)
    _post(account, "R", _at("10T09:00:00"), text="https://example.com/r", goal="reach", views=50)
    _post(account, "C", _at("19T09:00:00"), text="https://example.com/c", goal="click", views=40)
    _post(account, "N", _at("20T09:00:00"), text="リンクなし", goal="reach")
    rows = [("14", _clicks({URL_U: 1})),                        # 窓の前（投稿日の前日）
            ("15", _clicks({URL_U: 5, "https://example.com/r": 1})),
            ("16", _clicks({URL_U: 4})), ("17", _clicks({URL_U: 3})),
            ("18", _clicks({URL_U: 2})), ("19", _clicks({URL_U: 2, "https://example.com/c": 6})),
            ("20", _clicks({"https://example.com/c": 1})), ("21", {"clicks": 0}),
            ("10", _clicks({"https://example.com/r": 2})), ("11", {"clicks": 0}),
            ("12", _clicks({"https://example.com/r": 1})), ("13", {"clicks": 0})]
    _daily(account, rows)


def test_goalを問わず並べる_unrecordedでも出る_goalの層と混ぜない(isolated_account_factory):
    account = isolated_account_factory()
    _fixture(account)
    payload = analytics_clicks.answer(account["name"], since="30d", now=NOW)
    assert payload["report_type"] == "per_post_clicks" and payload["population"] == "all_goals"
    ids = [row["post_id"] for row in payload["posts"]]
    assert ids == ["R", "U", "C"], "reach・unrecorded・click のどれも並ぶ（投稿日時の順）"
    assert payload["not_attributable"] == {"no_link": 1}
    assert payload["summary"]["n_posts"] == 4 and payload["summary"]["n_attributable"] == 3
    # 層に分けない・行に goal を持たない。
    assert "strata" not in payload and "by_goal" not in payload
    assert all("goal" not in row for row in payload["posts"])
    # `--by goal` の click の層は click の投稿だけのまま（per-post-clicks を混ぜない）。
    _payload, yard = _yard(account, now=NOW, window_days=14)
    assert set(_by_id(yard)) == {"C"}


def test_clicks_before_postは窓の和に足さない_静的な1行(isolated_account_factory):
    account = isolated_account_factory()
    _fixture(account)
    payload = analytics_clicks.answer(account["name"], since="30d", now=NOW)
    row = {r["post_id"]: r for r in payload["posts"]}["U"]
    assert row["window_days"] == ["2026-09-15", "2026-09-16", "2026-09-17"]
    assert row["clicks_72h"] == 12, "5+4+3（前日の 1 は足さない）"
    assert row["clicks_before_post"] == 1
    assert row["before_post"]["days"] == ["2026-09-12", "2026-09-13", "2026-09-14"]
    assert row["before_post"]["days_counted"] == 3 and row["before_post"]["days_missing"] == 0
    # 窓の後（参考）: 18〜21 日（2+2+0+0）。22・23 日は日次に行が無い（欠測）・24 日は閉じていない。
    assert row["clicks_after_window"] == 4
    assert row["after_window"]["days_missing"] == 2 and row["after_window"]["days_not_closed"] == 1
    assert row["window_share"] == {
        "value": round(12 / 17, 4), "numerator": 12, "denominator": 17,
        "basis": "clicks_72h / (clicks_before_post + clicks_72h + clicks_after_window)",
        "reason": None}
    assert row["click_rate"] == 0.12 and row["rate_basis"] == "clicks_72h / views_24h"
    s = payload["summary"]
    assert s["clicks_before_post"]["sum"] == 1
    assert payload["notes"] == [{"code": "clicks_before_post_present",
                                 "message": analytics_clicks.BEFORE_PRESENT_NOTE}]
    assert "原因は言えません" in analytics_clicks.BEFORE_PRESENT_NOTE
    text = analytics_clicks.render_markdown(payload)
    assert "窓の前 1（窓には足さない）" in text and "clicks_before_post_present" in text


def test_窓の和の合計は窓の前を含まない(isolated_account_factory):
    account = isolated_account_factory()
    _fixture(account)
    payload = analytics_clicks.answer(account["name"], since="30d", now=NOW)
    by = {r["post_id"]: r for r in payload["posts"]}
    assert by["R"]["clicks_72h"] == 2 + 0 + 1 and by["C"]["clicks_72h"] == 6 + 1 + 0
    assert payload["summary"]["clicks_72h"]["sum"] == 12 + 3 + 7
    assert payload["summary"]["clicks_72h"]["median"] == 7


def test_同じリンク先のほかの投稿の窓の日は窓の前にも後にも数えない(isolated_account_factory):
    account = isolated_account_factory()
    url = "https://example.com/x"
    _post(account, "X1", _at("10T09:00:00"), text=url)
    _post(account, "X2", _at("13T12:00:00"), text=url)     # 75 時間後（共有ではない）
    _daily(account, [(f"{d:02d}", _clicks({url: 1})) for d in range(7, 24)])
    payload = analytics_clicks.answer(account["name"], since="30d", now=NOW)
    by = {r["post_id"]: r for r in payload["posts"]}
    before = by["X2"]["before_post"]
    assert before["days"] == ["2026-09-10", "2026-09-11", "2026-09-12"]
    assert before["days_excluded_other_post_window"] == 3
    assert by["X2"]["clicks_before_post"] is None and before["reason"] == "no_countable_day"
    after = by["X1"]["after_window"]
    assert after["days_excluded_other_post_window"] == 3, "13〜15 日は X2 の窓"
    assert by["X1"]["clicks_after_window"] == 4, "16〜19 日"
    assert by["X1"]["clicks_before_post"] == 3, "7〜9 日"


def test_by_goalのclickにもclicks_before_post(isolated_account_factory):
    account = isolated_account_factory()
    url = "https://example.com/c"
    _post(account, "C", _at("19T09:00:00"), text=url)
    _daily(account, [("18", _clicks({url: 2})), ("19", _clicks({url: 5})),
                     ("20", _clicks({url: 1})), ("21", {"clicks": 0})])
    payload, yard = _yard(account, now=NOW, window_days=14)
    row = _by_id(yard)["C"]
    assert row["clicks_72h"] == 6 and row["clicks_before_post"] == 2
    assert yard["per_post"]["clicks_72h"]["median"] == 6, "窓の前は窓の和に入らない"
    before = yard["clicks_before_post"]
    assert before["sum"] == 2 and before["n"] == 1 and before["denominator"] == 1
    assert before["code"] == "clicks_before_post_present"
    from thth import analytics_report
    assert "窓の前のクリック 2（窓には足さない）" in analytics_report.render_markdown(payload)


def test_CLIのper_post_clicksとsinceの組み合わせ(isolated_account_factory, capsys):
    account = isolated_account_factory()
    _fixture(account)
    name = account["name"]
    assert cli.main(["analytics-report", name, "--per-post-clicks", "--since", "30d", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["report_type"] == "per_post_clicks" and out["period"]["since"] == "30d"
    assert cli.main(["analytics-report", name, "--per-post-clicks"]) == 0
    assert "# 投稿ごとのクリック" in capsys.readouterr().out
    assert cli.main(["analytics-report", name, "--since", "30d"]) == 2
    assert cli.main(["analytics-report", name, "--per-post-clicks", "--compare-previous",
                     "--by", "goal"]) == 2
    assert cli.main(["analytics-report", "--project", "p", "--per-post-clicks"]) == 2
    assert cli.main(["analytics-report", name, "--per-post-clicks", "--since", "abc"]) == 2
