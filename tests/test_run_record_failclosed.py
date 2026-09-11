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
    # **実行記録として解釈できる形**にする（2026-09-12・N1 の検査を足したので、
    # `posts` が空だと「解釈できない」側に入る）。
    ok = {"run_id": "run-y", "account": "a", "rel_path": "q.md",
          "posts": [{"index": 1, "state": "pending", "post_id": None}]}
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


# --- 再々判定 N1・N2（2026-09-12 Codex）: 解釈できない記録を「無い」にしない ---

def test_JSONとして読めても実行記録として読めなければ止める(thth_root):
    """**「JSON として読める」と「実行記録として読める」は別**（N1）。

    棚（`topic_store`）は前から「読むたびに中身を照合する」を守っていたのに、
    **公開経路の実行記録だけ `json.load()` の成否しか見ていなかった。**
    中身を `{}` に置き換えると「壊れていない」ことになり、そのあと
    `find_latest()` が account 不一致で飛ばすので**「該当する実行が無い」と
    同じ状態**になった——凍結済みの段を持たない新しい束として、
    **1 段目が再公開できた。**
    """
    directory = _runs_dir(thth_root)
    for name, body in (("run-empty.json", {}),
                        ("run-noposts.json", {"run_id": "r", "account": "a",
                                               "rel_path": "q.md", "posts": []}),
                        ("run-badstate.json", {"run_id": "r", "account": "a",
                                                "rel_path": "q.md",
                                                "posts": [{"index": 1,
                                                            "state": "なにか"}]})):
        with open(os.path.join(directory, name), "w", encoding="utf-8") as f:
            json.dump(body, f)

    broken = threadrun.unreadable_runs()
    assert len(broken) == 3, broken
    assert all(b.endswith(".json") for b in broken)

    # **正しい形は通す**（止めすぎない）。
    # **`published` と名乗る段には、公開の事実を復元する項目が要る**
    # （2026-09-12・M1 の検査を足したので、`post_id` だけでは通らない）。
    ok = {"run_id": "run-ok", "account": "a", "rel_path": "q.md",
          "posts": [{"index": 1, "state": "published", "post_id": "P1",
                      "posted_at": "2026-09-15T19:00:00+09:00",
                      "text_sha256": "a" * 64, "bundle_sha": "b" * 64},
                     {"index": 2, "state": "pending", "post_id": None}]}
    assert threadrun.run_problem(ok) is None

    # **公開の痕跡が残っているのに published でない**のは矛盾（M1 の本体）。
    contradicted = {"run_id": "run-x", "account": "a", "rel_path": "q.md",
                    "posts": [{"index": 1, "state": "pending",
                                "post_id": "POST1",
                                "posted_at": "2026-09-15T19:00:00+09:00"}]}
    problem = threadrun.run_problem(contradicted)
    assert problem and "食い違います" in problem


def test_置き場が通常ファイルなら不在と同じにしない(thth_root):
    """**「無い」と「あるが置き場として使えない」は別**（N2）。

    `state/threads` に通常ファイルが置かれていると `os.path.isdir` が偽になり、
    **記録が 1 件も無いのと同じ扱い**になっていた。承認は通り、公開は
    `FileExistsError` で落ちた——**不正な状態で承認だけ成功していた。**
    """
    path = os.path.join(thth_root, "state", "threads")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("ディレクトリではない")

    broken = threadrun.unreadable_runs()
    assert broken == [os.path.abspath(path)], broken

    # **無いこと自体は、これまでどおり止めない。**
    os.remove(path)
    assert threadrun.unreadable_runs() == []


# --- 再判定 R1（2026-09-12 Codex）: 残った痕跡と、原稿との突き合わせ -----------

def test_last_okとroot_post_idも公開の痕跡として見る(thth_root):
    """**`post_id` と `posted_at` を消すだけで検査を抜けられた**（R1）。

    公開の痕跡は 2 つだけではない。`last_ok=publish` は**公開まで進んだ**と
    言っているし、`root_post_id` は**先頭段が出た**と言っている。
    """
    base = {"run_id": "run-a", "account": "a", "rel_path": "q.md", "posts": [
        {"index": 1, "state": "pending", "post_id": None, "posted_at": None}]}

    assert threadrun.run_problem(base) is None          # 普通の未着手は通す

    with_last_ok = json.loads(json.dumps(base))
    with_last_ok["posts"][0]["last_ok"] = "publish"
    problem = threadrun.run_problem(with_last_ok)
    assert problem and "last_ok=publish" in problem

    with_root = json.loads(json.dumps(base))
    with_root["root_post_id"] = "POST1"
    problem = threadrun.run_problem(with_root)
    assert problem and "root_post_id" in problem


def test_止めすぎない境界(thth_root):
    """**外部レビューが「維持する」と明記した正常な形**（R1）。

    - `published` に `last_ok` は必須ではない
    - **確定した失敗のあとの `pending` に `container_id` が残るのは正当**
      （再試行してよい）
    """
    published_without_last_ok = {
        "run_id": "run-b", "account": "a", "rel_path": "q.md",
        "root_post_id": "POST1",
        "posts": [{"index": 1, "state": "published", "post_id": "POST1",
                    "posted_at": "2026-09-15T19:00:00+09:00",
                    "text_sha256": "a" * 64, "bundle_sha": "b" * 64,
                    "last_ok": None}]}
    assert threadrun.run_problem(published_without_last_ok) is None

    failed_then_pending = {
        "run_id": "run-c", "account": "a", "rel_path": "q.md",
        "posts": [{"index": 1, "state": "pending", "post_id": None,
                    "posted_at": None, "container_id": "container-1",
                    "last_ok": "container"}]}
    assert threadrun.run_problem(failed_then_pending) is None


def test_記録を綺麗に巻き戻しても原稿が公開済みと言っていれば止める(
        thth_root, thread_account):
    """**3 つの検査が互いを覆い隠していた**（2026-09-12 に気づいた）。

    外部レビューの反例は「痕跡を 1 つ残す」形なので、`last_ok` の検査を外しても
    `root_post_id` の検査や原稿との突き合わせが拾ってしまい、**どれを壊しても
    反例が通ってしまった。** ここでは**実行記録を矛盾なく巻き戻し**（痕跡を
    全部消す）、**原稿だけが公開済みと言っている**状態にして、
    **原稿との突き合わせだけ**が効くようにする。
    """
    import json as _json
    api = FakeAdapter()
    first = publish(thread_account, api, max_posts=1)
    assert len(api.calls) == 1
    row = threadrun.load(first[0].run_id)

    # **矛盾の無い pending に戻す**（痕跡を残さない）。
    post = row["posts"][0]
    post.update({"state": "pending", "post_id": None, "posted_at": None,
                  "reply_to": None, "container_id": None, "last_ok": None})
    row["root_post_id"] = None
    with open(threadrun.run_path(row["run_id"]), "w", encoding="utf-8") as f:
        _json.dump(row, f, ensure_ascii=False)

    # 記録の中身だけでは矛盾が無い——**原稿と突き合わせないと判らない。**
    assert threadrun.run_problem(row) is None
    assert threadrun.unreadable_runs() == []

    second = FakeAdapter()
    results = publish(thread_account, second, max_posts=1)
    assert second.calls == [], "原稿が公開済みと言っているのに再送した"
    assert any("巻き戻っています" in (r.reason or "") for r in results), results
