"""数と返信の収集（T3・T4）— **経過時間で取る**（masaru 裁定 2026-09-10）。

**なぜ経過時間なのか。** 表示回数は積み上がるので、**投稿どうしで「いまの数字」を
比べても意味がない**（古い投稿ほど大きくなるだけ）。比べていいのは**同じ経過時間の
数字**——投稿から 24 時間後の値どうし、のように。

設計 §4.5 は当初「採取は 1 日 1 回でよい」としていた。**取り消す。** 1 日 1 回だと、
07:00 の投稿と 20:00 の投稿で最初の採取時点の経過時間が 13 時間ずれ、そのまま
比べると「時刻の差」ではなく「経過時間の差」を測ってしまう。

**そして、逃すと取り返せない。** Threads の Insights は「読んだ時点の累計」しか
返さない。3 日後に初めて読んでも 24 時間後の値は**永久に復元できない**。差が
いちばん出る初速がそこにあるので、**出た瞬間から取り始める**しかない。

採取の刻み（`AGE_MARKS_HOURS`）を投稿からの経過時間で置き、`thth run`（10 分刻み）
のたびに「まだ記録していない刻みを跨いだか」だけを見る。跨いでいれば 1 回読んで
1 行足す。**追記のみ・冪等**（同じ刻みを二度書かない）。

置き場（設計 §4.4・§4.5・利用者 repo）:

- `data/sns/insights/posts/<post_id>.ndjson` — 1 回の採取 1 行
- `data/sns/replies/<post_id>.ndjson` — 1 返信 1 行（`id` で重複除去）
- `data/sns/insights/account/<account>-<YYYY-MM>.ndjson` — 1 日 1 行（前日ぶん）

**取れなかった指標は書かない。** 0 と混ぜると、あとから「0 だったのか取れなかったのか」
が判らなくなる（規約 12: 判らないものを判らないと言う）。
"""
from __future__ import annotations

import datetime
import json
import os

from . import accounts as accounts_mod
from . import core
from . import jst
from . import queuefile
from . import redact as redact_mod
from . import writeback

# 投稿からの経過時間の刻み（時間）。**比較の単位はここ。**
# 初速（1h・6h）と、落ち着いたあと（24h・72h・7d）。
AGE_MARKS_HOURS = [1, 6, 24, 72, 168]


# **収集が書いてよい場所**（利用者 repo の中）。ここだけは、未 push の commit が
# 残っていたら次回に送り直してよい。**queue（`docs/sns/queue/`）は含めない**——
# あちらは公開の経路で、未 push の commit を自動で送るのは fail-closed を弱める。
COLLECTION_PREFIXES = ("data/sns/",)


def _git(repo_dir: str, args: list):
    import subprocess
    return subprocess.run(["git", "-C", repo_dir, *args], capture_output=True, text=True)


