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

from tests.conftest import init_git_pair, make_queue_text, commit_and_push_path, write_queue_file
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

    def conversation(self, post_id, *, since=None):
        if "conversation" in self.fail:
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


def test_採取時点の所有accountが行に書かれる(tmp_path, isolated_account_factory):
    """外部レビュー再判定 R3・2026-09-12: 台帳の行そのものに、採取時点の所有
    `account` を残す。これが無いと、`thth/measured.py` は「いまの原稿の
    account」を過去の所有として使うしかなく、原稿の account を書き換えると
    過去の台帳が黙って別 account の実測へ移し替えられていた。**この行の
    `account` が、あとから原稿の account が変わっても動かない所有の根拠**。"""
    pair, account = _setup(tmp_path, isolated_account_factory,
                            posted_at="2026-09-10T10:00:00+09:00")
    adapter = FakeAdapter()
    collect_mod.run_collect(account["name"], adapter=adapter, now=NOW, log=lambda _l: None)

    rows = _rows(pair["work"], "data/sns/insights/posts/POST1.ndjson")
    assert len(rows) == 1, rows
    assert rows[0]["account"] == account["name"], \
        "採取時点の account が行に無い——過去の所有の根拠が残っていない"


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

    rows = [r for r in _rows(pair["work"], "data/sns/replies/POST1.ndjson")
            if r.get("kind") != "fetch"]
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
    replies = [r for r in _rows(pair["work"], "data/sns/replies/POST1.ndjson")
               if r.get("kind") != "fetch"]
    assert len(replies) == 1


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


def test_返信の取得に失敗した刻みは次に再試行する(tmp_path, isolated_account_factory):
    """外部レビュー第 6 巡 P2-3。

    数が取れて返信が失敗すると、以前はその刻みが「済んだ」ことになり、
    **返信は二度と取りに行かなかった**。最後の刻み（168 時間）で起きると永久に取れない。
    """
    pair, account = _setup(tmp_path, isolated_account_factory,
                            posted_at="2026-09-03T12:00:00+09:00")  # 168 時間前
    failing = FakeAdapter(replies_rows=[{"id": "R1", "text": "返信"}], fail={"conversation"})
    rc = collect_mod.run_collect(account["name"], adapter=failing, now=NOW,
                                  log=lambda _l: None)
    assert rc == 1
    assert _rows(pair["work"], "data/sns/insights/posts/POST1.ndjson"), "数は取れているはず"
    assert _rows(pair["work"], "data/sns/replies/POST1.ndjson") == []

    # 10 分後、返信 API が直ったら**もう一度取りに行く**
    later = NOW + datetime.timedelta(minutes=10)
    healthy = FakeAdapter(replies_rows=[{"id": "R1", "text": "返信"}])
    rc2 = collect_mod.run_collect(account["name"], adapter=healthy, now=later,
                                   log=lambda _l: None)

    assert rc2 == 0, "再試行していない"
    assert healthy.reply_calls == ["POST1"], healthy.reply_calls
    rows = [r for r in _rows(pair["work"], "data/sns/replies/POST1.ndjson")
            if r.get("kind") != "fetch"]
    assert [r["id"] for r in rows] == ["R1"]
    # 数のほうは二重に記録しない
    assert len(_rows(pair["work"], "data/sns/insights/posts/POST1.ndjson")) == 1


def test_返信0件の成功と取得失敗を区別する(tmp_path, isolated_account_factory):
    """0 件で成功したら、その刻みはもう取りに行かない（毎回叩き直さない）。"""
    pair, account = _setup(tmp_path, isolated_account_factory,
                            posted_at="2026-09-10T10:00:00+09:00")
    empty = FakeAdapter(replies_rows=[])
    collect_mod.run_collect(account["name"], adapter=empty, now=NOW, log=lambda _l: None)
    assert empty.reply_calls == ["POST1"]

    again = FakeAdapter(replies_rows=[])
    collect_mod.run_collect(account["name"], adapter=again, now=NOW, log=lambda _l: None)
    assert again.reply_calls == [], "0 件だった刻みを取り直している"




