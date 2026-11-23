"""設計 持ち主の依頼で消す（2026-11-23・masaru「推しでオケ」）: thth.me と MCP の道。

- 記録の無い自分の投稿は、持ち主が発行した鍵（＝前もって任せた）＋二段。`confirm` が無ければ
  一段目（本文・日時・URL・digest）を返すだけで消えない（問い 1）。
- 記録のある投稿（THTH が出した物）は今までどおり一回で消える（問い 2）。
- 遠くの道でも安全装置に数え、`retracted_outside/` に道の名と鍵の id を残し、`/activity` に並べる。

媒体は叩かない: `adapters.make_adapter` を差し替えた偽の adapter が `own_post` と `delete_post` を数える。
"""
from __future__ import annotations

import inspect
import json

import pytest

from tests.test_v212_server_writes import env, remote  # noqa: F401
from tests.test_v3120_guard import credential_id, ledger  # noqa: F401
from thth import accounts, activity, approval, jst, retract_cli, sent
from thth import server_writes as writes
from thth.adapters import base as adapter_base
from thth.report_service import ReportServiceError


class FakeMedium:
    """偽の媒体。`rows` の持ち主・本文を試験が書き換える。`deletes` が証拠。"""

    def __init__(self):
        self.rows = {"777": {"owner": "me", "text": "外で出した本文", "timestamp": "2026-11-20T10:00:00+09:00"},
                     "888": {"owner": "someone", "text": "他人の本文", "timestamp": "2026-11-20T10:00:00+09:00"}}
        self.unreadable = False
        self.deletes = []
        self.reads = []

    def own_post(self, post_id):
        self.reads.append(post_id)
        if self.unreadable:
            raise adapter_base.PostUnreadable("HTTP 500")
        row = self.rows.get(post_id)
        if row is None:
            raise adapter_base.PostUnreadable("HTTP 404")
        if row["owner"] != "me":
            raise adapter_base.PostNotOwn("この口座の投稿ではありません")
        return {"post_id": post_id, "text": row["text"], "timestamp": row["timestamp"],
                "url": f"https://www.threads.net/@demo/post/{post_id}", "kind": "post"}

    def delete_post(self, post_id):
        self.deletes.append(post_id)
        return {"deleted_id": post_id}


@pytest.fixture
def medium(monkeypatch):
    from thth import adapters
    fake = FakeMedium()
    monkeypatch.setattr(adapters, "make_adapter", lambda *a: fake)
    return fake


def call(env, via="mcp", **request):
    return writes.execute(env["context"], dict(operation="retract_request", account="alpha", **request), via=via)


def refused(env, **request):
    with pytest.raises(ReportServiceError) as caught:
        call(env, **request)
    return str(caught.value)


def state_dir():
    return accounts.state_dir_for("alpha")


def test_記録の無い投稿はconfirm無しでは一段目を返すだけで消えない(env, remote, medium):
    first = call(env, post_id="777", reason="誤字")
    assert first["status"] == "confirm_required" and first["stage"] == 1 and first["retracted"] is False
    assert first["recorded"] is False and first["text"] == "外で出した本文"
    assert first["posted_at"] == "2026-11-20T10:00:00+09:00" and first["permalink"].endswith("/777")
    assert medium.deletes == []
    # 口座・id・理由だけから作った番号（MCP の遠くの道が付ける番号）も「confirm 無し」と同じ。
    plain = approval.compute_retract_digest(post_id="777", account="alpha", reason="誤字")
    assert call(env, post_id="777", reason="誤字", confirm=plain)["status"] == "confirm_required"
    assert medium.deletes == []


def test_記録の無い投稿は違う番号では消えず合う番号で1回だけ消える(env, remote, medium):
    """柵: サーバの番号の照らしを外すと赤（違う番号で消えてしまう）。"""
    first = call(env, post_id="777", reason="誤字")
    assert refused(env, post_id="777", reason="誤字", confirm="0" * 12) == "confirm_mismatch"
    assert medium.deletes == []
    done = call(env, post_id="777", reason="誤字", confirm=first["digest"])
    assert done["status"] == "retracted" and done["recorded"] is False and medium.deletes == ["777"]


def test_番号は一段目を通らないと作れない_本文の指紋が材料にある(env, remote, medium):
    """柵: 番号の材料から本文の指紋を抜くと赤（本文が変わっても同じ番号になる）。"""
    first = call(env, post_id="777", reason="誤字")
    medium.rows["777"]["text"] = "一段目のあとに直した本文"
    assert refused(env, post_id="777", reason="誤字", confirm=first["digest"]) == "confirm_mismatch"
    assert medium.deletes == []
    again = call(env, post_id="777", reason="誤字")
    assert again["digest"] != first["digest"]


def test_他人の投稿と読めない投稿はサーバでも消えない(env, remote, medium):
    assert refused(env, post_id="888", reason="誤字") == "post_not_own"
    medium.unreadable = True
    assert refused(env, post_id="777", reason="誤字") == "post_unreadable"
    assert medium.deletes == []
    assert "post_not_own" in writes.SAFE_ERRORS and "post_unreadable" in writes.SAFE_ERRORS
    assert "confirm_required" in writes.SAFE_ERRORS


