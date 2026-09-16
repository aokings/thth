"""`thth send`（同席の様態・設計 §3.7）。queue を通さずその場で 1 本出す経路。

不在の様態（timer→throw）との違いは queue と承認だけで、**守りは同じものを通る**
ことを確かめる: fail-closed（台帳 production: true が無ければ dry-run）・ロック・
inflight・切り詰めない・**digest による確認**（外部レビュー §1b・受け入れ 6）。
"""
from __future__ import annotations

import os

from thth import accounts as accounts_mod
from thth import approval as approval_mod
from thth import core
from thth import engagements as engagements_mod
from thth import inflight as inflight_mod
from thth import runs as runs_mod
from thth.adapters import base as adapter_base


def _digest_for(account_name: str, text: str, *, reply_to=None, topic=None) -> str:
    from thth import queuefile
    body = (text or "").strip()
    topic_value = queuefile.normalize_topic(topic)
    return approval_mod.compute_send_digest(
        text=body, account=account_name, reply_to=reply_to, topic=topic_value)


def test_送るのは渡された本文そのもの_整形も切り詰めもしない(isolated_account_factory):
    account = isolated_account_factory(production=True)
    sent = {}

    class _Spy:
        def publish(self, post, *, dry_run, on_container_created=None):
            sent["text"] = post.text
            sent["topic"] = post.topic
            from thth.adapters import base
            return base.PublishResult(post_id="P1", url=None, ts="2026-09-09T00:00:00+09:00")

    body = "  前後に空白のある本文。\n"
    digest = _digest_for(account["name"], body, topic="#苦味")
    r = core.send_once(account["name"], text=body, topic="#苦味",
                       production_flag=True, confirm=digest,
                       adapter_factory=lambda *_: _Spy())
    assert r.exit_code == 0 and r.post_id == "P1"
    # strip だけはする（末尾改行を数に含めない規約・§4.1）が、中身は変えない。
    assert sent["text"] == "前後に空白のある本文。"
    assert sent["topic"] == "苦味"   # 先頭の # は落ちる


def test_dryrunはdigestを表示する(isolated_account_factory):
    account = isolated_account_factory(production=True)
    lines = []
    r = core.send_once(account["name"], text="本文です",
                       production_flag=False, log=lines.append)
    assert r.exit_code == 0 and r.action == "skip"
    assert r.digest is not None and len(r.digest) == 12
    assert any(f"digest: {r.digest}" in line for line in lines)


def test_confirm無しのproductionは拒否する(isolated_account_factory):
    account = isolated_account_factory(production=True)

    class _Spy:
        def publish(self, *a, **k):
            raise AssertionError("confirm 無しなのに publish を呼んだ")

    r = core.send_once(account["name"], text="本文です", production_flag=True,
                       adapter_factory=lambda *_: _Spy())
    assert r.exit_code == 1 and r.action == "skip"
    assert r.digest is not None


def test_違うdigestのproductionは拒否する(isolated_account_factory):
    account = isolated_account_factory(production=True)

    class _Spy:
        def publish(self, *a, **k):
            raise AssertionError("digest が違うのに publish を呼んだ")

    r = core.send_once(account["name"], text="本文です", production_flag=True,
                       confirm="000000000000", adapter_factory=lambda *_: _Spy())
    assert r.exit_code == 1 and r.action == "skip"


def test_正しいdigestのproductionは送る(isolated_account_factory):
    account = isolated_account_factory(production=True)

    class _Spy:
        def publish(self, post, *, dry_run, on_container_created=None):
            from thth.adapters import base
            return base.PublishResult(post_id="P9", url=None, ts="2026-09-09T00:00:00+09:00")

    digest = _digest_for(account["name"], "本文です")
    r = core.send_once(account["name"], text="本文です", production_flag=True,
                       confirm=digest, adapter_factory=lambda *_: _Spy())
    assert r.exit_code == 0 and r.post_id == "P9"


def test_台帳がproductionでなければ本文を出さない(isolated_account_factory):
    account = isolated_account_factory(production=False)
    called = {"n": 0}

    class _Spy:
        def publish(self, *a, **k):
            called["n"] += 1
            raise AssertionError("dry-run なのに publish を呼んだ")

    lines = []
    r = core.send_once(account["name"], text="出てはいけない本文",
                       production_flag=True, adapter_factory=lambda *_: _Spy(),
                       log=lines.append)
    assert r.exit_code == 0 and r.action == "skip"
    assert called["n"] == 0
    assert "mode: rehearsal" in lines[0]


def test_長すぎる本文は切り詰めずに落とす(isolated_account_factory):
    account = isolated_account_factory(production=True)

    class _Spy:
        def publish(self, *a, **k):
            raise AssertionError("長すぎるのに publish を呼んだ")

    r = core.send_once(account["name"], text="あ" * 501,
                       production_flag=True, adapter_factory=lambda *_: _Spy())
    assert r.exit_code == 1 and r.action == "skip"


def test_inflightが残っていれば何もしない(isolated_account_factory):
    account = isolated_account_factory(production=True)
    state_dir = accounts_mod.state_dir_for(account["name"])
    inflight_mod.write(state_dir, file="(前回の残骸)", started="2026-09-09T00:00:00+09:00",
                       container_id=None)

    class _Spy:
        def publish(self, *a, **k):
            raise AssertionError("inflight が残っているのに publish を呼んだ")

    r = core.send_once(account["name"], text="本文", production_flag=True,
                       adapter_factory=lambda *_: _Spy())
    assert r.exit_code == 1 and r.action == "inflight"


