"""同席の様態（`thth send`）で出した投稿を `thth collect` が採る（設計 v2.0.1）。

**見つかった欠陥**（運用 2026-09-14）。`collect_once()` は queue の原稿だけを見て
いた。`thth send` は queue を通らず、記録は `state/<account>/sent/<post_id>.json`
だけ——だから同席専用の台帳（`repos/_none`・`scheduled: false`）では**実測も返信も
1 件も採れなかった。** `thth board` と `thth posts` は 2026-09-13 に `sent/` を読む
よう直っていたが、**collect だけが直っていなかった。**

ここで固定するのは 7 つ（発注 (a)〜(g)）。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os

import pytest

from tests.conftest import (init_git_pair, make_queue_text, run_thth)
from thth import accounts as accounts_mod
from thth import account_report as account_report_mod
from thth import cli as cli_mod
from thth import collect as collect_mod
from thth import jst
from thth import measured as measured_mod
from thth import sent as sent_mod

NOW = datetime.datetime(2026, 9, 14, 12, 0, tzinfo=jst.JST)

# **本文は `sent/` にだけ**（設計 v2.0.1 §4・受け入れ (f)）。仕込んでおいて、
# 採取が書いたファイルを丸ごと grep する。
SECRET_BODY = "SECRET-BODY-同席の本文は台帳に落ちない"


class FakeAdapter:
    """`tests/test_collect.py` の偽アダプタと同じ流儀（能力を名乗る）。"""

    CAPABILITIES = frozenset({"views"})

    @classmethod
    def capabilities(cls):
        return set(cls.CAPABILITIES)

    def __init__(self, *, views=100, replies_rows=None):
        self.views = views
        self.replies_rows = replies_rows or []
        self.insight_calls = []
        self.reply_calls = []

    def insights(self, post_id):
        self.insight_calls.append(post_id)
        return {"metrics": {"views": self.views, "likes": 3,
                            "replies": len(self.replies_rows)},
                "available": list(measured_mod.POST_METRIC_NAMES)}

    def conversation(self, post_id, *, since=None):
        self.reply_calls.append(post_id)
        return list(self.replies_rows)


def 同席専用の台帳(tmp_path, factory, *, name="masaru-bluesky-x"):
    """`repo_dir` が実在しない（`repos/_none` 相当）・`scheduled: false` の台帳。"""
    account = factory(name=name, repo_dir=str(tmp_path / "repos" / "_none"),
                       media="bluesky", handle="x.bsky.social",
                       production=True, scheduled=False)
    return account


def 送った記録(account_name: str, post_id: str, *, sent_at: str, text=SECRET_BODY) -> None:
    sent_mod.write(accounts_mod.state_dir_for(account_name), post_id=post_id,
                    text=text, body_hash="dummy-hash", sent_at=sent_at)


def _rows(path: str) -> list:
    if not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def 置き場(account_name: str) -> dict:
    return accounts_mod.data_dirs(accounts_mod.load_account(account_name), account_name)


def gitを呼んだら落ちる(monkeypatch):
    """**repo が無い account で git を 1 度でも呼んだら落ちる**（受け入れ (a)）。"""
    def 呼ぶな(*a, **k):
        raise AssertionError("repo が無いのに git を呼んだ")

    monkeypatch.setattr(collect_mod, "_git", 呼ぶな)
    monkeypatch.setattr(collect_mod.writeback, "sync_repo", 呼ぶな)
    monkeypatch.setattr(collect_mod.writeback, "commit_and_push", 呼ぶな)


# ---------------------------------------------------------------- (a)

def test_a_同席専用の台帳でもsentから実測と返信を採る(tmp_path, isolated_account_factory,
                                                        monkeypatch):
    """**出したものを測れないなら、出す意味が薄い。**

    `sent/` に 2 件。偽アダプタから実測と返信を採り、
    `$THTH_ROOT/state/<account>/data/sns/…` に書く。**git は 1 度も呼ばない**
    ——版管理の相手がいないので、書いて終わり（設計 v2.0.1 §1）。
    """
    account = 同席専用の台帳(tmp_path, isolated_account_factory)
    送った記録(account["name"], "POST-S1", sent_at="2026-09-14T10:00:00+09:00")
    送った記録(account["name"], "POST-S2", sent_at="2026-09-14T06:00:00+09:00")
    gitを呼んだら落ちる(monkeypatch)

    adapter = FakeAdapter(replies_rows=[{"message_id": "R1", "text": "返信"}])
    rc = collect_mod.run_collect(account["name"], adapter=adapter, now=NOW,
                                  log=lambda _l: None)

    assert rc == 0
    assert sorted(adapter.insight_calls) == ["POST-S1", "POST-S2"], adapter.insight_calls
    d = 置き場(account["name"])
    assert d["insights_posts"].startswith(accounts_mod.state_dir_for(account["name"])), d
    for post_id in ("POST-S1", "POST-S2"):
        insights = _rows(os.path.join(d["insights_posts"], f"{post_id}.ndjson"))
        assert len(insights) == 1, insights
        assert insights[0]["metrics"]["views"] == 100
        assert insights[0]["account"] == account["name"]
        # **出所を連れて歩く**（設計 v2.0.1 §3）。
        assert insights[0]["source"] == "sent", insights[0]
        # 同席の様態には原稿が無い。偽の名前を置かない。
        assert insights[0]["file"] is None, insights[0]

        replies = _rows(os.path.join(d["replies"], f"{post_id}.ndjson"))
        取得 = [r for r in replies if r.get("kind") == "fetch"]
        返信 = [r for r in replies if r.get("kind") != "fetch"]
        assert len(返信) == 1 and 返信[0]["message_id"] == "R1", replies
        assert 取得 and 取得[0]["source"] == "sent", 取得

    # repo 側には 1 バイトも書いていない（そもそも存在しない）。
    assert not os.path.exists(str(tmp_path / "repos" / "_none")), "repo を作った"


# ---------------------------------------------------------------- (b)

def test_b_queueとsentに同じpost_idがあれば1件(tmp_path, isolated_account_factory):
    """**同じ投稿を 2 回数えない。** 重なったら queue を正（多い方を残す）。

    `sent_at` を queue の `posted_at` とずらしてある。**重複除去が無いと、
    同じ投稿が「別の経過時間の 2 本」として台帳に入る**——刻みの冪等では
    止まらない（違う刻みを跨ぐので、どちらも「まだ記録していない」になる）。
    """
    pair = init_git_pair(tmp_path, seed_content=make_queue_text({
        "status": "posted", "post_id": "POST1",
        "posted_at": "2026-09-14T10:00:00+09:00"}))
    account = isolated_account_factory(repo_dir=pair["work"], production=True)
    送った記録(account["name"], "POST1", sent_at="2026-09-14T06:00:00+09:00")

    # **対象を組み立てた時点で 1 件**（ここが重複除去の本体）。
    cfg = accounts_mod.load_account(account["name"])
    from thth import core as core_mod
    from thth import writeback as writeback_mod
    対象 = collect_mod._with_sent_posts(
        core_mod.list_queue_files(
            cfg, tree_sha=writeback_mod.upstream_sha(cfg["repo_dir"])),
        account["name"], cfg)
    重なり = [qf for qf in 対象 if qf.front_matter.get("post_id") == "POST1"]
    assert len(重なり) == 1, [qf.path for qf in 重なり]
    assert collect_mod._source_of(重なり[0]) == "queue", 重なり[0].path

    adapter = FakeAdapter()
    collect_mod.run_collect(account["name"], adapter=adapter, now=NOW,
                             log=lambda _l: None)

    assert adapter.insight_calls == ["POST1"], f"2 回採っている: {adapter.insight_calls}"
    d = 置き場(account["name"])
    rows = _rows(os.path.join(d["insights_posts"], "POST1.ndjson"))
    assert len(rows) == 1, rows
    # queue が正——`file` も `source` も queue のまま。
    assert rows[0]["source"] == "queue", rows[0]
    assert rows[0]["file"] == "a.md", rows[0]
    # state 側には書いていない（repo があるので repo 側が正）。
    state_側 = os.path.join(accounts_mod.state_dir_for(account["name"]),
                             "data", "sns", "insights", "posts", "POST1.ndjson")
    assert not os.path.exists(state_側), "repo があるのに state に書いた"


# ---------------------------------------------------------------- (c)

def test_c_collect_daysの外のsentは採らない(tmp_path, isolated_account_factory,
                                              monkeypatch):
    """窓は queue と同じ（既定 14 日）。**別の母集団を作らない。**"""
    account = 同席専用の台帳(tmp_path, isolated_account_factory)
    送った記録(account["name"], "POST-OLD", sent_at="2026-08-01T10:00:00+09:00")
    送った記録(account["name"], "POST-NEW", sent_at="2026-09-14T10:00:00+09:00")
    gitを呼んだら落ちる(monkeypatch)

    adapter = FakeAdapter()
    collect_mod.run_collect(account["name"], adapter=adapter, now=NOW,
                             log=lambda _l: None)

    assert adapter.insight_calls == ["POST-NEW"], adapter.insight_calls
    d = 置き場(account["name"])
    assert not os.path.exists(os.path.join(d["insights_posts"], "POST-OLD.ndjson"))


# ---------------------------------------------------------------- (d)

def test_d_従来の台帳は置き場も行の中身も変わらない(tmp_path, isolated_account_factory):
    """**既存の経路は 1 バイトも変えない**（`source` の鍵が 1 つ増えるだけ）。

    `tests/test_collect.py` 等が無改変で通ることが本体だが、ここでも
    「repo の下の同じパスに、同じ形の行が 1 本」を明示して固定する。
    """
    pair = init_git_pair(tmp_path, seed_content=make_queue_text({
        "status": "posted", "post_id": "POST1",
        "posted_at": "2026-09-14T10:00:00+09:00"}))
    account = isolated_account_factory(repo_dir=pair["work"], production=True)

    collect_mod.run_collect(account["name"], adapter=FakeAdapter(), now=NOW,
                             log=lambda _l: None)

    rows = _rows(os.path.join(pair["work"], "data", "sns", "insights", "posts",
                               "POST1.ndjson"))
    assert len(rows) == 1, rows
    assert set(rows[0]) == {
        "post_id", "file", "source", "account", "medium", "topic", "reply_to",
        "text_length", "has_link", "collected_at", "posted_at", "age_hours",
        "marks", "metrics", "context"}, sorted(rows[0])
    assert rows[0]["context"] is None
    assert rows[0]["source"] == "queue"
    assert rows[0]["file"] == "a.md"
    assert rows[0]["age_hours"] == 2.0


# ---------------------------------------------------------------- (e)

def _画面(account_name, cmd, **kw):
    args = argparse.Namespace(account=account_name, post=None, json=False,
                               limit=25, refresh=False)
    for k, v in kw.items():
        setattr(args, k, v)
    return cmd(args)


def test_e_4つの口が同席専用の台帳をstateから読んで出所を出す(
        tmp_path, isolated_account_factory, monkeypatch, capsys):
    """`measured`・`threads`・`replies`・`posts` が state 側を読んで rc=0。"""
    account = 同席専用の台帳(tmp_path, isolated_account_factory)
    送った記録(account["name"], "POST-S1", sent_at="2026-09-14T10:00:00+09:00")
    gitを呼んだら落ちる(monkeypatch)
    collect_mod.run_collect(
        account["name"], now=NOW, log=lambda _l: None,
        adapter=FakeAdapter(replies_rows=[
            {"message_id": "R1", "username": "だれか", "text": "返信",
             "timestamp": "2026-09-14T02:30:00+0000"}]))
    capsys.readouterr()

    assert _画面(account["name"], cli_mod.cmd_measured) == 0
    out = capsys.readouterr().out
    assert "POST-S1" in out and "出所=同席の送信" in out, out
    assert "原稿なし" in out, out

    assert _画面(account["name"], cli_mod.cmd_threads) == 0
    out = capsys.readouterr().out
    assert "POST-S1" in out and "出所=同席の送信" in out, out

    assert _画面(account["name"], cli_mod.cmd_replies) == 0
    out = capsys.readouterr().out
    assert "@だれか" in out and "同席の送信" in out, out

    # `posts` は媒体に聞きに行く口なので、境界だけ差し替える（`tests/
    # test_account_report.py` と同じ流儀）。**突合の相手が `sent/` であること**を見る。
    monkeypatch.setattr(account_report_mod, "fetch_posts", lambda *a, **k: ([
        {"post_id": "POST-S1", "timestamp": "2026-09-14T01:00:00+0000",
         "url": "https://example.invalid/POST-S1", "text": "本文"}], None))
    assert _画面(account["name"], cli_mod.cmd_posts) == 0
    out = capsys.readouterr().out
    assert "THTH（同席の送信）" in out, out


# ---------------------------------------------------------------- (f)

def test_f_sentの本文は実測にも返信にも入らない(tmp_path, isolated_account_factory,
                                                  monkeypatch):
    """**本文は `sent/` にだけ**（設計 v2.0.1 §4・止まる条件）。

    行に入るのは `text_length` と `has_link`（queue 由来と同じ導出値）だけ。
    """
    account = 同席専用の台帳(tmp_path, isolated_account_factory)
    送った記録(account["name"], "POST-S1", sent_at="2026-09-14T10:00:00+09:00")
    gitを呼んだら落ちる(monkeypatch)
    collect_mod.run_collect(account["name"], adapter=FakeAdapter(), now=NOW,
                             log=lambda _l: None)

    base = os.path.join(accounts_mod.state_dir_for(account["name"]), "data")
    見た = 0
    for root, _dirs, names in os.walk(base):
        for name in names:
            path = os.path.join(root, name)
            見た += 1
            assert SECRET_BODY not in open(path, encoding="utf-8").read(), \
                f"本文が採取の台帳に落ちた: {path}"
    assert 見た >= 1, "採取が 1 ファイルも書いていない（この試験の前提が崩れている）"

    d = 置き場(account["name"])
    row = _rows(os.path.join(d["insights_posts"], "POST-S1.ndjson"))[0]
    assert row["text_length"] == len(SECRET_BODY), row
    assert row["has_link"] is False, row


# ---------------------------------------------------------------- (g)

def test_g_乾式_account_addした同席専用の台帳で採ってmeasuredに1件(
        tmp_path, monkeypatch):
    """**まっさらな `$THTH_ROOT` に `thth account add` を 1 本**（`tests/
    test_fresh_install.py` の型）。

    **`--repo-dir $THTH_ROOT/repos/_none` を付ける**（監査 2 回目・P2-1 で
    変更）。以前はここで `add` の既定（`$THTH_ROOT/repos/<project>`）のまま
    「実在しない＝同席専用」としていたが、**それでは「まだ clone していない」と
    「clone が見えなくなった」が同じ扱いになる**——後者で黙って state に転ぶと、
    書いたものは版管理にも board の「未送信」にも出ない。**repo を持たない
    アカウントは台帳でそう名乗る**（設計 §・`masaru-threads` と同じ綴り）。
    """
    root = tmp_path / "thth_root"
    accounts_dir = root / "accounts"
    root.mkdir()
    env = {"THTH_ROOT": str(root), "THTH_ACCOUNTS_DIR": str(accounts_dir)}
    monkeypatch.setenv("THTH_ROOT", str(root))
    monkeypatch.setenv("THTH_ACCOUNTS_DIR", str(accounts_dir))

    add = run_thth(["account", "add", "demo-bluesky", "--media", "bluesky",
                    "--project", "demo", "--handle", "demo.bsky.social",
                    "--repo-dir", "$THTH_ROOT/repos/_none"], env=env)
    assert add.returncode == 0, f"{add.stdout}\n{add.stderr}"
    cfg = accounts_mod.load_account("demo-bluesky")
    assert not accounts_mod.is_repo_backed(cfg), cfg["repo_dir"]

    送った記録("demo-bluesky", "POST-G1", sent_at="2026-09-14T09:00:00+09:00")
    rc = collect_mod.run_collect("demo-bluesky", adapter=FakeAdapter(views=42),
                                  now=NOW, log=lambda _l: None)
    assert rc == 0

    見た = run_thth(["measured", "demo-bluesky", "--json"], env=env)
    assert 見た.returncode == 0, f"{見た.stdout}\n{見た.stderr}"
    answer = json.loads(見た.stdout)
    assert len(answer["posts"]) == 1, answer
    assert answer["posts"][0]["post_id"] == "POST-G1"
    assert answer["posts"][0]["source"] == "sent", answer["posts"][0]
    assert answer["posts"][0]["rows"][0]["metrics"]["views"] == 42
