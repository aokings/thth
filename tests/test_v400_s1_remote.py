"""4.0 S1 §9 の順 9: 遠くの道（§6.4）とブックマークの受け口（§7.3）と MCP。

見るのは:
  - T-6b: はてなの口座の `send_request`・`retract_request` は `unsupported_operation`。
    `schedule_request` は `confirm` 無しで `stage: 1` と digest（何も書かない）、違う `confirm` は
    `confirm_mismatch`、同じなら刻む（配信の台帳 wanted・approved_kind・delivery）。別レーンの鍵では
    `scope_unavailable`。SNS の口座の `schedule_request` に confirm を付けると今までどおり断る。
  - T-6c: `cancel_schedule` ははてなの原稿を draft に戻し、配信の台帳は wanted のまま（消さない）。
  - `bookmark_request`: 2 段・自分の記事だけ・2 段目は adapter が無ければ `unsupported_medium`。
  - MCP: 新しい道具は `thth_bookmark_request` だけ。主体の止めを戻す道具は無い（T-2f）。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import secrets
import sys

import pytest

from tests.hatena_s1_flow_helpers import (ACCOUNT, NOW, FakeAdapter, approve, call, factory_for, fm_of,  # noqa: F401
                                          lane, world)
from thth import core, deliveries, hatena_bookmark, sent
from thth import server_writes as writes
from thth.hatena_publish import EntryResult
from thth.report_service import ReportServiceError


@pytest.fixture
def key(lane, tmp_path, monkeypatch):
    """lane-a の鍵（書ける・actor person）。根の隔離の検査はここで見るものではないので外す。"""
    from thth import report_http, report_isolation
    monkeypatch.setattr(report_isolation, "validate_environment", lambda *a, **k: None)

    def make(accounts):
        bearer = secrets.token_urlsafe(32)
        config = tmp_path / f"credentials-{len(accounts)}-{secrets.token_hex(2)}.json"
        value = dict(schema_version=1, root=str(lane.world.root), credentials=[dict(
            sha256=hashlib.sha256(bearer.encode()).hexdigest(),
            expires_at=(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)).isoformat(),
            revoked=False, accounts=accounts, writes=True, actor="person")])
        config.write_text(json.dumps(value))
        config.chmod(0o600)
        return report_http.load_credentials(config)[1][0][3]
    return make


# 予約の道は 5 分より古い時刻を断る（`PAST_GRACE_MINUTES`）。猶予の内の時刻で置く（timer はすぐ拾える）。
SOON = "2026-09-09T09:58:00+09:00"


def request(context, **body):
    return writes.execute(context, dict(account=ACCOUNT, **body), via="mcp")


def refused(context, **body):
    with pytest.raises(ReportServiceError) as caught:
        request(context, **body)
    return str(caught.value)


def draft_id(path):
    return hashlib.sha256(os.path.basename(path).encode()).hexdigest()


# --------------------------------------------------------------------------
# T-6b
# --------------------------------------------------------------------------

def test_T6b_はてなの口座はその場で出す口と消す口を持たない(lane, key):
    context = key({ACCOUNT: "lane-a"})
    assert refused(context, operation="send_request", body="本文") == "unsupported_operation"
    assert refused(context, operation="retract_request", post_id="tag:x", reason="r") == "unsupported_operation"


def test_T6b_予約は2段で_1段目は何も書かない(lane, key):
    """柵: はてなの口座の予約を 1 段で刻むと赤。"""
    context = key({ACCOUNT: "lane-a"})
    path = lane.queue(publish_at=SOON)
    first = request(context, operation="schedule_request", draft_id=draft_id(path))
    assert first["stage"] == 1 and first["status"] == "draft" and len(first["digest"]) == 12
    assert "配信原稿  lane-a-hatenablog → a.hatenablog.com" in first["preview"]
    assert "# アラビカ" in first["preview"]
    assert lane.repo not in first["preview"]          # サーバのパスを口から出さない
    assert fm_of(path)["status"] == "draft" and deliveries.list_ids(ACCOUNT) == []
    assert refused(context, operation="schedule_request", draft_id=draft_id(path),
                   confirm="0" * 12) == "confirm_mismatch"
    assert fm_of(path)["status"] == "draft"
    done = request(context, operation="schedule_request", draft_id=draft_id(path), confirm=first["digest"])
    assert done["status"] == "approved"
    fm = fm_of(path)
    assert fm["approved_kind"] == "hatena_entry" and fm["approved_by"] == "person"
    assert fm["approved_via"] == "mcp" and fm["approved_sha"].startswith(first["digest"])
    record = deliveries.read(ACCOUNT, fm["delivery"])
    assert record["state"] == "wanted" and record["history"][0]["origin"]["via"] == "mcp"
    # 刻んだものは timer が照合して出せる
    adapter = FakeAdapter()
    assert core.throw_once(ACCOUNT, production_flag=True, adapter_factory=factory_for(adapter),
                           now=NOW).action == "post"


def test_T6b_別レーンの鍵ではscope_unavailable(lane, key):
    context = key({"lane-b-hatenablog": "lane-b"})
    path = lane.queue()
    assert refused(context, operation="schedule_request", draft_id=draft_id(path)) == "scope_unavailable"


def test_T6b_confirmが文字列でなければinvalid_request(lane, key):
    context = key({ACCOUNT: "lane-a"})
    with pytest.raises(ReportServiceError) as caught:
        writes.schedule(context, {"operation": "schedule_request", "account": ACCOUNT, "draft_id": "0" * 64,
                                  "confirm": 1}, "mcp")
    assert str(caught.value) == "invalid_request"


def test_T6c_取り消しはdraftに戻し配信の台帳はwantedのまま(lane, key):
    context = key({ACCOUNT: "lane-a"})
    path = lane.queue(publish_at=SOON)
    first = request(context, operation="schedule_request", draft_id=draft_id(path))
    request(context, operation="schedule_request", draft_id=draft_id(path), confirm=first["digest"])
    delivery = fm_of(path)["delivery"]
    assert writes.cancel_schedule(ACCOUNT, draft_id(path), by="owner") == "cancelled"
    fm = fm_of(path)
    assert fm["status"] == "draft" and fm["delivery"] == delivery
    record = deliveries.read(ACCOUNT, delivery)
    assert record["state"] == "wanted" and record["seq"] == 1
    adapter = FakeAdapter()
    assert core.throw_once(ACCOUNT, production_flag=True, adapter_factory=factory_for(adapter),
                           now=NOW).action == "none"
    assert adapter.calls == []


# --------------------------------------------------------------------------
# ブックマーク（§7.3）
# --------------------------------------------------------------------------

def _published_delivery(lane):
    path = lane.queue()
    approve(path)
    assert core.throw_once(ACCOUNT, production_flag=True, adapter_factory=factory_for(FakeAdapter()),
                           now=NOW).action == "post"
    return fm_of(path)["delivery"]


def test_ブックマークは自分の公開済みの記事だけ(lane, key):
    context = key({ACCOUNT: "lane-a"})
    path = lane.queue()
    approve(path)
    delivery = fm_of(path)["delivery"]      # まだ wanted（URL が無い）
    assert refused(context, operation="bookmark_request", delivery=delivery,
                   comment="運営者です") == "bookmark_target_not_own"
    assert refused(context, operation="bookmark_request", delivery="dl_000000000000",
                   comment="運営者です") == "bookmark_target_not_own"


def test_ブックマークは2段で_2段目はadapterが作れなければ打たない(lane, key):
    """4.0 S4: 送信の口は本物（`hatenabookmark.factory`）。token が無ければ `token_missing` で何も書かない。"""
    context = key({ACCOUNT: "lane-a"})
    delivery = _published_delivery(lane)
    first = request(context, operation="bookmark_request", delivery=delivery, comment="運営者です",
                    tags="コーヒー,価格")
    assert first["stage"] == 1 and first["preview"]["url"] == "https://a.hatenablog.com/entry/2026/09/09/100000"
    assert first["preview"]["tags"] == ["コーヒー", "価格"] and first["preview"]["remaining_today"] == 3
    assert refused(context, operation="bookmark_request", delivery=delivery, comment="運営者です",
                   tags="コーヒー,価格", confirm="0" * 12) == "confirm_mismatch"
    assert refused(context, operation="bookmark_request", delivery=delivery, comment="運営者です",
                   tags="コーヒー,価格", confirm=first["digest"]) == "token_missing"
    assert [r for r in sent.records(lane.state) if r.get("kind") == "bookmark_add"] == []


def test_ブックマークはadapterがあれば打ち_2回目はbookmark_once(lane, key, monkeypatch):
    context = key({ACCOUNT: "lane-a"})
    delivery = _published_delivery(lane)

    from thth.adapters.hatenabookmark import BookmarkResult

    class Bookmarker:
        calls = []

        def get_bookmark(self, *, url):
            return None

        def add_bookmark(self, *, url, comment, tags):
            self.calls.append(url)
            return BookmarkResult(ok=True, status=200, bookmark={"user": "lane-owner", "permalink": "p"})
    monkeypatch.setattr(hatena_bookmark, "ADAPTER_FACTORY", lambda cfg, principal: Bookmarker())
    # 4.0 S4 §5.3 の自分の記事の枠（1 日 1 件）はここで見るものではないので上げる（bookmark_once を見る）。
    lane.world.edit_principal("hatena-main", daily_max_self_bookmarks=5, bookmark_same_host_daily=5)
    first = request(context, operation="bookmark_request", delivery=delivery, comment="運営者です")
    done = request(context, operation="bookmark_request", delivery=delivery, comment="運営者です",
                   confirm=first["digest"])
    assert done["status"] == "bookmarked" and len(Bookmarker.calls) == 1
    rows = [r for r in sent.records(lane.state) if r.get("kind") == "bookmark_add"]
    assert len(rows) == 1 and rows[0]["post_id"] == sent.bookmark_post_id(done["url"])
    # 4.0 S4 §3.3: 配信の台帳には書かない（正本は sent/bm: 1 つ）。
    record = deliveries.read(ACCOUNT, delivery)
    assert "bookmark" not in record["history"][-1] and record["state"] == "published"
    assert rows[0]["result"] == "confirmed" and rows[0]["target"] == "own"
    assert refused(context, operation="bookmark_request", delivery=delivery, comment="運営者です",
                   confirm=first["digest"]) == "bookmark_once"


def test_ブックマークはbookmarkのlanesに無いレーンでは断る(lane, key, monkeypatch):
    context = key({ACCOUNT: "lane-a"})
    delivery = _published_delivery(lane)
    principal = lane.world.principal("hatena-main")
    principal["bookmark"] = {"lanes": []}
    lane.world.write_principal(principal)
    monkeypatch.setattr(hatena_bookmark, "ADAPTER_FACTORY", lambda cfg, principal: pytest.fail("打った"))
    first = request(context, operation="bookmark_request", delivery=delivery, comment="運営者です")
    assert refused(context, operation="bookmark_request", delivery=delivery, comment="運営者です",
                   confirm=first["digest"]) == "bookmark_not_allowed_for_lane"


def test_ブックマークの手元の口_1段目と2段目(lane):
    delivery = _published_delivery(lane)
    rc, out, err = call(["bookmark", "add", ACCOUNT, "--delivery", delivery, "--comment", "運営者です"])
    assert rc == 1 and "digest: " in out and "https://a.hatenablog.com/entry/2026/09/09/100000" in out
    digest = [line.split(": ", 1)[1] for line in out.splitlines() if line.startswith("digest: ")][0]
    rc, out, err = call(["bookmark", "add", ACCOUNT, "--delivery", delivery, "--comment", "運営者です",
                         "--confirm", digest, "--by", "tester", "--production", "--json"])
    assert rc == 2 and json.loads(out)["error"] == "token_missing"   # 4.0 S4: token が無ければ打たない


def test_ブックマークはSNSの口座では受けない():
    with pytest.raises(hatena_bookmark.BookmarkError) as caught:
        hatena_bookmark.prepare("x", {"media": "threads"}, delivery="dl_000000000000", comment="c")
    assert caught.value.code == "unsupported_operation"


# --------------------------------------------------------------------------
# MCP
# --------------------------------------------------------------------------

@pytest.fixture
def mcp_server():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp"))
    import server
    return server


def test_MCPに足すのはthth_bookmark_requestだけ(mcp_server):
    names = {tool["name"] for tool in mcp_server.SERVER_TOOLS}
    assert "thth_bookmark_request" in names
    assert not any("principal" in name for name in names)
    schedule = next(t for t in mcp_server.SERVER_TOOLS if t["name"] == "thth_schedule_request")
    assert "confirm" in schedule["inputSchema"]["properties"]
    assert schedule["inputSchema"]["required"] == ["account", "draft_id"]
    build, _stdin = mcp_server.REMOTE_CLI["thth_bookmark_request"]
    assert build({"account": "a", "delivery": "dl_000000000000", "comment": "c", "confirm": "abc"}) == [
        "bookmark", "add", "a", "--delivery=dl_000000000000", "--comment=c", "--confirm=abc", "--production", "--json"]
    build, _stdin = mcp_server.REMOTE_CLI["thth_schedule_request"]
    assert build({"account": "a", "draft_id": "d"}) == ["schedule", "a", "--draft=d", "--production", "--json"]
