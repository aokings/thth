"""5.0 段 1＋段 2 の継ぎ目: `thth dashboard` が段 1 の本物（`thth.scenario`）を読む（設計 5.0 §4・§5・§5.7）。

段 2 の試験（`test_v50_dashboard.py`）は golden を差し込んで入力の形を見る。ここは差し込まずに、段 1 の試験と
同じ世界（`golden_world`）を本物の `scenario.build`／`scenario.show` で通す:

- 段 2 が読む段 1 の答えは golden と値まで同じ（同じファイルが両方の正本）。
- 要対応: events の `scenario_stalled`・`series_low` を先に使い、同じ ref は 1 度だけ数える。
- 階層 3: `scenario show` に足した鍵（`stages[].items`・`slots_upcoming`・`items[].delivery`/`post_ids`・
  `deliveries`）をそのまま使う。回ごとのインプレッションは実測の台帳を投稿の id で、はてブ数は
  `bookmark_received` と同じ出所を配信 id で引く。他人の本文と user は出さない。
"""
from __future__ import annotations

import json
import os
import re

import pytest

from tests.test_v500_scenario import GOLDEN, NOW, PROJECT, _sns, _write, golden_world, kp  # noqa: F401
from thth import accounts as accounts_mod, dashboard_cli, events_cli, hatena_reactions, jst, scenario

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(ROOT, "skills", "thth", "dashboard")
SLOT = re.compile(r'<script type="application/json" id="thth-data">(.*?)</script>', re.S)
SECRET_COMMENT = "他人のはてブのコメントの秘密"
DELIVERY = "dl_00000000a004"


@pytest.fixture
def joined(golden_world, monkeypatch):  # noqa: F811
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    return golden_world


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)


def _with_outcomes(world):
    """回ごとの成果の材料: シリーズの公開済みの回 1 本（実測つき）と、日記の配信への第三者のはてブ。"""
    q = world["queue"]
    _write(os.path.join(q, "th-num-1024.md"),
           _sns("kp-threads", status="posted", publish_at="2026-10-24T07:00:00+09:00", series="num",
                extra={"post_id": "1001", "posted_at": "2026-10-24T07:00:05+09:00"}))
    cfg = accounts_mod.load_account("kp-threads")
    posts = accounts_mod.data_dirs(cfg, "kp-threads")["insights_posts"]
    os.makedirs(posts, exist_ok=True)
    with open(os.path.join(posts, "1001.ndjson"), "w", encoding="utf-8") as f:
        for hours, views, likes in ((6, 80, 2), (24, 120, 5)):
            f.write(json.dumps({"account": "kp-threads", "post_id": "1001", "file": "th-num-1024.md",
                                "posted_at": "2026-10-24T07:00:05+09:00",
                                "collected_at": f"2026-10-2{4 if hours == 6 else 5}T{13 if hours == 6 else 7:02d}:00:00+09:00",
                                "age_hours": hours, "marks": [hours],
                                "metrics": {"views": views, "likes": likes, "replies": 1}}) + "\n")
    hb = accounts_mod.load_account("kp-hatenablog")
    path = hatena_reactions.ledger_path(hb, "kp-hatenablog", DELIVERY)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rows = [{"schema": hatena_reactions.SCHEMA, "delivery": DELIVERY, "account": "kp-hatenablog",
             "article": "kp/sencha", "collected_at": "2026-10-20T08:00:00+09:00",
             "bookmark": {"bookmarks": [{"user": "alice", "comment": SECRET_COMMENT, "timestamp": "2026/10/19 21:03"},
                                        {"user": "bob", "comment": "", "timestamp": "2026/10/19 22:00"}]}},
            {"schema": hatena_reactions.SCHEMA, "delivery": DELIVERY, "account": "kp-hatenablog",
             "article": "kp/sencha", "collected_at": "2026-10-21T08:00:00+09:00",
             "bookmark": {"bookmarks": [{"user": "alice", "comment": SECRET_COMMENT, "timestamp": "2026/10/19 21:03"},
                                        {"user": "carol", "comment": "", "timestamp": "2026/10/20 10:00"}]}}]
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return world


# ------------------------------------------------------------------ 継ぎ目

