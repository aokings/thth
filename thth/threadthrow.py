"""スレッド連投を出す（設計 §3・§4・§6・工程 4〜5）。

**段ごとにロックを取り直す。** 束の 4 本を 1 つのロックの中で出すと、
`revoke`（同じ clone ロックを取る）が割り込めず、**「1 本目のあとで止める」が
働かない**（Codex 指摘）。

```
段ごとに:
  ロックを取る → 同期 → 撤回と承認を再検査 → 期限を見る
  → 公開要求の直前にもう一度期限を見る → 公開 → 書き戻し → push → ロックを放す
```

**state に「進行中」と書くだけでは排他にならない。** 実行の取得・再開・段の
進行は**すべてロックの中で**判定して永続状態を更新する（Codex 最終条件 3）。
"""
from __future__ import annotations

import dataclasses
import os

from . import accounts as accounts_mod
from . import approval as approval_mod
from . import bundle as bundle_mod
from . import inflight as inflight_mod
from . import jst
from . import lock as lock_mod
from . import queuefile
from . import redact as redact_mod
from . import threadrun
from . import writeback as writeback_mod
from .adapters import base as adapter_base

# 1 回の起動での上限（設計 §6・Codex 最終条件 4）。
DEFAULT_MAX_BUNDLES_PER_RUN = 1
DEFAULT_MAX_POSTS_PER_RUN = 4


@dataclasses.dataclass
class StepResult:
    """1 段ぶんの結果。**「出た／出ない」ではなく「何が確定したか」。**"""

    action: str          # published / skipped / stopped / unresolved / failed
    index: int | None
    reason: str
    run_id: str | None = None
    post_id: str | None = None


def publish_bundle(account_name: str, rel_path: str, *, adapter_factory,
                    now=None, log=print, production: bool = True,
                    max_posts: int | None = None) -> list:
    """1 つの束を、出せるところまで出す。**段ごとに確認点を置く。**

    戻り値は `StepResult` の配列（1 段につき 1 つ）。
    """
    account_cfg = accounts_mod.load_account(account_name)
    limit = max_posts if max_posts is not None else int(
        account_cfg.get("max_posts_per_run", DEFAULT_MAX_POSTS_PER_RUN))
    results: list = []

    for _ in range(max(1, limit)):
        result = _one_step(account_name, account_cfg, rel_path,
                            adapter_factory=adapter_factory, now=now, log=log,
                            production=production)
        results.append(result)
        if result.action != "published":
            break
        # **出し切ったらそこで終わる。** 上限まで回し続けると、次の周で
        # 「すでに実行済みです」が 1 件余分に並ぶ（読み手が混乱する）。
        row = threadrun.load(result.run_id) if result.run_id else None
        if row is not None and threadrun.is_finished(row):
            break
    return results


def _one_step(account_name, account_cfg, rel_path, *, adapter_factory, now, log,
               production) -> StepResult:
    """1 段ぶん。**ロックを取って、放すまで。**"""
    repo_dir = account_cfg["repo_dir"]
    state_dir = accounts_mod.state_dir_for(account_name)
    repo_lock = lock_mod.AccountLock(accounts_mod.repo_lock_path_for(repo_dir))
    try:
        repo_lock.acquire()
    except lock_mod.LockBusy:
        return StepResult("skipped", None, "ほかの実行がこの repo を使っています")

    try:
        return _locked_step(account_name, account_cfg, rel_path, repo_dir,
                             state_dir, adapter_factory=adapter_factory,
                             now=now, log=log, production=production)
    finally:
        repo_lock.release()


