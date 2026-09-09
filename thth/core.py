"""throw の全体の流れ（発注 §3・設計 §3.3・§3.5・外部レビュー §1〜3）。lock・
inflight・select・throw・write-back・runs をまとめる **core の唯一の入口**。
CLI（`thth throw`・`thth run`）も MCP も、投稿を伴う操作はすべてここを通る。
ロックはここで確保する（§3.7）。
"""
from __future__ import annotations

import contextlib
import dataclasses
import datetime
import os
import uuid

from . import accounts as accounts_mod
from . import approval as approval_mod
from . import inflight as inflight_mod
from . import jst
from . import lock as lock_mod
from . import queuefile
from . import redact as redact_mod
from . import runs as runs_mod
from . import select as select_mod
from . import sent as sent_mod
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
    digest: str | None = None   # `thth send` の確認用 digest（外部レビュー §1b）


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


_ERROR_RUN_REASONS = ("hashtag", "duplicate_text", "approval_stale")


def _is_error_reason(reason: str) -> bool:
    return (
        reason in _ERROR_RUN_REASONS
        or reason.startswith("too_long(")
        or reason.startswith("topic_")
    )


def _default_adapter_factory(account_cfg: dict, token: dict | None) -> adapter_base.Adapter:
    if account_cfg["media"] != "threads":
        raise NotImplementedError(f"media={account_cfg['media']} は T1 の範囲外（X は T6 保留）")
    base_url = os.environ.get("THTH_THREADS_BASE_URL", threads_mod.DEFAULT_BASE_URL)
    wait_seconds = float(os.environ.get("THTH_THREADS_WAIT_SECONDS", str(threads_mod.DEFAULT_WAIT_SECONDS)))
    access_token = (token or {}).get("access_token", "")
    # user_id は `.token`（thth auth / thth token set が書く）を優先し、無ければ
    # 台帳 accounts/<account>.json を見る。同じ値の置き場が 2 つあるとずれるので、
    # 台帳側は空でも動く（統括の検収 T2a 指摘・T2b で解消）。
    user_id = (token or {}).get("user_id") or account_cfg.get("user_id", "")
    return threads_mod.ThreadsAdapter(
        base_url=base_url, access_token=access_token,
        user_id=user_id, wait_seconds=wait_seconds,
    )


@contextlib.contextmanager
def _account_locks(account_name: str, account_cfg: dict, state_dir: str):
    """1 アカウント分の実行の前に、**repo → account の順で**ロックを取る
    （外部レビュー §2・受け入れ 7・8）。

    設計は「1 repo に clone は 1 つ、アカウントは複数ぶら下がってよい」。ロックが
    account 単位だけだと、同じ clone の別アカウントが同時に index・作業ツリー・
    rebase の状態を触れてしまう。ここで repo_dir 単位のロックを account のロックの
    **外側**に足す。

    **取得順を固定する（repo → account）。逆順で取る経路を作らない**——ここが
    唯一の入口である限り、デッドロックは構造的に起きない（§3.7 と同じ考え方:
    「入口が増えても守りの数は増えない」）。release は取得と逆順（account →
    repo）にする。どちらのロックも取れなければ `lock_mod.LockBusy` をそのまま
    投げる（呼び出し側の `throw_once`/`send_once` が `action="locked"` に変換する）。
    """
    repo_lock_path = accounts_mod.repo_lock_path_for(account_cfg["repo_dir"])
    account_lock_path = os.path.join(state_dir, "lock")
    repo_lock = lock_mod.AccountLock(repo_lock_path)
    account_lock = lock_mod.AccountLock(account_lock_path)

    repo_lock.acquire()
    try:
        account_lock.acquire()
        try:
            yield
        finally:
            account_lock.release()
    finally:
        repo_lock.release()


