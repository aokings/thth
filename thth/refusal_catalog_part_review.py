"""断りの目録の項目（週の振り返りの材料・`thth review weekly`・`thth/review_weekly.py`）。thth/refusal_catalog.py が読む。

原則 `docs/原則_前向きな断り.md`: なぜ・どうすれば通るか・どうすればもっと良くなるか。
`since` は今の版（`thth/VERSION` を越えない柵）。版上げのとき、その版に直す。
"""
from __future__ import annotations

_C = ("cli",)
_SINCE = "5.30.0"
_WEEKLY = "thth review weekly <口座>"


def _entry(why, fix, command, better, kind="refusal"):
    return {"kind": kind, "why": why, "fix": fix, "command": command, "better": better, "doors": _C,
            "since": _SINCE}


ENTRIES = {
    "review_target_unknown": _entry(
        "thth review weekly に渡した名前が、口座の台帳にも project にも見つかりませんでした。",
        "thth account status <口座> で口座の名前を確かめるか、口座の台帳の project の名前を入れて打ち直してください。",
        _WEEKLY,
        "project の名前で打つと、その project の口座が全部 1 回で並び、落ち込みの印の立った口座が drop_accounts に出ます。"),
    "review_week_invalid": _entry(
        "--week の値が、見られる週に当たりませんでした（YYYY-MM-DD の形でない・まだ来ていない週・週の表の上限より古い週）。",
        "--week を YYYY-MM-DD（例 2026-11-16）で、今の週かそれより前の週の日付にして打ち直してください。省くと閉じた最後の週になります。",
        "thth review weekly <口座> --week <YYYY-MM-DD>",
        "週の終わりの日曜の投稿が 24 時間を越える月曜の夜より後に打つと、その週の数が欠けずにそろいます。"),
}