def test_継ぎ目は段1の本物でgoldenと値まで同じ(joined):
    assert dashboard_cli._scenario_module() is scenario
    payload, reason = dashboard_cli._load_scenario(PROJECT, NOW)
    assert reason is None
    with open(GOLDEN, encoding="utf-8") as f:
        assert payload == json.load(f)
    got = dashboard_cli.build(PROJECT, now=NOW)
    assert got["scenario"]["available"] is True and got["scenario"]["n_campaigns_stalled"] == 1
    assert [c["id"] for c in got["campaigns"]] == ["num-04-sencha", "sousa-026"]
    assert [s["id"] for s in got["series"]] == ["num"]
    assert "scenario_not_installed" not in json.dumps(got)


# ------------------------------------------------------------------ 要対応（events が先・1 度だけ）

def _attention(got, kind, ref):
    return [r for r in got["attention"] if r["kind"] == kind and r["ref"] == ref]


def test_要対応はeventsを先に使い同じrefは1度だけ(joined):
    events = events_cli.build(PROJECT, since=dashboard_cli.EVENTS_SINCE, now=NOW)
    kinds = {(e["kind"], e["ref"]) for e in events["events"]}
    # 前提: events が段 1 の 2 種類を出している（出ていなければ重なりを確かめたことにならない）。
    assert ("scenario_stalled", "num-04-sencha") in kinds and ("series_low", "num") in kinds
    got = dashboard_cli.build(PROJECT, now=NOW)
    stalled = _attention(got, "scenario_stalled", "num-04-sencha")
    low = _attention(got, "series_low", "num")
    assert len(stalled) == 1 and len(low) == 1
    assert stalled[0]["source"] == "events" and stalled[0]["event_id"] == scenario.stalled_event_id(
        PROJECT, "num-04-sencha", "article")
    assert low[0]["source"] == "events" and low[0]["event_id"] == scenario.series_low_event_id(PROJECT, "num")
    # events から来ても、段に応じた押すところ（本記事の段なら「公開した」）と次の枠が付く。
    assert [a["label"] for a in stalled[0]["actions"]][0] == "公開した"
    assert stalled[0]["stage"] == "article" and stalled[0]["days"] == 9
    assert low[0]["next_slot"] == "2026-10-30T07:00:00+09:00"
    assert low[0]["next"] == "承認済みストック 1 本（目安 2）"
    assert got["kpi"]["attention"]["by_kind"]["scenario_stalled"] == 1
    assert got["kpi"]["attention"]["by_kind"]["series_low"] == 1
    # 停滞していない sousa-026 は要対応に入らない。
    assert not _attention(got, "scenario_stalled", "sousa-026")


def test_eventsが読めなければ段1の答えから写し同じく1度だけ(joined, monkeypatch):
    monkeypatch.setattr(dashboard_cli, "_events", lambda *a, **k: None)
    got = dashboard_cli.build(PROJECT, now=NOW)
    stalled = _attention(got, "scenario_stalled", "num-04-sencha")
    low = _attention(got, "series_low", "num")
    assert len(stalled) == 1 and stalled[0]["source"] == "scenario" and stalled[0]["days"] == 9
    assert len(low) == 1 and low[0]["source"] == "scenario"


# ------------------------------------------------------------------ show に足した鍵

def test_showに足した鍵は足すだけで一覧の行と同じ値(joined):
    listed = {t["id"]: t for t in scenario.build(PROJECT, now=NOW)["threads"]}
    shown = scenario.show(PROJECT, "num-04-sencha", now=NOW)
    thread = shown["thread"]
    for key, value in listed["num-04-sencha"].items():
        if key == "stages":
            for row, base in zip(thread["stages"], value):
                assert {k: v for k, v in row.items() if k != "items"} == base
        else:
            assert thread[key] == value, key
    by_stage = {row["name"]: row["items"] for row in thread["stages"]}
    assert len(by_stage["collect"]) == 5 and {i["kind"] for i in by_stage["collect"]} == {"bookmark"}
    assert [(i["file"], i["delivery"], i["delivery_state"]) for i in by_stage["diary"]] == [
        ("hb-num04.md", DELIVERY, "published")]
    assert by_stage["article"] == [] and by_stage["warm"] == []
    assert [(i["file"], i["status"], i["medium"]) for i in by_stage["announce"]] == [
        ("x-num04-announce.md", "draft", "x")]
    assert shown["deliveries"] == [DELIVERY]
    assert {"delivery", "post_ids", "future_approved"} <= set(shown["items"][0])
    # はてブの成果物は時刻だけ（URL も他人の本文も持たない）。
    assert not any("example.com" in s for s in _strings(shown))