def throw_once(account_name: str, *, production_flag: bool = False,
                bypass_pace: bool = False, adapter_factory=None, log=None,
                now=None) -> ThrowResult:
    """§3.3 の順序で 1 アカウント分を投げる（dry-run 既定）。

    台帳 `max_per_run`（既定 1）まで「select → throw」を繰り返す（1 本ごとに
    select をやり直す・masaru 指摘 2026-09-09・`_throw_locked()` 参照）。返り値は
    最後に処理した結果（何本出たかは log と runs から分かる）。

    `production_flag` は CLI の `--production`。台帳 `production: true` が commit
    されていない限り、これを付けても dry-run のまま（fail-closed。§0・受け入れ 6）。
    `now` はテスト用の時刻注入（省略時は `jst.now_jst()`）。CLI・timer は渡さない。
    """
    log = log or (lambda line: None)
    adapter_factory = adapter_factory or _default_adapter_factory
    run_id = uuid.uuid4().hex[:12]

    account_cfg = accounts_mod.load_account(account_name)
    state_dir = accounts_mod.state_dir_for(account_name)

    try:
        with _account_locks(account_name, account_cfg, state_dir):
            return _throw_locked(
                account_name, account_cfg, state_dir, run_id,
                production_flag=production_flag, bypass_pace=bypass_pace,
                adapter_factory=adapter_factory, log=log, now=now,
            )
    except lock_mod.LockBusy:
        msg = f"{account_name} は既に実行中です（ロック取得失敗）"
        log(msg)
        return ThrowResult(exit_code=1, mode="rehearsal", action="locked", message=msg)


def _append_run(state_dir: str, account_name: str, run_id: str, mode: str, action: str,
                 file: str | None, post_id: str | None, now, *, status: str, error: str | None,
                 topic: str | None = None) -> None:
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
        # topic は「付けたこと」の記録（設計 §2.2: 読み返す field が無い）。
        # 実際に付けた（投稿に使った）ときだけ渡す・それ以外は None のまま。
        "topic": topic,
    }
    runs_mod.append_run(state_dir, record, jst.month_str(now))


