"""外部レビュー第 4 巡（2026-09-10）の 2 件を実プロセス・実 git で再現する。

出所: 外部レビュアーが `361de01` を隔離して実行した再現コード
（`/tmp/thth-fourth-UdYUAx/REVIEW.md`・`independent-results.txt`）。統括は同じ筋を
自分の攻撃スクリプト（scratchpad/p1.sh）でも再現してから、ここに常設のテストとして
書き直した（レビュアーのコードをそのまま取り込んだ第 2・3 巡と違い、今回は日本語で
書き直している——攻撃の筋は同じだが、変種を 4 つに増やしているため）。

**P1**: `sync_repo()` が確かめるのは commit の一致（`HEAD == @{u}`）であって、
これから読むファイルの一致ではなかった。remote で承認を撤回し、それを正常に pull
できたあとでも、作業ツリーに撤回前の承認済みファイルが残っていれば、それが選ばれて
公開される。未 commit・staged・追跡外・symlink の 4 変種すべてで起きる。

**P2**: 「8 日前の承認済み」と「同じ本文を昨日投稿済み」が重なると、
`duplicate_text` で先に落ちて `stale`（要確認）まで届かず、board から要確認が
消えた（`approved_waiting: 1`・要確認 0 件＝何も問題が無いように見える）。
"""
from __future__ import annotations

import dataclasses
import datetime
import os
from pathlib import Path

import pytest

from tests.conftest import (approve_via_cli, commit_and_push_if_changed, commit_and_push_path,
                            init_git_pair, make_queue_text, run_git, run_thth)
from thth import core, report
from thth.adapters.base import PublishResult

NOW = datetime.datetime.fromisoformat("2026-09-09T10:00:00+09:00")
REL = "docs/sns/queue/a.md"
BODY = "# メモ\n\n" + "\n".join(f"文脈 {i}" for i in range(12)) + "\n\n## threads\n\n本文 A。\n"


class Spy:
    """公開されたら記録する（実際の API は呼ばない）。"""

    def __init__(self):
        self.calls = []

    def publish(self, post, **kw):
        self.calls.append(post.text)
        return PublishResult("FOURTH", None, NOW.isoformat())


def _setup(tmp_path, factory, *, ensure_committed=True):
    """承認済み・push 済みの投稿 1 本と、その clone 一式を用意する。

    `ensure_committed`（既定 True）は「`thth approve` が commit しなかった場合は
    ここで commit・push する」。**修正前の commit にこのファイルを当てて落ちること
    を確かめられるようにするため**（規約 9）——修正前の `thth approve` は commit
    しないので、これが無いと P1 の再現が「pull が汚れた作業ツリーで失敗した」と
    いう別の理由で止まってしまい、塞ぎたい穴を突けない。承認が commit として
    残ること自体は `test_approveは承認をcommitとしてpushする` が別に見る。
    """
    pair = init_git_pair(tmp_path, seed_content=make_queue_text({"status": "draft"}, body=BODY))
    account = factory(repo_dir=pair["work"], production=True, quiet_hours=None,
                       min_interval_hours=0)
    path = Path(pair["work"]) / REL
    approved = approve_via_cli(str(path))
    assert approved.returncode == 0, approved.stderr
    if ensure_committed:
        commit_and_push_if_changed(pair["work"], REL, "承認（fixture の補い）")
    return pair, account, path


def _withdraw_on_remote(pair):
    """別の clone（masaru の手元に相当）で承認を撤回して push する。"""
    run_git(pair["seed"], ["pull", "--ff-only"])
    editor = Path(pair["seed"]) / REL
    editor.write_text(editor.read_text().replace("status: approved", "status: draft"))
    run_git(pair["seed"], ["add", REL])
    run_git(pair["seed"], ["commit", "-m", "承認を撤回"])
    run_git(pair["seed"], ["push"])


