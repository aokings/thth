"""3.6.0 `thth send --goal`（設計 3.6.0 §A1）。

同席の送信にも目的を 1 つ付けられる。**digest には入れない**（queue の承認の指紋に
入れないのと同じ）。`sent/` と runs に残り、`--by goal` と observe がそこから読む。
"""
from __future__ import annotations

import os

from tests.conftest import run_thth
from thth import accounts as accounts_mod
from thth import core, goals
from thth import runs as runs_mod
from thth import sent as sent_mod
from thth.adapters import base


class _Spy:
    def __init__(self):
        self.posts = []

    def publish(self, post, **_kwargs):
        self.posts.append(post)
        return base.PublishResult(post_id=f"P{len(self.posts)}", url=None,
                                  ts="2026-09-09T09:00:00+09:00")

    def fetch_post(self, _post_id):
        return {"author_key": None}


def _dry(account, **kwargs):
    lines = []
    result = core.send_once(account["name"], text="本文です", production_flag=False,
                            log=lines.append, **kwargs)
    return result, lines


def test_digestは目的で変わらない_乾式に目的を見せる(isolated_account_factory):
    account = isolated_account_factory(production=True)
    plain, _ = _dry(account)
    click, lines = _dry(account, goal="click")
    assert plain.digest == click.digest
    assert "目的: click（サイト誘導・digest には入りません）" in lines


def test_送るとsentとrunsに目的が残る(isolated_account_factory):
    account = isolated_account_factory(production=True)
    digest = _dry(account)[0].digest
    spy = _Spy()
    result = core.send_once(account["name"], text="本文です", production_flag=True,
                            confirm=digest, goal="follow", adapter_factory=lambda *_: spy)
    assert result.exit_code == 0 and result.post_id == "P1"
    state_dir = accounts_mod.state_dir_for(account["name"])
    assert sent_mod.read(state_dir, "P1")["goal"] == "follow"
    ok = [r for r in runs_mod.read_runs(state_dir) if r["action"] == "post" and r["status"] == "ok"]
    assert ok[-1]["goal"] == "follow"
    assert goals.recorded_goals(account["name"]) == {"P1": "follow"}


def test_目的を省くとnoneで残る(isolated_account_factory):
    account = isolated_account_factory(production=True)
    digest = _dry(account)[0].digest
    result = core.send_once(account["name"], text="本文です", production_flag=True,
                            confirm=digest, adapter_factory=lambda *_: _Spy())
    state_dir = accounts_mod.state_dir_for(account["name"])
    assert sent_mod.read(state_dir, result.post_id)["goal"] == goals.NONE


def test_4語以外は送らない(isolated_account_factory):
    account = isolated_account_factory(production=True)

    class _Never:
        def publish(self, *a, **k):
            raise AssertionError("目的が読めないのに publish を呼んだ")

    result = core.send_once(account["name"], text="本文です", production_flag=True,
                            confirm="x", goal="sales", adapter_factory=lambda *_: _Never())
    assert result.exit_code == 1 and result.error == "goal_invalid"


def test_CLIのgoalは4語だけ(isolated_account_factory, tmp_path):
    account = isolated_account_factory(production=True)
    text = tmp_path / "t.txt"
    text.write_text("本文です\n", encoding="utf-8")
    ok = run_thth(["send", account["name"], "--text-file", str(text), "--goal", "reply"])
    assert ok.returncode == 0, ok.stderr
    assert "目的: reply（会話・digest には入りません）" in ok.stdout
    bad = run_thth(["send", account["name"], "--text-file", str(text), "--goal", "sales"])
    assert bad.returncode == 2 and "--goal" in bad.stderr
    assert not os.path.isdir(os.path.join(accounts_mod.state_dir_for(account["name"]), "sent"))
