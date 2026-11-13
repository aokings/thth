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
- 台帳の器 段 1（設計 `docs/設計_台帳の器_2026-11-13.md` §5.1・§5.2・§10）。どれも読むだけで、台帳に書かない:
  - `vessel.py`: 種類の宣言と憲法（`.thth/`・JSON）・元のファイルのまま読む記録・版（全バイト）と依る所の指紋
    （読んだ値から）・内側の単位（番号 → 鍵 → 鍵と中身の指紋）・値ごとの範囲と時点と親の出典・指す欄・観測。
  - `yamlread.py`: YAML を字として読み、範囲の外の書き方を断る（PyYAML を入れた機械だけ・`thth[yaml]`）。
  - `judgment.py`: 判断の記録の最小の形と、効いているか（効いている・古い・消えた指し先）・結びの候補。
  - `vessel_cli.py`: `register kinds`・`register links`・器の `register check`。
- 台帳の器 段 2（設計 §5.5・§5.6 の (1)・§5.7・§10 の段 2・§13）。元のフォルダには書かない:
  - `keep.py`: 目録と目録の指紋・器の側の控え（`.thth/kept/`。git の外の台帳でも前の版を取り出す）・まとめての
    入れ替え（`.thth/batch/`。止まっても器の読み手には前か後のどちらかだけを見せ、続けるか戻すかが決まる）。
  - `adopt.py`: `adopt`（取り込む物の範囲・元のバイトのまま置く・取り込み直しは片道）・`restore`・`diff`・`merge`・
    表の 1 行の `row_set`・`row_add`（触っていない行のバイトが変わるなら書かずに断る）。
  - `adopt_cli.py`: `register adopt`・`restore`・`diff`・`keep`・`merge`・`resume`・`row set|add`。

**網に出ない**（どのファイルも網の部品を import しない・柵 `tests/test_register_base.py`）。
"""
from __future__ import annotations

COMMAND = "register"
# 記録（`admin_log`）に出す動きの名前の頭。
ACTION_PREFIX = "register_"
