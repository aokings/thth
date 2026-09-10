"""数と返信の収集（T3・T4）— **経過時間で取る**（masaru 裁定 2026-09-10）。

> ABテストが出来たり、分析できたり、PDCAサイクルを回すってのも大事だよね。
> 時間は巻き戻せない。

表示回数は積み上がるので、投稿どうしで「いまの数字」を比べても意味がない。
比べていいのは**同じ経過時間の数字**。そして Threads は「読んだ時点の累計」しか
返さないので、**逃した経過時間は永久に復元できない**。
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

from tests.conftest import init_git_pair, make_queue_text, commit_and_push_path
from thth import collect as collect_mod
from thth import jst

NOW = datetime.datetime(2026, 9, 10, 12, 0, tzinfo=jst.JST)


class FakeAdapter:
    def __init__(self, *, views=100, replies_rows=None, fail=None):
        self.views = views
        self.replies_rows = replies_rows or []
        self.fail = fail or set()
        self.insight_calls = []
        self.reply_calls = []

    def insights(self, post_id):
        if "insights" in self.fail:
            raise RuntimeError("取れません")
        self.insight_calls.append(post_id)
        return {"views": self.views, "likes": 3, "replies": len(self.replies_rows)}

    def replies(self, post_id, *, since=None):
        if "replies" in self.fail:
            raise RuntimeError("取れません")
        self.reply_calls.append(post_id)
        return list(self.replies_rows)

    def account_insights(self, user_id, *, since, until):
        return {"views": 1000, "clicks": 12, "followers_count": 50}


def _setup(tmp_path, factory, *, posted_at, post_id="POST1"):
    pair = init_git_pair(tmp_path, seed_content=make_queue_text({
        "status": "posted", "post_id": post_id, "posted_at": posted_at}))
    account = factory(repo_dir=pair["work"], production=True)
    return pair, account


def _rows(repo, rel):
    path = os.path.join(repo, rel)
    if not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def test_刻みを跨いだら1行だけ足す(tmp_path, isolated_account_factory):
    """2 時間前の投稿は 1h の刻みを跨いでいる。6h はまだ。"""
    pair, account = _setup(tmp_path, isolated_account_factory,
                            posted_at="2026-09-10T10:00:00+09:00")
    adapter = FakeAdapter()
    collect_mod.run_collect(account["name"], adapter=adapter, now=NOW, log=lambda _l: None)

    rows = _rows(pair["work"], "data/sns/insights/posts/POST1.ndjson")
    assert len(rows) == 1, rows
    assert rows[0]["marks"] == [1], rows[0]
    assert rows[0]["metrics"]["views"] == 100
    assert rows[0]["age_hours"] == 2.0


def test_同じ刻みを二度書かない(tmp_path, isolated_account_factory):
    pair, account = _setup(tmp_path, isolated_account_factory,
                            posted_at="2026-09-10T10:00:00+09:00")
    adapter = FakeAdapter()
    for _ in range(3):
        collect_mod.run_collect(account["name"], adapter=adapter, now=NOW, log=lambda _l: None)
    assert len(_rows(pair["work"], "data/sns/insights/posts/POST1.ndjson")) == 1
    assert len(adapter.insight_calls) == 1, "同じ刻みで API を叩き直している"


def test_跨いだ刻みが複数なら全部を1行に記録する(tmp_path, isolated_account_factory):
    """timer が止まっていて 3 日ぶん飛んだ場合。**取り返せないものは取り返せない**が、
    どの刻みを満たしたか（と実際の経過時間）は残す。"""
    pair, account = _setup(tmp_path, isolated_account_factory,
                            posted_at="2026-09-06T12:00:00+09:00")  # 96 時間前
    collect_mod.run_collect(account["name"], adapter=FakeAdapter(), now=NOW,
                             log=lambda _l: None)
    rows = _rows(pair["work"], "data/sns/insights/posts/POST1.ndjson")
    assert rows[0]["marks"] == [1, 6, 24, 72]
    assert rows[0]["age_hours"] == 96.0, "実際の経過時間が残る（24h の値ではないと判る）"


def test_返信はidで重複除去して追記する(tmp_path, isolated_account_factory):
    pair, account = _setup(tmp_path, isolated_account_factory,
                            posted_at="2026-09-10T10:00:00+09:00")
    first = FakeAdapter(replies_rows=[{"id": "R1", "text": "はじめの返信"}])
    collect_mod.run_collect(account["name"], adapter=first, now=NOW, log=lambda _l: None)

    later = NOW + datetime.timedelta(hours=5)   # 6h の刻みを跨ぐ
    second = FakeAdapter(replies_rows=[{"id": "R1", "text": "はじめの返信"},
                                        {"id": "R2", "text": "あとの返信"}])
    collect_mod.run_collect(account["name"], adapter=second, now=later, log=lambda _l: None)

    rows = _rows(pair["work"], "data/sns/replies/POST1.ndjson")
    assert [r["id"] for r in rows] == ["R1", "R2"], rows
    assert rows[1]["text"] == "あとの返信"


def test_数が取れなくても返信は採る(tmp_path, isolated_account_factory):
    """採取は部分的な成功を許す（投稿と違って次の実行で埋まる）。"""
    pair, account = _setup(tmp_path, isolated_account_factory,
                            posted_at="2026-09-10T10:00:00+09:00")
    adapter = FakeAdapter(replies_rows=[{"id": "R1", "text": "返信"}], fail={"insights"})
    rc = collect_mod.run_collect(account["name"], adapter=adapter, now=NOW, log=lambda _l: None)

    assert rc == 1
    assert _rows(pair["work"], "data/sns/insights/posts/POST1.ndjson") == []
    assert len(_rows(pair["work"], "data/sns/replies/POST1.ndjson")) == 1


def test_アカウントの日次は前日を1行だけ(tmp_path, isolated_account_factory):
    pair, account = _setup(tmp_path, isolated_account_factory,
                            posted_at="2026-09-10T10:00:00+09:00")
    for _ in range(2):
        collect_mod.run_collect(account["name"], adapter=FakeAdapter(), now=NOW,
                                 log=lambda _l: None)
    rows = _rows(pair["work"], f"data/sns/insights/account/{account['name']}-2026-09.ndjson")
    assert len(rows) == 1
    assert rows[0]["date"] == "2026-09-09"          # 前日の閉じた 1 日
    assert rows[0]["metrics"]["clicks"] == 12       # clicks はここでしか取れない


def test_collect_daysを過ぎた投稿は採らない(tmp_path, isolated_account_factory):
    pair, account = _setup(tmp_path, isolated_account_factory,
                            posted_at="2026-08-01T10:00:00+09:00")  # 40 日前
    adapter = FakeAdapter()
    collect_mod.run_collect(account["name"], adapter=adapter, now=NOW, log=lambda _l: None)
    assert adapter.insight_calls == []


def test_採ったものはcommitしてpushされる(tmp_path, isolated_account_factory):
    """利用者 repo に残らなければ、後から分析できない。"""
    import subprocess
    pair, account = _setup(tmp_path, isolated_account_factory,
                            posted_at="2026-09-10T10:00:00+09:00")
    collect_mod.run_collect(account["name"], adapter=FakeAdapter(), now=NOW,
                             log=lambda _l: None)
    in_origin = subprocess.run(["git", "-C", pair["bare"], "ls-tree", "-r", "--name-only", "main"],
                                capture_output=True, text=True).stdout
    assert "data/sns/insights/posts/POST1.ndjson" in in_origin, in_origin
