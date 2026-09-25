"""読める連投（`thth: 2`）を board と queue で「型外」に数えない（報告 2026-09-25 BrownBeaver）。

`queuefile.parse` は `thth: 2` を fail-closed で malformed にする（公開の経路は bundle 側で
読む）。数え方がそれをそのまま型外に足すと、連投を 1 本置くだけで盤面に「型外 1」が
常時出て、本当に壊れた原稿と見分けが付かない。壊れた連投は従前どおり型外。
"""
from __future__ import annotations

import os

from tests.test_bundle import BODY, FM, make
from thth import report as report_mod


def _put(qdir, name, text):
    with open(os.path.join(qdir, name), "w", encoding="utf-8") as f:
        f.write(text)


def _fm(account):
    return FM.replace("account: nigamilab-threads", f"account: {account}")


def test_読める連投は型外に数えずdraftに数える(isolated_account):
    name = isolated_account["name"]
    _put(isolated_account["queue_dir"], "bundle.md", make(_fm(name), BODY))

    q = report_mod.queue_summary(name)[name]
    assert q["type_mismatch"] == 0
    assert q["bundles"] == 1
    assert q["counts"]["draft"] == 1

    row = next(r for r in report_mod.board_summary()["accounts"] if r["account"] == name)
    assert row["type_mismatch"] == 0


def test_壊れた連投と壊れた単発は従前どおり型外(isolated_account):
    name = isolated_account["name"]
    qdir = isolated_account["queue_dir"]
    # top-level の鍵の重複＝読めない連投（セキュリティ監査 2026-09-14 の fail-closed）。
    _put(qdir, "broken-bundle.md", make(_fm(name) + "status: approved\n", BODY))
    _put(qdir, "broken.md", "---\nthth: 9\naccount: x\n---\n本文\n")

    q = report_mod.queue_summary(name)[name]
    assert q["type_mismatch"] == 2
    assert q["bundles"] == 0

    row = next(r for r in report_mod.board_summary()["accounts"] if r["account"] == name)
    assert row["type_mismatch"] == 2


def test_他accountの連投は数えない(isolated_account):
    name = isolated_account["name"]
    _put(isolated_account["queue_dir"], "other.md", make(_fm("someone-else-threads"), BODY))

    q = report_mod.queue_summary(name)[name]
    assert q["type_mismatch"] == 0
    assert q["bundles"] == 0
    assert q["counts"]["draft"] == 0
