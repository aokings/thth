"""直前の断りを道具が覚えておく（設計 3.3.0 B2）。

報告した側の LLM の自己分析（報告 r20260923-2f5a1b2d）: 自分から報告しなかった
理由の 1 つは「その場で材料が無い」。断った直後に何が起きたかを道具が控えておけば、
`thth report file <account> --from-last-refusal` の 1 行で再現手順が付く。

残すのは **`{at, version, command, reason_code, account}` の 5 つだけ**:

- `command` は argparse が知っている命令の名前（`report file` 等）だけ。引数は入れない。
- `reason_code` は断りの 1 行目の先頭にある静的な符丁（`target_unknown` 等・英小文字と
  `_` の語）だけ。無ければ `exit_<rc>`。**引数の値と重なる語は符丁とみなさない**
  （account 名や題が 1 行目の先頭に来る断りでも値を残さない）。
- 本文・引数の値・パス・秘密は残さない。

置き場は `state/<account>/last_refusals.json`（0600）・account ごとに直近 `KEEP` 件。
報告の口そのもの（`report …`）の断りは残さない——`--by` を付け忘れた断りが、
報告しようとしていた元の断りを押し出さないため。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import uuid

from . import accounts, jst, redact
from . import __version__

FILE = "last_refusals.json"
KEEP = 5
KEYS = ("at", "version", "command", "reason_code", "account")
# 符丁: 英小文字で始まり `_` で区切った語（`target_unknown`・`report_not_found`）。
_CODE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+")
_LEADING = re.compile(r"\s*([a-z][a-z0-9]*(?:_[a-z0-9]+)+)(?=$|[\s:：（(、。,])")
# CLI は `report file`、MCP のサーバ型は `mcp thth_draft_put` の形（どちらも静的な名前）。
_COMMAND = re.compile(r"[a-z][a-z0-9_-]*(?: [a-z][a-z0-9_-]*){0,2}|unknown")
_EXIT = re.compile(r"exit_[0-9]{1,3}")
MAX_CODE = 64
# 残さない命令（上の docstring）。
SKIP_COMMANDS = ("report",)


# **二段確認の 1 段目は断りではない**（依頼 3.8.2 件 3・報告 r20260924-9f07f36b）。
# `thth approve` の 1 段目（本文と digest を見せるだけ）と広場の open の 1 段目は、
# 呼び出し側を壊さないよう rc 1 のまま終わる。rc だけを見て「断った」と数えると、
# 確定を打つたびに `report_channel: … "exit_1 で断られた"` が付き、直前の断りの控えが
# 1 段目で埋まって、本当の断りを押し出していた。1 段目を出し終えた口が
# `mark_first_stage()` を呼び、`cli.main()` はその回の rc 1 を断りに数えない。
# 1 回の `cli.main()` の中だけで効く（入口で `clear_first_stage()`）。
_FIRST_STAGE = {"shown": False}


def mark_first_stage() -> None:
    """この回の rc 1 は二段確認の 1 段目（断りではない）と印を付ける。"""
    _FIRST_STAGE["shown"] = True


def clear_first_stage() -> None:
    _FIRST_STAGE["shown"] = False


def is_first_stage() -> bool:
    return _FIRST_STAGE["shown"]


def command_path(parser, argv) -> str:
    """argv の先頭から、argparse の子命令として知っている語だけをつないだ名前。"""
    path, current = [], parser
    for token in argv or []:
        sub = next((action for action in getattr(current, "_actions", [])
                    if isinstance(action, argparse._SubParsersAction)), None)
        if sub is None or token not in sub.choices:
            break
        path.append(token)
        current = sub.choices[token]
        if len(path) == 3:
            break
    return " ".join(path) or "unknown"


def reason_code(first_line, *, argv=(), command="", rc=2) -> str:
    """断りの 1 行目の先頭の符丁。値と重なる・秘密らしい・無いときは `exit_<rc>`。"""
    fallback = f"exit_{int(rc) if isinstance(rc, int) and 0 <= rc < 1000 else 2}"
    match = _LEADING.match(first_line or "")
    if match is None:
        return fallback
    code = match.group(1)
    if len(code) > MAX_CODE or redact.looks_like_secret(code):
        return fallback
    words = set(command.split())
    for value in argv or ():
        if not isinstance(value, str) or value in words or value.startswith("-"):
            continue
        value = value.strip()
        if value and (value in code or (len(value) >= 3 and code in value)):
            return fallback
    return code


def _valid(row, account) -> bool:
    return (isinstance(row, dict) and set(row) == set(KEYS)
            and row["account"] == account
            and isinstance(row["at"], str) and jst.parse(row["at"]) is not None
            and isinstance(row["version"], str) and len(row["version"]) <= 32
            and isinstance(row["command"], str) and _COMMAND.fullmatch(row["command"])
            and isinstance(row["reason_code"], str)
            and (_CODE.fullmatch(row["reason_code"]) or _EXIT.fullmatch(row["reason_code"]))
            and len(row["reason_code"]) <= MAX_CODE)


def _path(account):
    return os.path.join(accounts.state_dir_for(account), FILE)


def read(account) -> list:
    """その account の直前の断り（古い順）。**他の account の行は返さない**。"""
    if not accounts.name_is_safe(account):
        return []
    try:
        with open(_path(account), encoding="utf-8") as stream:
            rows = json.load(stream)
    except (OSError, ValueError, TypeError):
        return []
    if not isinstance(rows, list):
        return []
    return [row for row in rows if _valid(row, account)][-KEEP:]


def latest(account):
    rows = read(account)
    return rows[-1] if rows else None


def record(account, *, command, reason_code, now=None) -> bool:
    """1 件足す（直近 `KEEP` 件だけ残す）。台帳の無い account には書かない。"""
    if not isinstance(account, str) or not accounts.name_is_safe(account):
        return False
    if command.split(" ", 1)[0] in SKIP_COMMANDS:
        return False
    try:
        if account not in accounts.list_account_names():
            return False
        # 退出して止めた account・置き場の権限が危ない（775 等）ときは書かない
        # （止まった状態を観測するだけの口に副作用を足さない・`stop_observation`）。
        # 台帳が読めない account にも書かない。
        from . import stop_observation
        if stop_observation.diagnostic(account)["error"]:
            return False
        accounts.load_account(account)
    except (accounts.AccountError, OSError, ValueError, TypeError):
        return False
    row = {"at": jst.iso(now or jst.now_jst()), "version": __version__,
           "command": command, "reason_code": reason_code, "account": account}
    if not _valid(row, account):
        return False
    rows = (read(account) + [row])[-KEEP:]
    directory = accounts.state_dir_for(account)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    target = _path(account)
    temp = f"{target}.{uuid.uuid4().hex}.tmp"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(rows, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temp, target)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    os.chmod(target, 0o600)
    return True


# `--from-last-refusal` で本文を書かなかったときの本文（静的）。
DEFAULT_BODY = "直前に道具に断られました。道具が控えた断りを再現手順として添えます。"


def repro_text(row) -> str:
    """直前の断りを再現手順の文にする（5 つの値だけ）。"""
    command = row["command"]
    shown = ("MCP " + command[4:]) if command.startswith("mcp ") else "thth " + command
    return ("直前の断り（道具の記録・本文と引数の値は残していません）\n"
            f"at: {row['at']}\nversion: {row['version']}\ncommand: {shown}\n"
            f"reason_code: {row['reason_code']}\naccount: {row['account']}")
