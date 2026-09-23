"""観測の地図 第 6 段——`thth observe` の 1 行（設計 3.5.0 §3）。

見るのは:
  - 0 段（道具）に「地図: 伸びた点・強まった線」（候補の列挙だけ・本文は作らない）。
  - 点が無い・世間の層が無効なら、点と線の数と理由（null と混ぜない）。
  - 伸びた点は直近の日の n と前の平均、強まった線は共起の割合の変化で選ぶ。
  - 対象の project の地図だけ（他の持ち主の地図は出ない）。
"""
from __future__ import annotations

import datetime
import json

from thth import jst, map_store, map_view, map_world, morning
from tests.test_v350_map_nodes import projects  # noqa: F401  (fixture)


def _tool(payload):
    return next(node for node in payload["sections"] if node["section"] == "tool")["value"]


def _node(date, word, n, distinct=None):
    row = map_world.node_row(date, "threads", word, [], jst.now_jst(), reason=None)
    row.update(n=n, distinct=distinct or n, with_username=n, top3_share=0.3,
               latest_age_bucket="1h_6h", reason=None)
    return row


def _edge(date, a, b, co, denominator):
    return {"date": date, "medium": "threads", "edge": [a, b], "co": co,
            "denominator": denominator, "search_type": "RECENT", "window_hours": 24,
            "reason": None}


def test_点が無ければ理由_世間の層が無効なら数と理由(projects, monkeypatch):
    monkeypatch.delenv("THTH_MAP_WORLD", raising=False)
    payload = morning.build("kopicha", mark=False)
    cell = _tool(payload)["map"]["by_project"]["kopicha"]
    assert cell["cannot_say"] == "no_map_nodes" and cell["n_nodes"] == 0
    map_store.add_node("kopicha", "コーヒー", by="masaru")
    cell = _tool(morning.build("kopicha-threads", mark=False))["map"]["by_project"]["kopicha"]
    assert cell == {"project": "kopicha", "n_nodes": 1, "n_edges": 0, "grown_node": None,
                    "strengthened_edge": None, "cannot_say": "world_layer_disabled"}
    assert map_view.observe_text(cell) == "地図 kopicha: 点 1・線 0（world_layer_disabled）"
    # 他の持ち主の地図は出ない。
    assert set(_tool(morning.build("other", mark=False))["map"]["by_project"]) == {"other"}


def test_伸びた点と強まった線(projects, monkeypatch, capsys):
    for word in ("コーヒー", "紅茶"):
        map_store.add_node("kopicha", word, by="masaru")
    today = jst.now_jst().date()
    rows = []
    for back, (coffee, tea, co) in enumerate([(20, 9, 15), (10, 9, 5), (10, 9, 5), (10, 10, 5)]):
        date = (today - datetime.timedelta(days=back)).isoformat()
        rows += [_node(date, "コーヒー", coffee), _node(date, "紅茶", tea),
                 _edge(date, "コーヒー", "紅茶", co, coffee)]
    map_world.append_rows("kopicha", rows)
    monkeypatch.setenv("THTH_MAP_WORLD", "1")
    payload = morning.build("kopicha", mark=False)
    cell = _tool(payload)["map"]["by_project"]["kopicha"]
    assert cell["grown_node"] == {"node": "コーヒー", "medium": "threads",
                                  "date": today.isoformat(), "n": 20, "base": 10.0,
                                  "change_pct": 100}
    assert cell["strengthened_edge"]["edge"] == ["コーヒー", "紅茶"]
    assert cell["strengthened_edge"]["ratio"] == 0.75 and cell["cannot_say"] is None
    assert "body" not in json.dumps(cell)
    morning.render(payload)
    out = capsys.readouterr().out
    assert "地図 kopicha: 伸びた点 コーヒー（+100%・threads・20／前 10.0）" in out
    assert "強まった線 コーヒー—紅茶（threads・割合 0.75）" in out


def test_変化が無ければno_change(projects, monkeypatch):
    map_store.add_node("kopicha", "コーヒー", by="masaru")
    today = jst.now_jst().date()
    map_world.append_rows("kopicha", [_node((today - datetime.timedelta(days=d)).isoformat(),
                                            "コーヒー", 10) for d in range(3)])
    monkeypatch.setenv("THTH_MAP_WORLD", "1")
    cell = map_view.observe_summary("kopicha")
    assert cell["grown_node"] is None and cell["cannot_say"] == "no_change"