def _throw_locked(account_name, account_cfg, state_dir, run_id, *,
                   production_flag, bypass_pace, adapter_factory, log, now=None) -> ThrowResult:
    # inflight が残っていれば何もしない（§3.5）。ここは throw_once 1 回につき
    # 1 度だけ見る（下のループ中に新たに inflight が残るのは「曖昧な失敗」の
    # ときだけで、そのときはその場で打ち切って返すので読み直す必要が無い）。
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

    # 利用者 repo を select より前に同期する（設計 §3.3・外部レビュー再レビュー A）。
    # repo ロック・account ロックの中・inflight 確認の後・queue を読む前。失敗したら
    # 投稿しない（続行不能。前回の書き戻しが中断して push できなかった local commit
    # が残っている場合ここで --ff-only が失敗しうる。通常は inflight が残っていて
    # 手前で止まるが、万一 inflight が無くてもここで止める）。repo を持たない
    # アカウント（`masaru-threads` の `repos/_none`）・git repo でない・origin が
    # 無い場合は何もせず成功扱い（`writeback.sync_repo()` の docstring 参照）。
    synced, sync_err = writeback.sync_repo(account_cfg.get("repo_dir"))
    if not synced:
        msg = f"利用者 repo の同期に失敗しました（投稿しません）: {sync_err}"
        log(msg)
        _append_run(state_dir, account_name, run_id, mode, "none", None, None, now,
                    status="error", error="repo_sync_failed: " + (sync_err or ""))
        return ThrowResult(exit_code=2, mode=mode, action="none", message=msg,
                            error="repo_sync_failed")

    # 1 実行で出す本数（台帳 max_per_run・既定 1・設計 §3.3／§3.6・masaru 指摘
    # 2026-09-09）。**1 本ごとに select をやり直す**（前の投稿が次の select の
    # last_post_at に効く）。したがって min_interval_hours が 0 より大きければ、
    # 2 本目の select は必ず min_interval で全滅し、max_per_run を増やしても
    # 実際には 1 本しか出ない。**これは正しい挙動**（台帳の min_interval_hours が
    # そのアカウントの間隔を決める。max_per_run は「間隔の外側でまとめて出したい
    # ときの上限」であって、間隔を上書きしない）。
    max_per_run = account_cfg.get("max_per_run", 1)
    try:
        max_per_run = int(max_per_run)
    except (TypeError, ValueError):
        max_per_run = 1
    if max_per_run < 1:
        max_per_run = 1

    # dry-run（rehearsal）は実際には何も書き換えない（ディスク上の queue ファイルは
    # 変わらない）ので、select をそのままやり直すと毎回同じ 1 件を選び直してしまう。
    # 「まとめて出したら何が選ばれるか」のプレビューにする目的で、一度選んだ path は
    # このループの中でだけ除く（disk にも runs にも書かない・見た目だけの除外）。
    excluded_paths: set[str] = set()
    # 同じ理由で落ちた候補（too_long・hashtag・duplicate_text・topic_*）を、
    # 2 本目以降の select でもう一度評価して runs に重複記録しないための控え。
    logged_error_paths: set[str] = set()
    posted_count = 0
    last_result: ThrowResult | None = None
    # 直前の投稿の front-matter は `posted_at`（実際に公開した瞬間の壁時計・
    # `jst.iso()`）で書く。1 回の throw_once は 1 つの `now`（この関数の先頭で
    # 決めた値）で筋を通すので、ディスクの `posted_at` を読み直すと「投稿に
    # かかった時間の分だけ now より後」になり、min_interval_hours: 0（=制限しない）
    # のつもりでも `now - posted_at < 0` が真になって 2 本目以降が毎回落ちる
    # （2026-09-09 に実際に踏んだ）。**この run の中で自分が投げた分は `now` を
    # 基準に数える**（disk の値と併用: 他アカウント実行や前回の run 由来の
    # last_post_at はそのまま disk から読む）。
    run_last_post_at: datetime.datetime | None = None

    for _ in range(max_per_run):
        # last_post_at・recent_texts は「全ファイル」から計算する（直前に投げた
        # ファイルもここに含まれて初めて、次の select の間隔判定に効く）。
        # excluded_paths は select の候補プールからだけ外す（dry-run が disk を
        # 変えないので同じ 1 件を選び直さないための除外・下記コメント参照）。
        # ここを取り違えると、直前に投げたファイルが last_post_at の計算からも
        # 消えて min_interval が効かなくなる（2026-09-09 に実際に踏んだ）。
        files = list_queue_files(account_cfg)
        last_at = last_post_at(files, account_name)
        if run_last_post_at is not None:
            # この run の中で自分が投げた分は disk の実測 posted_at より優先する
            # （同じ理由・上のコメント参照）。この run より前の投稿はロックの外で
            # 起きようが無いので、run_last_post_at がある時点で常にそちらが最新。
            last_at = run_last_post_at
        recent_texts = select_mod.recent_posted_texts(
            files, account_name=account_name, media=account_cfg["media"], now=now)
        candidates = [qf for qf in files if qf.path not in excluded_paths]

        result = select_mod.select_one(
            candidates, account_name=account_name, account_cfg=account_cfg,
            now=now, last_post_at=last_at, recent_texts=recent_texts, bypass_pace=bypass_pace)

        for rej in result.rejections:
            if _is_error_reason(rej.reason) and rej.file not in logged_error_paths:
                logged_error_paths.add(rej.file)
                _append_run(state_dir, account_name, run_id, mode, "skip", rej.file, None, now,
                            status="error", error=rej.reason)

        if result.chosen is None:
            msg = "出すものが無い"
            log(msg)
            if last_result is None:
                _append_run(state_dir, account_name, run_id, mode, "none", None, None, now,
                            status="ok", error=None)
                return ThrowResult(exit_code=0, mode=mode, action="none", message=msg)
            # 既にこの実行で何本か出せたあとで尽きただけ。ここで打ち切り、
            # 直前の結果を返す（下のログで本数をまとめて出す）。
            break

        chosen = result.chosen
        excluded_paths.add(chosen.path)
        one_result = _throw_chosen(
            account_name, account_cfg, state_dir, run_id, mode, chosen, result.section,
            now, log, adapter_factory)
        last_result = one_result
        if one_result.action == "post" and one_result.exit_code == 0:
            posted_count += 1
            run_last_post_at = now
        if one_result.exit_code != 0:
            # 途中で失敗（曖昧な失敗で inflight を残す等）したらそこで打ち切る
            # （設計 §3.3）。
            break

    log(f"{account_name}: この実行で {posted_count} 本投げました"
        f"（mode={mode}・上限 max_per_run={max_per_run}）")
    return last_result


