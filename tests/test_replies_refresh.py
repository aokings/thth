"""`thth replies --refresh`（masaru 指示 2026-09-12・外部レビュー B）。

**刻みを待たずに取り直す。** ただし**刻みは進めない**——臨時の取得を「24h の数」に
化けさせない。`insights` も取らない。
"""
from __future__ import annotations

import json
import os

import pytest

from tests.conftest import init_git_pair, write_queue_file
from thth import collect as collect_mod, jst

NOW = __import__("datetime").datetime(2026, 9, 12, 12, 0, 0,
                                       tzinfo=jst.JST)
POSTED = "2026-09-12T11:30:00+09:00"       # 0.5h 前＝**どの刻みにも当たらない**


class _口:
    """会話だけを返す偽アダプタ。**insights を呼べば落ちる。**"""

    def __init__(self, rows, fail=False):
        self.rows, self.fail = rows, fail
        self.conversation_calls, self.insight_calls = [], []

    def conversation(self, post_id, **kw):
        self.conversation_calls.append(post_id)
        if self.fail:
            raise RuntimeError("取れません")
        return list(self.rows)

    def insights(self, post_id, **kw):
        self.insight_calls.append(post_id)
        raise AssertionError("**refresh が insights を呼んでいる**")

    def account_insights(self, *a, **kw):
        raise AssertionError("**refresh が account_insights を呼んでいる**")


def _仕立て(tmp_path, isolated_account_factory, *, posted_at=POSTED):
    pair = init_git_pair(tmp_path, seed_content="x\n", seed_name="seed.md")
    account = isolated_account_factory(repo_dir=pair["work"])
    write_queue_file(account["queue_dir"], "a.md", fm_overrides={
        "status": "posted", "post_id": "POST1", "posted_at": posted_at})
    return pair, account


def _台帳(pair, post_id="POST1"):
    path = os.path.join(pair["work"], "data", "sns", "replies", f"{post_id}.ndjson")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_刻みに当たらなくても取り直せる(tmp_path, isolated_account_factory):
    """**これが `--refresh` の目的。** 定期取得は 0.5h では何も取らない。"""
    pair, account = _仕立て(tmp_path, isolated_account_factory)
    口 = _口([{"id": "R1", "text": "返信"}])

    定期 = collect_mod.collect_once(account["name"], adapter=口, now=NOW,
                                     log=lambda _l: None)
    assert 口.conversation_calls == [], "刻みに当たらないのに取りに行っている"

    out = collect_mod.refresh_replies(account["name"], adapter=口, now=NOW,
                                       log=lambda _l: None)
    assert out["requested"] == 1 and out["fetched"] == 1
    assert out["new_replies"] == 1
    assert [r["id"] for r in _台帳(pair) if r["kind"] == "reply"] == ["R1"]


def test_刻みを進めない(tmp_path, isolated_account_factory):
    """**臨時の取得を「24h の数」に化けさせない。**"""
    pair, account = _仕立て(tmp_path, isolated_account_factory)
    口 = _口([{"id": "R1"}])
    collect_mod.refresh_replies(account["name"], adapter=口, now=NOW,
                                 log=lambda _l: None)
    fetch = [r for r in _台帳(pair) if r["kind"] == "fetch"]
    assert fetch and fetch[-1]["marks"] == [], "**刻みを進めている**"
    assert fetch[-1]["trigger"] == "refresh"


def test_insightsを呼ばない(tmp_path, isolated_account_factory):
    pair, account = _仕立て(tmp_path, isolated_account_factory)
    口 = _口([{"id": "R1"}])
    collect_mod.refresh_replies(account["name"], adapter=口, now=NOW,
                                 log=lambda _l: None)
    assert 口.insight_calls == []


def test_二度目は新しい返信が0件(tmp_path, isolated_account_factory):
    pair, account = _仕立て(tmp_path, isolated_account_factory)
    口 = _口([{"id": "R1"}, {"id": "R2"}])
    collect_mod.refresh_replies(account["name"], adapter=口, now=NOW,
                                 log=lambda _l: None)
    out = collect_mod.refresh_replies(account["name"], adapter=口, now=NOW,
                                       log=lambda _l: None)
    assert out["fetched"] == 1 and out["new_replies"] == 0
    ids = [r["id"] for r in _台帳(pair) if r["kind"] == "reply"]
    assert ids == ["R1", "R2"], f"**重複している**: {ids}"


def test_取れなかった投稿は失敗として残る(tmp_path, isolated_account_factory):
    pair, account = _仕立て(tmp_path, isolated_account_factory)
    out = collect_mod.refresh_replies(account["name"], adapter=_口([], fail=True),
                                       now=NOW, log=lambda _l: None)
    assert out["fetched"] == 0 and out["failed"], out
    assert out["failed"][0]["post_id"] == "POST1"
    assert _台帳(pair) == [], "**取れていないのに台帳へ書いている**"


