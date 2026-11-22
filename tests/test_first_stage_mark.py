"""二段確認の一段目は断りに数えない（柵・11/22）。

一段目（中身と digest を見せるだけ・rc 1）を返す口が `refusals.mark_first_stage()` を呼ばないと、
`cli.main()` がその rc 1 を断りとして控え、report_channel に「exit_1 で断られた」と出る。
11/20 に dm send で見つかり、11/22 に同じ穴が edit・retract・inflight・shop・link・register の
route と erase にもあった。新しい口で同じ忘れをしないよう、一段目の形を書くファイルは印も持つことを縛る。
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent / "thth"

# 一段目の形（JSON の "stage": 1・値の stage を 1 と見て分かれる）。
STAGE_ONE = re.compile(r"""["']stage["']\s*[:\]]\s*(?:==\s*)?1\b|\.get\(["']stage["']\)\s*==\s*1""")

# 一段目を書くが rc 1 で返さない・CLI の入口でないもの（理由つき）。
EXEMPT = {
    "ads/ops.py": "値を作るだけ。CLI（ads/cli.py）は一段目を rc 0 で返すので断りに数えられない",
    "ads/cli.py": "一段目を rc 0 で返す（_write_call）",
    "link/send.py": "値を作るだけ。印は link/cli.py の _first_stage が付ける",
    "register/erase.py": "値を作るだけ。印は register/erase_cli.py が付ける",
    "hatena_conflict.py": "stage の語は別の意味",
    "oggformats.py": "stage の語は別の意味（音声の形式の段）",
    "server_writes.py": "サーバの口で値を返すだけ。cli.main の rc を通らない",
    "server_meta.py": "サーバの口。一段目を ok として扱う（rc で断りに数えない）",
}


def test_一段目を書く口は断りに数えない印も持つ():
    missing = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in EXEMPT:
            continue
        text = path.read_text(encoding="utf-8")
        if STAGE_ONE.search(text) and "mark_first_stage" not in text:
            missing.append(rel)
    assert missing == [], ("一段目を返すのに refusals.mark_first_stage() を呼ばない口: "
                           f"{missing}（一段目の分かれ道で呼ぶか、rc 1 で返さないなら EXEMPT に理由を書く）")


def test_例外の表に残っているファイルは実在する():
    for rel in EXEMPT:
        assert (ROOT / rel).is_file(), rel
