"""セキュリティ監査 2 回目（v2.0.1 候補・2026-09-14）で見つかった穴。

正本は `docs/検収_セキュリティ監査_v2.0.0_2026-09-14.md` の
「監査 2 回目」節と `docs/設計_v2.0.1_同席送信の採集_2026-09-14.md`。

ここに集めたのは**単発の経路にあった守りが、あとから足した経路に無かった**もの。

- **P1-1**: `thth: 2`（連投）の書き戻しが、媒体の返した `post_id` を素通しで
  `bundle.set_post_fields()` に渡していた（単発の `core._throw_chosen()` には
  検査がある）。改行入りの `id` で束の front-matter に任意の行が入り、以後その
  account の `run` が `sync_repo` で止まる。
- **P2-1**: 置き場の判定が「実在する git repo か」だけだったので、`repo_dir` が
  一時的に見えないと**黙って state へ転ぶ**（v2.0.0 では loud に断っていた）。
- **P2-3**: `thth send` が媒体の `post_id` を検査していなかった。
"""
from __future__ import annotations

import datetime
import os
from pathlib import Path

import pytest

from tests.conftest import init_git_pair, run_git
from thth import accounts as accounts_mod
from thth import bundle as bundle_mod
from thth import inflight as inflight_mod
from thth import threadrun, threadthrow
from thth.adapters.base import PublishResult

from tests.test_thread_publish import REL, SEGMENTS, bundle_text  # noqa: F401

NOW = datetime.datetime.fromisoformat("2026-09-15T19:00:00+09:00")

# 束の front-matter に「別の行」を作る値。`posts:` の段は字下げされているので、
# 改行のあとを字下げ無しで書けば **top-level の行**になる。
注入 = "POST1\nstatus: approved\napproved_by: attacker"


class 悪いidを返す媒体:
    """偽の媒体（乗っ取られた口・間に入った proxy）。**本物は叩かない。**"""

    def __init__(self, post_id):
        self.post_id = post_id
        self.calls = []

    def publish(self, post, dry_run=False, on_container_created=None,
                 before_publish=None):
        if on_container_created:
            on_container_created(f"container-{len(self.calls) + 1}")
        if before_publish is not None:
            veto = before_publish()
            if veto:
                return PublishResult(None, None, NOW.isoformat(), error=str(veto),
                                      failure="publish_vetoed")
        self.calls.append(post)
        return PublishResult(self.post_id, None, NOW.isoformat())


@pytest.fixture
def thread_pair(tmp_path, isolated_account_factory):
    pair = init_git_pair(tmp_path, seed_content=bundle_text(),
                          seed_name="thread.md")
    account = isolated_account_factory(repo_dir=pair["work"], production=True,
                                        quiet_hours=None, min_interval_hours=0)
    return pair, account, Path(pair["work"]) / REL


def _publish(account, adapter, **kw):
    return threadthrow.publish_bundle(
        account["name"], REL, adapter_factory=lambda *_: adapter,
        now=kw.pop("now", NOW), **kw)


# --------------------------------------------------------------- P1-1

@pytest.mark.parametrize("悪いid", [
    注入,
    "POST1\rstatus: approved",
    "POST1\x00",
    "POST1\tstatus: approved",
    "..",
    "P" * 400,
])
def test_P1_1_連投は書けないpost_idを書き戻さずinflightを残す(
        thread_pair, 悪いid):
    """**単発（`core.py`）と同じ扱い**——書き戻さない・再公開しない・inflight を残す。"""
    pair, account, path = thread_pair
    before = path.read_text(encoding="utf-8")
    spy = 悪いidを返す媒体(悪いid)

    results = _publish(account, spy)

    assert [r.action for r in results] == ["unresolved"], [r.reason for r in results]
    assert "post_id" in results[0].reason
    # **1 文字も書いていない**（front-matter に注入行が入っていない）。
    assert path.read_text(encoding="utf-8") == before
    assert "approved_by: attacker" not in path.read_text(encoding="utf-8")

    # inflight が残っている＝次の実行が止まる。
    state_dir = accounts_mod.state_dir_for(account["name"])
    assert inflight_mod.read(state_dir) is not None

    # **再公開しない**（2 回目の実行で publish を呼ばない）。
    again = 悪いidを返す媒体("POST2")
    results2 = _publish(account, again)
    assert again.calls == []
    assert results2[0].action == "stopped"

    # run にも「結果が確定していない」として残る。
    run = threadrun.load(results[0].run_id)
    assert run["posts"][0]["state"] == threadrun.UNRESOLVED


def test_P1_1_まともなidはこれまでどおり書き戻る(thread_pair):
    pair, account, path = thread_pair
    results = _publish(account, 悪いidを返す媒体("POST1"))
    # 1 段目だけ確かめる（同じ id を 3 段に返す偽媒体なので 2 段目は親と衝突しない）。
    assert results[0].action == "published", results[0].reason
    assert "post_id: POST1" in path.read_text(encoding="utf-8")


def test_P1_1_AT_URIのpost_idは連投でも通る(thread_pair):
    """Bluesky の `at://…` は `/` と `:` を含むが**書ける**（弾かない）。"""
    pair, account, path = thread_pair
    uri = "at://did:plc:abc123/app.bsky.feed.post/3kabc"
    results = _publish(account, 悪いidを返す媒体(uri))
    assert results[0].action == "published", results[0].reason
    assert f"post_id: {uri}" in path.read_text(encoding="utf-8")


def test_P1_1_set_post_fieldsの入口が改行入りの値を断る():
    """**検査は 1 か所**（`writeback.check_front_matter_field()`）。

    `threadthrow` を直しただけでは、`set_post_fields()` を呼ぶ別の口が
    増えたときに同じ穴が空く。**書く側の入口でも断る。**
    """
    text = bundle_text()
    for 値 in (注入, "x\ry: z", "x\x00y"):
        with pytest.raises(ValueError):
            bundle_mod.set_post_fields(text, 1, {"post_id": 値})
    with pytest.raises(ValueError):
        bundle_mod.set_post_fields(text, 1, {"post_id\nstatus": "x"})
    # まともな値はこれまでどおり書ける。
    assert "post_id: POST9" in bundle_mod.set_post_fields(
        text, 1, {"post_id": "POST9"})
