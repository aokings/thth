"""承認の確定の段で、台帳から作った下書きの行を読み直す（計画 段 2・今の `thth/shop/recheck.py` の延長）。

`thth approve` の**確定の段**（`--confirm`・`--confirm-file`）で、頭に `from_register:` がある下書きだけ見る。
次のどれかなら前向きに断る（見せる段は何もしない）:

- 出さない期限（`not_after`）を過ぎた（`register_draft_expired`）。終わった催し・過ぎた臨時の休みを告げないため。
- 台帳の控えがこの機械に無い（`register_recheck_unavailable`）。店の事実は間違えたときの害が大きいので、
  読み直せないまま通さない（店の下書きは知らせて通すが、ここは断る側に倒す）。
- 行が無い・柵を通らない（推測に戻った・違うと言われた・見直し日を過ぎた・退役した・出してよい印が外れた・
  認めたあとに中身が変わった）。柵の符丁をそのまま言う。
- 行の中身の指紋が、下書きを作ったときの指紋と違う（`register_row_changed`）。

網には出ない（台帳は手元のフォルダ）。
"""
from __future__ import annotations

import re

from .. import jst, queuefile, redact, refusal
from . import fence, form, ledger
from .errors import RegisterRefused

_ORIGIN = re.compile(r"([A-Za-z0-9_.-]{1,64})/([a-z0-9][a-z0-9_.-]{0,79})\Z")
_DIGEST = re.compile(r"[0-9a-f]{16}\Z")


def _line(path: str, code: str, detail: str | None = None) -> str:
    found = refusal.parts(code) or {}
    text = f"{path}: {code}: {found.get('why') or code}" + (f"（{detail}）" if detail else "")
    fix = found.get("fix")
    if isinstance(fix, dict):
        fix = fix.get("text")
    if fix:
        text += " " + fix
    if found.get("better"):
        text += " " + found["better"]
    return redact.redact(text)


def check(path: str, front_matter: dict) -> str | None:
    """断る理由（1 行）か None。台帳の下書きでなければ None。"""
    value = front_matter.get("from_register")
    if value is None:
        return None
    matched = _ORIGIN.fullmatch(str(value).strip())
    digest = str(front_matter.get("from_register_digest") or "").strip()
    if not matched or not _DIGEST.fullmatch(digest):
        return _line(path, "register_row_changed", "from_register_unreadable")
    try:
        until = queuefile.parse_not_after(front_matter)
    except ValueError:
        until = None
    if until is None:
        # 台帳の下書きは必ず出さない期限を持つ。消されていたら、期限を確かめられないので断る。
        return _line(path, "register_draft_expired", "not_after_missing")
    if jst.now_jst() > until:
        return _line(path, "register_draft_expired", queuefile.jst_minute(until))
    name, row_id = matched.group(1), matched.group(2)
    try:
        cfg = ledger.load(name)
    except RegisterRefused as exc:
        if exc.code == "register_missing":
            return _line(path, "register_recheck_unavailable", name)
        return _line(path, exc.code, exc.detail)
    try:
        root = ledger.root_of(cfg)
        scanned = form.scan(root)
        row = form.find(scanned["rows"], row_id)
        if row is None:
            return _line(path, "register_row_missing", row_id)
        fence.usable(row, ledger.read_confirmed(name), jst.now_jst().date())
    except RegisterRefused as exc:
        return _line(path, exc.code, exc.detail)
    if form.fingerprint(row.text) != digest:
        return _line(path, "register_row_changed", row_id)
    return None
