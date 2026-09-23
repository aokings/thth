"""観測の地図 第 2 段——自分の層（設計 3.5.0 §1 の層 2）。

見るのは:
  - その話題（topic）で出した投稿の数と分母（同じ窓の投稿の数）。
  - views・likes・replies の中央値と n は既存の口（`analytics-report --by topic`）と一致する。
  - n が min_n に届かなければ中央値は null・理由 `below_min_n`（0 と混ぜない）。
  - account をまたいで足さない（媒体ごとに並ぶ）。台帳が読めない account は null と理由。
  - 読むだけ（台帳にも置き場にも書かない）。
"""
from __future__ import annotations

import datetime
from pathlib import Path

from thth import analytics_report, jst, map_view, measured
from tests.test_analytics_comparison import seed

NOW = jst.parse("2026-09-18T12:00:00+09:00")
SINCE = NOW - datetime.timedelta(days=7)


def _seed_topic(account, prefix, topic, values, *, days_ago=2):
    for i, value in enumerate(values):
        posted = NOW - datetime.timedelta(days=days_ago, hours=i)
        seed(account, f"{prefix}{i}", posted, value=value,
             extra={"topic": topic, "metrics": {"views": value, "likes": i, "replies": 1}})


def test_数と中央値はanalytics_reportのtopic別と一致(isolated_account_factory):
    a = isolated_account_factory("kopicha-threads", project="kopicha")
    _seed_topic(a, "c", "#コーヒー", [10, 20, 30, 40, 50, 60])
    _seed_topic(a, "t", "紅茶", [5, 6])
    seed(a, "old", NOW - datetime.timedelta(days=9), value=999, extra={"topic": "コーヒー"})
    layer = map_view.self_layer({a["name"]: {"media": "threads"}}, ["コーヒー", "紅茶", "緑茶"],
                                since=SINCE, now=NOW)
    coffee = layer["コーヒー"]["by_account"][a["name"]]
    assert coffee["posts"] == 6 and coffee["denominator"] == 8 and coffee["medium"] == "threads"
    assert coffee["metrics"]["views"] == {"median": 35.0, "n": 6, "denominator": 6, "reason": None}
    report = analytics_report.answer(a["name"], now=NOW, compare_previous=True, by="topic",
                                     window_days=7, min_n=5)
    strata = report["by_account"][a["name"]]["posts"]["stratified"]["strata"]
    current = strata["コーヒー"]["current"]["metrics"]
    for metric in ("views", "likes", "replies"):
        assert coffee["metrics"][metric]["median"] == current[metric]["median"], metric
        assert coffee["metrics"][metric]["n"] == current[metric]["n_eligible"], metric
    tea = layer["紅茶"]["by_account"][a["name"]]
    assert tea["posts"] == 2 and tea["metrics"]["views"] == {
        "median": None, "n": 2, "denominator": 2, "reason": "below_min_n"}
    none = layer["緑茶"]["by_account"][a["name"]]
    assert none["posts"] == 0 and none["denominator"] == 8
    assert none["metrics"]["views"]["median"] is None and none["metrics"]["views"]["n"] == 0


def test_accountをまたいで足さない_読めない台帳はnullと理由(isolated_account_factory, monkeypatch):
    a = isolated_account_factory("kopicha-threads", project="kopicha")
    b = isolated_account_factory("kopicha-bsky", project="kopicha", media="bluesky")
    _seed_topic(a, "a", "コーヒー", [10] * 5)
    _seed_topic(b, "b", "コーヒー", [90] * 5)
    layer = map_view.self_layer({a["name"]: {"media": "threads"}, b["name"]: {"media": "bluesky"}},
                                ["コーヒー"], since=SINCE, now=NOW)
    node = layer["コーヒー"]
    assert set(node) == {"by_account"}
    assert node["by_account"]["kopicha-threads"]["metrics"]["views"]["median"] == 10
    assert node["by_account"]["kopicha-bsky"]["metrics"]["views"]["median"] == 90

    real = measured.load

    def broken(name, **kwargs):
        if name == "kopicha-bsky":
            raise ValueError("broken")
        return real(name, **kwargs)
    monkeypatch.setattr(measured, "load", broken)
    layer = map_view.self_layer({a["name"]: {"media": "threads"}, b["name"]: {"media": "bluesky"}},
                                ["コーヒー"], since=SINCE, now=NOW)
    assert layer["コーヒー"]["by_account"]["kopicha-bsky"] == {
        "medium": "bluesky", "posts": None, "denominator": None, "metrics": None,
        "cannot_say": "measured_unreadable"}
    assert layer["コーヒー"]["by_account"]["kopicha-threads"]["posts"] == 5


def test_自分の層は読むだけ(isolated_account_factory):
    a = isolated_account_factory("kopicha-threads", project="kopicha")
    _seed_topic(a, "c", "コーヒー", [1, 2, 3, 4, 5])
    root = Path(a["repo_dir"])
    state = Path(a["repo_dir"]).parent
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    listing = sorted(str(p) for p in state.rglob("*"))
    map_view.self_layer({a["name"]: {"media": "threads"}}, ["コーヒー"], since=SINCE, now=NOW)
    assert before == {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert listing == sorted(str(p) for p in state.rglob("*"))
