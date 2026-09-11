"""スレッド連投の実行記録（設計 §2・§3・§4・工程 3〜5）。

Codex の検収条件のうち、**記録と親の解決**にあたる部分をここで固める。
"""
from __future__ import annotations

import datetime

import pytest

from thth import approval, threadrun

NOW = datetime.datetime.fromisoformat("2026-09-15T19:00:00+09:00")
SHAS = [approval.segment_sha(t) for t in ("1 段目", "2 段目", "3 段目")]


def start(thth_root, *, account="nigamilab-threads", rel_path="q/a.md",
           bundle_sha="abc", count=3):
    return threadrun.start(account=account, rel_path=rel_path,
                            bundle_sha=bundle_sha, segment_count=count,
                            segment_shas=SHAS, continue_until="2026-09-15T20:00:00+09:00",
                            by="test", now=NOW)


def draft_posts(row, upto):
    """原稿側（front matter）の記録を、公開記録から組み立てる。"""
    out = []
    for post in row["posts"][:upto]:
        out.append({"index": str(post["index"]), "post_id": post["post_id"],
                     "run_id": row["run_id"]})
    return out


# --- run_id は公開要求より前に発行する -------------------------------------

def test_公開要求より前にrun_idが決まる(thth_root):
    """**1 段目の post_id を実行 ID にしない**（Codex 最終条件 1）。

    公開結果が不明になったとき、post_id はまだ存在しない。
    **いちばん守りたい場面で使えない識別子は使えない。**
    """
    row = start(thth_root)
    assert row["run_id"].startswith("run-2026")
    assert row["root_post_id"] is None          # まだ 1 本も出していない
    assert threadrun.load(row["run_id"])["run_id"] == row["run_id"]

    # 結果が不明でも、記録は作れる。
    threadrun.mark(row, 1, threadrun.REQUESTED, container_id="c1")
    again = threadrun.load(row["run_id"])
    assert again["posts"][0]["state"] == threadrun.REQUESTED
    assert again["posts"][0]["container_id"] == "c1"


def test_1段目の公開でroot_post_idが決まる(thth_root):
    row = start(thth_root)
    threadrun.mark(row, 1, threadrun.PUBLISHED, post_id="P1",
                    posted_at=NOW.isoformat())
    assert threadrun.load(row["run_id"])["root_post_id"] == "P1"


# --- 次に出す段 -------------------------------------------------------------

def test_未解決があれば進まない(thth_root):
    """**自動で再送しない。後続も止める。**"""
    row = start(thth_root)
    threadrun.mark(row, 1, threadrun.REQUESTED)
    assert threadrun.next_index(row) is None

    row = start(thth_root, rel_path="q/b.md")
    threadrun.mark(row, 1, threadrun.UNRESOLVED, note="応答が読めなかった")
    assert threadrun.next_index(row) is None


def test_公開できたら次の段へ進む(thth_root):
    row = start(thth_root)
    assert threadrun.next_index(row) == 1
    threadrun.mark(row, 1, threadrun.PUBLISHED, post_id="P1")
    assert threadrun.next_index(row) == 2


def test_停止を確認したら進まない(thth_root):
    row = start(thth_root)
    threadrun.mark(row, 1, threadrun.PUBLISHED, post_id="P1")
    threadrun.confirm_stop(row, "別 clone から撤回された", now=NOW)
    assert threadrun.next_index(row) is None
    assert threadrun.is_finished(row) is True


# --- 親の解決（設計 §2.2） --------------------------------------------------

def test_親は公開記録から決まる(thth_root):
    row = start(thth_root)
    threadrun.mark(row, 1, threadrun.PUBLISHED, post_id="P1")
    parent = threadrun.resolve_parent(row, 2, draft_posts=draft_posts(row, 1),
                                       account=row["account"])
    assert parent == "P1"


def test_原稿だけ書き換えても親を差し替えられない(thth_root):
    """**Git で同期済みと、THTH が実際に公開した記録は別**（Codex 最終条件 1）。

    > 原稿に整合した ID 一式を書くだけでは、親を差し替えられないようにして
    > ください。
    """
    row = start(thth_root)
    threadrun.mark(row, 1, threadrun.PUBLISHED, post_id="P1")

    tampered = draft_posts(row, 1)
    tampered[0]["post_id"] = "べつの投稿"          # 原稿だけを書き換える
    with pytest.raises(threadrun.RunError) as e:
        threadrun.resolve_parent(row, 2, draft_posts=tampered,
                                  account=row["account"])
    assert "食い違います" in str(e.value)


