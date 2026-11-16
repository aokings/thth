"""段 12 の 1: seeds が本当に `sent/` に入るかを、はてなブログの記事と inflight の解きの道で見る（検収 段 12 の 4）。

SNS の不在の様態（throw）は `tests/test_register_stage12_posts.py`。ここは偽の adapter だけ（外に何も送らない）。
"""
from __future__ import annotations

import pytest

from tests.conftest import init_git_pair, make_queue_text
from tests.hatena_s1_flow_helpers import (ACCOUNT, NOW, FakeAdapter, approve, call, factory_for, fm_of,  # noqa: F401
                                          lane, world)
from tests.helpers import fake_threads_status as fake
from thth import accounts, cli, core, hatena_publish, inflight, sent

SEEDS = "claim:clm-bitter common:source:src-behrens"


def _throw(adapter):
    return core.throw_once(ACCOUNT, production_flag=True, adapter_factory=factory_for(adapter), now=NOW)


def test_はてなブログの記事を出すとsentの行にseedsを控える_承認は古くならない(lane):
    path = lane.queue(extra={"seeds": SEEDS})
    approve(path)
    assert _throw(FakeAdapter()).action == "post"
    (row,) = sent.records(lane.state)
    assert row["kind"] == "entry_create" and row["seeds"] == SEEDS.split()


class _Crash(Exception):
    pass


def test_はてなの記事をinflightから人がpublishedで解いてもseedsを控える(lane, monkeypatch):
    from tests.conftest import restore_real_hatena_adapters
    restore_real_hatena_adapters(monkeypatch)
    path = lane.queue(extra={"seeds": SEEDS})
    approve(path)
    adapter = FakeAdapter()
    with monkeypatch.context() as patched:
        patched.setattr(hatena_publish, "_record_outcome", lambda *a, **k: (_ for _ in ()).throw(_Crash()))
        with pytest.raises(_Crash):
            _throw(adapter)
    argv = ["inflight", ACCOUNT, "resolve", "--published", adapter.entry_id, "--by", "tester"]
    rc, out, err = call(argv)
    digest = [line.split(": ", 1)[1] for line in out.splitlines() if line.startswith("digest: ")][0]
    rc, out, err = call(argv + ["--confirm", digest])
    assert rc == 0, (out, err)
    (row,) = sent.records(lane.state)
    assert row["kind"] == "entry_create" and row["seeds"] == SEEDS.split()


def test_SNSの投稿をinflightから人がpublishedで解いてもseedsを控える(isolated_account_factory, tmp_path, capsys):
    text = make_queue_text()
    head, sep, rest = text.partition("\n")
    pair = init_git_pair(tmp_path, seed_content=head + sep + f"seeds: {SEEDS}\n" + rest, seed_name="a.md")
    account = isolated_account_factory(repo_dir=pair["work"], production=True)
    script = fake.Script(publish=[(500, None)], status=[503])
    with fake.serve(script) as base_url:
        result = core.throw_once(account["name"], production_flag=True,
                                 adapter_factory=lambda c, t: fake.adapter(base_url), now=NOW)
    assert result.action == "inflight"
    argv = ["inflight", account["name"], "resolve", "--published", "8001", "--by", "masaru"]
    assert cli.main(argv) == 1
    out = capsys.readouterr().out
    digest = next(line.split(": ", 1)[1] for line in out.splitlines() if line.startswith("digest: "))
    assert cli.main(argv + ["--confirm", digest]) == 0, capsys.readouterr()
    state = accounts.state_dir_for(account["name"])
    assert inflight.read(state) is None
    assert sent.read(state, "8001")["seeds"] == SEEDS.split()
