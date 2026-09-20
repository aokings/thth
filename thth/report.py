"""`thth queue` / `thth board`（発注 §3・受け入れ 4）。CLI と MCP の両方から呼ばれる。"""
from __future__ import annotations

import datetime
import os

from . import accounts as accounts_mod
from . import collect as collect_mod
from . import core
from . import inflight as inflight_mod
from . import healthcheck as healthcheck_mod
from . import incident as incident_mod
from . import lock as lock_mod
from . import maintain as maintain_mod
from . import selfupdate as selfupdate_mod
from . import sent as sent_mod
from . import writeback as writeback_mod
from . import jst
from . import queuefile
from . import select as select_mod

STATUS_KEYS = ("draft", "approved", "posted", "withdrawn")


def _next_via_select_one(files, *, account_name: str, account_cfg: dict, now):
    """「次に出るのは何か・いつか」を `select.select_one()` に寄せて計算する
    （食い違い 7 の裁定・2026-09-09。以前は別ロジックで、同じ答えを 2 か所で
    計算するとずれるため一本化した）。**いま出せるか**（静かな時間帯・最短間隔・
    文字数・重複等）まで込みで判定する。選ばれなかった候補の理由
    （`quiet_hours`・`min_interval`・`future` 等）も併記して返す（他アカウントの
    ファイル由来の `account_mismatch` はノイズなので除く）。
    """
    last_at = core.last_post_at(files, account_name)
    recent_texts = select_mod.recent_posted_texts(
        files, account_name=account_name, media=account_cfg["media"], now=now)
    result = select_mod.select_one(
        files, account_name=account_name, account_cfg=account_cfg,
        now=now, last_post_at=last_at, recent_texts=recent_texts)

    next_file = None
    next_at = None
    next_topic = None
    if result.chosen is not None:
        next_file = os.path.basename(result.chosen.path)
        publish_at_raw = result.chosen.front_matter.get("publish_at")
        try:
            next_at = queuefile.parse_publish_at(publish_at_raw).isoformat()
        except (TypeError, ValueError):
            next_at = None
        next_topic = queuefile.normalize_topic(result.chosen.front_matter.get("topic"))

    # **いま出せるものが無くても「次に出るもの」を答える**（kopicha セッション指摘
    # 2026-09-10: 承認済み 28 本があっても `next_file` が常に null だった）。
    # `select_one()` は「**いま**出すもの」を選ぶので、予定時刻がまだ来ていない
    # ものは `future` で落ちる。だが人が「次に出るもの」と言うときに知りたいのは
    # **待機列の先頭**のほう。`future` で落ちたものは妥当性検査を通っている
    # （＝あとは時刻が来るだけ）ので、その中で publish_at が最も早いものを返す。
    if next_file is None:
        waiting = []
        future_paths = {rej.file for rej in result.rejections if rej.reason == "future"}
        for qf in files:
            if qf.path not in future_paths:
                continue
            try:
                at = queuefile.parse_publish_at(qf.front_matter.get("publish_at"))
            except (TypeError, ValueError):
                continue
            waiting.append((at, qf))
        if waiting:
            waiting.sort(key=lambda t: (t[0], os.path.basename(t[1].path)))
            at, qf = waiting[0]
            next_file = os.path.basename(qf.path)
            next_at = at.isoformat()
            next_topic = queuefile.normalize_topic(qf.front_matter.get("topic"))

    # `future`（＝時刻を待っているだけ）は理由として並べない。28 本の `future` に
    # 埋もれて `approval_stale` や `overdue` が見えなくなる（同指摘）。
    rejections = [
        {"file": os.path.basename(rej.file), "reason": rej.reason}
        for rej in result.rejections
        if rej.reason not in ("account_mismatch", "future")
    ]
    return next_file, next_at, next_topic, rejections


