"""問いの記録（計画 段 3）。持ち主の台帳のフォルダの `問いの記録.jsonl` に 1 行ずつ足す（運営者は預からない）。

行の形（足すだけ・書き換えない）:

- 聞いた: `{"type": "asked", "id": "q-…", "hole": 穴の番号, "row": 行 or null, "at": 時刻, "by": 名前,
  "digest": 聞いたときの行の中身の指紋（行のある穴だけ）}`。答えの yes で認めるのは、今の中身の指紋がこれと同じときだけ
  （聞いたあとに書き足された中身を、見せないまま認めない・監査 5.19.0 直したほうがよい 2）。
- 答えた: `{"type": "answered", "id": 問いの番号, "result": yes|no|other|skip, "at", "by",
  "asked": 聞いた言葉 or null, "said": 答えの要約 or null, "new_row": 答えから足した行 or null}`

問いの記録は .md でないので、台帳の行としては読まない（`form.scan`）。聞いた言葉と答えの要約は 1 行 200 字まで・
秘密らしい値は断る（台帳は git の repo なので）。
"""
from __future__ import annotations

import datetime
import json
import os
import re
import secrets
import stat

from .. import jst, redact
from .errors import RegisterRefused

LOG_FILE = "問いの記録.jsonl"
RESULTS = ("yes", "no", "other", "skip")
TEXT_MAX = 200
LOG_MAX_BYTES = 2 * 1024 * 1024
_QUESTION = re.compile(r"q-[0-9]{8}-[0-9a-f]{6}\Z")


def path_of(root: str) -> str:
    return os.path.join(root, LOG_FILE)


def read(root: str) -> list:
    path = path_of(root)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return []
    if not stat.S_ISREG(info.st_mode) or info.st_size > LOG_MAX_BYTES:
        raise RegisterRefused("register_question_log_invalid")
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    value = json.loads(line)
                    if not _row_ok(value):
                        raise ValueError
                    out.append(value)
    except (OSError, ValueError, UnicodeDecodeError):
        raise RegisterRefused("register_question_log_invalid") from None
    return out


def _row_ok(value) -> bool:
    """問いの記録の 1 行の形（番号は決まった形の字・聞いた行は穴の番号を持つ）。壊れた行は記録ごと断る
    （`id` の無い行で `KeyError` にしない・監査 5.19.0 の気づき 5 の b）。"""
    if not isinstance(value, dict) or value.get("type") not in ("asked", "answered"):
        return False
    if not isinstance(value.get("id"), str) or not _QUESTION.fullmatch(value["id"]):
        return False
    if value["type"] == "asked":
        return isinstance(value.get("hole"), str) and (value.get("row") is None or isinstance(value["row"], str))
    return value.get("result") in RESULTS


def append(root: str, rows: list) -> None:
    path = path_of(root)
    if os.path.islink(path):
        raise RegisterRefused("register_question_log_invalid")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        raise RegisterRefused("register_row_unwritable") from None


def state(log: list) -> dict:
    """問いの番号 → `{"asked": 行, "answered": 行 or None}`。"""
    out = {}
    for row in log:
        if row["type"] == "asked":
            out[row["id"]] = {"asked": row, "answered": None}
        elif row["id"] in out and out[row["id"]]["answered"] is None:
            out[row["id"]]["answered"] = row
    return out


def _day(at) -> datetime.date | None:
    parsed = jst.parse(at)
    return jst.to_jst(parsed).date() if parsed else None


def asked_today(log: list, today: datetime.date) -> int:
    return sum(1 for row in log if row["type"] == "asked" and _day(row.get("at")) == today)


def last_asked(log: list, hole: str) -> datetime.date | None:
    days = [_day(row.get("at")) for row in log if row["type"] == "asked" and row.get("hole") == hole]
    days = [d for d in days if d]
    return max(days) if days else None


def pick(holes: list, log: list, today: datetime.date, *, per_day: int, gap_days: int) -> list:
    """今聞く穴（1 日の上限の残りまで・同じ穴は `gap_days` 日空ける・違うと言われた穴は聞かない）。"""
    room = max(0, per_day - asked_today(log, today))
    denied = {s["asked"].get("hole") for s in state(log).values()
              if s["answered"] and s["answered"].get("result") == "no"}
    out = []
    for hole in holes:
        if len(out) >= room:
            break
        if hole["hole"] in denied:
            continue
        last = last_asked(log, hole["hole"])
        if last is not None and (today - last).days < gap_days:
            continue
        out.append(hole)
    return out


def new_id(now: datetime.datetime) -> str:
    return f"q-{jst.to_jst(now):%Y%m%d}-{secrets.token_hex(3)}"


def check_id(value) -> str:
    if not isinstance(value, str) or not _QUESTION.fullmatch(value):
        raise RegisterRefused("register_args_invalid", "question")
    return value


def check_text(value) -> str | None:
    if value is None:
        return None
    if (not isinstance(value, str) or not value.strip() or len(value) > TEXT_MAX
            or any(ord(c) < 32 for c in value) or redact.looks_like_secret(value)):
        raise RegisterRefused("register_args_invalid", "answer_text")
    return value.strip()


def counts(log: list) -> dict:
    """問いの数・答えの数・答えから増えた行の数（計画 段 3 の確かめ方・§2 の条件 5）。"""
    rows = state(log).values()
    answered = [s["answered"] for s in rows if s["answered"]]
    return {"asked": len(rows), "answered": sum(1 for a in answered if a.get("result") != "skip"),
            "skipped": sum(1 for a in answered if a.get("result") == "skip"),
            "rows_from_answers": sum(1 for a in answered if a.get("new_row")),
            "open": sum(1 for s in rows if s["answered"] is None)}
