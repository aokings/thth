"""広場に置く手間を減らす（設計 3.8.0 §C）——`thth plaza post … --from …`。

材料の報告（r20260924-90aafc42・a51eaafa）: 置く側は、すでに手元にある出力（analytics-
report・after・study-report の数字、repo の文書、閉じた報告の返事）を人が写し直していた。
写し直すと数字が本文に入り、道具の観測と人の数字の区別が消える。

規律:

  (a) **observed は道具が付けた数字だけ**。`--from analytics-report|after` は、置く時点で
      道具がその計算を呼び直して数字（分母・期間・言えないこと）を `tool_numbers` に入れる。
      人や LLM の本文（`--body-file`）は読まない——本文の数字は従前どおり「本文」の欄。
      `--from study-report` は宣言を 3.4.0 と同じ観測の列にする（同じ計算）。
  (b) 本文には道具の下書き（数字の要約）を先頭に置き、人と LLM が解釈を下に足す。
  (c) **他人の返信・username を写さない**（project の範囲と同じ規律）。数字は集計だけ
      （投稿ごと・枝ごとの行は写さない）。
  (d) `--from-doc` は repo の中の md だけ（元の文書のパスと commit を持つ・本文は先頭
      4,000 字まで・秘密の検査は置く側の `plaza.post` と同じ）。commit と中身が
      違う（未 commit の変更がある）文書は置かない——「どの版の文書か」が言えないため。
  (e) `--from-report` は自分の project の閉じた報告の返事だけ（道具のコツとして写す）。
"""
from __future__ import annotations

import datetime
import os
import subprocess

from . import accounts, jst, private_store

FROM_TOOLS = ("analytics-report", "after", "study-report")
DOC_MAX = 4000
DOC_FILE_MAX = 1024 * 1024
# 報告の返事を写す上限（本文 8,000 字に人の解釈を足す余地を残す）。
REPORT_REPLY_MAX = 6000
CANNOT_SAY_MAX = 20
CANNOT_SAY_CHARS = 200
DRAFT_HEAD = "［道具の下書き・数字は観測の欄が正（道具が付けた）。解釈は下に人か LLM が足す］"


def _error(reason):
    from . import plaza
    return plaza.PlazaError(reason)


# ------------------------------------------------------------ 道具の数字

def _stat(value):
    value = value or {}
    return {"median": value.get("median"), "n": value.get("n")}


def _node_numbers(node):
    """after の 1 account の節から、集計の数字だけ（投稿ごと・枝ごとの行は写さない）。"""
    posts = node.get("posts") or {}
    eng = node.get("engagements") or {}
    return {"posts": {"n": posts.get("n"), "views_24h": _stat(posts.get("views_24h"))},
            "engagements": {"n": eng.get("n"), "reacted": eng.get("reacted"),
                            "likes_24h": _stat(eng.get("likes_24h")),
                            "replies_back_24h": _stat(eng.get("replies_back_24h")),
                            "views_24h": _stat(eng.get("views_24h"))}}


def _cannot_say(lines):
    out = []
    for line in lines or []:
        if isinstance(line, str) and line.strip():
            out.append(private_store.fold_paths(line.replace("\n", " ").strip())[:CANNOT_SAY_CHARS])
    return out[:CANNOT_SAY_MAX]


def _observed(numbers):
    """道具の数字が 1 つでも取れていれば True（中央値か、0 でない件数）。"""
    posts, eng = numbers["posts"], numbers["engagements"]
    medians = [posts["views_24h"]["median"]] + [eng[key]["median"] for key in
                                                ("likes_24h", "replies_back_24h", "views_24h")]
    return any(value is not None for value in medians) or bool(posts["n"]) or bool(eng["n"])


