"""観測の地図 第 7 段——保持・削除の依頼・退出（設計 3.5.0 §2・照合 §6-4・§6-5・§6-6）。

見るのは:
  - 保持は日単位（既定 180 日・1〜180）。collect は世間の層が無効でも毎回古い行を削る。
    読むときも保持を過ぎた行は見せない。
  - 削除の依頼: project の地図を丸ごと（点・線・行・ロック・置き場）／期間の行だけ。
    変更ログは presence-only。消したあと置き場のどこにも語も行も残らない。
  - 退出: 最後の account なら project の地図を丸ごと、他が残るならその媒体の行だけ。
    地図の置き場が壊れていれば何も止めずに断る。何度呼んでも同じ。既存の退出は崩さない。
  - 置き場は git の外（`state/` は .gitignore・repo の中に書かない）。
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

import pytest

from thth import accounts, admin_log, cli, jst, leave, leave_gate as gate, map_store, map_view
from thth import map_world
from tests.test_v212_leave import worker  # noqa: F401  (fixture)
from tests.test_v212_server_writes import env  # noqa: F401  (fixture)
from tests.test_v350_map_nodes import projects  # noqa: F401  (fixture)
from tests.test_v350_map_observe import _edge, _node

ROOT = Path(__file__).resolve().parent.parent


def _days_ago(days):
    return (jst.now_jst().date() - datetime.timedelta(days=days)).isoformat()


def map_dir(project="kopicha"):
    return Path(os.environ["THTH_ROOT"]) / "state" / "_map" / project


def _rows(project="kopicha"):
    rows = []
    for path in sorted(map_dir(project).glob("*.ndjson")):
        rows.extend(json.loads(line) for line in path.read_text().splitlines() if line.strip())
    return rows


def _seed(project="kopicha", *, ages=(0, 1, 30, 180, 181, 200), media=("threads",)):
    for word in ("コーヒー", "紅茶"):
        try:
            map_store.add_node(project, word, by="masaru")
        except map_store.MapError:
            pass
    rows = []
    for age in ages:
        for medium in media:
            row = _node(_days_ago(age), "コーヒー", 10)
            row["medium"] = medium
            edge = _edge(_days_ago(age), "コーヒー", "紅茶", 6, 10)
            edge["medium"] = medium
            rows += [row, edge]
    map_world.append_rows(project, rows)


def test_保持は日単位で削る_無効でもcollectが削る(projects, monkeypatch):
    monkeypatch.delenv("THTH_MAP_WORLD", raising=False)
    _seed()
    # 181 日前と 180 日前は同じ月に入ることがある——月ではなく日で削る。
    result = map_world.collect("kopicha")
    assert result["cannot_say"] == ["world_layer_disabled"] and result["rows_pruned"] == 4
    dates = sorted({row["date"] for row in _rows()})
    assert dates == sorted({_days_ago(a) for a in (0, 1, 30, 180)})
    assert _days_ago(181) not in json.dumps(_rows())
    for path in map_dir().glob("*.ndjson"):
        assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_保持の日数を短くすると消え_読むときも見せない(projects, monkeypatch, capsys):
    _seed()
    rc = cli.main(["admin", "map", "retention", "kopicha", "7", "--by", "masaru", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert rc == 0 and result["retention_days"] == 7 and result["rows_removed"] == 8
    assert sorted({row["date"] for row in _rows()}) == sorted({_days_ago(0), _days_ago(1)})
    for bad in ("0", "181", "-1"):
        rc = cli.main(["admin", "map", "retention", "kopicha", bad, "--by", "m", "--json"])
        assert rc == 2 and json.loads(capsys.readouterr().out) == {"cannot_say": ["invalid_retention"]}
    # 読むときの下限: 行が残っていても保持を過ぎた日は見せない。
    map_world.append_rows("kopicha", [_node(_days_ago(20), "コーヒー", 30)])
    monkeypatch.setenv("THTH_MAP_WORLD", "1")
    days = map_view.show("kopicha", since="60d")["nodes"][0]["world"]["by_medium"]["threads"]["days"]
    assert [day["date"] for day in days] == [_days_ago(1), _days_ago(0)]


def test_削除の依頼_期間の行だけ(projects, capsys):
    _seed()
    rc = cli.main(["admin", "map", "purge", "kopicha", "--from", _days_ago(30), "--to", _days_ago(1),
                   "--by", "masaru", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert rc == 0 and result["scope"] == "rows" and result["rows_removed"] == 4
    assert {row["date"] for row in _rows()} == {_days_ago(a) for a in (0, 180, 181, 200)}
    assert [row["word"] for row in map_store.load_config("kopicha")["nodes"]] == ["コーヒー", "紅茶"]
    rc = cli.main(["admin", "map", "purge", "kopicha", "--from", "2026-13-01", "--by", "m", "--json"])
    assert rc == 2 and json.loads(capsys.readouterr().out) == {"cannot_say": ["invalid_range"]}
    rc = cli.main(["admin", "map", "purge", "kopicha", "--world-only", "--by", "m", "--json"])
    assert rc == 0 and _rows() == [] and map_store.load_config("kopicha")["nodes"]


def test_削除の依頼_丸ごと消すと置き場のどこにも残らない(projects):
    _seed()
    map_store.add_edge("kopicha", "紅茶", "コーヒー", by="masaru")
    result = map_store.purge("kopicha", by="masaru")
    assert result["scope"] == "whole_map" and result["files_removed"] >= 2
    assert not map_dir().exists()
    root = Path(os.environ["THTH_ROOT"])
    for path in root.rglob("*"):
        if path.is_file():
            text = path.read_bytes().decode("utf-8", "replace")
            assert "コーヒー" not in text and "紅茶" not in text, path
            assert '"window_hours"' not in text, path
        assert ".map-" not in path.name and path.name != "map.json", path
    events, broken = admin_log.read(account="kopicha", event="map_purged")
    assert not broken and events[-1]["diff"]["map"] == ["present", "absent"]
    # 地図が無くなっても読む口は空の地図として答える（壊れない）。
    assert map_view.show("kopicha")["cannot_say"] == ["no_map_nodes"]
    with pytest.raises(map_store.MapError) as caught:
        map_store.purge("kopicha", by=None)
    assert str(caught.value) == "by_required"


def test_置き場はgitの外(projects):
    _seed()
    assert "state/" in (ROOT / ".gitignore").read_text().splitlines()
    root = Path(os.environ["THTH_ROOT"]).resolve()
    assert map_dir().resolve().is_relative_to(root / "state" / "_map")
    for info in projects.values():
        assert not map_dir().resolve().is_relative_to(Path(info["repo_dir"]).resolve())
        repo = Path(info["repo_dir"])
        assert not [p for p in repo.rglob("*.ndjson") if "_map" in str(p)]


# ---------------------------------------------------------------- 退出

def test_退出_最後のaccountならprojectの地図を丸ごと消す(env, worker):
    map_store.add_node("alpha", "コーヒー", by="masaru")
    map_world.append_rows("alpha", [_node(_days_ago(0), "コーヒー", 10)])
    map_store.add_node("beta", "紅茶", by="masaru")
    result = leave.run("alpha", by="operator")
    assert result["phase"] == "completed"
    assert not (env["root"] / "state" / "_map" / "alpha").exists()
    assert map_store.load_config("beta")["nodes"][0]["word"] == "紅茶"
    events, _ = admin_log.read(account="alpha", event="map_purged")
    assert len(events) == 1 and events[0]["by"] == "operator"
    # 何度呼んでも同じ（退出の再試行）。
    assert leave.run("alpha", by="operator") == result
    assert len(admin_log.read(account="alpha", event="map_purged")[0]) == 1


def test_退出_他のaccountが残るならその媒体の行だけ(env, worker):
    other = json.loads((env["root"] / "accounts" / "beta.json").read_text())
    other.update(account="alpha-bsky", project="alpha", media="bluesky",
                 repo_dir=str(env["root"] / "repos/_server/alpha-bsky"))
    (env["root"] / "accounts" / "alpha-bsky.json").write_text(json.dumps(other))
    map_store.add_node("alpha", "コーヒー", by="masaru")
    rows = []
    for medium in ("threads", "bluesky"):
        row = _node(_days_ago(0), "コーヒー", 10)
        row["medium"] = medium
        rows.append(row)
    map_world.append_rows("alpha", rows)
    leave.run("alpha", by="operator")
    assert [row["medium"] for row in map_world.read_rows("alpha")[0]] == ["bluesky"]
    assert map_store.load_config("alpha")["nodes"][0]["word"] == "コーヒー"


def test_退出_地図の置き場が壊れていれば止めずに断る(env, worker, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (env["root"] / "state" / "_map").mkdir(parents=True, mode=0o700)
    os.symlink(elsewhere, env["root"] / "state" / "_map" / "alpha")
    with pytest.raises(ValueError, match="^map_cleanup_incomplete$"):
        leave.run("alpha", by="operator")
    assert not gate.stopped("alpha") and worker == []
    assert accounts.load_account("alpha")["account"] == "alpha"