def test_pushに失敗しても未pushのcommitを残さない(tmp_path, isolated_account_factory):
    """masaru 裁定 2026-09-11。**足して固めるのをやめ、状態そのものを作らない。**

    第 6 巡・第 7 巡の P1 はどちらも「未 push の commit が残る」ことから出た。
    push できなければ commit を取り消し、中身はファイルに残す。**未 push が
    存在しないので、投稿は止まらない。**
    """
    import subprocess
    from pathlib import Path

    pair, account = _setup(tmp_path, isolated_account_factory,
                            posted_at="2026-09-10T10:00:00+09:00")
    hook = Path(pair["bare"]) / "hooks" / "pre-receive"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)

    rc = collect_mod.run_collect(account["name"], adapter=FakeAdapter(), now=NOW,
                                  log=lambda _l: None)
    assert rc == 1

    def git(*a):
        return subprocess.run(["git", "-C", pair["work"], *a],
                               capture_output=True, text=True).stdout.strip()

    assert git("rev-parse", "HEAD") == git("rev-parse", "@{u}"), \
        "未 push の commit が残っている（投稿が止まる）"
    assert _rows(pair["work"], "data/sns/insights/posts/POST1.ndjson"), "採取した中身が消えた"

    # **投稿は止まらない**
    from thth import writeback
    synced, err, _sha = writeback.sync_repo(pair["work"])
    assert synced, f"収集の失敗が同期を止めている: {err}"

    # board が「未送信」として見せる
    from thth import accounts as accounts_mod
    cfg = accounts_mod.load_account(account["name"])
    assert collect_mod.pending_paths(pair["work"], cfg), "未送信に気づく口が無い"


def test_origin復旧後に次の実行が送り直す(tmp_path, isolated_account_factory):
    import subprocess
    from pathlib import Path

    pair, account = _setup(tmp_path, isolated_account_factory,
                            posted_at="2026-09-10T10:00:00+09:00")
    hook = Path(pair["bare"]) / "hooks" / "pre-receive"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    collect_mod.run_collect(account["name"], adapter=FakeAdapter(), now=NOW,
                             log=lambda _l: None)
    hook.unlink()

    later = NOW + datetime.timedelta(hours=6)
    rc = collect_mod.run_collect(account["name"], adapter=FakeAdapter(), now=later,
                                  log=lambda _l: None)
    assert rc == 0, "origin が直っても送り直していない"

    in_origin = subprocess.run(["git", "-C", pair["bare"], "ls-tree", "-r", "--name-only", "main"],
                                capture_output=True, text=True).stdout
    assert "data/sns/insights/posts/POST1.ndjson" in in_origin, "採取が origin に届いていない"
    rows = _rows(pair["work"], "data/sns/insights/posts/POST1.ndjson")
    assert [r["marks"] for r in rows] == [[1], [6]], rows


def test_bundleのmediaが合わないとerrorsに出るが他の投稿は採れる(tmp_path, isolated_account_factory):
    """バグ 3: account の `media` を書き換えると（表記の修正など）、公開済みの
    `thth: 2` 束の `## <旧media>` 節が見つからなくなり `problems` が非空になる。
    以前は `continue` で黙ってその束の全投稿を採取から落とし、`posts: 0`・
    `errors: []` で正常終了に見えていた（監査 2026-09-11・掃討で検出）。
    冒頭コメントの規約 12「取れなかった指標は書かない。0 と混ぜると判らなくなる」
    がここで破れていた。読めなかった事実が `errors` に出ること、**他の投稿の
    採取は続く**ことを確かめる。
    """
    bundle_fm = """thth: 2
account: nigamilab-threads
publish_at: 2026-09-06T10:00:00+09:00
status: posted
posts:
  - index: 1
  - index: 2
"""
    # account の media（既定 threads）と合わない節名にして、
    # `bundle.load_segments()` を「節が無い」で失敗させる。
    bundle_body = """## other-media

段 1。

<!-- thth: 2/2 -->

段 2。
"""
    bundle_text = f"---\n{bundle_fm}---\n{bundle_body}"

    pair = init_git_pair(tmp_path, seed_content=bundle_text, seed_name="bundle.md")
    account = isolated_account_factory(repo_dir=pair["work"], production=True)

    # 束とは別に、ふつうに投稿できている v1 の記事も 1 本置く。
    write_queue_file(pair["queue_dir"], "normal.md",
                      fm_overrides={"status": "posted", "post_id": "POST1",
                                    "posted_at": "2026-09-10T10:00:00+09:00"})

    adapter = FakeAdapter()
    result = collect_mod.collect_once(account["name"], adapter=adapter, now=NOW,
                                       log=lambda _l: None)

    assert result["posts"] == 1, "束が読めなくても、他の投稿は数える"
    assert any("bundle.md" in e for e in result["errors"]), (
        f"束が読めなかった事実が errors に出ていない: {result['errors']}")
    assert _rows(pair["work"], "data/sns/insights/posts/POST1.ndjson"), (
        "束の失敗につられて、他の投稿の採取まで止まっている")


