"""承認の確定の段で、台帳から作った下書きの行を読み直す（計画 段 2・今の `thth/shop/recheck.py` の延長）。

`thth approve` の**確定の段**（`--confirm`・`--confirm-file`）で、頭に `from_register:` がある下書きだけ見る。
次のどれかなら前向きに断る（見せる段は何もしない）:

- 出さない期限（`not_after`）を過ぎた（`register_draft_expired`）。終わった催し・過ぎた臨時の休みを告げないため。
- 台帳の控えがこの機械に無い（`register_recheck_unavailable`）。店の事実は間違えたときの害が大きいので、
  読み直せないまま通さない（店の下書きは知らせて通すが、ここは断る側に倒す）。
- 行が無い・柵を通らない（推測に戻った・違うと言われた・見直し日を過ぎた・退役した・出してよい印が外れた・
  認めたあとに中身が変わった）。柵の符丁をそのまま言う。
- 行の中身の指紋が、下書きを作ったときの指紋と違う（`register_row_changed`）。

**台帳の下書きかどうかは、頭でなく THTH の側の控えで知る**（`thth/draft_origin.py`・監査 5.19.0 直したほうがよい 1）。
持ち主の LLM が本文を整えるときに頭の `from_register` の行を落としても、控えにある出所で読み直す。頭の出所が控えと
違う・出さない期限が控えより後に動かされた、なら断る。控えに無く、頭にも無いのに、名前が `register draft` の付ける形
（`…-register-<台帳>-<行>-<きっかけ>.md`）なら、読み直せないので断る。

網には出ない（台帳は手元のフォルダ）。
"""
from __future__ import annotations

import os
import re

from .. import draft_origin, jst, queuefile, redact, refusal
from . import fence, form, ledger
from .errors import RegisterRefused
from .triggers import TRIGGERS

_ORIGIN = re.compile(r"([A-Za-z0-9_.-]{1,64})/([a-z0-9][a-z0-9_.-]{0,79})\Z")
_DIGEST = re.compile(r"[0-9a-f]{16}\Z")
# `draft.file_name` の付ける形（台帳の名前と行の番号は - を含みうるので、きっかけの語で終わることだけ見る）。
_FILE_NAME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}-register-.+-(?:" + "|".join(TRIGGERS) + r")\.md\Z")
# 控えと比べる頭の行（`draft.text_for` が書く出所の 4 行）。
ORIGIN_KEYS = ("from_register", "from_register_digest", "from_register_commit", "from_register_trigger")
_DETAIL_WORD = re.compile(r"[a-z_]{1,40}\Z")


def _line(path: str, code: str, detail: str | None = None) -> str:
    word = {"reason": detail} if isinstance(detail, str) and _DETAIL_WORD.fullmatch(detail) else {}
    found = refusal.parts(code, **word) or {}
    text = f"{path}: {code}: {found.get('why') or code}" + (f"（{detail}）" if detail else "")
    fix = found.get("fix")
    if isinstance(fix, dict):
        fix = fix.get("text")
    if fix:
        text += " " + fix
    if found.get("better"):
        text += " " + found["better"]
    return redact.redact(text)


def recorded(path: str) -> tuple:
    """THTH の側の控えにある、この下書きの頭の行（`(頭 か None, 壊れた控えがあったか)`）。"""
    return draft_origin.find([ledger.state_dir_for(name) for name in ledger.list_names()], path)


def looks_like_draft(path: str) -> bool:
    """名前が `register draft` の付ける形か。"""
    return bool(_FILE_NAME.fullmatch(os.path.basename(str(path))))


def _with_record(path: str, front_matter: dict):
    """控えと頭を合わせる。`(使う頭, 断る理由)`。台帳の下書きでなければ `(None, None)`。"""
    record, broken = recorded(path)
    if record is None:
        if front_matter.get("from_register") is not None:
            return front_matter, None  # 控えの無い機械か、控えを書く前の下書き（今までどおり頭で読み直す）
        if broken and looks_like_draft(path):
            return None, _line(path, "register_ledger_invalid", "drafts")
        if looks_like_draft(path):
            return None, _line(path, "register_recheck_unavailable", "from_register_missing")
        return None, None
    for key in ORIGIN_KEYS:
        if key in front_matter and str(front_matter[key]).strip() != record.get(key, ""):
            return None, _line(path, "register_row_changed", "from_register_mismatch")
    merged = dict(front_matter)
    merged.update({key: record[key] for key in ORIGIN_KEYS if key not in front_matter and key in record})
    try:
        until, kept = queuefile.parse_not_after(front_matter), queuefile.parse_not_after(record)
    except ValueError:
        until, kept = None, None
    if until is not None and kept is not None and until > kept:
        return None, _line(path, "register_draft_expired", "not_after_moved")
    return merged, None


def check(path: str, front_matter: dict) -> str | None:
    """断る理由（1 行）か None。台帳の下書きでなければ None。"""
    front_matter, problem = _with_record(path, front_matter or {})
    if problem:
        return problem
    if front_matter is None:
        return None
    value = front_matter.get("from_register")
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
