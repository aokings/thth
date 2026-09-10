"""複数本の日時指定が指定どおりに効くか（masaru の受け入れ条件 2026-09-10）。

> 複数本数の投稿の日時指定が出来て、期限が切れたときなどにトークンの入替が
> 出来ればそれでOKなんですけど

「出来る」を口で言わずに、**3 本を別々の時刻に指定して、その時刻に出ることを
実プロセスで確かめる**。時計は注入する（`now` 引数）。
"""
from __future__ import annotations

import datetime
from pathlib import Path

from tests.conftest import approve_via_cli, init_git_pair, make_queue_text, commit_and_push_path, run_thth
from thth import core
from thth.adapters.base import PublishResult

REL = "docs/sns/queue/a.md"
TIMES = ["2026-09-09T09:00:00+09:00",
         "2026-09-09T12:00:00+09:00",
         "2026-09-09T15:00:00+09:00"]


class Spy:
    """公開の記録を取る。**`ts` は実行時刻を返す**（本物の Threads と同じ）。

    ここを固定値にすると `posted_at` がその値で書かれ、`min_interval_hours` の
    判定が実際と変わってしまう（最初この罠に落ちて、効いていない min_interval が
    「効いている」ように見えた）。
    """

    def __init__(self):
        self.calls = []
        self.now = None

    def publish(self, post, **kw):
        self.calls.append(post.text)
        return PublishResult(f"ID{len(self.calls)}", None, self.now.isoformat())


def _at(iso: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(iso)


def _setup_three(tmp_path, factory, **account_overrides):
    pair = init_git_pair(tmp_path, seed_content=make_queue_text(
        {"status": "draft", "publish_at": TIMES[0]}, body="## threads\n\n1 本目。\n"))
    account = factory(repo_dir=pair["work"], production=True, **account_overrides)
    first = Path(pair["work"]) / REL
    assert approve_via_cli(str(first)).returncode == 0

    for i, iso in enumerate(TIMES[1:], start=2):
        path = Path(pair["work"]) / f"docs/sns/queue/{i}.md"
        path.write_text(make_queue_text({"status": "approved", "publish_at": iso},
                                         body=f"## threads\n\n{i} 本目。\n"))
        commit_and_push_path(str(path), message=f"{i} 本目")
    return pair, account


def _run_at(account_name, iso, spy):
    spy.now = _at(iso)
    return core.throw_once(account_name, production_flag=True,
                            adapter_factory=lambda *_: spy, now=spy.now)


def test_3本を別々の時刻に指定したら指定どおりに出る(tmp_path, isolated_account_factory):
    """本命。台帳の既定（`min_interval_hours: 0`・`quiet_hours: null`）で、
    指定した 3 つの時刻にそれぞれ 1 本ずつ出る。"""
    pair, account = _setup_three(tmp_path, isolated_account_factory)
    spy = Spy()

    # 指定時刻より前には出ない
    _run_at(account["name"], "2026-09-09T08:50:00+09:00", spy)
    assert spy.calls == [], "指定時刻より前に出た"

    for i, iso in enumerate(TIMES, start=1):
        _run_at(account["name"], iso, spy)
        assert len(spy.calls) == i, f"{iso} に {i} 本目が出ていない: {spy.calls}"

    assert spy.calls == ["1 本目。", "2 本目。", "3 本目。"], spy.calls


def test_min_intervalを入れると指定時刻を上書きする(tmp_path, isolated_account_factory):
    """**明示した時刻より台帳の当て推量が勝つ**という形が残っていないことの確認。

    `min_interval_hours: 6` を**わざと**入れると 12 時の 1 本が出ない。これは
    設定した側の意思なので挙動としては正しいが、**既定でそうなっていてはいけない**
    （masaru 裁定 2026-09-09「1 日に 1 本の規制は使う側が決めること」）。
    ここでは「既定ではない」ことを、対比で固定する。
    """
    pair, account = _setup_three(tmp_path, isolated_account_factory,
                                  min_interval_hours=6, quiet_hours=None)
    spy = Spy()
    _run_at(account["name"], TIMES[0], spy)
    _run_at(account["name"], TIMES[1], spy)
    assert spy.calls == ["1 本目。"], f"min_interval を入れたのに 2 本目が出た: {spy.calls}"


def test_出ていない指定はboardに出る(tmp_path, isolated_account_factory):
    """指定時刻を過ぎても出ていないものが黙って埋もれない（board に理由付きで出る）。"""
    from thth import report
    pair, account = _setup_three(tmp_path, isolated_account_factory,
                                  min_interval_hours=6, quiet_hours=None)
    spy = Spy()
    _run_at(account["name"], TIMES[0], spy)          # 9 時の 1 本目だけ出た
    # 13:30 の時点: 12 時の 1 本は 1 時間半おくれている（min_interval で止まっている）。
    row = next(r for r in report.board_summary(now=_at("2026-09-09T13:30:00+09:00"))["accounts"]
               if r["account"] == account["name"])
    files = {item["file"]: item["reason"] for item in row["needs_review"]}
    assert "2.md" in files, f"12 時に出るはずのものが board に出ない: {row}"
    assert files["2.md"] == "min_interval", f"理由が添えられていない: {files}"


def test_timerが止まっていれば理由不明のoverdueとして出る(tmp_path, isolated_account_factory):
    """実行そのものが無い（timer が止まっている・VM が落ちている）場合。

    select は何も落としていないので理由が無い。それでも「指定した時刻を過ぎても
    出ていない」ことは board に出る（`overdue`）。
    """
    from thth import report
    pair, account = _setup_three(tmp_path, isolated_account_factory)
    row = next(r for r in report.board_summary(now=_at("2026-09-09T11:00:00+09:00"))["accounts"]
               if r["account"] == account["name"])
    files = {item["file"]: item["reason"] for item in row["needs_review"]}
    # 9 時の 1 本は 2 時間おくれている。12 時・15 時のものはまだ先なので出ない。
    assert files == {"a.md": "overdue"}, files
