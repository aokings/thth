"""5.34.0: 鍵に threads_business_basic があるのに Meta が #100 で断る（11/28 soh-ig・kopicha-ig）。

見るのは:
  - `thth doctor <IG>` の `connected_threads_user` の行: 鍵の一覧（/debug_token）に権限があれば
    「乗っていません」と言わず「権限は鍵にあるが Meta がこの欄を出さない（審査前の見込み）」と言う。
    一覧に無ければ今の文（乗っていません）のまま。
  - 権限の見張り（`thth admin meta keepalive`）: 一覧にある権限の読みが #100 で断られたら `withheld` として記録し、
    rc 1 にせず成功にも数えない。一覧に無い・#100 でない・5xx は今までどおり失敗（rc 1）。
偽の Graph API（loopback）と試験の中で作った値だけを使う。本物の Meta には触らない。
"""
from __future__ import annotations

import json

from tests.test_facebook_adapter import fake_graph as fb_fake_graph
from tests.test_m5_permission_watch import NOW, V, facebook_world, row_of, world  # noqa: F401
from tests.test_meta_stage2 import fake_graph
from thth import accounts, cli, refusal_catalog, runs
from thth import permission_watch as pw
from thth.adapters import instagram
from thth.meta import graph as meta_graph

PERMISSION = "threads_business_basic"
DENIED_100 = (400, {"error": {"code": 100, "type": "OAuthException",
                              "message": "(#100) Missing permission threads_business_basic for this field"}})


# ---------------------------------------------------------------- doctor（item 2）

def _probe(scopes, threads_answer):
    from tests.test_meta_stage3 import IG, adapter as ig_adapter, limit_route
    routes = {("GET", f"{V}/{IG}"): (200, {"id": IG, "username": "kopi_chaba"}),
              ("GET", f"{V}/debug_token"): (200, {"data": {"scopes": scopes, "expires_at": 0}}),
              ("GET", f"{V}/{IG}/content_publishing_limit"): limit_route(),
              ("GET", f"{V}/{IG}/media"): (200, {"data": []}),
              ("GET", f"{V}/{IG}/insights"): (200, {"data": []}),
              ("GET", f"{V}/{IG}/connected_threads_user"): threads_answer}
    with fb_fake_graph(routes) as (url, _calls):
        rows = {r["key"]: r for r in ig_adapter(url).probe()}
    return rows["connected_threads_user"]


def test_doctor_鍵の一覧にあれば乗っていませんと言わない():
    row = _probe([*instagram.SCOPES, PERMISSION], DENIED_100)
    assert row["ok"] is None
    assert "乗っていません" not in row["detail"] and "thth auth" not in row["detail"]
    assert "権限は鍵にあります" in row["detail"] and "Meta がこの欄を出しません" in row["detail"]
    assert "Advanced" in row["detail"]


def test_doctor_鍵の一覧に無ければ今の文のまま():
    row = _probe(list(instagram.SCOPES), DENIED_100)
    assert PERMISSION not in instagram.SCOPES
    assert row["ok"] is None and "乗っていません" in row["detail"]
    assert "Meta がこの欄を出しません" not in row["detail"]


# ---------------------------------------------------------------- keepalive（item 3）

def _ig_world(world, monkeypatch, *, source="response", scopes=None):
    facebook_world(world, monkeypatch, "a-ig", "instagram", ig_user_id="77")
    world.cfgs["a-ig"]["user_id"] = "77"
    path = world.cfgs["a-ig"]["token"]
    token = json.load(open(path, encoding="utf-8"))
    token.update(user_id="77", scopes=sorted(scopes if scopes is not None else instagram.InstagramAdapter.KEEPALIVE_READS),
                 scopes_source=source)
    open(path, "w", encoding="utf-8").write(json.dumps(token))
    return "a-ig"


