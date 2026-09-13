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

**投稿ごとの台帳の行は、採取時点の所有 account を自分自身に刻む**（外部レビュー
再判定 R3・2026-09-12）。`thth/measured.py` が過去、現在の原稿の account を
過去の所有として使い、原稿の account を書き換えると過去の台帳が黙って移し替え
られる欠陥があった。**過去の行は追記専用なので書き換えない**——`account` を
持たない古い行は「不明」として扱われる（`thth/measured.py` 参照）。
"""
from __future__ import annotations

import dataclasses
import datetime
import json
import os
import re

from . import accounts as accounts_mod
from . import core
from . import jst
from . import measured as measured_mod
from . import queuefile
from . import redact as redact_mod
from . import writeback
from .adapters import base as adapter_base

# 投稿からの経過時間の刻み（時間）。**比較の単位はここ。**
# 初速（1h・6h）と、落ち着いたあと（24h・72h・7d）。
AGE_MARKS_HOURS = [1, 6, 24, 72, 168]

# 利用者から始まった会話の置き場（設計 v2 §4.2「採集と実測の媒体差」）。
# **WhatsApp の芽**——`inbox` を持つアダプタがあれば、`collect` がここへ追記する。
INBOX_DIR = ("data", "sns", "inbox")


def _git(repo_dir: str, args: list):
    import subprocess
    return subprocess.run(["git", "-C", repo_dir, *args], capture_output=True, text=True)


def _safe_post_id(post_id) -> bool:
    """`post_id` をファイル名として使ってよいか。**外へ出る値を弾く。**"""
    if not isinstance(post_id, str) or not post_id.strip():
        return False
    if post_id in (".", ".."):
        return False
    return not any(c in post_id for c in ("/", "\\", "\x00"))


def _has_unpushed_commit(repo_dir: str) -> bool:
    """**自分が作った commit が未 push で残っているか。**

    `commit_and_push()` は add／commit／push の失敗を同じ `False` にまとめるので、
    **戻り値だけでは巻き戻してよいか分からない**（add で失敗していれば HEAD は
    動いていない）。**HEAD と upstream を見て決める。**
    """
    r = _git(repo_dir, ["rev-list", "--count", "@{u}..HEAD"])
    if r.returncode != 0:
        return False
    try:
        return int(r.stdout.strip()) > 0
    except ValueError:
        return False


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


def _read_ndjson_strict(path: str) -> tuple:
    """`(行, 壊れているか)`。**壊れた行を黙って飛ばさない。**

    `_read_ndjson()` は壊れた行を無視する（**定期取得では、1 行の壊れで採取全体を
    止めない**ため）。**取り直しでは流用しない**——壊れた台帳に追記すると、
    **何が入っていたか分からないまま上に積む**ことになる（外部レビュー B・
    2026-09-12）。
    """
    out = []
    if not os.path.exists(path):
        return (out, False)
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                return (out, True)
    return (out, False)


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


def reply_row_id(row):
    """返信 1 行の識別子。**`message_id` と `id` のどちらでも通す**（T3・2026-09-13）。

    Threads の `/conversation` は API の生の行をそのまま返すので鍵は **`id`**。
    Bluesky・Mastodon のアダプタは境界の `Message`（設計 v2 §4.2）の形で返すので
    鍵は **`message_id`**。ここが `id` 固定だったので、**Bluesky と Mastodon の
    返信は 1 行残らず「id 欠落」で捨てられていた**（`id_missing` にだけ数が載り、
    台帳には 1 件も入らない）。境界が `message_id` と決めた以上、直すのは読む側。

    **Threads の行は変えない**——返信の台帳（ndjson）は追記専用で、過去の行と
    同じ鍵で読めなくなると `thth replies` も `measured` も黙って壊れる。
    `message_id` を先に見るのは、両方持つ行（`Message` を dict に写したものに
    API の `id` が残っている場合）で境界の鍵を正とするため。
    """
    if not isinstance(row, dict):
        return None
    return row.get("message_id") or row.get("id")


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


def _save_replies(reply_path: str, post_id: str, replies: list, *, now,
                   age_hours, marks: list, trigger: str) -> list:
    """取ってきた会話を台帳へ追記する。**定期取得と `--refresh` で共有する。**

    **重複除去を二重実装しない**（外部レビュー B・2026-09-12）。台帳の中だけでなく
    **取ってきた配列の中の重複も除く**——頁の境界で同じ返信が 2 度現れうるし、
    同じ応答が `R1,R1,R2` でも台帳が `R1,R1,R2` になっていた。

    **`marks` は呼び出し側が決める。** `--refresh` は `[]` を渡す——**臨時の取得で
    刻みを進めない。** 進めると「24h の数」に化ける。

    **識別子の無い行を、黙って成功件数に含めない。** 識別子は `message_id` か
    `id`（`reply_row_id()`・媒体で鍵の名前が違う）。**API の行が台帳の管理項目
    （`kind`・`post_id`・`collected_at`）を上書きしないように、後から置く。**

    戻り値は**新しく入れた返信の行**。
    """
    known = {reply_row_id(row) for row in _reply_rows(reply_path)}
    fresh, 欠落 = [], 0
    for row in replies:
        rid = reply_row_id(row)
        if not rid:
            欠落 += 1
            continue
        if rid in known:
            continue
        known.add(rid)
        # **管理項目を後に置く**——API の行に同名の値があっても上書きさせない。
        fresh.append({**row, "kind": "reply", "post_id": post_id,
                       "collected_at": jst.iso(now)})
    # **取れたことそのものを 1 行残す**（返信 0 件の成功と、取得の失敗を区別する
    # ため。これが無いと「0 件だった」を「まだ取っていない」と読んでしまい、
    # 毎回取りに行く／二度と取りに行かない、のどちらかになる）。
    _append_ndjson(reply_path, fresh + [{
        "kind": "fetch", "post_id": post_id, "collected_at": jst.iso(now),
        "age_hours": None if age_hours is None else round(age_hours, 2),
        "marks": marks, "trigger": trigger,
        "replies": len(replies), "id_missing": 欠落}])
    return fresh


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
        if not _safe_post_id(post_id):
            # **`post_id` をそのままパスにしない**（独立検収 B・2026-09-12）。
            errors.append(f"post_id にパス区切りが入っています: {post_id!r}")
            continue
        try:
            posted_at = jst.parse(posted_at_raw) if hasattr(jst, "parse") else \
                datetime.datetime.fromisoformat(posted_at_raw)
            # **時間帯の無い `posted_at` で全体を止めない**（同上）。
            # `fromisoformat` は通るのに引き算で落ち、**他の正常な投稿まで
            # 採れなくなっていた。**
            age_hours = (now - posted_at).total_seconds() / 3600.0
        except (TypeError, ValueError):
            errors.append(f"{post_id}: posted_at を読めません（{posted_at_raw!r}）")
            continue
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
                # **「取れなかった指標」と「そもそも媒体に無い指標」を分ける**
                # （設計 v2 §4.2）。`available` はその媒体が持っている指標の
                # 名前で、そこに無いものは実測の行に `null` を書く——
                # **捨てない・数える**（設計 v1 §3.2.2）。あとで
                # `account_report.comparable_views()` が「媒体に views が無い」
                # という理由で比較から外し、件数と理由を残す。
                metrics, available = adapter_base.metrics_of(adapter.insights(post_id))
                if available is not None:
                    for name in measured_mod.POST_METRIC_NAMES:
                        if name not in available:
                            metrics.setdefault(name, None)
            except Exception as e:  # 採取の失敗で投稿を止めない
                errors.append(f"{post_id}: insights: {redact_mod.redact(str(e))}")
                metrics = None
        if metrics is not None:
            _append_ndjson(insight_path, [{
                "post_id": post_id,
                "file": os.path.basename(qf.path),
                # **採取時点の所有 account を根拠として残す**（外部レビュー
                # 再判定 R3・2026-09-12）。以前は台帳の行に `account` が無く、
                # `thth/measured.py` が `file` から**現在の**原稿を開いて
                # その account を過去の所有として使っていた。原稿の account を
                # 書き換えると、過去の台帳が黙って新しい account の実測へ
                # 移し替えられてしまっていた。**採取した「いま」判っている
                # account をこの行自身に刻む**ことで、あとから原稿の account が
                # 変わっても、この行の所有は変わらない。過去に書いた行は
                # 追記専用の台帳なので書き換えない——`account` が無い行は
                # `thth/measured.py` 側で「不明」として扱う。
                "account": account_name,
                # **どの媒体で測った数か**（設計 v2 §2.1「媒体をまたいで比較
                # しない」）。台帳の `media` を書き換えても過去の行は動かない
                # ——`account` を行に刻んだのと同じ理由（R3・2026-09-12）。
                "medium": account_cfg.get("media"),
                # **トピックを一緒に残す**（masaru 指摘 2026-09-10）。asmon は
                # フォロワー 0 で `中学受験` を付けた投稿が 200〜574 views、
                # nigamilab のトピック無しは 1 view。
                # **訂正 2026-09-12**: この「1 view」は**経過が数時間の疎通確認投稿**で、同じ投稿が 9/12 時点で **112 views**。**400 倍の大半は経過時間だった。**トピックが効かないという意味ではなく、**この数字では判定できない。**
                # **届ける経路はフォロワー
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
                # **会話全体を取る**（設計 §5・2026-09-12 に実装の逸脱が発覚）。
                # `/replies` は**上位 1 階層だけ**なので、**返信への返信——
                # つまりうちの側の発言——が台帳に残らなかった。**
                replies = adapter.conversation(post_id)
            except Exception as e:
                errors.append(f"{post_id}: conversation: {redact_mod.redact(str(e))}")
                replies = None
            if replies is not None:
                新しい = _save_replies(reply_path, post_id, replies, now=now,
                                        age_hours=age_hours, marks=reply_marks,
                                        trigger="marks")
                touched.append(reply_path)
                if 新しい:
                    log(f"返信 {len(新しい)} 件: {post_id}")

    # --- 利用者から始まった会話（**WhatsApp の芽**・設計 v2 §4.2）
    touched.extend(_collect_inbox(account_cfg, adapter, now=now, errors=errors,
                                   log=log))

    # --- アカウント単位の日次（前日ぶん・`clicks` はここでしか取れない）
    account_path = _collect_account_daily(account_name, account_cfg, adapter,
                                           now=now, errors=errors)
    if account_path:
        touched.append(account_path)

    return {"touched": sorted(set(touched)), "posts": posts_seen, "errors": errors}


_MONTH_RE = re.compile(r"^\d{4}-\d{2}")


def _inbox_month(row: dict, *, now) -> str:
    """その行を書く月（`<YYYY-MM>.ndjson`）。

    **届いた時刻の月**に置く（採った月ではない）。`timestamp` が無い・読めない
    ときだけ「いま」の月に落とす（行そのものには `timestamp` が残るので、
    あとから「置き場は推測だった」と分かる）。
    """
    stamp = row.get("timestamp")
    if isinstance(stamp, str) and _MONTH_RE.match(stamp):
        return stamp[:7]
    return jst.month_str(now)


def _collect_inbox(account_cfg: dict, adapter, *, now, errors: list, log) -> list:
    """`inbox` を持つアダプタから、利用者が始めた会話を採って追記する。

    **芽である**（設計 v2 §4.2）。v2-3 では偽の push 型アダプタでこの配管だけを
    通し、WhatsApp の実装は §7-9 の後。ここでやることは 3 つだけ:

    1. `capabilities()` に `inbox` があるアダプタだけに聞く（無い媒体は呼ばない）。
    2. `data/sns/inbox/<YYYY-MM>.ndjson` に**追記**する。
    3. **`message_id` で重複を除く**（冪等——同じ実行を 2 度走らせても増えない）。

    **`reply_deadline` はそのまま行に残す**（24 時間の会話窓・設計 v2 §4.1）。
    承認の待ち時間に上限が要るので、**期限を落とすと門が使えなくなる。**
    """
    try:
        capabilities = adapter.capabilities()
    except Exception:
        capabilities = set()
    if "inbox" not in (capabilities or set()):
        return []

    repo_dir = account_cfg.get("repo_dir") or ""
    try:
        messages = adapter.inbox()
    except Exception as e:
        errors.append(f"inbox: {redact_mod.redact(str(e))}")
        return []
    if not isinstance(messages, list):
        # **形が違うものを件数として数えない**（`_rows()` と同じ流儀）。
        errors.append(f"inbox: 一覧が配列ではありません（{type(messages).__name__}）")
        return []

    touched, 欠落 = [], 0
    by_month: dict = {}
    for message in messages:
        row = (dataclasses.asdict(message)
               if dataclasses.is_dataclass(message) and not isinstance(message, type)
               else message)
        if not isinstance(row, dict):
            欠落 += 1
            continue
        if not row.get("message_id"):
            # **id の無い行を、黙って成功件数に含めない**（`_save_replies()` と同じ）。
            欠落 += 1
            continue
        by_month.setdefault(_inbox_month(row, now=now), []).append(row)

    if 欠落:
        errors.append(f"inbox: message_id の無い行が {欠落} 件ありました（書いていません）")

    for month, rows in sorted(by_month.items()):
        path = os.path.join(repo_dir, *INBOX_DIR, f"{month}.ndjson")
        known = {r.get("message_id") for r in _read_ndjson(path)}
        fresh = []
        for row in rows:
            mid = row.get("message_id")
            if mid in known:
                continue
            known.add(mid)
            # **管理項目は後に置く**——媒体の行に同名の値があっても上書きさせない。
            fresh.append({**row, "kind": "inbox", "collected_at": jst.iso(now)})
        if not fresh:
            continue
        _append_ndjson(path, fresh)
        touched.append(path)
        log(f"問い合わせ {len(fresh)} 件: {month}")
    return touched


def _collect_account_daily(account_name, account_cfg, adapter, *, now, errors) -> str | None:
    """**前日の閉じた 1 日**を 1 行だけ記録する（外部レビュー §4-a）。

    当日ぶんを取ると、そのあとに起きた反応が記録に入らない。閉じた日だけ取る。

    **持たない媒体では呼ばない**（capability `account_insights`・T3・2026-09-13）。
    以前はここが媒体を問わず `adapter.account_insights()` を呼んでいたので
    （T0 の残件）、Bluesky・Mastodon では毎回 `AttributeError` を捕まえて
    `errors` に `account_insights: …` を積み、**採取が「1 本でも失敗したか」で
    非ゼロ終了し続けた**。「そもそも媒体に無い」を「失敗」と呼ばない
    ——`insights` の `available` で views を扱ったのと同じ筋（設計 v2 §4.2）。
    """
    try:
        capabilities = adapter.capabilities()
    except Exception:   # noqa: BLE001 — 能力を答えられない実装は「持たない」扱い
        capabilities = set()
    if "account_insights" not in (capabilities or set()):
        return None

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
        # **採れなかった理由を、どこにも出さずに捨てていた**（運用セッション指摘
        # 2026-09-12）。`errors` を集めて終了コードにはしていたが、**中身を
        # log に出していなかった。** そのため「返信の 1h と 6h がなぜ失敗したか」が
        # 後から追えなくなった（journal にも残っていない）。
        # **失敗の理由は、失敗した回にしか書けない。**
        for problem in result.get("errors") or []:
            log(f"採れなかったもの: {problem}")
    finally:
        repo_lock.release()

    for err in result["errors"]:
        log("採取の失敗: " + err)
    return 1 if result["errors"] else 0

# --- 臨時の取り直し（masaru 指示 2026-09-12・外部レビュー B）------------------

def _refresh_targets(account_name: str, account_cfg: dict, *, now, errors: list,
                      post_id: str | None) -> tuple:
    """取り直す対象を選ぶ。**定期取得と同じ選び方**（別の母集団を作らない）。

    戻り値は `(対象, 断り)`。**`--post` が対象外なら理由を返し、別 account や
    全投稿へ落とさない。**
    """
    repo_dir = account_cfg.get("repo_dir") or ""
    collect_days = int(account_cfg.get("collect_days", 14) or 14)
    files = core.list_queue_files(account_cfg,
                                   tree_sha=writeback.upstream_sha(repo_dir))
    対象 = []
    for qf in _with_bundle_posts(files, account_name, account_cfg, errors=errors):
        if qf.malformed:
            continue
        fm = qf.front_matter
        if fm.get("account") != account_name:
            continue
        pid, posted_at_raw = fm.get("post_id"), fm.get("posted_at")
        if not pid or not posted_at_raw:
            continue
        # **`post_id` をそのままパスにしない**（独立検収 B・2026-09-12）。
        # `../../../脱出` のような値で、**取った会話が repo の外に落ちていた**
        # ——版管理からも `thth replies` の読み口からも消えるのに、
        # **表示は「取れた 1 本」で成功に見えた。**
        if not _safe_post_id(pid):
            errors.append(f"post_id にパス区切りが入っています: {pid!r}")
            continue
        try:
            posted_at = datetime.datetime.fromisoformat(posted_at_raw)
            # **時間帯の無い `posted_at` で全体を止めない**（独立検収 B・
            # 2026-09-12）。`fromisoformat` は通るのに、引き算で
            # `TypeError: can't subtract offset-naive and offset-aware` が
            # **外まで抜けて、他の正常な投稿も一切取り直せなかった。**
            # **1 本読めないことを、全部読めないことにしない。**
            age = (now - posted_at).total_seconds() / 3600.0
        except (TypeError, ValueError):
            errors.append(f"{pid}: posted_at を読めません（{posted_at_raw!r}）")
            continue
        if age < 0 or age > collect_days * 24:
            continue
        対象.append((pid, age))
    if post_id is None:
        return (対象, None)
    見つけた = [t for t in 対象 if t[0] == post_id]
    if 見つけた:
        return (見つけた, None)
    return ([], f"{post_id} は、この account の収集対象ではありません"
                 f"（別 account・対象期間外・未公開・未知のいずれか）。"
                 f"**別の投稿では代用しません**")


def refresh_replies(account_name: str, *, adapter=None, now=None, log=print,
                     post_id: str | None = None) -> dict:
    """**刻みを待たずに会話を取り直す**（`thth replies <account> --refresh`）。

    **定期取得と何が違うか**——対象の選び方・保存・重複除去は**同じものを使う**。
    違うのは 3 つだけ。

    1. **刻みを見ない**（`due_marks` を通さない）。いつでも取りに行く。
    2. **刻みを進めない**（`marks: []` で記録する）。臨時の取得を「24h の数」に
       化けさせない。
    3. **`insights` を取らない。** 投稿・返信の送信も、承認も、トークン更新もしない。

    **API 成功・保存成功・push 成功を分けて返す**（外部レビュー B）。
    """
    from . import lock as lock_mod

    now = now if now is not None else jst.now_jst()
    out = {"account": account_name, "requested": 0, "fetched": 0, "new_replies": 0,
            "failed": [], "skipped": None, "saved": False,
            "remote": "unknown", "checked_at": jst.iso(now), "errors": []}
    try:
        account_cfg = accounts_mod.load_account(account_name)
    except accounts_mod.AccountError as e:
        out["errors"].append(str(e))
        out["skipped"] = "account_error"
        return out
    repo_dir = account_cfg.get("repo_dir")
    if not repo_dir or not os.path.isdir(repo_dir):
        out["skipped"] = "no_repo"
        return out

    if adapter is None:
        token = accounts_mod.load_token(account_cfg)
        if token is None:
            out["skipped"] = "no_token"
            out["errors"].append(f"token が無いので取り直せません: {account_name}")
            return out
        adapter = core._default_adapter_factory(account_cfg, token)

    repo_lock = lock_mod.AccountLock(accounts_mod.repo_lock_path_for(repo_dir))
    try:
        repo_lock.acquire()
    except lock_mod.LockBusy:
        # **待たない。** 投稿を塞ぐより見送る（定期取得と同じ扱い）。
        out["skipped"] = "locked"
        log(f"repo を別の実行が使っているので取り直しを見送ります: {repo_dir}")
        return out

    replies_dir = os.path.join(repo_dir,
                               account_cfg.get("replies_dir") or "data/sns/replies")
    touched = []
    try:
        synced, sync_err, _sha = writeback.sync_repo(repo_dir)
        if not synced:
            out["skipped"] = "not_synced"
            out["errors"].append(f"repo を同期できないので取り直しません: {sync_err}")
            return out

        対象, 断り = _refresh_targets(account_name, account_cfg, now=now,
                                      errors=out["errors"], post_id=post_id)
        if 断り:
            out["skipped"] = "out_of_scope"
            out["errors"].append(断り)
            return out
        out["requested"] = len(対象)
        if not 対象:
            # **「取るものが無い」と「送れなかった」を同じにしない**
            # （独立検収 B・2026-09-12）。
            out["remote"] = "nothing_to_send"

        for pid, age in 対象:
            reply_path = os.path.join(replies_dir, f"{pid}.ndjson")
            # **壊れた台帳には追記しない**（その対象の失敗にする）。
            _rows, broken = _read_ndjson_strict(reply_path)
            if broken:
                out["failed"].append({"post_id": pid, "reason": "台帳が壊れています"})
                continue
            try:
                replies = adapter.conversation(pid)
            except Exception as e:
                out["failed"].append({"post_id": pid,
                                       "reason": redact_mod.redact(str(e))})
                continue
            try:
                新しい = _save_replies(reply_path, pid, replies, now=now,
                                       age_hours=age, marks=[], trigger="refresh")
            except OSError as e:
                # **保存できないことを、その投稿の失敗にする**（独立検収 B・
                # 2026-09-12）。以前は `PermissionError` が外まで抜けて、
                # **2 本目以降が一切取れず、`failed` にも 1 件も入らなかった。**
                # API の失敗は 1 本で済むのに、保存の失敗だけ全体が落ちていた。
                out["failed"].append({"post_id": pid,
                                       "reason": f"保存できません: "
                                                  f"{redact_mod.redact(str(e))}"})
                continue
            touched.append(reply_path)
            out["fetched"] += 1
            out["new_replies"] += len(新しい)

        if touched:
            rel = [os.path.relpath(os.path.realpath(p), os.path.realpath(repo_dir))
                   for p in touched]
            out["saved"] = True
            pushed, push_err = writeback.commit_and_push(
                repo_dir, rel_path=rel,
                message=f"返信の取り直し: {len(rel)} ファイル（{account_name}）")
            if pushed:
                out["remote"] = "synced"
            else:
                out["remote"] = "not_synced"
                # **未 push の commit を一度も残さない**（masaru 裁定 2026-09-11・
                # 第 6/7 巡 P1。独立検収 B・2026-09-12 で**この入口だけ抜けて
                # いた**のが見つかった）。
                #
                # `commit_and_push` は add／commit／push の失敗を同じ False に
                # まとめるので、**「commit があるか」を自分で見てから**取り消す。
                # add／commit が失敗していれば HEAD は動いていないので、何もしない。
                #
                # **残すと `HEAD != @{u}` になり、`sync_repo()` がそれを拒否して
                # 投稿も採取も止まる。** 臨時の取り直しが 1 回失敗しただけで、
                # 以後 10 分ごとの timer が何もしなくなる。
                if _has_unpushed_commit(repo_dir):
                    undone = _undo_local_commit(repo_dir)
                    out["errors"].append(
                        f"保存はできましたが送れていません: {push_err}"
                        + ("（commit は取り消したので投稿は止まりません。"
                            "中身はファイルに残っています）"
                            if undone else
                            "。**commit を取り消せませんでした。"
                            "投稿が止まる可能性があります。**"))
                else:
                    out["errors"].append(
                        f"保存はできましたが送れていません: {push_err}"
                        f"（commit は作られていません）")
    finally:
        repo_lock.release()
    return out
