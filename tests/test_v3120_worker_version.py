"""3.12.0 §6-1・§6-6: 常駐（worker・3.13.0 までの名前は approval-worker）は、ディスクの版が動いたら自分で終わり（systemd が起こし直す）、
起動した版を board に出す。入口で umask 077 を自分で掛ける。

見るのは:
  - 常駐は起動した版（VERSION と commit）を state に残す（0600・秘密なし）。
  - ディスクの版が読み込んだ版と違えば `EXIT_MOVED`（0 以外）で終わる。`--once` は記録も比較もしない。
  - `thth board` は常駐の版を出し、ディスクの版と違えば知らせる。記録が無ければ何も言わない。
  - `thth worker` は ssh で umask 0002 のまま打っても、作るものは 700/600。
"""
from __future__ import annotations

import json
import os
import stat
from types import SimpleNamespace

import pytest

from thth import cli, worker_version
from thth import worker as worker_mod
from thth import report as report_mod


@pytest.fixture
def worker_env(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    monkeypatch.setenv("THTH_ROOT", str(root))
    calls = []
    monkeypatch.setattr(worker_mod, "run_once", lambda credentials: calls.append(credentials))
    monkeypatch.setattr(worker_mod.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(worker_version, "CHECK_SECONDS", 0)
    monkeypatch.setattr(worker_version, "loaded", lambda: {"version": "3.11.1", "rev": "a" * 40})
    previous = os.umask(0o002)
    yield root, calls
    os.umask(previous)


def test_ディスクの版が動いたら常駐は終わり_起動した版を残す(worker_env, monkeypatch, capsys):
    root, calls = worker_env
    disks = iter([{"version": "3.11.1", "rev": "a" * 40}, {"version": "3.11.1", "rev": "a" * 40},
                  {"version": "3.12.0", "rev": "b" * 40}])
    monkeypatch.setattr(worker_version, "on_disk", lambda: next(disks))
    rc = worker_mod.command(SimpleNamespace(credentials="/private/c.json", once=False))
    assert rc == worker_version.EXIT_MOVED and rc != 0
    assert len(calls) == 3
    err = capsys.readouterr().err
    assert "worker_restart: 3.11.1（aaaaaaa） -> 3.12.0（bbbbbbb）" in err
    path = root / "state" / worker_version.RECORD_NAME
    row = json.loads(path.read_text())
    assert row["version"] == "3.11.1" and row["rev"] == "a" * 40 and row["pid"] == os.getpid()
    assert set(row) == {"version", "rev", "pid", "started_at", "unit"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE((root / "state").stat().st_mode) == 0o700


def test_読めない欄では動いたと言わない():
    start = {"version": "3.11.1", "rev": "a" * 40}
    assert not worker_version.moved(start, {"version": "3.11.1", "rev": None})
    assert not worker_version.moved(start, {"version": None, "rev": None})
    assert worker_version.moved(start, {"version": "3.11.1", "rev": "c" * 40})
    assert worker_version.moved(start, {"version": "3.12.0", "rev": None})


def test_onceは記録も比較もしない_umaskは077(worker_env, monkeypatch):
    root, calls = worker_env
    monkeypatch.setattr(worker_version, "on_disk", lambda: pytest.fail("--once does not compare"))
    assert worker_mod.command(SimpleNamespace(credentials="/private/c.json", once=True)) == 0
    assert calls == ["/private/c.json"]
    assert not (root / "state" / worker_version.RECORD_NAME).exists()
    # 入口で掛けた umask がそのまま残っている（読むだけの往復）。
    current = os.umask(0o077)
    assert current == 0o077


def test_手で打った常駐が作るものは700と600(worker_env, monkeypatch):
    root, _ = worker_env
    made = []

    def run_once(credentials):
        directory = root / "state" / "someone"
        os.makedirs(directory)
        (directory / "job.json").write_text("{}")
        made.append(directory)
    monkeypatch.setattr(worker_mod, "run_once", run_once)
    assert worker_mod.command(SimpleNamespace(credentials="/private/c.json", once=True)) == 0
    (directory,) = made
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((directory / "job.json").stat().st_mode) == 0o600


def test_boardの行(monkeypatch):
    assert worker_version.board_lines(None) == []
    running = {"version": "3.11.1", "rev": "a" * 40, "pid": 4242, "started_at": "2026-09-25T06:05:00+09:00"}
    same = {"running": running, "disk": {"version": "3.11.1", "rev": "a" * 40}, "differs": False, "alive": True}
    (line,) = worker_version.board_lines(same)
    assert line == "常駐（worker）: 3.11.1（aaaaaaa）  pid 4242・起動 2026-09-25T06:05:00+09:00"
    old = dict(same, disk={"version": "3.12.0", "rev": "b" * 40}, differs=True)
    lines = worker_version.board_lines(old)
    assert len(lines) == 2 and "ディスクの版は 3.12.0（bbbbbbb） です——常駐は古い版で動いています" in lines[1]
    assert "systemctl restart thth-worker" in lines[1]
    dead = dict(same, alive=False)
    (line,) = worker_version.board_lines(dead)
    assert "pid 4242 は動いていません" in line


def test_boardに常駐の版とずれが出る(isolated_account, monkeypatch, capsys):
    assert worker_version.record_start({"version": "3.11.1", "rev": "a" * 40})
    monkeypatch.setattr(worker_version, "on_disk", lambda: {"version": "3.12.0", "rev": "b" * 40})
    summary = report_mod.board_summary()
    state = summary["app"]["worker"]
    assert state["running"]["version"] == "3.11.1" and state["differs"] is True and state["alive"] is True
    assert cli.main(["board"]) == 0
    out = capsys.readouterr().out
    assert f"常駐（worker）: 3.11.1（aaaaaaa）  pid {os.getpid()}・起動 " in out
    assert "ディスクの版は 3.12.0（bbbbbbb） です" in out


def test_記録が無ければboardは常駐について何も言わない(isolated_account, capsys):
    assert report_mod.board_summary()["app"]["worker"] is None
    assert cli.main(["board"]) == 0
    assert "常駐（worker）" not in capsys.readouterr().out


# --- 3.13.0: 名前を改めた（記録 worker.json・unit thth-worker.service）----------------------


def test_旧い記録approval_worker_jsonは新しい記録が無いときだけ読む(isolated_account):
    state = os.path.dirname(worker_version.record_path())
    os.makedirs(state, mode=0o700, exist_ok=True)
    legacy = {"version": "3.12.0", "rev": "c" * 40, "pid": 4242, "started_at": "2026-09-26T06:00:00+09:00"}
    with open(os.path.join(state, worker_version.LEGACY_RECORD_NAME), "w", encoding="utf-8") as stream:
        json.dump(legacy, stream)
    assert worker_version.read_record() == legacy
    assert worker_version.record_start({"version": "3.13.0", "rev": "d" * 40})
    assert worker_version.read_record()["version"] == "3.13.0"
    # 旧い記録は消さない・書き換えない。
    with open(os.path.join(state, worker_version.LEGACY_RECORD_NAME), encoding="utf-8") as stream:
        assert json.load(stream) == legacy


def test_どのunitで動いているかをcgroupから読む(tmp_path):
    cgroup = tmp_path / "cgroup"
    cgroup.write_text("0::/system.slice/thth-approval-worker.service\n")
    assert worker_version.current_unit(str(cgroup)) == worker_version.LEGACY_UNIT
    cgroup.write_text("12:pids:/system.slice/thth-worker.service\n0::/system.slice/thth-worker.service\n")
    assert worker_version.current_unit(str(cgroup)) == worker_version.UNIT
    cgroup.write_text("0::/user.slice/user-1000.slice/session-3.scope\n")
    assert worker_version.current_unit(str(cgroup)) is None
    assert worker_version.current_unit(str(tmp_path / "missing")) is None


def test_古いunitで動いていればboardが入れ替えを促す():
    running = {"version": "3.13.0", "rev": "a" * 40, "pid": 4242, "started_at": "2026-09-26T06:05:00+09:00"}
    base = {"disk": {"version": "3.13.0", "rev": "a" * 40}, "differs": False, "alive": True}
    old = worker_version.board_lines(dict(base, running=dict(running, unit=worker_version.LEGACY_UNIT)))
    assert len(old) == 2 and worker_version.LEGACY_UNIT in old[0] and worker_version.LEGACY_UNIT in old[1]
    assert "docs/運用_招待する側.md §6" in old[1]
    new = worker_version.board_lines(dict(base, running=dict(running, unit=worker_version.UNIT)))
    assert len(new) == 1 and worker_version.UNIT in new[0]
    # 古い unit で版がずれていれば、restart の案内はその unit の名前で出す。
    moved = worker_version.board_lines(dict(base, running=dict(running, unit=worker_version.LEGACY_UNIT),
                                            disk={"version": "3.13.1", "rev": "b" * 40}, differs=True))
    assert "systemctl restart thth-approval-worker" in moved[1]
