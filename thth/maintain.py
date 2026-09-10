"""`thth maintain`: 投稿とは独立にトークンを保つ（外部レビュー §5・設計 §3.2）。

**なぜ投稿から切り離すか。** 長期トークンは 60 日で切れる。更新の口
（`thth refresh`）はあったが、**どの経路からも呼ばれていなかった**——`thth run`
は `throw_once()` を呼ぶだけで、docstring に「T4 の refresh」と書いてあるだけ
だった。しかも仮に `run` の中に置いても足りない:

- **timer を持たないアカウント**（`masaru-threads` のような同席専用）は `run` が
  一度も走らない。投稿しないのにトークンだけは 60 日で死ぬ。
- **`inflight` が残っているアカウント**は `run` が手前の関門で止まる。人が気づく
  まで何日でも止まるので、その間トークンの更新も止まる。
- **長期停止中のアカウント**（承認済みの投稿が無い・`production: false`）も同じ。

つまり「投稿できること」と「トークンが生きていること」は別の話で、前者を後者の
**前提条件にしてはいけない**。`thth maintain` は queue を読まず、repo を同期せず、
`inflight` を見ず、`production` を見ない。**全アカウントを機械的に見て回り、
更新が要るものを更新し、人の手が要るものを述べる**だけ。

**account ロックは取らない。** 取ると、`inflight` で止まっているアカウントの
throw が握ったままのロックを待つことになり、「投稿が詰まるとトークンも死ぬ」と
いう塞ぎたかった形がそのまま戻ってくる。トークンの書き込みは
`secrets_fs.atomic_write_json()` で原子的なので、同時に走っても壊れない。

**値は一切表示しない。** 出すのは残り日数・取得時刻・状態だけ（`redact` を通す）。
"""
from __future__ import annotations

import json

from . import accounts as accounts_mod
from . import jst
from . import oauth as oauth_mod
from . import redact as redact_mod
from . import runs as runs_mod

# 残りがこれを切ったら、更新の期限（50 日）をとうに過ぎているのに更新できていない
# ということ（60 日 − 50 日 ＝ 残り 10 日で更新が始まる）。人を呼ぶ。
WARN_REMAINING_DAYS = 7.0

# state（board・runs にそのまま出る文字列）
OK = "ok"
REFRESHED = "refreshed"
REFRESH_DUE = "refresh_due"
REFRESH_FAILED = "refresh_failed"
EXPIRING = "expiring"
EXPIRED = "expired"
NO_TOKEN = "no_token"
UNREADABLE = "unreadable"
CONFIG_ERROR = "config_error"

# 人の手が要る state（`thth maintain` の終了コードが 1 になるもの）
ATTENTION_STATES = frozenset({REFRESH_FAILED, EXPIRING, EXPIRED, NO_TOKEN,
                              UNREADABLE, CONFIG_ERROR})

_MESSAGES = {
    OK: "問題ありません",
    REFRESHED: "更新しました",
    REFRESH_DUE: "更新が必要です（--check なので更新していません）",
    REFRESH_FAILED: "更新に失敗しました",
    EXPIRING: "まもなく切れます（更新できていません）",
    EXPIRED: "期限切れです。`thth token set` か `thth auth` で取り直してください",
    NO_TOKEN: "token がありません。`thth token set` か `thth auth` を実行してください",
    UNREADABLE: "token を読めません",
    CONFIG_ERROR: "台帳を読めません",
}


