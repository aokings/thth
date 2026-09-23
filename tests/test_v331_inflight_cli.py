"""3.3.1 §4: `thth inflight <account> [show|resolve] --by <名前>`（人の口）。

09-23 に masaru が `inflight.json` を手で消した操作の代わり。二段確認・控え
（`inflight.resolved-<日時>.json`）・変更ログ `inflight_resolved`。MCP には出さない。
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import os
import pathlib

from tests.conftest import init_git_pair, make_queue_text, write_queue_file
from tests.helpers import fake_threads_status as fake
from thth import accounts as accounts_mod
from thth import admin_log
from thth import cli
from thth import core
from thth import inflight as inflight_mod
from thth import inflight_cli
from thth import queuefile
from thth import runs as runs_mod

NOW = datetime.datetime.fromisoformat("2026-09-09T10:00:00+09:00")
REPO = pathlib.Path(__file__).resolve().parent.parent


def _stuck(isolated_account_factory, tmp_path, *, git=True):
    if git:
        pair = init_git_pair(tmp_path, seed_content=make_queue_text(), seed_name="a.md")
        account = isolated_account_factory(repo_dir=pair["work"], production=True)
    else:
        account = isolated_account_factory(production=True)
        write_queue_file(account["queue_dir"], "a.md")
    script = fake.Script(publish=[(500, None)], status=[503])
    with fake.serve(script) as base_url:
        result = core.throw_once(account["name"], production_flag=True,
                                 adapter_factory=lambda c, t: fake.adapter(base_url), now=NOW)
    assert result.action == "inflight"
    return account, accounts_mod.state_dir_for(account["name"])


def _cli(capsys, *argv):
    rc = cli.main(["inflight", *argv])
    out = capsys.readouterr()
    return rc, out.out, out.err


def _digest_from(out):
    return next(line.split(": ", 1)[1] for line in out.splitlines() if line.startswith("digest: "))


def test_showは中身を出すが本文とtokenとcontainer_idは出さない(isolated_account_factory, tmp_path, capsys):
    account, state_dir = _stuck(isolated_account_factory, tmp_path)
    rc, out, _ = _cli(capsys, account["name"], "show")
    assert rc == 0
    assert "a.md" in out and "container_id: あり" in out and "query_failed" in out
    assert "本文です" not in out and "fake-token" not in out and fake.CONTAINER_ID not in out
    rc, out, _ = _cli(capsys, account["name"], "show", "--json")
    info = json.loads(out)["inflight"]
    assert info["container_id_present"] is True and info["remote_state"] == "query_failed"
    assert info["post_id_present"] is False and info["self_resolvable"] is True


def test_showはinflightが無ければそう言う(isolated_account_factory, capsys):
    account = isolated_account_factory()
    rc, out, _ = _cli(capsys, account["name"])
    assert rc == 0 and "inflight はありません" in out


def test_resolve_not_publishedの往復_二段確認と控えと変更ログ(isolated_account_factory, tmp_path, capsys):
    account, state_dir = _stuck(isolated_account_factory, tmp_path, git=False)
    before = inflight_mod.read(state_dir)
    # 一段目: 何もしない。
    rc, out, _ = _cli(capsys, account["name"], "resolve", "--not-published", "--by", "masaru")
    assert rc == 1 and "解きません" in out
    assert inflight_mod.read(state_dir) == before
    code = _digest_from(out)
    # 二段目。
    rc, out, _ = _cli(capsys, account["name"], "resolve", "--not-published", "--by", "masaru",
                      "--confirm", code)
    assert rc == 0, out
    assert inflight_mod.read(state_dir) is None
    archives = inflight_mod.archives(state_dir)
    assert len(archives) == 1
    saved = json.loads(open(archives[0], encoding="utf-8").read())
    assert saved["resolved_by"] == "masaru" and saved["resolution"] == "human_not_published"
    assert saved["inflight"]["started"] == before["started"]
    assert os.stat(archives[0]).st_mode & 0o777 == 0o600
    rows, broken = admin_log.read(account=account["name"], event="inflight_resolved")
    assert len(rows) == 1 and rows[0]["by"] == "masaru" and not broken
    runs = [r for r in runs_mod.read_runs(state_dir) if r.get("inflight_resolution")]
    assert runs[-1]["inflight_resolution"] == "human_not_published"
    # 原稿は approved のまま（次の run が出し直す）。
    qf = queuefile.parse(os.path.join(account["queue_dir"], "a.md"))
    assert qf.get("status") == "approved"


def test_resolve_publishedは書き戻して解く(isolated_account_factory, tmp_path, capsys):
    account, state_dir = _stuck(isolated_account_factory, tmp_path)
    rc, out, _ = _cli(capsys, account["name"], "resolve", "--published", "8001", "--by", "masaru")
    assert rc == 1
    rc, out, err = _cli(capsys, account["name"], "resolve", "--published", "8001", "--by", "masaru",
                        "--confirm", _digest_from(out))
    assert rc == 0, err
    assert inflight_mod.read(state_dir) is None
    qf = queuefile.parse(os.path.join(account["queue_dir"], "a.md"))
    assert qf.get("status") == "posted" and str(qf.get("post_id")) == "8001"
    assert len(inflight_mod.archives(state_dir)) == 1
    rows, _ = admin_log.read(account=account["name"], event="inflight_resolved")
    assert rows[-1]["diff"]["resolution"] == [None, "human_published"]


def test_digestが違えば解かない_別のinflightには流用できない(isolated_account_factory, tmp_path, capsys):
    account, state_dir = _stuck(isolated_account_factory, tmp_path, git=False)
    rc, out, _ = _cli(capsys, account["name"], "resolve", "--not-published", "--by", "masaru")
    code = _digest_from(out)
    inflight_mod.update(state_dir, started="2026-09-09T11:00:00+09:00")
    rc, _, err = _cli(capsys, account["name"], "resolve", "--not-published", "--by", "masaru",
                      "--confirm", code)
    assert rc == 1 and "confirm_mismatch" in err
    assert inflight_mod.read(state_dir) is not None and inflight_mod.archives(state_dir) == []


def test_byと決めることが無ければ断る(isolated_account_factory, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("THTH_ACTOR", raising=False)
    account, state_dir = _stuck(isolated_account_factory, tmp_path, git=False)
    rc, _, err = _cli(capsys, account["name"], "resolve", "--not-published")
    assert rc == 1 and err.startswith("by_required")
    rc, _, err = _cli(capsys, account["name"], "resolve", "--by", "masaru")
    assert rc == 1 and err.startswith("decision_required")
    assert inflight_mod.read(state_dir) is not None


def test_出たと分かっているinflightはnot_publishedで解かない(isolated_account_factory, tmp_path, capsys):
    account, state_dir = _stuck(isolated_account_factory, tmp_path, git=False)
    inflight_mod.update(state_dir, post_id="8002")
    rc, _, err = _cli(capsys, account["name"], "resolve", "--not-published", "--by", "masaru")
    assert rc == 1 and err.startswith("published_known")
    rc, _, err = _cli(capsys, account["name"], "resolve", "--published", "9999", "--by", "masaru")
    assert rc == 1 and err.startswith("post_id_differs")


def test_添付のinflightはこの口では解かない(isolated_account_factory, tmp_path, capsys):
    account, state_dir = _stuck(isolated_account_factory, tmp_path, git=False)
    record = inflight_mod.read(state_dir)
    info = inflight_cli.summary({**record, "media": {"account": account["name"]}})
    assert info["media"] is True and info["self_resolvable"] is False


def test_mcpにはinflightの口が無い():
    spec = importlib.util.spec_from_file_location("thth_mcp_server", REPO / "mcp" / "server.py")
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    assert not any("inflight" in tool["name"] for tool in server.TOOLS)
