"""throw の全体の流れ（発注 §3・設計 §3.3・§3.5・外部レビュー §1〜3）。lock・
inflight・select・throw・write-back・runs をまとめる **core の唯一の入口**。
CLI（`thth throw`・`thth run`）も MCP も、投稿を伴う操作はすべてここを通る。
ロックはここで確保する（§3.7）。
"""
from __future__ import annotations

from . import api_diagnostic as api_diagnostic_mod

import contextlib
import dataclasses
import datetime
import os
import uuid

from . import accounts as accounts_mod
from . import adapters as adapters_mod
from . import approval as approval_mod
from . import engagements as engagements_mod
from . import inflight as inflight_mod
from . import jst
from . import lock as lock_mod
from . import postid as postid_mod
from . import queuefile
from . import tags as tags_mod
from . import redact as redact_mod
from . import runs as runs_mod
from . import select as select_mod
from . import sent as sent_mod
from . import writeback
from .adapters import base as adapter_base


@dataclasses.dataclass
class ThrowResult:
    exit_code: int
    mode: str              # "rehearsal" | "production"
    action: str            # "post" | "skip" | "none" | "locked" | "inflight"
    message: str
    file: str | None = None
    post_id: str | None = None
    # 出たものの URL（`PublishResult.url`）。**媒体が返したものだけ**を入れる
    # ——組み立てた推測は入れない（Threads は公開の応答で返さないので None）。
    url: str | None = None
    error: str | None = None
    digest: str | None = None   # `thth send` の確認用 digest（外部レビュー §1b）
    # 「出すものが無い」ときに、なぜ出せないかの内訳（外部レビュー再レビュー C）。
    # `thth throw` を手で打ったときに黙って終わらせないためのもの。
    # [{"file": ..., "reason": ...}, ...]。無ければ None。
    rejections: list | None = None
    # `error` が `text_mismatch_before_writeback`・`text_mismatch_after_rebase` の
    # ときに、5 項目（body・account・reply_to・topic・publish_at）のうちどれが
    # 食い違ったか（外部レビュー第 3 巡・持ち越し項目 C）。それ以外の error では
    # None のまま。
    mismatch_fields: list | None = None
    api_diagnostic: dict | None = None


def list_queue_files(account_cfg: dict, *, tree_sha: str | None) -> list:
    """queue を読む。**照合先の commit（`tree_sha`）を必ず受け取る**。

    `tree_sha` は「同期を確認した commit の OID」（外部レビュー第 5 巡 P1）。
    投稿の経路は `writeback.sync_repo()` が返した OID を、board のように同期を
    伴わない読み手は `writeback.upstream_sha()`（HEAD が upstream と一致して
    いればその OID・していなければ None）を渡す。**既定値を持たせない**——
    省略できると「その時の HEAD」を引き直す実装に戻ってしまい、同じ穴がまた
    開く（第 5 巡はまさにそれだった）。None なら何も確認できないので、
    すべてのファイルが `verified=False`（`unverified_content`）になる。
    """
    repo_dir = account_cfg["repo_dir"]
    queue_dir = os.path.join(repo_dir, account_cfg["queue_dir"])
    out = []
    if not os.path.isdir(queue_dir):
        return out
    for name in sorted(os.listdir(queue_dir)):
        if not name.endswith(".md"):
            continue
        path = os.path.join(queue_dir, name)
        # **1 回だけ読み**、その同じバイト列で「commit と一致するか」も判定する
        # （外部レビュー第 4 巡 P1）。読み直すと、検査したバイト列と select が
        # 見るバイト列が別物になりうる。ここでしか `verified` は立たない。
        # board（report.py）も同じ関数を通るので、公開の判断と board の診断が
        # 同じ事実を見る。
        with open(path, "rb") as f:
            raw = f.read()
        qf = queuefile.parse_text(raw.decode("utf-8"), path)
        qf.verified = writeback.matches_synced_commit(
            repo_dir, path, tree_sha=tree_sha, disk_bytes=raw)
        out.append(qf)
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


_ERROR_RUN_REASONS = ("hashtag", "duplicate_text", "approval_stale", "unverified_content")


def _is_error_reason(reason: str) -> bool:
    return (
        reason in _ERROR_RUN_REASONS
        or reason.startswith("too_long(")
        or reason.startswith("topic_")
        or reason.startswith("control_char(")
    )


