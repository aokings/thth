"""スレッド連投の実行記録（設計 §2・§3・§4・工程 3〜5）。

**Git で同期済みであることと、THTH が実際に公開した記録であることは別**
（Codex 最終条件 1）。原稿は利用者 repo にあって人が書き換えられる。
公開記録は THTH が自分で書いた事実。**両方が揃って初めて親が決まる。**

`run_id` は**最初の公開要求より前に発行して永続化する。** 1 段目の `post_id` を
実行 ID にする案は採らない——**1 段目の公開結果が不明になった場合、ID がまだ
無く、`inflight` も進行状態も作れない。** 結果不明はいちばん守りたい場面なので、
そこで使えない識別子は使えない。
"""
from __future__ import annotations

import json
import os
import re
import secrets

from . import accounts as accounts_mod
from . import jst

# 段の状態（設計 §3.1）。**「どこまで出たか」ではなく「何が確定しているか」。**
PENDING = "pending"        # 未着手
REQUESTED = "requested"    # 公開要求を送った。**結果は未確定**
PUBLISHED = "published"    # 公開を確認し、post_id を得た
UNRESOLVED = "unresolved"  # 結果が分からない。**自動で再送しない**
STOPPED = "stopped"        # 実行側が停止を確認した

_RUN_ID_RE = re.compile(r"^run-[0-9]{8}T[0-9]{6}-[0-9a-f]{8}$")


class RunError(Exception):
    """実行記録が読めない・食い違う。**推測で続けない。**"""


def runs_dir() -> str:
    return os.path.join(accounts_mod.thth_root(), "state", "threads")


def new_run_id(now=None) -> str:
    """**公開要求より前**に発行する実行 ID。"""
    now = now if now is not None else jst.now_jst()
    return f"run-{now.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(4)}"


def run_path(run_id: str) -> str:
    if not _RUN_ID_RE.match(run_id or ""):
        raise RunError(f"run_id の形が違います: {run_id!r}")
    return os.path.join(runs_dir(), f"{run_id}.json")


