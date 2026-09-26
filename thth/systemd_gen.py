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


def _tick_of(account_cfg: dict) -> int:
    """台帳の `tick_minutes`（読めない・1 未満なら既定 10）。"""
    tick_minutes = account_cfg.get("tick_minutes") or DEFAULT_TICK_MINUTES
    try:
        tick_minutes = int(tick_minutes)
    except (TypeError, ValueError):
        tick_minutes = DEFAULT_TICK_MINUTES
    if tick_minutes < 1:
        tick_minutes = DEFAULT_TICK_MINUTES
    return tick_minutes


def render_timer(account_cfg: dict) -> str:
    """`.timer` unit の中身を返す（`thth systemd <account>` が標準出力に出す）。

    `Persistent=true`・`Unit=thth@<account>.service`。既存の `systemd/thth@.service`
    をそのまま使う（`THTH_ROOT=/srv/thth` を渡している・変更しない）。
    """
    account_name = account_cfg["account"]
    tick_minutes = _tick_of(account_cfg)
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


# --- 採集だけの timer（同席専用アカウント・監査 2 回目・P2-5）-----------------
#
# **`thth collect` を回す口が無かった。** 採集は `thth run`（＝投稿）の最後に
# ぶら下がっているので、`scheduled: false`（timer を持たない）の同席専用
# アカウントでは**誰も呼ばない**——v2.0.1 で「同席の送信も採る」と直したのに、
# 実際に回すには人が手で `thth collect <account>` を打つしかなかった。
# **人が毎日打つことを前提にした設計は、打たれない日に黙って穴が空く。**
#
# 投稿の timer（`thth@<account>.timer`）とは**別の名前**にする
# （`thth-collect@<account>`）。同じ名前に寄せると、`scheduled: false` の
# アカウントで投稿の unit が enable されうる。
COLLECT_UNIT_PREFIX = "thth-collect@"


def collect_offset_minutes(account_name: str, tick_minutes: int) -> int:
    """採集の起点（分）。**投稿の起点とずらす**（同じ機械で当たりを重ねない）。"""
    return offset_minutes(f"{account_name}-collect", tick_minutes)


def render_collect_timer(account_cfg: dict) -> str:
    """`thth-collect@<account>.timer` の中身（`thth systemd <a> --collect-only`）。

    刻みは投稿と同じ `tick_minutes`（既定 10）。採集の刻みは投稿からの経過時間
    （1・6・24・72・168 時間）で決まり、timer は「跨いだか」を見に来るだけなので、
    細かく回しても API は叩かない（`collect.due_marks()`）。
    """
    account_name = account_cfg["account"]
    tick_minutes = _tick_of(account_cfg)
    offset = collect_offset_minutes(account_name, tick_minutes)
    return (
        f"# 生成: `thth systemd {account_name} --collect-only`（手で編集しない）。\n"
        f"# 同席専用（`thth send` だけで使う・`scheduled: false`）アカウントの\n"
        f"# 採集を回す。投稿の timer（thth@{account_name}.timer）は enable しない。\n"
        "[Unit]\n"
        f"Description=THTH collect for {account_name}"
        f"（{tick_minutes} 分ごと・終日・採集だけ）\n"
        "\n"
        "[Timer]\n"
        f"OnCalendar=*:{offset}/{tick_minutes}\n"
        "Persistent=true\n"
        "RandomizedDelaySec=0\n"
        f"Unit={COLLECT_UNIT_PREFIX}{account_name}.service\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def render_collect_service() -> str:
    """`thth-collect@.service`（雛形・`--collect-only --service`）。

    **投稿の service（`thth@.service`）と分ける。** あちらは `bin/thth-run` を
    呼ぶ（投稿してから採る）。こちらは `thth collect %i` だけ——**同席専用の
    アカウントで投稿の経路を走らせない。**
    """
    return (
        "# 生成: `thth systemd <account> --collect-only --service`（手で編集しない）。\n"
        "# 雛形 unit（`%i` にアカウント名が入る）。1 つ置けば全アカウントで使える。\n"
        "[Unit]\n"
        "Description=THTH collect for %i（採集だけ・投稿はしない）\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        "User=wt\n"
        "Environment=THTH_ROOT=/srv/thth\n"
        "ExecStart=/srv/thth/app/bin/thth collect %i\n"
        "TimeoutStartSec=300\n"
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


def render_worker_service(credentials: str) -> str:
    """Render a long-running worker; never read credentials or operate systemd.

    ExecStart is systemd syntax, not shell syntax. Limit this operator-supplied
    path to literal absolute ASCII components, excluding $, %, quoting and
    controls rather than silently expanding a different credentials file.
    """
    import re
    if (not isinstance(credentials, str)
            or not re.fullmatch(r'/[A-Za-z0-9._/-]+', credentials)
            or any(part in ('', '.', '..') for part in credentials.split('/')[1:])):
        raise ValueError('credentials: use an absolute ASCII path with letters, digits, /, ., _, - only')
    return (
        '# Generated by thth systemd --approval-worker --credentials PATH\n'
        '# Save as thth-approval-worker.service; credentials remain in their private file.\n'
        '[Unit]\n'
        'Description=THTH approval worker\n'
        'After=network-online.target\n'
        'Wants=network-online.target\n\n'
        '[Service]\n'
        'Type=simple\n'
        'User=wt\n'
        'Environment=THTH_ROOT=/srv/thth\n'
        # Git creates the managed clone's own files; a 002 umask would make them
        # group-writable and thth's isolation checks then refuse the repository.
        'UMask=0077\n'
        f'ExecStart=/srv/thth/app/bin/thth approval-worker --credentials {credentials}\n'
        'Restart=on-failure\n'
        'RestartSec=5\n\n'
        '[Install]\n'
        'WantedBy=multi-user.target\n'
    )
