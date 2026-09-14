"""front-matter への注入（セキュリティ監査 2026-09-14・P1-3／P1-4）。

**見つかり方**: front-matter は 1 行 1 項目の平たい `key: value`。`thth revoke
--reason` も `thth approve --by`（`THTH_ACTOR`）も、受け取った値をそのまま
`key: <値>` の 1 行にして書いていた。値に改行が入っていれば**そこから先は別の
行**になり、あとから書かれた行が後勝ちで効く——`--reason $'x\\nstatus: approved'`
で、**取り消したはずの原稿が承認済みに戻る**。同じ口を、媒体が返した `post_id`
も通る（`thth/core.py` の書き戻し）。

**直し方**: `writeback.set_front_matter_fields()` の入口で鍵と値を検査し、
改行・復帰・NUL があれば `ValueError` で断る（1 文字も書かない）。`post_id` は
それに加えて `postid.is_usable()` と制御文字なしを公開の直後に確かめ、外れたら
**`publish_ambiguous` と同じ扱い**（inflight を残す・再投稿しない）。
"""
from __future__ import annotations

import datetime
import os
from pathlib import Path

import pytest

from tests.conftest import run_thth, write_queue_file
from tests.test_atlas_review import REL, setup_pair
from thth import accounts as accounts_mod
from thth import core
from thth import inflight as inflight_mod
from thth import queuefile
from thth import writeback
from thth.adapters.base import PublishResult

NOW = datetime.datetime.fromisoformat("2026-09-09T10:00:00+09:00")

# 「別の行」を作る値。`\n` のあとが front-matter の行として効いてしまう。
注入 = "うっかり\nstatus: approved\napproved_by: attacker"


# ------------------------------------------------------------ 書き込みの入口

def test_set_front_matter_fieldsは改行入りの値を書かずに断る(tmp_path):
    path = tmp_path / "a.md"
    write_queue_file(str(tmp_path), "a.md")
    before = path.read_text(encoding="utf-8")

    with pytest.raises(ValueError) as e:
        writeback.set_front_matter_fields(str(path), {"revoked_reason": 注入})
    assert "改行" in str(e.value)
    # **1 文字も書いていない。**
    assert path.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("値", ["x\ny: z", "x\ry: z", "x\x00y"])
def test_改行復帰NULのどれでも断る(tmp_path, 値):
    path = tmp_path / "a.md"
    write_queue_file(str(tmp_path), "a.md")
    before = path.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        writeback.set_front_matter_fields(str(path), {"approved_by": 値})
    assert path.read_text(encoding="utf-8") == before


def test_鍵も検査する(tmp_path):
    path = tmp_path / "a.md"
    write_queue_file(str(tmp_path), "a.md")
    with pytest.raises(ValueError):
        writeback.set_front_matter_fields(str(path), {"x\nstatus": "approved"})


def test_普通の値はこれまでどおり書ける(tmp_path):
    path = tmp_path / "a.md"
    write_queue_file(str(tmp_path), "a.md")
    writeback.set_front_matter_fields(str(path), {"approved_by": "masaru", "post_id": None})
    fm = queuefile.parse(str(path)).front_matter
    assert fm["approved_by"] == "masaru"
    assert fm.get("post_id") in ("", None)


# ------------------------------------------------------------ CLI（revoke・approve）

def test_revokeは改行入りのreasonをloudに断りfront_matterを変えない(
        tmp_path, isolated_account_factory):
    pair, account, path = setup_pair(tmp_path, isolated_account_factory)
    before = path.read_text(encoding="utf-8")

    proc = run_thth(["revoke", str(path), "--by", "masaru", "--reason", 注入])

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "改行" in proc.stderr
    # front-matter は 1 行も変わっていない（承認済みのまま・注入行も無い）。
    after = path.read_text(encoding="utf-8")
    assert after == before
    assert "approved_by: attacker" not in after


def test_revokeは改行入りのbyをloudに断る(tmp_path, isolated_account_factory):
    pair, account, path = setup_pair(tmp_path, isolated_account_factory)
    before = path.read_text(encoding="utf-8")
    proc = run_thth(["revoke", str(path), "--by", 注入])
    assert proc.returncode == 2 and "改行" in proc.stderr
    assert path.read_text(encoding="utf-8") == before


