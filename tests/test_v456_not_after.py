"""4.5.6 出さない期限（`not_after:`）の柵（masaru 10/23・構想 5.0 §4.6）。

関東が学校説明会・文化祭などの期日のある行事を配信し始める。予約が詰まって遅れたり口座が止まったり
（10/23 の kopicha は inflight で 1.5 時間止まった）しても、終わった行事・締め切った申し込みを告げない。

- 期限の前は出る・過ぎたら出ない（原稿は approved のまま・held の `not_after_passed`）
- 口座は止めない（期限切れの原稿を外して次の原稿へ進む）
- 公開の直前（container を作って待ったあと）に期限をまたいだら公開しない
- 連投（`thth: 2`）は根の `not_after` を見る（途中の段で切れたら残りは出さない）
- lint（形・publish_at より前は誤り・過ぎていれば警告）
- 指紋（期限の無い原稿の指紋は今までと同じ・期限を延ばせば承認し直し）
"""
from __future__ import annotations

import datetime
import json
import os
import time
import types
from pathlib import Path

import pytest

from tests.conftest import init_git_pair, make_queue_text, parse_verified, run_git
from tests.test_fake_api import _adapter, fake_threads_server
from tests.test_max_per_run import _init_git_pair_multi
from thth import accounts as accounts_mod
from thth import approval, cli, core, events_cli, healthcheck, inflight, jst, lint, queuefile
from thth import report as report_mod
from thth import runs as runs_mod
from thth import select as select_mod
from thth import threadrun, threadthrow
from thth.adapters import threads as threads_mod
from thth.adapters.base import PublishResult

NOW = datetime.datetime.fromisoformat("2026-09-09T10:00:00+09:00")
CFG = {"media": "threads", "quiet_hours": None, "min_interval_hours": 0, "stale_days": 7, "hashtags": False}


