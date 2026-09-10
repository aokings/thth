"""`thth queue` / `thth board`（発注 §3・受け入れ 4）。CLI と MCP の両方から呼ばれる。"""
from __future__ import annotations

import datetime
import os

from . import accounts as accounts_mod
from . import core
from . import inflight as inflight_mod
from . import maintain as maintain_mod
from . import selfupdate as selfupdate_mod
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
        files = core.list_queue_files(
            account_cfg, tree_sha=writeback_mod.upstream_sha(account_cfg.get("repo_dir")))
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


def board_summary(now=None) -> dict:
    """アカウント・最終投稿・approved 待ち・inflight・型外・要確認の骨（設計 §4.6・
    外部レビュー再レビュー C で `needs_review`／`approval_stale_count` を追加）。"""
    accounts_out = []
    now = now if now is not None else jst.now_jst()
    for name in accounts_mod.list_account_names():
        try:
            account_cfg = accounts_mod.load_account(name)
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
        needs_review = _needs_review_detail(
            files, account_name=name, account_cfg=account_cfg, now=now)
        token_row = maintain_mod.inspect(name, now=now)
        approval_stale_count = sum(1 for item in needs_review if item["reason"] == "approval_stale")
        accounts_out.append({
            "account": name,
            "project": account_cfg.get("project"),
            "last_post_at": last_at.isoformat() if last_at else None,
            "approved_waiting": approved_waiting,
            "type_mismatch": type_mismatch,
            "inflight": inflight.get("file") if inflight else None,
            # 指紋の 5 項目のどれが食い違って inflight が残ったか（外部レビュー
            # 第 3 巡・持ち越し項目 C）。`core._throw_chosen()` が
            # `text_mismatch_before_writeback`・`text_mismatch_after_rebase` の
            # ときに inflight へ書く。それ以外（曖昧な失敗等）の inflight では
            # None のまま——board を見た人が「なぜ止まっているか」をファイルを
            # 開かずに区別できるように。
            "inflight_mismatch_fields": inflight.get("mismatch_fields") if inflight else None,
            "needs_review": needs_review,
            "approval_stale_count": approval_stale_count,
            # トークンの状態（`thth maintain` と同じ判定・読むだけで何も更新しない）。
            # 60 日の時限は投稿の可否と無関係に進むので、board に常に出す。
            # 値（access_token）には触れない——残り日数と状態だけ。
            "token_state": token_row["state"],
            "token_remaining_days": token_row["remaining_days"],
        })
    # 「動いているのに古い」を見える形にする（設計 §3.2・2026-09-10 に VM が
    # 4 巡分古いまま 10 分ごとに回っていたのを見つけた）。取りに行かない
    # （直前の `thth run` が fetch している）。
    return {"accounts": accounts_out, "generated_at": jst.iso(),
            "app": {"head": selfupdate_mod.head(),
                    "behind_origin": selfupdate_mod.behind_origin()}}