def test_対象外のpostは別の投稿で代用しない(tmp_path, isolated_account_factory):
    pair, account = _仕立て(tmp_path, isolated_account_factory)
    口 = _口([{"id": "R1"}])
    out = collect_mod.refresh_replies(account["name"], adapter=口, now=NOW,
                                       post_id="知らない投稿", log=lambda _l: None)
    assert out["skipped"] == "out_of_scope"
    assert 口.conversation_calls == [], "**対象外なのに取りに行っている**"


def test_期間外の投稿は対象にしない(tmp_path, isolated_account_factory):
    pair, account = _仕立て(tmp_path, isolated_account_factory,
                             posted_at="2026-08-01T10:00:00+09:00")   # 14 日超
    口 = _口([{"id": "R1"}])
    out = collect_mod.refresh_replies(account["name"], adapter=口, now=NOW,
                                       log=lambda _l: None)
    assert out["requested"] == 0 and 口.conversation_calls == []


def test_idの無い行を成功件数に含めない(tmp_path, isolated_account_factory):
    pair, account = _仕立て(tmp_path, isolated_account_factory)
    口 = _口([{"id": "R1"}, {"text": "id が無い"}])
    out = collect_mod.refresh_replies(account["name"], adapter=口, now=NOW,
                                       log=lambda _l: None)
    assert out["new_replies"] == 1
    fetch = [r for r in _台帳(pair) if r["kind"] == "fetch"][-1]
    assert fetch["id_missing"] == 1, "**id 欠落を黙って捨てている**"


def test_APIの行が台帳の管理項目を上書きしない(tmp_path, isolated_account_factory):
    """**`kind`・`post_id`・`collected_at` は台帳のもの。**"""
    pair, account = _仕立て(tmp_path, isolated_account_factory)
    口 = _口([{"id": "R1", "kind": "にせもの", "post_id": "よその投稿",
                "collected_at": "1999-01-01T00:00:00+09:00"}])
    collect_mod.refresh_replies(account["name"], adapter=口, now=NOW,
                                 log=lambda _l: None)
    行 = [r for r in _台帳(pair) if r.get("id") == "R1"][0]
    assert 行["kind"] == "reply" and 行["post_id"] == "POST1"
    assert 行["collected_at"].startswith("2026-09-12")


def test_ロックが取れなければ見送る(tmp_path, isolated_account_factory, monkeypatch):
    """**待たない。** 投稿を塞ぐより見送る。"""
    from thth import lock as lock_mod
    pair, account = _仕立て(tmp_path, isolated_account_factory)
    monkeypatch.setattr(lock_mod.AccountLock, "acquire",
                         lambda self: (_ for _ in ()).throw(lock_mod.LockBusy("ふさがっている")))
    口 = _口([{"id": "R1"}])
    out = collect_mod.refresh_replies(account["name"], adapter=口, now=NOW,
                                       log=lambda _l: None)
    assert out["skipped"] == "locked" and 口.conversation_calls == []

def test_壊れた台帳には追記しない(tmp_path, isolated_account_factory):
    """**何が入っていたか分からないまま上に積まない**（外部レビュー B）。"""
    pair, account = _仕立て(tmp_path, isolated_account_factory)
    path = os.path.join(pair["work"], "data", "sns", "replies", "POST1.ndjson")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"kind": "reply", "id": "R0"}\n壊れた行\n')

    口 = _口([{"id": "R1"}])
    out = collect_mod.refresh_replies(account["name"], adapter=口, now=NOW,
                                       log=lambda _l: None)
    assert out["failed"] and "壊れて" in out["failed"][0]["reason"]
    assert 口.conversation_calls == [], "**壊れているのに取りに行っている**"
    with open(path, encoding="utf-8") as f:
        assert f.read().count("\n") == 2, "**壊れた台帳に追記している**"


def test_refreshを付けなければ何も取りに行かない(tmp_path, isolated_account_factory,
                                                  capsys, monkeypatch):
    """**既定の `thth replies` は今までどおり台帳を読むだけ。**"""
    import argparse
    from thth import cli as cli_mod
    pair, account = _仕立て(tmp_path, isolated_account_factory)
    呼ばれた = []
    monkeypatch.setattr(cli_mod.collect_mod, "refresh_replies",
                         lambda *a, **k: 呼ばれた.append(1))
    cli_mod.cmd_replies(argparse.Namespace(account=account["name"], post=None,
                                            json=False, refresh=False))
    assert 呼ばれた == [], "**`--refresh` が無いのに取りに行っている**"


def test_全部取れたときだけ0で終わる():
    from thth import cli as cli_mod
    良い = {"skipped": None, "failed": [], "errors": [], "remote": "synced"}
    assert cli_mod._refresh_rc(良い) == 0
    for 駄目 in ({**良い, "skipped": "locked"},
                 {**良い, "failed": [{"post_id": "P", "reason": "x"}]},
                 {**良い, "errors": ["何か"]},
                 {**良い, "remote": "not_synced"}):
        assert cli_mod._refresh_rc(駄目) == 1, 駄目