def _atomic_write(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load(run_id: str) -> dict | None:
    path = run_path(run_id)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        row = json.load(f)
    if row.get("run_id") != run_id:
        raise RunError(f"実行記録の run_id が食い違います: {run_id}")
    return row


def save(row: dict) -> dict:
    _atomic_write(run_path(row["run_id"]), row)
    return row


def start(*, account: str, rel_path: str, bundle_sha: str, segment_count: int,
           segment_shas: list, continue_until: str, by: str, now=None) -> dict:
    """実行を始める。**公開要求を送る前に呼ぶ。**"""
    now = now if now is not None else jst.now_jst()
    row = {
        "run_id": new_run_id(now),
        "account": account,
        "rel_path": rel_path,
        "bundle_sha": bundle_sha,
        "continue_until": continue_until,
        "started_at": now.isoformat(),
        "started_by": by,
        "root_post_id": None,
        "stop_confirmed_at": None,
        "stop_reason": None,
        "posts": [
            {"index": i, "state": PENDING, "post_id": None, "posted_at": None,
             "reply_to": None, "container_id": None,
             "text_sha256": segment_shas[i - 1] if i <= len(segment_shas) else None,
             "bundle_sha": bundle_sha, "last_ok": None, "note": None}
            for i in range(1, segment_count + 1)
        ],
    }
    return save(row)


def find_open(account: str, rel_path: str) -> dict | None:
    """その原稿の**進行中の実行**を返す（無ければ None）。

    **同じ束を 2 つの実行が進めない**ための照会。呼び出し側はこれを
    **ロックの中で**行う（state に書くだけでは排他にならない・Codex 最終条件 3）。
    """
    directory = runs_dir()
    if not os.path.isdir(directory):
        return None
    found = None
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json") or name.endswith(".tmp"):
            continue
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as f:
                row = json.load(f)
        except (OSError, ValueError):
            continue
        if row.get("account") != account or row.get("rel_path") != rel_path:
            continue
        if is_finished(row):
            continue
        if found is None or row.get("started_at", "") > found.get("started_at", ""):
            found = row
    return found


def find_latest(account: str, rel_path: str) -> dict | None:
    """その原稿の**いちばん新しい実行**（終わっていても返す）。

    **終わった実行を見ないと、同じ束をもう一度最初から出してしまう**
    （実装中に踏んだ: 3 段出し切ったあと `find_open` が None を返し、
    4 周目で新しい実行が始まって 1 段目を再公開した）。
    """
    directory = runs_dir()
    if not os.path.isdir(directory):
        return None
    found = None
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json") or name.endswith(".tmp"):
            continue
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as f:
                row = json.load(f)
        except (OSError, ValueError):
            continue
        if row.get("account") != account or row.get("rel_path") != rel_path:
            continue
        if found is None or row.get("started_at", "") > found.get("started_at", ""):
            found = row
    return found


def open_runs(account: str) -> list:
    """その account の**終わっていない実行**（未解決を含む）。"""
    directory = runs_dir()
    if not os.path.isdir(directory):
        return []
    out = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json") or name.endswith(".tmp"):
            continue
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as f:
                row = json.load(f)
        except (OSError, ValueError):
            continue
        if row.get("account") == account and not is_finished(row):
            out.append(row)
    return out


STATES = (PENDING, REQUESTED, PUBLISHED, UNRESOLVED, SKIPPED) \
    if "SKIPPED" in globals() else (PENDING, REQUESTED, PUBLISHED, UNRESOLVED)

# 実行記録として使うのに要る項目（`start()` が必ず書くもの）。
_RUN_KEYS = ("run_id", "account", "rel_path", "posts")


def run_problem(row) -> str | None:
    """実行記録として**解釈できるか**（再々判定 N1・2026-09-12 Codex）。

    **JSON として読めることは、実行記録として読めることではない。**
    `unreadable_runs()` は `json.load()` の成否しか見ていなかったので、
    **中身を `{}` に置き換えると「壊れていない」ことになった。** そのあと
    `find_latest()` が account 不一致で飛ばすため「**該当する実行が無い**」と
    同じ状態になり、**凍結済みの段を持たない新しい束として 1 段目が再公開できた。**

    棚（`topic_store`）の側は前から「読むたびに中身を照合する」を守っていたのに、
    **公開経路の実行記録だけ素通しだった。** 同じ基準にする。

    **解釈できないものを「該当 run なし」に落とさない**——呼び出し側は
    `unreadable_runs()` で止まる。
    """
    if not isinstance(row, dict):
        return f"object ではありません（{type(row).__name__}）"
    missing = [k for k in _RUN_KEYS if not row.get(k)]
    if missing:
        return f"項目がありません: {missing}"
    for key in ("run_id", "account", "rel_path"):
        if not isinstance(row[key], str):
            return f"{key} が文字列ではありません"
    posts = row["posts"]
    if not isinstance(posts, list) or not posts:
        return "posts が空です"
    for i, post in enumerate(posts, start=1):
        if not isinstance(post, dict):
            return f"posts[{i}] が object ではありません"
        if post.get("index") != i:
            return f"posts[{i}] の index が {post.get('index')!r} です（順番どおりに）"
        if post.get("state") not in STATES:
            return f"posts[{i}] の state が知らない値です（{post.get('state')!r}）"
    return None


def unreadable_runs() -> list:
    """**読めない実行記録**の絶対パス（監査 2026-09-11・F1 と同じ形）。

    `open_runs()` / `find_latest()` は読めないファイルを `continue` で飛ばす。
    **読めないことと、無いことは別**（設計 §8「破損と不存在は別状態」）なのに、
    飛ばした結果が「未解決なし」と同じ値になっていた——**壊れた記録 1 件で、
    「結果が確定していない公開がある間は進まない」という関門が開く。**

    **ディレクトリが無いのは「判らない」ではない**（運用セッション指摘
    2026-09-11）。実行記録が 1 件も無いという**確定した事実**なので、ここでは
    空を返す。曖昧なのは「ファイルはあるのに読めない」だけ。
    """
    directory = runs_dir()
    if not os.path.exists(directory):
        return []            # **無いことは確定した事実。** 止めない
    if not os.path.isdir(directory):
        # **「無い」と「あるが置き場として使えない」は別**（再々判定 N2）。
        # 通常ファイルが置かれていると `isdir` が偽になり、**記録が 1 件も
        # 無いのと同じ扱い**になっていた。承認は通り、公開は
        # `FileExistsError` で落ちた——**不正な状態で承認だけ成功していた。**
        return [os.path.abspath(directory)]
    out = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json") or name.endswith(".tmp"):
            continue
        path = os.path.join(directory, name)
        try:
            with open(path, encoding="utf-8") as f:
                row = json.load(f)
        except (OSError, ValueError):
            out.append(os.path.abspath(path))
            continue
        # **JSON として読めても、実行記録として解釈できなければ同じ扱い**（N1）。
        if run_problem(row):
            out.append(os.path.abspath(path))
    return out


def unreadable_error(paths: list) -> str:
    """止めるときに、**人が動ける形**で言う（運用セッション指摘 2026-09-11）。

    > fail-closed は運用を止める権限を道具に渡すので、止まったときに人が
    > 動ける形にしてから入れてほしい。

    絶対パス・**止まっている範囲**・戻し方を書く。
    """
    return (f"実行記録を読めません: {paths[:3]}"
            f"{f'（ほか {len(paths) - 3} 件）' if len(paths) > 3 else ''}。"
            f"**読めないことを「未解決の公開なし」とは読みません。**"
            f"壊れた記録がどの account のものかは中身が読めない以上わからないので、"
            f"**連投の公開と承認は全 account で止まっています**"
            f"（単発の投稿・承認は止まっていません）。"
            f"戻すには: Threads を実際に見て**その束が出ていないこと**を確かめてから、"
            f"上のファイルを `state/threads/` の外へ退避して、もう一度実行して"
            f"ください。**退避すると「その実行は無かったこと」になります**"
            f"——出ている束の記録を退避すると、1 段目から出し直します。")


def has_unresolved(account: str) -> list:
    """**未解決の公開結果**がある実行（設計 §3.3）。

    これがある間は、**同じ account の別投稿へ進まない**（Codex 最終条件 3）。
    """
    return [r for r in open_runs(account)
            if any(p["state"] in (REQUESTED, UNRESOLVED) for p in r["posts"])]


def is_complete(row: dict) -> bool:
    """**全段が出た。** これだけが「終わった」。"""
    return all(p["state"] == PUBLISHED for p in row.get("posts", []))


def is_stopped(row: dict) -> bool:
    """**止まっている。** 終わってはいない（独立検収 2026-09-11・P2-6）。

    以前は `stop_confirmed_at` があれば「完了」にしていた。そのため
    **期限を延ばして再承認しても「すでに実行済みです」で再開できなかった。**
    **完了と停止は別物。**
    """
    return bool(row.get("stop_confirmed_at")) and not is_complete(row)


def is_finished(row: dict) -> bool:
    """もう進まない（完了または停止）。"""
    return is_complete(row) or bool(row.get("stop_confirmed_at"))


def has_unresolved_post(row: dict) -> bool:
    return any(p["state"] in (REQUESTED, UNRESOLVED) for p in row.get("posts", []))


def resume(row: dict, *, bundle_sha: str, continue_until: str, by: str,
            now=None) -> dict:
    """停止した実行を、**新しい承認版で同じ `run_id` のまま再開する**（P2-6）。

    **未解決は解除しない。** 結果の分からない公開がある実行は、人が Threads を
    見て判断するまで動かさない——そこを自動で通すと二重投稿になる。
    """
    if not is_stopped(row):
        raise RunError("停止していない実行は再開できません")
    if has_unresolved_post(row):
        raise RunError("結果の分からない公開があるので再開できません"
                        "（人が Threads を見て判断してください）")
    now = now if now is not None else jst.now_jst()
    row.setdefault("resumes", []).append({
        "at": now.isoformat(), "by": by,
        "from_stop_reason": row.get("stop_reason"),
        "bundle_sha": bundle_sha, "continue_until": continue_until,
    })
    row["stop_confirmed_at"] = None
    row["stop_reason"] = None
    row["continue_until"] = continue_until
    return save(row)


def next_index(row: dict) -> int | None:
    """次に出す段。**未解決があれば None**（自動で進まない）。"""
    if row.get("stop_confirmed_at"):
        return None
    for post in row["posts"]:
        if post["state"] in (REQUESTED, UNRESOLVED):
            return None
        if post["state"] == PENDING:
            return post["index"]
    return None


def snapshot_pending(row: dict, *, bundle_sha: str, segment_shas: list,
                      labels: dict | None = None, now=None) -> dict:
    """**未公開の段だけ**、これから送る版を記録に固定する（独立検収 P1-5）。

    再承認で本文を直しても、記録には `start()` のときの古い指紋と古い承認版が
    残っていた。**実際に送った本文と永続記録が食い違う**——凍結の検査が
    「旧本文を正本」として比べるので、**正しく送った新本文を変更扱いにする。**

    **公開済みの段の過去の承認版は変更しない**（Codex 最終条件 2）。
    """
    now = now if now is not None else jst.now_jst()
    for post in row["posts"]:
        if post["state"] == PUBLISHED:
            continue                      # **過去は動かさない**
        i = post["index"]
        if i <= len(segment_shas):
            post["text_sha256"] = segment_shas[i - 1]
        post["bundle_sha"] = bundle_sha
        if labels is not None:
            post["labels"] = dict(labels, at=now.isoformat())
    return save(row)


def mark(row: dict, index: int, state: str, **fields) -> dict:
    post = row["posts"][index - 1]
    if post["index"] != index:
        raise RunError(f"段の並びが壊れています: {index}")
    post["state"] = state
    post.update(fields)
    if state == PUBLISHED and index == 1:
        row["root_post_id"] = post.get("post_id")
    return save(row)


def confirm_stop(row: dict, reason: str, now=None) -> dict:
    """**実行側が停止を確認した**（設計 §4.2）。

    「停止要求済み」（原稿の `revoked_at`）とは別。**こちらは公開側の記録。**
    """
    now = now if now is not None else jst.now_jst()
    row["stop_confirmed_at"] = now.isoformat()
    row["stop_reason"] = reason
    return save(row)


def identity_error(row: dict | None, draft_posts: list, *, rel_path: str,
                    account: str | None = None) -> str | None:
    """原稿が名乗る実行と、手元の記録が合っているか（独立検収 P1-4）。

    **実行の身元をパスだけで決めない。** 完成した束のファイルを `git mv` すると
    `rel_path` が変わり、**post_id を持った原稿が「新しい束」として扱われて
    1 段目を再公開していた。**

    原稿に `post_id` か `run_id` が書かれているなら、**その実行を照合できない
    限り新規扱いにしない。** コピー・移動・記録の欠損も同じ検査に掛かる。
    """
    claimed_ids = {(_unquote(p.get("run_id")) or "") for p in draft_posts
                   if p.get("run_id")}
    has_post = any(p.get("post_id") for p in draft_posts)
    if not claimed_ids and not has_post:
        return None                      # 何も名乗っていない＝新しい束

    if row is None:
        return (f"この原稿には公開済みの記録が書かれていますが、"
                f"手元にその実行がありません（{sorted(claimed_ids) or 'run_id 無し'}）。"
                f"**移動・複製された原稿を新しい束として出すことはしません。**"
                f"出し直すなら記録を消してから別の原稿にしてください")
    if claimed_ids and claimed_ids != {row["run_id"]}:
        return (f"原稿が名乗る実行と手元の記録が違います"
                f"（原稿: {sorted(claimed_ids)} / 記録: {row['run_id']}）")
    if row.get("rel_path") != rel_path:
        return (f"この実行は別の原稿のものです"
                f"（記録: {row.get('rel_path')} / いま: {rel_path}）")
    # **account も照合する**（監査 2026-09-11）。`run_id` と `rel_path` しか見て
    # いなかったので、**別 account の repo に同じ相対パスの束があり、その原稿に
    # 他所の `run_id` が紛れ込んでいると（複製・コピー由来）、他 account の実行
    # 記録に書き込みながら公開できた。** 1 段目は `resolve_parent()` の account
    # 照合（`index > 1` だけ）にも掛からない。
    if account is not None and row.get("account") != account:
        return (f"この実行は別の account のものです"
                f"（記録: {row.get('account')} / いま: {account}）。"
                f"**複製された原稿を、他所の実行の続きとして出すことはしません**")
    return None


def find_by_run_id(run_id: str) -> dict | None:
    try:
        return load(run_id)
    except RunError:
        return None


def resolve_parent(row: dict, index: int, *, draft_posts: list,
                    account: str) -> str:
    """`index` 段目の**返信先 `post_id`** を決める（設計 §2.2）。

    **全部一致しなければ出さない。** 条件は設計 §2.2 の 6 つ。
    ここで返すのは「THTH 自身が公開したと記録している投稿の ID」であって、
    **原稿に書かれている文字列ではない。**
    """
    if index <= 1:
        return ""
    if row.get("account") != account:
        raise RunError("実行記録の account が違います")

    parent = row["posts"][index - 2]
    for earlier in row["posts"][: index - 1]:
        if earlier["state"] != PUBLISHED or not earlier["post_id"]:
            raise RunError(
                f"{earlier['index']} 段目がまだ公開されていません"
                f"（state: {earlier['state']}）。先の段から順に出します")
        # **別の実行で出した投稿を拾わない。**
        draft = _draft_post(draft_posts, earlier["index"])
        if draft is None:
            raise RunError(f"原稿に {earlier['index']} 段目の記録がありません")
        if _unquote(draft.get("run_id")) != row["run_id"]:
            raise RunError(
                f"{earlier['index']} 段目の記録が別の実行のものです"
                f"（原稿: {_unquote(draft.get('run_id'))} / "
                f"いまの実行: {row['run_id']}）")
        # **原稿の記録と永続化した公開記録が一致すること**（§2.1.1）。
        if _unquote(draft.get("post_id")) != earlier["post_id"]:
            raise RunError(
                f"{earlier['index']} 段目の post_id が原稿と公開記録で"
                f"食い違います（原稿: {_unquote(draft.get('post_id'))} / "
                f"記録: {earlier['post_id']}）")
    return parent["post_id"]


def _draft_post(draft_posts: list, index: int) -> dict | None:
    for post in draft_posts:
        if str(post.get("index")) == str(index):
            return post
    return None


def _unquote(value):
    if isinstance(value, str) and len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def frozen_records(row: dict) -> list:
    """**公開済み部分の記録**（承認の対象に入る・設計 §5）。"""
    return [{"index": p["index"], "post_id": p["post_id"],
             "text_sha256": p["text_sha256"]}
            for p in row["posts"] if p["state"] == PUBLISHED]


def summary(row: dict) -> dict:
    """人に見せる形（`revoke` と board・設計 §4.2）。

    **「N 段目以降は未公開」と断定しない。** 確認できた段・要求中の段・
    未着手の段を**分けて**出す（Codex 最終条件 3）。
    """
    published, requested, pending, unresolved = [], [], [], []
    for post in row["posts"]:
        {PUBLISHED: published, REQUESTED: requested,
         PENDING: pending, UNRESOLVED: unresolved,
         STOPPED: pending}[post["state"]].append(post["index"])
    return {
        "run_id": row["run_id"], "account": row["account"],
        "rel_path": row["rel_path"], "root_post_id": row.get("root_post_id"),
        "published": published, "requested": requested,
        "unresolved": unresolved, "pending": pending,
        "stop_confirmed_at": row.get("stop_confirmed_at"),
        "continue_until": row.get("continue_until"),
    }


def stop_report(account: str, rel_path: str) -> dict | None:
    """`revoke` が人に見せる形（設計 §4.2・Codex 最終条件 3）。

    **「N 段目以降は未公開」と断定しない。**
    確認できた段・要求中の段・未着手の段を**分けて**出す。
    **「次の確認点は約 10 分後」とも書かない**——束は同じ起動の中で進むので
    固定ではない。
    """
    row = find_latest(account, rel_path)
    if row is None:
        return None
    return summary(row)


def format_stop_report(report: dict | None) -> str:
    if report is None:
        return ("この原稿はまだ実行されていません（公開された段はありません）。")
    lines = []
    lines.append(f"  公開を確認できた段: {_列(report['published'])}")
    if report["requested"]:
        lines.append(f"  要求中（結果が未確定）の段: {_列(report['requested'])}")
    if report["unresolved"]:
        lines.append(f"  結果が分からない段: {_列(report['unresolved'])}")
    lines.append(f"  未着手の段: {_列(report['pending'])}")
    if report["stop_confirmed_at"]:
        lines.append(f"  実行側が停止を確認済み: {report['stop_confirmed_at']}")
    return "\n".join(lines)


def _列(values: list) -> str:
    return "、".join(str(v) for v in values) if values else "なし"
