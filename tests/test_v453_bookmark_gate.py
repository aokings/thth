"""4.5.3 の柵: はてなブックマークの CLI の `--production` も口座の台帳の `require_gate: true` に従う
（計画 4.4.3 の伺い 2・本線と合意。4.4.1 では gate の範囲の外だった）。

- add・edit・batch の 2 段目: 番号が無いか形が違えば、打つ前（HTTP も順番待ちも無し）に断る。
- 正しい番号なら通り、まとめて承認ではまとめのファイルに番号を残す。
- `require_gate` の無い口座は今までどおり番号なしで通る（既存の試験が見ている）。
"""
from __future__ import annotations

import json

import pytest

from tests.hatena_s1_flow_helpers import call
from tests.hatena_s4_helpers import ACCOUNT, EXTERNAL, bm, calls, fake_http, world  # noqa: F401
from tests.test_v410_bookmark_batch import bb, item, write  # noqa: F401
from thth import bookmark_batch


def _require_gate(world):
    data = world.account(ACCOUNT)
    data["require_gate"] = True
    world.write_account(data)


def _add_stage1(extra=()):
    rc, out, err = call(["bookmark", "add", ACCOUNT, "--url", EXTERNAL, "--comment", "c", "--json", *extra])
    assert rc == 1, (out, err)
    return json.loads(out)["digest"]


def _add_stage2(digest, extra=()):
    return call(["bookmark", "add", ACCOUNT, "--url", EXTERNAL, "--comment", "c", "--confirm", digest,
                 "--by", "tester", "--production", "--json", *extra])


@pytest.mark.parametrize("extra, code", [((), "approval_gate_required"),
                                         (("--approval", "A2610-25"), "approval_invalid")])
def test_add_refuses_without_gate_before_any_http(bm, extra, code):
    _require_gate(bm)
    digest = _add_stage1()
    before = len(calls(bm, "POST"))
    rc, out, err = _add_stage2(digest, extra)
    assert rc == 1, (out, err)
    assert json.loads(out)["error"] == code
    assert len(calls(bm, "POST")) == before


def test_add_with_gate_goes_through(bm):
    _require_gate(bm)
    digest = _add_stage1()
    rc, out, err = _add_stage2(digest, ("--approval", "A261023-7"))
    assert rc == 0, (out, err)
    assert json.loads(out)["approval"] == "A261023-7"


def test_first_stage_hint_names_approval(bm):
    _require_gate(bm)
    rc, out, err = call(["bookmark", "add", ACCOUNT, "--url", EXTERNAL, "--comment", "c"])
    assert rc == 1
    assert "--approval <番号>" in out


def test_edit_refuses_without_gate(bm):
    _require_gate(bm)
    rc, out, err = _add_stage2(_add_stage1(), ("--approval", "A261023-7"))
    assert rc == 0, (out, err)
    edit = ["bookmark", "edit", ACCOUNT, "--url", EXTERNAL, "--comment", "d", "--json"]
    rc, out, err = call(edit)
    assert rc == 1, (out, err)
    digest = json.loads(out)["digest"]
    before = len(calls(bm, "POST"))
    rc, out, err = call(edit + ["--confirm", digest, "--by", "tester", "--production"])
    assert rc == 1 and json.loads(out)["error"] == "approval_gate_required", (out, err)
    assert len(calls(bm, "POST")) == before
    rc, out, err = call(edit + ["--confirm", digest, "--by", "tester", "--production", "--approval", "A261023-9"])
    assert rc == 0, (out, err)


def test_batch_refuses_without_gate_and_records_gate(bb):  # noqa: F811
    _require_gate(bb)
    path = write(bb, [item("https://a.example.com/page/1")])
    rc, out, err = call(["bookmark", "batch", ACCOUNT, path, "--json"])
    digest = json.loads(out)["digest"]
    rc, out, err = call(["bookmark", "batch", ACCOUNT, path, "--confirm", digest, "--by", "masaru",
                         "--production", "--json"])
    assert rc == 1 and json.loads(out)["error"] == "approval_gate_required", (out, err)
    assert bookmark_batch.load_batches(ACCOUNT) == []
    rc, out, err = call(["bookmark", "batch", ACCOUNT, path, "--confirm", digest, "--by", "masaru",
                         "--production", "--approval", "A261023-8", "--json"])
    assert rc == 0, (out, err)
    [batch] = bookmark_batch.load_batches(ACCOUNT)
    assert batch["approval"] == "A261023-8"


def test_batch_without_require_gate_needs_no_number(bb):  # noqa: F811
    path = write(bb, [item("https://a.example.com/page/1")])
    rc, out, err = call(["bookmark", "batch", ACCOUNT, path, "--json"])
    digest = json.loads(out)["digest"]
    rc, out, err = call(["bookmark", "batch", ACCOUNT, path, "--confirm", digest, "--by", "masaru",
                         "--production", "--json"])
    assert rc == 0, (out, err)
    [batch] = bookmark_batch.load_batches(ACCOUNT)
    assert "approval" not in batch


# 監査（Fable 10/23）: MCP の bookmark_request・bookmark_batch_request も approval を受け、
# 遠くの CLI に --approval を組む（無いと require_gate: true の口座で MCP からは必ず断られる）。
def test_mcp_bookmark_tools_accept_and_forward_approval():
    import os
    import sys as _sys
    _sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp"))
    import server
    tools = {t["name"]: t for t in server.SERVER_TOOLS}
    for name in ("thth_bookmark_request", "thth_bookmark_batch_request"):
        assert "approval" in tools[name]["inputSchema"]["properties"], name
    add_argv = server.REMOTE_CLI["thth_bookmark_request"][0]({"account": "alpha", "url": EXTERNAL,
                                                               "approval": "A261021-25"})
    batch_argv = server.REMOTE_CLI["thth_bookmark_batch_request"][0]({"account": "alpha", "text": "",
                                                                      "approval": "A261021-25"})
    assert "--approval=A261021-25" in add_argv and "--approval=A261021-25" in batch_argv
    assert not any(a.startswith("--approval") for a in
                   server.REMOTE_CLI["thth_bookmark_request"][0]({"account": "alpha", "url": EXTERNAL}))