def tool_numbers(kind, target, *, account, project, now, window_days=None, trusted_accounts=None):
    """`--from analytics-report|after <account>`: 置く時点で道具が計算を呼び直した数字。

    `target` は置く account と同じ持ち主（同じ project）の account だけ。
    """
    from . import after_cli, analytics_report, plaza
    if kind not in ("analytics-report", "after") or not accounts.name_is_safe(target):
        raise _error("invalid_from")
    owners = plaza._owner_accounts(account, project, trusted_accounts)
    if target not in owners:
        raise _error("from_out_of_scope")
    default = (analytics_report.DEFAULT_WINDOW_DAYS if kind == "analytics-report"
               else after_cli.DEFAULT_WINDOW_DAYS)
    window = default if window_days is None else window_days
    if type(window) is not int or not 1 <= window <= 365:
        raise _error("invalid_from")
    allowed = tuple(trusted_accounts) if trusted_accounts is not None else None
    try:
        if kind == "analytics-report":
            payload = analytics_report.answer(target, window_days=window, now=now,
                                              allowed_names=allowed)
            node = payload["by_account"][target]
            period = {key: payload["period"].get(key) for key in ("start", "end", "window_days",
                                                                  "basis", "timezone")}
            cannot = list(node.get("cannot_say") or []) + list(payload.get("cannot_say") or [])
        else:
            node = after_cli.answer(target, window_days=window, now=now, allowed_names=allowed)
            start = jst.to_jst(now) - datetime.timedelta(days=window)
            period = {"start": jst.iso(start), "end": jst.iso(now), "window_days": window,
                      "basis": "posted_at", "timezone": "Asia/Tokyo"}
            cannot = list(node.get("cannot_say") or [])
    except (after_cli.AfterError, accounts.AccountError, OSError, ValueError, TypeError, KeyError):
        raise _error("from_unavailable") from None
    numbers = _node_numbers(node)
    command = f"thth {kind} {target} --window-days {window}"
    return {"source": kind, "target": target, "medium": owners[target], "command": command,
            "at": jst.iso(now), "period": period, "numbers": numbers,
            "cannot_say": _cannot_say(cannot), "observed": _observed(numbers)}


def _fmt(stat):
    if stat["median"] is None:
        return f"言えない（n={stat['n']}）"
    return f"{stat['median']}（n={stat['n']}）"


def numbers_draft(entry):
    """`tool_numbers` の下書き（本文の先頭に置く・数字は観測の欄と同じもの）。"""
    numbers, period = entry["numbers"], entry["period"]
    posts, eng = numbers["posts"], numbers["engagements"]
    lines = [DRAFT_HEAD,
             f"出し直し: {entry['command']}（{entry['at']}）",
             f"期間: 直近 {period.get('window_days')} 日（{period.get('start')} 〜 {period.get('end')}・"
             f"{period.get('basis')}）",
             f"自分の投稿 {posts['n']} 本・24h views 中央値 {_fmt(posts['views_24h'])}",
             f"絡みに行った返信 {eng['n']} 本・反応あり {eng['reacted']}・likes 中央値 "
             f"{_fmt(eng['likes_24h'])}・返ってきた返信 中央値 {_fmt(eng['replies_back_24h'])}"]
    if entry["cannot_say"]:
        lines.append("言えないこと: " + "／".join(entry["cannot_say"]))
    return "\n".join(lines)


def study_draft(observation, command):
    """`--from study-report` の下書き（3.4.0 の観測の列から・同じ数字）。"""
    lines = [DRAFT_HEAD, f"出し直し: {command}（{observation['at']}）"]
    for column in observation.get("columns") or []:
        if not column.get("observed"):
            lines.append(f"{column.get('medium') or '—'}: 取れない（{column.get('reason')}）")
            continue
        base = column["denominators"]["baseline"]
        changed = column["denominators"]["changed"]
        diffs = []
        for metric, value in (column.get("metrics") or {}).items():
            diff = value.get("observational_difference")
            diffs.append(f"{metric} " + ("—（" + str(value.get("reason")) + "）" if diff is None
                                         else f"{'+' if diff > 0 else ''}{diff}"))
        lines.append(f"{column.get('medium') or '—'}: 採用 {column.get('decision_at')}・"
                     f"前 {base['eligible']}/{base['requested']} 件・後 {changed['eligible']}/"
                     f"{changed['requested']} 件（時間適合/指定）・" + "・".join(diffs))
        if column.get("cannot_say"):
            lines.append("言えないこと: " + "／".join(_cannot_say(column["cannot_say"])))
    return "\n".join(lines)


def human_part(body):
    """本文のうち人や LLM が書いた部分（道具の下書きを除く）。「本文の数字」はここから数える。"""
    if not isinstance(body, str) or not body.startswith(DRAFT_HEAD):
        return body
    parts = body.split("\n\n", 1)
    return parts[1] if len(parts) == 2 else ""


