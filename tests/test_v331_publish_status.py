"""3.3.1 §2: 公開が 5xx・timeout・接続断で終わったら、inflight を残す前に container に訊く。

09-22 の asmon-kanto-threads は `threads_publish` が HTTP 500（`is_transient=true`）で
「分からない」のまま 21 時間止まった。媒体には出ていなかった——訊けば分かった。

**2 度出す経路を作らない**を回数で固定する: 再試行は FINISHED を確かめた直後の
1 回だけ（`publish_calls` は多くて 2）。PUBLISHED・問い合わせ失敗・IN_PROGRESS
では再試行しない（`publish_calls == 1`）。
"""
from __future__ import annotations

import datetime
import os

from tests.conftest import init_git_pair, make_queue_text, write_queue_file
from tests.helpers import fake_threads_status as fake
from thth import accounts as accounts_mod
from thth import core
from thth import inflight as inflight_mod
from thth import queuefile
from thth import runs as runs_mod
from thth.adapters import base as adapter_base

NOW = datetime.datetime.fromisoformat("2026-09-09T10:00:00+09:00")
TEXT = "本文です。"
# 公開の時刻（frozen_now_jst の 10:00 JST）から 30 秒後・UTC 表記（Threads の形）。
NEAR = "2026-09-09T01:00:30+0000"
FAR = "2026-09-09T00:30:00+0000"      # 30 分前（窓の外）


def _publish(script, **kwargs):
    with fake.serve(script) as base_url:
        adapter = fake.adapter(base_url, **kwargs)
        return adapter.publish(adapter_base.Post(text=TEXT), dry_run=False)


# ---- アダプタ単体 --------------------------------------------------------

def test_5xxのあとFINISHEDなら1回だけ再試行して出る():
    script = fake.Script(publish=[(500, None), ("ok", "777")], status=["FINISHED"])
    result = _publish(script)
    assert result.post_id == "777" and result.failure == "none"
    assert result.remote_state == "retried"
    assert script.publish_calls == 2 and script.status_calls == 1


def test_再試行も5xxならinflightを残す_3度目は無い():
    script = fake.Script(publish=[(500, None), (500, None), ("ok", "999")], status=["FINISHED"])
    result = _publish(script)
    assert result.post_id is None and result.failure == "publish_ambiguous"
    assert result.remote_state == "retry_failed"
    assert script.publish_calls == 2          # 再試行は 1 回だけ


def test_再試行が4xxでも出ていないとは言わない():
    # 1 度目が実は通っていて 2 度目を媒体が断った可能性を消せない。
    script = fake.Script(publish=[(500, None), (400, None)], status=["FINISHED"])
    result = _publish(script)
    assert result.failure == "publish_ambiguous" and result.remote_state == "retry_failed"
    assert script.publish_calls == 2


def test_接続断のあとFINISHEDなら1回だけ再試行して出る():
    script = fake.Script(publish=[("drop", None), ("ok", "778")], status=["FINISHED"])
    result = _publish(script)
    assert result.post_id == "778" and result.remote_state == "retried"
    assert script.publish_calls == 2


def test_PUBLISHEDなら再試行せず最近の投稿から指紋の一致する1件を書き戻す():
    script = fake.Script(publish=[(500, None)], status=["PUBLISHED"],
                         posts=[fake.post_row("555", TEXT, NEAR),
                                fake.post_row("556", "別の本文", NEAR),
                                fake.post_row("557", TEXT, FAR)])
    result = _publish(script)
    assert result.post_id == "555" and result.failure == "none"
    assert result.remote_state == "published_located"
    assert script.publish_calls == 1


def test_PUBLISHEDでも一覧に無ければpublished_unlocatedで止まる():
    script = fake.Script(publish=[(500, None)], status=["PUBLISHED"],
                         posts=[fake.post_row("557", TEXT, FAR)])
    result = _publish(script)
    assert result.post_id is None and result.failure == "publish_ambiguous"
    assert result.remote_state == "published_unlocated"
    assert script.publish_calls == 1


def test_PUBLISHEDで一致が2件なら決めない():
    script = fake.Script(publish=[(500, None)], status=["PUBLISHED"],
                         posts=[fake.post_row("555", TEXT, NEAR), fake.post_row("558", TEXT, NEAR)])
    result = _publish(script)
    assert result.failure == "publish_ambiguous" and result.remote_state == "published_unlocated"
    assert script.publish_calls == 1


def test_ERRORとEXPIREDは出ていないとして解く():
    for status in ("ERROR", "EXPIRED"):
        script = fake.Script(publish=[(500, None)], status=[status])
        result = _publish(script)
        assert result.post_id is None
        assert result.failure == "publish_definite"
        assert result.error.startswith("publish_failed_remote_error")
        assert result.remote_state == status.lower()
        assert script.publish_calls == 1


def test_問い合わせ失敗なら従前どおりinflight_3回で諦める():
    script = fake.Script(publish=[(500, None)], status=[500])
    result = _publish(script)
    assert result.failure == "publish_ambiguous" and result.remote_state == "query_failed"
    assert script.publish_calls == 1 and script.status_calls == 3


def test_IN_PROGRESSのままなら再試行しない():
    script = fake.Script(publish=[(500, None)], status=["IN_PROGRESS"])
    result = _publish(script)
    assert result.failure == "publish_ambiguous" and result.remote_state == "in_progress"
    assert script.publish_calls == 1 and script.status_calls == 3