def queue_summary(account_name: str | None, now=None) -> dict:
    """draft/approved/posted/型外 を数え、次に出るものと時刻を返す（アカウント別）。

    `now` はテスト用の時刻注入（省略時は `jst.now_jst()`）。
    """
    names = [account_name] if account_name else accounts_mod.list_account_names()
    out: dict = {}
    for name in names:
        try:
            account_cfg = accounts_mod.load_account(name)
        except accounts_mod.AccountError as e:
            out[name] = {"error": str(e)}
            continue
        # 同期を伴わない読み手の照合先（外部レビュー第 5 巡 P2）。
        has_queue_repo = accounts_mod.repo_state(account_cfg) != accounts_mod.REPO_NONE
        files = (core.list_queue_files(
            account_cfg, tree_sha=writeback_mod.upstream_sha(account_cfg.get("repo_dir")))
            if has_queue_repo else [])
        counts = {k: 0 for k in STATUS_KEYS}
        type_mismatch = 0
        for qf in files:
            if qf.front_matter.get("account") != name and not qf.malformed:
                continue
            if qf.malformed:
                type_mismatch += 1
                continue
            status = qf.front_matter.get("status")
            if status in counts:
                counts[status] += 1
        now_val = now if now is not None else jst.now_jst()
        next_file, next_at, next_topic, next_rejections = _next_via_select_one(
            files, account_name=name, account_cfg=account_cfg, now=now_val)
        out[name] = {
            # list_queue_files と同じ場所を示す。空の queue でも新しい原稿の
            # 置き場を発見できるよう、次のファイルの有無に依存させない。
            "repo_dir": os.path.abspath(account_cfg["repo_dir"]) if has_queue_repo else None,
            "queue_dir": (os.path.abspath(os.path.join(
                account_cfg["repo_dir"], account_cfg["queue_dir"])) if has_queue_repo else None),
            "counts": counts,
            "type_mismatch": type_mismatch,
            "next_file": next_file,
            "next_publish_at": next_at,
            "next_topic": next_topic,
            "next_rejections": next_rejections,
        }
    return out


# 指定した時刻をこれだけ過ぎても出ていなければ board に出す（masaru 受け入れ
# 条件 2026-09-10）。timer の刻みは 10 分なので、1 時間は「明らかにおかしい」。
OVERDUE_HOURS = 1.0


def _needs_review_detail(files, *, account_name: str, account_cfg: dict, now) -> list:
    """`select.select_one()` が拾った要確認（`approval_stale`・`stale`・`approved`
    なのに型外）を、board 向けにファイル名と理由の対で返す（外部レビュー再レビュー C）。

    board はいままで `approved_waiting: 1` としか出さず、「承認して待っている」の
    正常系と「承認が古くて（approval_stale）永久に出ない」の異常系を区別できなかった
    （黙って失敗する形）。`select_one()` は元々 `needs_review`（パスの一覧）と
    `rejections`（パスと理由の一覧）の両方を返しているので、ここで突き合わせるだけで
    board にも同じ情報を出せる。
    """
    last_at = core.last_post_at(files, account_name)
    recent_texts = select_mod.recent_posted_texts(
        files, account_name=account_name, media=account_cfg["media"], now=now)
    result = select_mod.select_one(
        files, account_name=account_name, account_cfg=account_cfg,
        now=now, last_post_at=last_at, recent_texts=recent_texts)
    reason_by_path = {rej.file: rej.reason for rej in result.rejections}
    out = [
        {"file": os.path.basename(path), "reason": reason_by_path.get(path, "needs_review")}
        for path in result.needs_review
    ]
    seen = set(result.needs_review)

    # **指定した時刻を過ぎたのに出ていないもの**（masaru 受け入れ条件 2026-09-10:
    # 「複数本数の投稿の日時指定が出来て…ればそれでOK」）。
    #
    # select の「いま出せるか」の関門（`quiet_hours`・`min_interval`・`max_per_run`・
    # timer が止まっている）は、**時間が経てば解決する**という理由で要確認に積んで
    # いない。それは正しいが、**時間が経っても解決しなかった場合に誰も気づかない**。
    # 指定した時刻は masaru の明示の指示なので、1 時間を過ぎても出ていなければ
    # 理由を添えて board に出す（理由が判らなければ `overdue`＝timer が止まって
    # いる・実行そのものが無い、など）。
    for qf in files:
        if qf.path in seen or qf.malformed:
            continue
        fm = qf.front_matter
        if (fm.get("account") != account_name or fm.get("status") != "approved"
                or fm.get("post_id")):
            continue
        raw = fm.get("publish_at")
        if not raw:
            continue
        try:
            publish_at = queuefile.parse_publish_at(raw)
        except ValueError:
            continue
        if now - publish_at <= datetime.timedelta(hours=OVERDUE_HOURS):
            continue
        # **select が選ぶはずのものでも外さない。** board は投稿しないので、
        # 「次の実行で出るはず」は保証ではない。1 時間以上遅れているなら、その
        # 「はず」が 6 回以上外れているということ（timer の刻みは 10 分）。
        if reason_by_path.get(qf.path):
            reason = reason_by_path[qf.path]
        elif not account_cfg.get("production"):
            # いちばん多い理由をそのまま名指しする（台帳が本番になっていない）。
            reason = "rehearsal"
        else:
            reason = "overdue"
        out.append({"file": os.path.basename(qf.path), "reason": reason})
        seen.add(qf.path)
    return out


