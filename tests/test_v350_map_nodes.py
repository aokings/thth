"""観測の地図 第 1 段——点と線の管理（設計 3.5.0 §1・§8・照合 §6-2）。

見るのは:
  - 点と包含の線は人だけが足す（`--by` 必須）。置き場は `state/_map/<project>/`（0600・0700）。
  - 点は project あたり 20 まで。@ハンドル・仮名・URL・個人名らしき語・秘密は点にできない。
  - 線の両端は既にある別々の点・輪は作らない・点を消すと掛かる線も消える。
  - 変更ログは presence-only（点の語は記録に入らない）。ログに書けなければ変えない。
  - 台帳に無い project の地図は作らない。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat

import pytest

from thth import admin_log, cli, map_store
from tests.conftest import init_real_repo


@pytest.fixture
def projects(isolated_account_factory, tmp_path):
    """持ち主 2 組: kopicha（threads・bluesky・mastodon）と other（threads）。"""
    kopicha_repo = init_real_repo(tmp_path, "kopicha")
    other_repo = init_real_repo(tmp_path, "other")
    return {
        "kopicha-threads": isolated_account_factory(
            "kopicha-threads", project="kopicha", repo_dir=kopicha_repo, handle="kopicha_tea"),
        "kopicha-bsky": isolated_account_factory(
            "kopicha-bsky", project="kopicha", media="bluesky", repo_dir=kopicha_repo,
            handle="kopicha.bsky.social"),
        "kopicha-mstdn": isolated_account_factory(
            "kopicha-mstdn", project="kopicha", media="mastodon", repo_dir=kopicha_repo,
            handle="kopicha", instance="https://mastodon.example"),
        "other-threads": isolated_account_factory(
            "other-threads", project="other", repo_dir=other_repo, handle="other_shop"),
    }


def map_dir(project="kopicha"):
    return Path(os.environ["THTH_ROOT"]) / "state" / "_map" / project


def test_点は人が足し置き場は私有(projects):
    result = map_store.add_node("kopicha", "  #コーヒー ", by="masaru")
    assert result["node"] == "コーヒー" and result["n_nodes"] == 1 and result["max_nodes"] == 20
    path = map_dir() / "map.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(map_dir().stat().st_mode) == 0o700
    assert stat.S_IMODE(map_dir().parent.stat().st_mode) == 0o700
    config = map_store.load_config("kopicha")
    assert [row["word"] for row in config["nodes"]] == ["コーヒー"]
    assert config["nodes"][0]["by"] == "masaru" and config["retention_days"] == 180
    # SNS の台帳（repo）には 1 バイトも書かない。
    repo = Path(projects["kopicha-threads"]["repo_dir"])
    assert not [p for p in repo.rglob("*") if ".git" not in p.parts and "map" in p.name]


def test_byが無ければ足さない(projects):
    for by in (None, "", "  ", "a\nb"):
        with pytest.raises(map_store.MapError) as caught:
            map_store.add_node("kopicha", "コーヒー", by=by)
        assert str(caught.value) == "by_required"
    assert not map_dir().exists()


def test_account名ならそのproject_台帳に無いprojectは作らない(projects):
    assert map_store.add_node("kopicha-bsky", "紅茶", by="m")["project"] == "kopicha"
    with pytest.raises(map_store.MapError) as caught:
        map_store.add_node("nobody", "紅茶", by="m")
    assert str(caught.value) == "project_unknown"
    with pytest.raises(map_store.MapError) as caught:
        map_store.add_node("../kopicha", "紅茶", by="m")
    assert str(caught.value) == "invalid_project"
    assert not map_dir("nobody").exists()


@pytest.mark.parametrize("word, reason", [
    ("@kopicha_tea", "node_handle"),
    ("＠someone", "node_handle"),
    ("コーヒー@家", "node_handle"),
    ("0123456789abcdef", "node_handle"),
    ("https://example.com/coffee", "node_url"),
    ("www.example", "node_url"),
    ("coffee.example.jp", "node_url"),
    ("threads.net", "node_url"),
    ("山田さん", "node_person_like"),
    ("鈴木先生", "node_person_like"),
    ("John Smith", "node_person_like"),
    ("", "invalid_node"),
    ("#", "invalid_node"),
    ("あ" * 41, "invalid_node"),
    ("改\n行", "invalid_node"),
    ("sk-ant-" + "a" * 30, "secret_detected"),
])
def test_点に入れられない語(projects, word, reason):
    with pytest.raises(map_store.MapError) as caught:
        map_store.add_node("kopicha", word, by="m")
    assert str(caught.value) == reason
    assert map_store.load_config("kopicha")["nodes"] == []


@pytest.mark.parametrize("word", ["先生", "天気模様", "源氏物語", "Coffee", "スペシャルティコーヒー",
                                  "中学受験", "coffee roasting"])
def test_話題の語は通る(projects, word):
    assert map_store.add_node("kopicha", word, by="m")["node"] == word


def test_点は20まで_同じ点は足さない(projects):
    for i in range(20):
        map_store.add_node("kopicha", f"話題{i}", by="m")
    with pytest.raises(map_store.MapError) as caught:
        map_store.add_node("kopicha", "話題20", by="m")
    assert str(caught.value) == "node_limit"
    map_store.add_node("other", "ｃｏｆｆｅｅ", by="m")
    with pytest.raises(map_store.MapError) as caught:
        map_store.add_node("other", "COFFEE", by="m")
    assert str(caught.value) == "node_exists"
    # 上限は project ごと。
    assert map_store.load_config("other")["nodes"][0]["word"] == "ｃｏｆｆｅｅ"


def test_包含の線_両端は点_輪は作らない_消すと線も消える(projects):
    for word in ("コーヒー", "スペシャルティコーヒー", "浅煎り"):
        map_store.add_node("kopicha", word, by="m")
    result = map_store.add_edge("kopicha", "スペシャルティコーヒー", "コーヒー", by="m")
    assert result["edge"] == {"narrower": "スペシャルティコーヒー", "broader": "コーヒー",
                              "kind": "broader"}
    map_store.add_edge("kopicha", "浅煎り", "スペシャルティコーヒー", by="m")
    for narrower, broader, reason in (("コーヒー", "浅煎り", "edge_cycle"),
                                      ("コーヒー", "スペシャルティコーヒー", "edge_cycle"),
                                      ("浅煎り", "浅煎り", "invalid_edge"),
                                      ("紅茶", "コーヒー", "invalid_edge"),
                                      ("浅煎り", "スペシャルティコーヒー", "edge_exists")):
        with pytest.raises(map_store.MapError) as caught:
            map_store.add_edge("kopicha", narrower, broader, by="m")
        assert str(caught.value) == reason, (narrower, broader)
    removed = map_store.remove_node("kopicha", "スペシャルティコーヒー", by="m")
    assert removed["edges_removed"] == 2
    assert map_store.load_config("kopicha")["edges"] == []
    with pytest.raises(map_store.MapError) as caught:
        map_store.remove_edge("kopicha", "浅煎り", "コーヒー", by="m")
    assert str(caught.value) == "edge_not_found"
    with pytest.raises(map_store.MapError) as caught:
        map_store.remove_node("kopicha", "スペシャルティコーヒー", by="m")
    assert str(caught.value) == "node_not_found"


def test_変更ログはpresence_onlyで点の語は入らない(projects):
    map_store.add_node("kopicha", "秘密の話題語", by="masaru")
    map_store.add_node("kopicha", "別の話題語", by="masaru")
    map_store.add_edge("kopicha", "秘密の話題語", "別の話題語", by="masaru")
    map_store.remove_edge("kopicha", "秘密の話題語", "別の話題語", by="masaru")
    map_store.remove_node("kopicha", "秘密の話題語", by="masaru")
    rows, broken = admin_log.read(account="kopicha")
    assert broken == 0
    assert [row["event"] for row in rows] == ["map_node_added", "map_node_added", "map_edge_added",
                                              "map_edge_removed", "map_node_removed"]
    raw = (Path(os.environ["THTH_ROOT"]) / "state" / "_admin" / "accounts.ndjson").read_text()
    assert "秘密の話題語" not in raw and "別の話題語" not in raw
    assert rows[0]["diff"]["map_node"] == ["absent", "present"]


def test_ログに書けなければ変えない(projects, monkeypatch):
    def broken(*args, **kwargs):
        raise admin_log.AdminLogError("admin_log_write_failed")
    monkeypatch.setattr(admin_log, "append", broken)
    with pytest.raises(map_store.MapError) as caught:
        map_store.add_node("kopicha", "コーヒー", by="m")
    assert str(caught.value) == "map_log_unavailable"
    assert map_store.load_config("kopicha")["nodes"] == []


def test_置き場がsymlinkなら読まない(projects, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    map_dir().parent.mkdir(parents=True, mode=0o700)
    os.symlink(elsewhere, map_dir())
    with pytest.raises(map_store.MapError) as caught:
        map_store.add_node("kopicha", "コーヒー", by="m")
    assert str(caught.value) == "map_store_unavailable"
    assert list(elsewhere.iterdir()) == []


def test_CLIの管理者の口(projects, capsys):
    rc = cli.main(["admin", "map", "node", "add", "kopicha", "コーヒー", "--by", "masaru", "--json"])
    out = capsys.readouterr().out
    assert rc == 0 and json.loads(out)["node"] == "コーヒー"
    cli.main(["admin", "map", "node", "add", "kopicha", "スペシャルティコーヒー", "--by", "masaru"])
    rc = cli.main(["admin", "map", "edge", "add", "kopicha", "スペシャルティコーヒー", "コーヒー",
                   "--by", "masaru"])
    out = capsys.readouterr().out
    assert rc == 0 and "スペシャルティコーヒー ⊂ コーヒー" in out
    rc = cli.main(["admin", "map", "node", "add", "kopicha", "@someone", "--by", "masaru", "--json"])
    captured = capsys.readouterr()
    assert rc == 2 and json.loads(captured.out) == {"cannot_say": ["node_handle"]}
    assert captured.err.startswith("node_handle: ")
    rc = cli.main(["admin", "map", "node", "add", "kopicha", "紅茶"])
    captured = capsys.readouterr()
    assert rc == 2 and captured.err.startswith("by_required")
