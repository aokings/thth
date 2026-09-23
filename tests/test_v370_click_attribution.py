"""3.7.0 §A1 click を投稿単位で（`basis: unique_url_72h`・`click_attribution`）。

あるリンク先を前後 72 時間で 1 本の投稿しか使っていないときだけ、そのリンク先の
投稿から 72 時間（投稿日を含む 3 暦日）のクリックをその投稿のものとして出す。
**共有のリンク先ではクリックを出さない**・**プロフィールのリンクは通さない**。
"""
from __future__ import annotations

import datetime
import os

from tests.conftest import write_queue_file
from tests.test_after_cli import _insight_path, _write_ndjson
from thth import accounts as accounts_mod
from thth import analytics_report, click_attribution, jst, lint, map_view, morning
from thth import sent as sent_mod

NOW = jst.parse("2026-09-24T12:00:00+09:00")


def _at(text):
    return jst.parse(f"2026-09-{text}+09:00")


def _post(account, post_id, posted, *, text="本文", goal="click", views=100, sent=True,
          link_urls=None, attachment_kinds=None, topic=None):
    row = {"account": account["name"], "post_id": post_id, "posted_at": jst.iso(posted),
           "collected_at": jst.iso(posted + datetime.timedelta(hours=25)), "age_hours": 25,
           "marks": [24], "reply_to": None, "topic": topic,
           "metrics": {"views": views, "likes": 0, "replies": 0, "reposts": 0}}
    _write_ndjson(_insight_path(account, post_id), [row])
    if sent:
        sent_mod.write(accounts_mod.state_dir_for(account["name"]), post_id=post_id, text=text,
                       body_hash="h", sent_at=jst.iso(posted), goal=goal,
                       link_urls=link_urls, attachment_kinds=attachment_kinds)


def _daily(account, rows):
    cfg = accounts_mod.load_account(account["name"])
    folder = accounts_mod.data_dirs(cfg, account["name"])["insights_account"]
    _write_ndjson(os.path.join(folder, f"{account['name']}-2026-09.ndjson"),
                  [{"account": account["name"], "date": f"2026-09-{day}", "metrics": metrics}
                   for day, metrics in rows])


def _clicks(url_values):
    """採取が残す形（`[{link_url, value}]`）。"""
    return {"clicks": sum(url_values.values()),
            "clicks_by_url": [{"link_url": url, "value": value}
                              for url, value in url_values.items()]}


def _yard(account, now=NOW, window_days=14):
    payload = analytics_report.answer(account["name"], now=now, compare_previous=True,
                                      min_n=1, by="goal", window_days=window_days)
    return payload, payload["by_account"][account["name"]]["posts"]["stratified"]["strata"][
        "click"]["yardstick"]["current"]


def _by_id(yard):
    return {row["post_id"]: row for row in yard["posts"]}


# ------------------------------------------------------------ 正規化

def test_リンク先の正規化はスキームと末尾のスラッシュとutmを外しホストだけ小文字に():
    n = click_attribution.normalize_url
    assert n("https://Example.COM/Path/?utm_source=x&utm_medium=y") == "example.com/Path"
    assert n("http://example.com/Path") == "example.com/Path"
    assert n("https://example.com/Path?a=1&utm_campaign=z&b=2") == "example.com/Path?a=1&b=2"
    assert n("https://example.com/path") != n("https://example.com/Path"), "パスは畳まない"
    assert n("ftp://example.com/x") is None and n("本文") is None
    assert click_attribution.urls_in("読んでね https://a.example/x?utm_source=t を見て。") == [
        "https://a.example/x?utm_source=t"]
    assert click_attribution.urls_in("https://a.example/xを見て") == ["https://a.example/x"]


# ------------------------------------------------------------ 一意のリンク先