def _section_matches(path: str, media: str, expected_hash: str) -> bool:
    """`path` の（media の節の）本文がいま `expected_hash`（送った本文の hash）と
    一致するか（外部レビュー §3・再レビュー B）。ファイルが読めない・節が無い場合は
    空文字列として扱う（＝一致しないほうに倒す。fail-closed）。

    書き戻し直前（rebase 前・ローカルのファイルに対して）と、`writeback.commit_and_push()`
    の `validate` コールバック（rebase の後・push の直前）の両方から呼ぶ。前者だけでは
    「rebase の後に remote の変更が入ってくる」ケースを見逃す（外部レビュー再レビュー
    §「validation occurs before remote changes are incorporated」）ので、両方が要る。
    """
    try:
        current_qf = queuefile.parse(path)
        current_section = queuefile.extract_section(current_qf.body, media)
    except OSError:
        current_section = None
    current_hash = approval_mod.compute_body_hash(current_section or "")
    return current_hash == expected_hash


def _throw_chosen(account_name, account_cfg, state_dir, run_id, mode, chosen, section,
                   now, log, adapter_factory) -> ThrowResult:
    """select_one() が選んだ 1 件を投げる（dry-run ならログに出すだけ）。

    `_throw_locked()` の max_per_run ループから 1 本ごとに呼ばれる（§3.3）。
    """
    started = jst.iso()
    # 送る本文の hash を inflight に書く（外部レビュー §3・受け入れ 9・10）。公開の
    # あと・書き戻しの前に、いま repo にある本文とこの hash を突き合わせる
    # （下の「送った本文と repo の本文の照合」参照）。
    body_hash = approval_mod.compute_body_hash(section)
    inflight_mod.write(state_dir, file=chosen.path, started=started, container_id=None,
                        body_hash=body_hash)

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
    # topic は select_one() の条件 9b で既に検査済み（不正なら候補から落ちている）。
    # ここでは正規化だけ行う（前後の空白・先頭の `#` を落とす・設計 §4.1）。
    topic = queuefile.normalize_topic(chosen.get("topic"))
    post = adapter_base.Post(text=section, reply_to=chosen.get("reply_to") or None, topic=topic)

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

    posted_at = publish_result.ts
    # 送った本文そのものを動かせない記録として残す（外部レビュー §3・受け入れ 9・
    # 10・これが正本）。書き戻し（front-matter 書き換え）より前に書く。
    sent_mod.write(state_dir, post_id=post_id, text=section, body_hash=body_hash, sent_at=posted_at)

    # テスト専用フック（受け入れ 10・公開成功直後の中断→次回 inflight で停止すること）。
    # 本番コードパスには影響しない（環境変数が立っているときだけ発火する）。
    if os.environ.get("THTH_TEST_CRASH_AFTER_PUBLISH") == "1":
        os._exit(1)

    # 書き戻しの直前に、いま repo にある本文と「送った本文」を照合する
    # （外部レビュー §3・受け入れ 9・10）。公開している最中に利用者が remote で
    # 本文を書き換えていると、ここで検知する。**要**: 不一致なら front-matter を
    # 書き換えない・再公開しない・inflight を残したまま exit 1 で止める。post_id が
    # 書かれないので、何もしなければ次の実行が同じファイルをもう一度出そうとする
    # ——それを inflight の残留が防ぐ（次回起動時の inflight チェックに掛かる）。
    #
    # **これだけでは足りない**（外部レビュー再レビュー B）: この時点ではまだ
    # `writeback.commit_and_push()` の `pull --rebase` を経ていないので、ここより
    # あとに remote から入ってくる変更は見えない。rebase の直後・push の直前に
    # もう一度同じ照合を行う（下の `validate=` 引数）。
    media = account_cfg["media"]
    if not _section_matches(chosen.path, media, body_hash):
        msg = (f"送った本文と repo の本文が食い違います（書き戻しません・"
               f"再公開もしません）: {chosen.path}")
        log(msg)
        _append_run(state_dir, account_name, run_id, mode, "post", chosen.path, post_id, now,
                    status="error", error="text_mismatch_before_writeback")
        # inflight は消さない（§3.5 の「曖昧な失敗」と同じ扱い。人が直すまで
        # このアカウントは次回以降も止まる）。
        return ThrowResult(exit_code=1, mode=mode, action="inflight",
                            message=msg, file=chosen.path, post_id=post_id,
                            error="text_mismatch_before_writeback")

    writeback.rewrite_front_matter(chosen.path, status="posted", post_id=post_id, posted_at=posted_at)
    repo_dir = account_cfg["repo_dir"]
    rel_path = os.path.relpath(chosen.path, repo_dir)
    commit_message = f"thth: {account_name} {os.path.basename(chosen.path)} を投稿（post_id {post_id}）"

    def _validate_after_rebase() -> bool:
        return _section_matches(chosen.path, media, body_hash)

    try:
        ok, err = writeback.commit_and_push(
            repo_dir, rel_path=rel_path, message=commit_message,
            validate=_validate_after_rebase)
    except writeback.PushValidationFailed as e:
        msg = str(e)
        log(msg)
        _append_run(state_dir, account_name, run_id, mode, "post", chosen.path, post_id, now,
                    status="error", error="text_mismatch_after_rebase")
        # push していない（commit はローカルに残る）。inflight も消さない
        # （§3.5 と同じ扱い。次回実行も inflight チェックで止まる・外部レビュー
        # 再レビュー B の受け入れ）。
        return ThrowResult(exit_code=1, mode=mode, action="inflight",
                            message=msg, file=chosen.path, post_id=post_id,
                            error="text_mismatch_after_rebase")

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
                status="ok", error=None, topic=topic)
    return ThrowResult(exit_code=0, mode=mode, action="post", message="投稿しました",
                        file=chosen.path, post_id=post_id)