def test_出所が残りactivityに消したの行が並ぶ(env, remote, medium):
    """柵: `origin` を渡さないと赤（道の名と鍵の id が残らない）。"""
    first = call(env, post_id="777", reason="誤字")
    call(env, post_id="777", reason="誤字", confirm=first["digest"])
    row = sent.read_outside_retraction(state_dir(), "777")
    assert row["retracted_via"] == "mcp" and row["retracted_credential"] == credential_id(env)
    assert row["retracted_by"] == "person" and row["kind"] == "post"
    assert row["posted_at"] == "2026-11-20T10:00:00+09:00"
    assert sent.read(state_dir(), "777") is None
    summary = activity.summary("person", "alpha", env["path"])
    retracted = [r for r in summary["rows"] if r["kind"] == "retracted"]
    assert [r["post_id"] for r in retracted] == ["777"] and retracted[0]["head"] == "外で出した本文"
    assert set(retracted[0]) == {"kind", "at", "head", "post_id", "draft_id", "reason", "reply"}
    assert summary["today"]["retracts"] == 1
    # 二度目は消さない
    assert refused(env, post_id="777", reason="誤字", confirm=first["digest"]) == "scope_unavailable"
    assert medium.deletes == ["777"]


def test_遠くの道でも記録の無い削除を安全装置に数える(env, remote, medium):
    """柵: `guard._action_rows` の `retracted_outside` の行を外すと赤（上限を超えて消せてしまう）。"""
    ledger(env, daily_max_retracts=2)
    for post_id in ("777", "778", "779"):
        medium.rows[post_id] = {"owner": "me", "text": "本文 " + post_id, "timestamp": jst.iso()}
    for post_id in ("777", "778"):
        first = call(env, post_id=post_id, reason="整理")
        assert call(env, post_id=post_id, reason="整理", confirm=first["digest"])["status"] == "retracted"
    first = call(env, post_id="779", reason="整理")
    assert refused(env, post_id="779", reason="整理", confirm=first["digest"]) == "retract_limit"
    assert medium.deletes == ["777", "778"]


def test_記録のある投稿のMCPの削除は今までどおり一回(env, remote, medium):
    """問い 2: THTH が出した投稿（sent/ の記録がある）は confirm 無しの一回で消える。読みは要らない。"""
    sent.write(str(state_dir()), post_id="555", text="THTH が出した本文", body_hash="x", sent_at=jst.iso())
    done = call(env, post_id="555", reason="訂正")
    assert done["status"] == "retracted" and done["recorded"] is True
    assert medium.deletes == ["555"] and medium.reads == []


def test_stage1は記録のある投稿でも消さない(env, remote, medium):
    sent.write(str(state_dir()), post_id="555", text="THTH が出した本文", body_hash="x", sent_at=jst.iso())
    first = call(env, via="api", post_id="555", reason="訂正", stage="1")
    assert first["status"] == "confirm_required" and first["recorded"] is True and first["text"] == "THTH が出した本文"
    assert first["digest"] == approval.compute_retract_digest(post_id="555", account="alpha", reason="訂正")
    assert medium.deletes == []
    assert refused(env, post_id="555", reason="訂正", confirm="0" * 12) == "confirm_mismatch"
    assert call(env, via="api", post_id="555", reason="訂正", confirm=first["digest"])["status"] == "retracted"
    assert medium.deletes == ["555"]


def _mcp_source():
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent / "mcp" / "server.py").read_text(encoding="utf-8")


def test_DELETEと持ち主の1件読みはretract_cliの1か所だけ():
    """柵: `server_writes`・`remote`・MCP から adapter の `delete_post`・`own_post` を直に呼ぶと赤。"""
    from thth import remote as remote_mod
    src = inspect.getsource(retract_cli)
    assert src.count(".own_post(") == 1 and src.count(".delete_post(") == 1
    for text in (inspect.getsource(writes), inspect.getsource(remote_mod), _mcp_source()):
        assert ".own_post(" not in text and ".delete_post(" not in text


def test_MCPの道具はconfirmを持ち_説明は持ち主の依頼だけ():
    from tests.test_mcp import _load_server_module
    server = _load_server_module()
    tool = next(t for t in server.SERVER_TOOLS if t["name"] == "thth_retract_request")
    assert "confirm" in tool["inputSchema"]["properties"]
    assert tool["inputSchema"]["required"] == ["account", "post_id", "reason"]
    assert "持ち主が消すと言った投稿だけ" in tool["description"] and "confirm_required" in tool["description"]
    build = server.REMOTE_CLI["thth_retract_request"][0]
    plain = approval.compute_retract_digest(post_id="777", account="alpha", reason="誤字")
    assert build({"account": "alpha", "post_id": "777", "reason": "誤字"})[-2] == "--confirm=" + plain
    assert build({"account": "alpha", "post_id": "777", "reason": "誤字",
                  "confirm": "abcdef123456"})[-2] == "--confirm=abcdef123456"


def test_監査5290D1_古いCLIの形のapiの依頼は記録の無い投稿を断る(env, remote, medium):
    """5.28.1 までの遠くの道の CLI は confirm も stage も付けず、status を見ずに「取り下げました」と言う。"""
    assert refused(env, via="api", post_id="777", reason="誤字") == "confirm_required"
    assert medium.deletes == []
    # 新しい CLI（stage・confirm を付ける）と MCP（via=mcp）は今までどおり
    first = call(env, via="api", post_id="777", reason="誤字", stage="1")
    assert first["status"] == "confirm_required"
    assert call(env, via="mcp", post_id="777", reason="誤字")["status"] == "confirm_required"
    assert call(env, via="api", post_id="777", reason="誤字", confirm=first["digest"])["status"] == "retracted"
    assert medium.deletes == ["777"]
