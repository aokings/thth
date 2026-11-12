"""店を読む口（設計 `docs/設計_ショップとWordPressの口_2026-11-12.md` §1〜§3・初めの版は BASE v2 だけ）。

媒体の adapter（`thth/adapters/`・投げる先）とは別の種類で、**読む元**。Instagram の商品タグの口
（`thth/meta_shop.py`）とは別のまとまりで、命令は同じ `thth shop` の家族（masaru 11/12「推しの通りで OK」）。
親の parser は `cli.parent()` の 1 か所で作り、両方がその下に動詞を足す。

- `wire.py`: 断りの符丁（`REASONS`）と網の口。**打つのは GET だけ・送り先は定数のホストだけ。**
- `ledger.py`: 台帳 `stores/<名前>.json`（0600）と状態 `store_state/<名前>/`（写し 1 枚）・鍵のファイル。
- `rows.py`: 店に寄らない行の部品（許した欄・在庫の「数えない」・買えるか）。
- `kinds.py`: 店の種類の表（名前・台帳の欄・口の作り方・id の形）。命令と読み直しはこの表を通す。
- `base.py`: BASE v2 の `GET /api/user`（店の名前と URL だけ取り出す）と `GET /api/items`（許した欄だけ）。
- `diff.py`: 写しとの違い（読むだけ・写しを進めるのは `--accept` の一手）。
- `draft.py`: 違いから queue の下書き（承認されていない形・1 回に 1 商品か上限 3 本）。
- `recheck.py`: `thth approve` の確定の段で店の下書きの商品を 1 回読み直す（値段・公開・在庫切れ）。
- `remove.py`: 店を外す（`thth shop remove`・`thth leave` の店の分）。鍵 → 写し → 台帳の順に消す。
- `cli.py`: 命令。
- 網に出るのは `wire.py` だけ（ほかのファイルは網の部品を import しない・柵 `tests/test_store_base_audit.py`）。

決まり（設計 §2）: 注文と買い手の情報を読まない・店に書かない・鍵を出力やファイルに出さない・引数で鍵を取らない。
この道具は BASE が提供するものではない（BASE の API 利用規約の条件・`NOT_BY_BASE`）。
"""
from __future__ import annotations

# 命令の名前（`thth shop …`・Instagram の商品タグの動詞と同じ家族）。
COMMAND = "shop"
# 記録・見張りに出す動きの名前の頭（Meta の権限の見張りの `shop_*` と混ぜない）。
ACTION_PREFIX = "shopread_"
NOT_BY_BASE = "この道具（THTH）は BASE が提供するものではありません。"