def schedule(account_name: str | None = None, *, now=None, days: int | None = None) -> list:
    """日付順に「いつ・何が出るか」を並べる（asmon 関東セッション指摘 2026-09-10）。

    > queue は future が 47 行並ぶだけで、日付順に時刻・トピック・書き出しを通しで
    > 見る手段がありません。

    読むだけ。`days` を渡すとその日数ぶんに絞る。`status` が `approved` のものと
    `draft` のものを両方出す——**連載を組むときに見たいのは「承認済みだけ」では
    なく「これから出る予定の全体」**だから。承認されていないものは `status` 欄で
    区別できる。
    """
    now = now if now is not None else jst.now_jst()
    names = [account_name] if account_name else accounts_mod.list_account_names()
    rows = []
    for name in names:
        try:
            account_cfg = accounts_mod.load_account(name)
        except accounts_mod.AccountError:
            continue
        files = core.list_queue_files(
            account_cfg, tree_sha=writeback_mod.upstream_sha(account_cfg.get("repo_dir")))
        media = account_cfg["media"]
        for qf in files:
            fm = qf.front_matter
            if qf.malformed or fm.get("account") != name or fm.get("post_id"):
                continue
            if fm.get("status") not in ("draft", "approved"):
                continue
            raw = fm.get("publish_at")
            if not raw:
                continue
            try:
                publish_at = queuefile.parse_publish_at(raw)
            except ValueError:
                continue
            if days is not None and publish_at > now + datetime.timedelta(days=days):
                continue
            section = queuefile.extract_section(qf.body, media) or ""
            head = section.strip().split("\n", 1)[0]
            rows.append({
                "account": name,
                "file": os.path.basename(qf.path),
                "publish_at": publish_at.isoformat(),
                "status": fm.get("status"),
                "topic": queuefile.normalize_topic(fm.get("topic")),
                "reply_to": fm.get("reply_to") or None,
                "past": publish_at <= now,
                "head": head[:60],
            })
    rows.sort(key=lambda r: (r["publish_at"], r["account"], r["file"]))
    return rows


def _thread_rows(account_name: str) -> list:
    """その account の**終わっていないスレッド連投**（読むだけ）。"""
    from . import threadrun as threadrun_mod
    out = []
    for row in threadrun_mod.open_runs(account_name):
        summary = threadrun_mod.summary(row)
        summary["stopped"] = threadrun_mod.is_stopped(row)
        summary["stop_reason"] = row.get("stop_reason")
        summary["unresolved_detail"] = [
            {"index": p["index"], "container_id": p.get("container_id"),
             "text_sha256": p.get("text_sha256"), "reply_to": p.get("reply_to"),
             "note": p.get("note"), "last_ok": p.get("last_ok")}
            for p in row["posts"]
            if p["state"] in (threadrun_mod.REQUESTED, threadrun_mod.UNRESOLVED)]
        summary["started_at"] = row.get("started_at")
        out.append(summary)
    return out


