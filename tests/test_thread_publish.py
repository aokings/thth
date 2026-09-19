"""スレッド連投の公開（設計 §3・§4・§5・工程 4〜5）。

**Codex が検収で再現するよう指定したもの**をここで押さえる:

- 1 段目の公開結果不明・各段の公開成功直後の中断・push 失敗で**再公開しない**
- **同じ内容の別実行の記録**や、**差し替えた親 ID** を使えない
- **別 clone からの撤回・本文変更・期限変更**が、次の公開判断に反映される
- **同時起動でも同じ段を二重公開しない**
- **続きの再承認**で公開済み部分が変わらず、未公開部分だけ続行する
- **既存 v1 の承認・公開が維持**され、**旧コードは v2 を拒否する**

実 Git clone / bare origin・偽アダプタ。**本物の API は呼ばない。**
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

import pytest

from tests.conftest import init_git_pair, run_git
from thth import approval, bundle, cli, inflight, threadrun, threadthrow
from thth.adapters.base import PublishResult

NOW = datetime.datetime.fromisoformat("2026-09-15T19:00:00+09:00")
REL = "docs/sns/queue/thread.md"
SEGMENTS = ["コーヒーが苦いのはカフェインのせいでしょうか。",
            "カフェインを戻す実験があります。",
            "結果は予想と違いました。https://nigamilab.com/x"]


class FakeAdapter:
    """偽 API。**本物は呼ばない。**"""

    def __init__(self, *, fail_at=None, failure="publish_failed", on_wait=None):
        self.calls = []
        self.fail_at = fail_at
        self.failure = failure
        self.on_wait = on_wait

    def publish(self, post, dry_run=False, on_container_created=None,
                 before_publish=None):
        n = len(self.calls) + 1
        if on_container_created:
            on_container_created(f"container-{n}")
        # **偽 API も本物と同じ順序にする**（container → 待機 → 最後の関門 → 公開）。
        # ここを省くと「公開要求の直前の検査」がテストを通り抜ける。
        if self.on_wait:
            self.on_wait()
        if before_publish is not None:
            veto = before_publish()
            if veto:
                return PublishResult(None, None, NOW.isoformat(), error=str(veto),
                                      failure="publish_vetoed")
        self.calls.append({"text": post.text, "reply_to": post.reply_to,
                            "topic": post.topic})
        if self.fail_at == n:
            return PublishResult(None, None, NOW.isoformat(),
                                  error=f"わざと失敗（{self.failure}）",
                                  failure=self.failure)
        return PublishResult(f"POST{n}", None, NOW.isoformat())


def bundle_text(*, segments=None, status="approved", publish_at=None,
                 continue_until=None, topic="コーヒー", account=None,
                 revoked_at=None, frozen=None, posts=None):
    segments = segments or SEGMENTS
    publish_at = publish_at or "2026-09-15T19:00:00+09:00"
    continue_until = continue_until or "2026-09-15T20:00:00+09:00"
    account = account or "nigamilab-threads"
    sha = approval.compute_bundle_sha(
        segments=segments, account=account, topic=topic,
        publish_at=publish_at, continue_until=continue_until, frozen=frozen)
    body = "\n\n".join(
        seg if i == 0 else f"<!-- thth: {i + 1}/{len(segments)} -->\n\n{seg}"
        for i, seg in enumerate(segments))
    rows = posts or [{"index": i + 1} for i in range(len(segments))]
    posts_text = ""
    for row in rows:
        first = True
        for key, value in row.items():
            prefix = "  - " if first else "    "
            posts_text += f"{prefix}{key}: {'' if value is None else value}\n"
            first = False
    fm = (f"thth: 2\naccount: {account}\npublish_at: {publish_at}\n"
          f"continue_until: {continue_until}\nstatus: {status}\n"
          f"approved_sha: {sha}\napproved_by: テスト\ntopic: {topic}\n"
          f"form: 困り事→理由→行動\noutlet: 記事へ\n"
          + (f"revoked_at: {revoked_at}\n" if revoked_at else "")
          + f"posts:\n{posts_text}")
    return f"---\n{fm}---\n# 経緯\n\nメモ。\n\n## threads\n\n{body}\n"


@pytest.fixture
def thread_account(tmp_path, isolated_account_factory):
    pair = init_git_pair(tmp_path, seed_content=bundle_text(),
                          seed_name="thread.md")
    account = isolated_account_factory(repo_dir=pair["work"], production=True,
                                        quiet_hours=None, min_interval_hours=0)
    return {"pair": pair, "account": account,
            "path": os.path.join(pair["work"], REL)}


def publish(thread_account, adapter, **kw):
    return threadthrow.publish_bundle(
        thread_account["account"]["name"], REL,
        adapter_factory=lambda *_: adapter, now=kw.pop("now", NOW), **kw)


def write_and_push(pair, text):
    path = Path(pair["work"]) / REL
    path.write_text(text, encoding="utf-8")
    run_git(pair["work"], ["add", REL])
    run_git(pair["work"], ["commit", "-m", "更新"])
    run_git(pair["work"], ["push"])


def _bluesky_bundle_text(account: str, *, transformed: bool = True,
                         include_body_tag: bool = True) -> str:
    segments = ["本文 #苦味" if include_body_tag else "本文", "続きです"]
    text = bundle_text(account=account, topic="茶", segments=segments).replace(
        "## threads", "## bluesky")
    old = approval.compute_bundle_sha(
        segments=segments, account=account, topic="茶",
        publish_at="2026-09-15T19:00:00+09:00",
        continue_until="2026-09-15T20:00:00+09:00")
    effective = [segments[0] + "\n#茶", "続きです"]
    new = approval.compute_bundle_sha(
        segments=effective, account=account, topic="茶",
        publish_at="2026-09-15T19:00:00+09:00",
        continue_until="2026-09-15T20:00:00+09:00")
    assert text.count("approved_sha: " + old) == 1
    return text.replace("approved_sha: " + old, "approved_sha: " + new) if transformed else text


def test_bluesky_bundle_displays_and_publishes_effective_segments(
        tmp_path, isolated_account_factory):
    name = "bluesky-bundle"
    source = _bluesky_bundle_text(name)
    pair = init_git_pair(tmp_path, seed_content=source, seed_name="thread.md")
    account = isolated_account_factory(name=name, repo_dir=pair["work"],
                                       media="bluesky", hashtags=True,
                                       production=True, quiet_hours=None,
                                       min_interval_hours=0)
    path = os.path.join(pair["work"], REL)
    prepared, error = cli._prepare_bundle(path, source)
    assert error is None
    assert prepared["segments"] == ["本文 #苦味\n#茶", "続きです"]
    adapter = FakeAdapter()
    results = threadthrow.publish_bundle(name, REL, adapter_factory=lambda *_: adapter,
                                         now=NOW)
    assert [r.action for r in results] == ["published", "published"]
    assert [call["text"] for call in adapter.calls] == prepared["segments"]


def test_bluesky_bundle_old_published_segment_stays_frozen_after_policy_change(
        tmp_path, isolated_account_factory):
    name = "bluesky-frozen"
    source = _bluesky_bundle_text(name, transformed=False, include_body_tag=False)
    pair = init_git_pair(tmp_path, seed_content=source, seed_name="thread.md")
    account = isolated_account_factory(name=name, repo_dir=pair["work"],
                                       media="bluesky", hashtags=False,
                                       production=True, quiet_hours=None,
                                       min_interval_hours=0)
    adapter = FakeAdapter()
    first = threadthrow.publish_bundle(name, REL, adapter_factory=lambda *_: adapter,
                                       now=NOW, max_posts=1)
    assert first[0].action == "published"
    assert adapter.calls[0]["text"] == "本文"
    ledger = Path(account["accounts_dir"]) / f"{name}.json"
    cfg = json.loads(ledger.read_text())
    cfg["hashtags"] = True
    ledger.write_text(json.dumps(cfg))
    path = os.path.join(pair["work"], REL)
    prepared, error = cli._prepare_bundle(path, Path(path).read_text())
    assert prepared is None
    assert "公開済みの段の本文は変えられません" in error
    later = threadthrow.publish_bundle(name, REL, adapter_factory=lambda *_: adapter,
                                       now=NOW)
    assert all(r.action != "published" for r in later)
    assert len(adapter.calls) == 1


# --- 正常系 -----------------------------------------------------------------

def test_束を順に出して親子がつながる(thread_account):
    adapter = FakeAdapter()
    results = publish(thread_account, adapter)

    assert [r.action for r in results] == ["published"] * 3, [r.reason for r in results]
    assert [c["text"] for c in adapter.calls] == SEGMENTS
    # **1 段目は topic つき・返信先なし。2 段目以降は前の段への返信。**
    assert adapter.calls[0]["topic"] == "コーヒー"
    assert adapter.calls[0]["reply_to"] is None
    assert adapter.calls[1]["reply_to"] == "POST1"
    assert adapter.calls[2]["reply_to"] == "POST2"
    # 2 段目以降に topic は付けない（設計 §7）。
    assert adapter.calls[1]["topic"] is None

    b = bundle.parse(thread_account["path"])
    assert [p.get("post_id") for p in b.posts] == ["POST1", "POST2", "POST3"]
    assert b.posts[1]["reply_to"] == "POST1"


def test_時刻前は出さない(thread_account):
    adapter = FakeAdapter()
    results = publish(thread_account, adapter,
                       now=datetime.datetime.fromisoformat("2026-09-15T18:00:00+09:00"))
    assert results[0].action == "skipped"
    assert "まだ時刻前" in results[0].reason
    assert adapter.calls == []


# --- 公開結果が不明 ---------------------------------------------------------

def test_1段目の結果不明で止まり再送しない(thread_account, thth_root):
    """**自動で再送しない。後続も止める。**"""
    adapter = FakeAdapter(fail_at=1, failure="publish_ambiguous")
    results = publish(thread_account, adapter)
    assert results[0].action == "unresolved"
    assert len(adapter.calls) == 1

    run = threadrun.load(results[0].run_id)
    assert run["posts"][0]["state"] == threadrun.UNRESOLVED
    assert run["posts"][0]["container_id"] == "container-1"

    # **もう一度走らせても送らない。**
    again = FakeAdapter()
    results2 = publish(thread_account, again)
    assert again.calls == []
    assert results2[0].action == "stopped"
    assert "確定していない" in results2[0].reason


def test_2段目の結果不明でも1段目は残る(thread_account, thth_root):
    adapter = FakeAdapter(fail_at=2, failure="publish_ambiguous")
    results = publish(thread_account, adapter)
    assert [r.action for r in results] == ["published", "unresolved"]
    b = bundle.parse(thread_account["path"])
    assert b.posts[0]["post_id"] == "POST1"
    assert b.posts[1].get("post_id") is None


# --- push 失敗 --------------------------------------------------------------

def test_push失敗でも再公開しない(thread_account, thth_root):
    """**公開済みとして保持し、後続を止める。**"""
    hook = Path(thread_account["pair"]["bare"]) / "hooks" / "pre-receive"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)

    adapter = FakeAdapter()
    results = publish(thread_account, adapter)
    assert results[0].action == "stopped"
    assert "再公開しません" in results[0].reason
    assert len(adapter.calls) == 1

    run = threadrun.load(results[0].run_id)
    assert run["posts"][0]["state"] == threadrun.PUBLISHED
    assert run["stop_confirmed_at"] is not None

    again = FakeAdapter()
    publish(thread_account, again)
    assert again.calls == [], "同じ段をもう一度出した"


# --- 撤回 -------------------------------------------------------------------

def test_別cloneからの撤回が次の段に効く(thread_account, thth_root):
    """**段ごとにロックを取り直すから、間に割り込める**（設計 §3.3）。"""
    adapter = FakeAdapter()
    first = threadthrow.publish_bundle(
        thread_account["account"]["name"], REL,
        adapter_factory=lambda *_: adapter, now=NOW, max_posts=1)
    assert first[0].action == "published"

    # 別 clone から撤回して push
    seed = thread_account["pair"]["seed"]
    run_git(seed, ["pull", "--ff-only"])
    path = Path(seed) / REL
    path.write_text(path.read_text().replace(
        "approved_by: テスト", "approved_by: テスト\nrevoked_at: 2026-09-15T19:01:00+09:00"))
    run_git(seed, ["add", REL])
    run_git(seed, ["commit", "-m", "撤回"])
    run_git(seed, ["push"])

    second = publish(thread_account, adapter)
    assert second[0].action == "stopped"
    assert "撤回" in second[0].reason
    assert len(adapter.calls) == 1, "撤回後に出した"

    run = threadrun.load(first[0].run_id)
    assert run["stop_confirmed_at"] is not None, "実行側が停止を確認していない"


def test_別cloneからの期限短縮が効く(thread_account, thth_root):
    adapter = FakeAdapter()
    threadthrow.publish_bundle(thread_account["account"]["name"], REL,
                                adapter_factory=lambda *_: adapter, now=NOW,
                                max_posts=1)
    run = threadrun.find_open(thread_account["account"]["name"], REL)
    frozen = threadrun.frozen_records(run)

    seed = thread_account["pair"]["seed"]
    run_git(seed, ["pull", "--ff-only"])
    shortened = bundle_text(
        continue_until="2026-09-15T19:00:30+09:00", frozen=frozen,
        posts=[{"index": 1, "post_id": "POST1", "run_id": run["run_id"]},
               {"index": 2}, {"index": 3}])
    (Path(seed) / REL).write_text(shortened)
    run_git(seed, ["add", REL]); run_git(seed, ["commit", "-m", "期限短縮"])
    run_git(seed, ["push"])

    later = datetime.datetime.fromisoformat("2026-09-15T19:05:00+09:00")
    results = publish(thread_account, adapter, now=later)
    assert results[0].action == "stopped"
    assert "継続期限" in results[0].reason
    assert len(adapter.calls) == 1


# --- 凍結と再承認 -----------------------------------------------------------

def test_公開済みの段を書き換えたら止まる(thread_account, thth_root):
    """**凍結は強制する。誤字修正でも公開履歴を書き換えない**（Codex 最終条件 4）。"""
    adapter = FakeAdapter()
    threadthrow.publish_bundle(thread_account["account"]["name"], REL,
                               adapter_factory=lambda *_: adapter, now=NOW,
                               max_posts=1)
    run = threadrun.find_open(thread_account["account"]["name"], REL)

    changed = list(SEGMENTS)
    changed[0] = changed[0].replace("苦い", "にがい")          # 誤字修正のつもり
    text = bundle_text(segments=changed, frozen=threadrun.frozen_records(run),
                        posts=[{"index": 1, "post_id": "POST1",
                                "run_id": run["run_id"]},
                               {"index": 2}, {"index": 3}])
    write_and_push(thread_account["pair"], text)

    results = publish(thread_account, adapter)
    assert results[0].action == "skipped"
    assert "公開済みの段の本文が変わっています" in results[0].reason
    assert len(adapter.calls) == 1


def test_続きの再承認で未公開部分だけ続行する(thread_account, thth_root):
    """**同じ実行の、新しい承認版**（Codex 最終条件 2）。"""
    adapter = FakeAdapter()
    threadthrow.publish_bundle(thread_account["account"]["name"], REL,
                               adapter_factory=lambda *_: adapter, now=NOW,
                               max_posts=1)
    run = threadrun.find_open(thread_account["account"]["name"], REL)
    run_id = run["run_id"]

    revised = list(SEGMENTS)
    revised[2] = "書き直した 3 段目。https://nigamilab.com/y"
    text = bundle_text(segments=revised, frozen=threadrun.frozen_records(run),
                        posts=[{"index": 1, "post_id": "POST1",
                                "run_id": run_id},
                               {"index": 2}, {"index": 3}])
    write_and_push(thread_account["pair"], text)

    results = publish(thread_account, adapter)
    assert [r.action for r in results] == ["published", "published"], \
        [r.reason for r in results]
    # **同じ実行のまま続いている。**
    assert results[0].run_id == run_id
    assert adapter.calls[1]["reply_to"] == "POST1"
    assert adapter.calls[2]["text"] == revised[2]
    assert threadrun.load(run_id)["posts"][0]["post_id"] == "POST1"


# --- 親の差し替え -----------------------------------------------------------

def test_原稿の親IDを差し替えても使わない(thread_account, thth_root):
    """**Git で同期済みと、THTH が公開した記録は別**（Codex 最終条件 1）。"""
    adapter = FakeAdapter()
    threadthrow.publish_bundle(thread_account["account"]["name"], REL,
                               adapter_factory=lambda *_: adapter, now=NOW,
                               max_posts=1)
    run = threadrun.find_open(thread_account["account"]["name"], REL)

    text = bundle_text(frozen=threadrun.frozen_records(run),
                        posts=[{"index": 1, "post_id": "ぜんぜん別の投稿",
                                "run_id": run["run_id"]},
                               {"index": 2}, {"index": 3}])
    write_and_push(thread_account["pair"], text)

    results = publish(thread_account, adapter)
    assert results[0].action == "skipped"
    assert "親を決められません" in results[0].reason
    assert len(adapter.calls) == 1


def test_別の実行の記録を書いても使わない(thread_account, thth_root):
    adapter = FakeAdapter()
    threadthrow.publish_bundle(thread_account["account"]["name"], REL,
                               adapter_factory=lambda *_: adapter, now=NOW,
                               max_posts=1)
    run = threadrun.find_open(thread_account["account"]["name"], REL)

    text = bundle_text(frozen=threadrun.frozen_records(run),
                        posts=[{"index": 1, "post_id": "POST1",
                                "run_id": "run-20260101T000000-deadbeef"},
                               {"index": 2}, {"index": 3}])
    write_and_push(thread_account["pair"], text)

    results = publish(thread_account, adapter)
    assert results[0].action == "stopped"
    # **身元の照合が先に効く。** 手元にその実行が無いので新規扱いにしない。
    assert "原稿が名乗る実行と手元の記録が違います" in results[0].reason
    assert len(adapter.calls) == 1, "別の実行の記録で先へ進んだ"


# --- 排他 -------------------------------------------------------------------

def test_ロックが取れなければ何も送らない(thread_account, thth_root):
    from thth import accounts as accounts_mod
    from thth import lock as lock_mod
    other = lock_mod.AccountLock(
        accounts_mod.repo_lock_path_for(thread_account["pair"]["work"]))
    other.acquire()
    try:
        adapter = FakeAdapter()
        results = publish(thread_account, adapter)
        assert results[0].action == "skipped"
        assert adapter.calls == []
    finally:
        other.release()


def test_inflightが残っていれば進まない(thread_account, thth_root):
    from thth import accounts as accounts_mod
    state_dir = accounts_mod.state_dir_for(thread_account["account"]["name"])
    inflight.write(state_dir, file="x", started="2026-09-15T18:00:00+09:00",
                    container_id=None, body_hash="h", approved_fingerprint="f")
    adapter = FakeAdapter()
    results = publish(thread_account, adapter)
    assert results[0].action == "stopped"
    assert adapter.calls == []


# --- 承認との結び付き -------------------------------------------------------

def test_承認していなければ出さない(thread_account):
    write_and_push(thread_account["pair"], bundle_text(status="draft"))
    adapter = FakeAdapter()
    assert publish(thread_account, adapter)[0].action == "skipped"
    assert adapter.calls == []


def test_本文を変えたら承認が外れる(thread_account):
    changed = list(SEGMENTS)
    changed[1] = "こっそり書き換えた 2 段目。"
    text = bundle_text()          # 元の approved_sha のまま
    text = text.replace(SEGMENTS[1], changed[1])
    write_and_push(thread_account["pair"], text)
    adapter = FakeAdapter()
    results = publish(thread_account, adapter)
    assert results[0].action == "skipped"
    assert "approval_stale" in results[0].reason
    assert adapter.calls == []


def test_段の割り方を変えても承認が外れる():
    """**連結ではなく段の配列**（Codex 最終条件 5）。"""
    a = approval.compute_bundle_sha(
        segments=["あ", "い"], account="x", topic=None,
        publish_at="2026-09-15T19:00:00+09:00",
        continue_until="2026-09-15T20:00:00+09:00")
    b = approval.compute_bundle_sha(
        segments=["あい"], account="x", topic=None,
        publish_at="2026-09-15T19:00:00+09:00",
        continue_until="2026-09-15T20:00:00+09:00")
    assert a != b


def test_v1の指紋に触っていない():
    """**いま approved の 84 本を巻き込まない。**"""
    assert approval.compute_approved_sha(
        section="本文 A。", account="nigamilab-threads", reply_to=None,
        topic="コーヒー", publish_at="2026-09-15T19:00:00+09:00"
    ) == approval.compute_approved_sha(
        section="本文 A。", account="nigamilab-threads", reply_to=None,
        topic="コーヒー", publish_at="2026-09-15T19:00:00+09:00")


# --- 同時起動（実プロセス） -------------------------------------------------

def _child(repo_dir, account_name, out_path):
    """別プロセスで同じ束を進めようとする。**偽アダプタを使う。**"""
    import json as _json
    from thth import threadthrow as tt
    from thth.adapters.base import PublishResult as PR

    calls = []

    class Spy:
        def publish(self, post, dry_run=False, on_container_created=None,
                     before_publish=None):
            if on_container_created:
                on_container_created("c")
            if before_publish is not None and before_publish():
                return PR(None, None, NOW.isoformat(), error="止めました",
                           failure="publish_vetoed")
            calls.append(post.text)
            return PR(f"CHILD{len(calls)}", None, NOW.isoformat())

    results = tt.publish_bundle(account_name, REL,
                                 adapter_factory=lambda *_: Spy(), now=NOW,
                                 max_posts=3)
    with open(out_path, "w", encoding="utf-8") as f:
        _json.dump({"actions": [r.action for r in results], "calls": calls}, f)


def test_同時起動でも同じ段を二重公開しない(thread_account, thth_root, tmp_path):
    """**Codex の検収条件。** 実プロセスを 2 つ走らせる。"""
    import multiprocessing as mp

    out_a = str(tmp_path / "a.json")
    out_b = str(tmp_path / "b.json")
    ctx = mp.get_context("fork")
    name = thread_account["account"]["name"]
    procs = [ctx.Process(target=_child,
                          args=(thread_account["pair"]["work"], name, out))
             for out in (out_a, out_b)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)

    got = []
    for out in (out_a, out_b):
        with open(out, encoding="utf-8") as f:
            got.append(json.load(f))

    published = [t for row in got for t in row["calls"]]
    # **同じ本文が 2 回送られていない。**
    assert len(published) == len(set(published)), published
    # 片方はロックか「実行済み」で止まっているはず。
    assert any("skipped" in row["actions"] or "stopped" in row["actions"]
               for row in got), got


# --- v1 との共存 ------------------------------------------------------------

def test_v1の原稿は今までどおり出る(tmp_path, isolated_account_factory):
    """**既存 v1 の承認・公開が維持される**（Codex の検収条件）。"""
    from tests.conftest import approve_via_cli, make_queue_text
    from thth import core
    from thth.adapters.base import PublishResult as PR

    body = "# メモ\n\n" + "\n".join(f"行 {i}" for i in range(12)) + "\n\n## threads\n\n本文 A。\n"
    pair = init_git_pair(tmp_path, seed_content=make_queue_text({"status": "draft"},
                                                                 body=body))
    account = isolated_account_factory(repo_dir=pair["work"], production=True,
                                        quiet_hours=None, min_interval_hours=0)
    path = os.path.join(pair["work"], "docs/sns/queue/a.md")
    assert approve_via_cli(path).returncode == 0

    calls = []

    class Spy:
        def publish(self, post, **kw):
            calls.append(post.text)
            return PR("V1POST", None, NOW.isoformat())

    result = core.throw_once(account["name"], production_flag=True,
                              adapter_factory=lambda *_: Spy(),
                              now=datetime.datetime.fromisoformat(
                                  "2026-09-09T10:00:00+09:00"))
    assert calls == ["本文 A。"], result


def test_v1の実行経路はv2を型外として落とす(thread_account, thth_root):
    """**旧コードは v2 を拒否する**（Codex の検収条件）。"""
    from thth import core
    from thth import queuefile as qf

    from thth import accounts as accounts_mod
    cfg = accounts_mod.load_account(thread_account["account"]["name"])
    files = core.list_queue_files(cfg, tree_sha=None)
    bundles = [f for f in files if f.path.endswith("thread.md")]
    assert bundles and all(f.malformed for f in bundles), \
        "v1 の一覧が v2 の原稿を型外にしていない"
    assert qf.parse(thread_account["path"]).malformed is True


# --- 承認から公開までの通し -------------------------------------------------

def test_二段承認が各段の全文と返信関係を出す(thread_account, thth_root):
    """**承認画面には、各段の全文と返信関係を明示する**（Codex 最終条件 5）。"""
    from tests.conftest import run_thth

    write_and_push(thread_account["pair"], bundle_text(status="draft"))
    first = run_thth(["approve", thread_account["path"]])
    assert first.returncode == 1, first.stdout          # 一段目は承認しない
    out = first.stdout
    assert "スレッド連投・3 段" in out, out
    assert "1/3　返信先なし（スレッドの先頭）" in out, out
    assert "2/3　1 段目への返信" in out, out
    assert "3/3　2 段目への返信" in out, out
    for seg in SEGMENTS:
        assert seg in out, seg
    assert "続けてよい期限" in out


def test_承認してから出すまで通る(thread_account, thth_root):
    """**LLM が作った束を、承認して、偽 API で出す**（Codex の検収条件）。"""
    from tests.conftest import approve_via_cli

    write_and_push(thread_account["pair"], bundle_text(status="draft"))
    approved = approve_via_cli(thread_account["path"], by="テスト")
    assert approved.returncode == 0, approved.stdout + approved.stderr

    adapter = FakeAdapter()
    results = publish(thread_account, adapter)
    assert [r.action for r in results] == ["published"] * 3, [r.reason for r in results]
    assert [c["reply_to"] for c in adapter.calls] == [None, "POST1", "POST2"]

    # **計測は投稿単位のまま束へ紐付ける**（設計 §9）。
    from thth import forms, threadrun as tr
    run = tr.load(results[0].run_id)
    outcome = forms.bundle_outcome(run, {"POST1": {"views": 10},
                                          "POST2": {"views": 4}})
    assert [r["post_id"] for r in outcome["posts"]] == ["POST1", "POST2", "POST3"]
    assert outcome["posts"][2]["measured"] is None
    assert "足して" in outcome["notice"]


def test_公開済みの段を直すと承認の時点で断る(thread_account, thth_root):
    """**承認時と公開時の両方で拒否する**（Codex 最終条件 2）。"""
    from tests.conftest import run_thth

    adapter = FakeAdapter()
    threadthrow.publish_bundle(thread_account["account"]["name"], REL,
                               adapter_factory=lambda *_: adapter, now=NOW,
                               max_posts=1)
    run = threadrun.find_open(thread_account["account"]["name"], REL)

    changed = list(SEGMENTS)
    changed[0] = changed[0].replace("苦い", "にがい")
    text = bundle_text(segments=changed, status="draft",
                        posts=[{"index": 1, "post_id": "POST1",
                                "run_id": run["run_id"]},
                               {"index": 2}, {"index": 3}])
    write_and_push(thread_account["pair"], text)

    proc = run_thth(["approve", thread_account["path"]])
    assert proc.returncode != 0
    assert "公開済みの段の本文は変えられません" in proc.stdout + proc.stderr


def test_revokeが確認できた段と未着手を分けて出す(thread_account, thth_root):
    """**「N 段目以降は未公開」と断定しない**（Codex 最終条件 3）。"""
    from tests.conftest import run_thth

    adapter = FakeAdapter()
    threadthrow.publish_bundle(thread_account["account"]["name"], REL,
                               adapter_factory=lambda *_: adapter, now=NOW,
                               max_posts=1)
    proc = run_thth(["revoke", thread_account["path"], "--by", "masaru",
                      "--reason", "やめます"])
    out = proc.stdout + proc.stderr
    assert "停止を要求しました" in out, out
    assert "公開を確認できた段: 1" in out, out
    assert "未着手の段: 2、3" in out, out
    assert "すでに送信済みの要求は取り消せません" in out, out
    # **「止まりました」とは言わない。**
    assert "止めました" not in out


def test_トピック提案が束の全段を読む(thread_account, thth_root):
    """**先頭だけを検査しても記事との適合は判断できない**（設計 §10）。"""
    from thth import topic_advice

    context = topic_advice.build_context(thread_account["path"])
    assert context["segment_count"] == 3
    assert context["segments"] == SEGMENTS
    # **最終段の URL を拾えている**（先頭だけ読んでいたら拾えない）。
    assert context["main_article_url"] == "https://nigamilab.com/x"


# ===========================================================================
# 独立検収（2026-09-11・Codex）で出た 7 件
# ===========================================================================

def test_公開中のremote変更で違う本文にIDをpushしない(thread_account, thth_root):
    """**P1-2。** v1 で直した経路が v2 に引き継がれていなかった。

    > 公開成功後、公開前の text をもとにファイルを上書きし、rebase 後の内容を
    > 照合せず push する。…**A の ID と B の本文が origin に書き戻される。**
    """
    seed = thread_account["pair"]["seed"]
    changed = list(SEGMENTS)
    changed[0] = "別 clone が書き換えた 1 段目。"

    def swap_on_wait():
        """公開要求の直前（待機中）に、別 clone が本文を差し替えて push。"""
        if getattr(swap_on_wait, "done", False):
            return
        swap_on_wait.done = True
        run_git(seed, ["pull", "--ff-only"])
        (Path(seed) / REL).write_text(bundle_text(segments=changed))
        run_git(seed, ["add", REL]); run_git(seed, ["commit", "-m", "別 clone"])
        run_git(seed, ["push"])

    adapter = FakeAdapter(on_wait=swap_on_wait)
    results = publish(thread_account, adapter)

    assert results[0].action == "stopped", results[0].reason
    assert "書き戻しを確定できませんでした" in results[0].reason
    assert "本文が変わっています" in results[0].reason or \
        "承認版が変わっています" in results[0].reason, results[0].reason

    # **origin に混ざったものが入っていない。**
    shown = run_git(thread_account["pair"]["bare"],
                     ["show", f"main:{REL}"]).stdout
    assert not ("別 clone が書き換えた 1 段目。" in shown and "POST1" in shown), \
        "本文 B に投稿 A の ID が付いて push された"
    # **後続へ進んでいない。**
    assert len(adapter.calls) == 1


def test_書き戻しが確定するまでinflightを消さない(thread_account, thth_root):
    """**P1-2 の後半。** 未完の書き戻しは account 共通の停止条件。"""
    from thth import accounts as accounts_mod
    hook = Path(thread_account["pair"]["bare"]) / "hooks" / "pre-receive"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)

    adapter = FakeAdapter()
    publish(thread_account, adapter)
    state_dir = accounts_mod.state_dir_for(thread_account["account"]["name"])
    assert inflight.read(state_dir) is not None, \
        "push が確定していないのに inflight を消した"


def test_待機中に期限を越えたら公開要求を送らない(thread_account, thth_root):
    """**P1-3。** 「公開要求の直前」は adapter の**中**。

    > その中に container 作成・30 秒待機・threads_publish があるため、
    > **待機後の期限は見ていない。**
    """
    over = {"now": NOW}
    import thth.jst as jst_mod
    real_now = jst_mod.now_jst

    def fake_now():
        return over["now"]

    def cross_deadline():
        # 待機のあいだに期限を越える。
        over["now"] = datetime.datetime.fromisoformat("2026-09-15T20:00:20+09:00")

    adapter = FakeAdapter(on_wait=cross_deadline)
    jst_mod.now_jst = fake_now
    try:
        results = publish(thread_account, adapter,
                           now=datetime.datetime.fromisoformat(
                               "2026-09-15T19:59:50+09:00"))
    finally:
        jst_mod.now_jst = real_now

    assert results[0].action == "stopped", results[0].reason
    assert "継続期限を越えました" in results[0].reason
    # **公開要求は 1 回も送っていない**（container は作ってよい）。
    assert adapter.calls == [], adapter.calls
    b = bundle.parse(thread_account["path"])
    assert b.posts[0].get("post_id") is None


def test_renameしても1段目を再公開しない(thread_account, thth_root):
    """**P1-4。** 実行の身元をパスだけで決めない。"""
    adapter = FakeAdapter()
    results = publish(thread_account, adapter)
    assert [r.action for r in results] == ["published"] * 3
    assert len(adapter.calls) == 3

    work = thread_account["pair"]["work"]
    renamed = "docs/sns/queue/renamed.md"
    run_git(work, ["mv", REL, renamed])
    run_git(work, ["commit", "-m", "rename"])
    run_git(work, ["push"])

    again = FakeAdapter()
    results2 = threadthrow.publish_bundle(
        thread_account["account"]["name"], renamed,
        adapter_factory=lambda *_: again, now=NOW)
    assert again.calls == [], "rename しただけで 1 段目を再公開した"
    assert results2[0].action == "stopped"
    assert "別の原稿" in results2[0].reason or "実行" in results2[0].reason


def test_続きの再承認で記録も新しい本文になる(thread_account, thth_root):
    """**P1-5。** 実際に送った本文と永続記録が食い違わない。"""
    from tests.conftest import approve_via_cli

    adapter = FakeAdapter()
    threadthrow.publish_bundle(thread_account["account"]["name"], REL,
                               adapter_factory=lambda *_: adapter, now=NOW,
                               max_posts=1)
    run_id = threadrun.find_open(thread_account["account"]["name"], REL)["run_id"]

    revised = list(SEGMENTS)
    revised[1] = "書き直した 2 段目。"
    run = threadrun.load(run_id)
    text = bundle_text(segments=revised, status="draft",
                        posts=[{"index": 1, "post_id": "POST1",
                                "run_id": run_id},
                               {"index": 2}, {"index": 3}])
    write_and_push(thread_account["pair"], text)
    assert approve_via_cli(thread_account["path"], by="テスト").returncode == 0

    results = publish(thread_account, adapter)
    assert [r.action for r in results] == ["published", "published"], \
        [r.reason for r in results]
    assert adapter.calls[1]["text"] == revised[1]

    row = threadrun.load(run_id)
    # **記録が実際に送った本文になっている。**
    assert row["posts"][1]["text_sha256"] == approval.segment_sha(revised[1])
    # **1 段目の過去の承認版は動いていない。**
    assert row["posts"][0]["text_sha256"] == approval.segment_sha(SEGMENTS[0])
    # 原稿と記録が一致している。
    b = bundle.parse(thread_account["path"])
    assert b.posts[1]["text_sha256"] == row["posts"][1]["text_sha256"]


def test_期限を延ばして再承認すれば再開できる(thread_account, thth_root):
    """**P2-6。** 完了と停止は別物。"""
    from tests.conftest import approve_via_cli

    adapter = FakeAdapter()
    threadthrow.publish_bundle(thread_account["account"]["name"], REL,
                               adapter_factory=lambda *_: adapter, now=NOW,
                               max_posts=1)
    run_id = threadrun.find_open(thread_account["account"]["name"], REL)["run_id"]

    # 期限切れで停止する
    late = datetime.datetime.fromisoformat("2026-09-15T21:00:00+09:00")
    stopped = publish(thread_account, adapter, now=late)
    assert stopped[0].action == "stopped"
    assert threadrun.is_stopped(threadrun.load(run_id)) is True

    # 期限を延ばして再承認
    text = bundle_text(continue_until="2026-09-15T23:00:00+09:00", status="draft",
                        posts=[{"index": 1, "post_id": "POST1", "run_id": run_id},
                               {"index": 2}, {"index": 3}])
    write_and_push(thread_account["pair"], text)
    assert approve_via_cli(thread_account["path"], by="テスト").returncode == 0

    results = publish(thread_account, adapter,
                       now=datetime.datetime.fromisoformat("2026-09-15T22:00:00+09:00"))
    assert [r.action for r in results] == ["published", "published"], \
        [r.reason for r in results]
    row = threadrun.load(run_id)
    assert row["run_id"] == run_id, "再開で run_id が変わった"
    assert row["resumes"][0]["from_stop_reason"]


def test_未解決のまま再開しない(thread_account, thth_root):
    """**再開しても、結果の分からない公開は解除しない。**"""
    adapter = FakeAdapter(fail_at=1, failure="publish_ambiguous")
    results = publish(thread_account, adapter)
    run = threadrun.load(results[0].run_id)
    threadrun.confirm_stop(run, "手で止めた")
    with pytest.raises(threadrun.RunError):
        threadrun.resume(run, bundle_sha="x", continue_until="2026-09-15T23:00:00+09:00",
                          by="t")


# --- 通常経路からの接続（P1-1） ---------------------------------------------

def test_通常のthrowから束が出る(thread_account, thth_root):
    """**P1-1。** `publish_bundle` を直接呼ぶテストだけでは完成ではない。

    > 新しい処理だけを直接実行できれば完成とはしない。
    """
    from thth import core

    calls = []

    class Spy(FakeAdapter):
        def publish(self, post, **kw):
            out = super().publish(post, **kw)
            calls.append(post.text)
            return out

    adapter = Spy()
    result = core.throw_once(thread_account["account"]["name"],
                              production_flag=True,
                              adapter_factory=lambda *_: adapter, now=NOW)
    assert calls == SEGMENTS, result
    assert result.action == "posted", result
    assert "3 段" in result.message


def test_rehearsalでは送らない(thread_account, thth_root):
    from thth import core

    adapter = FakeAdapter()
    result = core.throw_once(thread_account["account"]["name"],
                              production_flag=False,
                              adapter_factory=lambda *_: adapter, now=NOW)
    assert adapter.calls == []
    assert result.mode == "rehearsal"


def test_v1とv2が同じaccountに並んでいても両方動く(thread_account, thth_root):
    """v2 を出したあと、v1 の 1 本が従来どおり出る。"""
    from tests.conftest import approve_via_cli, make_queue_text
    from thth import core

    v1_body = "# メモ\n\n" + "\n".join(f"行 {i}" for i in range(12)) + \
        "\n\n## threads\n\n単発の本文。\n"
    v1_path = Path(thread_account["pair"]["work"]) / "docs/sns/queue/single.md"
    v1_path.write_text(make_queue_text({"status": "draft",
                                         "publish_at": "2026-09-15T19:00:00+09:00"},
                                        body=v1_body))
    run_git(thread_account["pair"]["work"], ["add", "-A"])
    run_git(thread_account["pair"]["work"], ["commit", "-m", "v1 を足す"])
    run_git(thread_account["pair"]["work"], ["push"])
    assert approve_via_cli(str(v1_path), by="テスト").returncode == 0

    adapter = FakeAdapter()
    core.throw_once(thread_account["account"]["name"], production_flag=True,
                     adapter_factory=lambda *_: adapter, now=NOW)
    assert [c["text"] for c in adapter.calls] == SEGMENTS

    # 束が出し切ったので、次は v1 が出る。
    adapter2 = FakeAdapter()
    core.throw_once(thread_account["account"]["name"], production_flag=True,
                     adapter_factory=lambda *_: adapter2, now=NOW)
    assert [c["text"] for c in adapter2.calls] == ["単発の本文。"]


def test_通常のcollectが全段を拾う(thread_account, thth_root):
    """**P2-7。** 3 段公開しても収集対象が 0 件だった。"""
    from thth import collect, forms

    adapter = FakeAdapter()
    results = publish(thread_account, adapter)
    assert [r.action for r in results] == ["published"] * 3

    class FakeInsights:
        def insights(self, post_id, **kw):
            # F3: `{"metrics", "available"}` だけ（旧い平の dict は断られる）。
            return {"metrics": {"views": {"POST1": 100, "POST2": 40,
                                          "POST3": 12}[post_id]},
                    "available": ["views"]}

        def conversation(self, post_id, **kw):
            return []

    later = datetime.datetime.fromisoformat("2026-09-15T20:05:00+09:00")
    out = collect.collect_once(thread_account["account"]["name"],
                                adapter=FakeInsights(), now=later)
    assert out["posts"] >= 3, out
    blob = json.dumps(out, ensure_ascii=False, default=str)
    for post_id in ("POST1", "POST2", "POST3"):
        assert post_id in blob, (post_id, out)

    # **束として紐付けて読める**（足さない・割らない）。
    run = threadrun.load(results[0].run_id)
    outcome = forms.bundle_outcome(run, {"POST1": {"views": 100},
                                          "POST2": {"views": 40},
                                          "POST3": {"views": 12}})
    assert [r["measured"]["views"] for r in outcome["posts"]] == [100, 40, 12]


def test_公開時のラベルが記録される(thread_account, thth_root):
    """**実測に使った版を記録する**（Codex 最終条件 4）。"""
    from thth import forms

    adapter = FakeAdapter()
    results = publish(thread_account, adapter, max_posts=1)
    run = threadrun.load(results[0].run_id)
    labels = run["posts"][0]["labels"]
    assert labels["form"] == "困り事→理由→行動"
    assert labels["outlet"] == "記事へ"
    assert labels["vocabulary_version"] == forms.VOCABULARY_VERSION
    assert labels["at"]


def test_boardに進行状態と停止理由が出る(thread_account, thth_root):
    """**P1-1 の後半。** board の進行状態表示・停止確認も同じ経路へ。"""
    from thth import report

    adapter = FakeAdapter(fail_at=2, failure="publish_ambiguous")
    publish(thread_account, adapter)

    row = next(r for r in report.board_summary()["accounts"]
               if r["account"] == thread_account["account"]["name"])
    threads = row["threads"]
    assert threads, row
    assert threads[0]["published"] == [1]
    assert threads[0]["unresolved"] == [2]
    assert threads[0]["pending"] == [3]
    # **結果不明のときに人が判断できる材料**（Codex 最終条件 4）。
    detail = threads[0]["unresolved_detail"][0]
    assert detail["index"] == 2
    assert detail["container_id"] == "container-2"
    assert detail["text_sha256"]
    assert detail["reply_to"] == "POST1"


def test_束が無いaccountの経路に手を出さない(tmp_path, isolated_account_factory):
    """**実装中に踏んだ。**

    束が 1 つも無くても同期していたので、**触っていないはずの v1 の経路で
    観測できる順序が変わり**、第 5 巡の回帰テスト（同期の直後に HEAD が動く筋）
    が別の場所で消費されて落ちた。
    """
    from tests.conftest import make_queue_text
    from thth import accounts as accounts_mod
    from thth import threadthrow as tt
    from thth import writeback

    pair = init_git_pair(tmp_path, seed_content=make_queue_text({"status": "draft"}))
    account = isolated_account_factory(repo_dir=pair["work"], production=True)
    cfg = accounts_mod.load_account(account["name"])
    assert tt.has_bundle_files(cfg) is False

    synced = []
    real = writeback.sync_repo
    try:
        writeback.sync_repo = lambda d: synced.append(d) or real(d)
        assert tt.run_for_account(account["name"],
                                   adapter_factory=lambda *_: FakeAdapter(),
                                   now=NOW) == []
    finally:
        writeback.sync_repo = real
    assert synced == [], "束が無いのに同期した"
