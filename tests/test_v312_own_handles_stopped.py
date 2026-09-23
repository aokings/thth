"""3.1.2 件 7: 退出の途中（停止中）の台帳が project 全体の未回答を潰さない。

実測（2026-09-23 12:55）: kopicha-server-mastodon が `leave` の途中（`stopped`）になった後、
`thth unanswered kopicha-threads` が 0 件になった（朝は 6 件）。`replies._own_handles()` が
停止中の台帳を `AccountStopped` で unreadable に落とし、handle の集合が不完全になって
すべての返信の `own` が None になっていた。

見るのは:
  - 3 台帳の project で 1 つを停止にしても、`unanswered` の n が減らず、`cannot_say` に
    `reply_ownership_unknown` が出ない（`who`・`morning` の 1 段も同じ関数を通る）。
  - 停止中の台帳の handle も「自分」として数える（その handle の返信は他者にしない）。
  - 本当に壊れた台帳（JSON でない）は従前どおり unreadable。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from thth import accounts, leave_gate, morning, replies, server_files, unanswered
from tests.test_v311_replies_owned import write_replies

POST = "18000000000000001"
NAMES = {"kopicha-threads": "threads", "kopicha-bluesky": "bluesky", "kopicha-mastodon": "mastodon"}


def stop(account):
    """退出の最初の段（`stopped`）と同じ停止の印だけを置く（tests/test_v212_leave_gate.py と同じ形）。"""
    with leave_gate.lease(account, exclusive=True):
        with server_files.directory(leave_gate.location(), create=True, private=True) as fd:
            server_files.replace_at(fd, account + ".json",
                                    server_files.encode({"account": account, "phase": "stopped"}),
                                    private=True)


@pytest.fixture
def project(isolated_account_factory):
    infos = {name: isolated_account_factory(name, project="kopicha", media=medium,
                                            handle="kopi_" + medium, instance="https://mastodon.social")
             for name, medium in NAMES.items()}
    info = infos["kopicha-threads"]
    queue = Path(info["queue_dir"])
    queue.mkdir(parents=True, exist_ok=True)
    (queue / "t.md").write_text(
        "---\nthth: 1\naccount: kopicha-threads\nstatus: posted\npublish_at: 2026-09-08T09:00:00+09:00\n"
        f"post_id: {POST}\nposted_at: 2026-09-08T09:00:00+09:00\nreply_to:\n---\n\n本文\n",
        encoding="utf-8")
    write_replies(Path(info["repo_dir"]) / "data" / "sns" / "replies", POST, [
        {"kind": "reply", "id": f"R{n}", "username": f"person{n}", "post_id": POST,
         "timestamp": "2026-09-08T10:00:00+09:00", "collected_at": "2026-09-08T11:00:00+09:00",
         "text": "他人の返信"} for n in range(3)] + [
        # 停止中になる account（mastodon）の handle からの返信は「自分」——未回答に数えない。
        {"kind": "reply", "id": "OWN", "username": "kopi_mastodon", "post_id": POST,
         "timestamp": "2026-09-08T10:30:00+09:00", "collected_at": "2026-09-08T11:00:00+09:00",
         "text": "自分の別口座"}])
    return infos


def _unanswered():
    return unanswered.answer("kopicha-threads", since="7d")


def test_停止中の台帳があっても未回答が減らない(project):
    before = _unanswered()
    assert before["n_total"] == 3 and "reply_ownership_unknown" not in before["cannot_say"]
    stop("kopicha-mastodon")
    with pytest.raises(accounts.AccountStopped):
        accounts.load_account("kopicha-mastodon")
    after = _unanswered()
    assert after["n_total"] == 3, after["cannot_say"]
    assert "reply_ownership_unknown" not in after["cannot_say"]
    assert "unproven_roots_excluded" not in after["cannot_say"]
    assert sorted(row["reply_id"] for row in after["replies"]) == ["R0", "R1", "R2"]


def test_停止中の台帳のhandleも自分として集める(project):
    stop("kopicha-mastodon")
    handles, unreadable = replies._own_handles()
    assert "kopi_mastodon" in handles and unreadable == []
    # 停止は戻っていない（読んだだけ）。
    assert leave_gate.stopped("kopicha-mastodon")


def test_壊れた台帳は従前どおりunreadable(project):
    path = Path(project["kopicha-bluesky"]["accounts_dir"]) / "kopicha-bluesky.json"
    path.write_text("{ not json", encoding="utf-8")
    handles, unreadable = replies._own_handles()
    assert unreadable == ["kopicha-bluesky"] and "kopi_bluesky" not in handles
    assert "reply_ownership_unknown" in _unanswered()["cannot_say"]


def test_morningの1段も停止中の台帳で潰れない(project, monkeypatch):
    monkeypatch.setattr(morning, "_world", lambda *a, **k: morning.cell(cannot_say="no_watch_words"))
    monkeypatch.setattr(morning, "_mentions_rows", lambda *a, **k: morning.cell(cannot_say="not_supported"))
    stop("kopicha-mastodon")
    payload = morning.build("kopicha-threads", mark=False)
    section = next(s for s in payload["sections"] if s["section"] == "unanswered")
    rows = section["value"]["by_account"]["kopicha-threads"]["value"]["replies"]["value"]
    assert rows["n"] == 3 and "reply_ownership_unknown" not in json.dumps(rows["cannot_say"])



def test_whoも停止中の台帳で相手の返信を見失わない(project):
    from thth import who_cli
    from thth.adapters import base
    key = base.author_key("threads", "person1")
    before = who_cli.answer(account_name="kopicha-threads", author_key=key)
    stop("kopicha-mastodon")
    after = who_cli.answer(account_name="kopicha-threads", author_key=key)
    assert before["met"] == after["met"] == 1 and after["first"] == "2026-09-08T10:00:00+09:00"
    assert after["cannot_say"] == []
