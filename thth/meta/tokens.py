"""鍵の見立て（設計 MetaAPI 計画 B-1・C-1・C-2・段 2）。

`adapters/facebook.py` の `_debug_token`・`_data_access_verdict` と、`adapters/auth_facebook.py` の
`/debug_token` の読みを抜き出した。

- `/debug_token`（**L2**: developers.facebook.com/docs/graph-api/reference/debug_token）: 応答の
  `data` に `scopes`・`expires_at`・`data_access_expires_at`・`is_valid`・`app_id`・`profile_id`。
  呼ぶ側はアプリの鍵（`<app_id>|<app_secret>`）か、アプリの開発者のユーザーの鍵。**ページの鍵で
  訊けるかは文書に無い**（未確認・doctor は訊けなければ「確かめられなかった」に落とす）。
- `data_access_expires_at`（90 日のデータに触れる期限・C-1）: 残り `DATA_ACCESS_WARN_DAYS` 日を
  切ったら注意、過ぎたら ×。
- 再認可の URL: 認可の画面の引数に `auth_type=reauthorize`（出典は計画 C-2 の 3・この段では
  原文を読み直していない）。`config_id` と併せて効くかは**未確認**（G-4）。
  `thth auth --reauthorize` の口はまだ作っていない（計画 C-2 の 3・できなければ普通の `thth auth`
  で足りる）。ここには引数の組み立てだけを置く。
"""
from __future__ import annotations

import datetime

from .. import jst

# データに触れる期限の注意の線（`maintain.DATA_ACCESS_WARN_DAYS` と同じ 14 日・計画 C-2）。
DATA_ACCESS_WARN_DAYS = 14.0
REAUTHORIZE = {"auth_type": "reauthorize"}


def debug_data(body) -> dict | None:
    """`/debug_token` の応答から `data`（object でなければ None）。"""
    data = body.get("data") if isinstance(body, dict) else None
    return data if isinstance(data, dict) else None


def scopes_of(data) -> list | None:
    """`data.scopes` が文字列の並びのときだけ返す（形が違えば None・嘘の一覧を作らない）。"""
    scopes = data.get("scopes") if isinstance(data, dict) else None
    if isinstance(scopes, list) and all(isinstance(s, str) for s in scopes):
        return list(scopes)
    return None


def int_field(data, key) -> int | None:
    value = data.get(key) if isinstance(data, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def data_access_verdict(value, *, recorded=None, hint: str, now=None):
    """`/debug_token` の**今の** `data_access_expires_at` → `(ok, detail)`（計画 C-2 の 2）。

    doctor は保存した値でなく今の値を言う（`.token` は書き直さない）。保存した値と違えば、
    そのことと `hint`（`thth auth` のやり直し）を言う。過ぎていれば ×、14 日を切っていれば
    ○のまま注意を書く（人を呼ぶのは `thth maintain` の `data_access_expiring`）。
    """
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return None, "応答に data_access_expires_at がありません（期限を言えません）"
    now = now or jst.now_jst()
    remaining = (value - now.timestamp()) / 86400.0
    when = jst.iso(datetime.datetime.fromtimestamp(value, jst.JST))
    detail = f"{when}（残り {remaining:.1f} 日）"
    if recorded is not None and recorded != value:
        detail += f"・.token の記録と違います（{hint} で書き直せます）"
    if remaining <= 0:
        return False, detail + f"・過ぎています。{hint} で認可し直してください"
    if remaining < DATA_ACCESS_WARN_DAYS:
        return True, detail + f"・14 日を切りました。{hint} で認可し直してください"
    return True, detail


def dialog_params(params: dict, *, reauthorize: bool = False) -> dict:
    """認可の画面の引数。`reauthorize` なら `auth_type=reauthorize` を足す（元の dict は変えない）。"""
    out = dict(params)
    if reauthorize:
        out.update(REAUTHORIZE)
    return out