def push_pending_collection(repo_dir: str, *, log=print) -> tuple:
    """未 push の commit が**収集ぶんだけ**なら送り直す（外部レビュー第 6 巡 P1-1）。

    **なぜ要るか。** 収集は commit してから push する。push に失敗すると
    `HEAD != @{u}` のまま残り、`writeback.sync_repo()` がそれを拒否する。
    その結果——

      - 次回以降の収集も止まる（同期できないので）
      - **予約投稿も止まる**（同じ clone を使うので）
      - origin が復旧しても、**未 push の commit を送る経路がどこにも無い**

    採取の失敗が投稿を恒久的に止める、という形になっていた。実際に外部レビューが
    再現した（origin を一時的に壊し、直したあとも exit 2 が続く）。

    **直し方の線引き。** 「未 push があっても通す」にはしない——それは公開側の
    fail-closed を弱める。**未 push の commit が触ったパスを全部見て、
    `data/sns/` の下だけだったときに限り**送り直す。queue を触る未 push の commit
    （書き戻しが中断した等）が 1 つでも混ざっていれば、**送らずに人を呼ぶ**。

    戻り値 `(送り直したか, 説明)`。
    """
    head = _git(repo_dir, ["rev-parse", "HEAD"])
    upstream = _git(repo_dir, ["rev-parse", "@{u}"])
    if head.returncode != 0 or upstream.returncode != 0:
        return False, ""
    if head.stdout.strip() == upstream.stdout.strip():
        return False, ""          # 未 push は無い

    ok, why = _only_collection_ahead(repo_dir)
    if not ok:
        return False, why

    fetch = _git(repo_dir, ["fetch", "origin"])
    if fetch.returncode != 0:
        return False, "fetch に失敗しました（origin にまだ届きません）"

    # fetch で remote が進んでいたら、条件をもう一度見る（間に何か入っている場合）。
    ok, why = _only_collection_ahead(repo_dir)
    if not ok:
        return False, why

    # **stage 状態を守る**（外部レビュー第 7 巡 P2-4）。この経路にも rebase がある
    # のに、書き戻し側だけ直して**ここには入れていなかった**。
    saved_tree, staged_paths = writeback.save_index(repo_dir)
    before_pull = _git(repo_dir, ["rev-parse", "HEAD"]).stdout.strip()
    try:
        rebase = _git(repo_dir, ["pull", "--rebase", "--autostash"])
    finally:
        writeback.restore_index(repo_dir, saved_tree, staged_paths,
                                 since=before_pull, log=log)
    if rebase.returncode != 0:
        return False, "未 push の収集を載せ直せませんでした: " + redact_mod.redact(rebase.stderr)

    # **載せ直したあとに、もう一度確かめてから送る。** rebase が何を作ったかは
    # 事前の検査では分からない（第 7 巡 P1-1）。
    ok, why = _only_collection_ahead(repo_dir)
    if not ok:
        return False, ("載せ直した結果が収集ぶんだけになりませんでした。送りません: " + why)

    push = _git(repo_dir, ["push"])
    if push.returncode != 0:
        return False, "未 push の収集をまだ送れません: " + redact_mod.redact(push.stderr)
    log("前回送れなかった収集を送り直しました")
    return True, ""


def _only_collection_ahead(repo_dir: str) -> tuple:
    """未 push の中身が**収集ぶんだけ**かを確かめる `(よいか, 理由)`。

    **`git log --name-only` は使わない**（外部レビュー第 7 巡 P1-1）。
    **merge commit は、既定では変更したファイルを 1 つも出さない。** そのため
    「何も触っていない commit」に見え、queue の撤回を含む merge がパス検査を
    素通りした。その状態で rebase すると**撤回が落ち、古い承認が復活して公開された**。

    直し方は 2 つ重ねる:

    1. **merge commit があれば、そもそも自動で送らない**（人を呼ぶ）。
    2. パスは `git diff --name-only @{u}...HEAD`（**3 点**＝分岐点から HEAD まで）で
       取る。merge の有無に関わらず、こちら側で変わったファイルが全部出る。
    """
    merges = _git(repo_dir, ["rev-list", "--merges", "@{u}..HEAD"])
    if merges.returncode != 0:
        return False, "未 push の commit を読めません"
    if merges.stdout.strip():
        return False, ("未 push に merge commit が含まれるので、自動では送りません。"
                       "人が確認してください。")

    names = _git(repo_dir, ["diff", "--name-only", "@{u}...HEAD"])
    if names.returncode != 0:
        return False, "未 push の commit が何を触ったか読めません"
    touched = {line.strip() for line in names.stdout.splitlines() if line.strip()}
    if not touched:
        return True, ""
    outside = sorted(p for p in touched
                     if not any(p.startswith(prefix) for prefix in COLLECTION_PREFIXES))
    if outside:
        return False, ("未 push の commit が収集以外のファイルを含むので、自動では送りません"
                       f"（{', '.join(outside[:3])}）。人が確認してください。")
    return True, ""


