"""絡みの台帳（`data/sns/engagements/`）を採取の commit に含める（T9-1・
照合「X Developer Agreement と自分の泉」検収 1）。

**事実**: 投稿の commit（`core.py` の `writeback.commit_and_push(rel_path=原稿)`）は
原稿ファイルだけ、採取の commit（`collect.py` の `result["touched"]`）は採取で
触ったファイルだけを add する。同席送信（`core._send_locked`）は commit しない。
`thth/engagements.py::append()` が書く `data/sns/engagements/<YYYY-MM>.ndjson` は
どこからも add されず、VM の repo に未追跡のまま溜まっていた。

固定するのは 4 つ（発注 T9-1 の (a)〜(d)）:

  (a) `engagements.append` で 1 行書いた repo で `collect` を 1 回 → origin に
      `data/sns/engagements/<月>.ndjson` が push されている。
  (b) 採取で触るものが 0 でも (a) と同じ。
  (c) repo が無い account では git を呼ばない。
  (d) 何も変わっていなければ空の commit を作らない。
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess

from tests.conftest import init_git_pair, make_queue_text
from tests.test_collect import FakeAdapter
from thth import accounts as accounts_mod
from thth import collect as collect_mod
from thth import engagements as engagements_mod
from thth import jst

NOW = datetime.datetime(2026, 9, 16, 12, 0, tzinfo=jst.JST)


class NoOpAdapter:
    """能力を何も名乗らない偽アダプタ（`collect_once()` に何も触らせない）。

    `tests/test_collect_sent.py` の `FakeAdapter` と違い `views` すら持たない
    ——**採取が触るものを 0 にする**ためだけの道具。
    """

    CAPABILITIES = frozenset()

    @classmethod
    def capabilities(cls):
        return set(cls.CAPABILITIES)

    def insights(self, post_id):
        raise AssertionError("能力を名乗っていないのに insights を呼んだ")

    def conversation(self, post_id, *, since=None):
        raise AssertionError("能力を名乗っていないのに conversation を呼んだ")

    def account_insights(self, user_id, *, since, until):
        raise AssertionError("能力を名乗っていないのに account_insights を呼んだ")


def _engagement_row(**over):
    row = {
        "schema": engagements_mod.SCHEMA,
        "post_id": "111",
        "reply_to": "222",
        "root_post": None,
        "author_key": None,
        "account": None,  # append() 側の既定に任せる
        "medium": "threads",
        "topic": None,
        "form": None,
        "hour_band": "朝",
        "posted_at": "2026-09-16T08:00:00+09:00",
        "found_by": None,
    }
    row.update(over)
    return row


def _in_origin(bare: str) -> str:
    return subprocess.run(
        ["git", "-C", bare, "ls-tree", "-r", "--name-only", "main"],
        capture_output=True, text=True,
    ).stdout


def test_a_絡みの台帳が採取のcommitでpushされる(tmp_path, isolated_account_factory):
    """採取が投稿本体も触る、ふつうの回。絡みの台帳も同じ commit に乗って
    origin まで届く。"""
    pair = init_git_pair(tmp_path, seed_content=make_queue_text({
        "status": "posted", "post_id": "POST1",
        "posted_at": "2026-09-16T10:00:00+09:00"}))  # 2 時間前・1h の刻みを跨ぐ
    account = isolated_account_factory(repo_dir=pair["work"], production=True)

    cfg = accounts_mod.load_account(account["name"])
    engagements_mod.append(cfg, account["name"],
                            _engagement_row(account=account["name"]), now=NOW)

    rc = collect_mod.run_collect(account["name"], adapter=FakeAdapter(), now=NOW,
                                  log=lambda _l: None)

    assert rc == 0
    in_origin = _in_origin(pair["bare"])
    assert "data/sns/engagements/2026-09.ndjson" in in_origin, in_origin
    # ついでに、投稿の実測も同じ commit の対象として届いていること
    # （足した処理が既存の touched を壊していないこと）。
    assert "data/sns/insights/posts/POST1.ndjson" in in_origin, in_origin


def test_b_採取で触るものが0でも絡みの台帳はpushされる(tmp_path, isolated_account_factory):
    """`collect_once()` の `touched` が空でも、絡みの台帳の未追跡・変更だけで
    commit する。"""
    pair = init_git_pair(tmp_path, seed_content=make_queue_text({
        "status": "posted", "post_id": "POST1",
        # collect_days（既定 14 日）をとうに過ぎている → posts_seen は 0、
        # touched も空のまま（NoOpAdapter は何も能力を名乗らないので
        # アカウント日次も呼ばれない）。
        "posted_at": "2026-08-01T10:00:00+09:00"}))
    account = isolated_account_factory(repo_dir=pair["work"], production=True)

    cfg = accounts_mod.load_account(account["name"])
    engagements_mod.append(cfg, account["name"],
                            _engagement_row(account=account["name"]), now=NOW)

    rc = collect_mod.run_collect(account["name"], adapter=NoOpAdapter(), now=NOW,
                                  log=lambda _l: None)

    assert rc == 0
    in_origin = _in_origin(pair["bare"])
    assert "data/sns/engagements/2026-09.ndjson" in in_origin, in_origin
    # 投稿側は本当に何も触っていないこと（この test が (a) の焼き直しに
    # なっていないことの確認）。
    assert "data/sns/insights/posts/POST1.ndjson" not in in_origin, in_origin


def test_c_repoが無いaccountでは絡みの台帳も含めてgitを呼ばない(
        tmp_path, isolated_account_factory, monkeypatch):
    """`repo_dir` が実在しない台帳（`repos/_none` 相当）では、絡みの台帳の
    判定そのもの（`_pending_engagement_paths`）を呼ばない——呼べば
    `git status` を repo の外で走らせる危険がある。"""
    account = isolated_account_factory(
        repo_dir=str(tmp_path / "repos" / "_none"), production=True, scheduled=False)

    def 呼ぶな(*a, **k):
        raise AssertionError("repo が無いのに絡みの台帳の git 判定を呼んだ")

    monkeypatch.setattr(collect_mod, "_pending_engagement_paths", 呼ぶな)
    monkeypatch.setattr(collect_mod, "_git", 呼ぶな)
    monkeypatch.setattr(collect_mod.writeback, "sync_repo", 呼ぶな)
    monkeypatch.setattr(collect_mod.writeback, "commit_and_push", 呼ぶな)

    rc = collect_mod.run_collect(account["name"], adapter=NoOpAdapter(), now=NOW,
                                  log=lambda _l: None)
    assert rc == 0


def test_d_変わっていなければ空のcommitを作らない(tmp_path, isolated_account_factory,
                                                    monkeypatch):
    """絡みの台帳に変更が無い回は、`commit_and_push` を一切呼ばない。"""
    pair = init_git_pair(tmp_path, seed_content=make_queue_text({
        "status": "posted", "post_id": "POST1",
        "posted_at": "2026-08-01T10:00:00+09:00"}))  # collect_days を過ぎている
    account = isolated_account_factory(repo_dir=pair["work"], production=True)

    calls = []
    real_commit_and_push = collect_mod.writeback.commit_and_push

    def 数える(*a, **k):
        calls.append((a, k))
        return real_commit_and_push(*a, **k)

    monkeypatch.setattr(collect_mod.writeback, "commit_and_push", 数える)

    rc = collect_mod.run_collect(account["name"], adapter=NoOpAdapter(), now=NOW,
                                  log=lambda _l: None)

    assert rc == 0
    assert calls == [], "変更が無いのに commit_and_push を呼んだ"
