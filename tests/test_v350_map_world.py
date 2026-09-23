"""観測の地図 第 5 段——世間の層と共起（設計 3.5.0 §1・§2・§8・照合 §6）。**既定で無効。**

見るのは（媒体は偽物・本物は叩かない）:
  - 既定で無効: `THTH_MAP_WORLD=1` が無ければ媒体を叩かず `world_layer_disabled`。
  - 有効なら点ごとに検索 1 回（媒体ごと）。共起は同じ結果から数える（追加の検索をしない）。
  - 保存行は許可リストの項目だけ。本文・post_id・username・author_key・permalink・時刻の
    生値は置き場のどこにも・出力のどこにも出ない（leak probe）。
  - 小標本（n・distinct・co が 5 未満）は null と `small_sample`。0 件は `zero_or_filtered`。
  - search_type・limit・期間を固定して記録。1 日 1 回（同じ日の 2 回目は叩かない）。
  - Threads は API だけ（アダプタの keyword_search）。Mastodon は「そのサーバで検索に出た数」。
  - 世間の層は project の外（広場の open に参加した他の持ち主）には出ない。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
from pathlib import Path

import pytest

from thth import cli, jst, map_store, map_view, map_world, plaza
from thth.adapters import base as adapter_base
from tests.test_v350_map_nodes import projects  # noqa: F401  (fixture)

POST_ID = "POSTID17841400000001"
USER = "leakuser_zz"
AUTHOR_KEY = "feedfacecafe1234"
BODY = "LEAKBODY"


def message(i, *, text, username=None, minutes=30, author_key=None):
    stamp = jst.now_jst() - datetime.timedelta(minutes=minutes + i)
    user = username or f"{USER}{i}"
    return {"message_id": f"{POST_ID}{i}", "username": user,
            "author_key": author_key or adapter_base.author_key("threads", user),
            "text": f"{BODY}{i} {text}", "timestamp": jst.iso(stamp), "medium": "threads",
            "permalink": f"https://www.threads.net/@{user}/post/{POST_ID}{i}",
            "has_replies": True}


def results():
    """点ごとの偽の検索結果（コーヒー: 8 件・うち 6 件が紅茶・3 件が浅煎りを含む）。"""
    coffee = ([message(i, text="コーヒーと紅茶") for i in range(6)]
              + [message(6, text="コーヒー 浅煎り"), message(7, text="コーヒーの浅煎り豆")])
    coffee[0]["text"] += " 浅煎り"
    tea = [message(i, text="紅茶", minutes=200) for i in range(3)]           # 小標本
    light = [message(i, text="浅煎りとコーヒーと紅茶", username="same_one", author_key="0" * 16)
             for i in range(6)]                                            # distinct 1
    old = [message(i, text="コーヒー", minutes=60 * 30) for i in range(9)]   # 窓の外
    return {"コーヒー": coffee + old[:2], "紅茶": tea, "浅煎り": light, "緑茶": []}


class Fake:
    def __init__(self, medium, table, error=None):
        self.medium = medium
        self.table = table
        self.error = error
        self.calls = []

    def keyword_search(self, word, **kwargs):
        self.calls.append((word, kwargs))
        if self.error is not None:
            raise self.error
        return [dict(row) for row in self.table.get(word, [])]


@pytest.fixture
def world(projects, monkeypatch):
    for word in ("コーヒー", "紅茶", "浅煎り", "緑茶"):
        map_store.add_node("kopicha", word, by="masaru")
    fakes = {medium: Fake(medium, results()) for medium in ("threads", "bluesky", "mastodon")}
    monkeypatch.setattr(map_world, "_adapter", lambda name, cfg: (fakes[cfg.get("media")], None))
    monkeypatch.setenv("THTH_MAP_WORLD", "1")
    return fakes


def map_dir(project="kopicha"):
    return Path(os.environ["THTH_ROOT"]) / "state" / "_map" / project


def stored_rows(project="kopicha"):
    rows = []
    for path in sorted(map_dir(project).glob("*.ndjson")):
        rows.extend(json.loads(line) for line in path.read_text().splitlines() if line.strip())
    return rows


def test_既定で無効_媒体を叩かない(world, monkeypatch, capsys):
    monkeypatch.delenv("THTH_MAP_WORLD")
    result = map_world.collect("kopicha")
    assert result["cannot_say"] == ["world_layer_disabled"] and result["searches"] == 0
    assert all(fake.calls == [] for fake in world.values())
    assert list(map_dir().glob("*.ndjson")) == []
    rc = cli.main(["map", "collect", "kopicha", "--json"])
    assert rc == 0 and json.loads(capsys.readouterr().out)["cannot_say"] == ["world_layer_disabled"]
    shown = map_view.show("kopicha")
    assert shown["world_layer"] == {"enabled": False, "cannot_say": "world_layer_disabled"}
    assert map_view.world_enabled() is False
    monkeypatch.setenv("THTH_MAP_WORLD", "true")
    assert map_view.world_enabled() is False  # 「1」だけ


def test_点ごとに検索1回_共起は同じ結果から数える(world):
    result = map_world.collect("kopicha")
    assert result["searches"] == 12 and result["cannot_say"] == []
    for fake in world.values():
        assert [word for word, _ in fake.calls] == ["コーヒー", "紅茶", "浅煎り", "緑茶"]
        assert all(kwargs == {"search_type": "RECENT", "limit": 25} for _, kwargs in fake.calls)
    rows = [row for row in stored_rows() if row["medium"] == "threads"]
    edges = {tuple(row["edge"]): row for row in rows if "edge" in row}
    assert edges[("コーヒー", "紅茶")]["co"] == 6 and edges[("コーヒー", "紅茶")]["denominator"] == 8
    assert edges[("コーヒー", "浅煎り")] == {
        "date": "2026-09-09", "medium": "threads", "edge": ["コーヒー", "浅煎り"], "co": None,
        "denominator": 8, "search_type": "RECENT", "window_hours": 24, "reason": "small_sample"}
    # co が 0 の線は行を作らない。小標本の点（紅茶）からは線を作らない。
    assert ("コーヒー", "緑茶") not in edges and not any(a == "紅茶" for a, _ in edges)
    # 異なり投稿者が小標本の点（浅煎り: 6 件・1 人）からは、語を含んでいても線を作らない。
    assert not any(a == "浅煎り" for a, _ in edges)


def test_小標本はnullと理由_0件はzero_or_filtered(world):
    map_world.collect("kopicha")
    nodes = {row["node"]: row for row in stored_rows() if row.get("medium") == "threads" and "node" in row}
    coffee = nodes["コーヒー"]
    assert coffee["n"] == 8 and coffee["requested"] == 25 and coffee["distinct"] == 8
    assert coffee["top3_share"] == 0.375 and coffee["latest_age_bucket"] == "lt_1h"
    assert coffee["reason"] is None and coffee["with_username"] == 8
    assert nodes["紅茶"] == {"date": "2026-09-09", "medium": "threads", "node": "紅茶", "n": None,
                             "requested": 25, "distinct": None, "with_username": None,
                             "top3_share": None, "latest_age_bucket": None, "search_type": "RECENT",
                             "window_hours": 24, "reason": "small_sample"}
    light = nodes["浅煎り"]
    assert light["n"] == 6 and light["distinct"] is None and light["top3_share"] is None
    assert light["reason"] == "small_sample"
    assert nodes["緑茶"]["n"] == 0 and nodes["緑茶"]["reason"] == "zero_or_filtered"


def test_保存行は許可リストの項目だけ_leak_probe(world, capsys):
    rc = cli.main(["map", "collect", "kopicha", "--json"])
    collected = capsys.readouterr().out
    assert rc == 0
    rc = cli.main(["map", "show", "kopicha", "--json"])
    shown = capsys.readouterr().out
    assert rc == 0
    rows = stored_rows()
    assert rows and all(set(row) in (map_world.NODE_KEYS, map_world.EDGE_KEYS) for row in rows)
    forbidden = [POST_ID, USER, AUTHOR_KEY, "0" * 16, BODY, "threads.net", "same_one",
                 '"permalink"', '"post_id"', '"message_id"', '"username"', '"author_key"',
                 '"timestamp"', '"text"', '"preview"']
    for i in range(10):
        stamp = jst.now_jst() - datetime.timedelta(minutes=30 + i)
        forbidden += [jst.iso(stamp), jst.iso(stamp)[:16]]
    forbidden.append(adapter_base.author_key("threads", USER + "0"))
    root = Path(os.environ["THTH_ROOT"])
    everything = [(str(path), path.read_bytes().decode("utf-8", "replace"))
                  for path in root.rglob("*") if path.is_file()]
    for needle in forbidden:
        for name, text in everything:
            assert needle not in text, (needle, name)
        assert needle not in collected, needle
        assert needle not in json.dumps([json.loads(shown)["nodes"][i]["world"]
                                         for i in range(4)], ensure_ascii=False), needle
        assert needle not in json.dumps(json.loads(shown)["edges"], ensure_ascii=False), needle
    assert hashlib.sha256(collected.encode()).hexdigest()  # 出力は数だけ（上で確かめた）


def test_1日1回(world):
    map_world.collect("kopicha")
    again = map_world.collect("kopicha")
    assert again["searches"] == 0
    assert all(cell["status"] == "already_collected_today" for cell in again["media"].values())
    assert all(len(fake.calls) == 4 for fake in world.values())


@pytest.mark.parametrize("error, reason", [
    (adapter_base.PermissionMissing("threads_keyword_search", "SECRET-DETAIL"), "scope_missing"),
    (TimeoutError("timed out SECRET-DETAIL"), "provider_timeout"),
    (RuntimeError("boom SECRET-DETAIL"), "unavailable"),
])
def test_失敗は静的な理由だけ(world, error, reason):
    world["threads"].error = error
    map_world.collect("kopicha")
    rows = [row for row in stored_rows() if row["medium"] == "threads"]
    assert rows and all(row["reason"] == reason and row["n"] is None for row in rows)
    assert "SECRET-DETAIL" not in json.dumps(stored_rows(), ensure_ascii=False)


def test_valid_rowは許可リストの外と小標本を断る(world):
    base = map_world.node_row("2026-09-09", "threads", "コーヒー", [], jst.now_jst())
    assert map_world.valid_row(base)
    for extra in ({"post_id": "1"}, {"username": "u"}, {"text": "t"}, {"timestamp": "x"}):
        assert not map_world.valid_row({**base, **extra}), extra
    for key, value in (("n", 3), ("distinct", 1), ("search_type", "TOP"), ("window_hours", 48),
                       ("requested", 100), ("medium", "x"), ("date", "2026-09-09T10:00")):
        assert not map_world.valid_row({**base, key: value}), key
    edge = {"date": "2026-09-09", "medium": "threads", "edge": ["a", "b"], "co": 2,
            "denominator": 8, "search_type": "RECENT", "window_hours": 24, "reason": None}
    assert not map_world.valid_row(edge)
    with pytest.raises(map_store.MapError):
        map_world.append_rows("kopicha", [{**base, "post_id": "1"}])
    assert list(map_dir().glob("*.ndjson")) == []


def test_mapshowに世間の層と共起_Mastodonはそのサーバの数(world):
    map_world.collect("kopicha")
    payload = map_view.show("kopicha", node="コーヒー")
    coffee = payload["nodes"][0]
    assert payload["world_layer"] == {"enabled": True, "cannot_say": None}
    assert set(coffee["world"]["by_medium"]) == {"threads", "bluesky", "mastodon"}
    assert coffee["world"]["by_medium"]["mastodon"]["label"].startswith("そのサーバで検索に出た数")
    assert coffee["world"]["by_medium"]["threads"]["latest"]["n"] == 8
    co = payload["edges"]["co"]
    assert co[0]["edge"] == ["コーヒー", "紅茶"] and co[0]["ratio"] == 0.75
    assert payload["neighbors"]["co"] and all("コーヒー" in e["edge"] for e in payload["neighbors"]["co"])
    # 無効に戻したら見せない（行は保持と削除の規律で消える）。
    os.environ.pop("THTH_MAP_WORLD")
    assert map_view.show("kopicha")["nodes"][0]["world"]["cannot_say"] == "world_layer_disabled"


def test_点を消すとその点の行も消える(world):
    map_world.collect("kopicha")
    removed = map_store.remove_node("kopicha", "紅茶", by="masaru")
    assert removed["rows_removed"] > 0
    rows = stored_rows()
    assert not any(row.get("node") == "紅茶" or "紅茶" in (row.get("edge") or []) for row in rows)


def test_ThreadsはAPIだけ_Xは叩かない(projects, monkeypatch):
    source = (Path(__file__).resolve().parent.parent / "thth" / "map_world.py").read_text()
    for name in ("urllib", "http.client", "requests", "socket", "webbrowser"):
        assert f"import {name}" not in source and f"from {name}" not in source, name
    assert map_world._adapter("x-one", {"media": "x"}) == (None, "not_supported")


def test_世間の層はprojectの外に出ない_openの参加者にも(tmp_path, monkeypatch):
    from tests.test_v340_plaza_mcp import _tenant, _text
    _root, server = _tenant(tmp_path, monkeypatch, allowed={"second": "other"})
    monkeypatch.setenv("THTH_MAP_WORLD", "1")
    map_store.add_node("kopicha", "コーヒー", by="masaru")
    fake = Fake("threads", results())
    monkeypatch.setattr(map_world, "_adapter", lambda name, cfg: (fake, None))
    map_world.collect("kopicha")
    plaza.set_membership("kopicha", joined=True, by="admin")
    plaza.set_membership("other", joined=True, by="admin")
    for target in ("kopicha", "first"):
        denied = server.call_tool("thth_map_show", {"project": target})
        assert denied["isError"] and _text(denied) == "scope_unavailable", target
    # 管理者の CLI（VM の運用者）は project を名指しして読める。
    assert map_view.show("kopicha")["nodes"][0]["world"]["by_medium"]["threads"]["latest"]["n"] == 8