def _routes(threads_answer):
    post = {"id": "1", "caption": "c", "timestamp": "2026-11-01T00:00:00+0000"}
    return {("GET", V + "/77"): (200, {"id": "77", "username": "u"}),
            ("GET", V + "/me"): (200, {"id": "77", "username": "u"}),
            ("GET", V + "/77/media"): (200, {"data": [post]}),
            ("GET", V + "/1/comments"): (200, {"data": []}),
            ("GET", V + "/77/insights"): (200, {"data": []}),
            ("GET", V + "/77/connected_threads_user"): threads_answer,
            ("GET", V + "/77/upcoming_events"): (200, {"data": []})}


def _keepalive(monkeypatch, capsys, threads_answer):
    with fake_graph(_routes(threads_answer)) as (base, _seen):
        monkeypatch.setenv(meta_graph.BASE_URL_ENV, base)
        rc = cli.main(["admin", "meta", "keepalive", "--json"])
    return rc, json.loads(capsys.readouterr().out)


def _results(out):
    return {row["permission"]: row["result"] for row in out["results"]}


def test_keepalive_鍵にある権限の100は失敗に数えず成功にも数えない(world, monkeypatch, capsys):
    name = _ig_world(world, monkeypatch)
    rc, out = _keepalive(monkeypatch, capsys, DENIED_100)
    assert rc == 0
    results = _results(out)
    assert results[PERMISSION] == "withheld"
    assert {v for k, v in results.items() if k != PERMISSION} == {"ok"}
    row = next(r for r in out["results"] if r["permission"] == PERMISSION)
    assert row["error"] == pw.WITHHELD
    lines = [json.loads(line) for line in
             open(runs.path_for(accounts.state_dir_for(name), "2026-11"), encoding="utf-8")]
    mine = [line for line in lines if line.get("permission") == PERMISSION]
    assert [(line["status"], line["error"]) for line in mine] == [("withheld", pw.WITHHELD)]
    view = row_of(pw.build(), PERMISSION)
    assert view["days_idle"] != 0 and view["keepalive_error"] == pw.WITHHELD   # 成功に数えない
    assert row_of(pw.build(), "instagram_basic")["days_idle"] == 0


def test_keepalive_文の出力も失敗と言わない(world, monkeypatch, capsys):
    _ig_world(world, monkeypatch)
    with fake_graph(_routes(DENIED_100)) as (base, _seen):
        monkeypatch.setenv(meta_graph.BASE_URL_ENV, base)
        assert cli.main(["admin", "meta", "keepalive"]) == 0
    text = capsys.readouterr().out
    line = next(line for line in text.splitlines() if PERMISSION in line)
    assert "withheld" in line and "失敗に数えません" in line


def test_keepalive_一覧がresponseでなければ今までどおり失敗(world, monkeypatch, capsys):
    _ig_world(world, monkeypatch, source="requested")
    rc, out = _keepalive(monkeypatch, capsys, DENIED_100)
    assert rc == 1 and _results(out)[PERMISSION] == "error"


def test_keepalive_100でない権限の断りは今までどおり失敗(world, monkeypatch, capsys):
    _ig_world(world, monkeypatch)
    rc, out = _keepalive(monkeypatch, capsys, (403, {"error": {"code": 200, "message": "Permissions error"}}))
    assert rc == 1 and _results(out)[PERMISSION] == "error"


def test_keepalive_鍵が使えないのは今までどおり失敗(world, monkeypatch, capsys):
    _ig_world(world, monkeypatch)
    rc, out = _keepalive(monkeypatch, capsys, (400, {"error": {"code": 190, "message": "Invalid OAuth access token"}}))
    assert rc == 1 and _results(out)[PERMISSION] == "error"


def test_keepalive_5xxは今までどおり失敗(world, monkeypatch, capsys):
    _ig_world(world, monkeypatch)
    rc, out = _keepalive(monkeypatch, capsys, (500, {"error": {"code": 1, "message": "unknown"}}))
    assert rc == 1 and _results(out)[PERMISSION] == "error"


def test_目録に3つがそろう():
    entry = refusal_catalog.CATALOG[pw.WITHHELD]
    assert entry["why"] and entry["fix"]["text"] and entry["better"]
    assert pw.WITHHELD in pw.KEEPALIVE_REFUSALS