def test_一意のリンク先なら投稿日を含む3暦日のクリックとクリック率を出す(isolated_account_factory):
    account = isolated_account_factory()
    _post(account, "U1", _at("18T21:00:00"), views=200,
          text="新しい記事 https://Example.com/a/?utm_source=threads")
    _daily(account, [("18", _clicks({"https://example.com/a": 3, "https://other.example/": 9})),
                     ("19", _clicks({"https://example.com/a?utm_medium=x": 4})),
                     ("20", {"clicks": 0}),
                     ("21", _clicks({"https://example.com/a": 100}))])   # 窓の外
    payload, yard = _yard(account)
    row = _by_id(yard)["U1"]
    assert row["basis"] == "unique_url_72h" and row["window"] == "post_day_plus_2"
    assert row["window_days"] == ["2026-09-18", "2026-09-19", "2026-09-20"]
    assert "3 暦日" in row["window_note"]
    assert row["urls"] == ["example.com/a"]
    assert row["clicks_72h"] == 7, "3+4+0（21 日は窓の外・他のリンク先は数えない）"
    assert row["views_24h"] == 200 and row["click_rate"] == 0.035
    assert row["rate_basis"] == "clicks_72h / views_24h"
    assert yard["per_post"]["clicks_72h"] == {"median": 7, "n_eligible": 1, "n_total": 1,
                                              "reason": None}
    assert yard["per_post"]["click_rate"]["median"] == 0.035
    assert yard["cannot_say"] == []
    text = analytics_report.render_markdown(payload)
    assert "unique_url_72h" in text and "投稿日を含む 3 暦日" in text


def test_共有のリンク先ではクリックを出さない(isolated_account_factory):
    account = isolated_account_factory()
    _post(account, "S1", _at("15T09:00:00"), text="https://example.com/a")
    _post(account, "S2", _at("17T20:00:00"), text="続き https://EXAMPLE.com/a/", goal="reach")
    _post(account, "S3", _at("21T09:00:00"), text="https://example.com/b")
    _daily(account, [(f"{d}", _clicks({"https://example.com/a": 5, "https://example.com/b": 2}))
                     for d in range(14, 24)])
    _payload, yard = _yard(account)
    rows = _by_id(yard)
    assert rows["S1"]["cannot_say"] == "url_shared_72h", "S2 は 59 時間後に同じリンク先"
    assert rows["S1"]["clicks_72h"] is None and rows["S1"]["basis"] is None
    assert rows["S3"]["basis"] == "unique_url_72h" and rows["S3"]["clicks_72h"] == 6
    assert yard["per_post"]["reasons"] == {"url_shared_72h": 1}
    assert yard["cannot_say"] == ["url_shared_72h"]


def test_72時間を超えて離れていれば同じリンク先でも別の投稿として測れる(isolated_account_factory):
    account = isolated_account_factory()
    _post(account, "A", _at("14T09:00:00"), text="https://example.com/a")
    _post(account, "B", _at("17T09:30:00"), text="https://example.com/a")   # 72.5 時間後
    _daily(account, [(f"{d}", _clicks({"https://example.com/a": 1})) for d in range(14, 21)])
    _payload, yard = _yard(account)
    rows = _by_id(yard)
    assert rows["A"]["clicks_72h"] == 3 and rows["B"]["clicks_72h"] == 3


def test_リンクが無い投稿とプロフィールのリンクは言えない(isolated_account_factory):
    account = isolated_account_factory(profile_links=["https://kopicha.example/"])
    _post(account, "N1", _at("15T09:00:00"), text="リンクなし")
    _post(account, "P1", _at("18T09:00:00"), text="プロフィールから https://KOPICHA.example")
    _post(account, "P2", _at("21T20:00:00"),
          text="https://kopicha.example/ と https://kopicha.example/shop")
    _daily(account, [(f"{d}", _clicks({"https://kopicha.example": 50,
                                        "https://kopicha.example/shop": 2}))
                     for d in range(14, 24)])
    _payload, yard = _yard(account)
    rows = _by_id(yard)
    assert rows["N1"]["cannot_say"] == "no_link"
    assert rows["P1"]["cannot_say"] == "profile_link" and rows["P1"]["clicks_72h"] is None
    assert rows["P2"]["urls"] == ["kopicha.example/shop"] and rows["P2"]["clicks_72h"] == 6
    assert rows["P2"]["profile_link_excluded"] is True


