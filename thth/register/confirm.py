"""認める操作と違うと言った操作（計画 段 1・§8 の 6）。

- 認める: 頭の `confirmed` を「本人が認めた」に、`confirmed_at` を今日にして、THTH の側の記録に
  `{行の番号: {"digest": 中身の指紋, "at", "by"}}` を書く。頭を先に書き、記録をあとに書く（記録が書けなければ
  頭は認めたでも柵は「記録が無い」で止める側に倒れる）。
- 違うと言った: 頭の `confirmed` を「本人が違うと言った」にして、記録を外す。行は消さない（同じ推測を
  二度聞かないため・構想 §9 の 2）。
- 二段で認める種類（`kinds.two_stage`・営業の時間と臨時）は、1 段目で行の全文と digest を見せ、
  `--confirm <digest>` で確定する。digest は台帳の名前・行の番号・中身の指紋から作るので、見せたあとに
  中身が変われば 2 段目は通らない。

**THTH が台帳に書くのは頭の `MANAGED` の欄だけ**（本文と、ほかの欄の行には触らない）。
"""
from __future__ import annotations

import hashlib
import os
import stat
import tempfile

from .. import jst
from . import form, ledger
from .errors import RegisterRefused


def digest_for(name: str, row: form.Row) -> str:
    raw = f"{name}\n{row.id}\n{form.fingerprint(row.text)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def with_head(text: str, updates: dict) -> str:
    """頭の `updates` の欄だけを置き換える（無い欄は頭の終わりに足す）。ほかの行は 1 字も変えない。"""
    if any(key not in form.MANAGED for key in updates):
        raise ValueError("register_head_field_not_managed")
    parts = form.split(text)
    if parts is None or not parts[2]:
        raise RegisterRefused("register_row_invalid", "head_unclosed")
    lines = text.split("\n")
    end = 1 + len(parts[0])
    left = dict(updates)
    for index in range(1, end):
        key = lines[index].partition(":")[0].strip()
        if key in left and not lines[index][:1].isspace():
            lines[index] = f"{key}: {left.pop(key)}"
    added = [f"{key}: {value}" for key, value in left.items()]
    return "\n".join(lines[:end] + added + lines[end:])


def write_row(root: str, row: form.Row, text: str) -> None:
    """行のファイルを原子的に書き換える（今の mode を保つ・symlink は書かない）。"""
    path = os.path.join(root, row.path)
    try:
        info = os.lstat(path)
    except OSError:
        raise RegisterRefused("register_row_unwritable") from None
    if not stat.S_ISREG(info.st_mode):
        raise RegisterRefused("register_row_unwritable")
    directory = os.path.dirname(path)
    try:
        fd, temp = tempfile.mkstemp(dir=directory, prefix=".thth-register-", suffix=".tmp")
    except OSError:
        raise RegisterRefused("register_row_unwritable") from None
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.chmod(temp, stat.S_IMODE(info.st_mode))
        os.replace(temp, path)
    except OSError:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise RegisterRefused("register_row_unwritable") from None


def confirm(name: str, root: str, row: form.Row, *, by: str) -> dict:
    today = jst.today_str()
    text = with_head(row.text, {"confirmed": form.YES, "confirmed_at": today})
    write_row(root, row, text)
    records = ledger.read_confirmed(name)
    records[row.id] = {"digest": form.fingerprint(text), "at": jst.iso(), "by": by}
    ledger.write_confirmed(name, records)
    return {"row": row.id, "confirmed": form.YES, "confirmed_at": today, "digest": records[row.id]["digest"]}


def deny(name: str, root: str, row: form.Row, *, by: str) -> dict:
    today = jst.today_str()
    text = with_head(row.text, {"confirmed": form.NO, "confirmed_at": today})
    write_row(root, row, text)
    records = ledger.read_confirmed(name)
    if records.pop(row.id, None) is not None:
        ledger.write_confirmed(name, records)
    return {"row": row.id, "confirmed": form.NO, "confirmed_at": today}