def test_知らないstatusは分からないとして扱う():
    script = fake.Script(publish=[(500, None)], status=["SOMETHING_NEW"])
    result = _publish(script)
    assert result.failure == "publish_ambiguous" and result.remote_state == "query_failed"
    assert script.publish_calls == 1


def test_4xxでは問い合わせない():
    script = fake.Script(publish=[(400, None)], status=["FINISHED"])
    result = _publish(script)
    assert result.failure == "publish_definite" and result.remote_state is None
    assert script.status_calls == 0 and script.publish_calls == 1


def test_問い合わせの前に毎回20秒待つ_試験は注入したsleepで待たない():
    slept = []
    script = fake.Script(publish=[(500, None)], status=["IN_PROGRESS", "IN_PROGRESS", "FINISHED"])
    script.publish.append(("ok", "779"))
    result = _publish(script, status_poll_seconds=20.0, sleep=slept.append)
    assert result.post_id == "779"
    # 最初の問い合わせも待ってから（5xx 直後の遷移中を FINISHED と読まない）。
    assert slept == [20.0, 20.0, 20.0]


def test_既定の間隔は20秒で_envで上書きできる(monkeypatch):
    from thth.adapters import threads as threads_mod
    monkeypatch.delenv("THTH_THREADS_STATUS_POLL_SECONDS", raising=False)
    assert threads_mod.ThreadsAdapter(access_token="x", user_id="1").status_poll_seconds == 20.0
    monkeypatch.setenv("THTH_THREADS_STATUS_POLL_SECONDS", "5")
    assert threads_mod.ThreadsAdapter(access_token="x", user_id="1").status_poll_seconds == 5.0
    monkeypatch.setenv("THTH_THREADS_STATUS_POLL_SECONDS", "abc")
    assert threads_mod.ThreadsAdapter(access_token="x", user_id="1").status_poll_seconds == 20.0
    monkeypatch.setenv("THTH_THREADS_STATUS_POLL_SECONDS", "999")
    assert threads_mod.ThreadsAdapter(access_token="x", user_id="1").status_poll_seconds == 60.0


# ---- core を通して（inflight と原稿） ------------------------------------

def _factory(base_url):
    return lambda _cfg, _token: fake.adapter(base_url)


def test_core_ERRORならinflightを解いて原稿はapprovedのまま_runsにpublish_failed_remote_error(isolated_account_factory):
    account = isolated_account_factory(production=True)
    write_queue_file(account["queue_dir"], "a.md")
    state_dir = accounts_mod.state_dir_for(account["name"])
    script = fake.Script(publish=[(500, None)], status=["ERROR"])
    with fake.serve(script) as base_url:
        result = core.throw_once(account["name"], production_flag=True,
                                 adapter_factory=_factory(base_url), now=NOW)
    assert result.action == "post" and result.exit_code == 1
    assert inflight_mod.read(state_dir) is None
    qf = queuefile.parse(os.path.join(account["queue_dir"], "a.md"))
    assert qf.get("status") == "approved" and not qf.get("post_id")
    rows = [r for r in runs_mod.read_runs(state_dir) if r["action"] == "post"]
    assert rows[-1]["error"].startswith("publish_failed_remote_error")
    assert rows[-1]["remote_state"] == "error"


def test_core_問い合わせ失敗ならinflightが残り最後の問い合わせ結果が写る(isolated_account_factory):
    account = isolated_account_factory(production=True)
    write_queue_file(account["queue_dir"], "a.md")
    state_dir = accounts_mod.state_dir_for(account["name"])
    script = fake.Script(publish=[(500, None)], status=[503])
    with fake.serve(script) as base_url:
        result = core.throw_once(account["name"], production_flag=True,
                                 adapter_factory=_factory(base_url), now=NOW)
    assert result.action == "inflight"
    left = inflight_mod.read(state_dir)
    assert left["remote_state"] == "query_failed" and left["remote_checked_at"]
    assert left["container_id"] == fake.CONTAINER_ID
    # 本文そのものは inflight に書かない（指紋だけ）。
    assert "本文です" not in str(left)
    assert len(left["text_fingerprint"]) == 64


def test_core_FINISHEDで再試行して出たら書き戻してrunsにretried(isolated_account_factory, tmp_path):
    pair = init_git_pair(tmp_path, seed_content=make_queue_text(), seed_name="a.md")
    account = isolated_account_factory(repo_dir=pair["work"], production=True)
    state_dir = accounts_mod.state_dir_for(account["name"])
    script = fake.Script(publish=[(500, None), ("ok", "780")], status=["FINISHED"])
    lines = []
    with fake.serve(script) as base_url:
        result = core.throw_once(account["name"], production_flag=True,
                                 adapter_factory=_factory(base_url), now=NOW, log=lines.append)
    assert result.exit_code == 0 and result.post_id == "780"
    assert inflight_mod.read(state_dir) is None
    ok = [r for r in runs_mod.read_runs(state_dir) if r["status"] == "ok" and r["action"] == "post"]
    assert ok[-1]["remote_state"] == "retried"
    assert any("1 回だけ再試行" in line for line in lines)
    assert script.publish_calls == 2