def test_別の実行の記録を拾わない(thth_root):
    """**同じ内容の別実行の記録を使えない**（Codex の検収条件）。"""
    first = start(thth_root)
    threadrun.mark(first, 1, threadrun.PUBLISHED, post_id="P1")
    second = start(thth_root)                    # 同じ原稿を出し直した
    threadrun.mark(second, 1, threadrun.PUBLISHED, post_id="Q1")

    # 原稿には**前の実行**の記録が残っている、という状況。
    stale = draft_posts(first, 1)
    with pytest.raises(threadrun.RunError) as e:
        threadrun.resolve_parent(second, 2, draft_posts=stale,
                                  account=second["account"])
    assert "別の実行のものです" in str(e.value)


def test_前の段が出ていなければ親にしない(thth_root):
    row = start(thth_root)
    with pytest.raises(threadrun.RunError) as e:
        threadrun.resolve_parent(row, 2, draft_posts=[], account=row["account"])
    assert "まだ公開されていません" in str(e.value)


def test_原稿に記録が無ければ止める(thth_root):
    row = start(thth_root)
    threadrun.mark(row, 1, threadrun.PUBLISHED, post_id="P1")
    with pytest.raises(threadrun.RunError) as e:
        threadrun.resolve_parent(row, 2, draft_posts=[], account=row["account"])
    assert "原稿に 1 段目の記録がありません" in str(e.value)


def test_accountが違えば止める(thth_root):
    row = start(thth_root)
    threadrun.mark(row, 1, threadrun.PUBLISHED, post_id="P1")
    with pytest.raises(threadrun.RunError):
        threadrun.resolve_parent(row, 2, draft_posts=draft_posts(row, 1),
                                  account="kopicha-threads")


def test_引用符つきのpost_idも同じものとして扱う(thth_root):
    """front matter に `post_id: "18016…"` と書かれていても照合できる。"""
    row = start(thth_root)
    threadrun.mark(row, 1, threadrun.PUBLISHED, post_id="18016228439944497")
    posts = draft_posts(row, 1)
    posts[0]["post_id"] = '"18016228439944497"'
    assert threadrun.resolve_parent(row, 2, draft_posts=posts,
                                     account=row["account"]) == "18016228439944497"


# --- 排他と未解決 -----------------------------------------------------------

def test_進行中の実行を見つける(thth_root):
    row = start(thth_root)
    threadrun.mark(row, 1, threadrun.PUBLISHED, post_id="P1")
    found = threadrun.find_open("nigamilab-threads", "q/a.md")
    assert found["run_id"] == row["run_id"]

    threadrun.mark(row, 2, threadrun.PUBLISHED, post_id="P2")
    threadrun.mark(row, 3, threadrun.PUBLISHED, post_id="P3")
    assert threadrun.find_open("nigamilab-threads", "q/a.md") is None


def test_未解決がある間は同じaccountの別投稿へ進まない(thth_root):
    """**Codex 最終条件 3。**"""
    row = start(thth_root)
    assert threadrun.has_unresolved("nigamilab-threads") == []
    threadrun.mark(row, 1, threadrun.REQUESTED)
    blocked = threadrun.has_unresolved("nigamilab-threads")
    assert [r["run_id"] for r in blocked] == [row["run_id"]]
    # 別の account は止めない。
    assert threadrun.has_unresolved("kopicha-threads") == []


# --- 表示（設計 §4.2） ------------------------------------------------------

def test_確認できた段と要求中と未着手を分けて出す(thth_root):
    """**「N 段目以降は未公開」と断定しない**（Codex 最終条件 3）。"""
    row = start(thth_root)
    threadrun.mark(row, 1, threadrun.PUBLISHED, post_id="P1")
    threadrun.mark(row, 2, threadrun.REQUESTED, container_id="c2")
    out = threadrun.summary(row)
    assert out["published"] == [1]
    assert out["requested"] == [2]
    assert out["pending"] == [3]
    assert out["unresolved"] == []


def test_凍結する記録を取り出せる(thth_root):
    """再承認は**固定した公開済み部分の記録と、変更後の残り**をまとめて。"""
    row = start(thth_root)
    threadrun.mark(row, 1, threadrun.PUBLISHED, post_id="P1")
    frozen = threadrun.frozen_records(row)
    assert frozen == [{"index": 1, "post_id": "P1", "text_sha256": SHAS[0]}]


def test_壊れたrun_idは断る(thth_root):
    with pytest.raises(threadrun.RunError):
        threadrun.run_path("../逃げる")
