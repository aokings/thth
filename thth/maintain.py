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
import sys

from . import accounts as accounts_mod
from . import adapters as adapters_mod
from . import jst
from . import oauth as oauth_mod
from . import redact as redact_mod
from . import runs as runs_mod
from .adapters import base as adapter_base

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
# **`.token` は在るが、この媒体に要る鍵が足りない**（独立監査 1・P3-8・
# 2026-09-13）。`doctor` は媒体の `TOKEN_KEYS` を見て「トークンが無い」と
# 言っていたのに、`maintain.inspect()` は `load_token()` が dict を返しさえ
# すれば「在る」として先へ進んでいた——Bluesky の `.token` に `identifier`
# だけあって `app_password` が無い（`thth auth` を途中で止めた・手で書いた）
# とき、**doctor は「トークンが無い」、board は「ok/期限なし」**と、同じ
# 状態を 2 通りに言っていた。**道具の中で言い分を割らない。**
TOKEN_INCOMPLETE = "token_incomplete"
UNREADABLE = "unreadable"
CONFIG_ERROR = "config_error"

# 人の手が要る state（`thth maintain` の終了コードが 1 になるもの）
ATTENTION_STATES = frozenset({REFRESH_FAILED, EXPIRING, EXPIRED, NO_TOKEN,
                              TOKEN_INCOMPLETE, UNREADABLE, CONFIG_ERROR})