def _write(dir_path, name, text):
    path = os.path.join(str(dir_path), name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _select(files, now=NOW):
    return select_mod.select_one(files, account_name="nigamilab-threads", account_cfg=CFG,
                                 now=now, last_post_at=None, recent_texts=set())


def _fm(path):
    return queuefile.parse(path).front_matter


# ------------------------------------------------------------------ 指紋

def test_期限の無い原稿の指紋は今までと同じ():
    base = dict(section="本文です。", account="nigamilab-threads", reply_to=None, topic=None,
                publish_at="2026-09-09T08:00:00+09:00")
    before = approval.compute_approved_sha(**base)
    assert approval.compute_approved_sha(**base, not_after=None) == before
    assert approval.compute_approved_sha(**base, not_after="  ") == before
    # front-matter から抜いても同じ（`publish_options` が None を返す）。
    assert approval.publish_options({"publish_at": "2026-09-09T08:00:00+09:00"})["not_after"] is None
    assert approval.compute_approved_sha(**base, **approval.publish_options({})) == before
    # 束も同じ。
    bundle = dict(segments=["一", "二"], account="a", topic=None, publish_at="2026-09-09T08:00:00+09:00",
                  continue_until="2026-09-09T12:00:00+09:00")
    assert approval.compute_bundle_sha(**bundle, not_after=None) == approval.compute_bundle_sha(**bundle)


def test_期限は指紋に入り延ばせば承認し直し():
    base = dict(section="本文です。", account="nigamilab-threads", reply_to=None, topic=None,
                publish_at="2026-09-09T08:00:00+09:00")
    plain = approval.compute_approved_sha(**base)
    one = approval.compute_approved_sha(**base, not_after="2026-09-09T12:00:00+09:00")
    later = approval.compute_approved_sha(**base, not_after="2026-09-10T12:00:00+09:00")
    assert len({plain, one, later}) == 3
    # 表記の揺れ（秒の省略）は同じ指紋（publish_at と同じ正規化）。
    assert approval.compute_approved_sha(**base, not_after="2026-09-09T12:00+09:00") == one
    bundle = dict(segments=["一", "二"], account="a", topic=None, publish_at="2026-09-09T08:00:00+09:00",
                  continue_until="2026-09-09T12:00:00+09:00")
    assert approval.compute_bundle_sha(**bundle, not_after="2026-09-09T11:00:00+09:00") != \
        approval.compute_bundle_sha(**bundle)


def test_承認のあとに期限を延ばすとapproval_stale(tmp_path):
    text = make_queue_text({"not_after": "2026-09-09T12:00:00+09:00"})
    stretched = text.replace("not_after: 2026-09-09T12:00:00+09:00", "not_after: 2026-09-10T12:00:00+09:00")
    assert stretched != text
    qf = parse_verified(_write(tmp_path, "a.md", stretched))
    result = _select([qf])
    assert result.chosen is None
    assert [(os.path.basename(r.file), r.reason) for r in result.rejections] == [("a.md", "approval_stale")]


# ------------------------------------------------------------------ lint

def _lint(tmp_path, fm):
    path = _write(tmp_path, "l.md", make_queue_text({"status": "draft", **fm}))
    return lint.lint_file(path)


def test_lint_期限の形と前後と過ぎた警告(tmp_path, frozen_now_jst):
    frozen_now_jst(NOW)
    assert not any("not_after" in m for m in _lint(tmp_path, {}))
    bad = _lint(tmp_path, {"not_after": "2026-09-09 12:00"})
    assert any(m.startswith("not_after: 時刻として読めません") for m in bad)
    assert lint.reason_code(bad) == "invalid_not_after"
    before = _lint(tmp_path, {"publish_at": "2026-09-09T12:00:00+09:00", "not_after": "2026-09-09T11:00:00+09:00"})
    assert any(m.startswith("not_after: publish_at より前") for m in before)
    # 過ぎていれば警告だけ（承認はできる・出ない）。
    past = _lint(tmp_path, {"not_after": "2026-09-09T09:00:00+09:00"})
    notes = [m for m in past if "not_after" in m]
    assert notes and all(lint.is_warning(m) for m in notes)
    assert "2026-09-09 09:00 を過ぎています" in notes[0]
    assert not [m for m in _lint(tmp_path, {"not_after": "2026-09-09T11:00:00+09:00"}) if "not_after" in m]


# ------------------------------------------------------------------ select（期限の前・後・次へ）

def test_期限の前は出て過ぎたら出ない(tmp_path):
    qf = parse_verified(_write(tmp_path, "a.md", make_queue_text({"not_after": "2026-09-09T10:30:00+09:00"})))
    assert _select([qf]).chosen is qf
    # ちょうど期限の時刻はまだ出る（過ぎたら出さない）。
    assert _select([qf], now=datetime.datetime.fromisoformat("2026-09-09T10:30:00+09:00")).chosen is qf
    late = datetime.datetime.fromisoformat("2026-09-09T10:31:00+09:00")
    result = _select([qf], now=late)
    assert result.chosen is None
    assert [r.reason for r in result.rejections] == ["not_after_passed"]
    assert qf.path in result.needs_review
    items = select_mod.held_items(result, [qf], late)
    assert [(i["file"], i["category"], i["due"]) for i in items] == [("a.md", "not_after_passed", True)]
    code = select_mod.held_reason_code(items)
    assert code == "approved_but_held: not_after_passed 1"
    assert healthcheck.is_held_code(code) and healthcheck._reason_code_is_safe(code)


def test_期限切れを外して次の原稿へ進む(tmp_path):
    old = parse_verified(_write(tmp_path, "a.md", make_queue_text(
        {"publish_at": "2026-09-09T07:00:00+09:00", "not_after": "2026-09-09T09:00:00+09:00"},
        body="## threads\n\n土曜の説明会は 9 時までに受付です。\n")))
    nxt = parse_verified(_write(tmp_path, "b.md", make_queue_text(
        {"publish_at": "2026-09-09T08:00:00+09:00"}, body="## threads\n\n次の原稿です。\n")))
    result = _select([old, nxt])
    assert result.chosen is nxt


def test_期限の形が壊れた承認済みは要確認(tmp_path):
    qf = parse_verified(_write(tmp_path, "a.md", make_queue_text({"not_after": "あした"})))
    result = _select([qf])
    assert result.chosen is None
    assert [r.reason for r in result.rejections] == ["not_after_invalid"]
    assert qf.path in result.needs_review


# ------------------------------------------------------------------ throw（本物の select と偽 API）

def _account(isolated_account_factory, tmp_path, files, **kw):
    pair = _init_git_pair_multi(tmp_path, files)
    account = isolated_account_factory(repo_dir=pair["work"], production=True, min_interval_hours=0,
                                       max_per_run=kw.pop("max_per_run", 3), **kw)
    return pair, account


def test_throwは期限切れを出さず口座を止めずに次を出す(isolated_account_factory, tmp_path):
    files = {
        "a.md": make_queue_text({"publish_at": "2026-09-09T07:00:00+09:00",
                                 "not_after": "2026-09-09T09:00:00+09:00"}, body="## threads\n\n終わった行事\n"),
        "b.md": make_queue_text({"publish_at": "2026-09-09T08:00:00+09:00",
                                 "not_after": "2026-09-09T11:00:00+09:00"}, body="## threads\n\n期限の前\n"),
        "c.md": make_queue_text({"publish_at": "2026-09-09T09:00:00+09:00"}, body="## threads\n\n期限なし\n"),
    }
    pair, account = _account(isolated_account_factory, tmp_path, files)
    with fake_threads_server({"create": "ok", "publish": "ok"}) as base_url:
        result = core.throw_once(account["name"], production_flag=True,
                                 adapter_factory=lambda *_: _adapter(base_url), now=NOW)
    assert result.exit_code == 0
    queue = os.path.join(pair["work"], "docs", "sns", "queue")
    assert _fm(os.path.join(queue, "a.md"))["status"] == "approved"
    assert not _fm(os.path.join(queue, "a.md")).get("post_id")
    assert _fm(os.path.join(queue, "b.md"))["status"] == "posted"
    assert _fm(os.path.join(queue, "c.md"))["status"] == "posted"
    assert inflight.read(accounts_mod.state_dir_for(account["name"])) is None


def test_runの時刻で選んでも壁時計で過ぎていれば出さない(isolated_account_factory, tmp_path, frozen_now_jst):
    # 返信を出していた間・前の原稿を出していた間に期限を過ぎた（select は run の始めの時刻で選ぶ）。
    files = {
        "a.md": make_queue_text({"publish_at": "2026-09-09T08:00:00+09:00",
                                 "not_after": "2026-09-09T10:00:30+09:00"}, body="## threads\n\n期限の近い原稿\n"),
        "b.md": make_queue_text({"publish_at": "2026-09-09T09:00:00+09:00"}, body="## threads\n\n期限なし\n"),
    }
    # max_per_run 1: 出さなかった原稿は 1 本に数えない（同じ run で次の原稿が出る）。
    pair, account = _account(isolated_account_factory, tmp_path, files, max_per_run=1)
    frozen_now_jst(NOW + datetime.timedelta(minutes=2))
    with fake_threads_server({"create": "ok", "publish": "ok"}) as base_url:
        result = core.throw_once(account["name"], production_flag=True,
                                 adapter_factory=lambda *_: _adapter(base_url), now=NOW)
        created = len(base_url.handler_cls.create_params)
    assert result.exit_code == 0
    queue = os.path.join(pair["work"], "docs", "sns", "queue")
    assert _fm(os.path.join(queue, "a.md"))["status"] == "approved"
    assert _fm(os.path.join(queue, "b.md"))["status"] == "posted"
    assert created == 1   # a は container も作っていない
    runs = runs_mod.read_runs(accounts_mod.state_dir_for(account["name"]))
    assert any(r.get("file", "").endswith("a.md") and r.get("error") == "not_after_passed" for r in runs)


def test_公開の直前に期限をまたいだら公開せず次へ(isolated_account_factory, tmp_path, frozen_now_jst, monkeypatch):
    files = {
        "a.md": make_queue_text({"publish_at": "2026-09-09T08:00:00+09:00",
                                 "not_after": "2026-09-09T10:00:30+09:00"}, body="## threads\n\n締め切り間近\n"),
        "b.md": make_queue_text({"publish_at": "2026-09-09T09:00:00+09:00"}, body="## threads\n\n期限なし\n"),
    }
    pair, account = _account(isolated_account_factory, tmp_path, files)
    frozen_now_jst(NOW)
    # container を作ったあとの待ち（30 秒）で時計が期限をまたぐ。
    monkeypatch.setattr(threads_mod, "time", types.SimpleNamespace(
        sleep=lambda _s: frozen_now_jst(NOW + datetime.timedelta(minutes=1)),
        monotonic=time.monotonic, time=time.time))
    published = []
    with fake_threads_server({"create": "ok", "publish": "ok"}) as base_url:
        def factory(*_a):
            adapter = _adapter(base_url, wait_seconds=1)
            original = adapter._post

            def counting(path, params, *a, **k):
                if path.endswith("/threads_publish"):
                    published.append(params.get("creation_id"))
                return original(path, params, *a, **k)
            adapter._post = counting
            return adapter
        result = core.throw_once(account["name"], production_flag=True, adapter_factory=factory, now=NOW)
        created = len(base_url.handler_cls.create_params)
    queue = os.path.join(pair["work"], "docs", "sns", "queue")
    assert result.exit_code == 0
    assert created == 2 and len(published) == 1   # a は container まで・公開要求は b の 1 回だけ
    assert _fm(os.path.join(queue, "a.md"))["status"] == "approved"
    assert not _fm(os.path.join(queue, "a.md")).get("post_id")
    assert _fm(os.path.join(queue, "b.md"))["status"] == "posted"
    assert inflight.read(accounts_mod.state_dir_for(account["name"])) is None


# ------------------------------------------------------------------ 見える（schedule・queue・events）

def test_scheduleとeventsに期限切れが見える(isolated_account_factory, tmp_path, capsys, frozen_now_jst):
    frozen_now_jst(NOW)
    files = {"a.md": make_queue_text({"publish_at": "2026-09-09T07:00:00+09:00",
                                      "not_after": "2026-09-09T09:00:00+09:00"},
                                     body="## threads\n\n文化祭は 9 時まで\n")}
    _pair, account = _account(isolated_account_factory, tmp_path, files)
    rows = report_mod.schedule(account["name"], now=NOW)
    assert rows[0]["expired"] is True and rows[0]["not_after"] == "2026-09-09T09:00:00+09:00"
    cli.show_schedule(rows)
    assert "出さない期限を過ぎました" in capsys.readouterr().out
    payload = events_cli.build(account["name"], since="24h", now=NOW)
    mine = [e for e in payload["events"] if e["kind"] == "not_after_passed"]
    assert len(mine) == 1 and mine[0]["ref"] == "a.md"
    assert mine[0]["occurred_at"].startswith("2026-09-09T09:00")
    assert payload["by_kind"]["not_after_passed"] == 1
    summary = report_mod.queue_summary(account["name"], now=NOW)[account["name"]]
    assert {"file": "a.md", "reason": "not_after_passed"} in summary["next_rejections"]


def test_approveの1段目に期限を出す(isolated_account_factory, tmp_path, capsys, frozen_now_jst):
    frozen_now_jst(NOW)
    files = {"a.md": make_queue_text({"status": "draft", "publish_at": "2026-09-09T11:00:00+09:00",
                                      "not_after": "2026-09-09T12:00:00+09:00"}),
             "b.md": make_queue_text({"status": "draft", "publish_at": "2026-09-09T08:00:00+09:00",
                                      "not_after": "2026-09-09T09:00:00+09:00"})}
    pair, _account_cfg = _account(isolated_account_factory, tmp_path, files)
    queue = os.path.join(pair["work"], "docs", "sns", "queue")
    ok, _ = cli._prepare_one(os.path.join(queue, "a.md"))
    assert "出さない期限 2026-09-09 12:00" in ok["not_after_line"] and ok["warning"] is None
    late, _ = cli._prepare_one(os.path.join(queue, "b.md"))
    assert "期限を過ぎているので確定しても出ません" in late["warning"]
    cli._show_first_stage([ok], "x" * 12, as_json=False)
    assert "期限      : 出さない期限 2026-09-09 12:00" in capsys.readouterr().out


# ------------------------------------------------------------------ 連投（thth: 2）

REL = "docs/sns/queue/thread.md"
B_NOW = datetime.datetime.fromisoformat("2026-09-15T19:00:00+09:00")
SEGMENTS = ["説明会の案内 1。", "説明会の案内 2。", "説明会の案内 3。"]


class FakeAdapter:
    def __init__(self, on_wait=None):
        self.calls = []
        self.on_wait = on_wait

    def publish(self, post, dry_run=False, on_container_created=None, before_publish=None):
        if on_container_created:
            on_container_created(f"container-{len(self.calls) + 1}")
        if self.on_wait:
            self.on_wait(len(self.calls) + 1)
        if before_publish is not None:
            veto = before_publish()
            if veto:
                return PublishResult(None, None, B_NOW.isoformat(), error=str(veto), failure="publish_vetoed")
        self.calls.append(post.text)
        return PublishResult(f"POST{len(self.calls)}", None, B_NOW.isoformat())


def _bundle_text(not_after, publish_at="2026-09-15T19:00:00+09:00"):
    until = "2026-09-15T20:00:00+09:00"
    sha = approval.compute_bundle_sha(segments=SEGMENTS, account="nigamilab-threads", topic=None,
                                      publish_at=publish_at, continue_until=until, not_after=not_after)
    body = "\n\n".join(seg if i == 0 else f"<!-- thth: {i + 1}/{len(SEGMENTS)} -->\n\n{seg}"
                       for i, seg in enumerate(SEGMENTS))
    posts = "".join(f"  - index: {i + 1}\n" for i in range(len(SEGMENTS)))
    fm = (f"thth: 2\naccount: nigamilab-threads\npublish_at: {publish_at}\ncontinue_until: {until}\n"
          + (f"not_after: {not_after}\n" if not_after else "")
          + f"status: approved\napproved_sha: {sha}\napproved_by: テスト\nposts:\n{posts}")
    return f"---\n{fm}---\n# 経緯\n\nメモ。\n\n## threads\n\n{body}\n"


@pytest.fixture
def thread_account(tmp_path, isolated_account_factory):
    def make(not_after, publish_at="2026-09-15T19:00:00+09:00"):
        pair = init_git_pair(tmp_path / not_after.replace(":", ""),
                             seed_content=_bundle_text(not_after, publish_at), seed_name="thread.md")
        account = isolated_account_factory(repo_dir=pair["work"], production=True,
                                           quiet_hours=None, min_interval_hours=0)
        return pair, account
    return make


def test_連投は根の期限を過ぎたら候補にしない(thread_account):
    # 18:30 から出す予定で 18:50 まで（承認済みのまま予約が詰まって 19:00 になった）。
    _pair, account = thread_account("2026-09-15T18:50:00+09:00", publish_at="2026-09-15T18:30:00+09:00")
    cfg = accounts_mod.load_account(account["name"])
    assert threadthrow.due_bundles(cfg, now=B_NOW, tree_sha=None) == []
    _pair, account = thread_account("2026-09-15T19:30:00+09:00")
    cfg = accounts_mod.load_account(account["name"])
    assert threadthrow.due_bundles(cfg, now=B_NOW, tree_sha=None) == [REL]


def test_連投は途中の段で期限を過ぎたら残りを出さない(thread_account, frozen_now_jst):
    _pair, account = thread_account("2026-09-15T19:05:00+09:00")
    frozen_now_jst(B_NOW)

    def on_wait(n):
        # 2 段目の待ちのあいだに期限をまたぐ。
        if n == 2:
            frozen_now_jst(B_NOW + datetime.timedelta(minutes=10))
    adapter = FakeAdapter(on_wait=on_wait)
    results = threadthrow.publish_bundle(account["name"], REL, adapter_factory=lambda *_: adapter, now=B_NOW)
    assert adapter.calls == [SEGMENTS[0]]
    assert [r.action for r in results] == ["published", "stopped"]
    assert "出さない期限" in results[-1].reason
    run = threadrun.find_latest(account["name"], REL)
    assert threadrun.is_stopped(run)
    # 次の run では、期限を過ぎて止まった束は候補から外れる（口座を止めない）。
    cfg = accounts_mod.load_account(account["name"])
    later = B_NOW + datetime.timedelta(minutes=10)
    assert threadthrow.due_bundles(cfg, now=later, tree_sha=None) == []


# 監査（Fable 10/23）1: Threads の本文の原稿で公開が曖昧に失敗し、次の run の自己解決が FINISHED の
# 同じ container を公開する道。期限を過ぎていれば公開せず、inflight を解いて口座を先へ進める。
def test_自己解決のFINISHEDでも期限を過ぎていれば公開しない(isolated_account_factory, tmp_path, frozen_now_jst):
    from tests.conftest import init_git_pair, make_queue_text
    from tests.helpers import fake_threads_status as fake
    from thth import accounts as accounts_mod
    from thth import core
    from thth import inflight as inflight_mod
    from thth import runs as runs_mod
    start = datetime.datetime.fromisoformat("2026-09-09T10:00:00+09:00")
    later = datetime.datetime.fromisoformat("2026-09-09T11:00:00+09:00")
    text = make_queue_text({"not_after": "2026-09-09T10:30:00+09:00"})
    pair = init_git_pair(tmp_path, seed_content=text, seed_name="a.md")
    account = isolated_account_factory(repo_dir=pair["work"], production=True)
    state_dir = accounts_mod.state_dir_for(account["name"])
    factory = lambda base_url: (lambda _cfg, _token: fake.adapter(base_url))  # noqa: E731
    with fake.serve(fake.Script(publish=[(500, None)], status=[503])) as base_url:
        first = core.throw_once(account["name"], production_flag=True, adapter_factory=factory(base_url), now=start)
    assert first.action == "inflight"
    frozen_now_jst(later)
    script = fake.Script(publish=[("ok", "999")], status=["FINISHED"])
    with fake.serve(script) as base_url:
        core.throw_once(account["name"], production_flag=True, adapter_factory=factory(base_url), now=later)
    assert script.publish_calls == 0 and script.create_calls == 0
    assert inflight_mod.read(state_dir) is None
    rows = runs_mod.read_runs(state_dir)
    assert any(r.get("inflight_resolution") == "remote_finished_not_after"
               and r.get("error") == "not_after_passed" for r in rows)
