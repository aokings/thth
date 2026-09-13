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

@pytest.mark.parametrize("結果, 出るはず, 出ないはず", [
    ({"skipped": None, "requested": 2, "fetched": 2, "new_replies": 3,
      "failed": [], "errors": [], "saved": True, "remote": "synced",
      "checked_at": "2026-09-12T12:00:00+09:00"},
     ["新しい返信 3 件", "送信済み", "全件取れたとは限りません"], ["見送りました"]),
    ({"skipped": "locked", "requested": 0, "fetched": 0, "new_replies": 0,
      "failed": [], "errors": [], "saved": False, "remote": "unknown",
      "checked_at": "x"},
     ["見送りました", "ほかの実行"], ["新しい返信"]),
    ({"skipped": None, "requested": 1, "fetched": 0, "new_replies": 0,
      "failed": [{"post_id": "P1", "reason": "取れません"}], "errors": [],
      "saved": False, "remote": "unknown", "checked_at": "x"},
     ["取れなかった", "P1"], ["送信済み"]),
    ({"skipped": None, "requested": 1, "fetched": 1, "new_replies": 1,
      "failed": [], "errors": ["送れていません"], "saved": True,
      "remote": "not_synced", "checked_at": "x"},
     ["保存はできましたが送れていません"], ["送信済み"]),
])
def test_人向け出力は状態を書き分ける(結果, 出るはず, 出ないはず, capsys):
    """**一度も実行されていなかった経路**（2026-09-12・こちらの洗い出しで発覚）。

    **API 成功・保存成功・送信成功は別**なので、画面でも分かれること。
    """
    from thth import cli as cli_mod
    cli_mod._print_refresh(結果)
    out = capsys.readouterr().out
    for 語 in 出るはず:
        assert 語 in out, f"**{語} が出ていない**: {out}"
    for 語 in 出ないはず:
        assert 語 not in out, f"**{語} が出てしまっている**: {out}"


def test_人向け出力でrefreshを通しで走らせる(tmp_path, isolated_account_factory,
                                              capsys, monkeypatch):
    """**人向けの経路を、実際に端から端まで通す。**"""
    import argparse
    from thth import cli as cli_mod
    pair, account = _仕立て(tmp_path, isolated_account_factory)
    口 = _口([{"id": "R1", "text": "返信", "username": "よそ"}])
    # **差し替える前に本物を捕まえる。** `cli.collect_mod` は同じモジュール
    # なので、中で名前越しに呼ぶと**自分自身を呼んで**無限に回る。
    本物 = collect_mod.refresh_replies

    def _差し替え(name, **kw):
        kw.pop("log", None)
        return 本物(name, adapter=口, now=NOW, log=lambda _l: None, **kw)

    monkeypatch.setattr(cli_mod.collect_mod, "refresh_replies", _差し替え)
    rc = cli_mod.cmd_replies(argparse.Namespace(account=account["name"], post=None,
                                                 json=False, refresh=True))
    out = capsys.readouterr().out
    assert "取り直し:" in out and "新しい返信 1 件" in out
    # **終了コードを確かめる**（独立検収 B・2026-09-12）。
    # `assert rc in (0, 1)` と書いていたので、**人向けの終了コードが常に 0 に
    # なっていた欠陥を、このテストが通していた。**
    assert rc == 0, "取れているのに非 0"


def test_人向けでも失敗は終了コードに出る(tmp_path, isolated_account_factory,
                                            capsys, monkeypatch):
    """**`&&` で繋いだときに失敗が素通りしない**（独立検収 B）。"""
    import argparse
    from thth import cli as cli_mod
    pair, account = _仕立て(tmp_path, isolated_account_factory)
    本物 = collect_mod.refresh_replies

    def _差し替え(name, **kw):
        kw.pop("log", None)
        return 本物(name, adapter=_口([], fail=True), now=NOW,
                    log=lambda _l: None, **kw)

    monkeypatch.setattr(cli_mod.collect_mod, "refresh_replies", _差し替え)
    rc = cli_mod.cmd_replies(argparse.Namespace(account=account["name"], post=None,
                                                 json=False, refresh=True))
    assert rc == 1, "**人向けだと失敗しても 0 で終わっている**"


def test_取るものが無いのは失敗ではない(tmp_path, isolated_account_factory,
                                          capsys, monkeypatch):
    """**「取るものが無い」と「送れなかった」を同じにしない**（独立検収 B）。"""
    import argparse
    from thth import cli as cli_mod
    pair, account = _仕立て(tmp_path, isolated_account_factory,
                             posted_at="2026-08-01T10:00:00+09:00")   # 期間外
    本物 = collect_mod.refresh_replies

    def _差し替え(name, **kw):
        kw.pop("log", None)
        return 本物(name, adapter=_口([]), now=NOW, log=lambda _l: None, **kw)

    monkeypatch.setattr(cli_mod.collect_mod, "refresh_replies", _差し替え)
    rc = cli_mod.cmd_replies(argparse.Namespace(account=account["name"], post=None,
                                                 json=False, refresh=True))
    assert rc == 0, "**取るものが無いだけなのに失敗にしている**"


