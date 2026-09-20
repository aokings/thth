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

from . import api_diagnostic

import dataclasses
import os

from . import accounts as accounts_mod
from . import approval as approval_mod
from . import bundle as bundle_mod
from . import inflight as inflight_mod
from . import media_delivery
from . import jst, leave_gate
from . import lock as lock_mod
from . import postid as postid_mod
from . import queuefile
from . import forms as forms_mod
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
    api_diagnostic: dict | None = None
    file: str | None = None


def due_bundles(account_cfg: dict, *, now, tree_sha) -> list:
    """出せる束の `rel_path` を並べる（設計 §6・独立検収 P1-1）。

    **製品の経路から呼ぶための口。** これが無かったので、`thth throw` は
    v2 を「出すものが無い」で素通りしていた——**モジュールを書いて、それを
    直接呼ぶテストだけ書いていた**（規約 11 そのもの）。
    """
    from . import core as core_mod

    repo_dir = account_cfg.get("repo_dir") or ""
    account_name = account_cfg["account"]
    out = []
    for qf in core_mod.list_queue_files(account_cfg, tree_sha=tree_sha):
        if not qf.malformed:
            continue                      # v1 は従来の経路が扱う
        try:
            with open(qf.path, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        if not bundle_mod.is_bundle_text(text):
            continue
        b = bundle_mod.parse_text(text, qf.path)
        if b.malformed or b.front_matter.get("account") != account_name:
            continue
        if b.front_matter.get("status") != "approved":
            continue
        if b.front_matter.get("revoked_at"):
            continue
        if bundle_mod.check(b, account_cfg=account_cfg):
            hard = [p for p in bundle_mod.check(b, account_cfg=account_cfg)
                    if not p.startswith("warning:")]
            if hard:
                continue
        try:
            if now < queuefile.parse_publish_at(b.front_matter["publish_at"]):
                continue
        except (KeyError, ValueError, TypeError):
            continue
        rel = os.path.relpath(os.path.realpath(qf.path),
                               os.path.realpath(repo_dir))
        # **もう出し切った束は候補にしない**（毎回「実行済みです」と並ぶのを防ぐ）。
        known = threadrun.find_latest(account_name, rel)
        if known is not None and threadrun.is_complete(known):
            continue
        out.append(rel)
    return sorted(out)


def has_bundle_files(account_cfg: dict) -> bool:
    """queue に `thth: 2` の原稿が 1 つでもあるか（**同期しない・読むだけ**）。"""
    repo_dir = account_cfg.get("repo_dir") or ""
    queue_dir = os.path.join(repo_dir, account_cfg.get("queue_dir") or
                              "docs/sns/queue")
    if not os.path.isdir(queue_dir):
        return False
    for name in sorted(os.listdir(queue_dir)):
        if not name.endswith(".md"):
            continue
        try:
            with open(os.path.join(queue_dir, name), encoding="utf-8") as f:
                head = f.read(400)
        except (OSError, UnicodeDecodeError):
            continue
        if bundle_mod.is_bundle_text(head + "\n---\n"):
            return True
    return False


def run_for_account(account_name: str, *, adapter_factory, now=None, log=print,
                     production: bool = True) -> list:
    """その account の束を、上限まで進める（**製品の入口**・独立検収 P1-1）。

    `thth throw` / `thth run` / timer からここへ来る。**新しい処理だけを
    直接実行できても完成ではない。**
    """
    account_cfg = accounts_mod.load_account(account_name)
    now = now if now is not None else jst.now_jst()

    repo_dir = account_cfg["repo_dir"]
    # **束が 1 つも無ければ、同期もロックもしない**（v1 の経路に手を出さない）。
    # ここを省いたら、束を持たない account でも同期が 1 回増え、v1 の
    # 第 5 巡テスト（同期の直後に HEAD が動く筋）が別の場所で消費された。
    # **新しい機能が、触っていないはずの経路の観測できる順序を変えていた。**
    if not has_bundle_files(account_cfg):
        return []

    repo_lock = lock_mod.AccountLock(accounts_mod.repo_lock_path_for(repo_dir))
    try:
        repo_lock.acquire()
    except lock_mod.LockBusy:
        return []
    try:
        ok, _err, tree_sha = writeback_mod.sync_repo(repo_dir)
        if not ok:
            return []
        rels = due_bundles(account_cfg, now=now, tree_sha=tree_sha)
    finally:
        repo_lock.release()

    limit = int(account_cfg.get("max_bundles_per_run", DEFAULT_MAX_BUNDLES_PER_RUN))
    results = []
    for rel in rels[:max(1, limit)]:
        results += publish_bundle(account_name, rel, adapter_factory=adapter_factory,
                                   now=now, log=log, production=production)
    return results


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

    # **読めない実行記録があるなら、その手前で止まる**（監査 2026-09-11）。
    # `has_unresolved()` は読めない記録を飛ばすので、**壊れた記録 1 件で
    # 下の関門が開いていた**（F1 と同じ「読めない＝問題なし」）。
    unreadable = threadrun.unreadable_runs()
    if unreadable:
        return StepResult("stopped", None, threadrun.unreadable_error(unreadable))

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
    # **原稿が名乗る実行を照合する**（独立検収 P1-4）。パスだけで身元を決めない。
    claimed = None
    for post in b.posts:
        rid = threadrun._unquote(post.get("run_id"))
        if rid:
            claimed = threadrun.find_by_run_id(rid) or claimed
            break
    known = claimed or latest
    identity = threadrun.identity_error(known, b.posts, rel_path=rel_path,
                                         account=account_name)
    if identity:
        return StepResult("stopped", None, identity,
                           run_id=(known or {}).get("run_id"))
    run = None if (known is None or threadrun.is_finished(known)) else known
    if fm.get("revoked_at"):
        if run is not None and not run.get("stop_confirmed_at"):
            threadrun.confirm_stop(run, f"原稿が撤回されています（{fm['revoked_at']}）",
                                    now=now)
        return StepResult("stopped", None,
                           f"撤回されています（{fm.get('revoked_at')}）",
                           run_id=(run or {}).get("run_id"))

    # **原稿が「公開済み」と言っている段を、実行記録が未着手として扱わない**
    # （再判定 R1・2026-09-12 Codex の受け入れ条件 3）。原稿の `post_id` は
    # 公開後に書き戻されるので、**それが残っているのに run 側が pending なら、
    # どちらかが巻き戻っている。** 記録の中身だけでは判らないので、
    # ここ（原稿と突き合わせられる場所）で見る。
    #
    # **逆向き（run は published・原稿に post_id が無い）は止めない**——
    # 書き戻しが途中で落ちた正当な状態で、`identity_error()` の担当。
    if run is not None:
        run_posts = {p.get("index"): p for p in run.get("posts") or []}
        for i, draft_post in enumerate(b.posts, start=1):
            claimed = threadrun._unquote(draft_post.get("post_id"))
            recorded = run_posts.get(i) or {}
            if claimed and recorded.get("state") != threadrun.PUBLISHED:
                return StepResult(
                    "stopped", None,
                    f"{i} 段目は原稿に post_id（{claimed}）が書かれているのに、"
                    f"実行記録では {recorded.get('state')!r} です。"
                    f"**どちらかが巻き戻っています**——Threads を実際に見て、"
                    f"その段が出ているかを確かめてください",
                    run_id=run.get("run_id"))

    segments = bundle_mod.effective_segments(
        b.segments, account_cfg, fm.get("topic"))
    frozen = threadrun.frozen_records(run) if run else []
    from . import media as media_mod
    try:
        manifests = [media_mod.manifest_for(post, account_cfg) for post in b.posts]
    except media_mod.MediaError:
        return StepResult("skipped", None, "approval_stale: attachment changed or unreadable")
    expected = approval_mod.compute_bundle_sha(
        segments=segments, account=account_name, topic=fm.get("topic"),
        publish_at=fm.get("publish_at"), continue_until=fm.get("continue_until"),
        frozen=frozen, media_manifest=manifests)
    if fm.get("approved_sha") != expected:
        return StepResult("skipped", None,
                           "approved_sha が中身と合いません（approval_stale）")

    media_errors=[media_delivery.error_for(account_cfg,m) for m in manifests]
    if any(media_errors):
        return StepResult("skipped", None, next(x for x in media_errors if x))

    def media_stale(require_approved=True):
        try:
            with open(path,encoding='utf-8') as stream:current=bundle_mod.parse_text(stream.read(),path)
            _,problems=bundle_mod.load_segments(current,account_cfg['media'])
            if problems or current.front_matter.get('account')!=account_name:return True
            if require_approved and (current.front_matter.get('status')!='approved' or current.front_matter.get('revoked_at')):return True
            now_manifests=[media_mod.manifest_for(row,account_cfg) for row in current.posts]
            actual=approval_mod.compute_bundle_sha(
                segments=bundle_mod.effective_segments(current.segments,account_cfg,current.front_matter.get('topic')),
                account=account_name,topic=current.front_matter.get('topic'),
                publish_at=current.front_matter.get('publish_at'),continue_until=current.front_matter.get('continue_until'),
                frozen=frozen,media_manifest=now_manifests)
            return current.malformed or actual!=expected or current.front_matter.get('approved_sha')!=expected
        except (OSError,ValueError,TypeError):return True

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

    # **停止と完了は別物**（独立検収 P2-6）。停止している実行は、新しい承認版が
    # 成立していれば**同じ run_id のまま**再開できる。完了した束は再開しない。
    if run is None and known is not None and threadrun.is_stopped(known):
        if threadrun.has_unresolved_post(known):
            return StepResult("stopped", None,
                               "結果の分からない公開があるので再開しません",
                               run_id=known["run_id"])
        try:
            run = threadrun.resume(known, bundle_sha=expected,
                                    continue_until=fm["continue_until"],
                                    by=fm.get("approved_by") or "unknown", now=now)
            log(f"停止していた実行を再開します（{known['run_id']}）")
        except threadrun.RunError as e:
            return StepResult("stopped", None, f"再開できません: {e}",
                               run_id=known["run_id"])

    if run is None and known is not None:
        # **同じ原稿で 2 つ目の実行を自動では始めない**（設計 §2.3）。
        # 「最初から別スレッドとして再投稿する」のは**明示の操作**であって、
        # timer が勝手にやってよいことではない。
        done = threadrun.summary(known)
        return StepResult("stopped", None,
                           f"この原稿はすでに出し切っています"
                           f"（run_id: {known['run_id']}・公開: {done['published']}）。"
                           f"出し直すなら別の原稿にしてください",
                           run_id=known["run_id"])

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

    # **これから送る版を、送る前に記録へ固定する**（独立検収 P1-5）。
    # 再承認で本文を直しても記録が古いままだと、**実際に送った本文と永続記録が
    # 食い違い**、次の凍結検査が「旧本文を正本」として比べて、正しく送った
    # 新本文を変更扱いにする。**公開済みの段の過去の承認版は動かさない。**
    labels = {"form": fm.get("form"), "outlet": fm.get("outlet"),
              "numbering": fm.get("numbering"),
              "vocabulary_version": forms_mod.VOCABULARY_VERSION}
    threadrun.snapshot_pending(
        run, bundle_sha=expected,
        segment_shas=[approval_mod.segment_sha(s) for s in segments],
        labels=labels, now=now)

    started = jst.iso()
    inflight_mod.write(state_dir, file=path, started=started, container_id=None,
                        body_hash=approval_mod.compute_body_hash(section),
                        approved_fingerprint=expected)
    threadrun.mark(run, index, threadrun.REQUESTED, reply_to=parent or None)

    token = accounts_mod.load_token(account_cfg)
    adapter = leave_gate.bind(adapter_factory(account_cfg, token), account_cfg)
    post = adapter_base.Post(text=section, reply_to=parent or None, topic=topic,
                             hashtags_allowed=bool(account_cfg.get("hashtags", True)))

    def on_container_created(container_id):
        inflight_mod.update(state_dir, container_id=container_id)
        threadrun.mark(run, index, threadrun.REQUESTED, container_id=container_id)

    def before_publish():
        """**実際の公開要求の直前**（container 作成と 30 秒待機のあと）。

        独立検収 P1-3: 以前はここが `adapter.publish()` の**外側**にあり、
        **待機のあいだに継続期限を越えても公開していた。**
        """
        if any(manifests) and media_stale():return 'approval_stale'
        if jst.now_jst() > until:
            return (f"継続期限を越えました（{fm['continue_until']}）。"
                     f"公開要求は送っていません")
        return None

    result = media_delivery.publish(adapter,post,cfg=account_cfg,
        fm=b.posts[index-1],manifest=manifests[index-1],state_dir=state_dir,
        on_container_created=on_container_created,before_publish=before_publish)

    if result.failure == "publish_vetoed":
        # **container は作ったが公開していない。** 出ていないので inflight は消す。
        inflight_mod.clear(state_dir)
        threadrun.mark(run, index, threadrun.PENDING, note=result.error)
        threadrun.confirm_stop(run, result.error or "公開直前の関門で止めました",
                                now=jst.now_jst())
        return StepResult("stopped", index, result.error or "公開直前に止めました",
                           run_id=run["run_id"])

    if result.error or not result.post_id:
        detail_data = api_diagnostic.clean(result.api_diagnostic)
        err = redact_mod.redact(result.error or "不明なエラー")
        if result.failure in ("publish_ambiguous", "media_ambiguous", "media_held"):
            # **出たか分からない。自動で再送しない。後続も止める。**
            threadrun.mark(run, index, threadrun.UNRESOLVED, note=err, api_diagnostic=detail_data)
            log(f"公開の結果が分かりません（{index} 段目）。inflight を残します")
            return StepResult("unresolved", index,
                               f"公開の結果が分かりません: {err}",
                               run_id=run["run_id"], api_diagnostic=detail_data,
                               file=os.path.join(account_cfg["repo_dir"], rel_path))
        inflight_mod.clear(state_dir)
        threadrun.mark(run, index, threadrun.PENDING, note=err, api_diagnostic=detail_data)
        return StepResult("failed", index, f"公開に失敗しました: {err}",
                           run_id=run["run_id"], api_diagnostic=detail_data,
                               file=os.path.join(account_cfg["repo_dir"], rel_path))

    # **公開は成功した。ここから先が失敗しても再公開しない。**
    #
    # **媒体が返した `post_id` を、確かめずに書き戻さない**（セキュリティ監査
    # 2 回目・P1-1）。単発（`core._throw_chosen()`）には 2026-09-14 に入れた
    # 検査が、**連投にだけ無かった**——`result.post_id` が素通しで
    # `bundle.set_post_fields()` に渡り、改行入りの `id` で束の front-matter に
    # 任意の行（`status: approved` 等）が入った。書いたものはそのまま commit・
    # push されるので、以後その account の `run` は `sync_repo` で止まる。
    #
    # 扱いは `publish_ambiguous` と同じ（§3.5・単発と揃える）。**出たことは
    # 判っているが、それを記録できない**——inflight を残して人を呼び、後続の段は
    # 進めない（親の ID が信用できないので、繋ぎ先が決まらない）。
    if not postid_mod.is_usable(result.post_id):
        msg = ("媒体が返した post_id が台帳に書けない形です"
               "（書き戻しません・再公開もしません・inflight を残します）")
        inflight_mod.update(state_dir, mismatch_fields=["post_id"])
        threadrun.mark(run, index, threadrun.UNRESOLVED, note="unusable_post_id")
        log(f"{msg}（{index} 段目）")
        return StepResult("unresolved", index, msg, run_id=run["run_id"])

    posted_at = result.ts or jst.iso()
    sent_sha = approval_mod.segment_sha(section)
    try:
        threadrun.mark(run, index, threadrun.PUBLISHED, post_id=result.post_id,
                        posted_at=posted_at, reply_to=parent or None,
                        text_sha256=sent_sha, bundle_sha=expected, last_ok="publish",
                        **({"media":result.media} if result.media else {}))
    except (OSError,ValueError):
        if not manifests[index-1]:raise
        return StepResult("unresolved",index,"media_record_unconfirmed: 公開後の記録未確定。inflight を保持し再送しません",run_id=run["run_id"],post_id=result.post_id)

    updated = bundle_mod.set_post_fields(text, index, {
        "post_id": result.post_id,
        "posted_at": posted_at,
        "reply_to": parent or "",
        "run_id": run["run_id"],
        "bundle_sha": expected,
        "text_sha256": sent_sha,
    })
    with open(path, "w", encoding="utf-8") as f:
        f.write(updated)

    mismatch = {}

    def validate_after_rebase():
        """**rebase のあと・push の前に、送ったものと一致するかを見る**
        （独立検収 P1-2）。

        v1 で直した経路が v2 に引き継がれていなかった。**公開の最中に別 clone が
        本文を変えると、投稿 A の ID を本文 B に付けて push していた。**
        """
        # **ディスクを読み直す。** 取り込み（rebase）で中身が変わりうるので、
        # 書いたつもりの文字列ではなく**いまそこにあるもの**を見る。
        try:
            with open(path, encoding="utf-8") as f:
                current = f.read()
        except OSError as e:
            mismatch["why"] = f"取り込みのあと原稿を読めません: {e}"
            return False
        after = bundle_mod.parse_text(current, path)
        why = None
        if after.malformed:
            why = f"取り込みのあと原稿が読めなくなりました（{bundle_mod.why_malformed(current)}）"
        elif any(manifests) and media_stale(require_approved=False):
            why = 'approval_stale: attachment intent changed'
        elif after.front_matter.get("approved_sha") != expected:
            why = "取り込みのあと承認版が変わっています"
        else:
            segs, problems = bundle_mod.load_segments(after, account_cfg["media"])
            if problems:
                why = f"取り込みのあと段が読めません: {problems[0]}"
            elif len(segs) != len(segments):
                why = f"取り込みのあと段の数が変わりました（{len(segs)}）"
            elif approval_mod.segment_sha(bundle_mod.effective_segments(
                    segs, account_cfg, after.front_matter.get("topic"))[index - 1]) != sent_sha:
                why = (f"取り込みのあと {index} 段目の本文が変わっています"
                        f"——**送ったのは別の本文です**")
            else:
                row = after.posts[index - 1] if index <= len(after.posts) else {}
                if threadrun._unquote(row.get("post_id")) != result.post_id:
                    why = f"取り込みのあと {index} 段目の post_id が違います"
        if why:
            mismatch["why"] = why
            log(f"push を止めます: {why}")
            return False
        return True

    try:
        pushed, push_err = writeback_mod.commit_and_push(
            repo_dir, rel_path=rel_path,
            message=f"THTH: {rel_path} の {index} 段目を公開（{result.post_id}）",
            validate=validate_after_rebase)
    except writeback_mod.PushValidationFailed as e:
        # **照合に落ちた＝送ったものと違うものを push しようとした。**
        # v1 と同じく例外で上がってくるので、ここで受けて止める（P1-2）。
        pushed, push_err = False, str(e)
    if not pushed:
        # **公開済みとして保持し、後続を止める。再公開しない**（設計 §3.1）。
        # **inflight は消さない**——書き戻しが終わっていないので、
        # **同じ account の別投稿（v1 を含む）へも進ませない**（P1-2）。
        why = mismatch.get("why") or push_err
        threadrun.mark(run, index, threadrun.PUBLISHED,
                        note=f"書き戻しを確定できず: {why}")
        threadrun.confirm_stop(run, f"書き戻しを確定できませんでした: {why}",
                                now=jst.now_jst())
        return StepResult("stopped", index,
                           f"公開はできましたが書き戻しを確定できませんでした"
                           f"（{why}）。**再公開しません**",
                           run_id=run["run_id"], post_id=result.post_id)

    # **push が通ってから inflight を消す**（P1-2）。
    inflight_mod.clear(state_dir)
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
