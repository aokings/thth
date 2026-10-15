"""運営者の CLI の `thth send` にも安全装置を掛ける（10/15 masaru「推しで」）。

それまでは thth.me を通る道にだけ掛かり、music-research-threads が VM の `thth send` で
5 分に 7 件出た（間隔 1 時間・burst 10 分に 3 件の台帳のまま）。
"""
from __future__ import annotations

import datetime

from thth import core, guard, jst
from thth.adapters import base
from tests.test_send import _digest_for

NOW = datetime.datetime(2026, 10, 15, 17, 30, tzinfo=jst.JST)


class _Spy:
    def __init__(self):
        self.n = 0

    def publish(self, post, *, dry_run, on_container_created=None):
        self.n += 1
        return base.PublishResult(post_id=f"P{self.n}{abs(hash(post.text)) % 10**6}", url=None,
                                  ts=jst.iso(NOW))

    def fetch_post(self, post_id):
        return {"author_key": "0123456789abcdef"}


def _send(account, spy, text, *, minute, reply_to=None):
    now = NOW + datetime.timedelta(minutes=minute)
    return core.send_once(account["name"], text=text, reply_to=reply_to, production_flag=True,
                          confirm=_digest_for(account["name"], text, reply_to=reply_to),
                          adapter_factory=lambda *_: spy, now=now)


def test_最短間隔が経っていなければ断り_返信には掛けない(isolated_account_factory):
    account = isolated_account_factory(production=True, min_interval_hours=1)
    spy = _Spy()
    assert _send(account, spy, "一本目", minute=0).exit_code == 0
    r = _send(account, spy, "二本目", minute=5)
    assert r.exit_code == 1 and r.action == "guard" and r.error == "too_soon"
    assert "最短間隔" in r.message and "thth account set" in r.message
    assert spy.n == 1
    # 返信は会話なので間隔を掛けない（thth.me の道と同じ裁定 (a)）。
    assert _send(account, spy, "返信です", minute=6, reply_to="17999").exit_code == 0


def test_急な連投は口座を止めて断る(isolated_account_factory):
    account = isolated_account_factory(production=True, min_interval_hours=0,
                                       burst={"count": 3, "minutes": 10})
    spy = _Spy()
    for i in range(3):
        assert _send(account, spy, f"本文{i}", minute=i).exit_code == 0
    r = _send(account, spy, "四本目", minute=3)
    assert r.action == "guard" and r.error == "account_stopped" and "resume" in r.message
    assert guard.stopped(account["name"])["reason"] == "burst"
    assert spy.n == 3
    # 止まった口座は次も断る（戻すのは人）。
    assert _send(account, spy, "五本目", minute=30).action == "guard"


def test_1日の上限で断る(isolated_account_factory):
    account = isolated_account_factory(production=True, min_interval_hours=0, daily_max_posts=2,
                                       burst={"count": 10, "minutes": 10})
    spy = _Spy()
    assert _send(account, spy, "一", minute=0).exit_code == 0
    assert _send(account, spy, "二", minute=1).exit_code == 0
    r = _send(account, spy, "三", minute=2)
    assert r.error == "daily_limit" and spy.n == 2


def test_dryrunには掛けない(isolated_account_factory):
    account = isolated_account_factory(production=True, min_interval_hours=1)
    spy = _Spy()
    assert _send(account, spy, "一本目", minute=0).exit_code == 0
    r = core.send_once(account["name"], text="下書き", adapter_factory=lambda *_: spy,
                       now=NOW + datetime.timedelta(minutes=1))
    assert r.action != "guard" and r.digest


def test_account_statusは安全装置の掛かる所を言う(isolated_account_factory, capsys):
    from thth import account_settings
    account = isolated_account_factory(production=True)
    account_settings.show_status(account_settings.status(account["name"], now=NOW))
    out = capsys.readouterr().out
    assert "安全装置の掛かる所: thth send（VM の CLI・thth.me・MCP）" in out
    assert "timer が出す予約の原稿には掛からない" in out


def test_止めの印を置けない置き場でも数は守らせ_落ちない(isolated_account_factory):
    """VM の 775 の置き場や手元の Mac（THTH_ROOT の外）。例外で落とさず、印は置かない。"""
    account = isolated_account_factory(production=True, min_interval_hours=0,
                                       burst={"count": 2, "minutes": 10})
    import os
    from thth import accounts
    state = accounts.state_dir_for(account["name"])
    os.makedirs(state, exist_ok=True)
    os.chmod(state, 0o775)   # VM の kopicha-bluesky 等と同じモード
    assert not guard.stop_place_usable(account["name"])
    spy = _Spy()
    assert _send(account, spy, "一", minute=0).exit_code == 0
    assert _send(account, spy, "二", minute=1).exit_code == 0
    r = _send(account, spy, "三", minute=2)
    assert r.error == "burst_limit" and "止めずに" in r.message and spy.n == 2
    assert _send(account, spy, "四", minute=30).exit_code == 0  # 窓が過ぎれば出せる
    assert not os.path.exists(os.path.join(state, "guard", "stopped.json"))