def test_シリーズのshowは今後4週の枠と埋めている原稿(joined):
    shown = scenario.show(PROJECT, "num", now=NOW)
    listed = scenario.build(PROJECT, now=NOW)["series"][0]
    assert {k: v for k, v in shown["series"].items() if k != "slots_upcoming"} == listed
    up = shown["series"]["slots_upcoming"]
    assert len(up) == 8                                   # 火・金 × 4 週
    assert up[0] == {"at": "2026-10-30T07:00:00+09:00", "filled": False, "file": None}
    assert up[1] == {"at": "2026-11-03T07:00:00+09:00", "filled": True, "file": "th-num-1103.md"}
    assert sum(1 for s in up if s["filled"]) == 1
    # 14 日の空き枠の数は一覧の empty_slots_14d と食い違わない。
    horizon = jst.iso(NOW + __import__("datetime").timedelta(days=14))
    assert sum(1 for s in up if not s["filled"] and s["at"] <= horizon) == listed["empty_slots_14d"]


# ------------------------------------------------------------------ 階層 3 は show を使い、成果は既存の台帳から

def test_キャンペーンの階層はshowの成果物とはてブ数を使う(joined):
    _with_outcomes(joined)
    got = dashboard_cli.build(PROJECT, "campaign", "num-04-sencha", now=NOW)["campaign"]
    stages = [d["stage"] for d in got["deliverables"]]
    assert stages.count("collect") == 5 and stages.count("diary") == 1 and stages.count("announce") == 1
    diary = next(d for d in got["deliverables"] if d["stage"] == "diary")
    assert diary["status"] == "posted" and diary["ref"] == "hb-num04.md" and diary["delivery"] == DELIVERY
    out = got["outcomes"]
    # alice・bob・carol（刻みを跨いでも 1 人 1 回）。events の bookmark_received と同じ数。
    assert out["bookmarks_received"] == 3
    n_events = sum(1 for e in events_cli.build(PROJECT, since="30d", now=NOW)["events"]
                   if e["kind"] == "bookmark_received" and e["ref"] == DELIVERY)
    assert n_events == 3
    assert "bookmarks_received" not in out["cannot_say"]
    # 告知はまだ出ていないので、インプレッションは「—」と理由。ブログ PV・記事のクリックは取っていない。
    assert out["impressions"] is None and out["cannot_say"]["impressions"] == "not_published_yet"
    assert out["blog_pv"] is None and out["cannot_say"]["blog_pv"] == "blog_pv_not_collected"
    assert out["article_clicks"] is None
    assert got["deliveries"] == [DELIVERY]
    assert "scenario_show_unavailable" not in got["cannot_say"]
    text = json.dumps(got, ensure_ascii=False)
    for needle in (SECRET_COMMENT, "alice", "bob", "carol", "example.com", joined["repo"]):
        assert needle not in text


def test_シリーズの階層は回ごとのインプレッションを投稿のidで引く(joined):
    _with_outcomes(joined)
    got = dashboard_cli.build(PROJECT, "series", "num", now=NOW)["series"]
    assert len(got["slots_upcoming"]) == 8 and got["slots_upcoming"][1]["filled"] is True
    assert {i["ref"] for i in got["items"]} == {"th-num-1024.md", "th-num-1103.md"}
    episodes = got["outcomes"]["per_episode"]
    assert [e["ref"] for e in episodes] == ["th-num-1024.md"]
    ep = episodes[0]
    # いちばん新しい実測（24h: views 120・likes 5・replies 1）。
    assert ep["impressions"] == 120 and ep["reactions_total"] == 6 and ep["engagement_rate_pct"] == 5.0
    assert ep["clicks"] is None and ep["cannot_say"]["clicks"] == "article_clicks_not_connected"
    assert got["outcomes"]["average_impressions"] == 120.0
    assert got["outcomes"]["average_impressions_by_account"] == {"kp-threads": {"average": 120.0, "n": 1}}
    assert "scenario_show_unavailable" not in got["cannot_say"]