def test_窓が閉じていない_日次が欠けている_近くにリンク先の記録が無い(isolated_account_factory):
    account = isolated_account_factory()
    _post(account, "OPEN", _at("23T09:00:00"), text="https://example.com/open")
    _post(account, "GAP", _at("16T09:00:00"), text="https://example.com/gap")
    _post(account, "NEAR", _at("12T09:00:00"), text="https://example.com/near")
    _post(account, "MANUAL", _at("13T09:00:00"), sent=False)   # THTH を通していない投稿
    _daily(account, [("16", _clicks({"https://example.com/gap": 1})),
                     ("18", _clicks({"https://example.com/gap": 1}))])
    _payload, yard = _yard(account, window_days=14)
    rows = _by_id(yard)
    assert rows["OPEN"]["basis"] == "unique_url_72h"
    assert rows["OPEN"]["clicks_missing"] == "window_open" and rows["OPEN"]["clicks_72h"] is None
    assert rows["GAP"]["clicks_missing"] == "daily_missing"
    assert rows["NEAR"]["cannot_say"] == "nearby_link_unrecorded"


def test_添付のリンク先は記録から読み_控える前の記録はリンク先が記録に無い(isolated_account_factory):
    account = isolated_account_factory()
    _post(account, "L1", _at("15T09:00:00"), text="記事です", attachment_kinds=["link"],
          link_urls=["https://example.com/card"])
    _post(account, "L0", _at("19T09:00:00"), text="記事です", attachment_kinds=["link"])
    _daily(account, [(f"{d}", _clicks({"https://example.com/card": 2})) for d in range(14, 24)])
    _payload, yard = _yard(account)
    rows = _by_id(yard)
    assert rows["L1"]["clicks_72h"] == 6
    assert rows["L0"]["cannot_say"] == "link_unrecorded"


def test_公開の記録に添付のリンク先を控える():
    from thth import core
    manifest = {"files": [], "attachments": [{"type": "link", "url": "https://example.com/c"},
                                             {"type": "text", "text": "x",
                                              "link": "https://example.com/t"}]}
    assert core._link_urls_kwargs(manifest) == {
        "link_urls": ["https://example.com/c", "https://example.com/t"]}
    assert core._link_urls_kwargs(None) == {}
    assert core._link_urls_kwargs({"files": [], "attachments": []}) == {"link_urls": []}


def test_台帳のprofile_linksは形を検査する(isolated_account_factory):
    import pytest
    account = isolated_account_factory(profile_links=["javascript:alert(1)"])
    with pytest.raises(accounts_mod.AccountError, match="invalid_profile_links"):
        accounts_mod.load_account(account["name"])


# ------------------------------------------------------------ observe と地図

def test_observeの前回の観測からにclickの投稿単位の数(isolated_account_factory):
    account = isolated_account_factory()
    now = _at("22T12:00:00")
    y = now - datetime.timedelta(days=1)
    _post(account, "C1", y.replace(hour=9), text="https://example.com/a", views=50)
    _post(account, "C2", y.replace(hour=10), text="リンクなし")
    _daily(account, [])
    node = morning._yesterday_posts(account["name"], now)
    by_id = {post["post_id"]: post for post in node["posts"]}
    assert by_id["C1"]["click"]["basis"] == "unique_url_72h"
    assert by_id["C1"]["click"]["clicks_missing"] == "window_open"
    assert by_id["C1"]["goal_cannot_say"] is None
    assert by_id["C2"]["goal_cannot_say"] == "no_link"
    lines = []
    morning._render_yesterday(account["name"], {"medium": "threads", **node}, lines.append)
    text = "\n".join(lines)
    assert "click: 一意のリンク先（unique_url_72h）・72h のクリックはまだ言えません" in text
    assert "言えない: no_link" in text

    # 窓が閉じたあとなら数とクリック率。
    later = _at("25T12:00:00")
    _daily(account, [(d, _clicks({"https://example.com/a": 2})) for d in ("21", "22", "23")])
    node = morning._yesterday_posts(account["name"], later, read_at=jst.iso(y.replace(hour=0)))
    click = {p["post_id"]: p for p in node["posts"]}["C1"]["click"]
    assert click["clicks_72h"] == 6 and click["click_rate"] == 0.12


