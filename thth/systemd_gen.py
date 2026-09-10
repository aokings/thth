"""`thth systemd <account>`: 台帳から `.timer` unit を生成する（設計 §3.2・
masaru 指摘 2026-09-09）。

手で書くと刻みがずれる（実際に旧 `systemd/thth@nigamilab-threads.timer` は
毎時 `07..22:07` になっていて、設計の 10 分刻みと食い違っていた）。台帳の
`tick_minutes`（既定 10）から `OnCalendar=*:<offset>/<tick_minutes>` を機械的に
出すことで、台帳と unit がずれる事故を作り直しの手間ごと無くす。

**offset はアカウント名のハッシュから決める**（設計 §3.2: アカウントごとに
起点を 1〜2 分ずつずらし、git の pull/push と Meta への当たりを重ねない）。
組み込みの `hash()` は使わない（文字列 hash は `PYTHONHASHSEED` でプロセスごと
に変わり、再実行のたびに違う unit が出てしまう）。`sha256` の先頭 8 桁を
`tick_minutes` で割った余りにする（決定的・Python の版に依らない）。

**静かな時間帯（quiet_hours）は unit に出さない**（設計 §3.2 の裁定）。timer は
終日走らせ、静かな時間帯はコード側の `select`（`thth/select.py`）だけが見る。
真実の置き場を 1 つにする（unit と台帳の 2 か所に「いつ出さないか」を書くと、
どちらかを直し忘れて食い違う）。夜の実行は「出すものが無い」で終わるだけで
API を叩かないので軽い。
"""
from __future__ import annotations

import hashlib

DEFAULT_TICK_MINUTES = 10


def offset_minutes(account_name: str, tick_minutes: int) -> int:
    """アカウント名から起点（分）を決める（`sha256` 先頭 8 桁 mod `tick_minutes`）。"""
    digest = hashlib.sha256(account_name.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % tick_minutes


def render_timer(account_cfg: dict) -> str:
    """`.timer` unit の中身を返す（`thth systemd <account>` が標準出力に出す）。

    `Persistent=true`・`Unit=thth@<account>.service`。既存の `systemd/thth@.service`
    をそのまま使う（`THTH_ROOT=/srv/thth` を渡している・変更しない）。
    """
    account_name = account_cfg["account"]
    tick_minutes = account_cfg.get("tick_minutes") or DEFAULT_TICK_MINUTES
    try:
        tick_minutes = int(tick_minutes)
    except (TypeError, ValueError):
        tick_minutes = DEFAULT_TICK_MINUTES
    if tick_minutes < 1:
        tick_minutes = DEFAULT_TICK_MINUTES
    offset = offset_minutes(account_name, tick_minutes)

    return (
        f"# 生成: `thth systemd {account_name}`（手で編集しない。台帳\n"
        f"# accounts/{account_name}.json の tick_minutes を直して作り直す。設計 §3.2）。\n"
        "[Unit]\n"
        f"Description=THTH throw for {account_name}"
        f"（{tick_minutes} 分ごと・終日。生成: thth systemd {account_name}）\n"
        "\n"
        "[Timer]\n"
        "# 静かな時間帯（quiet_hours）はここでは絞らない。台帳とコード側の select だけが\n"
        "# 見る（真実の置き場を 1 つにする）。timer は終日走らせ、夜の実行は「出すものが\n"
        "# 無い」で終わるだけで API を叩かないので軽い（設計 §3.2 の裁定）。\n"
        f"OnCalendar=*:{offset}/{tick_minutes}\n"
        "Persistent=true\n"
        "RandomizedDelaySec=0\n"
        f"Unit=thth@{account_name}.service\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


# `thth maintain` の timer は **1 日 1 回・アカウント別ではない**（`thth maintain`
# 自身が全アカウントを見て回る）。時刻は投稿の刻み（10 分ごと）と重ならない
# 明け方に置く。`Persistent=true` なので VM が落ちていた日も起動後に 1 回走る
# ——60 日の時限に対して「その日走らなかった」を作らないため。
MAINTAIN_ONCALENDAR = "*-*-* 04:17:00"


def render_maintain_timer() -> str:
    """`thth-maintain.timer` の中身を返す（`thth systemd --maintain`）。"""
    return (
        "# 生成: `thth systemd --maintain`（手で編集しない）。\n"
        "[Unit]\n"
        "Description=THTH maintain（全アカウントのトークン保守・1 日 1 回）\n"
        "\n"
        "[Timer]\n"
        f"OnCalendar={MAINTAIN_ONCALENDAR}\n"
        "# VM が落ちていた日も起動後に 1 回走らせる（走らなかった日を作らない）。\n"
        "Persistent=true\n"
        "RandomizedDelaySec=0\n"
        "Unit=thth-maintain.service\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def render_maintain_service() -> str:
    """`thth-maintain.service` の中身を返す（`thth systemd --maintain --service`）。

    投稿の service（`thth@.service`）と分ける。**投稿が止まっていてもトークンの
    保守は走る**という切り分けが、unit の粒度としても見えるようにする。
    """
    return (
        "# 生成: `thth systemd --maintain --service`（手で編集しない）。\n"
        "[Unit]\n"
        "Description=THTH maintain（全アカウントのトークン保守）\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        "User=wt\n"
        "Environment=THTH_ROOT=/srv/thth\n"
        "ExecStart=/srv/thth/app/bin/thth maintain\n"
        "TimeoutStartSec=300\n"
    )
