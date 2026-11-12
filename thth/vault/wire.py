"""金庫と庭の口の断りと、monban・git の出力の読み方（段 1・仮・masaru の答えを待たずに推しで作った・朝に覆せる）。

**出力をそのまま写さない。** monban の道具と git の出力には、遠くの URL・端末の道・（壊れた道具なら）鍵や戻しの文字列が
混ざりうる。THTH が利用者に見せるのは、ここで取り出した**符丁（形を検査した英小文字の語）と数だけ**。
"""
from __future__ import annotations

import json
import re

# 断りの符丁（この口が出すもの全部・目録 `thth/refusal_catalog_part_vault.py` に 3 つがある）。
REASONS = frozenset((
    # 金庫（thth sync）
    "sync_args_invalid", "sync_tool_missing", "sync_not_repo", "sync_register_unclean", "sync_in_progress",
    "sync_branch_mismatch", "sync_remote_missing", "sync_url_invalid", "sync_conflict", "sync_retry_exhausted",
    "sync_tampered", "sync_busy", "sync_box_too_large", "sync_vault_full", "sync_not_joined", "sync_login_required",
    "sync_unavailable", "sync_vault_missing", "sync_join_failed", "sync_remote_redirected", "sync_init_needs_terminal",
    "sync_failed",
    # 庭（thth garden）
    "garden_args_invalid", "garden_tool_missing", "garden_snapshot_missing", "garden_out_not_empty", "garden_dir_missing", "garden_empty",
    "garden_kind_refused", "garden_size_refused", "garden_text_refused", "garden_active_refused",
    "garden_external_refused", "garden_markup_refused", "garden_guide_missing", "garden_publish_unconfirmed", "garden_not_ready",
    "garden_busy", "garden_full", "garden_login_required", "garden_unavailable", "garden_failed",
))

# 詳細に入れてよい形（静的な語・検査した相対の道・数）。道は日本語の名前もそのまま出す（どのファイルかが分かるように・
# 監査 段 1 の 5）。制御文字・引用符・<>・\ は入らない（`garden._safe_name` が ? に替える）。
_DETAIL = re.compile(r"[\w./:~ ?#()-]{1,96}\Z")
# monban の符丁の形（monban の errors.safe_code と同じ向き: 英小文字と _）。
MONBAN_CODE = re.compile(r"[a-z][a-z0-9_]{2,47}\Z")
_MONBAN_LINE = re.compile(r"(?:^|\n)\s*(?:remote: )?monban(?:-vault)?: ([a-z][a-z0-9_]{2,47})\b")


class VaultRefused(Exception):
    """金庫と庭の口の断り。`code` は `REASONS` のどれか、`detail` は形を検査した静的な語（無ければ None）。"""

    def __init__(self, code: str, detail: str | None = None, **extra):
        if code not in REASONS:
            code = "sync_failed" if code.startswith("sync_") else "garden_failed"
        self.code = code
        self.detail = detail if isinstance(detail, str) and _DETAIL.fullmatch(detail) else None
        self.extra = {k: v for k, v in extra.items() if isinstance(v, (int, bool)) or v is None
                      or (isinstance(v, str) and _DETAIL.fullmatch(v))}
        super().__init__(code)

    def __str__(self) -> str:
        return f"{self.code}（{self.detail}）" if self.detail else self.code


def monban_code(text: str) -> str | None:
    """`monban: <符丁> — …`・`monban-vault: <符丁> — …` の行から符丁だけ（最初の 1 つ）。文の残りは捨てる。"""
    m = _MONBAN_LINE.search(text or "")
    return m.group(1) if m else None


def monban_json(text: str) -> dict:
    """`monban-vault … --json` の 1 つの値から、許した欄だけを取り出す（`error`・`head`・`files`・`bytes`・`seq`・
    `joined`・`stopped`・`status`）。形が違う欄は捨てる。読めなければ {}。"""
    try:
        value = json.loads((text or "").strip().splitlines()[-1]) if (text or "").strip() else None
    except (ValueError, IndexError):
        return {}
    if not isinstance(value, dict):
        return {}
    out = {}
    if isinstance(value.get("error"), str) and MONBAN_CODE.fullmatch(value["error"]):
        out["error"] = value["error"]
    if isinstance(value.get("head"), str) and re.fullmatch(r"[a-f0-9]{64}", value["head"]):
        out["head"] = value["head"]
    for key in ("files", "bytes", "seq", "boxes"):
        if isinstance(value.get(key), int) and not isinstance(value.get(key), bool) and value[key] >= 0:
            out[key] = value[key]
    for key in ("joined", "stopped", "owner_stopped", "operator_stopped"):
        if isinstance(value.get(key), bool):
            out[key] = value[key]
    if isinstance(value.get("status"), str) and MONBAN_CODE.fullmatch(value["status"]):
        out["status"] = value["status"]
    return out


# git の出す断りの印（monban の設計案 §15.6 の表）。文でなく印だけを見る。
GIT_FETCH_FIRST = re.compile(r"\(fetch first\)|\bfetch first\b")
GIT_NON_FF = re.compile(r"non-fast-forward")
GIT_CONFLICT = re.compile(r"^CONFLICT \(|Automatic merge failed", re.MULTILINE)
GIT_NO_REMOTE_REF = re.compile(r"couldn't find remote ref")
GIT_NO_HELPER = re.compile(r"Unable to find remote helper|remote-monban' is not a git command")
