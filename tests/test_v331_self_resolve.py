"""3.3.1 §3: inflight が残っている run は、止まる前に 1 回だけ媒体に訊く。

決まれば解いて進み、決まらなければ従前どおり止まる（理由 `inflight_unresolved`）。
**自己解決から公開の要求は 1 度も飛ばない**——出ていないと決めたら select から
（承認の検査を全部通って）新しい container で出し直す。出ていたら書き戻すだけ。
"""
from __future__ import annotations

import datetime
import json
import os

from tests.conftest import init_git_pair, make_queue_text
from tests.helpers import fake_threads_status as fake
from thth import accounts as accounts_mod
from thth import core
from thth import healthcheck
from thth import inflight as inflight_mod
from thth import queuefile
from thth import runs as runs_mod
from thth import sent as sent_mod

NOW = datetime.datetime.fromisoformat("2026-09-09T10:00:00+09:00")
TEXT = "本文です。"
NEAR = "2026-09-09T01:00:30+0000"


def _factory(base_url):
    return lambda _cfg, _token: fake.adapter(base_url)


def _stuck(isolated_account_factory, tmp_path):
    """公開が 5xx・問い合わせも失敗で inflight が残った状態を本物の経路で作る。"""
    pair = init_git_pair(tmp_path, seed_content=make_queue_text(), seed_name="a.md")
    account = isolated_account_factory(repo_dir=pair["work"], production=True)
    state_dir = accounts_mod.state_dir_for(account["name"])
    first = fake.Script(publish=[(500, None)], status=[503])
    with fake.serve(first) as base_url:
        result = core.throw_once(account["name"], production_flag=True,
                                 adapter_factory=_factory(base_url), now=NOW)
    assert result.action == "inflight"
    assert inflight_mod.read(state_dir)["container_id"] == fake.CONTAINER_ID
    return account, state_dir, _queue_path(account)


def _again(account, script, **kwargs):
    lines = []
    with fake.serve(script) as base_url:
        result = core.throw_once(account["name"], adapter_factory=_factory(base_url),
                                 now=NOW, log=lines.append,
                                 **{"production_flag": True, **kwargs})
    return result, lines


def _queue_path(account):
    return os.path.join(account["queue_dir"], "a.md")


def test_ERRORなら解いて同じrunで新しいcontainerから出し直す(isolated_account_factory, tmp_path):
    account, state_dir, _ = _stuck(isolated_account_factory, tmp_path)
    script = fake.Script(publish=[("ok", "801")], status=["ERROR"])
    result, lines = _again(account, script)
    assert result.exit_code == 0 and result.post_id == "801"
    assert script.status_calls == 1                    # 問い合わせは 1 回
    assert script.create_calls == 1 and script.publish_calls == 1
    rows = runs_mod.read_runs(state_dir)
    resolved = [r for r in rows if r.get("inflight_resolution") == "remote_error"]
    assert resolved and resolved[-1]["error"] == "publish_failed_remote_error"
    assert len(inflight_mod.archives(state_dir)) == 1
    assert inflight_mod.read(state_dir) is None
    assert any("出ていません" in line for line in lines)


def test_FINISHEDなら公開せずに解いてselectからやり直す(isolated_account_factory, tmp_path):
    account, state_dir, _ = _stuck(isolated_account_factory, tmp_path)
    script = fake.Script(publish=[("ok", "802")], status=["FINISHED"])
    result, _ = _again(account, script)
    assert result.post_id == "802"
    # 古い container を公開しない（新しい container を作ってから 1 回だけ公開）。
    assert script.create_calls == 1 and script.publish_calls == 1
    rows = runs_mod.read_runs(state_dir)
    assert any(r.get("inflight_resolution") == "remote_finished"
               and r["error"] == "inflight_resolved_not_published" for r in rows)


