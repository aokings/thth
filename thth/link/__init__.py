"""店の変化の連動（設計 `docs/設計_店の変化の連動_2026-11-19.md`）: 台帳の 1 つの変化 → 束 → 出し先へ出す。

- 網に出ない部分（宣言・束・二度出さない）は台帳の機械の中（`thth/register/linkage.py`・`bundle.py`）。
- ここは出す側（網に出る）: `send.py`（Google マップ＝`thth/gbp/`・IG のフィードとストーリー＝`core.send_once`）・
  `status.py`（`thth scenario` の現在地と `thth events` の 1 行）・`cli.py`（`thth link …`）。

**同意のある物だけ確認なしで出す**（持ち主が `.thth/linkage.json` に前もって具体的に書いた同意・取り消しは次の回から
効く）。同意の無い物は束ごとの二段確認。Google 側が THTH の前に出した値から変わっていたら自動では出さずに止める。
"""
