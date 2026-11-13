"""台帳の機械（構想 `docs/構想_台帳ベースの総合サイト運営_2026-11-12.md` §5・§9 の 4、計画
`docs/計画_実店舗_2026-11-13.md` 段 1〜4）。

店にも自前のメディアにも共通の土台（段 1）の上に、実店舗の形（段 2）・聞いて埋める（段 3）・外の場所に手で貼る束
（段 4）を足す。命令は `thth register …`。

台帳は持ち主の手元のフォルダ（git の repo のことが多い）で、1 行 = 1 枚の .md。頭の項目（front-matter）に
番号・状態・由来・見直し日・出してよいかの印を持つ。THTH の運営者は台帳を預からない。

- `errors.py`: 断りの符丁（`REASONS`）と `RegisterRefused`。
- `form.py`: 1 枚の .md を読んで形を点検する・中身の指紋（THTH が書く欄を除く）。
- `ledger.py`: THTH の側の控え（`registers/<名前>.json`）と認めた記録（`register_state/<名前>/confirmed.json`）。
- `fence.py`: 柵の関数 1 つ「この行は中身の材料に使えるか」。
- `confirm.py`: 認める操作・違うと言った操作（頭の決まった欄だけを書く）。
- `kinds.py`: 行の種類ごとの決まり（二段で認める種類・既定で出さない欄・本文の形）。
- `cli.py`: 命令。

**網に出ない**（どのファイルも網の部品を import しない・柵 `tests/test_register_base.py`）。
"""
from __future__ import annotations

COMMAND = "register"
# 記録（`admin_log`）に出す動きの名前の頭。
ACTION_PREFIX = "register_"