def compose(draft, human):
    """道具の下書き＋人や LLM の本文（無ければ下書きだけ）。"""
    human = human.strip() if isinstance(human, str) else ""
    return draft + ("\n\n" + human if human else "")


# ------------------------------------------------------------ 文書

def _git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                          timeout=30, check=False)


def from_doc(path, *, account):
    """`--from-doc <repo の中の md>`: 題・本文（先頭 4,000 字）・元の文書のパスと commit。"""
    if not isinstance(path, str) or not path.strip():
        raise _error("invalid_from_doc")
    try:
        cfg = accounts.load_account(account)
    except accounts.AccountError:
        raise _error("account_unavailable") from None
    repo = cfg.get("repo_dir")
    if not isinstance(repo, str) or not os.path.isdir(repo):
        raise _error("from_doc_outside_repo")
    root = os.path.realpath(repo)
    candidate = path if os.path.isabs(path) else os.path.join(root, path)
    real = os.path.realpath(candidate)
    if not real.startswith(root + os.sep):
        raise _error("from_doc_outside_repo")
    rel = os.path.relpath(real, root)
    if not rel.endswith(".md") or ".git" in rel.split(os.sep):
        raise _error("invalid_from_doc")
    try:
        if not os.path.isfile(real) or os.path.islink(candidate):
            raise _error("invalid_from_doc")
        with open(real, "rb") as stream:
            data = stream.read(DOC_FILE_MAX + 1)
    except OSError:
        raise _error("invalid_from_doc") from None
    if len(data) > DOC_FILE_MAX:
        raise _error("invalid_from_doc")
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        raise _error("invalid_from_doc") from None
    try:
        tracked = _git(root, "ls-files", "--error-unmatch", "--", rel)
        clean = _git(root, "diff", "--quiet", "HEAD", "--", rel)
        log = _git(root, "log", "-1", "--format=%H", "--", rel)
    except (OSError, subprocess.SubprocessError):
        raise _error("from_doc_not_committed") from None
    commit = log.stdout.strip()
    if tracked.returncode != 0 or clean.returncode != 0 or len(commit) < 7:
        raise _error("from_doc_not_committed")
    body_text = text.strip()
    truncated = len(body_text) > DOC_MAX
    title = None
    for line in body_text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    title = (title or os.path.splitext(os.path.basename(rel))[0])[:120]
    head = f"［元の文書: {rel}@{commit[:7]}" + ("・先頭 4,000 字" if truncated else "") + "］"
    return {"title": title, "body": head + "\n" + body_text[:DOC_MAX],
            "source": {"kind": "doc", "path": rel, "commit": commit, "truncated": truncated,
                       "chars": len(body_text)}}


# ------------------------------------------------------------ 閉じた報告

def from_report(report_id, *, account, project):
    """`--from-report <report_id>`: 自分の project の閉じた報告の返事を道具のコツとして写す。

    範囲の外の報告と無い報告は同じ `from_report_not_found`（在ることを漏らさない）。
    写すのは実装側の返事だけ（報告した側の追記は写さない）。
    """
    from . import report_inbox
    scope = report_inbox.Scope([] if project else [account], [project] if project else [])
    try:
        record = report_inbox.show(report_id, scope=scope)
    except report_inbox.ReportError:
        raise _error("from_report_not_found") from None
    if record.get("status") != "closed":
        raise _error("from_report_open")
    replies = [row["text"] for row in record.get("replies") or []
               if row.get("role") != "reporter" and isinstance(row.get("text"), str)]
    if not replies:
        raise _error("from_report_no_reply")
    closed = record.get("closed") or {}
    head = (f"［報告 {record['report_id']} の返事（閉じた版 {closed.get('version')}・"
            f"{closed.get('reason')}）］")
    text = "\n\n".join(replies)
    if len(text) > REPORT_REPLY_MAX:
        text = text[:REPORT_REPLY_MAX] + "（以下略）"
    return {"title": record["title"][:120], "body": head + "\n" + text,
            "source": {"kind": "report", "report_id": record["report_id"],
                       "closed_version": closed.get("version"),
                       "closed_reason": closed.get("reason")}}
