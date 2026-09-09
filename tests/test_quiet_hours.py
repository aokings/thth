"""静かな時間帯（quiet_hours）を意図して覆うテスト（T3b・2026-09-09）。

T3a で「たまたま覆えていた」もの（本物の壁時計に頼っていて、静かな時間帯に
pytest を走らせると必ず落ちる 5 件）を conftest.py の `frozen_now_jst`
autouse fixture で直した。ここではその上で、静かな時間帯まわりの挙動そのものを
**意図して**固定する: 中では出ない・外では出る・日をまたぐ判定（22:00〜07:00 で
23:00 も 02:00 も「中」）・境界（22:00 ちょうど・07:00 ちょうど）・
`quiet_hours: null` なら深夜でも出る、の 5 点。
"""
from __future__ import annotations

import datetime

from tests.conftest import make_queue_text
from thth import jst
from thth import queuefile
from thth import select as select_mod

ACCOUNT = "nigamilab-threads"

DEFAULT_QUIET_HOURS = ["22:00", "07:00"]


def _dt(hour: int, minute: int = 0, day: int = 9) -> datetime.datetime:
    return datetime.datetime(2026, 9, day, hour, minute, 0, tzinfo=jst.JST)


def account_cfg(**overrides):
    cfg = {
        "media": "threads",
        "hashtags": False,
        "quiet_hours": DEFAULT_QUIET_HOURS,
        "min_interval_hours": 0,
        "stale_days": 7,
    }
    cfg.update(overrides)
    return cfg


def write(tmp_path, name, **kwargs):
    path = tmp_path / name
    path.write_text(make_queue_text(**kwargs), encoding="utf-8")
    return str(path)


def _approved_due_file(tmp_path, *, publish_at="2026-09-08T00:00:00+09:00"):
    """承認済みで期限も来ている（publish_at が過去）投稿を 1 本用意する。

    このファイルの `now` は 2026-09-09 の 02:00〜23:00 の範囲を試すので、
    publish_at はそのどれよりも前（前日 00:00）にしておく（`future` 条件・
    条件 5 と混ざらないようにする。quiet_hours の判定だけを見たいため）。
    """
    p = write(tmp_path, "a.md", fm_overrides={"publish_at": publish_at})
    return queuefile.parse(p)


def _select(files, *, now, cfg):
    return select_mod.select_one(
        files, account_name=ACCOUNT, account_cfg=cfg, now=now,
        last_post_at=None, recent_texts=set())


# --- 1. 静かな時間帯の中では、承認済みで期限も来ている投稿が出ない ---

def test_静かな時間帯の中では出ない(tmp_path):
    qf = _approved_due_file(tmp_path)
    result = _select([qf], now=_dt(23, 0), cfg=account_cfg())
    assert result.chosen is None
    assert any(r.reason == "quiet_hours" for r in result.rejections)


# --- 2. 静かな時間帯の外では、同じものが出る ---

def test_静かな時間帯の外では出る(tmp_path):
    qf = _approved_due_file(tmp_path)
    result = _select([qf], now=_dt(10, 0), cfg=account_cfg())
    assert result.chosen is not None
    assert result.chosen.path == qf.path


# --- 3. 日をまたぐ指定（22:00〜07:00）で、23:00 も 02:00 も「中」 ---

def test_日をまたぐ指定で23時は中(tmp_path):
    assert select_mod.in_quiet_hours(_dt(23, 0), DEFAULT_QUIET_HOURS) is True


def test_日をまたぐ指定で02時は中(tmp_path):
    assert select_mod.in_quiet_hours(_dt(2, 0), DEFAULT_QUIET_HOURS) is True


def test_日をまたぐ指定で23時は投稿が出ない(tmp_path):
    qf = _approved_due_file(tmp_path)
    result = _select([qf], now=_dt(23, 0), cfg=account_cfg())
    assert result.chosen is None


def test_日をまたぐ指定で02時は投稿が出ない(tmp_path):
    qf = _approved_due_file(tmp_path)
    result = _select([qf], now=_dt(2, 0), cfg=account_cfg())
    assert result.chosen is None


# --- 4. 境界（22:00 ちょうど・07:00 ちょうど） ---
# `in_quiet_hours()` は start <= cur（22:00 ちょうどは「中」に含む）・
# cur < end（07:00 ちょうどは「中」から外れる＝再開の境界）という半開区間。

def test_境界_22時ちょうどは中(tmp_path):
    assert select_mod.in_quiet_hours(_dt(22, 0), DEFAULT_QUIET_HOURS) is True


def test_境界_22時ちょうどは投稿が出ない(tmp_path):
    qf = _approved_due_file(tmp_path)
    result = _select([qf], now=_dt(22, 0), cfg=account_cfg())
    assert result.chosen is None


def test_境界_07時ちょうどは中でない(tmp_path):
    assert select_mod.in_quiet_hours(_dt(7, 0), DEFAULT_QUIET_HOURS) is False


def test_境界_07時ちょうどは投稿が出る(tmp_path):
    qf = _approved_due_file(tmp_path)
    result = _select([qf], now=_dt(7, 0), cfg=account_cfg())
    assert result.chosen is not None


def test_境界_21時59分は中でない(tmp_path):
    assert select_mod.in_quiet_hours(_dt(21, 59), DEFAULT_QUIET_HOURS) is False


def test_境界_06時59分は中(tmp_path):
    assert select_mod.in_quiet_hours(_dt(6, 59), DEFAULT_QUIET_HOURS) is True


# --- 5. quiet_hours: null なら深夜でも出る ---

def test_quiet_hoursがnullなら深夜でも出る(tmp_path):
    qf = _approved_due_file(tmp_path)
    result = _select([qf], now=_dt(2, 0), cfg=account_cfg(quiet_hours=None))
    assert result.chosen is not None
    assert result.chosen.path == qf.path


def test_quiet_hoursが空配列でも深夜でも出る(tmp_path):
    qf = _approved_due_file(tmp_path)
    result = _select([qf], now=_dt(2, 0), cfg=account_cfg(quiet_hours=[]))
    assert result.chosen is not None


# --- frozen_now_jst fixture 自体の上書き入口の確認（conftest.py 参照） ---
# `thth.core.throw_once()` のように `now` を渡さず `jst.now_jst()` に頼る経路が、
# frozen_now_jst() で差し替えた時刻を実際に読むことを確認する（回帰防止）。

def test_frozen_now_jstは既定で静かな時間帯の外を返す():
    now = jst.now_jst()
    assert select_mod.in_quiet_hours(now, DEFAULT_QUIET_HOURS) is False


def test_frozen_now_jstを静かな時間帯の中へ上書きできる(frozen_now_jst):
    frozen_now_jst(_dt(23, 0))
    now = jst.now_jst()
    assert now == _dt(23, 0)
    assert select_mod.in_quiet_hours(now, DEFAULT_QUIET_HOURS) is True
