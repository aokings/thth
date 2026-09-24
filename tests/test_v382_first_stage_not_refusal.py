"""3.8.2 件 3: 二段確認の 1 段目を「断り」に数えない（報告 r20260924-9f07f36b）。

実測: `thth approve` の 1 段目（digest を出すだけ・rc 1）の末尾に
`report_channel: … --title "exit_1 で断られた"` が付き、3.3.0 の直前の断りの控え
（`last_refusals`）にも入っていた。

- approve の 1 段目と広場の open の 1 段目は、rc 1 のまま（呼び出し側を壊さない）、
  断りの行を出さず、`last_refusals` にも記録しない。
- つまずきの年表の材料にもならない（`--from-last-refusal` が拾うものが無い）。
- 本当の断り（digest 違い）では従前どおり出る・記録する。
"""
from __future__ import annotations

import json
import os

import pytest

from tests.conftest import write_queue_file
from thth import accounts, cli, plaza, refusals, report_timeline
from tests.test_v340_plaza_open import ledger, join  # noqa: F401  (fixture)
from tests.test_v340_plaza_store import owners  # noqa: F401  (fixture)


def _draft(account):
    return write_queue_file(account["queue_dir"], "a.md", body="## threads\n\n1 段目の本文。\n",
                            fm_overrides={"account": account["name"], "status": "draft",
                                          "approved_sha": None})


def _refusal_file(account_name):
    return os.path.join(accounts.state_dir_for(account_name), refusals.FILE)


def test_approveの1段目はreport_channelを出さず_last_refusalsにも入らない(isolated_account, tmp_path, capsys):
    name = isolated_account["name"]
    path = _draft(isolated_account)

    assert cli.main(["approve", path, "--account", name]) == 1
    out, err = capsys.readouterr()

    assert "digest: " in out  # 1 段目は出ている（rc も従前どおり 1）
    assert "report_channel" not in err and "report_channel" not in out
    assert "で断られた" not in err
    assert refusals.read(name) == []
    assert not os.path.exists(_refusal_file(name))
    # 年表の材料にもならない（直前の断りの控えが無い）。
    assert cli.main(["report", "file", name, "--kind", "friction", "--from-last-refusal",
                     "--title", "1 段目", "--by", "テスト"]) != 0
    assert "no_last_refusal" in capsys.readouterr().err
    scope = report_timeline.report_inbox.Scope([name], ["nigamilab"])
    assert report_timeline.timeline(scope, notes_dir=tmp_path)["rows"] == []


def test_approveのjsonの1段目も断りに数えない(isolated_account, capsys):
    name = isolated_account["name"]
    path = _draft(isolated_account)
    assert cli.main(["approve", path, "--account", name, "--json"]) == 1
    out, err = capsys.readouterr()
    assert json.loads(out)["not_until_confirmed"] is True
    assert "report_channel" not in err
    assert refusals.read(name) == []


def test_digest違いの本当の断りでは従前どおり出て記録する(isolated_account, capsys):
    name = isolated_account["name"]
    path = _draft(isolated_account)
    assert cli.main(["approve", path, "--account", name]) == 1
    capsys.readouterr()

    rc = cli.main(["approve", path, "--account", name, "--confirm", "0" * 12, "--by", "テスト"])
    err = capsys.readouterr().err

    assert rc == 1
    assert err.splitlines()[0].startswith("digest が一致しないので承認しません")
    assert err.splitlines()[-1].startswith(f"report_channel: thth report file {name} ")
    [row] = refusals.read(name)
    assert row["command"] == "approve" and row["reason_code"] == "exit_1"
    # 次の cli.main で印は持ち越さない（1 段目の直後の本当の断りも数える）。
    assert not refusals.is_first_stage()


def test_広場のopenの1段目も断りに数えない(owners, ledger, tmp_path, capsys):
    join("kopicha")
    body = tmp_path / "b.txt"
    body.write_text("朝の問いを続けた", encoding="utf-8")
    argv = ["plaza", "post", "kopicha-threads", "--kind", "finding", "--title", "朝の問い",
            "--body-file", str(body), "--scope", "Threads の朝", "--open", "--by", "s"]

    assert cli.main(argv) == 1
    out, err = capsys.readouterr()
    assert out.startswith("一段目: まだ open にしていません")
    assert "report_channel" not in err
    assert refusals.read("kopicha-threads") == []

    # 二段目の digest 違いは本当の断り（従前どおり）。
    assert cli.main(argv + ["--confirm", "x" * 20]) == 2
    assert "report_channel" in capsys.readouterr().err
    [row] = refusals.read("kopicha-threads")
    assert row["command"] == "plaza post"
    assert plaza.admin_list()["n"] == 0


@pytest.mark.parametrize("json_flag", [[], ["--json"]], ids=["text", "json"])
def test_広場のupdateでopenにする1段目も断りに数えない(owners, capsys, json_flag):
    join("kopicha")
    inside = plaza.post("kopicha-bsky", kind="question", title="内輪の問い", body="b",
                        scope_note="Bluesky", by="b")
    argv = ["plaza", "update", inside["plaza_id"], "--as", "kopicha-bsky", "--visibility", "open",
            "--by", "b", *json_flag]
    assert cli.main(argv) == 1
    assert "report_channel" not in capsys.readouterr().err
