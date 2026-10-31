"""広告の口（設計 MetaAPI 計画 B-4・D・段 4〜5）。

広告は投稿先の媒体ではない（「お金で配る物」・広告アカウントは複数のページ・IG にまたがる）ので、
adapter の枠（`adapters.REGISTRY`・`media_registry.MEDIA`）には入れない。はてなの主体
（`thth/principals.py`）の前例に倣い、台帳と状態を口座の外に置く:

- `ledger.py`  台帳 `adaccounts/<名前>.json`・状態 `adaccount_state/<名前>/`・鍵・止めの印・
  書く操作を断る印（通貨が JPY 以外・B-13）
- `graphread.py`  Graph の**読むだけ**の口（GET しか持たない・共通層 `thth/meta/` に乗る）
- `auth.py`  `thth ads auth`（ユーザーの長い鍵を広告の口座の `.token` に）・`--exchange` の試し
- `watch.py`  collect のたびの読み（`snapshot.json`）と出来事（`ads_active_outside_thth` ほか）
- `graphwrite.py`  Graph の**書く**口（段 5・POST はここの `_post()` 1 か所・作るは全部 `PAUSED`）
- `ops.py`  書く操作（boost・activate・extend・pause・stop・lower・end）と記録 `ops/`
- `budget.py`  月の上限の予約→発射→確定（見積もり・請求書ではない）
- `digest.py`  承認の digest `thth-ads-1`（Meta から読み直した木から作る）
- `cli.py`  `thth ads accounts|add|auth|doctor|show|insights|collect|boost|activate|extend|pause|stop|budget|end`

**読む命令は Meta に書かない**（`tests/test_meta_stage4.py`）。書く口は段 5 の決まった命令の決まった関数だけ
（`graphwrite.ALLOWED_CALLERS`・`tests/test_meta_stage5.py`）。`spend_cap` と THTH の上限（`ceilings`）・
`ads_enabled`・`production` を上げる口は THTH に無い（D-1）。上限と旗は VM の台帳を直接書く。
"""
