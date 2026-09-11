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


def _git(repo_dir: str, args: list):
    import subprocess
    return subprocess.run(["git", "-C", repo_dir, *args], capture_output=True, text=True)


def _undo_local_commit(repo_dir: str) -> bool:
    """直前の commit を取り消して、中身を作業ツリーに戻す（`reset --soft`）。

    **収集は、未 push の commit を一度も残さない**（masaru 裁定 2026-09-11）。

    第 6 巡・第 7 巡の P1 は、どちらもここから出た。push に失敗すると
    `HEAD != @{u}` が残り、`sync_repo()` がそれを拒否して**採取も投稿も止まる**。
    第 6 巡ではそれを「自動で送り直す」機構で塞いだが、**その機構が第 7 巡の
    公開の穴を作った**（merge を見落として撤回を消す）。

    **足して固めるのをやめ、状態そのものを作らない形にする。** push できなければ
    commit を取り消す。中身はファイルに残るので、次の実行が commit し直す。
    未 push が存在しないので、投稿は止まらない。rebase もしないので、
    merge の穴も stage の問題も起きない。
    """
    return _git(repo_dir, ["reset", "--soft", "HEAD~1"]).returncode == 0


def pending_paths(repo_dir: str, account_cfg: dict) -> list:
    """**まだ送れていない収集ファイル**（作業ツリーで変わっているもの）。

    `thth board` がこれを出す。healthchecks を入れるまで、**採取が送れていない
    ことに気づく唯一の口**（masaru 指示 2026-09-11: 失敗時の通知）。
    """
    if not repo_dir or not os.path.isdir(repo_dir):
        return []
    status = _git(repo_dir, ["status", "--porcelain", "--", "data/sns"])
    if status.returncode != 0:
        return []
    out = []
    for line in status.stdout.splitlines():
        path = line[3:].strip()
        if path:
            out.append(path)
    return sorted(out)


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


def _with_bundle_posts(files: list, account_name: str, account_cfg: dict, *,
                        errors: list) -> list:
    """v1 のファイル一覧に、**v2 の段を 1 投稿 1 件として足す**（P2-7）。

    段ごとに `post_id` と `posted_at` を持つ**疑似 queue ファイル**にして、
    既存の収集ループにそのまま流す。**計測は投稿単位のまま**（設計 §9）で、
    束への紐付けは `thth/forms.py` の `bundle_outcome()` が行う。

    `errors` に読めなかった束を積む（監査 2026-09-11・掃討で検出）。
    以前は `problems`（media の節が無い等）が非空だと `continue` で黙って
    その束の全投稿を採取から落としていた。account の `media` を書き換えると
    （表記の修正など）旧 media の節が見つからなくなり、公開済みの投稿があっても
    `posts: 0`・`errors: []` で正常終了に見えてしまっていた。この冒頭コメントの
    規約 12「取れなかった指標は書かない。0 と混ぜると判らなくなる」がまさに
    ここで破れていたので、読めなかった事実を `errors` に残す。**他の束の採取は
    続ける**——1 件読めないことを全部読めないことにしない。
    """
    from . import bundle as bundle_mod
    from . import queuefile as queuefile_mod

    out = list(files)
    for qf in files:
        if not qf.malformed:
            continue
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
        segments, problems = bundle_mod.load_segments(b, account_cfg["media"])
        if problems:
            errors.append(f"{qf.path}: bundle: " + "; ".join(problems))
            continue
        for i, post in enumerate(b.posts, start=1):
            post_id = bundle_mod.unquote(post.get("post_id"))
            if not post_id or not post.get("posted_at"):
                continue
            fm = dict(b.front_matter)
            fm.update({"post_id": post_id, "posted_at": post.get("posted_at"),
                        "reply_to": bundle_mod.unquote(post.get("reply_to")) or None,
                        "thread_index": str(i), "thread_run_id":
                            bundle_mod.unquote(post.get("run_id"))})
            # 段ごとの本文を「その投稿の本文」として持たせる。
            body = f"## {account_cfg['media']}\n\n{segments[i - 1]}\n" \
                if i <= len(segments) else qf.body
            out.append(queuefile_mod.QueueFile(
                path=f"{qf.path}#{i}", malformed=False, front_matter=fm,
                body=body, verified=qf.verified))
    return out


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
    # **スレッド連投の段も拾う**（独立検収 2026-09-11・P2-7）。
    # v1 の `qf.malformed` 判定が `thth: 2` を落とすので、3 段公開しても
    # **収集対象は 0 件だった。** 出したものを測れないなら、出す意味が薄い。
    for qf in _with_bundle_posts(files, account_name, account_cfg, errors=errors):
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
                # **未 push の commit を残さない。** 残すと `HEAD != @{u}` になり、
                # 次回以降の同期検査が投稿ごと止める（第 6 巡 P1-1）。中身は
                # ファイルに残るので、次の実行が commit し直す。
                undone = _undo_local_commit(repo_dir)
                log(f"採取したものを push できませんでした: {push_err}")
                log("送れていない採取はファイルに残しました"
                    + ("（commit は取り消したので投稿は止まりません）。"
                       if undone else
                       "。**commit を取り消せませんでした。投稿が止まる可能性があります。**")
                    + " `thth board` の 未送信 に出ます。次の実行で送り直します。")
                return 1
            log(f"採取しました: {len(rel)} ファイル（投稿 {result['posts']} 本を見ました）")
    finally:
        repo_lock.release()

    for err in result["errors"]:
        log("採取の失敗: " + err)
    return 1 if result["errors"] else 0