def test_採れなかった理由をlogに出す(tmp_path, isolated_account_factory):
    """**理由を集めておきながら、どこにも出していなかった**（2026-09-12）。

    運用セッションが VM を追っていて詰まった: 返信の 1h と 6h がなぜ失敗したかを
    知りたいのに、`/srv/thth/logs/` は空で journal にも理由が無い。
    `run_collect()` は `errors` を集めて終了コードにしていたが、**中身を log に
    出していなかった。** 失敗の理由は、**失敗した回にしか書けない。**
    """
    pair, account = _setup(tmp_path, isolated_account_factory,
                            posted_at="2026-09-03T12:00:00+09:00")   # 168 時間前
    failing = FakeAdapter(replies_rows=[{"id": "R1", "text": "返信"}],
                           fail={"conversation"})
    lines = []
    rc = collect_mod.run_collect(account["name"], adapter=failing, now=NOW,
                                  log=lines.append)
    assert rc == 1
    reasons = [l for l in lines if l.startswith("採れなかったもの:")]
    assert reasons, f"理由が出ていない: {lines}"
    assert "conversation" in reasons[0] and "POST1" in reasons[0], reasons

def test_返信への返信も台帳に残る(tmp_path, isolated_account_factory):
    """**うちの側の発言が台帳に残らなかった**（2026-09-12）。

    `/{post_id}/replies` は**上位 1 階層だけ**を返す。masaru が 08:25〜08:40 に
    kopicha の投稿への返信 2 件に**返信した**（＝2 段目）が、`thth replies` に
    1 件も出なかった。運用セッションがスクリーンショットで現物を見ていたのに。
    **会話の片側しか記録されない。**

    **設計にはもともと `GET /{post_id}/conversation`（全階層）と書いてあった**
    （設計 §5）。実装が `/replies` を呼んでいたのは**逸脱**。

    ここでは、**会話の口が 2 段目を返したら、それが台帳に残る**ことだけを見る
    （階層をこちらで組み立てるのではなく、**返ってきたものを落とさない**こと）。
    """
    pair, account = _setup(tmp_path, isolated_account_factory,
                            posted_at="2026-09-10T10:00:00+09:00")
    会話 = [
        {"id": "R1", "text": "薬品匂？", "username": "funaemon2",
         "is_reply": True, "replied_to": {"id": "POST1"}},
        # **2 段目**——返信への返信。`/replies` では返ってこなかったもの。
        {"id": "R2", "text": "そうなんです", "username": "kopi_chaba",
         "is_reply": True, "replied_to": {"id": "R1"}, "root_post": {"id": "POST1"}},
    ]
    adapter = FakeAdapter(replies_rows=会話)
    collect_mod.run_collect(account["name"], adapter=adapter, now=NOW,
                             log=lambda _l: None)

    ids = {r.get("id") for r in _rows(pair["work"], "data/sns/replies/POST1.ndjson")}
    assert "R1" in ids, "1 段目が落ちている"
    assert "R2" in ids, "**2 段目（返信への返信）が台帳に残っていない**"

def test_会話の口は階層が読める項目を取りに行く():
    """**変異で分かった**（2026-09-12）: 経路を `/replies` に戻す変異も、
    `root_post` を落とす変異も、**こちらが足した「2 段目が残る」テストでは
    捕まらない**（偽アダプタは経路も項目も見ない）。ここで口の側を押さえる。

    階層の形（どれがどれへの返信か）が残らないと、**会話として読み返せない。**
    """
    from thth.adapters import threads as threads_mod

    呼ばれた = {}

    class _口(threads_mod.ThreadsAdapter):
        def __init__(self):
            pass

        def _get(self, path, params):
            呼ばれた["path"] = path
            呼ばれた["fields"] = params.get("fields", "")
            return {"data": []}

        def _rows(self, body, 何):
            return body["data"]

    _口().conversation("POST1")

    assert 呼ばれた["path"].endswith("/conversation"), \
        "**上位 1 階層しか返さない口を呼んでいる**"
    for 項目 in ("id", "text", "username", "timestamp", "replied_to", "root_post"):
        assert 項目 in 呼ばれた["fields"], f"`{項目}` を取りに行っていない"