def test_口座をまたいでインプレッションを足さない(joined):
    _with_outcomes(joined)
    _write(os.path.join(joined["queue"], "x-num-1025.md"),
           _sns("kp-x", status="posted", publish_at="2026-10-25T07:00:00+09:00", series="num", media="x",
                extra={"post_id": "2002", "posted_at": "2026-10-25T07:00:05+09:00"}))
    cfg = accounts_mod.load_account("kp-x")
    posts = accounts_mod.data_dirs(cfg, "kp-x")["insights_posts"]
    os.makedirs(posts, exist_ok=True)
    with open(os.path.join(posts, "2002.ndjson"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"account": "kp-x", "post_id": "2002", "collected_at": "2026-10-26T07:00:00+09:00",
                            "age_hours": 24, "marks": [24], "metrics": {"views": 300, "likes": 3}}) + "\n")
    out = dashboard_cli.build(PROJECT, "series", "num", now=NOW)["series"]["outcomes"]
    assert out["average_impressions"] is None and out["cannot_say"]["average_impressions"] == "accounts_not_summed"
    assert set(out["average_impressions_by_account"]) == {"kp-threads", "kp-x"}


def test_fullでも詳細は同じ答え(joined):
    _with_outcomes(joined)
    full = dashboard_cli.build(PROJECT, now=NOW, full=True)
    one = dashboard_cli.build(PROJECT, "series", "num", now=NOW)["series"]
    assert full["series_details"][0] == one
    camp = dashboard_cli.build(PROJECT, "campaign", "num-04-sencha", now=NOW)["campaign"]
    assert full["campaign_details"][0] == camp


# ------------------------------------------------------------------ 型（本物の答えを貼って描ける）

LEVELS = {"all": (), "project": (PROJECT,), "campaign": (PROJECT, "campaign", "num-04-sencha"),
          "series": (PROJECT, "series", "num")}


def _paste(level, payload):
    with open(os.path.join(TEMPLATES, f"{level}.html"), encoding="utf-8") as f:
        html = f.read()
    text = json.dumps(payload, ensure_ascii=False)
    return text, SLOT.sub(lambda m: '<script type="application/json" id="thth-data">' + text.replace("</", "<\\/")
                          + "</script>", html, count=1)


@pytest.mark.parametrize("level", list(LEVELS))
def test_本物の答えを型に貼れる(joined, level):
    _with_outcomes(joined)
    payload = dashboard_cli.build(*LEVELS[level], now=NOW)
    text, html = _paste(level, payload)
    assert len(SLOT.findall(html)) == 1 and json.loads(SLOT.findall(html)[0].replace("<\\/", "</")) == payload
    # 型の表の理由（THTH_REASON）が、答えに出る cannot_say の符丁をすべて言える。
    reasons = set(re.findall(r'(\w+):"', re.search(r"const THTH_REASON=\{(.*?)\};", html).group(1)))
    codes = set()

    def collect(value, inside=False):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "cannot_say":
                    if isinstance(child, dict):
                        codes.update(v for v in child.values() if isinstance(v, str))
                    elif isinstance(child, str):
                        codes.add(child)
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)
    collect(payload)
    assert codes <= reasons, codes - reasons


@pytest.mark.skipif(not os.environ.get("THTH_DASHBOARD_DUMP"), reason="見本を書き出すときだけ（人が型を目で見る用）")
def test_本物の答えを型に貼った見本を書き出す(joined):
    _with_outcomes(joined)
    out = os.environ["THTH_DASHBOARD_DUMP"]
    os.makedirs(out, exist_ok=True)
    for level, args in LEVELS.items():
        text, html = _paste(level, dashboard_cli.build(*args, now=NOW))
        with open(os.path.join(out, level + ".json"), "w", encoding="utf-8") as f:
            f.write(text)
        with open(os.path.join(out, level + ".html"), "w", encoding="utf-8") as f:
            f.write(html)