def _default_adapter_factory(account_cfg: dict, token: dict | None) -> adapter_base.Adapter:
    """台帳の `media` からアダプタを 1 つ作る。**媒体名の分岐はここに書かない。**

    設計 v2 §4.2。以前はここが `media != "threads"` を弾いて `ThreadsAdapter` を
    直に組み立てていた（Threads 固有になっている 6 箇所の 1 つめ）。**媒体を
    足すたびに core を触る形**だったので、`thth/adapters/__init__.py` の
    `REGISTRY` へ移した。**知らない `media` は、知っている媒体の一覧を添えて
    loud に断る**（T-B0）——それも `make_adapter()` の中。
    """
    return adapters_mod.make_adapter(account_cfg, token)


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
    account_lock_path = accounts_mod.account_lock_path_for(account_name)
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

    # **スレッド連投を先に見る**（独立検収 2026-09-11・P1-1）。
    # v2 は**段ごとにロックを取り直す**ので、ここで account+repo ロックを
    # 握ったまま入ることはできない。**握る前に**分岐する。
    from . import threadthrow as threadthrow_mod
    bundle_results = threadthrow_mod.run_for_account(
        account_name, adapter_factory=adapter_factory, now=now, log=log,
        production=(bool(account_cfg.get('production')) and production_flag))
    if bundle_results:
        last = bundle_results[-1]
        published = [r for r in bundle_results if r.action == "published"]
        mode = "production" if (bool(account_cfg.get('production')) and production_flag) else "rehearsal"
        detail_data = api_diagnostic_mod.clean(last.api_diagnostic)
        if detail_data:
            _append_run(state_dir, account_name, last.run_id or run_id, mode, "post",
                        last.file, None, now or jst.now_jst(), status="error",
                        error=last.reason, api_diagnostic=detail_data)
        return ThrowResult(
            exit_code=0 if last.action in ("published", "skipped", "stopped") else 1,
            mode=mode,
            action="posted" if published else last.action,
            message=(f"スレッド連投: {len(published)} 段を公開しました"
                      if published else f"スレッド連投: {last.reason}"),
            file=last.file, post_id=last.post_id,
            error=last.reason if detail_data else None, api_diagnostic=detail_data)

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
                 topic: str | None = None, mismatch_fields: list | None = None,
                 engagement_write_failed: bool = False,
                 engagement_author_lookup_failed: bool = False,
                 api_diagnostic: dict | None = None) -> None:
    record = {
        "account": account_name,
        "run_id": run_id,
        "mode": mode,
        "action": action,
        "file": os.path.basename(file) if file else None,
        "post_id": post_id,
        # **投稿の処理では収集を測っていない**（外部レビュー C・2026-09-12）。
        # ここが `0` 固定だったので、**「見に行って 0 件だった」と読めた。**
        # 収集は `cmd_run` のこの後で走る（`cli.py`）。**測っていないものを
        # 0 と書かない。** `maintain.py` も同じく `None`。
        #
        # **古い `0` の行は書き換えない。** 遡って「成功 0」とも「未取得」とも
        # 確定しない——**旧形式・収集結果不明**として読む。
        "collected": None,
        "refreshed": False,
        "quota": None,
        "status": status,
        "error": redact_mod.redact(error),
        "api_diagnostic": api_diagnostic_mod.clean(api_diagnostic),
        # topic は「付けたこと」の記録（設計 §2.2: 読み返す field が無い）。
        # 実際に付けた（投稿に使った）ときだけ渡す・それ以外は None のまま。
        "topic": topic,
        # 指紋が食い違ったときの内訳（外部レビュー第 3 巡・持ち越し項目 C）。
        "mismatch_fields": mismatch_fields,
        # 絡みの台帳（`engagements.append()`）が書けなかったか（発注 T0-1）。
        # **公開そのものは成功のまま**——loud だが exit code は変えない。
        "engagement_write_failed": engagement_write_failed,
        # best-effort の `fetch_post()`（`author_key` を埋める・T7-2）が
        # 例外で終わったか。**これも公開の成否とは無関係**——`author_key` が
        # 単に無かった（None のまま）だけでは立たない。
        "engagement_author_lookup_failed": engagement_author_lookup_failed,
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
    # 手前で止まるが、万一 inflight が無くてもここで止める）。fail-closed（外部
    # レビュー第 3 巡 P1）: 唯一の例外は repo_dir が存在しないこと（`masaru-threads`
    # の `repos/_none` のような send 専用アカウント）。それ以外は git repo として
    # 同期の成功を確認できて初めて成功扱いになる（`writeback.sync_repo()` の
    # docstring 参照）。
    synced, sync_err, synced_sha = writeback.sync_repo(account_cfg.get("repo_dir"))
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
        # 照合先は**この run が同期を確認した OID に固定する**（外部レビュー第 5 巡
        # P1）。ループの 2 周目以降も同じ OID を使う——書き戻しで HEAD が進んでも、
        # 「この run が確かめた状態」を動かさないため。
        files = list_queue_files(account_cfg, tree_sha=synced_sha)
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
                # `thth throw` を手で打ったときに理由を添える（外部レビュー再レビュー
                # C）。account_mismatch は他アカウントのファイル由来のノイズなので
                # 除く（report.py の queue_summary と同じ流儀）。
                rejections = [
                    {"file": os.path.basename(rej.file), "reason": rej.reason}
                    for rej in result.rejections if rej.reason != "account_mismatch"
                ]
                return ThrowResult(exit_code=0, mode=mode, action="none", message=msg,
                                    rejections=rejections or None)
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


def _current_fingerprint(path: str, media: str) -> str | None:
    """`path` の**いまの**内容から、`approval.compute_approved_sha()` と同じ 5 項目
    （本文・account・reply_to・topic・publish_at）の指紋を計算する（外部レビュー
    再々レビュー P1・1）。ファイルが読めない・型外・media の節が無い・publish_at が
    壊れている場合は None を返す（＝呼び出し側で「一致しない」扱いにする。
    fail-closed）。
    """
    try:
        current_qf = queuefile.parse(path)
    except OSError:
        return None
    if current_qf.malformed:
        return None
    fm = current_qf.front_matter
    current_section = queuefile.extract_section(current_qf.body, media)
    try:
        return approval_mod.compute_approved_sha(
            section=current_section or "", account=fm.get("account"),
            reply_to=fm.get("reply_to"), topic=fm.get("topic"),
            publish_at=fm.get("publish_at"),
            **approval_mod.publish_options(fm))
    except (ValueError, TypeError):
        return None


def _fingerprint_matches(path: str, media: str, expected_fingerprint: str) -> bool:
    """`path` の**いまの** 5 項目の指紋が、公開の直前に固定した `expected_fingerprint`
    と一致するか（外部レビュー再々レビュー P1・1）。

    これまでは本文（media の節）だけの hash（`compute_body_hash()`）しか見ていな
    かったため、公開中に別 clone から `account`・`topic`・`reply_to`・`publish_at`
    だけを書き換えて push されても検知できなかった（本文の hash は変わらないので
    すり抜ける）。ここでは `compute_approved_sha()` と同じ 5 項目全部を、**いま
    ファイルに書かれている値**から計算し直し、`_throw_chosen()` が公開の直前に
    固定した指紋と比較する（現在の `approved_sha` の値そのものとは比較しない
    ——remote が別内容で再承認されている可能性があるため。設計どおり「実際に
    公開した投稿」の指紋という不動点と比較する）。

    書き戻し直前（rebase 前・ローカルのファイルに対して）と、
    `writeback.commit_and_push()` の `validate` コールバック（rebase の後・push の
    直前）の両方から呼ぶ。前者だけでは「rebase の後に remote の変更が入ってくる」
    ケースを見逃す（外部レビュー再レビュー §「validation occurs before remote
    changes are incorporated」）ので、両方が要る。
    """
    current_fingerprint = _current_fingerprint(path, media)
    return current_fingerprint is not None and current_fingerprint == expected_fingerprint


def _mismatch_fields(path: str, media: str, expected_components: dict) -> list:
    """`_fingerprint_matches()` が False を返したとき、5 項目のうち**どれが**
    食い違ったのかを返す（外部レビュー第 3 巡・持ち越し項目 C）。

    hash 一致検査だけでは「一致しない」ことしか分からず、人が止まった原因を
    ファイルを開いて自分で探すしかなかった。`expected_components`
    （`_throw_chosen()` が公開の直前に `approval.compute_approved_components()` で
    固定したもの）と、`path` の**いまの**内容から同じ関数で計算し直した値を
    項目ごとに突き合わせ、違ったキー名（`body`・`account`・`reply_to`・`topic`・
    `publish_at`）だけを返す。ファイルが読めない・型外・media の節が無い・
    publish_at が壊れている場合は `["file_unreadable"]` を返す（`_current_fingerprint()`
    が None を返すのと同じ状況の言い換え）。
    """
    try:
        current_qf = queuefile.parse(path)
    except OSError:
        return ["file_unreadable"]
    if current_qf.malformed:
        return ["file_unreadable"]
    fm = current_qf.front_matter
    current_section = queuefile.extract_section(current_qf.body, media)
    try:
        current_components = approval_mod.compute_approved_components(
            section=current_section or "", account=fm.get("account"),
            reply_to=fm.get("reply_to"), topic=fm.get("topic"),
            publish_at=fm.get("publish_at"),
            **approval_mod.publish_options(fm))
    except (ValueError, TypeError):
        return ["file_unreadable"]
    # 5 項目＋任意項目（`location_id`・`share_to_instagram`・v2.1-B）。
    return [key for key in approval_mod.COMPARED_KEYS
            if (current_components.get(key) or "") != (expected_components.get(key) or "")]


def _engagement_root_post(value) -> str | None:
    """front-matter `reply_to_root`。post_id の形でなければ `None`（設計「自分の泉」§4）。"""
    if not value or not postid_mod.is_usable(value):
        return None
    return value


def _engagement_author_key(value) -> str | None:
    """front-matter `reply_to_author_key`。16 進 16 桁でなければ `None`。"""
    if isinstance(value, str) and engagements_mod.AUTHOR_KEY_RE.match(value):
        return value
    return None


def _engagement_found_by(value) -> str | None:
    """front-matter `found_by`。3 値以外は `None`。"""
    return value if value in engagements_mod.FOUND_BY_VALUES else None


def _fetch_author_key_best_effort(adapter, reply_to: str, *, log) -> tuple:
    """`reply_to` の投稿を 1 回だけ `fetch_post()` して `author_key` を
    best-effort で埋める（T7-2・設計「自分の泉」§4）。

    呼ぶのは front-matter（`reply_to_author_key`）／`send` の
    `--reply-to-author-key` に値が無いときだけ。**`PermissionMissing`・
    `AdapterError` は `None` に化ける**——絡みの台帳の `author_key` は
    best-effort の添え物で、これが引けないことは公開の成否と無関係
    （止めない）。**本文（`fetch_post()` が返す `text` 等）はここで捨てる**
    ——使うのは `author_key` だけ。

    戻り値 `(author_key, lookup_failed)`。`lookup_failed` が `True` のときだけ
    呼び出し側が `runs` に `engagement_author_lookup_failed` を立てる
    （`author_key` が単に取れなかった＝`None` だっただけでは立てない——
    `fetch_post()` が例外を投げたときだけ「引こうとして失敗した」）。
    """
    try:
        root_row = adapter.fetch_post(reply_to)
    except (adapter_base.PermissionMissing, adapter_base.AdapterError) as e:
        log("絡みの台帳の author_key を引けませんでした"
            f"（公開は続けます・reply_to={reply_to}）: {redact_mod.redact(str(e))}")
        return None, True
    return root_row.get("author_key"), False


def _record_engagement(account_cfg, account_name, *, media, topic, form, post_id,
                        posted_at, now, log, reply_to, reply_to_root,
                        reply_to_author_key, found_by, adapter=None) -> tuple:
    """絡みの台帳に 1 行足す（設計「自分の泉」§4・発注 T0-1・T7-2 で経路を広げる）。

    `reply_to` が無ければ何もしない。**`chosen`（queue の front-matter）を
    直接は読まない**——呼び出し側（`_throw_chosen()`・`_send_locked()`）が
    それぞれの経由（front-matter／`send` の flag）から組んだ値を渡す
    （T7-2 発注書「chosen でなく引数から組む形に広げる」・queue の門
    （`_throw_chosen`）も `send`（同席送信）も同じ 1 か所を通る）。

    `reply_to_author_key` が渡されなければ、`adapter` が渡されているときだけ
    `_fetch_author_key_best_effort()` で埋める。**書けなかったら（disk 等）
    公開は成功のまま**——ここは loud に 1 行 log するだけで、例外を外へ出さない。

    戻り値 `(engagement_write_failed, engagement_author_lookup_failed)`。
    どちらも公開の成否を変えない——呼び出し側が `runs` に記録するだけ。
    """
    if not reply_to:
        return False, False
    # 遅延 import（`thth/threadshape.py` は `thth/collect.py` を import し、
    # `collect.py` は `thth/core.py` を import する——モジュール先頭で読み込むと
    # 循環 import になる）。
    from . import threadshape as threadshape_mod
    author_key = _engagement_author_key(reply_to_author_key)
    lookup_failed = False
    if author_key is None and adapter is not None:
        author_key, lookup_failed = _fetch_author_key_best_effort(adapter, reply_to, log=log)
    row = {
        "schema": engagements_mod.SCHEMA,
        "post_id": post_id,
        "reply_to": reply_to,
        "root_post": _engagement_root_post(reply_to_root),
        "author_key": author_key,
        "account": account_name,
        "medium": media,
        "topic": topic,
        "form": form,
        "hour_band": threadshape_mod.hour_band(jst.parse(posted_at)),
        "posted_at": posted_at,
        "found_by": _engagement_found_by(found_by),
    }
    try:
        engagements_mod.append(account_cfg, account_name, row, now=now)
    except (OSError, engagements_mod.EngagementError) as e:
        log(f"絡みの台帳に書けませんでした（公開は成功したまま・post_id={post_id}）: {e}")
        return True, lookup_failed
    return False, lookup_failed


def _throw_chosen(account_name, account_cfg, state_dir, run_id, mode, chosen, section,
                   now, log, adapter_factory) -> ThrowResult:
    """select_one() が選んだ 1 件を投げる（dry-run ならログに出すだけ）。

    `_throw_locked()` の max_per_run ループから 1 本ごとに呼ばれる（§3.3）。
    """
    started = jst.iso()
    media = account_cfg["media"]
    # 公開の直前に、いま選ばれている内容（`compute_approved_sha()` と同じ 5 項目:
    # 本文・account・reply_to・topic・publish_at）の指紋を固定する（外部レビュー
    # 再々レビュー P1・1）。select_one() がこの時点までに approved_sha との一致を
    # 検査済みなので、ここでの値は「masaru が承認した内容そのもの」と一致している
    # ——この不動点を、書き戻し前・rebase 後の照合の基準にする（本文だけの hash では
    # account・topic・reply_to・publish_at の書き換えを見逃すため）。
    # 任意項目（場所・Instagram 共有・設計 v2 §4.3）も指紋に入る——承認後に
    # 変えれば select が `approval_stale` で落としている。
    options = approval_mod.publish_options(chosen.front_matter)
    expected_fingerprint = approval_mod.compute_approved_sha(
        section=section, account=account_name, reply_to=chosen.get("reply_to"),
        topic=chosen.get("topic"), publish_at=chosen.get("publish_at"), **options)
    # 上と同じ 5 項目を、hash にする前の正規化済みの値のまま持っておく
    # （外部レビュー第 3 巡・持ち越し項目 C）。指紋が食い違ったときに
    # `_mismatch_fields()` へ渡して「どの項目が」違ったかを特定するため
    # （hash 自体からは個々の項目を復元できない）。
    expected_components = approval_mod.compute_approved_components(
        section=section, account=account_name, reply_to=chosen.get("reply_to"),
        topic=chosen.get("topic"), publish_at=chosen.get("publish_at"), **options)
    # 送る本文の hash（後方互換・`tests/test_sent_integrity.py` が参照）も併せて
    # inflight に書く（外部レビュー §3・受け入れ 9・10）。実際の照合は上の指紋で行う。
    body_hash = approval_mod.compute_body_hash(section)
    inflight_mod.write(state_dir, file=chosen.path, started=started, container_id=None,
                        body_hash=body_hash, approved_fingerprint=expected_fingerprint)

    if mode == "rehearsal":
        log("投げるはずの本文:")
        log(tags_mod.prepared(account_cfg["media"], section,
                              queuefile.normalize_topic(chosen.get("topic")),
                              hashtags=bool(account_cfg.get("hashtags", True))))
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
    post = adapter_base.Post(text=tags_mod.prepared(account_cfg["media"], section, topic,
                                                    hashtags=bool(account_cfg.get("hashtags", True))),
                             reply_to=chosen.get("reply_to") or None, topic=topic,
                             hashtags_allowed=bool(account_cfg.get("hashtags", True)),
                             location_id=options["location_id"],
                             share_to_instagram=options["share_to_instagram"])

    # **公開要求の手前で、要る権限が乗っているかを見る**（設計 v2 §4.3 受け入れ (c)・
    # v2.1-B）。場所・Instagram 共有はそれぞれ別の権限を要る。`.token` の `scopes`
    # （doctor と同じ物差し）に無ければ、**コンテナも作らず**に inflight を消して
    # rc=2——`thth auth` のやり直しを促す。`scopes` が判らないトークンはここでは
    # 止めない（媒体の応答の権限系エラーで `failure="permission"` になる）。
    missing = adapter_base.missing_for_post(adapter, token, post)
    if missing:
        err = adapter_base.not_granted_message(missing[0]).replace(
            "<account>", account_name)
        log(f"公開しません: {err}")
        inflight_mod.clear(state_dir)
        _append_run(state_dir, account_name, run_id, mode, "post", chosen.path, None, now,
                    status="error", error=f"permission_missing({missing[0]})")
        return ThrowResult(exit_code=2, mode=mode, action="post", message=err,
                            file=chosen.path, error=err)

    def on_container_created(container_id):
        inflight_mod.update(state_dir, container_id=container_id)

    publish_result = adapter.publish(post, dry_run=False, on_container_created=on_container_created)

    if publish_result.error or not publish_result.post_id:
        detail_data = api_diagnostic_mod.clean(publish_result.api_diagnostic)
        err = redact_mod.redact(publish_result.error or "不明なエラー")
        log(f"公開失敗: {err}")
        if publish_result.failure == "permission":
            # 媒体が「権限が無い」と断った（コンテナ作成の段・出ていない）。
            # inflight を残す理由は無い。rc=2 で `thth auth` のやり直しを促す。
            err = err.replace("<account>", account_name)
            inflight_mod.clear(state_dir)
            _append_run(state_dir, account_name, run_id, mode, "post", chosen.path, None, now,
                        status="error", error=err, api_diagnostic=detail_data)
            return ThrowResult(exit_code=2, mode=mode, action="post", message=err,
                                file=chosen.path, error=err, api_diagnostic=detail_data)
        # 失敗の三分類（設計 §3.5・T1 検収 2026-09-09）。core は `failure` だけを見て
        # 分岐する（HTTP の状態番号は core が解釈しない・アダプタに閉じる・§3.4）。
        if publish_result.failure == "publish_ambiguous":
            # 出たか分からない失敗 → inflight を残す（消すと二重投稿になりうる）。
            # 次の実行の冒頭の inflight チェックに乗る（board にもそのまま出る）。
            msg = f"公開の結果が分からないので inflight を残します: {chosen.path}"
            log(msg)
            _append_run(state_dir, account_name, run_id, mode, "post", chosen.path, None, now,
                        status="error", error=err, api_diagnostic=detail_data)
            return ThrowResult(exit_code=1, mode=mode, action="inflight", message=msg,
                                file=chosen.path, error=err, api_diagnostic=detail_data)
        # コンテナ作成の失敗・公開が 4xx は「実際には出ていない」ので inflight を残す理由が無い。
        inflight_mod.clear(state_dir)
        _append_run(state_dir, account_name, run_id, mode, "post", chosen.path, None, now,
                    status="error", error=err, api_diagnostic=detail_data)
        return ThrowResult(exit_code=1, mode=mode, action="post", message="公開に失敗しました",
                            file=chosen.path, error=err, api_diagnostic=detail_data)

    post_id = publish_result.post_id
    # **媒体が返した `post_id` を、確かめずに書き戻さない**（セキュリティ監査
    # 2026-09-14・P1-4）。`post_id` は front-matter の 1 行になり、台帳の
    # ファイル名にもなる。改行の入った `id` を返すサーバ（乗っ取られた媒体・
    # 間に入った proxy・偽の口）が相手だと、**書き戻しが front-matter に
    # `status: approved` を後勝ちで足せた**——出したはずの原稿が承認済みに戻り、
    # 次の実行がもう一度出す。
    #
    # ここで断るときの扱いは **`publish_ambiguous` と同じ**（§3.5）。**出たこと
    # は判っているが、それを記録できない**——記録できないまま inflight を消すと
    # 二重投稿になるので、残して人を呼ぶ。再投稿はしない。
    if not postid_mod.is_usable(post_id):
        msg = ("媒体が返した post_id が台帳に書けない形です"
               "（書き戻しません・再公開もしません・inflight を残します）: "
               f"{chosen.path}")
        log(msg)
        _append_run(state_dir, account_name, run_id, mode, "post", chosen.path, None, now,
                    status="error", error="unusable_post_id")
        inflight_mod.update(state_dir, mismatch_fields=["post_id"])
        return ThrowResult(exit_code=1, mode=mode, action="inflight", message=msg,
                            file=chosen.path, error="unusable_post_id")
    inflight_mod.update(state_dir, post_id=post_id)

    posted_at = publish_result.ts
    # 送った本文そのものを動かせない記録として残す（外部レビュー §3・受け入れ 9・
    # 10・これが正本）。書き戻し（front-matter 書き換え）より前に書く。指紋
    # （外部レビュー再々レビュー P1・1）も併せて残す。
    sent_mod.write(state_dir, post_id=post_id, text=section, body_hash=body_hash,
                    sent_at=posted_at, approved_fingerprint=expected_fingerprint)

    # 絡みの台帳（設計「自分の泉」§4・発注 T0-1）。**公開の確定直後・書き戻しより
    # 前**——post_id の書き戻しが失敗しても、出た事実そのものは変わらない。
    engagement_write_failed, engagement_author_lookup_failed = _record_engagement(
        account_cfg, account_name, media=media, topic=topic, form=chosen.get("form"),
        post_id=post_id, posted_at=posted_at, now=now, log=log,
        reply_to=chosen.get("reply_to"), reply_to_root=chosen.get("reply_to_root"),
        reply_to_author_key=chosen.get("reply_to_author_key"), found_by=chosen.get("found_by"),
        adapter=adapter)

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
    #
    # **本文だけでは足りない**（外部レビュー再々レビュー P1・1）: account・topic・
    # reply_to・publish_at だけを別 clone から書き換えられても、本文の hash は
    # 変わらないのですり抜ける。5 項目の指紋（`expected_fingerprint`）で照合する。
    if not _fingerprint_matches(chosen.path, media, expected_fingerprint):
        # どの項目が食い違ったのかを特定する（外部レビュー第 3 巡・持ち越し項目 C）。
        # ログ・runs・board（inflight 経由）の 3 箇所に出す。人が止まった原因を
        # ファイルを開いて自分で探さずに済むように。
        mismatch_fields = _mismatch_fields(chosen.path, media, expected_components)
        msg = (f"送った内容（本文・account・reply_to・topic・publish_at）と repo の"
               f"内容が食い違います（食い違った項目: {', '.join(mismatch_fields)}）"
               f"（書き戻しません・再公開もしません）: {chosen.path}")
        log(msg)
        _append_run(state_dir, account_name, run_id, mode, "post", chosen.path, post_id, now,
                    status="error", error="text_mismatch_before_writeback",
                    mismatch_fields=mismatch_fields,
                    engagement_write_failed=engagement_write_failed,
                    engagement_author_lookup_failed=engagement_author_lookup_failed)
        # inflight は消さない（§3.5 の「曖昧な失敗」と同じ扱い。人が直すまで
        # このアカウントは次回以降も止まる）。board が同じ内訳を出せるよう
        # inflight にも書いておく（`thth.report.board_summary()` 参照）。
        inflight_mod.update(state_dir, mismatch_fields=mismatch_fields)
        return ThrowResult(exit_code=1, mode=mode, action="inflight",
                            message=msg, file=chosen.path, post_id=post_id,
                            error="text_mismatch_before_writeback",
                            mismatch_fields=mismatch_fields)

    writeback.rewrite_front_matter(chosen.path, status="posted", post_id=post_id, posted_at=posted_at)
    repo_dir = account_cfg["repo_dir"]
    rel_path = os.path.relpath(chosen.path, repo_dir)
    commit_message = f"thth: {account_name} {os.path.basename(chosen.path)} を投稿（post_id {post_id}）"

    # rebase 後の検証が失敗したとき、どの項目が食い違ったかをここへ拾っておく
    # （外部レビュー第 3 巡・持ち越し項目 C）。`validate` は bool しか返せない
    # コールバック契約なので、詳細は外側のこの変数で受け取る
    # （`writeback.commit_and_push()` は validate を最大でも 1 回だけ呼んでから
    # 例外を送出するので、上書きの心配はない）。
    rebase_mismatch_fields: list = []

    def _validate_after_rebase() -> bool:
        ok = _fingerprint_matches(chosen.path, media, expected_fingerprint)
        if not ok:
            rebase_mismatch_fields[:] = _mismatch_fields(chosen.path, media, expected_components)
        return ok

    try:
        ok, err = writeback.commit_and_push(
            repo_dir, rel_path=rel_path, message=commit_message,
            validate=_validate_after_rebase)
    except writeback.PushValidationFailed as e:
        mismatch_fields = rebase_mismatch_fields or ["unknown"]
        msg = str(e) + f"（食い違った項目: {', '.join(mismatch_fields)}）"
        log(msg)
        _append_run(state_dir, account_name, run_id, mode, "post", chosen.path, post_id, now,
                    status="error", error="text_mismatch_after_rebase",
                    mismatch_fields=mismatch_fields,
                    engagement_write_failed=engagement_write_failed,
                    engagement_author_lookup_failed=engagement_author_lookup_failed)
        # push していない（commit はローカルに残る）。inflight も消さない
        # （§3.5 と同じ扱い。次回実行も inflight チェックで止まる・外部レビュー
        # 再レビュー B の受け入れ）。board が同じ内訳を出せるよう inflight にも
        # 書いておく（`thth.report.board_summary()` 参照）。
        inflight_mod.update(state_dir, mismatch_fields=mismatch_fields)
        return ThrowResult(exit_code=1, mode=mode, action="inflight",
                            message=msg, file=chosen.path, post_id=post_id,
                            error="text_mismatch_after_rebase",
                            mismatch_fields=mismatch_fields)

    if not ok:
        log(f"push に失敗しました: {err}")
        # push 失敗では inflight を消さない（§3.5・§4.3・post_id が origin に届くまで残す）
        _append_run(state_dir, account_name, run_id, mode, "post", chosen.path, post_id, now,
                    status="error", error=err,
                    engagement_write_failed=engagement_write_failed,
                    engagement_author_lookup_failed=engagement_author_lookup_failed)
        return ThrowResult(exit_code=1, mode=mode, action="post",
                            message="投稿には成功したが push に失敗しました",
                            file=chosen.path, post_id=post_id, error=err)

    inflight_mod.clear(state_dir)
    _append_run(state_dir, account_name, run_id, mode, "post", chosen.path, post_id, now,
                status="ok", error=None, topic=topic,
                engagement_write_failed=engagement_write_failed,
                engagement_author_lookup_failed=engagement_author_lookup_failed)
    return ThrowResult(exit_code=0, mode=mode, action="post", message="投稿しました",
                        file=chosen.path, post_id=post_id)


def send_once(account_name: str, *, text: str, topic: str | None = None,
               reply_to: str | None = None, reply_to_root: str | None = None,
               reply_to_author_key: str | None = None, found_by: str | None = None,
               production_flag: bool = False,
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

    **`reply_to_root`・`reply_to_author_key`・`found_by`**（T7-2・設計「自分の泉」
    §4）: queue の front-matter と同じ意味の 3 つ。`reply_to` があるときだけ
    絡みの台帳に 1 行残るが、**同席の様態には queue の front-matter が無い**
    ので、これらを `send` の flag として直接受け取る（`thth send --reply-to-root`
    ・`--reply-to-author-key`・`--found-by`）。`reply_to_author_key` を渡さなければ
    best-effort に `adapter.fetch_post(reply_to)` で埋める。
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
            reply_to=reply_to, reply_to_root=reply_to_root,
            reply_to_author_key=reply_to_author_key, found_by=found_by,
            production_flag=production_flag, confirm=confirm,
            adapter_factory=adapter_factory, log=log, now=now,
        )
    except lock_mod.LockBusy:
        msg = f"{account_name} は既に実行中です（ロック取得失敗）"
        log(msg)
        return ThrowResult(exit_code=1, mode="rehearsal", action="locked", message=msg)


def _send_locked(account_name, account_cfg, state_dir, run_id, *, text, topic, reply_to,
                  reply_to_root=None, reply_to_author_key=None, found_by=None,
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

        # send の指紋もフィールド区切りに制御文字を使う。queue と同じく、
        # 正規化や digest 表示より前に拒否する（既存のハッシュ定義は変えない）。
        for field, value in (("text", text), ("reply_to", reply_to), ("topic", topic)):
            control = queuefile.find_control_char(value)
            if control is not None:
                index, codepoint = control
                msg = (f"{field} に制御文字が含まれています"
                       f"（位置 {index}・U+{codepoint:04X}）。送信しません")
                log(msg)
                error = f"control_char({field},U+{codepoint:04X})"
                _append_run(state_dir, account_name, run_id, mode, "skip", None, None,
                            now, status="error", error=error)
                return ThrowResult(exit_code=1, mode=mode, action="skip",
                                   message=msg, error=error)

        body = (text or "").strip()
        if not body:
            log("本文が空です")
            return ThrowResult(exit_code=2, mode=mode, action="none", message="本文が空です")

        media = account_cfg["media"]
        # **上限は台帳の `char_limit` で上書きできる**（設計 v2 §4.2）。
        limit = queuefile.limit_for(media, account_cfg)
        # **数え方も媒体ごと**（`limit_for()` と対・引継ぎ 2026-09-15 §3-D）。
        effective = tags_mod.prepared(media, body, queuefile.normalize_topic(topic),
                                      hashtags=bool(account_cfg.get("hashtags", True)))
        n = queuefile.count_for(media, effective)
        if n > limit:
            # **次の一手を 1 行**（T2・第 1 回の記録 §3）。第 1 回（2026-09-13・L1）は
            # 3 体中 2 体がここで止まり、自分で本文を縮めるか分けるかを迷った。
            # 道具は**切り詰めない**（規約）が、**分けるかどうかを考える口**は
            # ある——`thth forms` が媒体ごとの形と「分ける理由になるもの／
            # ならないもの」を述べる。
            msg = (f"長すぎます（{n} 字・上限 {limit} 字）。切り詰めません。\n"
                   f"分けるかどうかは `thth forms`")
            log(msg)
            _append_run(state_dir, account_name, run_id, mode, "skip", None, None, now,
                        status="error", error=f"too_long({n})")
            return ThrowResult(exit_code=1, mode=mode, action="skip", message=msg)

        topic_value = queuefile.normalize_topic(topic)
        if topic_value is not None:
            topic_err = (queuefile.topic_error(topic_value) if media == "threads"
                         else tags_mod.topic_error(media, topic_value))
            if topic_err is not None:
                log(f"トピックが不正です: {topic_err}")
                _append_run(state_dir, account_name, run_id, mode, "skip", None, None, now,
                            status="error", error=topic_err)
                return ThrowResult(exit_code=1, mode=mode, action="skip", message=topic_err)

        tag_issues = [e for e in tags_mod.errors(media, body, topic_value, account_cfg)
                      if not e.startswith("warning:")]
        if tag_issues:
            msg = tag_issues[0]
            log(msg)
            _append_run(state_dir, account_name, run_id, mode, "skip", None, None, now,
                        status="error", error=msg)
            return ThrowResult(exit_code=1, mode=mode, action="skip", message=msg)

        # 確認用 digest（外部レビュー §1b・受け入れ 6）。`approved_sha` と同じ正規化
        # だが `publish_at` は含めない（send に予約時刻という概念が無いため）。
        digest = approval_mod.compute_send_digest(
            text=body, account=account_name, reply_to=reply_to, topic=topic_value)

        if mode == "rehearsal":
            log("投げるはずの本文:")
            log(effective)
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
        post = adapter_base.Post(text=effective, reply_to=reply_to or None, topic=topic_value,
                                 hashtags_allowed=bool(account_cfg.get("hashtags", True)))

        def on_container_created(container_id):
            inflight_mod.update(state_dir, container_id=container_id)

        result = adapter.publish(post, dry_run=False, on_container_created=on_container_created)

        if result.error or not result.post_id:
            detail_data = api_diagnostic_mod.clean(result.api_diagnostic)
            err = redact_mod.redact(result.error or "不明なエラー")
            log(f"公開失敗: {err}")
            if result.failure == "publish_ambiguous":
                # 出たか分からない → inflight を残して人を呼ぶ（§3.5）
                msg = "公開の結果が分からないので inflight を残します"
                log(msg)
                _append_run(state_dir, account_name, run_id, mode, "post", None, None, now,
                            status="error", error=err, api_diagnostic=detail_data)
                return ThrowResult(exit_code=1, mode=mode, action="inflight", message=msg, error=err, api_diagnostic=detail_data)
            inflight_mod.clear(state_dir)
            _append_run(state_dir, account_name, run_id, mode, "post", None, None, now,
                        status="error", error=err, api_diagnostic=detail_data)
            return ThrowResult(exit_code=1, mode=mode, action="post",
                                message="公開に失敗しました", error=err, api_diagnostic=detail_data)

        # **媒体が返した `post_id` を、確かめずに台帳の鍵にしない**（監査 2 回目・
        # P2-3）。不在の様態（`_throw_chosen()`）には 2026-09-14 に入れた検査が、
        # **同席の様態にだけ無かった**。ここで素通しになると 2 つ壊れる:
        #
        #   1. `post_id` は `sent/<post_id>.json` の**ファイル名**になる。300 文字の
        #      `id` で `open()` が `OSError: File name too long` を上げ、**traceback
        #      が端末に出る**（送信は成功しているのに、何が起きたか読めない）。
        #   2. `..` や `/` を含む `id` は、`postid.to_filename()` を通す前の段階で
        #      台帳の鍵として出回る（runs・board・`thth posts` の突合）。
        #
        # 扱いは `publish_ambiguous` と同じ（§3.5）。**出たことは判っているが記録
        # できない**——inflight を残して人を呼ぶ。`sent/` には書かない（書けない）。
        if not postid_mod.is_usable(result.post_id):
            msg = ("媒体が返した post_id が台帳に書けない形です"
                   "（記録できないので inflight を残します・再送はしません）")
            log(msg)
            _append_run(state_dir, account_name, run_id, mode, "post", None, None, now,
                        status="error", error="unusable_post_id")
            inflight_mod.update(state_dir, mismatch_fields=["post_id"])
            return ThrowResult(exit_code=1, mode=mode, action="inflight", message=msg,
                                error="unusable_post_id")

        # **送った本文そのものを残す**（`state/<account>/sent/<post_id>.json`）。
        # 不在の様態（`_throw_chosen()`）は書き戻しの照合のためにこれを書いていたが、
        # **同席の様態は書いていなかった**——`thth send` には書き戻す front-matter が
        # 無いので、**実際に出た本文がどこにも残らなかった**。queue にも repo にも
        # 無い以上、ここが唯一の正本になる（v2-3・2026-09-13）。`post_id` は媒体に
        # よって `/` を含む（Bluesky の AT URI）ので、パスは `postid` を通す
        # （`sent.path_for()`）。
        sent_mod.write(state_dir, post_id=result.post_id, text=body,
                        body_hash=approval_mod.compute_body_hash(body),
                        sent_at=result.ts or jst.iso(),
                        # 承認の在り処が「masaru がその場で見た本文」なので、
                        # queue の 5 項目の指紋ではなく `--confirm` の digest を残す。
                        approved_fingerprint=digest)

        # 絡みの台帳（設計「自分の泉」§4・T7-2）。**不在の様態（`_throw_chosen()`）
        # にしか配線が無かった**——`thth send --reply-to` で出した返信は台帳に
        # 残らず、`after --reply-to`・`already_replied`・`who` の「→」が埋まら
        # なかった（kopicha の実物の所見）。ここも `_record_engagement()` の
        # 同じ 1 か所を通す（`form` は queue front-matter が無いので `None`）。
        engagement_write_failed, engagement_author_lookup_failed = _record_engagement(
            account_cfg, account_name, media=media, topic=topic_value, form=None,
            post_id=result.post_id, posted_at=result.ts or jst.iso(), now=now, log=log,
            reply_to=reply_to, reply_to_root=reply_to_root,
            reply_to_author_key=reply_to_author_key, found_by=found_by, adapter=adapter)

        inflight_mod.clear(state_dir)
        _append_run(state_dir, account_name, run_id, mode, "post", None, result.post_id, now,
                    status="ok", error=None,
                    engagement_write_failed=engagement_write_failed,
                    engagement_author_lookup_failed=engagement_author_lookup_failed)
        log(f"投稿しました: post_id={result.post_id}")
        # **出たものを見に行ける形で言う**（運用の報告 2026-09-13: 出したあと、
        # 実物を確かめるのに `post_id` から URL を組み立て直していた）。URL を
        # 作るのは媒体の仕事なので、ここは `PublishResult.url` をそのまま出す
        # だけ——**無ければ黙る**（Threads は公開の応答から URL を返さない。
        # 組み立てた推測を URL として出さない）。
        if result.url:
            log(f"URL: {result.url}")
        return ThrowResult(exit_code=0, mode=mode, action="post", message="投稿しました",
                            post_id=result.post_id, url=result.url)