# **次の一手は媒体が知っている**（`Adapter.TOKEN_SETUP_HINT`・独立監査 1・
# P2-4・2026-09-13）。ここの文言は `thth maintain` だけでなく **board と
# `thth account` にもそのまま出る**（どちらも `inspect()` の `message` を
# 読む）ので、Threads 用の `thth token set` を焼き付けると、Bluesky の
# アカウントに**入らない道**を 3 か所で勧めることになる（doctor は媒体を
# 見るようになっていたので、道具の中で言い分が割れていた）。
# `{hint}` を書いた文言だけが差し替わる。
_MESSAGES = {
    OK: "問題ありません",
    REFRESHED: "更新しました",
    REFRESH_DUE: "更新が必要です（--check なので更新していません）",
    REFRESH_FAILED: "更新に失敗しました",
    EXPIRING: "まもなく切れます（更新できていません）",
    EXPIRED: "期限切れです。`{hint}` で取り直してください",
    NO_TOKEN: "token がありません。`{hint}` を実行してください",
    TOKEN_INCOMPLETE: "token に足りない項目があります。`{hint}` を実行してください",
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
        # **「判らない」と「期限を持たない」を分ける**（設計 v2 §4.2）。
        # `remaining_days: null` は台帳を読めないときにも出る。どちらなのかを
        # 読み手（board・runs・`--json`）が判別できないと、Bluesky の
        # 「期限が無い」が Threads の「読めない」と同じ顔で並ぶ。
        "no_expiry": False,
        "refreshed": False,
        "message": "",
    }

    try:
        account_cfg = accounts_mod.load_account(account_name)
    except accounts_mod.AccountError as e:
        return _finish(row, CONFIG_ERROR, detail=str(e))

    if account_cfg.get('media') == 'x':
        from .adapters.auth_x import remaining, REFRESH_BEFORE_SECONDS
        token = accounts_mod.load_token(account_cfg)
        if token is None:
            return _finish(row, NO_TOKEN, hint='thth auth <account> --by <actor>')
        if not isinstance(token,dict):
            return _finish(row, UNREADABLE, detail='X のtoken形式が読めません')
        if not token.get('access_token') or not token.get('refresh_token'):
            return _finish(row, TOKEN_INCOMPLETE, hint='thth auth <account> --by <actor>',
                           detail='access_token / refresh_token が必要です')
        try:
            seconds = remaining(token, now)
        except ValueError:
            return _finish(row, UNREADABLE, detail='X の応答由来の期限が判りません')
        row['obtained_at'] = token.get('obtained_at')
        row['age_days'] = round((now-jst.parse(token['obtained_at'])).total_seconds()/86400, 2)
        row['remaining_days'] = round(seconds/86400, 5)
        return _finish(row, REFRESH_DUE if seconds <= REFRESH_BEFORE_SECONDS else OK,
                       detail='X は短命です。毎日1回の timer では期限維持を保証しません')

    # **媒体を先に引く**（独立監査 1・P1-1・2026-09-13）。期限を持つかどうかも、
    # 更新の口があるかどうかも**媒体の知識**で、`.token` の中身から推し量る
    # ものではない。以前はここが `.token` の `no_expiry` という**データの印
    # だけ**を見ていたので、印の無い Mastodon の `.token`（導入文書が示す手置き
    # の形）が「残り 60 日の Threads のトークン」に見え、50 日を越えた日から
    # `run_maintain()` が毎日 `refresh` を呼び、**Mastodon の access token が
    # Meta のサーバへ飛んでいた**。`media` を知らないときは断る——「判らない」を
    # 「Threads だ」と読み替えない。
    try:
        adapter_cls = adapters_mod.adapter_class(account_cfg.get("media"))
    except adapter_base.AdapterError as e:
        return _finish(row, CONFIG_ERROR, detail=str(e))
    hint = adapter_cls.TOKEN_SETUP_HINT

    token = accounts_mod.load_token(account_cfg)
    if token is None:
        return _finish(row, NO_TOKEN, hint=hint)
    # **在るかどうかの判定は媒体が持っている**（`TOKEN_KEYS`・`has_token()`）。
    # doctor と同じ物差しを使う（P3-8）。**欠けている鍵の名前は言う**——
    # 「足りない」だけでは何を足すのか判らない。**値は読まない。**
    if not adapter_cls.has_token(token):
        欠け = [key for key in adapter_cls.TOKEN_KEYS if not token.get(key)]
        return _finish(row, TOKEN_INCOMPLETE, hint=hint,
                       detail="足りない鍵: " + "・".join(欠け))

    row["obtained_at"] = token.get("obtained_at")
    unreadable = None
    age_seconds = remaining_days = None
    try:
        age_seconds, remaining_days = oauth_mod.token_age_and_remaining(token, now)
    except oauth_mod.OAuthError as e:
        unreadable = str(e)
    else:
        row["age_days"] = round(age_seconds / 86400.0, 2)

    if adapter_cls.TOKEN_NO_EXPIRY:
        # **期限を持たない媒体**（Bluesky の App Password・Mastodon の access
        # token・設計 v2 §4.2）。**`.token` の中身に関わらずここで終わる**
        # ——`no_expiry` の印が無い（手で置いた・古い道具が書いた）`.token` でも、
        # `obtained_at` が無くて年齢が判らなくても、**期限が無いことは変わらない**。
        # board に「残り 60 日」と嘘を言わせないための分岐（P1-1・(b)）。
        row["no_expiry"] = True
        return _finish(row, OK, detail="期限を持たないトークンです（更新は不要）")

    if unreadable is not None:
        return _finish(row, UNREADABLE, detail=unreadable)

    age_days = age_seconds / 86400.0

    if remaining_days is None:
        # `.token` に `no_expiry: true` が立っている（期限を持つ媒体でも、
        # 人がそう書いたなら額面どおりに読む）。
        row["no_expiry"] = True
        return _finish(row, OK, detail="期限を持たないトークンです（更新は不要）")

    row["remaining_days"] = round(remaining_days, 2)

    if remaining_days <= 0:
        # 期限切れは更新では戻らない（Meta 側が失効したトークンの更新を受け付けない）。
        return _finish(row, EXPIRED, hint=hint)
    if age_days > oauth_mod.REFRESH_AFTER_DAYS:
        if "refresh" not in adapter_cls.capabilities():
            # **更新の口が無い媒体を `REFRESH_DUE` に落とさない**（P1-1）。
            # 落とすと `run_maintain()` が `oauth.run_refresh()` を呼ぶ
            # ——その先は `graph.threads.net` である。人を呼んで止まる。
            return _finish(row, EXPIRING,
                           detail="この媒体に更新の口がありません（取り直してください）")
        if age_seconds / 3600.0 < oauth_mod.MIN_REFRESH_AGE_HOURS:
            # 公式の条件（24 時間未満は更新できない）。50 日超と同時には起こり得ないが、
            # `expires_in` が極端に短い台帳では起こる。更新を試みずに人を呼ぶ。
            return _finish(row, EXPIRING)
        return _finish(row, REFRESH_DUE)
    if remaining_days < WARN_REMAINING_DAYS:
        return _finish(row, EXPIRING)
    return _finish(row, OK)


def _finish(row: dict, state: str, *, detail: str = "", hint: str | None = None) -> dict:
    """`hint` はその媒体の「トークンの入れ方」（`Adapter.TOKEN_SETUP_HINT`）。

    台帳も媒体も引けなかったときは既定（`thth token set`）——**判らないときに
    別の道を名指ししない**。
    """
    row["state"] = state
    message = _MESSAGES.get(state, state)
    if "{hint}" in message:
        message = message.format(hint=hint or adapter_base.Adapter.TOKEN_SETUP_HINT)
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
        message = "アカウントが 1 つもありません（accounts/ を確認してください）"
        if as_json:
            print(message, file=sys.stderr)
        else:
            log(message)
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
            # **3 つを別の顔で出す**: 期限を持たない／判らない／残り日数。
            if r.get("no_expiry"):
                remaining = "期限なし"
            elif r["remaining_days"] is None:
                remaining = "—"
            else:
                remaining = f"残り {r['remaining_days']:.1f} 日"
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