def test_push拒否で未pushのcommitを残さない(tmp_path, isolated_account_factory,
                                              monkeypatch):
    """**P1。** 臨時の取り直しが 1 回失敗しただけで、以後 timer が投稿も採取も
    しなくなる状態を、新しい入口から作れていた（独立検収 B）。"""
    from thth import writeback
    pair, account = _仕立て(tmp_path, isolated_account_factory)
    monkeypatch.setattr(writeback, "commit_and_push",
                         lambda *a, **k: (False, "push を拒否されました"))
    out = collect_mod.refresh_replies(account["name"], adapter=_口([{"id": "R1"}]),
                                       now=NOW, log=lambda _l: None)
    assert out["remote"] == "not_synced"
    # **未 push の commit が残っていない。**
    残り = __import__("subprocess").run(
        ["git", "-C", pair["work"], "rev-list", "--count", "@{u}..HEAD"],
        capture_output=True, text=True)
    assert 残り.stdout.strip() == "0", "**未 push の commit が残っている**"
    # 次の同期が通ること（投稿が止まらない）。
    synced, err, _sha = writeback.sync_repo(pair["work"])
    assert synced, f"**次の同期が通らない**: {err}"


def test_post_idにパス区切りがあっても台帳の外に書かない(tmp_path, isolated_account_factory):
    """**取った会話が repo の外に落ちて、表示は「成功」だった**（独立検収 B）。

    **守り方を変えた**（T3・2026-09-13）。以前は `/` を含む `post_id` を弾いて
    いたが、Bluesky の `post_id` は AT URI（`at://…/app.bsky.feed.post/<rkey>`）で
    **正しい値が `/` を含む**——弾くと Bluesky の投稿が 1 本も採取されない。
    いまはパスにする直前に percent-encode する（`thth/postid.py`）ので、
    **区切りが区切りとして効かない。** 守っているもの（repo の外に書かない）は
    同じで、Threads の既存のファイル名も 1 文字も変わらない。
    """
    pair, account = _仕立て(tmp_path, isolated_account_factory)
    from tests.conftest import write_queue_file
    write_queue_file(account["queue_dir"], "b.md", fm_overrides={
        "status": "posted", "post_id": "../../../脱出", "posted_at": POSTED})
    out = collect_mod.refresh_replies(account["name"], adapter=_口([{"id": "R1"}]),
                                       now=NOW, log=lambda _l: None)
    # **どこにも脱出していない。**
    assert not os.path.exists(os.path.join(tmp_path, "脱出.ndjson"))
    assert not os.path.exists(os.path.join(pair["work"], "..", "脱出.ndjson"))
    # 書いたものがあれば、それは replies_dir の**直下**（名前は encode 済み）。
    replies_dir = os.path.realpath(os.path.join(pair["work"], "data", "sns", "replies"))
    for name in (os.listdir(replies_dir) if os.path.isdir(replies_dir) else []):
        居場所 = os.path.realpath(os.path.join(replies_dir, name))
        assert os.path.dirname(居場所) == replies_dir, 居場所


def test_時間帯の無いposted_atで全体を止めない(tmp_path, isolated_account_factory):
    """**1 本読めないことを、全部読めないことにしない**（独立検収 B）。"""
    pair, account = _仕立て(tmp_path, isolated_account_factory)
    from tests.conftest import write_queue_file
    write_queue_file(account["queue_dir"], "b.md", fm_overrides={
        "status": "posted", "post_id": "POST2",
        "posted_at": "2026-09-12 11:30:00"})      # **+09:00 が無い**
    out = collect_mod.refresh_replies(account["name"], adapter=_口([{"id": "R1"}]),
                                       now=NOW, log=lambda _l: None)
    assert out["fetched"] == 1, "**壊れた 1 本で全部止まっている**"
    assert any("posted_at を読めません" in e for e in out["errors"]), out


def test_保存できない投稿はその投稿の失敗にする(tmp_path, isolated_account_factory,
                                                monkeypatch):
    """**API の失敗は 1 本で済むのに、保存の失敗だけ全体が落ちていた**（独立検収 B）。"""
    pair, account = _仕立て(tmp_path, isolated_account_factory)
    monkeypatch.setattr(collect_mod, "_save_replies",
                         lambda *a, **k: (_ for _ in ()).throw(
                             PermissionError("書けません")))
    out = collect_mod.refresh_replies(account["name"], adapter=_口([{"id": "R1"}]),
                                       now=NOW, log=lambda _l: None)
    assert out["failed"] and "保存できません" in out["failed"][0]["reason"]
    assert out["fetched"] == 0