def test_地図の自分の層のclickは投稿単位の中央値とクリック率(isolated_account_factory):
    account = isolated_account_factory("kopicha-threads", project="kopicha")
    _post(account, "C1", _at("15T09:00:00"), text="https://example.com/a", topic="コーヒー")
    _post(account, "C2", _at("16T09:00:00"), text="https://example.com/a", topic="コーヒー")
    _post(account, "C3", _at("20T09:00:00"), text="https://example.com/b", topic="コーヒー",
          views=40)
    _daily(account, [(f"{d}", _clicks({"https://example.com/a": 9, "https://example.com/b": 2}))
                     for d in range(14, 24)])
    layer = map_view.self_layer({account["name"]: {"media": "threads"}}, ["コーヒー"],
                                since=NOW - datetime.timedelta(days=14), now=NOW, min_n=1)
    row = layer["コーヒー"]["by_account"][account["name"]]["by_goal"]["click"]
    assert row["posts"] == 3 and row["cannot_say"] is None
    assert row["primary"] == {"metric": "clicks_72h", "median": 6, "n": 1, "denominator": 3,
                              "reason": None, "basis": "unique_url_72h",
                              "window": "post_day_plus_2"}
    assert row["click_rate"]["median"] == 0.15 and row["click_rate"]["denominator"] == 3
    assert row["reasons"] == {"url_shared_72h": 2}
    line = map_view.goal_line(layer["コーヒー"]["by_account"][account["name"]]["by_goal"])
    assert "click 3 本（clicks_72h 中央値 6・n=1/3・クリック率 中央値 0.15" in line


# ------------------------------------------------------------ lint の知らせ

def _draft(account, name, publish_at, body, goal="click"):
    return write_queue_file(account["queue_dir"], name, body=body,
                            fm_overrides={"account": account["name"], "status": "draft",
                                          "publish_at": publish_at, "goal": goal})


def test_lintは同じリンク先を前後72時間に使うclickの原稿を知らせる_断らない(isolated_account_factory):
    account = isolated_account_factory()
    first = _draft(account, "a.md", "2026-09-25T08:00:00+09:00",
                   "## threads\n記事 https://example.com/a\n")
    _draft(account, "b.md", "2026-09-27T08:00:00+09:00",
           "## threads\n続き https://EXAMPLE.com/a/?utm_source=x\n")
    _draft(account, "c.md", "2026-09-25T20:00:00+09:00",
           "## threads\n同じ日でも別のリンク先 https://example.com/c\n")
    _draft(account, "d.md", "2026-09-25T21:00:00+09:00",
           "## threads\n目的が reach https://example.com/a\n", goal="reach")
    problems = lint.lint_file(first)
    warnings = [p for p in problems if lint.is_warning(p)]
    assert [p for p in problems if not lint.is_warning(p)] == []
    assert len(warnings) == 1 and warnings[0].startswith("warning: url_shared_72h:")
    assert "b.md" in warnings[0] and "c.md" not in warnings[0] and "d.md" not in warnings[0]
    third = os.path.join(account["queue_dir"], "c.md")
    assert [p for p in lint.lint_file(third) if lint.is_warning(p)] == [], \
        "同じ日でもリンク先が違えば測れる"