@pytest.mark.parametrize("variant", ["未commit", "staged", "追跡外", "symlink"])
def test_復元された古い承認済みファイルは公開されない(tmp_path, isolated_account_factory, variant):
    pair, account, path = _setup(tmp_path, isolated_account_factory)
    approved_text = path.read_text()
    _withdraw_on_remote(pair)

    # 1 回目: 撤回を pull して、何も出ないことを確かめる（作業ツリーは綺麗）。
    spy = Spy()
    first = core.throw_once(account["name"], production_flag=True,
                             adapter_factory=lambda *_: spy, now=NOW)
    assert not spy.calls, dataclasses.asdict(first)
    assert run_git(pair["work"], ["rev-parse", "HEAD"]).stdout == \
        run_git(pair["work"], ["rev-parse", "@{u}"]).stdout, "HEAD は upstream に追いついている"

    # 2 回目: 撤回前の承認済みファイルを作業ツリーに戻す（再承認はしない）。
    target = path
    if variant == "未commit":
        path.write_text(approved_text)
    elif variant == "staged":
        path.write_text(approved_text)
        run_git(pair["work"], ["add", REL])
    elif variant == "追跡外":
        target = Path(pair["work"]) / "docs/sns/queue/b.md"
        target.write_text(approved_text)  # HEAD に一度も入っていないファイル
    else:
        outside = tmp_path / "退避" / "a.md"
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_text(approved_text)
        path.unlink()
        path.symlink_to(outside)

    spy2 = Spy()
    result = core.throw_once(account["name"], production_flag=True,
                              adapter_factory=lambda *_: spy2, now=NOW)
    assert not spy2.calls, f"撤回済みの本文を公開した: {dataclasses.asdict(result)}"
    reasons = {r["file"]: r["reason"] for r in (result.rejections or [])}
    assert reasons.get(os.path.basename(str(target))) == "unverified_content", reasons
    # 作業中の変更は消さない（fail-closed だが破壊はしない）。
    assert target.exists() and target.read_text() == approved_text


def test_同期したcommitと一致する承認はこれまでどおり公開される(tmp_path, isolated_account_factory):
    """過剰に塞いでいないことの確認（`unverified_content` が正常系を止めない）。"""
    pair, account, path = _setup(tmp_path, isolated_account_factory)
    spy = Spy()
    result = core.throw_once(account["name"], production_flag=True,
                              adapter_factory=lambda *_: spy, now=NOW)
    assert spy.calls == ["本文 A。"], dataclasses.asdict(result)
    assert result.post_id == "FOURTH"


def test_approveは承認をcommitとしてpushする(tmp_path, isolated_account_factory):
    """承認がファイルの中だけに存在する状態を作らない（P1 の裏返し）。

    select は「同期を確認した commit の中身と一致するファイル」しか候補にしない
    ので、`thth approve` が commit・push しなければ承認は永久に出ない。
    """
    pair, account, path = _setup(tmp_path, isolated_account_factory, ensure_committed=False)
    in_origin = run_git(pair["bare"], ["show", "main:" + REL]).stdout
    assert "status: approved" in in_origin
    assert "approved_sha:" in in_origin
    assert run_git(pair["work"], ["status", "--porcelain"]).stdout.strip() == ""


def test_期限切れと重複が重なっても要確認が消えない(tmp_path, isolated_account_factory):
    """外部レビュー第 4 巡 P2。

    - `a.md`: 8 日前が予定の承認済み（`stale_days: 7` を超えている＝要確認）
    - `b.md`: 昨日投稿済み・本文は `a.md` と同じ（＝重複本文）

    `duplicate_text` で先に落ちても、board の要確認から消えてはいけない。
    """
    body = "## threads\n\n重なる本文。\n"
    pair = init_git_pair(tmp_path, seed_content=make_queue_text(
        {"publish_at": "2026-09-01T08:00:00+09:00"}, body=body))
    account = isolated_account_factory(repo_dir=pair["work"])
    posted = Path(pair["work"]) / "docs/sns/queue/b.md"
    posted.write_text(make_queue_text({
        "status": "posted", "post_id": "OLD", "publish_at": "2026-09-08T08:00:00+09:00",
        "posted_at": "2026-09-08T08:00:30+09:00"}, body=body))
    commit_and_push_path(str(posted), message="昨日の投稿")

    row = next(r for r in report.board_summary()["accounts"]
               if r["account"] == account["name"])
    assert row["approved_waiting"] == 1
    assert row["needs_review"], f"重複と期限切れが重なると要確認が消えた: {row}"
    assert {item["file"] for item in row["needs_review"]} == {"a.md"}