def inspect(account_name: str, *, now) -> dict:
    """1 アカントのトークンの状態を見る（何も書き換えない・API も叩かない）。

    戻り値は board・runs・`--json` がそのまま使える形。`remaining_days` は
    `expires_in`（無ければ公式の 60 日）から計算した残り日数。
    """
    row = {
        "account": account_name,
        "state": None,
        "obtained_at": None,
        "age_days": None,
        "remaining_days": None,
        "refreshed": False,
        "message": "",
    }

    try:
        account_cfg = accounts_mod.load_account(account_name)
    except accounts_mod.AccountError as e:
        return _finish(row, CONFIG_ERROR, detail=str(e))

    token = accounts_mod.load_token(account_cfg)
    if token is None:
        return _finish(row, NO_TOKEN)

    try:
        age_seconds, remaining_days = oauth_mod.token_age_and_remaining(token, now)
    except oauth_mod.OAuthError as e:
        return _finish(row, UNREADABLE, detail=str(e))

    age_days = age_seconds / 86400.0
    row["obtained_at"] = token.get("obtained_at")
    row["age_days"] = round(age_days, 2)
    row["remaining_days"] = round(remaining_days, 2)

    if remaining_days <= 0:
        # 期限切れは更新では戻らない（Meta 側が失効したトークンの更新を受け付けない）。
        return _finish(row, EXPIRED)
    if age_days > oauth_mod.REFRESH_AFTER_DAYS:
        if age_seconds / 3600.0 < oauth_mod.MIN_REFRESH_AGE_HOURS:
            # 公式の条件（24 時間未満は更新できない）。50 日超と同時には起こり得ないが、
            # `expires_in` が極端に短い台帳では起こる。更新を試みずに人を呼ぶ。
            return _finish(row, EXPIRING)
        return _finish(row, REFRESH_DUE)
    if remaining_days < WARN_REMAINING_DAYS:
        return _finish(row, EXPIRING)
    return _finish(row, OK)


def _finish(row: dict, state: str, *, detail: str = "") -> dict:
    row["state"] = state
    message = _MESSAGES.get(state, state)
    if detail:
        message = f"{message}: {detail}"
    row["message"] = redact_mod.redact(message)
    return row


def needs_attention(row: dict) -> bool:
    return row["state"] in ATTENTION_STATES


def run_maintain(account: str | None = None, *, check: bool = False,
                  as_json: bool = False, log=print, now=None,
                  refresh=None) -> int:
    """全アカウント（`account` 指定時はその 1 本）のトークンを保つ。

    `check=True` は**何も更新しない**（状態を述べるだけ）。`refresh` はテスト用の
    差し替え口で、既定は `thth.oauth.run_refresh`。

    終了コード: 人の手が要るものが 1 つでもあれば 1、無ければ 0。台帳そのものを
    読めない（アカウントが 1 つも無い）ときも 1（黙って 0 を返さない）。
    """
    now = now if now is not None else jst.now_jst()
    refresh = refresh if refresh is not None else oauth_mod.run_refresh

    if account:
        names = [account]
    else:
        names = accounts_mod.list_account_names()
    if not names:
        log("アカウントが 1 つもありません（accounts/ を確認してください）")
        return 1

    rows = []
    for name in names:
        row = inspect(name, now=now)

        if row["state"] == REFRESH_DUE and not check:
            rc = refresh(name, log=lambda line: None, now=now)
            if rc == 0:
                after = inspect(name, now=now)
                after["refreshed"] = True
                # 更新直後は age が 0 に戻るので state は ok になる。更新したことが
                # 分かるように上書きする（runs・board に「何をしたか」を残す）。
                row = _finish(after, REFRESHED)
            else:
                row = _finish(row, REFRESH_FAILED)

        rows.append(row)
        _record(name, row, now=now)

    attention = [r for r in rows if needs_attention(r)]

    if as_json:
        log(json.dumps({"now": jst.iso(now), "checked": len(rows),
                        "attention": len(attention), "accounts": rows},
                       ensure_ascii=False, indent=2))
    else:
        for r in rows:
            remaining = "—" if r["remaining_days"] is None else f"残り {r['remaining_days']:.1f} 日"
            mark = "要確認" if needs_attention(r) else "  "
            log(f"{mark} {r['account']}: {r['state']}（{remaining}）— {r['message']}")
        if attention:
            log(f"人の手が要るアカウントが {len(attention)} 件あります。")

    return 1 if attention else 0


def _record(account_name: str, row: dict, *, now) -> None:
    """runs に 1 行残す（`action: "maintain"`・設計 §4.6）。

    投稿と同じ流れに載せる。**毎回書く**——「今日の保守が走った」ことそのものが
    見たい情報だから（走らなかった日を後から見分けられる）。
    """
    try:
        state_dir = accounts_mod.state_dir_for(account_name)
    except accounts_mod.AccountError:
        return
    runs_mod.append_run(state_dir, {
        "account": account_name,
        "run_id": "maintain-" + jst.iso(now),
        "mode": "maintenance",
        "action": "maintain",
        "file": None,
        "post_id": None,
        "collected": None,
        "refreshed": bool(row.get("refreshed")),
        "quota": None,
        "status": "error" if needs_attention(row) else "ok",
        "error": row["state"] if needs_attention(row) else None,
    }, jst.month_str(now))