def send_once(account_name: str, *, text: str, topic: str | None = None,
               reply_to: str | None = None, production_flag: bool = False,
               confirm: str | None = None, adapter_factory=None, log=None,
               now=None) -> ThrowResult:
    """`thth send`（**同席の様態**・設計 §3.7）。queue を通さずその場で 1 本出す。

    対話の中で masaru が本文を読んで「出して」と言ったときの経路。承認は既に
    その場で済んでいるので `status: approved` は要らない。**その代わり、承認の
    在り処が「masaru が見た本文」なので、渡された text をそのまま投げる**
    （整形も切り詰めもしない）。

    不在の様態（timer→`throw_once`）との違いは queue と承認だけで、**守りは同じ
    ものを通る**: ロックは core の入口・inflight・fail-closed（台帳 `production: true`
    が commit されていなければ dry-run）・runs への記録。

    **`confirm`**（外部レビュー §1b・受け入れ 6）: dry-run（rehearsal）は本文・
    account・reply_to・topic から計算した短い digest（`approval.compute_send_digest()`）
    を表示するだけ。`--production` で実際に送るときは、その digest を
    `--confirm <digest>` として渡さなければならない。省略・不一致はどちらも拒否
    （「見せたものと送るものが同じ」を機械で担保する）。
    """
    log = log or (lambda line: None)
    adapter_factory = adapter_factory or _default_adapter_factory
    run_id = uuid.uuid4().hex[:12]

    account_cfg = accounts_mod.load_account(account_name)
    state_dir = accounts_mod.state_dir_for(account_name)
    now = now if now is not None else jst.now_jst()

    try:
        return _send_locked(
            account_name, account_cfg, state_dir, run_id, text=text, topic=topic,
            reply_to=reply_to, production_flag=production_flag, confirm=confirm,
            adapter_factory=adapter_factory, log=log, now=now,
        )
    except lock_mod.LockBusy:
        msg = f"{account_name} は既に実行中です（ロック取得失敗）"
        log(msg)
        return ThrowResult(exit_code=1, mode="rehearsal", action="locked", message=msg)