def _signature_state(check) -> str:
    """配布参照の署名を**確かめた結果**（監査 2 回目・P2-4）。

    `off`（確かめていない）／`verified`（確かめる設定で、署名では止まっていない）
    ／`unverified`（確かめられず、**取り込んでいない**）。

    **「確かめる設定か」と「確かめられたか」は別物。** 前は前者だけを見ていたので、
    `THTH_REQUIRE_SIGNED_RELEASE=1` で署名が確かめられずに配布を見送った回でも、
    board は「署名: 確認」と出していた——**いちばん人を呼ばなければならない回に、
    いちばん安心な語が出ていた。**
    """
    if not selfupdate_mod.require_signed_release():
        return "off"
    if check and not check.get("ok") \
            and selfupdate_mod.SIGNATURE_ERROR in str(check.get("error") or ""):
        return "unverified"
    return "verified"


def board_summary(now=None) -> dict:
    """アカウント・最終投稿・approved 待ち・inflight・型外・要確認の骨（設計 §4.6・
    外部レビュー再レビュー C で `needs_review`／`approval_stale_count` を追加）。"""
    accounts_out = []
    now = now if now is not None else jst.now_jst()
    for name in accounts_mod.list_account_names():
        try:
            account_cfg = accounts_mod.load_account(name)
        except accounts_mod.AccountStopped as e:
            from .stop_observation import reason
            code=reason(e)
            accounts_out.append({'account':name,'error':code,'stop_reason':code,'cannot_say':[code]})
            continue
        except accounts_mod.AccountError as e:
            accounts_out.append({"account": name, "error": str(e)})
            continue
        # 同期を伴わない読み手の照合先（外部レビュー第 5 巡 P2）。
        files = core.list_queue_files(
            account_cfg, tree_sha=writeback_mod.upstream_sha(account_cfg.get("repo_dir")))
        last_at = core.last_post_at(files, name)
        approved_waiting = sum(
            1 for qf in files
            if not qf.malformed and qf.front_matter.get("account") == name
            and qf.front_matter.get("status") == "approved"
            and not qf.front_matter.get("post_id")
        )
        type_mismatch = sum(1 for qf in files if qf.malformed)
        state_dir = accounts_mod.state_dir_for(name)
        inflight = inflight_mod.read(state_dir)
        notification_status = healthcheck_mod.read_status(state_dir)
        inflight_diagnostic = (healthcheck_mod.diagnostic(
            name, "fail", state_dir=state_dir) if inflight else None)
        # **取り下げ済み**の数（`thth retract`・設計 v2 §4.3）。queue の
        # front-matter と `sent/` の両方から（同じ post_id は 1 つに数える）。
        retracted_ids = {
            qf.front_matter.get("post_id") for qf in files
            if not qf.malformed and qf.front_matter.get("retracted_at")
            and qf.front_matter.get("post_id")}
        retracted_ids |= {row["post_id"] for row in sent_mod.retracted_records(state_dir)}
        # **いま run が走っているか**（引継ぎ 2026-09-13「小さいもの」）。
        # board は inflight しか見ていなかったので、**「実行中で待っている」と
        # 「止まっている」が同じ顔**だった（inflight が書かれるのは公開の直前
        # だけで、select・同期・書き戻しの間は空）。読み取りだけ——
        # **ロックを試しに取らない**（`AccountLock.holder_pid()` の docstring）。
        running_pid = lock_mod.AccountLock.holder_pid(
            accounts_mod.account_lock_path_for(name))
        # **採取の最中も見える口**（引継ぎ 2026-09-15 §3-D）。`collect` は repo を
        # 持つアカウントでは **repo のロックしか握らない**ので、上の
        # `running_pid`（account のロック）には出ない——10 分ごとの採取が走って
        # いても board は「止まっている」と同じ顔をしていた。
        #
        # **repo のロックは collect 専用ではない**（`approve`・`revoke`・
        # スレッド連投の 1 段も握る）。だから言えるのは「この repo で何かが
        # 走っている」まで。`thth run` は repo と account の両方を握るので、
        # **account のロックも握られていれば run**、repo だけなら採取の側、と
        # 読み分ける（`running` に出ているものは `collecting` から外す）。
        repo_dir_for_lock = account_cfg.get("repo_dir")
        repo_running_pid = None
        if isinstance(repo_dir_for_lock, str) and repo_dir_for_lock:
            repo_running_pid = lock_mod.AccountLock.holder_pid(
                accounts_mod.repo_lock_path_for(repo_dir_for_lock))
        # **同席の様態（`thth send`）で出したものも「最後に出したもの」に数える**
        # （運用の報告 2026-09-13: 実際に 2 媒体へ出したのに board の
        # `last_post` が `(なし)` のままだった）。
        #
        # 原因は `core.last_post_at()` が **queue の front-matter だけ**を見て
        # いること。`send` は queue を通らないので書き戻す front-matter が無く、
        # `state/<account>/sent/<post_id>.json` が唯一の記録になる
        # （`core._send_locked()`）。**出した事実がそこにしか無いなら、board も
        # そこを見るしかない。**
        #
        # **`select` の間合い（`min_interval_hours`）には混ぜない**。あちらが
        # 見る `core.last_post_at()` はそのまま——同席の送信は masaru がその場で
        # 決めるもので、不在の様態の間合いを消費させる筋のものではない。ここは
        # **board の表示だけ**を直す。
        sent_at, sent_row = sent_mod.latest_sent(state_dir)
        last_post_at = last_at
        last_post_source = "queue" if last_at is not None else None
        if sent_at is not None and (last_at is None or sent_at > last_at):
            last_post_at = sent_at
            last_post_source = "sent"
        collected_at = collect_mod.last_collected_at(account_cfg, name)
        inbox_state = collect_mod.read_inbox_state(name)
        needs_review = _needs_review_detail(
            files, account_name=name, account_cfg=account_cfg, now=now)
        token_row = maintain_mod.inspect(name, now=now)
        approval_stale_count = sum(1 for item in needs_review if item["reason"] == "approval_stale")
        accounts_out.append({
            "account": name,
            "project": account_cfg.get("project"),
            "last_post_at": last_post_at.isoformat() if last_post_at else None,
            # **どちらの記録から言っているか**（鍵の追加だけ・意味は変えない）。
            # `"queue"` は書き戻された front-matter（不在の様態）、`"sent"` は
            # `state/<account>/sent/`（同席の様態）、`None` は「出した記録が
            # 無い」。混ぜた 1 つの値だけを出すと、読み手が素性を確かめられない。
            "last_post_source": last_post_source,
            "last_sent_at": sent_at.isoformat() if sent_at else None,
            # **最後に採れたのはいつか**（監査 2 回目・P2-5）。同席専用の
            # アカウントは投稿の timer を持たないので、**採集が止まっていても
            # 画面には何も出なかった**。`None` は「1 度も採っていない」。
            "last_collected_at": (collected_at.isoformat() if collected_at else None),
            # **最後の採取で inbox（言及）がどうだったか**（本番 P1 2026-09-14）。
            # `"permission_missing"` は「トークンに権限が乗っていないので叩いて
            # いない」——失敗ではなく状態。`None` は記録が無い（判らない）。
            # 読むだけ（`collect.read_inbox_state()`・取りに行かない）。
            "inbox_state": inbox_state.get("state") if inbox_state else None,
            "inbox_permission": inbox_state.get("permission") if inbox_state else None,
            "last_sent_post_id": sent_row.get("post_id") if sent_row else None,
            "approved_waiting": approved_waiting,
            "type_mismatch": type_mismatch,
            "retracted_count": len(retracted_ids),
            "inflight": inflight.get("file") if inflight else None,
            # endpoint は check の秘密なので board は env を読まない。最後の run が
            # 安全な状態ファイルへ残した「設定有無・配送結果」だけを表示する。
            "incident_notifications": incident_mod.summary(account_cfg, state_dir),
            "inflight_started": (inflight_diagnostic.since
                                  if inflight_diagnostic else None),
            "inflight_reason": (inflight_diagnostic.reason
                                 if inflight_diagnostic else None),
            "inflight_reason_code": (inflight_diagnostic.reason_code
                                      if inflight_diagnostic else None),
            "inflight_next_action": (inflight_diagnostic.next_action
                                      if inflight_diagnostic else None),
            "inflight_next_action_code": (inflight_diagnostic.next_action_code
                                           if inflight_diagnostic else None),
            "notification_configured": (
                notification_status.get("endpoint_configured")
                if notification_status else None),
            "notification_delivery": (
                notification_status.get("delivery") if notification_status else None),
            "notification_last_attempt_at": (
                notification_status.get("last_attempt_at") if notification_status else None),
            "notification_last_state": (
                notification_status.get("last_state") if notification_status else None),
            "notification_category": (
                notification_status.get("category") if notification_status else None),
            # 指紋の 5 項目のどれが食い違って inflight が残ったか（外部レビュー
            # 第 3 巡・持ち越し項目 C）。`core._throw_chosen()` が
            # `text_mismatch_before_writeback`・`text_mismatch_after_rebase` の
            # ときに inflight へ書く。それ以外（曖昧な失敗等）の inflight では
            # None のまま——board を見た人が「なぜ止まっているか」をファイルを
            # 開かずに区別できるように。
            "inflight_mismatch_fields": inflight.get("mismatch_fields") if inflight else None,
            "needs_review": needs_review,
            "approval_stale_count": approval_stale_count,
            # **スレッド連投の進行状態**（独立検収 2026-09-11・P1-1）。
            # 「どこまで出たか」ではなく**確認できた段・要求中の段・未着手の段**
            # を分けて出す。**停止の確認**もここに出る。
            "threads": _thread_rows(name),
            # トークンの状態（`thth maintain` と同じ判定・読むだけで何も更新しない）。
            # 60 日の時限は投稿の可否と無関係に進むので、board に常に出す。
            # 値（access_token）には触れない——残り日数と状態だけ。
            "token_state": token_row["state"],
            "token_remaining_days": token_row["remaining_days"],
            # **「判らない」（null）と「期限を持たない」を混ぜない**
            # （設計 v2 §4.2）。`token_remaining_days: null` だけでは
            # 「読めなかった」と「期限が無い媒体」の区別が付かない。
            "token_no_expiry": bool(token_row.get("no_expiry")),
            # **まだ送れていない採取**（masaru 指示 2026-09-11: 失敗時の通知）。
            # push に失敗すると commit を取り消してファイルに残すので、
            # 未 push の commit は残らない（＝投稿は止まらない）。代わりに
            # **送れていないことに気づく口がここになる**。
            "collect_pending": len(collect_mod.pending_paths(
                account_cfg.get("repo_dir"), account_cfg)),
            # **見つかったときだけ True**。False は「走っていない」の証明では
            # ない（`holder_pid()` の但し書き）。`collect` は repo のロックしか
            # 握らないので、**採取の最中はここに出ない**。
            "running": running_pid is not None,
            "running_pid": running_pid,
            # **repo のロックを誰かが握っているか**。`running` と同じ但し書き
            # （見つかったときだけ True・False は「走っていない」の証明ではない）。
            "repo_running": repo_running_pid is not None,
            "repo_running_pid": repo_running_pid,
        })
    # 「動いているのに古い」を見える形にする（設計 §3.2・2026-09-10 に VM が
    # 4 巡分古いまま 10 分ごとに回っていたのを見つけた）。**取りに行かない**
    # （直前の `thth run` が fetch している）。
    #
    # **board は取りに行かない。だから「いま」を言えない**（外部レビュー F3 残件・
    # P2・2026-09-12）。**保存済みの記録から言えるのは「その時刻に確認した参照との
    # 比較」まで。** 記録が更新も削除もできない状態だと古い `ok: true` が残り、
    # 別プロセスの board が「追いついています」と出していた。**無効化の仕組みを
    # これ以上増やすのではなく、出力契約を変える**（外部レビューの指示）。
    #
    # **鍵の名前も変える**（規約 5）。`behind_release` のままだと、古い読み手が
    # 「remote に対する現在の遅れ」として読み続ける。
    #
    # **比較の基準は「最後に記録できた取得試行のときの配布参照」**（外部レビュー
    # F5・P2）。**表示する SHA と比較する SHA が別物だった**——表示は記録された
    # SHA、比較は**いまの** `origin/release`。記録の更新に失敗すると両者がずれ、
    # **実際は別の commit にいるのに「一致しています」と出た。** 基準を記録側へ
    # 揃えたので、**機械の読み手も同じ基準を確認できるように、その SHA を出す。**
    #
    # ここまでに直した読み違いも残しておく:
    # - `None` は「遅れていない」ではなく**「判らない」**（F2）
    # - `0` は「配ったもので動いている」ではない。**先にいても 0 になる**（F1）
    # **1 枚の画面は、1 組の SHA で作る**（外部レビュー F5 残件・P2・2026-09-12）。
    # 記録と `HEAD` を**ここで 1 度だけ読み**、以後この 2 値だけを使う。数える側で
    # 読み直すと、**その間に自己更新が終わったとき、表示と計数が別の SHA を指す。**
    app = release_summary()
    # **走っているものの一覧**（`--json` の読み手用）。アカウントの実行ロックを
    # 握っているものの名前と、自己更新（`_app.lock`）が走っていれば `"_app"`。
    running = [row["account"] for row in accounts_out if row.get("running")]
    app_pid = lock_mod.AccountLock.holder_pid(accounts_mod.app_lock_path())
    if app_pid is not None:
        running.append("_app")
    # **採取の側**（repo のロックだけを握っているもの）。`running` に出ている
    # ものは除く——`thth run` は両方握るので、二重に数えると「run と collect が
    # 同時に走っている」と読めてしまう。
    collecting = [row["account"] for row in accounts_out
                  if row.get("repo_running") and not row.get("running")]
    return {"accounts": accounts_out, "generated_at": jst.iso(),
            "running": running,
            "collecting": collecting,
            # **どこの台帳を読んで、この一覧を作ったか**（設計 v2 §3・v2-2a）。
            # 「外」と「repo の中（互換）」で並ぶ顔ぶれが変わる。**画面が誰の
            # 台帳を読んだのかを言わないと、移行のさなかに何が正か判らない。**
            "accounts_dir": accounts_mod.accounts_dir_info(),
            "app": app}


