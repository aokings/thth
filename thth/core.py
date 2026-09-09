"""throw の全体の流れ（発注 §3・設計 §3.3・§3.5）。lock・inflight・select・throw・
write-back・runs をまとめる **core の唯一の入口**。CLI（`thth throw`・`thth run`）も
MCP も、投稿を伴う操作はすべてここを通る。ロックはここで確保する（§3.7）。
"""
from __future__ import annotations

import dataclasses
import os
import uuid

from . import accounts as accounts_mod
from . import inflight as inflight_mod
from . import jst
from . import lock as lock_mod
from . import queuefile
from . import redact as redact_mod
from . import runs as runs_mod
from . import select as select_mod
from . import writeback
from .adapters import base as adapter_base
from .adapters import threads as threads_mod


@dataclasses.dataclass
class ThrowResult:
    exit_code: int
    mode: str              # "rehearsal" | "production"
    action: str            # "post" | "skip" | "none" | "locked" | "inflight"
    message: str
    file: str | None = None
    post_id: str | None = None
    error: str | None = None


def list_queue_files(account_cfg: dict) -> list:
    repo_dir = account_cfg["repo_dir"]
    queue_dir = os.path.join(repo_dir, account_cfg["queue_dir"])
    out = []
    if not os.path.isdir(queue_dir):
        return out
    for name in sorted(os.listdir(queue_dir)):
        if not name.endswith(".md"):
            continue
        out.append(queuefile.parse(os.path.join(queue_dir, name)))
    return out


def last_post_at(files, account_name: str):
    latest = None
    for qf in files:
        if qf.malformed:
            continue
        fm = qf.front_matter
        if fm.get("account") != account_name or fm.get("status") != "posted":
            continue
        posted_at_raw = fm.get("posted_at")
        if not posted_at_raw:
            continue
        try:
            posted_at = queuefile.parse_publish_at(posted_at_raw)
        except ValueError:
            continue
        if latest is None or posted_at > latest:
            latest = posted_at
    return latest


_ERROR_RUN_REASONS = ("hashtag", "duplicate_text")


def _is_error_reason(reason: str) -> bool:
    return reason in _ERROR_RUN_REASONS or reason.startswith("too_long(")


def _default_adapter_factory(account_cfg: dict, token: dict | None) -> adapter_base.Adapter:
    if account_cfg["media"] != "threads":
        raise NotImplementedError(f"media={account_cfg['media']} は T1 の範囲外（X は T6 保留）")
    base_url = os.environ.get("THTH_THREADS_BASE_URL", threads_mod.DEFAULT_BASE_URL)
    wait_seconds = float(os.environ.get("THTH_THREADS_WAIT_SECONDS", str(threads_mod.DEFAULT_WAIT_SECONDS)))
    access_token = (token or {}).get("access_token", "")
    return threads_mod.ThreadsAdapter(
        base_url=base_url, access_token=access_token,
        user_id=account_cfg.get("user_id", ""), wait_seconds=wait_seconds,
    )


def throw_once(account_name: str, *, production_flag: bool = False,
                bypass_pace: bool = False, adapter_factory=None, log=None,
                now=None) -> ThrowResult:
    """§3.3 の順序で 1 アカウント分を投げる（dry-run 既定）。

    `production_flag` は CLI の `--production`。台帳 `production: true` が commit
    されていない限り、これを付けても dry-run のまま（fail-closed。§0・受け入れ 6）。
    `now` はテスト用の時刻注入（省略時は `jst.now_jst()`）。CLI・timer は渡さない。
    """
    log = log or (lambda line: None)
    adapter_factory = adapter_factory or _default_adapter_factory
    run_id = uuid.uuid4().hex[:12]

    account_cfg = accounts_mod.load_account(account_name)
    state_dir = accounts_mod.state_dir_for(account_name)
    lock_path = os.path.join(state_dir, "lock")

    account_lock = lock_mod.AccountLock(lock_path)
    try:
        account_lock.acquire()
    except lock_mod.LockBusy:
        msg = f"{account_name} は既に実行中です（ロック取得失敗）"
        log(msg)
        return ThrowResult(exit_code=1, mode="rehearsal", action="locked", message=msg)

    try:
        return _throw_locked(
            account_name, account_cfg, state_dir, run_id,
            production_flag=production_flag, bypass_pace=bypass_pace,
            adapter_factory=adapter_factory, log=log, now=now,
        )
    finally:
        account_lock.release()


def _append_run(state_dir: str, account_name: str, run_id: str, mode: str, action: str,
                 file: str | None, post_id: str | None, now, *, status: str, error: str | None) -> None:
    record = {
        "account": account_name,
        "run_id": run_id,
        "mode": mode,
        "action": action,
        "file": os.path.basename(file) if file else None,
        "post_id": post_id,
        "collected": 0,
        "refreshed": False,
        "quota": None,
        "status": status,
        "error": redact_mod.redact(error),
    }
    runs_mod.append_run(state_dir, record, jst.month_str(now))