# --- T7-2: `send --reply-to` も絡みの台帳に 1 行書く（設計「自分の泉」§4） --------
# kopicha の実物の所見: `_throw_chosen()`（queue の門）は絡みの台帳に書くが、
# 同席の様態（`send_once()`）には配線が無く、`thth send --reply-to <id>` で
# 出した返信は台帳に残らなかった。


class _SpyWithFetch:
    """`publish()` に加えて `fetch_post()` を持つ偽アダプタ（T7-2）。

    `reply_to_author_key` を渡さないときの best-effort 経路を確かめる。
    """

    def __init__(self, *, fetch_row=None, fetch_error=None):
        self.fetch_calls: list = []
        self._fetch_row = fetch_row
        self._fetch_error = fetch_error

    def publish(self, post, *, dry_run, on_container_created=None):
        return adapter_base.PublishResult(
            post_id="ENG1", url=None, ts="2026-09-16T10:00:00+09:00")

    def fetch_post(self, post_id):
        self.fetch_calls.append(post_id)
        if self._fetch_error is not None:
            raise self._fetch_error
        return self._fetch_row


def test_replyToがあれば絡みの台帳に1行書く_author_keyは明示した値を使う(isolated_account_factory):
    account = isolated_account_factory(production=True)
    cfg = accounts_mod.load_account(account["name"])
    spy = _SpyWithFetch()
    digest = _digest_for(account["name"], "返信です", reply_to="R1")
    r = core.send_once(
        account["name"], text="返信です", reply_to="R1", reply_to_root="ROOT1",
        reply_to_author_key="a" * 16, found_by="manual",
        production_flag=True, confirm=digest, adapter_factory=lambda *_: spy)
    assert r.exit_code == 0 and r.post_id == "ENG1"

    # 明示した author_key があるので fetch_post は best-effort でも叩かない。
    assert spy.fetch_calls == []

    rows = engagements_mod.records(cfg, account["name"])
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["post_id"] == "ENG1" and row["reply_to"] == "R1"
    assert row["root_post"] == "ROOT1"
    assert row["author_key"] == "a" * 16
    assert row["found_by"] == "manual"
    assert row["account"] == account["name"] and row["medium"] == "threads"


def test_reply_to_author_keyが無ければfetch_postでbesteffortに埋める(isolated_account_factory):
    account = isolated_account_factory(production=True)
    cfg = accounts_mod.load_account(account["name"])
    expected_key = adapter_base.author_key("threads", "someone")
    spy = _SpyWithFetch(fetch_row={"message_id": "R1", "username": "someone",
                                   "author_key": expected_key})
    digest = _digest_for(account["name"], "返信です", reply_to="R1")
    r = core.send_once(
        account["name"], text="返信です", reply_to="R1",
        production_flag=True, confirm=digest, adapter_factory=lambda *_: spy)
    assert r.exit_code == 0
    assert spy.fetch_calls == ["R1"]   # 1 回だけ叩く

    rows = engagements_mod.records(cfg, account["name"])
    assert len(rows) == 1
    assert rows[0]["author_key"] == expected_key
    # best-effort が成功しているので、runs に失敗の印は立たない。
    state_dir = accounts_mod.state_dir_for(account["name"])
    run_rows = [r for r in runs_mod.read_runs(state_dir) if r.get("post_id") == "ENG1"]
    assert len(run_rows) == 1
    assert run_rows[0]["engagement_write_failed"] is False
    assert run_rows[0]["engagement_author_lookup_failed"] is False


def test_fetch_postが失敗してもauthor_keyがnullになるだけで公開は成功する(isolated_account_factory):
    account = isolated_account_factory(production=True)
    cfg = accounts_mod.load_account(account["name"])
    spy = _SpyWithFetch(fetch_error=adapter_base.AdapterError("投稿の取得: HTTP 400 不明"))
    digest = _digest_for(account["name"], "返信です", reply_to="R1")
    r = core.send_once(
        account["name"], text="返信です", reply_to="R1",
        production_flag=True, confirm=digest, adapter_factory=lambda *_: spy)
    assert r.exit_code == 0, "author_key が埋まらなくても公開は止めない"
    assert spy.fetch_calls == ["R1"]

    rows = engagements_mod.records(cfg, account["name"])
    assert len(rows) == 1
    assert rows[0]["author_key"] is None   # 「見当たらない」であって公開の失敗ではない

    state_dir = accounts_mod.state_dir_for(account["name"])
    run_rows = [r for r in runs_mod.read_runs(state_dir) if r.get("post_id") == "ENG1"]
    assert len(run_rows) == 1
    assert run_rows[0]["status"] == "ok"
    assert run_rows[0]["engagement_write_failed"] is False
    assert run_rows[0]["engagement_author_lookup_failed"] is True


def test_reply_toが無ければ絡みの台帳には何も書かない(isolated_account_factory):
    account = isolated_account_factory(production=True)
    cfg = accounts_mod.load_account(account["name"])
    spy = _SpyWithFetch()
    digest = _digest_for(account["name"], "独り言です")
    r = core.send_once(account["name"], text="独り言です",
                       production_flag=True, confirm=digest, adapter_factory=lambda *_: spy)
    assert r.exit_code == 0
    assert spy.fetch_calls == []
    assert engagements_mod.records(cfg, account["name"]) == []
