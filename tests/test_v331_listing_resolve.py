"""3.3.1 §3 の後段: container の無い媒体（Mastodon・Bluesky・X）の自己解決。

自分の最近の投稿を公開時刻の前後 10 分で引き、本文の指紋が一致する投稿が
**ちょうど 1 件**なら書き戻して解く。**0 件なら解かない**——出ていないと
言い切れない媒体がある（本文を変えて返す・索引が遅れる）。2 件でも決めない。
"""
from __future__ import annotations

import datetime
import os

from tests.conftest import init_git_pair, make_queue_text
from thth import accounts as accounts_mod
from thth import core
from thth import inflight as inflight_mod
from thth import queuefile
from thth import runs as runs_mod
from thth.adapters import base as adapter_base

NOW = datetime.datetime.fromisoformat("2026-09-09T10:00:00+09:00")
BODY = "## mastodon\n\n本文です。\n"
NEAR = "2026-09-09T01:02:00.000Z"
FAR = "2026-09-09T00:40:00.000Z"


class ListOnlyAdapter(adapter_base.Adapter):
    """container を持たない媒体の偽物。公開は「分からない」で終わり、一覧だけ返す。"""

    CAPABILITIES = frozenset({"recent_posts"})

    def __init__(self, *, rows=None, fail=False):
        self.rows = rows or []
        self.fail = fail
        self.publish_calls = 0
        self.list_calls = 0

    def publish(self, post, *, dry_run, on_container_created=None, before_publish=None):
        self.publish_calls += 1
        return adapter_base.PublishResult(None, None, "2026-09-09T10:00:00+09:00",
                                          error="公開失敗: timed out",
                                          failure="publish_ambiguous")

    def recent_posts(self, *, limit=25):
        self.list_calls += 1
        if self.fail:
            raise adapter_base.AdapterError("fake_list_failed")
        return list(self.rows)


def _row(post_id, text, ts):
    return {"post_id": post_id, "timestamp": ts, "url": None, "text": text, "topic": None}


def _stuck(isolated_account_factory, tmp_path):
    seed = make_queue_text(body=BODY, media="mastodon")
    pair = init_git_pair(tmp_path, seed_content=seed, seed_name="a.md")
    account = isolated_account_factory(repo_dir=pair["work"], production=True,
                                       media="mastodon", instance="https://mastodon.example")
    state_dir = accounts_mod.state_dir_for(account["name"])
    first = ListOnlyAdapter()
    result = core.throw_once(account["name"], production_flag=True,
                             adapter_factory=lambda c, t: first, now=NOW)
    assert result.action == "inflight"
    left = inflight_mod.read(state_dir)
    assert left["container_id"] is None and left["text_fingerprint"]
    return account, state_dir


def _again(account, adapter):
    return core.throw_once(account["name"], production_flag=True,
                           adapter_factory=lambda c, t: adapter, now=NOW)


def test_一覧に1件あれば書き戻して解き_出し直さない(isolated_account_factory, tmp_path):
    account, state_dir = _stuck(isolated_account_factory, tmp_path)
    adapter = ListOnlyAdapter(rows=[_row("m-1", "本文です。", NEAR),
                                    _row("m-2", "本文です。", FAR),
                                    _row("m-3", "別の本文", NEAR)])
    result = _again(account, adapter)
    assert adapter.publish_calls == 0                 # 2 度出していない
    assert inflight_mod.read(state_dir) is None
    qf = queuefile.parse(os.path.join(account["queue_dir"], "a.md"))
    assert qf.get("status") == "posted" and qf.get("post_id") == "m-1"
    rows = runs_mod.read_runs(state_dir)
    assert any(r.get("inflight_resolution") == "listing_located" and r["post_id"] == "m-1"
               for r in rows)
    assert len(inflight_mod.archives(state_dir)) == 1
    assert result.action in ("none", "post")


def test_空白や改行を詰め直された本文も同じと見る(isolated_account_factory, tmp_path):
    account, state_dir = _stuck(isolated_account_factory, tmp_path)
    adapter = ListOnlyAdapter(rows=[_row("m-4", "\n本文です。  \n", NEAR)])
    _again(account, adapter)
    assert inflight_mod.read(state_dir) is None


def test_0件なら解かない(isolated_account_factory, tmp_path):
    account, state_dir = _stuck(isolated_account_factory, tmp_path)
    adapter = ListOnlyAdapter(rows=[_row("m-2", "本文です。", FAR)])
    result = _again(account, adapter)
    assert result.action == "inflight" and result.error == "inflight_unresolved"
    assert adapter.publish_calls == 0
    left = inflight_mod.read(state_dir)
    assert left["remote_state"] == "listing_none"
    assert inflight_mod.archives(state_dir) == []


def test_2件なら決めない(isolated_account_factory, tmp_path):
    account, state_dir = _stuck(isolated_account_factory, tmp_path)
    adapter = ListOnlyAdapter(rows=[_row("m-5", "本文です。", NEAR), _row("m-6", "本文です。", NEAR)])
    result = _again(account, adapter)
    assert result.action == "inflight" and adapter.publish_calls == 0
    assert inflight_mod.read(state_dir)["remote_state"] == "listing_many"


def test_一覧が引けなければ解かない(isolated_account_factory, tmp_path):
    account, state_dir = _stuck(isolated_account_factory, tmp_path)
    adapter = ListOnlyAdapter(fail=True)
    result = _again(account, adapter)
    assert result.action == "inflight" and adapter.publish_calls == 0
    assert inflight_mod.read(state_dir)["remote_state"] == "listing_failed"


def test_指紋の無い古いinflightは訊かずに従前どおり止まる(isolated_account_factory, tmp_path):
    account, state_dir = _stuck(isolated_account_factory, tmp_path)
    record = inflight_mod.read(state_dir)
    record.pop("text_fingerprint")
    inflight_mod.clear(state_dir)
    inflight_mod.write(state_dir, **{k: record[k] for k in (
        "file", "started", "container_id", "post_id", "body_hash", "approved_fingerprint")})
    adapter = ListOnlyAdapter(rows=[_row("m-1", "本文です。", NEAR)])
    result = _again(account, adapter)
    assert result.action == "inflight" and result.error is None
    assert adapter.list_calls == 0

