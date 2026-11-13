"""台帳の 1 行 = 1 枚の .md を読み、形を点検する（構想 §5・計画 段 1）。

1 枚の形:

```
---
id: shop-hours                 # ずっと同じ番号（英小文字・数字・_・.・-）
kind: hours                    # 行の種類（店も自前のメディアも、英小文字の語）
status: 本採用                 # 試し・本採用・退役
source: owner                  # owner（持ち主が書いた）・asked（聞いて埋めた）・guess（推測）・register（レジから）
confirmed: 本人が認めた        # 推測・本人が認めた・本人が違うと言った（3 つの印）
confirmed_at: 2026-11-13       # THTH が書く欄（認める操作のとき）
review_by: 2027-02-13          # 見直し日
public: true                   # 外に出してよいか
private_fields: phone          # 出さない印の欄（, で区切る・任意）
public_fields: address         # 既定で出さない欄のうち、出してよい欄（任意）
---
本文
```

- 頭の無い .md（`---` で始まらない）は行ではない（README などの読み物）。数えるだけで点検しない。
- 隠しの名前（`.` で始まる）のファイルとフォルダは読まない。symlink は辿らない（台帳の外を読まない）。
- 中身の指紋（`fingerprint`）は、THTH が書く欄（`MANAGED`）の行を除いた全文の sha256 の頭 16 字。
  認めたあとに中身（頭のほかの欄か本文）が 1 字でも変われば指紋が変わり、認めたが外れる（`fence.py`）。
- 秘密らしい値（鍵・パスワード）がある行は断る（台帳は git の repo なので・計画 §6 の 5）。
"""
from __future__ import annotations

import datetime
import hashlib
import os
import re
from dataclasses import dataclass, field

from .. import redact
from . import kinds as kinds_mod

HEAD_REQUIRED = ("id", "kind", "status", "source", "confirmed", "review_by", "public")
STATUSES = ("試し", "本採用", "退役")
RETIRED = "退役"
SOURCES = ("owner", "asked", "guess", "register")
GUESS, YES, NO = "推測", "本人が認めた", "本人が違うと言った"
CONFIRMS = (GUESS, YES, NO)
LIST_FIELDS = ("private_fields", "public_fields")
# THTH が台帳に書く欄はこれだけ（頭の決まった欄・本文には触らない）。段 4 で外の場所の 2 つ（貼った日・貼った行）を足した。
MANAGED = ("confirmed", "confirmed_at", "synced_on", "synced_row")
# 中身の材料として外に出さない、行の仕組みの欄。
INTERNAL = HEAD_REQUIRED + ("confirmed_at",) + LIST_FIELDS
ROW_MAX_BYTES = 64 * 1024
ROWS_MAX = 5000
ID = re.compile(r"[a-z0-9][a-z0-9_.-]{0,79}\Z")
KIND = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
KEY = re.compile(r"[a-z][a-z0-9_]{0,39}\Z")
_COMMENT = re.compile(r"\s+#.*\Z")

# 点検の語ごとの直し方（`thth register check` が 1 行ずつ添える）。語は `:` の前で引く。
PROBLEM_TEXT = {
    "head_unclosed": "頭の終わりの --- の行を足してください",
    "head_line": "頭の行は「欄: 値」の形にしてください（欄は英小文字・数字・_）",
    "key_duplicate": "同じ欄が 2 回あります。1 つにまとめてください",
    "missing": "この欄を頭に足してください（id・kind・status・source・confirmed・review_by・public の 7 つは要ります）",
    "id_form": "id は英小文字・数字・_・.・- で 80 字までにしてください",
    "id_duplicate": "同じ id の行がほかにあります。片方の id を付け直してください（id はずっと同じものを指します）",
    "kind_form": "kind は英小文字で始まる語にしてください（例 hours・event・voice）",
    "status": "status は 試し・本採用・退役 のどれかにしてください",
    "source": "source は owner・asked・guess・register のどれかにしてください",
    "confirmed": "confirmed は 推測・本人が認めた・本人が違うと言った のどれかにしてください",
    "review_by": "review_by は YYYY-MM-DD の日付にしてください",
    "date_form": "日付の欄は YYYY-MM-DD にしてください",
    "public": "public は true か false にしてください",
    "list_field": "private_fields・public_fields は欄の名前を , で区切って並べてください",
    "too_large": "1 枚は 64 KB までにしてください。長い本文は行を分けると、見直し日も分けて持てます",
    "not_utf8": "UTF-8 で保存し直してください",
    "secret": "鍵やパスワードは台帳に書かず、THTH の鍵の置き場（thth の token set の命令）に渡してください",
    "symlink": "symlink は辿りません。台帳のフォルダの中に本物のファイルとして置いてください",
    "too_many": f"行は {ROWS_MAX} 枚までにしてください（台帳を分けると点検も速くなります）",
}


@dataclass
class Row:
    path: str                     # 台帳の根からの相対の道
    head: dict = field(default_factory=dict)
    body: str = ""
    text: str = ""
    problems: list = field(default_factory=list)

    @property
    def id(self) -> str | None:
        value = self.head.get("id")
        return value if isinstance(value, str) and ID.fullmatch(value) else None

    @property
    def kind(self) -> str:
        return self.head.get("kind") or ""


def parse_day(value) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(value) if isinstance(value, str) and len(value) == 10 else None
    except ValueError:
        return None


