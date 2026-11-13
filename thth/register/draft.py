"""きっかけから queue の下書きを 1 本ずつ作る（**承認されていない形**・計画 段 2）。

- 材料は柵（`fence.usable`）を通った行だけ。本文に入るのは、その行の**外に出してよい欄**（`form.outward`）だけ
  （住所・電話・臨時の休みの理由は既定で入らない）。
- 本文は素の 1〜3 行（日・時刻・名前）。文は持ち主の LLM が書く前提（`thth/shop/draft.py` と同じ）。
- 頭に出所: `from_register: <台帳>/<行>`・`from_register_digest`（認めた中身の指紋）・`from_register_commit`
  （台帳が git の repo なら HEAD の短い番号・違えば none）・`from_register_trigger`。
- 頭に**出さない期限** `not_after`（臨時は最後の日の終わり・催しは終わりの時刻）。期限を過ぎた下書きは、承認の確定の段で
  断り（`recheck.py`）、予約からも出ない（今の `not_after` の柵・`select.py`）。
- まとめて作らない: 1 回に 1 行（`--row`）か、種類を選んで上限 `MAX_DRAFTS` 本。同じ行・同じきっかけ・同じ指紋の下書きが
  queue にあれば作らない（`register_draft_exists`）。
- 書く前に今の lint（`thth/lint.py`）に通す。同じ名前のファイルがあれば上書きしない。commit・push はしない。
"""
from __future__ import annotations

import datetime
import os
import re
import secrets
import subprocess
from pathlib import Path

from .. import accounts, jst
from . import form, kinds
from .errors import RegisterRefused

MAX_DRAFTS = 3
_CONTROL = re.compile(r"[\x00-\x1f\x7f  ‪-‮⁦-⁩]")
_WEEK = "月火水木金土日"
TEXT_MAX = 80


def clean(value, limit: int = TEXT_MAX) -> str:
    """本文に入れる値（制御文字と向きを変える印を落とし、頭の # と --- を落とす）。"""
    text = _CONTROL.sub("", str(value or "")).replace("---", "").strip()
    return text.lstrip("#＃ ").strip()[:limit]


def queue_dir(cfg) -> Path:
    try:
        repo = Path(cfg["repo_dir"]).absolute()
        relative = Path(cfg["queue_dir"])
    except (KeyError, TypeError):
        raise RegisterRefused("register_draft_account", "queue_dir") from None
    if relative.is_absolute() or not relative.parts or any(p.startswith(".") for p in relative.parts):
        raise RegisterRefused("register_draft_account", "queue_dir")
    queue = repo / relative
    if os.path.commonpath([repo, queue.absolute()]) != str(repo) or not queue.is_dir():
        raise RegisterRefused("register_draft_account", "queue_dir")
    return queue


def load_account(name: str) -> dict:
    try:
        return accounts.load_account(name)
    except (accounts.AccountError, ValueError):
        raise RegisterRefused("register_draft_account", "account") from None