def _send_locked(account_name, account_cfg, state_dir, run_id, *, text, topic, reply_to,
                  production_flag, confirm, adapter_factory, log, now) -> ThrowResult:
    with _account_locks(account_name, account_cfg, state_dir):
        existing_inflight = inflight_mod.read(state_dir)
        if existing_inflight is not None:
            msg = f"inflight が残っています: {existing_inflight.get('file')}"
            log(msg)
            return ThrowResult(exit_code=1, mode="rehearsal", action="inflight", message=msg,
                                file=existing_inflight.get("file"))

        production = bool(account_cfg.get("production")) and production_flag
        mode = "production" if production else "rehearsal"
        log(f"mode: {mode}")

        body = (text or "").strip()
        if not body:
            log("本文が空です")
            return ThrowResult(exit_code=2, mode=mode, action="none", message="本文が空です")

        media = account_cfg["media"]
        limit = queuefile.MEDIA_LIMITS.get(media, 500)
        n = queuefile.char_count(body)
        if n > limit:
            msg = f"長すぎます（{n} 字・上限 {limit} 字）。切り詰めません。"
            log(msg)
            _append_run(state_dir, account_name, run_id, mode, "skip", None, None, now,
                        status="error", error=f"too_long({n})")
            return ThrowResult(exit_code=1, mode=mode, action="skip", message=msg)

        topic_value = queuefile.normalize_topic(topic)
        if topic_value is not None:
            topic_err = queuefile.topic_error(topic_value)
            if topic_err is not None:
                log(f"トピックが不正です: {topic_err}")
                _append_run(state_dir, account_name, run_id, mode, "skip", None, None, now,
                            status="error", error=topic_err)
                return ThrowResult(exit_code=1, mode=mode, action="skip", message=topic_err)

        # 確認用 digest（外部レビュー §1b・受け入れ 6）。`approved_sha` と同じ正規化
        # だが `publish_at` は含めない（send に予約時刻という概念が無いため）。
        digest = approval_mod.compute_send_digest(
            text=body, account=account_name, reply_to=reply_to, topic=topic_value)

        if mode == "rehearsal":
            log("投げるはずの本文:")
            log(body)
            if topic_value:
                log(f"トピック: {topic_value}")
            log(f"digest: {digest}")
            _append_run(state_dir, account_name, run_id, mode, "skip", None, None, now,
                        status="ok", error=None)
            return ThrowResult(exit_code=0, mode=mode, action="skip",
                                message="dry-run: 投げるはずの本文をログに出した", digest=digest)

        # ---- production ----
        # dry-run で見せた digest と一致する `--confirm` が無ければ送らない
        # （省略も不一致も拒否・受け入れ 6）。「見せたものと送るものが同じ」を
        # ここで機械的に担保する。
        if confirm != digest:
            if confirm is None:
                msg = f"digest が指定されていないので送信しません（--confirm {digest} が必要）"
                error = "confirm_missing"
            else:
                msg = "digest が一致しないので送信しません"
                error = "confirm_mismatch"
            log(msg)
            _append_run(state_dir, account_name, run_id, mode, "skip", None, None, now,
                        status="error", error=error)
            return ThrowResult(exit_code=1, mode=mode, action="skip", message=msg, digest=digest)

        inflight_mod.write(state_dir, file="(send)", started=jst.iso(), container_id=None)
        token = accounts_mod.load_token(account_cfg)
        adapter = adapter_factory(account_cfg, token)
        post = adapter_base.Post(text=body, reply_to=reply_to or None, topic=topic_value)

        def on_container_created(container_id):
            inflight_mod.update(state_dir, container_id=container_id)

        result = adapter.publish(post, dry_run=False, on_container_created=on_container_created)

        if result.error or not result.post_id:
            err = redact_mod.redact(result.error or "不明なエラー")
            log(f"公開失敗: {err}")
            if result.failure == "publish_ambiguous":
                # 出たか分からない → inflight を残して人を呼ぶ（§3.5）
                msg = "公開の結果が分からないので inflight を残します"
                log(msg)
                _append_run(state_dir, account_name, run_id, mode, "post", None, None, now,
                            status="error", error=err)
                return ThrowResult(exit_code=1, mode=mode, action="inflight", message=msg, error=err)
            inflight_mod.clear(state_dir)
            _append_run(state_dir, account_name, run_id, mode, "post", None, None, now,
                        status="error", error=err)
            return ThrowResult(exit_code=1, mode=mode, action="post",
                                message="公開に失敗しました", error=err)

        inflight_mod.clear(state_dir)
        _append_run(state_dir, account_name, run_id, mode, "post", None, result.post_id, now,
                    status="ok", error=None)
        log(f"投稿しました: post_id={result.post_id}")
        return ThrowResult(exit_code=0, mode=mode, action="post", message="投稿しました",
                            post_id=result.post_id)