def release_summary():
    """One shared cached release observation for board and administrator reports."""
    check = selfupdate_mod.release_check()
    basis = (check or {}).get("release")
    head_sha = selfupdate_mod.head()
    behind = selfupdate_mod.behind_release(base=basis, head_sha=head_sha)
    ahead = selfupdate_mod.ahead_of_release(base=basis, head_sha=head_sha)
    return {"head": head_sha,
                    "release_ref": selfupdate_mod.RELEASE_REF,
                    "release_check": check,
                    "behind_cached_release": behind,
                    "ahead_cached_release": ahead,
                    "comparison_basis": "recorded_release",
                    # **署名を確かめているか**（セキュリティ監査 2026-09-14・P2-5）。
                    # 既定は off（いまの release は無署名なので、無条件に入れると
                    # 次の配布で VM が止まる）。**確かめていないことを黙らない。**
                    "signature_checked": selfupdate_mod.require_signed_release(),
                    # **確かめた結果**（監査 2 回目・P2-4）。`signature_checked` は
                    # 「確かめる設定か」でしかなく、**確かめられなかった回まで
                    # 「署名: 確認」と出していた**。3 値で言い分ける:
                    #   `off`        — 確かめていない（既定）
                    #   `verified`   — 確かめる設定で、最後の取得は署名で止まっていない
                    #   `unverified` — 確かめられず、**取り込んでいない**
                    "signature_state": _signature_state(check),
                    "comparison_ref_sha": basis,
                    # **board は取りに行かないので、常に未確認。**
                    "remote_current_verified": False}