def parse_list(value) -> list | None:
    """`a, b` か `[a, b]` を欄の名前の並びに。形が違えば None。空なら []。"""
    if value is None:
        return []
    text = value.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    items = [part.strip().strip("'\"") for part in text.split(",") if part.strip()]
    return items if all(KEY.fullmatch(item) for item in items) else None


def _value(raw: str) -> str:
    value = _COMMENT.sub("", raw).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    return value


def split(text: str):
    """`(頭の行の並び, 本文, 閉じたか)`。頭が無ければ None。"""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return lines[1:index], "\n".join(lines[index + 1:]), True
    return lines[1:], "", False


def parse(path: str, text: str) -> Row:
    row = Row(path=path, text=text)
    parts = split(text)
    if parts is None:
        return row
    head_lines, row.body, closed = parts
    if not closed:
        row.problems.append("head_unclosed")
    for number, line in enumerate(head_lines, start=2):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, raw = line.partition(":")
        key = key.strip()
        if not sep or not KEY.fullmatch(key) or line[:1].isspace():
            row.problems.append(f"head_line:{number}")
            continue
        if key in row.head:
            row.problems.append(f"key_duplicate:{key}")
            continue
        row.head[key] = _value(raw)
    _check_head(row)
    return row


def _check_head(row: Row) -> None:
    head = row.head
    for key in HEAD_REQUIRED:
        if not head.get(key):
            row.problems.append(f"missing:{key}")
    if head.get("id") and not ID.fullmatch(head["id"]):
        row.problems.append("id_form")
    if head.get("kind") and not KIND.fullmatch(head["kind"]):
        row.problems.append("kind_form")
    if head.get("status") and head["status"] not in STATUSES:
        row.problems.append("status")
    if head.get("source") and head["source"] not in SOURCES:
        row.problems.append("source")
    if head.get("confirmed") and head["confirmed"] not in CONFIRMS:
        row.problems.append("confirmed")
    if head.get("review_by") and parse_day(head["review_by"]) is None:
        row.problems.append("review_by")
    if head.get("confirmed_at") and parse_day(head["confirmed_at"]) is None:
        row.problems.append("date_form:confirmed_at")
    if head.get("public") and head["public"] not in ("true", "false"):
        row.problems.append("public")
    for key in LIST_FIELDS:
        if key in head and parse_list(head[key]) is None:
            row.problems.append(f"list_field:{key}")
    if KIND.fullmatch(head.get("kind") or ""):
        row.problems.extend(kinds_mod.problems(head["kind"], head, row.body))
    if redact.looks_like_secret(row.text):
        row.problems.append("secret")


def is_row(row: Row) -> bool:
    return split(row.text) is not None


def fingerprint(text: str) -> str:
    """THTH が書く欄（`MANAGED`）の行を除いた全文の sha256 の頭 16 字。"""
    lines = text.split("\n")
    parts = split(text)
    if parts is not None:
        head_lines, _body, _closed = parts
        end = 1 + len(head_lines)
        kept = [line for line in lines[1:end] if line.partition(":")[0].strip() not in MANAGED]
        lines = lines[:1] + kept + lines[end:]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16]


def read_file(root: str, relative: str) -> Row:
    path = os.path.join(root, relative)
    if os.path.islink(path):
        return Row(path=relative, problems=["symlink"], text="---\n")
    try:
        with open(path, "rb") as f:
            data = f.read(ROW_MAX_BYTES + 1)
    except OSError:
        return Row(path=relative, problems=["not_utf8"], text="---\n")
    if len(data) > ROW_MAX_BYTES:
        return Row(path=relative, problems=["too_large"], text="---\n")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return Row(path=relative, problems=["not_utf8"], text="---\n")
    return parse(relative, text)


def scan(root: str) -> dict:
    """台帳のフォルダを読む。`{"rows": [Row], "skipped": 頭の無い .md の数, "problems": [{"path", "id", "words"}]}`。"""
    rows, skipped = [], 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(files):
            if name.startswith(".") or not name.endswith(".md"):
                continue
            relative = os.path.relpath(os.path.join(directory, name), root)
            row = read_file(root, relative)
            if not is_row(row):
                skipped += 1
                continue
            rows.append(row)
            if len(rows) > ROWS_MAX:
                return {"rows": rows[:ROWS_MAX], "skipped": skipped,
                        "problems": [{"path": ".", "id": None, "words": ["too_many"]}]}
    seen: dict[str, str] = {}
    for row in rows:
        if row.id is None:
            continue
        if row.id in seen:
            row.problems.append("id_duplicate")
        else:
            seen[row.id] = row.path
    problems = [{"path": row.path, "id": row.id, "words": list(row.problems)} for row in rows if row.problems]
    return {"rows": rows, "skipped": skipped, "problems": problems}


def problem_text(word: str) -> str:
    key = word.split(":", 1)[0]
    return PROBLEM_TEXT.get(key) or kinds_mod.PROBLEM_TEXT.get(key) or "形を直してください"


def find(rows: list, row_id: str) -> Row | None:
    return next((row for row in rows if row.id == row_id), None)


def outward(row: Row) -> dict:
    """外に出してよい頭の欄（行の仕組みの欄・出さない印の欄・既定で出さない欄を除く）。"""
    private = set(parse_list(row.head.get("private_fields")) or [])
    allowed = set(parse_list(row.head.get("public_fields")) or [])
    hidden = (kinds_mod.private_by_default(row.kind) - allowed) | private
    return {key: value for key, value in row.head.items()
            if key not in INTERNAL and key not in MANAGED and key not in hidden}