def _locked_step(account_name, account_cfg, rel_path, repo_dir, state_dir, *,
                  adapter_factory, now, log, production) -> StepResult:
    now = now if now is not None else jst.now_jst()

    # **未解決の公開結果がある間は、同じ account の別投稿へ進まない**
    # （Codex 最終条件 3）。自分の束の未解決もここで止まる。
    blocked = threadrun.has_unresolved(account_name)
    if blocked:
        ids = [r["run_id"] for r in blocked]
        return StepResult("stopped", None,
                           f"結果が確定していない公開があります: {ids}。"
                           f"人が Threads を見て判断するまで進みません")
    if inflight_mod.read(state_dir):
        return StepResult("stopped", None,
                           "inflight が残っています（前の公開の結果が未確定）")

    ok, err, tree_sha = writeback_mod.sync_repo(repo_dir)
    if not ok:
        return StepResult("skipped", None, f"同期できません: {err}")

    path = os.path.join(repo_dir, rel_path)
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        return StepResult("skipped", None, f"原稿を読めません: {e}")

    if not writeback_mod.matches_synced_commit(repo_dir, path, tree_sha=tree_sha,
                                                disk_bytes=raw):
        return StepResult("skipped", None,
                           "原稿が同期した commit と一致しません（unverified_content）")

    text = raw.decode("utf-8")
    b = bundle_mod.parse_text(text, path)
    problems = bundle_mod.check(b, account_cfg=account_cfg)
    hard = [p for p in problems if not p.startswith("warning:")]
    if hard:
        return StepResult("skipped", None, f"形式検査で止まりました: {hard[:2]}")

    fm = b.front_matter
    if fm.get("account") != account_name:
        return StepResult("skipped", None, "account が違います")
    if fm.get("status") != "approved":
        return StepResult("skipped", None, f"status が approved ではありません（{fm.get('status')}）")

    # **終わった実行も見る。** これを見ないと、出し切ったあとにもう一度
    # 最初から出してしまう（実装中に実際に踏んだ）。
    latest = threadrun.find_latest(account_name, rel_path)
    run = None if (latest is None or threadrun.is_finished(latest)) else latest
    if fm.get("revoked_at"):
        if run is not None and not run.get("stop_confirmed_at"):
            threadrun.confirm_stop(run, f"原稿が撤回されています（{fm['revoked_at']}）",
                                    now=now)
        return StepResult("stopped", None,
                           f"撤回されています（{fm.get('revoked_at')}）",
                           run_id=(run or {}).get("run_id"))

    segments = b.segments
    frozen = threadrun.frozen_records(run) if run else []
    expected = approval_mod.compute_bundle_sha(
        segments=segments, account=account_name, topic=fm.get("topic"),
        publish_at=fm.get("publish_at"), continue_until=fm.get("continue_until"),
        frozen=frozen)
    if fm.get("approved_sha") != expected:
        return StepResult("skipped", None,
                           "approved_sha が中身と合いません（approval_stale）")

    # **公開済み部分の凍結**（設計 §5・Codex 最終条件 2）。
    if run is not None:
        drift = _frozen_drift(run, segments)
        if drift:
            return StepResult("skipped", None,
                               f"公開済みの段の本文が変わっています: {drift}")

    start_at = queuefile.parse_publish_at(fm["publish_at"])
    if now < start_at:
        return StepResult("skipped", None, f"まだ時刻前です（{fm['publish_at']}）")
    until = queuefile.parse_publish_at(fm["continue_until"])
    if now > until:
        if run is not None and not run.get("stop_confirmed_at"):
            threadrun.confirm_stop(run, f"継続期限を過ぎました（{fm['continue_until']}）",
                                    now=now)
        return StepResult("stopped", None,
                           f"継続期限を過ぎています（{fm['continue_until']}）",
                           run_id=(run or {}).get("run_id"))

    if run is None and latest is not None:
        # **同じ原稿で 2 つ目の実行を自動では始めない**（設計 §2.3）。
        # 「最初から別スレッドとして再投稿する」のは**明示の操作**であって、
        # timer が勝手にやってよいことではない。
        done = threadrun.summary(latest)
        return StepResult("stopped", None,
                           f"この原稿はすでに実行済みです"
                           f"（run_id: {latest['run_id']}・公開: {done['published']}・"
                           f"停止: {latest.get('stop_reason') or 'なし'}）。"
                           f"出し直すなら別の原稿にしてください",
                           run_id=latest["run_id"])

    if run is None:
        # **公開要求より前に run_id を発行して永続化する**（Codex 最終条件 1）。
        run = threadrun.start(
            account=account_name, rel_path=rel_path, bundle_sha=expected,
            segment_count=len(segments),
            segment_shas=[approval_mod.segment_sha(s) for s in segments],
            continue_until=fm["continue_until"],
            by=fm.get("approved_by") or "unknown", now=now)

    index = threadrun.next_index(run)
    if index is None:
        return StepResult("stopped", None, "この束はもう進みません",
                           run_id=run["run_id"])

    try:
        parent = threadrun.resolve_parent(run, index, draft_posts=b.posts,
                                           account=account_name)
    except threadrun.RunError as e:
        return StepResult("skipped", index, f"親を決められません: {e}",
                           run_id=run["run_id"])

    section = segments[index - 1]
    topic = queuefile.normalize_topic(fm.get("topic")) if index == 1 else None

    if not production:
        log(f"dry-run: {index} 段目（返信先 {parent or 'なし'}）")
        log(section)
        return StepResult("skipped", index, "dry-run", run_id=run["run_id"])

    # **公開要求の直前に、もう一度いまの時刻で期限を見る**（Codex 最終条件 3）。
    # 30 秒待機や同期のあいだに越えることがある。
    now2 = jst.now_jst()
    if now2 > until:
        threadrun.confirm_stop(run, "公開要求の直前に継続期限を越えました", now=now2)
        return StepResult("stopped", index,
                           f"公開の直前に継続期限を越えました（{fm['continue_until']}）",
                           run_id=run["run_id"])

    started = jst.iso()
    inflight_mod.write(state_dir, file=path, started=started, container_id=None,
                        body_hash=approval_mod.compute_body_hash(section),
                        approved_fingerprint=expected)
    threadrun.mark(run, index, threadrun.REQUESTED, reply_to=parent or None)

    token = accounts_mod.load_token(account_cfg)
    adapter = adapter_factory(account_cfg, token)
    post = adapter_base.Post(text=section, reply_to=parent or None, topic=topic)

    def on_container_created(container_id):
        inflight_mod.update(state_dir, container_id=container_id)
        threadrun.mark(run, index, threadrun.REQUESTED, container_id=container_id)

    result = adapter.publish(post, dry_run=False,
                              on_container_created=on_container_created)

    if result.error or not result.post_id:
        err = redact_mod.redact(result.error or "不明なエラー")
        if result.failure == "publish_ambiguous":
            # **出たか分からない。自動で再送しない。後続も止める。**
            threadrun.mark(run, index, threadrun.UNRESOLVED, note=err)
            log(f"公開の結果が分かりません（{index} 段目）。inflight を残します")
            return StepResult("unresolved", index,
                               f"公開の結果が分かりません: {err}",
                               run_id=run["run_id"])
        inflight_mod.clear(state_dir)
        threadrun.mark(run, index, threadrun.PENDING, note=err)
        return StepResult("failed", index, f"公開に失敗しました: {err}",
                           run_id=run["run_id"])

    # **公開は成功した。ここから先が失敗しても再公開しない。**
    threadrun.mark(run, index, threadrun.PUBLISHED, post_id=result.post_id,
                    posted_at=result.ts or jst.iso(), reply_to=parent or None,
                    last_ok="publish")
    inflight_mod.clear(state_dir)

    updated = bundle_mod.set_post_fields(text, index, {
        "post_id": result.post_id,
        "posted_at": result.ts or jst.iso(),
        "reply_to": parent or "",
        "run_id": run["run_id"],
        "bundle_sha": expected,
        "text_sha256": approval_mod.segment_sha(section),
    })
    with open(path, "w", encoding="utf-8") as f:
        f.write(updated)

    pushed, push_err = writeback_mod.commit_and_push(
        repo_dir, rel_path=rel_path,
        message=f"THTH: {rel_path} の {index} 段目を公開（{result.post_id}）")
    if not pushed:
        # **公開済みとして保持し、後続を止める。再公開しない**（設計 §3.1）。
        threadrun.mark(run, index, threadrun.PUBLISHED, note=f"push 失敗: {push_err}")
        threadrun.confirm_stop(run, f"書き戻しの push に失敗しました: {push_err}",
                                now=jst.now_jst())
        return StepResult("stopped", index,
                           f"公開はできましたが push に失敗しました（{push_err}）。"
                           f"**再公開しません**",
                           run_id=run["run_id"], post_id=result.post_id)

    return StepResult("published", index, f"{index} 段目を公開しました",
                       run_id=run["run_id"], post_id=result.post_id)


def _frozen_drift(run: dict, segments: list) -> list:
    """公開済みの段の本文が変わっていないか（設計 §5）。"""
    drift = []
    for post in run["posts"]:
        if post["state"] != threadrun.PUBLISHED:
            continue
        i = post["index"]
        if i > len(segments):
            drift.append(i)
            continue
        if approval_mod.segment_sha(segments[i - 1]) != post["text_sha256"]:
            drift.append(i)
    return drift
