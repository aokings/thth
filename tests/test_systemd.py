"""`thth systemd <account>`（設計 §3.2・masaru 指摘 2026-09-09）。

台帳から `.timer` unit を機械的に出す。手で書くと刻みがずれる（実際に旧
`systemd/thth@nigamilab-threads.timer` は毎時になっていた）ので、生成に一本化した。
"""
from __future__ import annotations

from tests.conftest import run_thth
from thth import systemd_gen


def test_offset_minutesは決定的():
    a = systemd_gen.offset_minutes("nigamilab-threads", 10)
    b = systemd_gen.offset_minutes("nigamilab-threads", 10)
    assert a == b
    assert 0 <= a < 10


def test_offset_minutesはアカウントごとにずれる():
    """設計 §3.2: アカウントごとに起点をずらす（git の pull/push と Meta への
    当たりを重ねない）。実在する 4 アカウントで衝突しないことを固定する。"""
    names = ["nigamilab-threads", "asmon-kanto-threads", "kopicha-threads", "masaru-threads"]
    offsets = [systemd_gen.offset_minutes(n, 10) for n in names]
    assert len(set(offsets)) == len(names)  # 全部ばらける


def test_offset_minutesはPYTHONHASHSEEDに依らない():
    """組み込み hash() は使わない（プロセスごとに変わると再現しない）。sha256 な
    ので同じ入力なら常に同じ出力。"""
    import hashlib
    expected = int(hashlib.sha256(b"nigamilab-threads").hexdigest()[:8], 16) % 10
    assert systemd_gen.offset_minutes("nigamilab-threads", 10) == expected


def test_render_timerはOnCalendarにoffsetとtick_minutesを埋める():
    cfg = {"account": "nigamilab-threads", "tick_minutes": 10}
    text = systemd_gen.render_timer(cfg)
    offset = systemd_gen.offset_minutes("nigamilab-threads", 10)
    assert f"OnCalendar=*:{offset}/10" in text
    assert "Unit=thth@nigamilab-threads.service" in text
    assert "Persistent=true" in text
    assert "[Timer]" in text
    assert "[Install]" in text
    assert "WantedBy=timers.target" in text


def test_render_timerはtick_minutes省略時10():
    cfg = {"account": "nigamilab-threads"}
    text = systemd_gen.render_timer(cfg)
    offset = systemd_gen.offset_minutes("nigamilab-threads", 10)
    assert f"OnCalendar=*:{offset}/10" in text


def test_render_timerはquiet_hoursを出さない():
    """静かな時間帯は unit 側で絞らない（select だけが見る・設計 §3.2 の裁定）。
    unit の中に時刻範囲の絞り込み（07..22 等）が出ないことを確認する。"""
    cfg = {"account": "nigamilab-threads", "tick_minutes": 10}
    text = systemd_gen.render_timer(cfg)
    assert "07.." not in text
    assert "22:00" not in text


def test_cli_thth_systemdは台帳から生成して標準出力に出す(isolated_account):
    result = run_thth(["systemd", isolated_account["name"]])
    assert result.returncode == 0
    offset = systemd_gen.offset_minutes(isolated_account["name"], 10)
    assert f"OnCalendar=*:{offset}/10" in result.stdout
    assert f"Unit=thth@{isolated_account['name']}.service" in result.stdout


def test_cli_thth_systemdは台帳が無ければexit2():
    result = run_thth(["systemd", "no-such-account"])
    assert result.returncode == 2
