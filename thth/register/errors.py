"""台帳の機械の断り（符丁は `REASONS`・3 つの文は目録 `thth/refusal_catalog_part10.py`）。

断りの文は符丁と、形を検査した静的な語（`detail`）だけ。台帳の本文・パス・値は断りに入れない
（台帳は持ち主の物で、断りの文は記録や報告に残りうるので）。
"""
from __future__ import annotations

import re

REASONS = frozenset((
    # 段 1: 土台（控え・形・印の柵・認める操作）。
    "register_args_invalid", "register_name_taken", "register_missing", "register_ledger_invalid",
    "register_path_unusable", "register_row_missing", "register_row_invalid", "register_row_secret",
    "register_row_guess", "register_row_denied", "register_row_unconfirmed", "register_row_review_passed",
    "register_row_not_public", "register_row_retired", "register_confirm_mismatch", "register_row_unwritable",
    "register_log_unavailable",
    # 段 2: 実店舗の形・きっかけ・下書き・承認の時の読み直し。
    "register_draft_account", "register_draft_nothing", "register_draft_exists", "register_draft_rejected",
    "register_draft_expired", "register_row_changed", "register_recheck_unavailable",
    # 段 3: 聞いて埋める（問いの記録）。
    "register_question_log_invalid", "register_question_missing", "register_question_answered",
    # 段 4: 外の場所に手で貼る束。
    "register_paste_kind", "register_paste_nothing", "register_place_not_place", "register_compare_empty",
    # 台帳の器 段 1: 読んで、同じ対象を指す（宣言・YAML の範囲・内側の単位・指す欄・判断の記録）。
    "register_kind_invalid", "register_yaml_refused", "register_record_invalid", "register_record_missing",
    "register_unit_ambiguous", "register_link_invalid", "register_judgment_invalid", "register_judgment_same_person",
))
_DETAIL = re.compile(r"[A-Za-z0-9_./:= -]{1,80}\Z")


class RegisterRefused(Exception):
    """台帳の機械の断り。`code` は `REASONS` のどれか、`detail` は形を検査した静的な語（無ければ None）。"""

    def __init__(self, code: str, detail: str | None = None, **extra):
        if code not in REASONS:
            code = "register_row_invalid"
        self.code = code
        self.detail = detail if isinstance(detail, str) and _DETAIL.fullmatch(detail) else None
        self.extra = extra
        super().__init__(code)

    def __str__(self) -> str:
        return f"{self.code}（{self.detail}）" if self.detail else self.code