def test_PUBLISHEDで一覧に1件あれば書き戻して解き_出し直さない(isolated_account_factory, tmp_path):
    account, state_dir, _ = _stuck(isolated_account_factory, tmp_path)
    script = fake.Script(publish=[("ok", "999")], status=["PUBLISHED"],
                         posts=[fake.post_row("803", TEXT, NEAR)])
    result, _ = _again(account, script)
    assert script.publish_calls == 0 and script.create_calls == 0   # 2 度出していない
    assert inflight_mod.read(state_dir) is None
    qf = queuefile.parse(_queue_path(account))
    assert qf.get("status") == "posted" and str(qf.get("post_id")) == "803"
    assert sent_mod.read(state_dir, "803")["text"].strip() == TEXT
    rows = runs_mod.read_runs(state_dir)
    ok = [r for r in rows if r.get("inflight_resolution") == "remote_published"]
    assert ok and ok[-1]["status"] == "ok" and ok[-1]["post_id"] == "803"
    # 解いたあとの run は出すものが無い（同じ原稿は posted）。
    assert result.action in ("none", "post")
    archived = json.load(open(inflight_mod.archives(state_dir)[-1], encoding="utf-8"))
    assert archived["resolved_by"] == "thth" and archived["post_id"] == "803"


def test_PUBLISHEDでも一覧に無ければ止まりinflight_unresolved(isolated_account_factory, tmp_path):
    account, state_dir, _ = _stuck(isolated_account_factory, tmp_path)
    script = fake.Script(status=["PUBLISHED"], posts=[])
    result, lines = _again(account, script)
    assert result.action == "inflight" and result.error == "inflight_unresolved"
    assert script.publish_calls == 0
    left = inflight_mod.read(state_dir)
    assert left["remote_state"] == "published_unlocated"
    assert any("thth inflight" in line and "resolve" in line for line in lines)


def test_問い合わせ失敗なら解かずに止まる(isolated_account_factory, tmp_path):
    account, state_dir, _ = _stuck(isolated_account_factory, tmp_path)
    script = fake.Script(status=[500])
    result, _ = _again(account, script)
    assert result.action == "inflight" and result.error == "inflight_unresolved"
    assert script.publish_calls == 0 and script.create_calls == 0
    assert inflight_mod.read(state_dir)["remote_state"] == "query_failed"
    assert inflight_mod.archives(state_dir) == []


def test_IN_PROGRESSなら解かずに止まる(isolated_account_factory, tmp_path):
    account, state_dir, _ = _stuck(isolated_account_factory, tmp_path)
    script = fake.Script(status=["IN_PROGRESS"])
    result, _ = _again(account, script)
    assert result.action == "inflight" and script.publish_calls == 0
    assert inflight_mod.read(state_dir)["remote_state"] == "in_progress"


def test_dry_runでは訊かない(isolated_account_factory, tmp_path):
    account, state_dir, _ = _stuck(isolated_account_factory, tmp_path)
    script = fake.Script(status=["ERROR"])
    result, _ = _again(account, script, production_flag=False)
    assert result.action == "inflight" and result.error is None
    assert script.status_calls == 0
    assert inflight_mod.read(state_dir) is not None


def test_出たと分かっているinflightには触らない(isolated_account_factory, tmp_path):
    account, state_dir, _ = _stuck(isolated_account_factory, tmp_path)
    inflight_mod.update(state_dir, post_id="804")
    script = fake.Script(status=["ERROR"])
    result, _ = _again(account, script)
    assert result.action == "inflight" and script.status_calls == 0
    assert inflight_mod.read(state_dir)["post_id"] == "804"


def test_原稿が公開時と違えば書き戻さずに止まる(isolated_account_factory, tmp_path):
    account, state_dir, path = _stuck(isolated_account_factory, tmp_path)
    text = open(path, encoding="utf-8").read().replace("本文です。", "書き換えた本文。")
    open(path, "w", encoding="utf-8").write(text)
    from tests.conftest import commit_and_push_path
    commit_and_push_path(path)
    script = fake.Script(status=["PUBLISHED"], posts=[fake.post_row("805", TEXT, NEAR)])
    result, _ = _again(account, script)
    assert result.action == "inflight" and result.error == "text_mismatch_before_writeback"
    left = inflight_mod.read(state_dir)
    assert left["remote_post_id"] == "805" and not left.get("post_id")
    assert script.publish_calls == 0


def test_inflight_unresolvedは死活通知の理由になり次の一手はthth_inflight_resolve(tmp_path):
    diag = healthcheck.diagnostic("acct", "fail", state_dir=str(tmp_path),
                                  reason="inflight_unresolved")
    assert diag.reason_code == "inflight_unresolved"
    assert diag.next_action_code == "resolve_inflight_by_hand"
    assert "thth inflight" in diag.next_action and "resolve" in diag.next_action
    assert "[inflight_unresolved]" in diag.reason
