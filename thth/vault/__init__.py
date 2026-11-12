"""金庫と庭の THTH の側の口（段 1・**仮**）。`thth sync`（台帳を monban の金庫と合わせる）と `thth garden`（庭の頁）。

**masaru の答えを待たずに推しで作った・朝に覆せる**（masaru 11/12 夜「私の回答はまたずにナイトシフトで…金庫と庭まで
すすめて」）。正本は monban の設計案（docs/設計案_monban_台帳の版の金庫_thth_sync_2026-11-12.md）の §15.6 と、
THTH の docs/構想_台帳ベースの総合サイト運営_2026-11-12.md の §6・docs/原則_庭の来訪者と広告_2026-11-12.md。

- `wire.py`: 断りの符丁（`REASONS`）と、monban の道具の出力から符丁だけを取り出す所（**出力をそのまま写さない**）。
- `tools.py`: monban の道具（`monban-vault`・`git-remote-monban`）と git を探して呼ぶ 1 か所。
- `sync.py`: 台帳の点検（仮）と check → pull → push の並び。
- `garden.py`: 店の写しから静的な頁を作る口と、出す前の検査（monban の制限＋THTH の柵）。
- `cli.py`: 命令。

決まり:

- **暗号は THTH で書かない。** 金庫の箱を作る・開ける・比べて置くは monban の `git-remote-monban` と `monban-vault`。
- **鍵・戻しの文字列・合言葉を THTH は持たない。** `thth sync init` は monban-vault の出力を端末へ直に流し（THTH は
  読まない）、`thth sync join` は標準入力を monban-vault へ直に渡す（THTH は読まない）。ほかの命令も、monban と git の
  出力は符丁と数だけを取り出し、文をそのまま写さない（柵 `tests/test_sync_garden.py`）。
- **本物の monban には試験で繋がない**（試験は偽の実行ファイル）。
"""
from __future__ import annotations

# monban の道具の入れ方（段 1: 公開の registry にはまだ出ていない・monban の設計案 §15.6）。
INSTALL_HINT = "monban の checkout で pip install '.[vault]'"
