"""広告の口（設計 MetaAPI 計画 B-4・D・段 4）。**この段は読むだけ。**

広告は投稿先の媒体ではない（「お金で配る物」・広告アカウントは複数のページ・IG にまたがる）ので、
adapter の枠（`adapters.REGISTRY`・`media_registry.MEDIA`）には入れない。はてなの主体
（`thth/principals.py`）の前例に倣い、台帳と状態を口座の外に置く:

- `ledger.py`  台帳 `adaccounts/<名前>.json`・状態 `adaccount_state/<名前>/`・鍵・止めの印・
  書く操作を断る印（通貨が JPY 以外・B-13）
- `graphread.py`  Graph の**読むだけ**の口（GET しか持たない・共通層 `thth/meta/` に乗る）
- `auth.py`  `thth ads auth`（ユーザーの長い鍵を広告の口座の `.token` に）・`--exchange` の試し
- `watch.py`  collect のたびの読み（`snapshot.json`）と出来事（`ads_active_outside_thth` ほか）
- `cli.py`  `thth ads accounts|add|auth|doctor|show|insights|collect`

**この段の `thth ads` は Meta に書く命令を 1 つも持たない**（作る・配信・予算・`spend_cap`・止める
以外の変更）。`tests/test_meta_stage4.py` が、命令の一覧・Graph への要求の method（GET だけ）・
この包みの綴り（POST・DELETE を持たない）で縛る。`spend_cap` と THTH の上限（`ceilings`）を
上げる口は段 5 になっても THTH に持たせない（D-1）。上限は VM の台帳を直接書く。
"""
