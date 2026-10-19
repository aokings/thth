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
import time
import uuid

from . import accounts as accounts_mod
from . import adapters as adapters_mod
from . import approval as approval_mod
from . import engagements as engagements_mod
from . import goals as goals_mod
from . import inflight as inflight_mod
from . import inflight_resolve as inflight_resolve_mod
from . import media_delivery
from . import media as media_mod
from . import jst, leave_gate
from . import lock as lock_mod
from . import postid as postid_mod
from . import publish_hold as publish_hold_mod
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
    # burst の順番待ちに並べた返信の出す時刻（`action == "queued"`・3.25.0）。
    publish_at: str | None = None


def _link_urls_kwargs(manifest) -> dict:
    """sent に控える添付のリンク先（設計 3.7.0 §A1）。添付が無ければ何も足さない。

    添付があれば空でも書く——click を投稿単位で数えるとき、「添付にリンクが無かった」と
    「控えていない（3.7.0 より前）」を分けるため（`click_attribution.recorded_links`）。
    """
    if not manifest:
        return {}
    from . import click_attribution
    # 書けない形（長すぎる）で公開後の記録を落とさない——控えられるものだけ。
    return {"link_urls": [url for url in click_attribution.manifest_links(manifest)
                          if len(url) <= 2048][:10]}


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
        if select_mod.is_reply(fm):
            # 4.3（監査 中-2）: 最短間隔（`min_interval_hours`）は投稿の時刻だけから数える。返信で投稿を押さない。
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


def held_items_for_account(account_name: str, account_cfg: dict, *, now) -> list:
    """承認済みなのに出られない原稿（設計 3.3.0 A1）。**読むだけ**（select を 1 回）。

    照合先は board と同じ `writeback.upstream_sha()`（同期を伴わない読み手）。
    `thth run` は throw が同期を済ませた直後に呼ぶので、その run が見た状態と
    同じ commit を読む。`due` を問わず返す（通知に数えるのは呼び出し側で `due`
    だけ・`select.held_reason_code()`）。
    """
    if accounts_mod.repo_state(account_cfg) == accounts_mod.REPO_NONE:
        return []
    files = list_queue_files(
        account_cfg, tree_sha=writeback.upstream_sha(account_cfg.get("repo_dir")))
    recent = select_mod.recent_posted_texts(
        files, account_name=account_name, media=account_cfg["media"], now=now)
    result = select_mod.select_one(
        files, account_name=account_name, account_cfg=account_cfg, now=now,
        last_post_at=last_post_at(files, account_name), recent_texts=recent)
    return select_mod.held_items(result, files, now)


def held_applies(account_cfg: dict | None) -> bool:
    """`held` を数える account か: timer に載る（`scheduled`・既定 true）本番だけ。

    rehearsal は元から何も出さないので「出せるはずのものが出られない」に当たらない
    （運用通知も rehearsal では黙る・`incident.notify()`）。
    """
    cfg = account_cfg or {}
    return bool(cfg.get("scheduled", True)) and bool(cfg.get("production"))


_ERROR_RUN_REASONS = ("hashtag", "duplicate_text", "approval_stale", "unverified_content")


def _hatena_refusal_noted(reason) -> bool:
    """はてなの配信原稿の断りのうち、run のログに次の一手を出すもの（設計 4.0 S2 §8 の 2）。"""
    from . import hatena_entry
    return reason == "approval_stale" or reason in hatena_entry.S2_REFUSALS


