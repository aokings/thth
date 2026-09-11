"""読めない実行記録を「未解決なし」と読まない（監査 2026-09-11・F1 と同じ形）。

設計 §8 は「破損と不存在は別状態」と決めているのに、**公開経路の実行記録だけ
その規約から外れていた**——`open_runs()` が読めないファイルを飛ばし、飛ばした
結果が「未解決の公開なし」と同じ値になっていた。

**ディレクトリが無いのは「判らない」ではない**（運用セッション指摘 2026-09-11）。
実行記録が 1 件も無いという確定した事実なので、止めない。
"""
from __future__ import annotations

import json
import os

import pytest

from tests.conftest import run_thth
from tests.test_thread_publish import (FakeAdapter, publish,   # noqa: F401
                                        thread_account)
from thth import threadrun


def _runs_dir(thth_root):
    path = os.path.join(thth_root, "state", "threads")
    os.makedirs(path, exist_ok=True)
    return path


def test_記録が無いことでは止めない(thth_root):
    """**配布した瞬間に全部止まる**ことのないように（運用セッション指摘）。

    いまの VM には `state/threads` が存在しない。「無い」を「判らない」に
    含めると、**連投の運用実績が無いだけの account で公開と承認が止まる。**
    """
    assert threadrun.unreadable_runs() == []
    _runs_dir(thth_root)                      # 空のディレクトリでも同じ
    assert threadrun.unreadable_runs() == []


def test_読めない記録は名前で返す(thth_root):
    directory = _runs_dir(thth_root)
    with open(os.path.join(directory, "run-x.json"), "w", encoding="utf-8") as f:
        f.write("{壊れた")
    ok = {"run_id": "run-y", "account": "a", "rel_path": "q.md", "posts": []}
    with open(os.path.join(directory, "run-y.json"), "w", encoding="utf-8") as f:
        json.dump(ok, f)

    broken = threadrun.unreadable_runs()
    assert len(broken) == 1
    assert broken[0].endswith("run-x.json")
    assert os.path.isabs(broken[0]), "絶対パスで返す（どれを見ればいいか分かる形）"


def test_止めるときに人が動ける形で言う(thth_root):
    """**fail-closed は運用を止める権限を道具に渡す**（運用セッション指摘）。

    > 止まったときに人が動ける形にしてから入れてほしい。
    """
    message = threadrun.unreadable_error(["/srv/thth/state/threads/run-x.json"])
    assert "/srv/thth/state/threads/run-x.json" in message      # どれを見るか
    assert "全 account" in message                              # どこまで止まるか
    assert "単発の投稿・承認は止まっていません" in message        # 止まらない範囲
    assert "退避" in message and "確かめて" in message           # 戻し方


def test_壊れた記録があるあいだは連投を公開しない(thth_root, isolated_account):
    """**壊れた記録 1 件で「未解決の公開なし」になっていた。**"""
    directory = _runs_dir(thth_root)
    unresolved = {
        "run_id": "run-broken", "account": isolated_account["name"],
        "rel_path": "docs/sns/queue/a.md", "posts": [
            {"index": 1, "state": "unresolved", "post_id": None,
             "text_sha256": "a" * 64}]}
    path = os.path.join(directory, "run-broken.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(unresolved, f)

    # 読める状態なら、未解決として止まる（従来どおり）。
    assert threadrun.has_unresolved(isolated_account["name"])

    # **読めなくすると、未解決が消える**——ここが穴だった。
    with open(path, "w", encoding="utf-8") as f:
        f.write("{壊れた")
    assert threadrun.has_unresolved(isolated_account["name"]) == []
    # **だから、その手前で止める。**
    assert threadrun.unreadable_runs(), "読めない記録を見つけられていない"


def test_壊れた記録があるあいだは連投を承認しない(thth_root, thread_account):
    """承認側も同じ穴（`find_latest` → `frozen` が空 → 凍結の検査が素通り）。"""
    directory = _runs_dir(thth_root)
    with open(os.path.join(directory, "run-x.json"), "w", encoding="utf-8") as f:
        f.write("{壊れた")

    proc = run_thth(["approve", str(thread_account["path"])])
    assert proc.returncode != 0
    assert "実行記録を読めません" in proc.stdout + proc.stderr
    # **単発は止めない**（同じ経路を通らない）。
    assert "単発の投稿・承認は止まっていません" in proc.stdout + proc.stderr


def test_壊れた記録があるあいだは実際に1本も公開しない(thth_root, thread_account):
    """**関門が開いていないことを、実際に公開させて確かめる**（規約 11）。

    `has_unresolved()` の戻り値だけを見ても、**公開経路がそこで止まるかは
    確かめたことにならない。** 偽 API を渡して、呼び出し回数が 0 であることを
    見る。
    """
    api = FakeAdapter()
    directory = _runs_dir(thth_root)
    with open(os.path.join(directory, "run-x.json"), "w", encoding="utf-8") as f:
        f.write("{壊れた")

    results = publish(thread_account, api)
    assert len(api.calls) == 0, "壊れた記録があるのに公開した"
    assert any("実行記録を読めません" in (r.reason or "") for r in results), results

    # **壊れたファイルを退避すれば動く**（戻し方が本当に効くか）。
    os.rename(os.path.join(directory, "run-x.json"),
               os.path.join(thth_root, "state", "run-x.json.broken"))
    results = publish(thread_account, api)
    assert len(api.calls) == 3, [r.reason for r in results]   # 3 段そろって出る


def test_別accountの実行の続きとして出さない(thth_root):
    """**`run_id` と `rel_path` しか照合していなかった**（監査 2026-09-11）。

    別 account の repo に同じ相対パスの束があり、その原稿に他所の `run_id` が
    紛れ込んでいると（複製・コピー由来）、**他 account の実行記録に書き込み
    ながら公開できた。** 1 段目は `resolve_parent()` の account 照合にも
    掛からない（`index > 1` のときだけ見ていた）。
    """
    row = {"run_id": "run-a", "account": "A-threads", "rel_path": "q.md",
           "posts": [{"index": 1, "state": "pending", "post_id": None}]}
    posts = [{"index": 1, "run_id": "run-a"}]

    assert threadrun.identity_error(row, posts, rel_path="q.md",
                                     account="A-threads") is None
    problem = threadrun.identity_error(row, posts, rel_path="q.md",
                                        account="B-threads")
    assert problem and "別の account" in problem