def _throw_locked(account_name, account_cfg, state_dir, run_id, *,
                   production_flag, bypass_pace, adapter_factory, log, now=None) -> ThrowResult:
    # inflight が残っていれば何もしない（§3.5）
    existing_inflight = inflight_mod.read(state_dir)
    if existing_inflight is not None:
        msg = f"inflight が残っています: {existing_inflight.get('file')}"
        log(msg)
        return ThrowResult(exit_code=1, mode="rehearsal", action="inflight", message=msg,
                            file=existing_inflight.get("file"))

    # fail-closed: 台帳 production: true が commit されていない限り dry-run（§0）
    production = bool(account_cfg.get("production")) and production_flag
    mode = "production" if production else "rehearsal"
    log(f"mode: {mode}")

    now = now if now is not None else jst.now_jst()
    files = list_queue_files(account_cfg)
    last_at = last_post_at(files, account_name)
    recent_texts = select_mod.recent_posted_texts(
        files, account_name=account_name, media=account_cfg["media"], now=now)

    result = select_mod.select_one(
        files, account_name=account_name, account_cfg=account_cfg,
        now=now, last_post_at=last_at, recent_texts=recent_texts, bypass_pace=bypass_pace)

    for rej in result.rejections:
        if _is_error_reason(rej.reason):
            _append_run(state_dir, account_name, run_id, mode, "skip", rej.file, None, now,
                        status="error", error=rej.reason)

    if result.chosen is None:
        msg = "出すものが無い"
        log(msg)
        _append_run(state_dir, account_name, run_id, mode, "none", None, None, now,
                    status="ok", error=None)
        return ThrowResult(exit_code=0, mode=mode, action="none", message=msg)

    chosen = result.chosen
    section = result.section
    started = jst.iso()
    inflight_mod.write(state_dir, file=chosen.path, started=started, container_id=None)

    if mode == "rehearsal":
        log("投げるはずの本文:")
        log(section)
        inflight_mod.clear(state_dir)
        _append_run(state_dir, account_name, run_id, mode, "skip", chosen.path, None, now,
                    status="ok", error=None)
        return ThrowResult(exit_code=0, mode=mode, action="skip",
                            message="dry-run: 投げるはずの本文をログに出した", file=chosen.path)

    # ---- production ----
    token = accounts_mod.load_token(account_cfg)
    adapter = adapter_factory(account_cfg, token)
    post = adapter_base.Post(text=section, reply_to=chosen.get("reply_to") or None)

    def on_container_created(container_id):
        inflight_mod.update(state_dir, container_id=container_id)

    publish_result = adapter.publish(post, dry_run=False, on_container_created=on_container_created)

    if publish_result.error or not publish_result.post_id:
        err = redact_mod.redact(publish_result.error or "不明なエラー")
        log(f"公開失敗: {err}")
        # 失敗の三分類（設計 §3.5・T1 検収 2026-09-09）。core は `failure` だけを見て
        # 分岐する（HTTP の状態番号は core が解釈しない・アダプタに閉じる・§3.4）。
        if publish_result.failure == "publish_ambiguous":
            # 出たか分からない失敗 → inflight を残す（消すと二重投稿になりうる）。
            # 次の実行の冒頭の inflight チェックに乗る（board にもそのまま出る）。
            msg = f"公開の結果が分からないので inflight を残します: {chosen.path}"
            log(msg)
            _append_run(state_dir, account_name, run_id, mode, "post", chosen.path, None, now,
                        status="error", error=err)
            return ThrowResult(exit_code=1, mode=mode, action="inflight", message=msg,
                                file=chosen.path, error=err)
        # コンテナ作成の失敗・公開が 4xx は「実際には出ていない」ので inflight を残す理由が無い。
        inflight_mod.clear(state_dir)
        _append_run(state_dir, account_name, run_id, mode, "post", chosen.path, None, now,
                    status="error", error=err)
        return ThrowResult(exit_code=1, mode=mode, action="post", message="公開に失敗しました",
                            file=chosen.path, error=err)

    post_id = publish_result.post_id
    inflight_mod.update(state_dir, post_id=post_id)

    # テスト専用フック（受け入れ 10・公開成功直後の中断→次回 inflight で停止すること）。
    # 本番コードパスには影響しない（環境変数が立っているときだけ発火する）。
    if os.environ.get("THTH_TEST_CRASH_AFTER_PUBLISH") == "1":
        os._exit(1)

    posted_at = publish_result.ts
    writeback.rewrite_front_matter(chosen.path, status="posted", post_id=post_id, posted_at=posted_at)
    repo_dir = account_cfg["repo_dir"]
    rel_path = os.path.relpath(chosen.path, repo_dir)
    commit_message = f"thth: {account_name} {os.path.basename(chosen.path)} を投稿（post_id {post_id}）"
    ok, err = writeback.commit_and_push(repo_dir, rel_path=rel_path, message=commit_message)

    if not ok:
        log(f"push に失敗しました: {err}")
        # push 失敗では inflight を消さない（§3.5・§4.3・post_id が origin に届くまで残す）
        _append_run(state_dir, account_name, run_id, mode, "post", chosen.path, post_id, now,
                    status="error", error=err)
        return ThrowResult(exit_code=1, mode=mode, action="post",
                            message="投稿には成功したが push に失敗しました",
                            file=chosen.path, post_id=post_id, error=err)

    inflight_mod.clear(state_dir)
    _append_run(state_dir, account_name, run_id, mode, "post", chosen.path, post_id, now,
                status="ok", error=None)
    return ThrowResult(exit_code=0, mode=mode, action="post", message="投稿しました",
                        file=chosen.path, post_id=post_id)
