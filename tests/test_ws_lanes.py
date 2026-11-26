"""括りの段 2 の柵（Mac の側）: レーンの鍵のファイルは `~/.config/thth/lanes/<レーン>.json`。

設計: docs/設計_THTHアカウント（括り）_叩き台_2026-11-26.md §4.1・§4.2。
- レーンを名指ししたら、そのレーンのファイルだけを見る（無ければ `not_logged_in`・remote.json へ倒さない）。
- 名指しが無ければ今までどおり remote.json。
- `thth --lane <レーン> …` は先頭だけを見る。
"""
from __future__ import annotations

import io
import json
import os

import pytest

from thth import cli, remote


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("THTH_REMOTE_CONFIG", raising=False)
    monkeypatch.delenv("THTH_LANE", raising=False)
    yield tmp_path
    # cli が立てた THTH_LANE を残さない（monkeypatch の delenv は後片付けで値を戻してしまうので pop）。
    os.environ.pop("THTH_LANE", None)


def test_default_is_remote_json(home):
    assert remote.config_path() == str(home / ".config/thth/remote.json")


def test_lane_picks_its_own_file(home, monkeypatch):
    monkeypatch.setenv("THTH_LANE", "asmon-kanto")
    assert remote.config_path() == str(home / ".config/thth/lanes/asmon-kanto.json")


def test_named_lane_without_file_does_not_fall_back(home, monkeypatch):
    (home / ".config/thth").mkdir(parents=True)
    (home / ".config/thth/remote.json").write_text(json.dumps({"url": "https://thth.me", "key": "k" * 20}))
    monkeypatch.setenv("THTH_LANE", "kopicha-sns")
    assert remote.load() is None


@pytest.mark.parametrize("bad", ["Asmon", "../x", "a/b", "-x"])
def test_bad_lane_is_refused(home, monkeypatch, bad):
    monkeypatch.setenv("THTH_LANE", bad)
    with pytest.raises(remote.RemoteError, match="lane_invalid"):
        remote.config_path()


def test_login_stdin_with_lane_writes_lane_file(home, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("k" * 24 + "\n"))
    rc = cli.main(["login", "--stdin", "--lane", "asmon-kanto"])
    assert rc == 0
    path = home / ".config/thth/lanes/asmon-kanto.json"
    data = json.loads(path.read_text())
    assert data["lane"] == "asmon-kanto" and data["key"] == "k" * 24
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert not (home / ".config/thth/remote.json").exists()


def test_leading_lane_flag_selects_file_for_logout(home, capsys):
    lanes = home / ".config/thth/lanes"
    lanes.mkdir(parents=True)
    (lanes / "a-one.json").write_text("{}")
    (home / ".config/thth/remote.json").write_text("{}")
    assert cli.main(["--lane", "a-one", "logout"]) == 0
    assert not (lanes / "a-one.json").exists()
    assert (home / ".config/thth/remote.json").exists()


def test_lane_flag_only_at_the_head():
    assert cli._take_lane_flag(["send", "--lane", "x"]) == ["send", "--lane", "x"]