def recover_pending(account_name: str, *, log=print) -> None:
    """`thth run` が**投稿の前に**呼ぶ。送り直せるものがあれば送る。

    投稿より先に呼ぶのは、**収集の取り残しが投稿を止めたままにしない**ため。
    ロックは投稿と同じ clone ロックを取る（取れなければ何もしない）。
    """
    from . import lock as lock_mod
    try:
        account_cfg = accounts_mod.load_account(account_name)
    except accounts_mod.AccountError:
        return
    repo_dir = account_cfg.get("repo_dir")
    if not repo_dir or not os.path.isdir(repo_dir):
        return
    lock = lock_mod.AccountLock(accounts_mod.repo_lock_path_for(repo_dir))
    try:
        lock.acquire()
    except lock_mod.LockBusy:
        return
    try:
        recovered, message = push_pending_collection(repo_dir, log=log)
        if message and not recovered:
            log("未 push の収集: " + message)
    finally:
        lock.release()


def _read_ndjson(path: str) -> list:
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    return out


def _append_ndjson(path: str, rows: list) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _reply_fetches(path: str) -> list:
    """返信ファイルのうち、**取得を試みた記録**の行だけ（`kind: fetch`）。"""
    return [row for row in _read_ndjson(path) if row.get("kind") == "fetch"]


def _reply_rows(path: str) -> list:
    """返信ファイルのうち、**返信そのもの**の行だけ。

    `kind` を持たない古い行は返信として扱う（2026-09-10 以前に書いたもの）。
    """
    return [row for row in _read_ndjson(path) if row.get("kind") != "fetch"]


def due_marks(age_hours: float, recorded: list) -> list:
    """まだ記録していない刻みのうち、もう跨いだもの。"""
    done = set()
    for row in recorded:
        for mark in row.get("marks") or []:
            done.add(mark)
    return [m for m in AGE_MARKS_HOURS if age_hours >= m and m not in done]


