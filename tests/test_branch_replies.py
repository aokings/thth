"""絡みに行った先（他人の根）で、こちらの返信に付いた返事を採って数える（要望 r20261007-4b57e1ee）。

Threads の `/conversation` は根の投稿でしか枝を返さない。自分の返信に向けると 0 件で、
返事が台帳にも `unanswered` にも出なかった（10/7 実測: `/conversation` 0 件・`/replies` 1 件）。
"""
from __future__ import annotations

import datetime
import json
import os

import pytest

from thth import accounts as accounts_mod
from thth import collect as collect_mod
from thth import jst
from thth import measured as measured_mod
from thth import postid
from thth import sent as sent_mod
from thth import unanswered

NOW = datetime.datetime(2026, 10, 7, 12, 0, tzinfo=jst.JST)
SENT_AT = "2026-10-07T10:00:00+09:00"
AT = "2026-10-07T10:30:00+09:00"


def row(rid, parent, root, username="neighbor"):
    return {"id": rid, "username": username, "text": "質問です", "timestamp": AT,
            "replied_to": {"id": parent}, "root_post": {"id": root}}


class FakeThreads:
    CAPABILITIES = frozenset({"views"})

    @classmethod
    def capabilities(cls):
        return set(cls.CAPABILITIES)

    def __init__(self, branch_rows):
        self.branch_rows = branch_rows
        self.calls = []

    def insights(self, post_id):
        return {"metrics": {"views": 1}, "available": list(measured_mod.POST_METRIC_NAMES)}

    def conversation(self, post_id, *, since=None):
        self.calls.append(("conversation", post_id))
        return []

    def branch_replies(self, post_id):
        self.calls.append(("branch", post_id))
        return list(self.branch_rows.get(post_id, []))


def account(tmp_path, factory):
    return factory(name="kopi-threads", repo_dir=str(tmp_path / "repos" / "_none"),
                   media="threads", handle="kopi", production=True, scheduled=False)


def sent(name, pid, reply_to=None):
    sent_mod.write(accounts_mod.state_dir_for(name), post_id=pid, text="本文",
                   body_hash="h", sent_at=SENT_AT, reply_to=reply_to)


def test_reply_on_foreign_root_is_collected_with_branch_replies(tmp_path, isolated_account_factory):
    a = account(tmp_path, isolated_account_factory)
    sent(a["name"], "ROOT")                       # 自分の根
    sent(a["name"], "MINE", reply_to="FOREIGN")   # 他人の根に付けた返信
    sent(a["name"], "INNER", reply_to="X1")       # 自分の根のスレッドの中の返信
    adapter = FakeThreads({
        "MINE": [row("THEIRS", "MINE", "FOREIGN")],
        # 自分の根の中の返事は根の会話で採れているので、枝の台帳には入れない。
        "INNER": [row("DUP", "INNER", "ROOT")],
    })
    assert collect_mod.run_collect(a["name"], adapter=adapter, now=NOW, log=lambda _l: None) == 0
    assert ("conversation", "ROOT") in adapter.calls
    assert ("branch", "MINE") in adapter.calls and ("conversation", "MINE") not in adapter.calls

    result = unanswered.answer(a["name"], now=NOW)
    assert [r["reply_id"] for r in result["replies"]] == ["THEIRS"]
    assert result["replies"][0]["post_id"] == "MINE"


def test_branch_counts_only_direct_replies_and_answered_ones_drop(tmp_path, isolated_account_factory):
    a = account(tmp_path, isolated_account_factory)
    sent(a["name"], "MINE", reply_to="FOREIGN")
    adapter = FakeThreads({"MINE": [row("THEIRS", "MINE", "FOREIGN"),
                                    row("ELSE", "SOMEONE", "FOREIGN")]})
    collect_mod.run_collect(a["name"], adapter=adapter, now=NOW, log=lambda _l: None)
    assert [r["reply_id"] for r in unanswered.answer(a["name"], now=NOW)["replies"]] == ["THEIRS"]
    sent(a["name"], "ANSWER", reply_to="THEIRS")
    assert unanswered.answer(a["name"], now=NOW)["replies"] == []


def test_refresh_uses_branch_replies_for_own_replies(tmp_path, isolated_account_factory):
    a = account(tmp_path, isolated_account_factory)
    sent(a["name"], "MINE", reply_to="FOREIGN")
    adapter = FakeThreads({"MINE": [row("THEIRS", "MINE", "FOREIGN")]})
    out = collect_mod.refresh_replies(a["name"], adapter=adapter, now=NOW, log=lambda _l: None)
    assert out["new_replies"] == 1 and adapter.calls == [("branch", "MINE")]


# ---- 3: Threads のリンクから id を引く（台帳の permalink と突き合わせる）


def ledger_row(a, rid, permalink):
    directory = accounts_mod.data_dirs(accounts_mod.load_account(a["name"]), a["name"])["replies"]
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "MINE.ndjson"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"id": rid, "post_id": "MINE", "permalink": permalink}) + "\n")


def test_threads_permalink_resolves_from_reply_ledger(tmp_path, isolated_account_factory):
    a = account(tmp_path, isolated_account_factory)
    ledger_row(a, "18154056961460639", "https://www.threads.com/@neighbor/post/DdAbC123xyz")
    cfg = accounts_mod.load_account(a["name"])
    for url in ("https://www.threads.com/@neighbor/post/DdAbC123xyz",
                "https://www.threads.net/@neighbor/post/DdAbC123xyz?xmt=AQF0",
                "https://threads.com/@neighbor/post/DdAbC123xyz/"):
        assert postid.for_account(cfg, url, a["name"]) == "18154056961460639"
    assert postid.for_account(cfg, "18154056961460639", a["name"]) == "18154056961460639"


@pytest.mark.parametrize("url,code", [
    ("https://www.threads.com/@neighbor/post/DdNotThere1", "post_url_not_in_ledger"),
    ("https://www.threads.com/share/abc", "share_url_unsupported"),
    ("https://example.com/@neighbor/post/DdAbC123xyz", "invalid_post_url"),
])
def test_threads_url_refusals_are_named(tmp_path, isolated_account_factory, url, code):
    a = account(tmp_path, isolated_account_factory)
    with pytest.raises(postid.PostIdError, match=code):
        postid.for_account(accounts_mod.load_account(a["name"]), url, a["name"])