def test_approveは改行入りのTHTH_ACTORをloudに断り承認しない(
        tmp_path, isolated_account_factory):
    from tests.conftest import init_git_pair, make_queue_text

    pair = init_git_pair(tmp_path, seed_content=make_queue_text({"status": "draft"}))
    isolated_account_factory(repo_dir=pair["work"], production=True, quiet_hours=None)
    path = Path(pair["work"]) / REL

    first = run_thth(["approve", str(path)])
    digests = [line.split(": ", 1)[1].strip() for line in first.stdout.splitlines()
               if line.startswith("digest: ")]
    assert digests, first.stdout + first.stderr
    bundle = [line.split(": ", 1)[1].strip() for line in first.stdout.splitlines()
              if line.startswith("bundle digest: ") or line.startswith("digest: ")][-1]

    proc = run_thth(["approve", str(path), "--confirm", bundle],
                    env={"THTH_ACTOR": 注入})
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "改行" in proc.stderr
    text = path.read_text(encoding="utf-8")
    assert "status: draft" in text
    assert "approved_by: attacker" not in text


# ------------------------------------------------------------ 媒体が返す post_id

class 改行入りのidを返す媒体:
    """偽の Mastodon（乗っ取られた口・間に入った proxy）。**本物は叩かない。**"""

    def __init__(self, post_id: str):
        self.post_id = post_id
        self.calls = []

    def publish(self, post, **kw):
        self.calls.append(post)
        return PublishResult(self.post_id, None, NOW.isoformat())


@pytest.mark.parametrize("悪いid", [
    "12345\nstatus: approved",
    "12345\rstatus: approved",
    "12345\x00",
    "12345\tstatus: approved",
])
def test_媒体が改行入りのidを返したら書き戻さずinflightを残す(
        tmp_path, isolated_account_factory, 悪いid):
    pair, account, path = setup_pair(tmp_path, isolated_account_factory)
    spy = 改行入りのidを返す媒体(悪いid)

    result = core.throw_once(account["name"], production_flag=True,
                             adapter_factory=lambda *_: spy, now=NOW)

    assert result.exit_code == 1
    assert result.action == "inflight"
    # front-matter は書き換わっていない（`status: approved` のまま・注入行も無い）。
    text = path.read_text(encoding="utf-8")
    assert "status: posted" not in text
    assert "approved_by: attacker" not in text
    # inflight が残っている＝次の実行は止まる。
    state_dir = accounts_mod.state_dir_for(account["name"])
    assert inflight_mod.read(state_dir) is not None

    # **次の run は再投稿しない**（1 回しか publish を呼ばない）。
    second = core.throw_once(account["name"], production_flag=True,
                             adapter_factory=lambda *_: spy, now=NOW)
    assert second.action == "inflight"
    assert len(spy.calls) == 1


def test_まともなidはこれまでどおり書き戻される(tmp_path, isolated_account_factory):
    pair, account, path = setup_pair(tmp_path, isolated_account_factory)
    spy = 改行入りのidを返す媒体("18001234567890")
    result = core.throw_once(account["name"], production_flag=True,
                             adapter_factory=lambda *_: spy, now=NOW)
    assert result.exit_code == 0, result.message
    assert "post_id: 18001234567890" in path.read_text(encoding="utf-8")
    assert inflight_mod.read(accounts_mod.state_dir_for(account["name"])) is None


def test_ATURIのpost_idは通る(tmp_path, isolated_account_factory):
    """Bluesky の `at://…/…` は `/` と `:` を含むが**書ける**（弾かない）。"""
    pair, account, path = setup_pair(tmp_path, isolated_account_factory)
    uri = "at://did:plc:abc123/app.bsky.feed.post/3kabc"
    spy = 改行入りのidを返す媒体(uri)
    result = core.throw_once(account["name"], production_flag=True,
                             adapter_factory=lambda *_: spy, now=NOW)
    assert result.exit_code == 0, result.message
    assert f"post_id: {uri}" in path.read_text(encoding="utf-8")