def commit_of(root: str) -> str:
    """台帳の repo の HEAD の短い番号（git の repo でなければ none）。網には出ない（`rev-parse` だけ）。"""
    try:
        done = subprocess.run(["git", "-C", root, "rev-parse", "--short=12", "HEAD"], capture_output=True, text=True,
                              timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return "none"
    value = done.stdout.strip()
    return value if done.returncode == 0 and re.fullmatch(r"[0-9a-f]{7,12}", value) else "none"


def day_text(day: datetime.date) -> str:
    return f"{day.month}月{day.day}日（{_WEEK[day.weekday()]}）"


def body_lines(trigger: str, row: form.Row) -> list:
    """本文の素の行（外に出してよい欄だけから作る）。"""
    f = form.outward(row)
    if trigger == "special_soon":
        start = kinds.parse_day(f.get("date"))
        end = kinds.parse_day(f.get("end_date")) or start
        when = day_text(start) if end == start else f"{day_text(start)}から{day_text(end)}まで"
        if f.get("closed") == "true":
            line = f"{when}はお休みします"
        else:
            line = f"{when}は {clean(f.get('open'))}〜{clean(f.get('close'))} の営業です"
        if f.get("reason"):
            line += f"（{clean(f['reason'])}）"
        return [line]
    if trigger == "event_soon":
        starts, ends = kinds.parse_minute(f.get("starts")), kinds.parse_minute(f.get("ends"))
        end_text = f"{ends:%H:%M}" if ends.date() == starts.date() else f"{day_text(ends.date())} {ends:%H:%M}"
        lines = [f"{clean(f.get('name'))}: {day_text(starts.date())} {starts:%H:%M}〜{end_text}"]
        extra = [f"{label}: {clean(f[key])}" for key, label in (("place", "場所"), ("fee", "参加費"), ("booking", "予約"))
                 if f.get(key)]
        return lines + ([" ・".join(extra)] if extra else [])
    if trigger == "hours_change":
        start = kinds.parse_day(f.get("effective_from"))
        days, notes, _problems = kinds.hours_lines(row.body)
        lines = [f"{day_text(start)}から営業の時間が変わります"]
        for day in kinds.WEEKDAYS:
            value = days.get(day)
            if value == "closed":
                lines.append(f"{day} 休み")
            elif value:
                lines.append(f"{day} " + ", ".join(_range_text(a, b) for a, b in value))
        return lines + [clean(note, 120) for note in notes]
    if trigger == "item_new":
        price = f"（{int(f['price']):,} 円）" if f.get("price") else ""
        return [f"{clean(f.get('name'))}{price}が店頭に並びました"]
    if trigger == "season_start":
        until = kinds.parse_day(f.get("season_to"))
        return [f"{clean(f.get('name'))}が始まります" + (f"（{day_text(until)}まで）" if until else "")]
    raise RegisterRefused("register_draft_nothing")


def _range_text(start: int, end: int) -> str:
    def hm(minutes: int) -> str:
        prefix = "翌" if minutes >= 1440 else ""
        minutes %= 1440
        return f"{prefix}{minutes // 60:02d}:{minutes % 60:02d}"
    return f"{hm(start)}-{hm(end)}"


def publish_at(now: datetime.datetime, not_after: datetime.datetime) -> str:
    """仮の時刻（翌日の 12:00 JST）。それが出さない期限より後なら、今から 10 分後（承認の前に人と LLM が直す）。"""
    day = jst.to_jst(now).date() + datetime.timedelta(days=1)
    at = datetime.datetime.combine(day, datetime.time(12, 0), tzinfo=jst.JST)
    if at > not_after:
        at = (jst.to_jst(now) + datetime.timedelta(minutes=10)).replace(second=0, microsecond=0)
    return jst.iso(at)


def origin(register: str, row_id: str) -> str:
    return f"{register}/{row_id}"


def text_for(register: str, account: str, media: str, trigger: str, row: form.Row, *, commit: str,
             now: datetime.datetime, not_after: datetime.datetime) -> str:
    lines = ["---", "thth: 1", f"account: {account}", "status: draft", f"publish_at: {publish_at(now, not_after)}",
             f"not_after: {jst.iso(not_after)}", f"from_register: {origin(register, row.id)}",
             f"from_register_digest: {form.fingerprint(row.text)}", f"from_register_commit: {commit}",
             f"from_register_trigger: {trigger}", "---", "", f"## {media}", ""]
    return "\n".join(lines + body_lines(trigger, row) + [""])


def file_name(register: str, row_id: str, trigger: str, now) -> str:
    return f"{jst.to_jst(now):%Y-%m-%d}-register-{register}-{row_id}-{trigger}.md"


def head(text: str) -> dict:
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}
    out = {}
    for line in lines[1:200]:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if sep and key.startswith("from_register"):
            out[key.strip()] = value.strip()
    return out


def same_exists(queue: Path, register: str, row: form.Row, trigger: str) -> str | None:
    want = {"from_register": origin(register, row.id), "from_register_trigger": trigger,
            "from_register_digest": form.fingerprint(row.text)}
    marker = "from_register: " + want["from_register"]
    for path in sorted(queue.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if marker in text and all(head(text).get(k) == v for k, v in want.items()):
            return path.name
    return None


def write(queue: Path, name: str, text: str) -> str:
    """lint に通してから、同じ名前が無いときだけ書く。"""
    from .. import lint
    target = queue / name
    if target.exists():
        raise RegisterRefused("register_draft_exists")
    temp = queue / (".lint-" + secrets.token_hex(8) + ".tmp")
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        problems = [p for p in lint.lint_file(str(temp)) if not lint.is_warning(p)]
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass
    if problems:
        raise RegisterRefused("register_draft_rejected", lint.reason_code(problems) or "lint")
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        raise RegisterRefused("register_draft_exists") from None
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return str(target)