def collect_once(account_name: str, *, adapter, now=None, log=print) -> dict:
    """1 アカウントぶんの採取。**書き込みと push は呼び出し側（`run_collect`）。**

    戻り値は `{"insight_files": [...], "reply_files": [...], "posts": n, "errors": [...]}`。
    どれか 1 本で失敗しても、ほかは続ける（採取は投稿と違い、部分的に成功して
    構わない——次の実行で埋まる）。
    """
    now = now if now is not None else jst.now_jst()
    account_cfg = accounts_mod.load_account(account_name)
    repo_dir = account_cfg.get("repo_dir") or ""
    collect_days = int(account_cfg.get("collect_days", 14) or 14)

    tree_sha = writeback.upstream_sha(repo_dir)
    files = core.list_queue_files(account_cfg, tree_sha=tree_sha)

    insights_dir = os.path.join(repo_dir, "data", "sns", "insights", "posts")
    replies_dir = os.path.join(repo_dir, account_cfg.get("replies_dir") or "data/sns/replies")

    touched, errors, posts_seen = [], [], 0
    for qf in files:
        if qf.malformed:
            continue
        fm = qf.front_matter
        if fm.get("account") != account_name:
            continue
        post_id = fm.get("post_id")
        posted_at_raw = fm.get("posted_at")
        if not post_id or not posted_at_raw:
            continue
        try:
            posted_at = jst.parse(posted_at_raw) if hasattr(jst, "parse") else \
                datetime.datetime.fromisoformat(posted_at_raw)
        except (TypeError, ValueError):
            continue
        age_hours = (now - posted_at).total_seconds() / 3600.0
        if age_hours < 0 or age_hours > collect_days * 24:
            continue
        posts_seen += 1

        section = queuefile.extract_section(qf.body, account_cfg["media"])
        insight_path = os.path.join(insights_dir, f"{post_id}.ndjson")
        reply_path = os.path.join(replies_dir, f"{post_id}.ndjson")

        # **数と返信で、済んだ刻みを別々に持つ**（外部レビュー第 6 巡 P2-3）。
        # 以前は insights の記録だけから刻みを計算していたので、**数が取れて返信が
        # 失敗すると、その刻みは「済んだ」ことになり、返信は二度と取りに行かなかった**。
        # 最後の刻み（168 時間）で失敗すると、その投稿の返信は永久に取れない。
        # 「部分的な成功は次の実行で埋まる」という約束に反していた。
        marks = due_marks(age_hours, _read_ndjson(insight_path))
        reply_marks = due_marks(age_hours, _reply_fetches(reply_path))
        if not marks and not reply_marks:
            continue

        # --- 数
        metrics = None
        if marks:
            try:
                metrics = adapter.insights(post_id)
            except Exception as e:  # 採取の失敗で投稿を止めない
                errors.append(f"{post_id}: insights: {redact_mod.redact(str(e))}")
                metrics = None
        if metrics is not None:
            _append_ndjson(insight_path, [{
                "post_id": post_id,
                "file": os.path.basename(qf.path),
                # **トピックを一緒に残す**（masaru 指摘 2026-09-10）。asmon は
                # フォロワー 0 で `中学受験` を付けた投稿が 200〜574 views、
                # nigamilab のトピック無しは 1 view。**届ける経路はフォロワー
                # ではなくトピック**なので、数と一緒に記録しないと後から
                # 突き合わせられない。front-matter から取るので API は増やさない。
                "topic": queuefile.normalize_topic(fm.get("topic")),
                "reply_to": fm.get("reply_to") or None,
                "text_length": len(section) if section is not None else None,
                "has_link": ("http://" in (section or "")) or ("https://" in (section or "")),
                "collected_at": jst.iso(now),
                "posted_at": posted_at_raw,
                "age_hours": round(age_hours, 2),
                "marks": marks,
                "metrics": metrics,
            }])
            touched.append(insight_path)

        # --- 返信（`id` で重複除去して追記）
        if reply_marks:
            try:
                replies = adapter.replies(post_id)
            except Exception as e:
                errors.append(f"{post_id}: replies: {redact_mod.redact(str(e))}")
                replies = None
            if replies is not None:
                known = {row.get("id") for row in _reply_rows(reply_path)}
                fresh = [{"kind": "reply", "collected_at": jst.iso(now),
                          "post_id": post_id, **row}
                         for row in replies if row.get("id") and row["id"] not in known]
                # **取れたことそのものを 1 行残す**（返信 0 件の成功と、取得の失敗を
                # 区別するため。これが無いと「0 件だった」を「まだ取っていない」と
                # 読んでしまい、毎回取りに行く／二度と取りに行かない、のどちらかになる）。
                _append_ndjson(reply_path, fresh + [{
                    "kind": "fetch", "post_id": post_id, "collected_at": jst.iso(now),
                    "age_hours": round(age_hours, 2), "marks": reply_marks,
                    "replies": len(replies)}])
                touched.append(reply_path)
                if fresh:
                    log(f"返信 {len(fresh)} 件: {post_id}")

    # --- アカウント単位の日次（前日ぶん・`clicks` はここでしか取れない）
    account_path = _collect_account_daily(account_name, account_cfg, adapter,
                                           now=now, errors=errors)
    if account_path:
        touched.append(account_path)

    return {"touched": sorted(set(touched)), "posts": posts_seen, "errors": errors}


