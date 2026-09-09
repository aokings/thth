"""指紋が食い違ったとき、5 項目のどれが違うのかをログ・runs・board に出す
（外部レビュー第 3 巡・持ち越し項目 C）。

`thth.core._fingerprint_matches()` は「一致しない」ことしか教えず、人が止まった
原因（本文か・account か・topic か…）をファイルを開いて自分で探すしかなかった。
`thth.core._mismatch_fields()`（`approval.compute_approved_components()` を使う）
が、公開直前に固定した 5 項目の値と、いまファイルにある値を項目ごとに突き合わせて
食い違ったキー名だけを返す。ここではそれが `ThrowResult.mismatch_fields`・
`state/<account>/runs-*.ndjson` の `mismatch_fields`・`report.board_summary()` の
`inflight_mismatch_fields` の 3 箇所に一貫して出ることを確かめる。
"""
from __future__ import annotations

import datetime

from tests.conftest import init_git_pair, run_git, run_thth, write_queue_file
from tests.test_atlas_review import setup_pair
from thth import accounts as accounts_mod
from thth import core
from thth import report as report_mod
from thth import runs as runs_mod
from thth.adapters import base as adapter_base

NOW = datetime.datetime.fromisoformat("2026-09-09T10:00:00+09:00")
REL = "docs/sns/queue/a.md"


def test_書き戻し前の食い違いはtopicだけを指す(isolated_account_factory):
    """本文はそのまま・topic だけを公開中に書き換えると、mismatch_fields は
    ['topic'] だけになる（body 等は巻き込まない）。"""
    account = isolated_account_factory(production=True)
    path = write_queue_file(account["queue_dir"], "a.md", fm_overrides={"topic": "苦味"})
    state_dir = accounts_mod.state_dir_for(account["name"])

    class _TopicMutatingSpy:
        def publish(self, post, *, dry_run, on_container_created=None):
            with open(path, encoding="utf-8") as f:
                text = f.read()
            assert "topic: 苦味" in text
            text = text.replace("topic: 苦味", "topic: 別トピック")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return adapter_base.PublishResult(
                post_id="MISMATCH_TOPIC", url=None, ts="2026-09-09T10:00:30+09:00")

    result = core.throw_once(
        account["name"], production_flag=True,
        adapter_factory=lambda *_: _TopicMutatingSpy(), now=NOW)

    assert result.exit_code == 1
    assert result.error == "text_mismatch_before_writeback"
    assert result.mismatch_fields == ["topic"], result.mismatch_fields
    assert "topic" in result.message

    # runs にも同じ内訳が残る。
    runs = runs_mod.read_runs(state_dir)
    assert runs, "runs が 1 件も無い"
    assert runs[-1]["mismatch_fields"] == ["topic"]
    assert runs[-1]["error"] == "text_mismatch_before_writeback"

    # board（inflight 経由）にも同じ内訳が出る。
    board = report_mod.board_summary()
    row = next(r for r in board["accounts"] if r["account"] == account["name"])
    assert row["inflight"] is not None and row["inflight"].endswith("a.md")
    assert row["inflight_mismatch_fields"] == ["topic"]


def test_rebase後の食い違いはreply_toだけを指す(tmp_path, isolated_account_factory):
    """`test_atlas_third_review.test_metadata_without_merge_conflict` と同じ形
    （別 clone から公開中に reply_to だけを書き換えて push する）だが、ここでは
    `mismatch_fields` の中身そのものを見る（reviewer の再現ファイルは assert を
    直さない約束なので、別ファイルとしてここに置く）。"""
    pair, account, path = setup_pair(tmp_path, isolated_account_factory)

    # `test_atlas_third_review.test_metadata_without_merge_conflict` と同じ下準備:
    # THTH が書き戻す出力欄（status・post_id・posted_at）と編集者が触る欄を
    # front-matter の中で空行を挟んで離しておく。そうしないと、同じ行の近くを
    # 両側が触って実際の merge conflict になり、`text_mismatch_after_rebase` まで
    # 届かずに push 失敗（別の error）で止まってしまう——安全側の停止ではあるが、
    # ここで見たいのは「rebase 後の指紋照合が reply_to を検知する」ことなので、
    # 衝突しない配置にして確かめる。
    raw = path.read_text().split("\n")
    end = raw.index("---", 1)
    output_keys = {"status", "post_id", "posted_at"}
    editorial = [l for l in raw[1:end] if l.partition(":")[0] not in output_keys]
    output = [l for l in raw[1:end] if l.partition(":")[0] in output_keys]
    path.write_text("\n".join(["---", *editorial, *[""] * 8, *output, "---", *raw[end + 1:]]))
    run_git(pair["work"], ["add", REL])
    run_git(pair["work"], ["commit", "-m", "Separate editorial and output fields"])
    run_git(pair["work"], ["push"])
    run_git(pair["seed"], ["pull", "--ff-only"])

    class _ReplyToMutatingEditor:
        def publish(self, post, **kw):
            editor_path = pair["seed"] + "/" + REL
            with open(editor_path, encoding="utf-8") as f:
                text = f.read()
            lines = [
                "reply_to: 999999" if line.startswith("reply_to:") else line
                for line in text.splitlines()
            ]
            with open(editor_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            run_git(pair["seed"], ["add", REL])
            run_git(pair["seed"], ["commit", "-m", "Change reply_to during publication"])
            run_git(pair["seed"], ["push"])
            return adapter_base.PublishResult("MISMATCH_REPLY", None, NOW.isoformat())

    result = core.throw_once(
        account["name"], production_flag=True,
        adapter_factory=lambda *_: _ReplyToMutatingEditor(), now=NOW)

    assert result.error == "text_mismatch_after_rebase", result
    assert result.mismatch_fields == ["reply_to"], result.mismatch_fields

    state_dir = accounts_mod.state_dir_for(account["name"])
    runs = runs_mod.read_runs(state_dir)
    assert runs[-1]["mismatch_fields"] == ["reply_to"]

    board = report_mod.board_summary()
    row = next(r for r in board["accounts"] if r["account"] == account["name"])
    assert row["inflight_mismatch_fields"] == ["reply_to"]