def _is_error_reason(reason: str) -> bool:
    # reply_to_file（設計 3.2.0 §2）: 指した原稿が取り下げ・読めない・消えた、と
    # 書き方の誤り（承認後に指した原稿の account が変わった等）は人が直すので
    # 実エラーとして runs に残す。**待ち（`waiting_for`）は誤りではない**ので残さない。
    return (
        reason in _ERROR_RUN_REASONS
        or reason.startswith("too_long(")
        or reason.startswith("topic_")
        or reason.startswith("control_char(")
        or reason.startswith(select_mod.unresolved("target_"))
        or reason == queuefile.REPLY_TO_CONFLICT
        or reason.startswith("reply_to_file_")
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
def _account_locks(account_name: str, account_cfg: dict, state_dir: str, wait=0):
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

    **はてなの口座は主体のロックを挟む: repo → principal → account**（設計 4.0 S1 §4.3）。
    同じはてな ID を使う別レーンの 2 process は repo が違うので repo では待たず、principal で
    1 つが待つ。順は全順序（repo < principal < account）で、principal を持ったまま repo を
    待つ経路は無い。SNS の口座は principal を取らない（今までどおり 2 段）。待つ締め切りは
    3 つで共有する（timer の run は 60 秒）。
    """
    from . import principals as principals_mod
    repo_lock_path = accounts_mod.repo_lock_path_for(account_cfg["repo_dir"])
    account_lock_path = accounts_mod.account_lock_path_for(account_name)
    repo_lock = lock_mod.AccountLock(repo_lock_path)
    principal_lock = None
    if principals_mod.uses_principal(account_cfg):
        principals_mod.ensure_state_dir(account_cfg["principal"])
        principal_lock = lock_mod.AccountLock(principals_mod.lock_path_for(account_cfg["principal"]))
    account_lock = lock_mod.AccountLock(account_lock_path)

    deadline = lock_mod.time.monotonic() + lock_mod.wait_seconds(wait)
    lock_mod.acquire(repo_lock, wait)
    try:
        if principal_lock is not None:
            lock_mod.acquire(principal_lock, max(0, deadline - lock_mod.time.monotonic()))
        try:
            lock_mod.acquire(account_lock, max(0, deadline - lock_mod.time.monotonic()))
            try:
                yield
            finally:
                account_lock.release()
        finally:
            if principal_lock is not None:
                principal_lock.release()
    finally:
        repo_lock.release()


# **timer の実行は repo のロックを少し待つ**（masaru 10/12「直す」）。同じ repo に
# ぶら下がるアカウントは、名前のハッシュで決まる起点（`systemd_gen.offset_minutes`）が
# 重なると同じ分に起動する。待たないと、片方の実行（kopicha-x と
# kopicha-mastodon は 10/8 から 1 日 70〜85 回）が丸ごと見送られて exit 1 になっていた。
# 60 秒は 10 分の刻みと service の timeout（300 秒）より十分短い。手で打つ
# `thth throw` などは今までどおり待たない（`--wait` で選ぶ）。
RUN_LOCK_WAIT_SECONDS = 60


def throw_once(account_name: str, *, production_flag: bool = False,
                bypass_pace: bool = False, adapter_factory=None, log=None,
                now=None, wait=0, fotolife_factory=None) -> ThrowResult:
    """§3.3 の順序で 1 アカウント分を投げる（dry-run 既定）。

    台帳 `max_per_run`（既定 1）まで「select → throw」を繰り返す（1 本ごとに
    select をやり直す・masaru 指摘 2026-09-09・`_throw_locked()` 参照）。返り値は
    最後に処理した結果（何本出たかは log と runs から分かる）。

    `production_flag` は CLI の `--production`。台帳 `production: true` が commit
    されていない限り、これを付けても dry-run のまま（fail-closed。§0・受け入れ 6）。
    `now` はテスト用の時刻注入（省略時は `jst.now_jst()`）。CLI・timer は渡さない。
    `wait` はロックを待つ秒数（既定 0＝待たない。timer は `RUN_LOCK_WAIT_SECONDS`）。
    `fotolife_factory` ははてなの口座の画像の段（フォトライフ・設計 4.0 S3 §6.1）の差し替え口
    （None なら本物）。
    """
    log = log or (lambda line: None)
    adapter_factory = adapter_factory or _default_adapter_factory
    run_id = uuid.uuid4().hex[:12]
    # 返信の時間（150 秒）は入口から数える（ロックを待った分も含める・監査 中-4）。
    entered_at = _monotonic()

    account_cfg = accounts_mod.load_account(account_name)
    state_dir = accounts_mod.state_dir_for(account_name)

    # **媒体がアカウント側の断りを返した口座は、自動では投げ直さない**（3.19.2・
    # `publish_hold`）。10/14 に kopicha-x が X の 403（x_not_permitted）を 61 回
    # 受けた。スレッド連投も同じ口座から出るので、その分岐より前で止める。
    # runs には書かない（inflight で止まっている run と同じ・毎回同じ行が積もるだけ）。
    # 止まっていることは死活通知・運用通知・board・morning が言う。
    hold_row = publish_hold_mod.held(account_name)
    if hold_row is not None:
        msg = publish_hold_mod.message(account_name, hold_row)
        log(msg)
        if bool(account_cfg.get("production")) and production_flag:
            return ThrowResult(exit_code=2, mode="production", action="publish_held", message=msg,
                               file=hold_row.get("file"),
                               error=hold_row.get("reason") or publish_hold_mod.UNREADABLE)

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
        with _account_locks(account_name, account_cfg, state_dir, wait=wait):
            return _throw_locked(
                account_name, account_cfg, state_dir, run_id,
                production_flag=production_flag, bypass_pace=bypass_pace,
                adapter_factory=adapter_factory, log=log, now=now,
                fotolife_factory=fotolife_factory, started_at=entered_at,
            )
    except lock_mod.LockBusy:
        msg = f"{account_name} は既に実行中です（ロック取得失敗）"
        log(msg)
        return ThrowResult(exit_code=1, mode="rehearsal", action="locked", message=msg)


def due_reply_paths(paths, *, now=None) -> list:
    """承認した原稿のうち、返信で `publish_at` が今か過去のもの（4.3・承認の場の送りの対象）。"""
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    out = []
    for path in paths:
        try:
            qf = queuefile.parse(path)
        except (OSError, ValueError):
            continue
        fm = qf.front_matter
        if qf.malformed or fm.get("status") != "approved" or fm.get("post_id") or not select_mod.is_reply(fm):
            continue
        try:
            at = queuefile.parse_publish_at(fm.get("publish_at"))
        except (TypeError, ValueError):
            continue
        if at <= now:
            out.append(path)
    return out


def _mapped_paths(paths, account_cfg) -> tuple:
    """承認した原稿のパスを、口座の repo（`repo_dir`）の中の同じ相対パスに置き換える（監査 低-3）。

    承認は VM の口座の clone で打つのがふつうだが、別の clone の原稿でも repo の中の相対パスが同じなら同じ原稿
    （push 済み・口座の同期で届く）。戻り値 `(口座の repo の実パスの集合, repo の外で突き合わせられなかった本数)`。
    """
    repo = os.path.realpath(account_cfg["repo_dir"])
    mapped, outside = set(), 0
    for path in paths:
        top = writeback.repo_toplevel(path)
        if top is None:
            outside += 1
            continue
        rel = os.path.relpath(os.path.realpath(path), os.path.realpath(top))
        mapped.add(os.path.realpath(os.path.join(repo, rel)))
    return mapped, outside


def send_due_replies(account_name: str, paths, *, adapter_factory=None, log=None, now=None,
                     wait=0) -> dict:
    """承認の場の送り（4.3・設計「返信の扱い（4.3）」の 7）: 承認した返信のうち時刻の来たものを、timer と
    同じ道（口座と repo のロック・同期・select・返信の窓と日の上限・時間）でその場で出す。投稿は出さない。

    戻り値 `{"sent", "waiting", "reason", "next_at", "skipped", "error", "message"}`。`skipped` は送らなかった
    理由（`not_sns`・`not_production`・`not_scheduled`・`publish_held`・`guard_stopped`・`stopped`・`missing_token`・
    `locked`・`nothing_due`・`account_unreadable`・`not_in_repo`）。ロックは待たない（`wait=0`・timer が動いて
    いれば timer が出す・監査 低-1）。送れなかった分は queue に残り timer が出す。
    """
    entered_at = _monotonic()
    log = log or (lambda line: None)
    adapter_factory = adapter_factory or _default_adapter_factory
    out = {"account": account_name, "sent": 0, "waiting": 0, "reason": None, "next_at": None,
           "skipped": None, "error": None, "message": None}
    due = due_reply_paths(paths, now=now)
    if not due:
        out["skipped"] = "nothing_due"
        return out
    try:
        account_cfg = accounts_mod.load_account(account_name)
    except accounts_mod.AccountStopped:
        out["skipped"] = "stopped"
        return out
    except accounts_mod.AccountError:
        out["skipped"] = "account_unreadable"
        return out
    from . import guard as guard_mod, principals
    if account_cfg.get("media") == "hatenablog" or principals.uses_principal(account_cfg):
        out["skipped"] = "not_sns"
    elif not bool(account_cfg.get("production")):
        out["skipped"] = "not_production"
    elif account_cfg.get("scheduled", True) is False:
        out["skipped"] = "not_scheduled"
    elif publish_hold_mod.held(account_name) is not None:
        out["skipped"] = "publish_held"
    elif guard_mod.stopped(account_name) is not None:
        # 安全装置か持ち主が止めた口座（監査 中-3）。timer の道も止めの印は見ないが、承認の場では送らない。
        out["skipped"] = "guard_stopped"
    elif not accounts_mod.token_exists(account_cfg):
        out["skipped"] = "missing_token"
    if out["skipped"]:
        return out
    only, outside = _mapped_paths(due, account_cfg)
    if not only:
        out["skipped"] = "not_in_repo"
        return out
    state_dir = accounts_mod.state_dir_for(account_name)
    stats = {}
    try:
        with _account_locks(account_name, account_cfg, state_dir, wait=wait):
            result = _throw_locked(
                account_name, account_cfg, state_dir, uuid.uuid4().hex[:12],
                production_flag=True, bypass_pace=False, adapter_factory=adapter_factory, log=log, now=now,
                only_paths=only, replies_only=True, stats=stats, started_at=entered_at, via="approve")
    except lock_mod.LockBusy:
        out["skipped"] = "locked"
        return out
    except accounts_mod.AccountStopped:
        out["skipped"] = "stopped"
        return out
    out.update({"sent": stats.get("replies_sent", 0), "waiting": stats.get("replies_waiting", 0),
                "reason": stats.get("waiting_reason"),
                "next_at": jst.iso(stats["next_at"]) if stats.get("next_at") is not None else None,
                "message": result.message,
                "window_count": stats.get("window_count"), "window_minutes": stats.get("window_minutes")})
    missing = outside + sum(1 for path in only if not os.path.exists(path))
    if result.exit_code != 0:
        out["error"] = result.error or result.action
    elif missing and not out["sent"] and not out["waiting"]:
        out["skipped"] = "not_in_repo"
    elif not out["sent"] and not out["waiting"] and result.rejections:
        out["error"] = result.rejections[0]["reason"]
    return out


# 承認の場の送りで送らなかった理由（人向けの 1 句・`--json` は符丁のまま）。
SKIP_PHRASES = {
    "not_production": "本番の口座でないので（production: true でない）timer に任せます",
    "not_scheduled": "timer に載っていない口座なので送りません（scheduled: false）",
    "publish_held": "媒体の断りで自動の投稿を止めているので送りません（thth account status で確かめてください）",
    "guard_stopped": "口座が止まっているので送りません（戻すのは thth account resume）",
    "stopped": "口座が止まっているので送りません",
    "missing_token": "token が無いので送りません",
    "locked": "いま timer が動いているので、その場では送りません（timer が出します）",
    "account_unreadable": "台帳を読めないので送りません",
    "not_in_repo": "承認した原稿が口座の repo に見つからないので送りません（承認した clone と口座の repo_dir を確かめてください）",
}
# 送れなかった理由（select の断り・公開の失敗の符丁）の 1 句。
ERROR_PHRASES = {
    "quiet_hours": "夜間（quiet_hours）なので明けてから timer が出します",
    "future": "時刻がまだ来ていないので timer が出します",
    "approval_stale": "承認のあとに中身が変わったので出しません（承認し直してください）",
    "unverified_content": "push した中身と口座の repo が一致しないので timer に任せます",
    "inflight": "前の公開の結果がまだ決まっていないので timer に任せます",
    "stale": "時刻から日が経ちすぎているので出しません",
}


def error_phrase(code) -> str:
    text = str(code or "")
    for key, phrase in ERROR_PHRASES.items():
        if text == key or text.startswith(key + ":") or text.startswith(key + "("):
            return phrase
    if text.startswith("reply_to_unresolved"):
        return "返信先がまだ決まらないので timer に任せます"
    return "送れなかった分は timer に残ります"


def due_replies_lines(results) -> list:
    """承認の場の送りの人向けの文（口座ごと）。送らなかった口座は理由を 1 句で。"""
    lines = []
    for row in results:
        name = row["account"]
        if row["skipped"] in ("nothing_due", "not_sns"):
            continue
        if row["skipped"]:
            lines.append(f"{name}: 時刻の過ぎた返信を、{SKIP_PHRASES.get(row['skipped'], '送りませんでした')}")
            continue
        line = f"{name}: 承認して {row['sent']} 本送りました"
        if row["waiting"]:
            line += "・" + waiting_phrase(row["reason"], row["waiting"], row)
        if row["error"]:
            line += f"（{error_phrase(row['error'])}）"
        lines.append(line)
    return lines


def _append_run(state_dir: str, account_name: str, run_id: str, mode: str, action: str,
                 file: str | None, post_id: str | None, now, *, status: str, error: str | None,
                 topic: str | None = None, mismatch_fields: list | None = None,
                 engagement_write_failed: bool = False,
                 engagement_author_lookup_failed: bool = False,
                 api_diagnostic: dict | None = None,
                 resolved_from: dict | None = None, extra: dict | None = None) -> None:
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
        # reply_to_file（設計 3.2.0 §3）: どの原稿から返信先を解決したか。
        # 解決して出しに行った行だけに入る（それ以外は None）。
        "reply_to_file": (resolved_from or {}).get("file"),
        "resolved_from": dict(resolved_from) if resolved_from else None,
    }
    # 足す鍵（設計 3.3.1: `remote_state`・`inflight_resolution`）。値があるときだけ
    # 載せる——無い run の行の形は変えない。
    for key, value in (extra or {}).items():
        if value is not None:
            record[key] = value
    runs_mod.append_run(state_dir, record, jst.month_str(now))


# 公開の応答は分からなかったが、媒体に訊いて決まったとき（設計 3.3.1 §2）の 1 行。
_REMOTE_RECOVERED = {
    "retried": ("公開の応答は分からなかったが container が FINISHED（まだ出ていない）"
                "だったので、1 回だけ再試行して出ました"),
    "published_located": ("公開の応答は分からなかったが container は PUBLISHED で、"
                          "自分の最近の投稿から本文の指紋が一致する 1 件を見つけました"),
}


def _remote_extra(publish_result) -> dict:
    state = getattr(publish_result, "remote_state", None)
    return {"remote_state": state} if state in inflight_resolve_mod.REMOTE_STATES else {}


def _note_remote_state(state_dir: str, publish_result, log) -> None:
    """inflight に「最後の問い合わせ結果」を写す（`thth inflight show` が出す）。

    書けなくても止め方は変えない（inflight は残っている）。添付の inflight は
    journal が別の約束なので触らない。
    """
    state = getattr(publish_result, "remote_state", None)
    if state not in inflight_resolve_mod.REMOTE_STATES:
        return
    try:
        record = inflight_mod.read(state_dir)
        if not isinstance(record, dict) or record.get("media"):
            return
        inflight_mod.update(state_dir, remote_state=state, remote_checked_at=jst.iso())
    except (OSError, ValueError):
        log("inflight に問い合わせ結果を書けませんでした（inflight は残っています）")


# 4.3（設計「上限と連投の柵」の「返信の扱い（4.3）」）: 返信どうしの間（本番の run・2 本目から）と、1 回の実行で
# 返信に使う時間。timer の service は 300 秒で切られる（`TimeoutStartSec=300`）。時間は `throw_once` の入口（ロックを
# 待つ前）から数え（監査 中-4）、1 本の見込み（Threads は container を作って 30 秒待つので 40 秒）を足して上限を
# 超えるなら、その返信は始めない（投稿で時間を使った回は 1 本目の返信も止める）。残りは次の run が出す。
REPLY_GAP_SECONDS = 3
REPLY_RUN_BUDGET_SECONDS = 150
REPLY_ESTIMATE_SECONDS = {"threads": 40}
REPLY_ESTIMATE_DEFAULT_SECONDS = 5
# 試験の差し替え口（時計と待ち）。
_monotonic = time.monotonic
_pause = time.sleep


def reply_estimate_seconds(media) -> int:
    """返信 1 本にかかる見込みの秒（媒体ごと）。"""
    return REPLY_ESTIMATE_SECONDS.get(media, REPLY_ESTIMATE_DEFAULT_SECONDS)


class _ReplyWindow:
    """1 回の run の返信の窓と日の上限（`guard.reply_window` の写し + この run で出した返信）。"""

    def __init__(self, info, now):
        self.count = info["count"]
        self.minutes = info["minutes"]
        self.times = list(info["times"])
        self.daily = info.get("daily")
        self.daily_used = info.get("daily_used") or 0
        self.now = now

    def refusal(self) -> str | None:
        """もう 1 本出せなければ理由（`daily`＝日の上限・`window`＝返信の窓）。出せれば None。"""
        if self.daily is not None and self.daily_used + 1 > self.daily:
            return "daily"
        if len(self.times) + 1 > self.count:
            return "window"
        return None

    def note(self) -> None:
        self.times.append(self.now)
        self.daily_used += 1

    def next_at(self, reason):
        if reason == "daily":
            day = jst.to_jst(self.now).date() + datetime.timedelta(days=1)
            return datetime.datetime.combine(day, datetime.time(0, 0), tzinfo=jst.JST)
        from . import assess
        return assess.window_slot(self.times, self.count, self.minutes, self.now)


def _reply_window_for(account_name, account_cfg, now):
    """返信の窓。はてなの口座・表が作れないときは None（返信も `max_per_run` に数える 4.2 までの形）。"""
    if account_cfg.get("media") == "hatenablog":
        return None
    from . import guard as guard_mod
    try:
        info = guard_mod.reply_window(account_name, account_cfg, now=now)
    except (OSError, ValueError):
        info = None
    return _ReplyWindow(info, now) if info is not None else None


def _when_phrase(at) -> str:
    if at is None:
        return "次の timer で出ます"
    at = jst.to_jst(at)
    if at.time() == datetime.time(0, 0):
        return f"{at.strftime('%m/%d')} 0 時以降の timer で出ます"
    return f"{at.strftime('%H:%M')} ごろの timer で出ます"


def waiting_phrase(reason, waiting, stats) -> str:
    """残した返信の 1 句（窓・日の上限・時間）。"""
    when = _when_phrase(stats.get("next_at") if not isinstance(stats.get("next_at"), str)
                        else jst.parse(stats["next_at"]))
    if reason == "window":
        return (f"{waiting} 本は返信の窓（{stats.get('window_minutes')} 分に {stats.get('window_count')} 件）を"
                f"超えたので {when}")
    if reason == "daily":
        return f"{waiting} 本は今日の返信の上限（reply.daily）に達したので {when}"
    return f"{waiting} 本は 1 回の実行の時間（{REPLY_RUN_BUDGET_SECONDS} 秒）に収まらないので {when}"


def reply_summary_line(account_name, stats) -> str | None:
    """返信をまとめて出した run の人向けの 1 行（出した本数・窓や時間で残した本数と次の時刻）。"""
    sent = stats.get("replies_sent", 0)
    waiting = stats.get("replies_waiting", 0)
    if not sent and not waiting:
        return None
    line = (f"{account_name}: 返信を {sent} 本出すはずです（dry-run）" if stats.get("mode") == "rehearsal"
            else f"{account_name}: 返信を {sent} 本出しました")
    if waiting:
        line += "・" + waiting_phrase(stats.get("waiting_reason"), waiting, stats) + "（口座は止めていません）"
    return line


def _count_waiting(files, account_name, excluded, only_paths, now) -> int:
    """窓・日の上限・時間で残した返信の本数（時刻の来た承認済みの返信のうち、この run で出していないもの・1 回で数える）。"""
    count = 0
    for qf in files:
        fm = qf.front_matter
        if (qf.malformed or qf.path in excluded or fm.get("account") != account_name
                or fm.get("status") != "approved" or fm.get("post_id") or not select_mod.is_reply(fm)):
            continue
        if only_paths is not None and os.path.realpath(qf.path) not in only_paths:
            continue
        try:
            at = queuefile.parse_publish_at(fm.get("publish_at"))
        except (TypeError, ValueError):
            continue
        if at <= now:
            count += 1
    return count


def _throw_locked(account_name, account_cfg, state_dir, run_id, *,
                   production_flag, bypass_pace, adapter_factory, log, now=None,
                   fotolife_factory=None, only_paths=None, replies_only=False,
                   stats=None, started_at=None, via="timer") -> ThrowResult:
    """ロックの中の 1 回の実行。`only_paths`（実パスの集合）・`replies_only` は承認の場の送り（4.3）が使う:
    指した原稿のうち返信だけを出す。`stats` には返信の内訳（出した本数・残した本数・理由・次の時刻）を書く。
    `started_at`（`_monotonic()` の値）は返信の時間を数える起点（`throw_once` の入口・監査 中-4）。`via` は
    媒体の断りの記録に残す道（`timer`・承認の場の送りは `approve`）。
    """
    stats = stats if stats is not None else {}
    stats.update({"replies_sent": 0, "replies_waiting": 0, "posts_sent": 0, "waiting_reason": None,
                  "next_at": None})
    # inflight が残っていれば何もしない（§3.5）。ここは throw_once 1 回につき
    # 1 度だけ見る（下のループ中に新たに inflight が残るのは「曖昧な失敗」の
    # ときだけで、そのときはその場で打ち切って返すので読み直す必要が無い）。
    #
    # **止まる前に 1 回だけ媒体に訊く**（設計 3.3.1 §3）。決まれば道具が解いて
    # 先へ進み、決まらなければ従前どおり止まる。訊くのは本番の run だけ——
    # 解いたときの書き戻し（commit と push）は本番の書き込みなので、dry-run の
    # `thth throw` は従前どおり止まるだけにする。
    existing_inflight = inflight_mod.read(state_dir)
    if existing_inflight is not None:
        outcome = None
        if bool(account_cfg.get("production")) and production_flag:
            outcome = inflight_resolve_mod.self_resolve(
                account_name, account_cfg, state_dir, existing_inflight,
                adapter_factory=adapter_factory, run_id=run_id,
                now=now if now is not None else jst.now_jst(), log=log)
        if outcome is None or not outcome.resolved:
            msg = f"inflight が残っています: {existing_inflight.get('file')}"
            log(msg)
            # 訊いても決まらなかったときは `inflight_unresolved`（死活通知の理由・
            # 次の一手は `thth inflight <account> resolve`）。出たと分かったのに
            # 記録できなかったときはその理由。訊いていなければ従前どおり None。
            error = None
            if outcome is not None and outcome.asked:
                error = outcome.error or "inflight_unresolved"
            return ThrowResult(exit_code=1, mode="rehearsal", action="inflight", message=msg,
                                file=existing_inflight.get("file"), error=error,
                                post_id=outcome.post_id if outcome is not None else None)

    # fail-closed: 台帳 production: true が commit されていない限り dry-run（§0）
    production = bool(account_cfg.get("production")) and production_flag
    mode = "production" if production else "rehearsal"
    log(f"mode: {mode}")

    # 試験の時計（`now` を渡した run）では返信どうしの間を待たない（4.3）。
    now_injected = now is not None
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

    # **返信は `max_per_run` の外**（4.3）: 投稿（返信でないもの）は `max_per_run` 本まで、返信は返信の窓
    # （`reply.burst`・既定 10 分に 10）と 1 回の時間（`REPLY_RUN_BUDGET_SECONDS`）に入るだけ出す。窓・時間を
    # 超えた返信は queue に残り次の run が出す（`publish_at` は書き換えない＝指紋を変えない）。表が作れなければ
    # 返信も `max_per_run` に数える（4.2 までの振る舞い）。
    window = _reply_window_for(account_name, account_cfg, now)
    stats["mode"] = mode
    if window is not None:
        stats.update({"window_count": window.count, "window_minutes": window.minutes})

    def _as_reply(front_matter):
        # 表が作れない（窓が無い）ときは返信も投稿と同じに数える（`max_per_run` の内）。
        return window is not None and select_mod.is_reply(front_matter)

    posts_left = 0 if replies_only else max_per_run
    skip_posts = replies_only
    replies_closed = False
    replies_done = 0
    started_at = _monotonic() if started_at is None else started_at
    estimate = reply_estimate_seconds(account_cfg.get("media"))
    # 1 回の run で回す上限（選ぶたびに 1 本出すか、投稿・返信のどちらかを打ち切るので、ふつうは届かない）。
    for _ in range(max_per_run + 1000):
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
        candidates = [qf for qf in files if qf.path not in excluded_paths
                      and (only_paths is None or os.path.realpath(qf.path) in only_paths)
                      and not (skip_posts and not _as_reply(qf.front_matter))
                      and not (replies_closed and _as_reply(qf.front_matter))]

        # 指した原稿（reply_to_file）は候補から外したものも含めた全ファイルから探す
        # （dry-run で一度選んだ問いを外すと、答えの側で「無い」に化ける）。
        result = select_mod.select_one(
            candidates, account_name=account_name, account_cfg=account_cfg,
            now=now, last_post_at=last_at, recent_texts=recent_texts, bypass_pace=bypass_pace,
            pool=files)

        for rej in result.rejections:
            # はてなの配信原稿は、承認が古い・S2 の断り（画像・相対リンク・未 commit）を run のログに
            # 次の一手つきで 1 行出す（設計 4.0 S2 §8 の 2）。SNS の口座は今までどおり。
            hatena_note = (account_cfg.get("media") == "hatenablog"
                           and _hatena_refusal_noted(rej.reason))
            if (_is_error_reason(rej.reason) or hatena_note) and rej.file not in logged_error_paths:
                logged_error_paths.add(rej.file)
                _append_run(state_dir, account_name, run_id, mode, "skip", rej.file, None, now,
                            status="error", error=rej.reason)
                if hatena_note:
                    from . import hatena_entry
                    log(hatena_entry.refusal_log_line(rej.file, rej.reason))

        if result.chosen is None:
            msg = "出すものが無い"
            if last_result is None and not replies_closed:
                log(msg)
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
        is_reply = _as_reply(chosen.front_matter)
        if is_reply:
            reason = window.refusal()
            if reason is None and _monotonic() - started_at + estimate > REPLY_RUN_BUDGET_SECONDS:
                reason = "time"
            if reason is not None:
                # 日の上限・窓・時間を超えた。この run ではもう返信を出さない（残りは queue のまま・口座は止めない）。
                # 残りは 1 回で数える（監査 低-4・選び直しの空回りをしない）。
                replies_closed = True
                stats["waiting_reason"] = reason
                stats["next_at"] = window.next_at(reason) if reason != "time" else None
                stats["replies_waiting"] = _count_waiting(files, account_name, excluded_paths, only_paths, now)
                continue
            if replies_done and production and now_injected is False:
                _pause(REPLY_GAP_SECONDS)
        elif posts_left <= 0:
            # 投稿は `max_per_run` 本まで。返信が残っていれば続けて選ぶ。
            skip_posts = True
            continue
        excluded_paths.add(chosen.path)
        if account_cfg.get("media") == "hatenablog":
            # はてなブログの配信原稿（設計 4.0 S1 §6.5）。送信は S2 の adapter（無ければ出さない）。
            from . import hatena_publish
            one_result = hatena_publish.throw_chosen(
                account_name, account_cfg, state_dir, run_id, mode, chosen, now, log,
                adapter_factory=adapter_factory, fotolife_factory=fotolife_factory)
        else:
            one_result = _throw_chosen(
                account_name, account_cfg, state_dir, run_id, mode, chosen, result.section,
                now, log, adapter_factory, resolution=result.resolution, via=via)
        last_result = one_result
        if one_result.exit_code == 0:
            # 出した（dry-run は出したつもり）分を数える。dry-run も窓と本数で「まとめて出したら何が出るか」を見せる。
            if is_reply:
                replies_done += 1
                window.note()
                stats["replies_sent"] = replies_done
            else:
                posts_left -= 1
                stats["posts_sent"] = stats["posts_sent"] + 1
        if one_result.action == "post" and one_result.exit_code == 0:
            posted_count += 1
            if not is_reply:
                # 4.3（監査 中-2）: 最短間隔の起点は投稿だけ（返信で投稿を押さない）。
                run_last_post_at = now
        if one_result.exit_code != 0:
            # 途中で失敗（曖昧な失敗で inflight を残す等）したらそこで打ち切る
            # （設計 §3.3）。
            break

    log(f"{account_name}: この実行で {posted_count} 本投げました"
        f"（mode={mode}・上限 max_per_run={max_per_run}・返信は max_per_run の外）")
    line = reply_summary_line(account_name, stats)
    if line:
        log(line)
    if last_result is None:
        # 返信が窓・時間で残っただけで、この run では 1 本も出していない。
        msg = line or "出すものが無い"
        _append_run(state_dir, account_name, run_id, mode, "none", None, None, now, status="ok", error=None)
        return ThrowResult(exit_code=0, mode=mode, action="none", message=msg)
    return last_result


def _fingerprint_account_cfg(fm: dict, media: str, account_cfg: dict | None):
    if account_cfg is not None:
        return account_cfg
    try:
        return accounts_mod.load_account(fm.get("account"))
    except accounts_mod.AccountError:
        # Legacy Threads fixture/incident callers have no account ledger; its
        # untransformed fingerprint does not depend on account tag policy.
        if media == "threads":
            return {"media": "threads"}
        raise


def _current_fingerprint(path: str, media: str,
                         account_cfg: dict | None = None) -> str | None:
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
        cfg = _fingerprint_account_cfg(fm, media, account_cfg)
        return approval_mod.compute_approved_sha(
            section=approval_mod.effective_section(current_section or "", cfg, fm.get("topic")), account=fm.get("account"),
            reply_to=approval_mod.reply_to_for_fingerprint(fm), topic=fm.get("topic"),
            publish_at=fm.get("publish_at"),
            media_manifest=media_mod.manifest_for(fm,cfg),
            **approval_mod.publish_options(fm))
    except (ValueError, TypeError, accounts_mod.AccountError):
        return None


def _fingerprint_matches(path: str, media: str, expected_fingerprint: str,
                         account_cfg: dict | None = None) -> bool:
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
    current_fingerprint = _current_fingerprint(path, media, account_cfg)
    return current_fingerprint is not None and current_fingerprint == expected_fingerprint


def _mismatch_fields(path: str, media: str, expected_components: dict,
                     account_cfg: dict | None = None) -> list:
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
        cfg = _fingerprint_account_cfg(fm, media, account_cfg)
        current_components = approval_mod.compute_approved_components(
            section=approval_mod.effective_section(current_section or "", cfg, fm.get("topic")), account=fm.get("account"),
            reply_to=approval_mod.reply_to_for_fingerprint(fm), topic=fm.get("topic"),
            publish_at=fm.get("publish_at"),
            media_manifest=media_mod.manifest_for(fm,cfg),
            **approval_mod.publish_options(fm))
    except (ValueError, TypeError, accounts_mod.AccountError):
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


def _reply_to_for_post(chosen, resolution, account_name) -> tuple:
    """公開に渡す `reply_to` と、出さない理由（出すなら None）の組。

    **`reply_to_file` を持つのに解決していなければ出さない**（設計 3.2.0 §0・§2）。
    `reply_to` 無しで送る——root に落とす——分岐はここに**置かない**。select が
    解決した post_id（`resolution`）が無い・名前が食い違う・usable でない、の
    どれでも断る。さらに公開の直前に指した原稿を読み直し、select のあとで取り下げ
    られた・消えた・別の post_id になった場合も断る。`reply_to`（post_id 直書き）
    の道は従前どおり。
    """
    fm = chosen.front_matter
    name = queuefile.reply_to_file_of(fm)
    if name is None:
        if queuefile.reply_to_file_static_problem(chosen.path, fm) is not None:
            return None, queuefile.REPLY_TO_FILE_INVALID
        return chosen.get("reply_to") or None, None
    if (not resolution or resolution.get("file") != name
            or not postid_mod.is_usable(resolution.get("post_id"))):
        return None, select_mod.UNRESOLVED
    again = select_mod.resolve_reply_to_file(chosen, account_name=account_name,
                                             require_verified=False)
    if again is None or again.reason is not None or again.post_id != resolution["post_id"]:
        return None, select_mod.UNRESOLVED
    return resolution["post_id"], None


def _announce_for_post(chosen, resolution, account_cfg):
    """告知（4.0 S4 §9.4）: `(announced | None, 断りの符丁 | None)`。

    `announces` の無い原稿は `(None, None)`。select が解決した URL（`resolution["article_url"]`）が
    無い・台帳を読み直したら違う URL か未解決になった → 出さない（`announces_unresolved`）。
    """
    from . import announce as announce_mod
    fm = chosen.front_matter
    if announce_mod.announces_of(fm) is None:
        return None, None
    url = (resolution or {}).get("article_url")
    again = announce_mod.resolve(fm, account_cfg)
    if (not url or again is None or again.reason is not None or again.url != url
            or again.delivery != (resolution or {}).get("announces")):
        return None, announce_mod.PREFIX
    return {"announces": again.delivery, "article_url": url, "article": again.article}, None


def _throw_chosen(account_name, account_cfg, state_dir, run_id, mode, chosen, section,
                   now, log, adapter_factory, resolution=None, via="timer") -> ThrowResult:
    """select_one() が選んだ 1 件を投げる（dry-run ならログに出すだけ）。

    `_throw_locked()` の max_per_run ループから 1 本ごとに呼ばれる（§3.3）。
    `resolution` は select が `reply_to_file` を解決した結果（`{"file", "post_id"}`）。
    """
    started = jst.iso()
    # 返信先を先に決める（inflight を書く前・何も送らないうちに断る）。
    reply_to, reply_refusal = _reply_to_for_post(chosen, resolution, account_name)
    if reply_refusal is not None:
        msg = (f"返信先を解決できないので出しません（root には落としません）: "
               f"{os.path.basename(chosen.path)}（{reply_refusal}）")
        log(msg)
        _append_run(state_dir, account_name, run_id, mode, "post", chosen.path, None, now,
                    status="error", error=reply_refusal)
        return ThrowResult(exit_code=2, mode=mode, action="post", message=msg,
                           file=chosen.path, error=reply_refusal)
    resolved_from = ({"file": resolution["file"], "post_id": reply_to}
                     if queuefile.reply_to_file_of(chosen.front_matter) is not None else None)
    # 告知（4.0 S4 §9.4）: 結ぶ記事の URL を、select が解決した値ともう一度読んだ台帳で確かめる。
    announced, announce_refusal = _announce_for_post(chosen, resolution, account_cfg)
    if announce_refusal is not None:
        msg = (f"告知の記事を解決できないので出しません: {os.path.basename(chosen.path)}（{announce_refusal}）")
        log(msg)
        _append_run(state_dir, account_name, run_id, mode, "post", chosen.path, None, now,
                    status="error", error=announce_refusal)
        return ThrowResult(exit_code=2, mode=mode, action="post", message=msg,
                           file=chosen.path, error=announce_refusal)
    # 投稿の目的（設計 3.6.0 §A）。**指紋の外**——承認の時点の目的（`approved_goal`）と
    # 違っても止めず、runs と sent に「目的の変更」として残す。front-matter だけを読む。
    goal_value = goals_mod.goal_of(chosen)
    goal_change = goals_mod.change(chosen)
    if goal_change:
        log(goals_mod.change_line(goal_change))
    from . import media as media_mod
    if media_mod.declared(chosen.front_matter) and media_delivery.unavailable(account_cfg):
        return ThrowResult(exit_code=2,mode=mode,action='media_provider_unavailable',message='media_provider_unavailable',file=chosen.path)
    try:
        manifest = media_mod.manifest_for(chosen.front_matter, account_cfg)
    except media_mod.MediaError as exc:
        return ThrowResult(exit_code=2,mode=mode,action='approval_stale',message=str(exc),file=chosen.path)
    media_error=media_delivery.error_for(account_cfg,manifest)
    if media_error:
        return ThrowResult(exit_code=2,mode=mode,action='media_provider_unavailable',message=media_error,file=chosen.path)
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
    effective_section = approval_mod.effective_section(
        section, account_cfg, chosen.get("topic"))
    # reply_to の欄は `file:<名前>`（reply_to_file の原稿・解決した post_id は入れない・
    # 設計 3.2.0 §3）か `reply_to` の値。adapter に渡すのは解決した post_id。
    fingerprint_reply_to = approval_mod.reply_to_for_fingerprint(chosen.front_matter)
    expected_fingerprint = approval_mod.compute_approved_sha(
        section=effective_section, account=account_name, reply_to=fingerprint_reply_to,
        topic=chosen.get("topic"), publish_at=chosen.get("publish_at"), media_manifest=manifest, **options)
    # 上と同じ 5 項目を、hash にする前の正規化済みの値のまま持っておく
    # （外部レビュー第 3 巡・持ち越し項目 C）。指紋が食い違ったときに
    # `_mismatch_fields()` へ渡して「どの項目が」違ったかを特定するため
    # （hash 自体からは個々の項目を復元できない）。
    expected_components = approval_mod.compute_approved_components(
        section=effective_section, account=account_name, reply_to=fingerprint_reply_to,
        topic=chosen.get("topic"), publish_at=chosen.get("publish_at"), media_manifest=manifest, **options)
    # 告知（4.0 S4 §9.4 の 2）: 指紋は置き換えの**前**の本文で固定した（上）。inflight に書く本文と
    # adapter に渡す本文は `{article_url}` を台帳の `published_url` に置き換えたもの。
    from . import announce as announce_mod
    send_section = (announce_mod.fill(effective_section, announced["article_url"])
                    if announced is not None else effective_section)
    if announced is not None and queuefile.count_for(media, send_section) > queuefile.limit_for(media, account_cfg):
        # 置き換えたあとの本文が上限を越える（監査 低-4・select も同じ理由で落とす）。何も書かずに出さない。
        refusal = announce_mod.unresolved(announce_mod.TOO_LONG_AFTER_FILL)
        msg = f"告知の本文が URL を入れると上限を越えるので出しません: {os.path.basename(chosen.path)}（{refusal}）"
        log(msg)
        _append_run(state_dir, account_name, run_id, mode, "post", chosen.path, None, now,
                    status="error", error=refusal)
        return ThrowResult(exit_code=2, mode=mode, action="post", message=msg, file=chosen.path, error=refusal)
    # 送る本文の hash（後方互換・`tests/test_sent_integrity.py` が参照）も併せて
    # inflight に書く（外部レビュー §3・受け入れ 9・10）。実際の照合は上の指紋で行う。
    body_hash = approval_mod.compute_body_hash(send_section)
    # 本文の指紋（設計 3.3.1 §3）: 結果が分からなかったとき、次の run が自分の
    # 最近の投稿と照合する。本文そのものは inflight に書かない。
    inflight_mod.write(state_dir, file=chosen.path, started=started, container_id=None,
                        body_hash=body_hash, approved_fingerprint=expected_fingerprint,
                        text_fingerprint=inflight_resolve_mod.text_fingerprint(send_section),
                        reply_to=reply_to)
    if announced is not None:
        log(f"告知: {announced['announces']} → {announced['article_url']}")

    if mode == "rehearsal":
        if resolved_from:
            log(f"返信先: {resolved_from['file']} → {resolved_from['post_id']}")
        if options.get("community_id"):
            log(f"コミュニティ: {options['community_id']}")
        log("投げるはずの本文:")
        log(send_section)
        inflight_mod.clear(state_dir)
        _append_run(state_dir, account_name, run_id, mode, "skip", chosen.path, None, now,
                    status="ok", error=None, resolved_from=resolved_from)
        return ThrowResult(exit_code=0, mode=mode, action="skip",
                            message="dry-run: 投げるはずの本文をログに出した", file=chosen.path)

    # ---- production ----
    token = accounts_mod.load_token(account_cfg)
    adapter = leave_gate.bind(adapter_factory(account_cfg, token), account_cfg)
    # topic は select_one() の条件 9b で既に検査済み（不正なら候補から落ちている）。
    # ここでは正規化だけ行う（前後の空白・先頭の `#` を落とす・設計 §4.1）。
    topic = queuefile.normalize_topic(chosen.get("topic"))
    post = adapter_base.Post(text=send_section,
                             reply_to=reply_to, topic=topic,
                             hashtags_allowed=bool(account_cfg.get("hashtags", True)),
                             location_id=options["location_id"],
                             share_to_instagram=options["share_to_instagram"],
                             community_id=options["community_id"])

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

    def final_media_veto():
        try:
            current=queuefile.parse(chosen.path)
            if current.malformed or current.get('status')!='approved' or current.get('approved_sha')!=expected_fingerprint or current.get('revoked_at'):return 'approval_stale'
        except (OSError,ValueError):return 'approval_stale'
        return None if _fingerprint_matches(chosen.path,media,expected_fingerprint,account_cfg) else 'approval_stale'
    publish_result = media_delivery.publish(adapter,post,cfg=account_cfg,
        fm=chosen.front_matter,manifest=manifest,state_dir=state_dir,
        on_container_created=on_container_created,
        before_publish=final_media_veto if manifest else None)

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
        if publish_result.failure in ("publish_ambiguous", "media_ambiguous", "media_held"):
            # 出たか分からない失敗 → inflight を残す（消すと二重投稿になりうる）。
            # 次の実行の冒頭の inflight チェックに乗る（board にもそのまま出る）。
            msg = ("添付作成後に公開を停止したので inflight を残します" if publish_result.failure=="media_held" else "公開の結果が分からないので inflight を残します") + f": {chosen.path}"
            log(msg)
            _note_remote_state(state_dir, publish_result, log)
            _append_run(state_dir, account_name, run_id, mode, "post", chosen.path, None, now,
                        status="error", error=err, api_diagnostic=detail_data,
                        extra=_remote_extra(publish_result))
            return ThrowResult(exit_code=1, mode=mode, action="inflight", message=msg,
                                file=chosen.path, error=err, api_diagnostic=detail_data)
        # コンテナ作成の失敗・公開が 4xx は「実際には出ていない」ので inflight を残す理由が無い。
        # 公開が 5xx でも container が ERROR・EXPIRED と答えたら同じ（設計 3.3.1 §2・
        # 原稿は approved のまま残り、次の run が新しい container から出す）。
        inflight_mod.clear(state_dir)
        _append_run(state_dir, account_name, run_id, mode, "post", chosen.path, None, now,
                    status="error", error=err, api_diagnostic=detail_data,
                    extra=_remote_extra(publish_result))
        # アカウント側の断り（X の x_not_permitted）なら、次の run から投げ直さない（3.19.2）。
        publish_hold_mod.note_refusal(account_name, account_cfg, err, file=chosen.path,
                                      via=via, log=log)
        return ThrowResult(exit_code=1, mode=mode, action="post", message="公開に失敗しました",
                            file=chosen.path, error=err, api_diagnostic=detail_data)

    if getattr(publish_result, "remote_state", None):
        log(_REMOTE_RECOVERED.get(publish_result.remote_state,
                                  f"公開の応答は分からなかったが媒体に訊いて決まりました: {publish_result.remote_state}"))

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
    try:
        inflight_mod.update(state_dir, post_id=post_id)

        posted_at = publish_result.ts
        # 送った本文そのものを動かせない記録として残す（外部レビュー §3・受け入れ 9・
        # 10・これが正本）。書き戻し（front-matter 書き換え）より前に書く。指紋
        # （外部レビュー再々レビュー P1・1）も併せて残す。
        sent_mod.write(state_dir, post_id=post_id, text=post.text,
                        body_hash=approval_mod.compute_body_hash(post.text),
                        sent_at=posted_at, approved_fingerprint=expected_fingerprint, reply_to=post.reply_to,
                        resolved_from=resolved_from,
                        attachment_kinds=media_mod.attachment_kinds(manifest),
                        goal=goal_value, goal_change=goal_change,
                        **_link_urls_kwargs(manifest),
                        **({"media":publish_result.media} if publish_result.media else {}),
                        **({"community_id": post.community_id} if post.community_id else {}),
                        **({"announces": announced["announces"], "article_url": announced["article_url"],
                            **({"article": announced["article"]} if announced.get("article") else {})}
                           if announced is not None else {}))
    except (OSError,ValueError):
        if not manifest:raise
        return ThrowResult(exit_code=1,mode=mode,action="inflight",message="公開後の記録を確定できません。再送せず inflight を保持します",file=chosen.path,post_id=post_id,error="media_record_unconfirmed")

    # 絡みの台帳（設計「自分の泉」§4・発注 T0-1）。**公開の確定直後・書き戻しより
    # 前**——post_id の書き戻しが失敗しても、出た事実そのものは変わらない。
    engagement_write_failed, engagement_author_lookup_failed = _record_engagement(
        account_cfg, account_name, media=media, topic=topic, form=chosen.get("form"),
        post_id=post_id, posted_at=posted_at, now=now, log=log,
        reply_to=reply_to, reply_to_root=chosen.get("reply_to_root"),
        reply_to_author_key=chosen.get("reply_to_author_key"),
        # reply_to_file は人が「この原稿に返す」と書いたもの（`manual` 相当・§3）。
        found_by=chosen.get("found_by") or ("manual" if resolved_from else None),
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
    if not _fingerprint_matches(chosen.path, media, expected_fingerprint, account_cfg):
        # どの項目が食い違ったのかを特定する（外部レビュー第 3 巡・持ち越し項目 C）。
        # ログ・runs・board（inflight 経由）の 3 箇所に出す。人が止まった原因を
        # ファイルを開いて自分で探さずに済むように。
        mismatch_fields = _mismatch_fields(chosen.path, media, expected_components, account_cfg)
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

    if resolved_from:
        # 解決した post_id も書き戻す（`reply_to_file` は残す・承認の指紋の外の項目）。
        writeback.set_front_matter_fields(chosen.path, {
            "status": "posted", "post_id": post_id, "posted_at": posted_at,
            "reply_to_resolved": resolved_from["post_id"]})
    else:
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
        ok = _fingerprint_matches(chosen.path, media, expected_fingerprint, account_cfg)
        if not ok:
            rebase_mismatch_fields[:] = _mismatch_fields(chosen.path, media, expected_components, account_cfg)
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
                engagement_author_lookup_failed=engagement_author_lookup_failed,
                resolved_from=resolved_from,
                extra={**_remote_extra(publish_result), "goal": goal_value,
                       "goal_change": goal_change})
    return ThrowResult(exit_code=0, mode=mode, action="post", message="投稿しました",
                        file=chosen.path, post_id=post_id)


# 安全装置の断りの言い出し（`guard_refusal_message` の `kind` ごと）。はてなの記事は SNS の
# 「取り下げ」「送信」でなく、記事の操作の語で言う。
GUARD_REFUSAL_HEADS = {"publish": "送信しません", "retract": "取り下げません",
                       "entry_create": "公開しません", "entry_update": "更新しません",
                       "entry_delete": "削除しません"}


def guard_refusal_message(account_name: str, refused, *, kind: str = "publish") -> str:
    """安全装置の断り（`guard.GuardRefused`）を人向けの 1 行に。数は `thth account set` で変えられる。

    `kind` は `publish`（`thth send`）か `retract`（`thth retract`・10/15）、はてなの記事の操作の
    `entry_create`・`entry_update`・`entry_delete`（実機の確かめ 10/17 の 5）。言い出しと
    「次に…できる」の語だけ変わる。
    """
    code = str(refused)
    can = "消せる" if kind in ("retract", "entry_delete") else "出せる"
    when = f"（次に{can}のは {refused.next_at}）" if getattr(refused, "next_at", None) else ""
    if code == "retract_limit":
        why = f"今日の削除が上限（daily_max_retracts）に達しました{when}"
    elif code == "too_soon":
        why = f"前の公開から最短間隔（min_interval_hours）が経っていません{when}。返信には掛かりません"
    elif code == "daily_limit" and getattr(refused, "reason", None) == "reply_daily":
        # 4.3（監査 中-1）: 返信だけの日の上限（`reply.daily`）。口座は止めない。
        why = (f"今日の返信が上限（reply.daily）に達しました{when}。口座は止めていません。"
               "数は thth account set <口座> reply.daily <数> --by <名前> で変えられます")
    elif code == "daily_limit":
        why = f"今日の公開が上限（daily_max_posts）に達しました{when}"
    elif code == "quiet_hours":
        why = f"夜間（quiet_hours）です{when}"
    elif code == "burst_limit" and getattr(refused, "reason", None) == "bookmark_burst":
        # 4.0.1: ブックマークの連投の窓（主体の bookmark_burst）。主体は止めない。
        when = f"（次に打てるのは {refused.next_at}）" if getattr(refused, "next_at", None) else ""
        why = ("ブックマークの連投の数（bookmark_burst）を超えます。主体は止めずに、この 1 件だけ断りました"
               f"{when}。数は thth principal set で変えられます")
    elif code == "burst_limit" and getattr(refused, "next_at", None):
        # 返信の burst（3.25.0）で、順番待ちに並べられなかった（timer に載っていない口座・添付つき）。
        why = ("急な連投（burst）の数を超えます。返信なので口座は止めずに、この 1 件だけ断りました"
               f"（timer に載っていない口座と添付つきの返信は順番待ちに並べられません）{when}")
    elif code == "burst_limit":
        why = "急な連投（burst）の数を超えます（この置き場には止めの印を置けないので、口座は止めずにこの 1 件だけ断りました）"
    elif code == "account_stopped" and getattr(refused, "reason", None) == "burst":
        why = ("急な連投（burst）で口座を止めました。戻すのは "
               f"thth account resume {account_name} --by <名前>")
    elif code == "account_stopped":
        why = f"口座が止まっています（{getattr(refused, 'reason', None) or '理由不明'}）。thth account resume {account_name} --by <名前>"
    elif code == "principal_stopped":
        # 主体（はてな ID・設計 4.0 S1 §4.1）の止め。その主体のすべてのブログとブックマークに効く。
        why = (f"主体（はてな ID）が止まっています（{getattr(refused, 'reason', None) or '理由不明'}）。"
               "戻すのは thth principal resume <主体> --by <名前>")
    elif code in ("principal_daily_limit", "principal_retract_limit", "blog_daily_limit",
                  "blog_retract_limit", "bookmark_limit"):
        why = f"主体かブログの 1 日の上限（{code}）に達しました{when}。数は thth principal set で変えられます"
    elif code in ("self_bookmark_limit", "bookmark_host_limit"):
        # 4.0 S4 §5.3: 自分の記事は 1 日 1 件・同じホストへは 1 日 1 件（主体ごと）。
        what = "自分の記事のブックマーク" if code == "self_bookmark_limit" else "同じホストへのブックマーク"
        why = f"{what}の 1 日の上限（{code}）に達しました{when}。数は thth principal set で変えられます"
    elif code == "bookmark_once":
        why = ("このページは同じ owner のどれかの主体が既にブックマークしています（1 ページに 1 回だけ・"
               "削除したあとも付け直しません）")
    elif code == "bookmark_exists_remote":
        why = "この主体ではてなに既にブックマークがあります（手で付けたものを THTH は上書きしません）"
    elif code == "bookmark_busy":
        why = "同じ owner の別のブックマークが打っている最中です。少し置いてもう一度"
    elif code == "bookmark_not_allowed_for_lane":
        # 実機の確かめ 10/17 の 7: 直す先は口座でなく主体の台帳（bookmark.lanes）。
        try:
            cfg = accounts_mod.load_account(account_name)
            principal, project = cfg.get("principal") or "<主体>", cfg.get("project") or "<lane>"
        except Exception:  # noqa: BLE001 — 文のためだけ
            principal, project = "<主体>", "<lane>"
        why = (f"このレーン（{project}）は主体のブックマークを許すレーン（主体の台帳の bookmark.lanes）にありません。"
               f"許すなら thth principal set {principal} bookmark.lanes <lane>[,<lane>…] --by <名前>")
    elif code == "invalid_media_traits":
        return (f"{GUARD_REFUSAL_HEADS.get(kind, '送信しません')}（安全装置）: 媒体の特性の台帳が検査を通らず、"
                "柵の表を作れません（invalid_media_traits）。thth media check で理由を確かめてください")
    else:
        why = code
    head = GUARD_REFUSAL_HEADS.get(kind, "送信しません")
    from . import guard as guard_mod
    if code in guard_mod.PRINCIPAL_REFUSALS:
        # 主体・ブログ・ブックマークの段は主体の台帳が直す先（実機の確かめ 10/17 の 8・口座の set を重ねない）。
        return f"{head}（安全装置）: {why}"
    return (f"{head}（安全装置）: {why}。数は thth account set {account_name} <名前> <値> --by <名前>"
            " で変えられます")


def refusal_parts(account_name: str, refused, *, kind: str = "publish") -> dict:
    """安全装置の断りの文の型（設計「上限と連投の柵」§7.2・段 4）。

    `{"line", "basis", "how_to_pass", "assessment"}`。`line` は今までの 1 行（`guard_refusal_message`）そのまま
    （符丁と言い出しを変えない）。`basis` は根拠の 1 行（台帳の項目の文・種類・確かさ。推測は「THTH の推測」）、
    `how_to_pass` は通すにはの 1 行（`assessment.how_to_pass`）。行の無い断りは `basis`・`how_to_pass` が None。
    """
    found = getattr(refused, "assessment", None)
    out = {"line": guard_refusal_message(account_name, refused, kind=kind), "basis": None, "how_to_pass": None,
           "assessment": public_assessment(found)}
    if not isinstance(found, dict):
        return out
    if (found.get("trait") or {}).get("id"):
        out["basis"] = _basis_line(found)
    out["how_to_pass"] = found.get("how_to_pass")
    return out


def _trait_media(trait_id) -> str:
    return str(trait_id).split(".", 1)[0]


def _basis_line(found) -> str | None:
    """根拠の 1 行（§7.2 の `根拠:`）。台帳の項目の文を引く（引けなければ id と確かさだけ）。"""
    trait = found.get("trait") or {}
    tid = trait.get("id")
    if not tid:
        return None
    rule = found.get("rule")
    window = found.get("window")
    used, count = found.get("used"), found.get("count")
    head = f"行 {rule}"
    if isinstance(used, int) and isinstance(count, int):
        span = f"{window['minutes']} 分" if isinstance(window, dict) and window.get("minutes") else (
            "今日" if window == "day" else "")
        head += f"（{SCOPE_LABEL.get(found.get('scope'), found.get('scope'))}・{span}{'・' if span else ''}"
        head += f"{used}/{count}）"
    if str(tid).startswith("thth:"):
        return f"{head}・根拠: THTH の線（{tid}）"
    from . import advise
    statement, kind, conf, etype = None, None, trait.get("confidence"), None
    try:
        from . import media_traits
        doc = media_traits.load(_trait_media(tid))
        item = next((t for t in doc["traits"] if t["id"] == tid), None)
        if item is not None:
            statement, kind = item.get("statement"), item.get("kind")
            conf = conf or item.get("confidence")
            etype = (item.get("evidence") or {}).get("type")
    except Exception:  # noqa: BLE001 — 文のためだけ
        pass
    basis = advise.basis_text({"id": tid, "statement": statement, "kind": kind, "confidence": conf,
                               "evidence_type": etype})
    if trait.get("count_evidence") == "inferred":
        basis += "・数は THTH の推測（確かさ 低）"
    elif trait.get("count_evidence") == "thth_line":
        basis += "・数は THTH の線"
    return f"{head}・根拠: {basis}"


SCOPE_LABEL = {"account": "口座", "principal": "主体", "owner": "owner", "blog": "ブログ"}
# 遠くの道・MCP の本文に出してよい `assessment` の欄（§7.4・静的な語と時刻と命令の文だけ）。
PUBLIC_ASSESSMENT_KEYS = ("outcome", "rule", "scope", "used", "count", "next_at", "how_to_pass")


def public_assessment(found) -> dict | None:
    if not isinstance(found, dict):
        return None
    out = {key: found.get(key) for key in PUBLIC_ASSESSMENT_KEYS}
    trait = found.get("trait") or {}
    out["trait"] = {"id": trait.get("id"), "confidence": trait.get("confidence"),
                    "count_evidence": trait.get("count_evidence")} if trait else None
    if found.get("stopped_by_rule"):
        out["stopped_by_rule"] = found["stopped_by_rule"]
    return out


def refusal_lines(account_name: str, refused, *, kind: str = "publish") -> list:
    """§7.2 の型の 3 行（1 行目は今の文・根拠・通すには）。行の無い断りは 1 行。"""
    parts = refusal_parts(account_name, refused, kind=kind)
    out = [parts["line"]]
    if parts["basis"]:
        out.append(f"  {parts['basis']}")
    if parts["how_to_pass"]:
        out.append(f"  通すには: {parts['how_to_pass']}")
    return out


def _queue_burst_reply(account_name, account_cfg, state_dir, run_id, mode, *, now, log, digest, refused,
                       text, topic, reply_to, goal, reply_to_root, reply_to_author_key, found_by) -> ThrowResult:
    """`thth send --reply-to` が burst に当たった: 口座を止めず、窓が空く時刻の順番待ちに並べる（3.25.0）。"""
    from . import burst_queue as burst_queue_mod, guard as guard_mod
    publish_at = jst.parse(refused.next_at)
    try:
        placed = burst_queue_mod.queue_cli(
            account_name, account_cfg, text=text, topic=topic, reply_to=reply_to, publish_at=publish_at,
            goal=goal, reply_to_root=reply_to_root, reply_to_author_key=reply_to_author_key,
            found_by=found_by, now=now)
    except guard_mod.GuardRefused as daily:
        msg = guard_refusal_message(account_name, daily)
        log(msg)
        _append_run(state_dir, account_name, run_id, mode, "skip", None, None, now,
                    status="error", error=str(daily))
        return ThrowResult(exit_code=1, mode=mode, action="guard", message=msg, digest=digest, error=str(daily))
    except (OSError, ValueError) as exc:
        msg = guard_refusal_message(account_name, refused) + f"（順番待ちに並べられませんでした: {exc}）"
        log(msg)
        _append_run(state_dir, account_name, run_id, mode, "skip", None, None, now,
                    status="error", error="burst_limit")
        return ThrowResult(exit_code=1, mode=mode, action="guard", message=msg, digest=digest,
                           error="burst_limit")
    msg = burst_queue_mod.message(account_name, publish_at)
    log(msg)
    log(f"  原稿: {placed['file']}")
    _append_run(state_dir, account_name, run_id, mode, "queued", placed["file"], None, now,
                status="ok" if placed["pushed"] else "error",
                error=None if placed["pushed"] else "queue_commit_unconfirmed",
                extra={"publish_at": placed["publish_at"], "reason": "burst"})
    if not placed["pushed"]:
        msg2 = ("順番待ちの原稿を commit・push できませんでした。このままでは出ません"
                f"（board に unverified_content として出ます）: {placed['push_error']}")
        log(msg2)
        return ThrowResult(exit_code=1, mode=mode, action="queued", message=msg2, file=placed["file"],
                           digest=digest, error="queue_commit_unconfirmed", publish_at=placed["publish_at"])
    return ThrowResult(exit_code=0, mode=mode, action="queued", message=msg, file=placed["file"],
                       digest=digest, publish_at=placed["publish_at"])


def send_once(account_name: str, *, text: str, topic: str | None = None,
               reply_to: str | None = None, reply_to_root: str | None = None,
               reply_to_author_key: str | None = None, found_by: str | None = None,
               production_flag: bool = False,
               confirm: str | None = None, adapter_factory=None, log=None,
               now=None, wait=0, before_execute=None, lock_context=None, media_rows=None,
               goal: str | None = None, origin: dict | None = None,
               community_id: str | None = None) -> ThrowResult:
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

    **`goal`**（設計 3.6.0 §A1）: 投稿の目的（`reach`・`click`・`follow`・`reply`・
    省略は `none`）。`sent/` と runs に残る。**digest には入れない**——queue の
    承認の指紋に入れないのと同じ（目的は測り方の札で、公開される中身ではない）。
    """
    log = log or (lambda line: None)
    adapter_factory = adapter_factory or _default_adapter_factory
    run_id = uuid.uuid4().hex[:12]

    account_cfg = accounts_mod.load_account(account_name)
    if account_cfg.get("media") == "hatenablog":
        # はてなブログは配信原稿を承認して timer で出す道だけ（設計 4.0 S1 §5・S2 §9 の 7）。
        # 同席の送信・返信は持たない（主体の 3 段の数え・配信の台帳を通らない書き込みを作らない）。
        msg = ("unsupported_operation: はてなブログの口座には thth send（返信も）で出せません。"
               "配信原稿を thth approve で承認し、timer が出します")
        log(msg)
        return ThrowResult(exit_code=2, mode="rehearsal", action="unsupported_operation", message=msg,
                           error="unsupported_operation")
    reply_to = postid_mod.for_account(account_cfg, reply_to, account_name)
    state_dir = accounts_mod.state_dir_for(account_name)
    now = now if now is not None else jst.now_jst()

    try:
        return _send_locked(
            account_name, account_cfg, state_dir, run_id, text=text, topic=topic,
            reply_to=reply_to, reply_to_root=reply_to_root,
            reply_to_author_key=reply_to_author_key, found_by=found_by,
            production_flag=production_flag, confirm=confirm,
            adapter_factory=adapter_factory, log=log, now=now, wait=wait,
            before_execute=before_execute, lock_context=lock_context, media_rows=media_rows,
            goal=goal, origin=origin, community_id=community_id,
        )
    except lock_mod.LockBusy:
        msg = f"{account_name} は既に実行中です（ロック取得失敗）。--wait <秒> で空くのを待てます"
        log(msg)
        return ThrowResult(exit_code=1, mode="rehearsal", action="locked", message=msg)


def _send_locked(account_name, account_cfg, state_dir, run_id, *, text, topic, reply_to,
                  reply_to_root=None, reply_to_author_key=None, found_by=None,
                  production_flag, confirm, adapter_factory, log, now, wait=0, before_execute=None, lock_context=None, media_rows=None,
                  goal=None, origin=None, community_id=None) -> ThrowResult:
    locks = ((lock_context or _account_locks(account_name, account_cfg, state_dir, wait=wait))
             if production_flag else contextlib.nullcontext())
    with locks:
        # **dry-run でも断る**（実機 2026-09-22）。inflight が残ったまま
        # `thth send`（`--production` なし）を打つと、digest まで出して 0 で
        # 終わっていた——次に `--production` を付けたら必ず断られるのに、
        # 残っていることを一言も言わない。不在の様態（`_throw_locked`）は
        # 最初から様態に関わらず断っている。同じにする。
        existing_inflight = inflight_mod.read(state_dir)
        if existing_inflight is not None:
            msg = f"inflight が残っています: {existing_inflight.get('file')}"
            log(msg)
            return ThrowResult(exit_code=1, mode="rehearsal", action="inflight", message=msg,
                                file=existing_inflight.get("file"))
        # 媒体がアカウント側の断りを返して止めた口座（3.19.2）。手の `thth send` も断る
        # ——同じ制限に同じ書き込みを重ねない。**dry-run でも断る**（上の inflight と同じ
        # 理由: 次に `--production` を付けたら必ず断られる）。1 本だけ試す口は持たない。
        # 人が X の画面で直したら `thth account resume` で戻し、まだ断られるなら 1 回で
        # また止まる——それが「1 本だけ試す」に当たる。
        hold_row = publish_hold_mod.held(account_name)
        if hold_row is not None:
            msg = publish_hold_mod.message(account_name, hold_row)
            log(msg)
            return ThrowResult(exit_code=2, mode="rehearsal", action="publish_held", message=msg,
                               error=hold_row.get("reason") or publish_hold_mod.UNREADABLE)

        production = bool(account_cfg.get("production")) and production_flag
        mode = "production" if production else "rehearsal"
        log(f"mode: {mode}")

        # send の指紋もフィールド区切りに制御文字を使う。queue と同じく、
        # 正規化や digest 表示より前に拒否する（既存のハッシュ定義は変えない）。
        for field, value in (("text", text), ("reply_to", reply_to), ("topic", topic),
                             ("community_id", community_id)):
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

        from . import media as media_mod
        try:
            manifest = media_mod.manifest_for({'media': media_rows or []}, account_cfg)
        except media_mod.MediaError as exc:
            return ThrowResult(exit_code=2, mode=mode, action='invalid_attachment', message=str(exc), error=str(exc))
        body = (text or "").strip()
        if not body and not media_rows:
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
            figure = queuefile.length_figure(media, n, limit)
            msg = ((f"長すぎます: {figure}。切り詰めません。\n" if figure
                    else f"長すぎます（{n} 字・上限 {limit} 字）。切り詰めません。\n")
                   + "分けるかどうかは `thth forms`")
            log(msg)
            _append_run(state_dir, account_name, run_id, mode, "skip", None, None, now,
                        status="error", error=f"too_long({n})")
            return ThrowResult(exit_code=1, mode=mode, action="skip", message=msg)

        # X のコミュニティ（3.19.0・`--community <id>`）。queue の `community_id:` と同じ門
        # （X だけ・数字の形・返信と同時に使わない）。**dry-run でも断る**（digest を出さない）。
        community_value = (str(community_id).strip() or None) if community_id is not None else None
        if community_value is not None:
            from . import lint as lint_mod
            problems = lint_mod.community_id_errors(
                {"community_id": community_value, "reply_to": reply_to}, account_cfg)
            if problems:
                msg = problems[0]
                log(msg)
                _append_run(state_dir, account_name, run_id, mode, "skip", None, None, now,
                            status="error", error="community_id_invalid")
                return ThrowResult(exit_code=2, mode=mode, action="skip", message=msg,
                                   error="community_id_invalid")

        topic_value = queuefile.normalize_topic(topic)
        if topic_value is not None:
            topic_err = (queuefile.topic_error(topic_value) if media == "threads"
                         else tags_mod.topic_error(media, topic_value))
            if topic_err is not None:
                log(f"トピックが不正です: {topic_err}")
                _append_run(state_dir, account_name, run_id, mode, "skip", None, None, now,
                            status="error", error=topic_err)
                return ThrowResult(exit_code=1, mode=mode, action="skip", message=topic_err)

        # 投稿の目的（設計 3.6.0 §A1）。4 語か省略（`none`）。digest には入れない。
        goal_value = goals_mod.normalize(goal)
        if goal_value is None:
            msg = f"{goals_mod.GOAL_INVALID}: {goals_mod.GOAL_INVALID_MESSAGE}"
            log(msg)
            _append_run(state_dir, account_name, run_id, mode, "skip", None, None, now,
                        status="error", error=goals_mod.GOAL_INVALID)
            return ThrowResult(exit_code=1, mode=mode, action="skip", message=msg,
                               error=goals_mod.GOAL_INVALID)

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
            text=effective, account=account_name, reply_to=reply_to, topic=topic_value, media_manifest=manifest,
            community_id=community_value)

        if mode == "rehearsal":
            # **dry-run は publish が断るものを断る**（実機 2026-09-22: 8 MB を
            # 超える公開 JPEG に digest が出て、本番の preflight で初めて落ちた）。
            # 媒体の上限を見る場所は lint と同じ 1 か所（`lint_notes`）にする
            # ——2 か所に書けば必ずずれる。instance に届かないときだけ断らず、
            # 既存の capability_unobserved を警告として言う（上限は upload の
            # 直前にもう一度観測するので、ここで断る根拠にならない）。
            media_notes = (media_delivery.lint_notes(account_cfg, {'media': media_rows or []},
                                                     text=effective, unreachable_warns=True)
                           if manifest else [])
            refusals = [note for note in media_notes if not note.startswith('warning: ')]
            if refusals:
                for note in media_notes:
                    log(note)
                return ThrowResult(exit_code=2, mode=mode, action='invalid_attachment',
                                   message=refusals[0], error=refusals[0])
            log("投げるはずの本文:")
            log(effective)
            if topic_value:
                log(f"トピック: {topic_value}")
            if community_value:
                log(f"コミュニティ: {community_value}（参加していないコミュニティには X が断ります）")
            if goal_value != goals_mod.NONE:
                log(f"目的: {goal_value}（{goals_mod.LABELS[goal_value]}・digest には入りません）")
            log(queuefile.length_line(media, effective, account_cfg))
            if manifest: log(media_mod.display(manifest))
            for note in media_notes:
                log(note)
            log(f"digest: {digest}")
            _append_run(state_dir, account_name, run_id, mode, "skip", None, None, now,
                        status="ok", error=None)
            return ThrowResult(exit_code=0, mode=mode, action="skip",
                                message="dry-run: 投げるはずの本文をログに出した", digest=digest)

        media_error=media_delivery.error_for(account_cfg,manifest)
        if media_error or (manifest and confirm is not None and confirm!=digest):
            error = 'approval_stale' if confirm is not None and confirm != digest else media_error
            log(error)
            return ThrowResult(exit_code=2, mode=mode, action=error, message=error, error=error, digest=digest)

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

        # **運営者の CLI（VM で打つ `thth send`）にも安全装置を掛ける**（10/15 masaru「推しで」）。
        # それまでは thth.me を通る道（`server_writes` の `before_execute`）にだけ掛かり、
        # music-research-threads が 5 分で 7 件出た。thth.me の道は `before_execute` の中で
        # 見るので二重にしない。timer の予約（`throw_once`）は対象外のまま。
        if before_execute is None:
            from . import burst_queue as burst_queue_mod, guard as guard_mod
            try:
                guard_mod.check(account_name, account_cfg, "publish", now=now,
                                via="cli", reply=bool(reply_to), cli=True,
                                pending_replies=burst_queue_mod.pending(account_name, account_cfg, now=now)
                                if reply_to else None)
            except guard_mod.GuardRefused as refused:
                # **返信の burst は口座を止めず、窓が空く時刻の順番待ちに並べる**（3.25.0・kopicha 10/16）。
                # timer に載っていない口座・添付のある返信は並べない（止めずに burst_limit で断る）。
                if (reply_to and str(refused) == "burst_limit" and refused.next_at
                        and burst_queue_mod.can_queue(account_cfg) and not manifest):
                    return _queue_burst_reply(
                        account_name, account_cfg, state_dir, run_id, mode, now=now, log=log, digest=digest,
                        refused=refused, text=body, topic=topic_value, reply_to=reply_to, goal=goal_value,
                        reply_to_root=reply_to_root, reply_to_author_key=reply_to_author_key, found_by=found_by)
                msg = guard_refusal_message(account_name, refused)
                log(msg)
                _append_run(state_dir, account_name, run_id, mode, "skip", None, None, now,
                            status="error", error=str(refused))
                return ThrowResult(exit_code=1, mode=mode, action="guard", message=msg,
                                   digest=digest, error=str(refused))

        bound_token = before_execute(account_cfg) if before_execute is not None else None
        inflight_mod.write(state_dir, file="(send)", started=jst.iso(), container_id=None,
                           text_fingerprint=inflight_resolve_mod.text_fingerprint(effective),
                           reply_to=reply_to or None)
        token = bound_token if before_execute is not None else accounts_mod.load_token(account_cfg)
        adapter = leave_gate.bind(adapter_factory(account_cfg, token), account_cfg)
        post = adapter_base.Post(text=effective, reply_to=reply_to or None, topic=topic_value,
                                 hashtags_allowed=bool(account_cfg.get("hashtags", True)),
                                 community_id=community_value)

        def on_container_created(container_id):
            inflight_mod.update(state_dir, container_id=container_id)

        result = media_delivery.publish(adapter,post,cfg=account_cfg,
            fm={'media':media_rows or []},manifest=manifest,state_dir=state_dir,
            on_container_created=on_container_created,
            before_publish=None)

        if result.error or not result.post_id:
            detail_data = api_diagnostic_mod.clean(result.api_diagnostic)
            err = redact_mod.redact(result.error or "不明なエラー")
            log(f"公開失敗: {err}")
            if result.failure in ("publish_ambiguous", "media_ambiguous", "media_held"):
                # 出たか分からない → inflight を残して人を呼ぶ（§3.5）
                msg = "添付作成後に公開を停止したので inflight を残します" if result.failure=="media_held" else "公開の結果が分からないので inflight を残します"
                log(msg)
                _note_remote_state(state_dir, result, log)
                _append_run(state_dir, account_name, run_id, mode, "post", None, None, now,
                            status="error", error=err, api_diagnostic=detail_data,
                            extra=_remote_extra(result))
                return ThrowResult(exit_code=1, mode=mode, action="inflight", message=msg, error=err, api_diagnostic=detail_data)
            inflight_mod.clear(state_dir)
            _append_run(state_dir, account_name, run_id, mode, "post", None, None, now,
                        status="error", error=err, api_diagnostic=detail_data,
                        extra=_remote_extra(result))
            publish_hold_mod.note_refusal(account_name, account_cfg, err, via="send", log=log)
            return ThrowResult(exit_code=1, mode=mode, action="post",
                                message="公開に失敗しました", error=err, api_diagnostic=detail_data)
        if getattr(result, "remote_state", None):
            log(_REMOTE_RECOVERED.get(result.remote_state,
                                      f"公開の応答は分からなかったが媒体に訊いて決まりました: {result.remote_state}"))

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
        try:
            sent_mod.write(state_dir, post_id=result.post_id, text=post.text,
                            body_hash=approval_mod.compute_body_hash(post.text),
                            sent_at=result.ts or jst.iso(),
                            # 承認の在り処が「masaru がその場で見た本文」なので、
                            # queue の 5 項目の指紋ではなく `--confirm` の digest を残す。
                            approved_fingerprint=digest, reply_to=post.reply_to,
                            attachment_kinds=media_mod.attachment_kinds(manifest),
                            goal=goal_value,
                            **_link_urls_kwargs(manifest),
                            **({"media":result.media} if result.media else {}),
                            **({"community_id": community_value} if community_value else {}),
                            **({"origin": origin} if origin is not None else {}))
        except (OSError,ValueError):
            if not manifest:raise
            return ThrowResult(exit_code=1,mode=mode,action="inflight",message="公開後の記録を確定できません。再送せず inflight を保持します",post_id=result.post_id,error="media_record_unconfirmed")

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
                    engagement_author_lookup_failed=engagement_author_lookup_failed,
                    extra={**_remote_extra(result), "goal": goal_value,
                           # 直接の公開（設計 3.12.0 §3.2）: 誰の依頼か（via と資格の id）。
                           **({"via": origin.get("via"), "credential": origin.get("credential")}
                              if origin is not None else {})})
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