def _collect_account_daily(account_name, account_cfg, adapter, *, now, errors) -> str | None:
    """**前日の閉じた 1 日**を 1 行だけ記録する（外部レビュー §4-a）。

    当日ぶんを取ると、そのあとに起きた反応が記録に入らない。閉じた日だけ取る。
    """
    repo_dir = account_cfg.get("repo_dir") or ""
    yesterday = (now - datetime.timedelta(days=1)).date()
    path = os.path.join(repo_dir, "data", "sns", "insights", "account",
                        f"{account_name}-{yesterday.strftime('%Y-%m')}.ndjson")
    day = yesterday.isoformat()
    if any(row.get("date") == day for row in _read_ndjson(path)):
        return None

    start = datetime.datetime.combine(yesterday, datetime.time(0, 0), tzinfo=jst.JST)
    end = start + datetime.timedelta(days=1)
    token = accounts_mod.load_token(account_cfg) or {}
    user_id = token.get("user_id") or account_cfg.get("user_id")
    if not user_id:
        return None
    try:
        metrics = adapter.account_insights(
            user_id, since=str(int(start.timestamp())), until=str(int(end.timestamp())))
    except Exception as e:
        errors.append(f"account_insights: {redact_mod.redact(str(e))}")
        return None
    if not metrics:
        return None
    _append_ndjson(path, [{"account": account_name, "date": day,
                            "collected_at": jst.iso(now), "metrics": metrics}])
    return path


def run_collect(account_name: str, *, adapter=None, now=None, log=print) -> int:
    """`thth collect <account>`／`thth run` から呼ぶ入口。ロック・同期・push まで。

    **投稿と同じ clone ロックを取る**（同じ作業ツリーに書くので）。同期できなければ
    採取しない（fail-closed。書いたものが push できない状態を作らない）。

    採取は**部分的な成功を許す**——1 本の投稿で失敗しても他は続け、次の実行で
    埋まる。投稿と違って取り返しがつくため。終了コードは「1 本でも失敗したか」。
    """
    from . import lock as lock_mod

    now = now if now is not None else jst.now_jst()
    try:
        account_cfg = accounts_mod.load_account(account_name)
    except accounts_mod.AccountError as e:
        log(str(e))
        return 2
    repo_dir = account_cfg.get("repo_dir")
    if not repo_dir or not os.path.isdir(repo_dir):
        return 0  # 送信専用アカウント等。採るものが無い。

    if adapter is None:
        token = accounts_mod.load_token(account_cfg)
        if token is None:
            log(f"token が無いので採取しません: {account_name}")
            return 2
        adapter = core._default_adapter_factory(account_cfg, token)

    repo_lock = lock_mod.AccountLock(accounts_mod.repo_lock_path_for(repo_dir))
    try:
        repo_lock.acquire()
    except lock_mod.LockBusy:
        log(f"repo を別の実行が使っているので採取を見送ります: {repo_dir}")
        return 0  # 次の実行（10 分後）で採る

    try:
        # **同期を試す前に、前回送れなかった収集を送り直す**（第 6 巡 P1-1）。
        # これが無いと、一度 push に失敗しただけで採取も投稿も永久に止まる。
        recovered, recover_msg = push_pending_collection(repo_dir, log=log)
        if recover_msg and not recovered:
            log("未 push の収集: " + recover_msg)

        synced, sync_err, _sha = writeback.sync_repo(repo_dir)
        if not synced:
            log(f"repo を同期できないので採取しません: {sync_err}")
            return 2

        result = collect_once(account_name, adapter=adapter, now=now, log=log)

        if result["touched"]:
            rel = [os.path.relpath(os.path.realpath(p), os.path.realpath(repo_dir))
                   for p in result["touched"]]
            pushed, push_err = writeback.commit_and_push(
                repo_dir, rel_path=rel,
                message=f"収集: {len(rel)} ファイル（{account_name}）")
            if not pushed:
                log(f"採取したものを push できませんでした: {push_err}")
                return 1
            log(f"採取しました: {len(rel)} ファイル（投稿 {result['posts']} 本を見ました）")
    finally:
        repo_lock.release()

    for err in result["errors"]:
        log("採取の失敗: " + err)
    return 1 if result["errors"] else 0
